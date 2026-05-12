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

    Canonical intent set (3 values, oriented around "does this modify
    the source repo?"):

    - ``reply`` — single-turn chat reply; no task is created.
    - ``artifact`` — produce a deliverable that does NOT modify the
      source repo (research report, summary, news digest, OR a known
      skill invocation). Defaults to auto-approve / start-immediately;
      set ``force_draft=True`` to add an approval gate (useful for
      expensive long-running artifacts).
    - ``repo_update`` — modify the source repo: code edit, refactor,
      OR skill source create-or-repair. The dispatcher decides
      ``skill_change`` vs ``repo_change`` task type by checking whether
      ``skill_name`` is set. Always requires user approval.

    Pre-v2 intent names (``chat_reply``, ``invoke_skill``,
    ``oneoff_artifact``, ``propose_repo_change``, ``update_skill``, plus
    older aliases like ``reply_once``, ``create_skill``, ``repair_skill``)
    are accepted at parse time and normalized to canonical. See
    ``_INTENT_ALIASES``.
    """

    decision: str
    confidence: float
    goal: str
    risk_hints: list[str]
    raw_text: str
    skill_name: str | None = None
    task_type: str | None = None
    completion_mode: str | None = None
    # When True the dispatcher creates an approval-gated DRAFT task
    # instead of starting immediately. Only meaningful for ``artifact``
    # (``repo_update`` always drafts regardless of this flag).
    force_draft: bool = False

    def __post_init__(self) -> None:
        # Normalize ``decision`` on every RouteDecision construction so:
        # (a) router-parsed payloads emitting legacy intent names land in
        #     canonical form ready for dispatcher match
        # (b) test fixtures and any direct constructor users can use either
        #     v1 (chat_reply / oneoff_artifact / propose_repo_change /
        #     update_skill / invoke_skill) or v2 names — both work without
        #     test churn during the v1→v2 migration window
        # ``frozen=True`` blocks regular assignment so we go through
        # ``object.__setattr__`` (the documented escape hatch).
        normalized = normalize_intent(self.decision)
        if normalized != self.decision:
            object.__setattr__(self, "decision", normalized)


_RESERVED_PAYLOAD_KEYS = frozenset({"messages", "model", "max_tokens", "temperature"})

# Canonical intents — what the v2 router prompt asks the model to output.
_CANONICAL_INTENTS = frozenset({
    "reply",
    "artifact",
    "repo_update",
})

# Intent name normalization. Includes:
# (a) v1 canonical names (chat_reply / invoke_skill / oneoff_artifact /
#     propose_repo_change / update_skill) so older router models still work.
# (b) pre-v1 legacy aliases (reply_once / create_skill / repair_skill etc.)
#     accepted from very old fixtures or hand-typed decision strings.
# All normalized to v2 canonical at parse time so ``RouteDecision.decision``
# is always one of ``_CANONICAL_INTENTS``.
_INTENT_ALIASES: dict[str, str] = {
    # v1 canonical → v2 canonical
    "chat_reply": "reply",
    "invoke_skill": "artifact",  # resolution check happens in dispatcher
    "oneoff_artifact": "artifact",
    "propose_repo_change": "repo_update",
    "update_skill": "repo_update",  # dispatcher decides create-vs-repair
    # Pre-v1 legacy → v2 canonical
    "reply_once": "reply",
    "invoke_existing_skill": "artifact",
    "propose_artifact_task": "artifact",
    "propose_repo_task": "repo_update",
    "propose_task": "repo_update",
    "create_skill": "repo_update",
    "repair_skill": "repo_update",
}


def normalize_intent(raw: str) -> str:
    """Map a raw intent string (v2 canonical or any legacy form) to v2 canonical.

    Returns ``"reply"`` for unknown values — the safe default that keeps
    the runtime in chat mode rather than spawning a task.
    """
    cleaned = raw.strip().lower()
    if cleaned in _CANONICAL_INTENTS:
        return cleaned
    aliased = _INTENT_ALIASES.get(cleaned)
    if aliased is not None:
        return aliased
    return "reply"


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
            "Classify the user message into one of three intents, organized around "
            "WHETHER THE TASK MODIFIES THE SOURCE REPO:\n"
            "  reply        — single-turn chat answer (no task spawned). DEFAULT.\n"
            "  artifact     — produce a deliverable that does NOT modify the source "
            "repo (research report, summary, news digest, analysis), OR invoke a "
            "known skill. Defaults to auto-approve / start-immediately; set "
            "force_draft=true only for expensive long-running ones.\n"
            "  repo_update  — modify the source repo: edit code, fix bugs, refactor, "
            "write tests, OR create/repair a skill (when skill_name is set). Always "
            "requires user approval.\n\n"
            "DISAMBIGUATION RULES (read first):\n"
            "- Default to `reply` when in doubt — zero blast radius.\n"
            "- For one-time deliverables (keywords: 报告/总结/调研/分析/research/summary/"
            "brief/report/headlines/digest), choose `artifact` with task_type='artifact'.\n"
            "- For invoking a known skill on this single request (e.g. 'run paper-digest "
            "on today's arxiv'), choose `artifact` with skill_name set to the known skill. "
            "Known skills may be provided as name plus description; prefer this when the "
            "current message semantically matches one of those descriptions, or when "
            "recent context shows a known skill was just merged / synced / recently used "
            "and the current message is a follow-up asking to run it / try again.\n"
            "- For skill SOURCE create-or-repair (build a new skill / edit existing skill "
            "behavior — keywords: create a skill / 创建/新建/生成 skill / package as a "
            "workflow / 改一下 skill / fix this skill), choose `repo_update` with "
            "skill_name set. The dispatcher decides create-vs-repair by whether "
            "skill_name matches a registered skill. In the feedback case keep skill_name "
            "pointed at the existing skill — do not invent a parallel new skill just "
            "because the user mentions an external repo, project, or tool.\n"
            "- For generic repository edits (fix typo, add feature, run tests), choose "
            "`repo_update` WITHOUT skill_name.\n"
            "- Never infer skill-creation intent just because the word 'skill' appears in "
            "prior context or the known-skills list.\n"
            "- When both artifact and repo_update could apply, prefer `artifact` (lower "
            "blast radius: no repo modification).\n\n"
            "Output strict JSON only with keys: decision, confidence, goal, risk_hints, "
            "skill_name, task_type, completion_mode, force_draft.\n"
            "decision must be one of: reply, artifact, repo_update.\n"
            "If artifact WITH skill_name, keep goal empty when possible.\n"
            "If artifact WITHOUT skill_name, write a concise executable goal; "
            "task_type='artifact', completion_mode='reply'.\n"
            "If repo_update WITH skill_name, write a concise goal; "
            "task_type='skill_change', completion_mode='merge'; "
            "skill_name as hyphen-case slug.\n"
            "If repo_update WITHOUT skill_name, write a concise executable goal; "
            "task_type='repo_change', completion_mode='merge'.\n"
            "If reply, goal can be empty string and skill_name should be empty string.\n"
            "force_draft (bool, default false): set true ONLY for `artifact` decisions "
            "when the task is expected to take 10+ minutes or invokes a heavy skill "
            "(market-briefing-*, paper-digest, youtube-podcast-digest, deals-scanner). "
            "Lets the user approve-before-execute. Ignored for `repo_update` "
            "(always drafts) and `reply` (no task).\n"
            "confidence must be a float between 0 and 1.\n\n"
            "EXAMPLES:\n"
            "- User: \"调研 Jensen Huang 过去 3 年所有公开演讲，整理成报告\"\n"
            "  → {\"decision\":\"artifact\",\"task_type\":\"artifact\","
            "\"completion_mode\":\"reply\",\"force_draft\":true}\n"
            "- User: \"帮我做一个 skill，每天总结 AI 领域的新论文\"\n"
            "  → {\"decision\":\"repo_update\",\"skill_name\":\"ai-paper-daily-digest\","
            "\"task_type\":\"skill_change\",\"completion_mode\":\"merge\"}\n"
            "- User: \"paper-digest 这个 skill 昨天输出的 summary 太短了，改一下\"\n"
            "  → {\"decision\":\"repo_update\",\"skill_name\":\"paper-digest\","
            "\"task_type\":\"skill_change\",\"completion_mode\":\"merge\"}\n"
            "- User: \"用 paper-digest 看一下今天 arxiv 的 LLM 板块\"\n"
            "  → {\"decision\":\"artifact\",\"skill_name\":\"paper-digest\"}\n"
            "- User: \"把 README 里的 typo 修一下\"\n"
            "  → {\"decision\":\"repo_update\",\"task_type\":\"repo_change\","
            "\"completion_mode\":\"merge\"}\n"
            "- User: \"现在几点了\"\n"
            "  → {\"decision\":\"reply\"}"
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
        # ``force_draft`` is a v2 field. Older routers won't emit it; treat
        # missing / non-bool as False. JSON booleans deserialize as Python
        # bool already; accept the literal strings "true"/"false" too for
        # robustness against models that emit them as strings.
        raw_force_draft = parsed.get("force_draft", False)
        if isinstance(raw_force_draft, bool):
            force_draft = raw_force_draft
        elif isinstance(raw_force_draft, str):
            force_draft = raw_force_draft.strip().lower() == "true"
        else:
            force_draft = False

        return RouteDecision(
            decision=decision,
            confidence=confidence,
            goal=goal,
            risk_hints=risk_hints,
            raw_text=text[:2000],
            skill_name=skill_name,
            task_type=task_type,
            completion_mode=completion_mode,
            force_draft=force_draft,
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
