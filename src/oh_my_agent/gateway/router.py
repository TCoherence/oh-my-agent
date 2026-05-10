from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteDecision:
    """Router classification result.

    The canonical intent set has 5 values:

    - ``chat_reply`` — single-turn chat answer; no task is created.
    - ``invoke_skill`` — invoke an existing skill (router supplies
      ``skill_name`` when known).
    - ``oneoff_artifact`` — generate a one-off deliverable (report,
      summary, brief) that is not derived from a known skill.
    - ``propose_repo_change`` — multi-step repository edit task.
    - ``update_skill`` — create-or-repair a skill. The dispatcher
      decides between create and repair by checking whether
      ``skill_name`` matches a registered skill.

    Older intent names are accepted as aliases at parse time and
    normalized to the canonical form above. See ``_INTENT_ALIASES``.
    """

    decision: str
    confidence: float
    goal: str
    risk_hints: list[str]
    raw_text: str
    skill_name: str | None = None
    task_type: str | None = None
    completion_mode: str | None = None


_RESERVED_PAYLOAD_KEYS = frozenset({"messages", "model", "max_tokens", "temperature"})

# Canonical intents — what the router prompt asks the model to output.
_CANONICAL_INTENTS = frozenset({
    "chat_reply",
    "invoke_skill",
    "oneoff_artifact",
    "propose_repo_change",
    "update_skill",
})

# Legacy intent names accepted from older router models / test fixtures /
# users typing in raw decision strings. Normalized to canonical at parse
# time so ``RouteDecision.decision`` is always one of ``_CANONICAL_INTENTS``.
_INTENT_ALIASES: dict[str, str] = {
    "reply_once": "chat_reply",
    "invoke_existing_skill": "invoke_skill",
    "propose_artifact_task": "oneoff_artifact",
    "propose_repo_task": "propose_repo_change",
    "propose_task": "propose_repo_change",
    "create_skill": "update_skill",
    "repair_skill": "update_skill",
}


def normalize_intent(raw: str) -> str:
    """Map a raw intent string (canonical or legacy) to canonical form.

    Returns ``"chat_reply"`` for unknown values — the safe default that
    keeps the runtime in chat mode rather than spawning a task.
    """
    cleaned = raw.strip().lower()
    if cleaned in _CANONICAL_INTENTS:
        return cleaned
    aliased = _INTENT_ALIASES.get(cleaned)
    if aliased is not None:
        return aliased
    return "chat_reply"


