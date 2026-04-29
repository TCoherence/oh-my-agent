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
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = int(timeout_seconds)
        self._confidence_threshold = float(confidence_threshold)
        self._max_retries = max(0, int(max_retries))
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
            "max_tokens": 400,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": (f"{context}\n\nCurrent user message:\n{message[:1500]}" if context else message[:1500])},
            ],
        }
        payload: dict[str, Any] = {**self._extra_body, **core_payload}
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
                "Router returned non-JSON content model=%s sample=%r",
                self._model,
                text[:200],
            )
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
        text = text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
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