class OpenAICompatibleRouter:
    """LLM classifier for deciding chat reply vs runtime task proposal."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 15,
        confidence_threshold: float = 0.55,
        max_retries: int = 1,
        max_tokens: int = 4096,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = int(timeout_seconds)
        self._confidence_threshold = float(confidence_threshold)
        self._max_retries = max(0, int(max_retries))
        # Reasoning-tuned models (e.g. DeepSeek V4 flash, deepseek-reasoner)
        # share their token budget with chain-of-thought, and pretty-printed
        # JSON with multiple risk_hints adds up fast. 400 was the legacy
        # default and was found to truncate content mid-JSON. max_tokens is
        # only a ceiling — the model bills per generated token and stops
        # naturally — so 4096 costs the same as 1024 in the common case
        # while leaving slack for reasoner outliers. timeout_seconds (15s
        # default) bounds worst-case latency.
        self._max_tokens = max(1, int(max_tokens))
        raw_extra = dict(extra_body) if isinstance(extra_body, dict) else {}
        self._extra_body: dict[str, Any] = {}
        for key, value in raw_extra.items():
            if key in _RESERVED_PAYLOAD_KEYS:
                logger.warning(
                    "Router extra_body key %r is reserved and will be ignored",
                    key,
                )
                continue
            self._extra_body[key] = value

    @property
    def confidence_threshold(self) -> float:
        return self._confidence_threshold

    async def route(self, message: str, *, context: str | None = None) -> RouteDecision | None:
        system_prompt = (
            "Classify the user message into one of five intents:\n"
            "  chat_reply         — single-turn chat answer (no task spawned)\n"
            "  invoke_skill       — run an existing skill on this request\n"
            "  oneoff_artifact    — produce a one-off deliverable (report, summary, brief)\n"
            "  propose_repo_change — multi-step repository edit\n"
            "  update_skill       — create or repair a skill (the dispatcher decides "
            "create-vs-repair from the skill_name)\n\n"
            "DISAMBIGUATION RULES (read first):\n"
            "- For one-time deliverables like research reports, summaries, analyses, "
            "briefs, news digests (keywords: 报告/总结/调研/分析/research/summary/brief/report), "
            "choose `oneoff_artifact`. This is the default for 'do this research and give me a report' requests.\n"
            "- Choose `update_skill` ONLY when (a) the user explicitly asks to create/build/"
            "make a skill (keywords: create a skill / 创建/新建/生成 skill / package as a workflow), "
            "OR (b) the workflow is clearly recurring or parameterizable (scheduled, templated "
            "with variable inputs like 'for any ticker I give', 'every morning'), OR (c) the "
            "user is giving feedback on a recently invoked skill and asking to fix/improve/adapt it. "
            "In the feedback case keep `skill_name` pointed at the existing skill — do not invent a "
            "parallel new skill just because the user mentions an external repo, project, or tool.\n"
            "- Never infer skill-creation intent just because the word 'skill' appears in prior "
            "context or the known-skills list.\n"
            "- When both artifact and skill could apply, prefer `oneoff_artifact` (lower blast radius).\n\n"
            "Output strict JSON only with keys: decision, confidence, goal, risk_hints, skill_name, task_type, completion_mode.\n"
            "decision must be one of: chat_reply, invoke_skill, oneoff_artifact, propose_repo_change, update_skill.\n"
            "If invoke_skill, keep goal empty when possible and provide skill_name if obvious. "
            "Known skills may be provided as name plus description; prefer invoke_skill when the "
            "current message semantically matches one of those skill descriptions, or when recent "
            "context shows a known skill was just merged / synced / recently used and the current "
            "message is a follow-up asking to run it / try again / continue with that skill.\n"
            "If oneoff_artifact, propose_repo_change, or update_skill, write a concise executable goal.\n"
            "If update_skill, also provide skill_name as a hyphen-case slug.\n"
            "If oneoff_artifact, task_type should be 'artifact' and completion_mode should be 'reply'.\n"
            "If propose_repo_change, task_type should be 'repo_change' and completion_mode should be 'merge'.\n"
            "If update_skill, task_type should be 'skill_change' and completion_mode should be 'merge'.\n"
            "If chat_reply, goal can be empty string and skill_name should be empty string.\n"
            "confidence must be a float between 0 and 1.\n\n"
            "EXAMPLES:\n"
            "- User: \"调研 Jensen Huang 过去 3 年所有公开演讲，整理成报告\"\n"
            "  → {\"decision\":\"oneoff_artifact\",\"task_type\":\"artifact\",\"completion_mode\":\"reply\"}\n"
            "- User: \"帮我做一个 skill，每天总结 AI 领域的新论文\"\n"
            "  → {\"decision\":\"update_skill\",\"skill_name\":\"ai-paper-daily-digest\",\"task_type\":\"skill_change\",\"completion_mode\":\"merge\"}\n"
            "- User: \"paper-digest 这个 skill 昨天输出的 summary 太短了，改一下\"\n"
            "  → {\"decision\":\"update_skill\",\"skill_name\":\"paper-digest\",\"task_type\":\"skill_change\",\"completion_mode\":\"merge\"}\n"
            "- User: \"用 paper-digest 看一下今天 arxiv 的 LLM 板块\"\n"
            "  → {\"decision\":\"invoke_skill\",\"skill_name\":\"paper-digest\"}"
        )
        core_payload: dict[str, Any] = {
            "model": self._model,
            "temperature": 0,
            "max_tokens": self._max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": (f"{context}\n\nCurrent user message:\n{message[:1500]}" if context else message[:1500])},
            ],
        }
        payload: dict[str, Any] = {**self._extra_body, **core_payload}
        # Default JSON mode unless the user has explicitly set their own
        # response_format via extra_body (e.g. a custom schema, or disabling
        # for endpoints that reject the field). DeepSeek's OpenAI-compatible
        # API accepts {"type":"json_object"}; the system prompt already
        # mentions JSON, which is the only requirement for that mode.
        payload.setdefault("response_format", {"type": "json_object"})
        attempts = self._max_retries + 1
        data: dict[str, Any] | None = None
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                data = await asyncio.to_thread(self._post_json, payload)
                break
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Router request failed model=%s base=%s timeout=%ss attempt=%d/%d err_type=%s err=%s",
                    self._model,
                    self._base_url,
                    self._timeout_seconds,
                    attempt,
                    attempts,
                    type(exc).__name__,
                    exc,
                )
                if attempt < attempts:
                    await asyncio.sleep(0.25 * attempt)
        if data is None:
            if last_exc:
                logger.debug("Router final failure detail", exc_info=last_exc)
            return None

        text = self._extract_content(data)
        if not text:
            logger.warning(
                "Router returned empty content model=%s base=%s",
                self._model,
                self._base_url,
            )
            return None
        parsed = self._parse_json(text)
        if not parsed:
            logger.warning(
                "Router returned non-JSON content model=%s len=%d sample=%r",
                self._model,
                len(text),
                text[:500],
            )
            logger.debug("Router non-JSON full text model=%s text=%r", self._model, text)
            return None

        decision = normalize_intent(str(parsed.get("decision", "")))

        confidence = self._to_float(parsed.get("confidence", 0.0))
        goal = str(parsed.get("goal", "")).strip()
        skill_name = str(parsed.get("skill_name", "")).strip() or None
        task_type = str(parsed.get("task_type", "")).strip() or None
        completion_mode = str(parsed.get("completion_mode", "")).strip() or None
        hints = parsed.get("risk_hints", [])
        risk_hints = [str(h).strip() for h in hints if str(h).strip()] if isinstance(hints, list) else []

        return RouteDecision(
            decision=decision,
            confidence=confidence,
            goal=goal,
            risk_hints=risk_hints,
            raw_text=text[:2000],
            skill_name=skill_name,
            task_type=task_type,
            completion_mode=completion_mode,
        )

    def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url=f"{self._base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"http {exc.code}: {detail[:300]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"url error: {exc}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid json response: {raw[:200]}") from exc

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        message = choices[0].get("message", {})
        content = message.get("content", "")
        return str(content).strip()

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        """Parse the router's JSON output, with three layers of recovery.

        Layer 1: direct ``json.loads`` after stripping markdown code fences
        (`````json … ````` is a common output style for
        instruction-tuned models even when JSON mode is on).

        Layer 2: substring between the first ``{`` and the last ``}`` —
        catches prose-wrapped JSON (``"Here you go: {...}"``).

        Layer 3: best-effort recovery for JSON that was truncated mid-output
        because the model hit ``max_tokens`` mid-emission. We walk the text
        tracking string and bracket state, then auto-close any open string
        plus pending brackets in reverse order. Without this, reasoning
        models like DeepSeek V4 flash that emit pretty-printed JSON
        occasionally got their output dropped entirely on the unlucky run.
        """
        text = text.strip()
        if not text:
            return None

        # Strip markdown code fences (```json ... ``` / ``` ... ```).
        if text.startswith("```"):
            first_newline = text.find("\n")
            if first_newline > 0:
                text = text[first_newline + 1 :]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        # Layer 1: direct parse.
        try:
            result = json.loads(text)
            return result if isinstance(result, dict) else None
        except json.JSONDecodeError:
            pass

        # Layer 2: prose-wrapped — pluck the longest {...} candidate.
        start = text.find("{")
        if start == -1:
            return None
        end = text.rfind("}")
        if end > start:
            try:
                result = json.loads(text[start : end + 1])
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        # Layer 3: truncation recovery.
        return OpenAICompatibleRouter._recover_truncated_json(text[start:])

    @staticmethod
    def _recover_truncated_json(text: str) -> dict[str, Any] | None:
        """Best-effort parse of JSON cut off by max_tokens.

        Scans ``text`` once tracking string/escape state and a bracket
        stack. On EOF we (a) close any half-open string, (b) drop a dangling
        comma or bare key prefix, then (c) close the bracket stack in reverse
        order. If that still doesn't parse we fall back to truncating at the
        last comma seen at top-level depth and closing the object — that
        recovers the leading complete key/value pairs even if a later one
        got chopped mid-value.
        """
        if not text or text[0] != "{":
            return None

        in_string = False
        escape = False
        stack: list[str] = []
        # Index just before the comma that ended the most recent fully
        # complete top-level key/value pair (so [:idx] + "}" is valid JSON).
        last_complete_pair_end = -1

        for i, ch in enumerate(text):
            if escape:
                escape = False
                continue
            if in_string:
                if ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
                continue
            if ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]":
                if stack and stack[-1] == ch:
                    stack.pop()
            elif ch == "," and len(stack) == 1 and stack[0] == "}":
                last_complete_pair_end = i

        # Strategy A: close what's open.
        # 1) close a half-open string with a quote
        # 2) trim trailing whitespace + a dangling comma (``{"a":1,`` → ``{"a":1``)
        # 3) close pending brackets in reverse stack order
        # Cases this leaves invalid (e.g. ``{"a":1,"b`` produces ``{"a":1,"b"}``
        # — a key with no value) fall through to Strategy B.
        candidate = text
        if in_string:
            candidate += '"'
        candidate = candidate.rstrip().rstrip(",").rstrip()
        candidate += "".join(reversed(stack))
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        # Strategy B: keep only the prefix up to the last complete
        # top-level pair, then close the object.
        if last_complete_pair_end > 0:
            prefix = text[:last_complete_pair_end] + "}"
            try:
                result = json.loads(prefix)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            val = float(value)
        except (TypeError, ValueError):
            return 0.0
        if val < 0:
            return 0.0
        if val > 1:
            return 1.0
        return val
