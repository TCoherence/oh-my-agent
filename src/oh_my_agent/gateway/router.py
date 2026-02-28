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
    decision: str  # "reply_once" | "invoke_existing_skill" | "propose_artifact_task" | "propose_repo_task" | "create_skill" | "repair_skill"
    confidence: float
    goal: str
    risk_hints: list[str]
    raw_text: str
    skill_name: str | None = None
    task_type: str | None = None
    completion_mode: str | None = None


class OpenAICompatibleRouter:
    """LLM classifier for deciding chat reply vs runtime task proposal."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 8,
        confidence_threshold: float = 0.55,
        max_retries: int = 1,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = int(timeout_seconds)
        self._confidence_threshold = float(confidence_threshold)
        self._max_retries = max(0, int(max_retries))

    @property
    def confidence_threshold(self) -> float:
        return self._confidence_threshold

    async def route(self, message: str, *, context: str | None = None) -> RouteDecision | None:
        payload = {
            "model": self._model,
            "temperature": 0,
            "max_tokens": 300,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Classify whether the user message should be handled as "
                        "a one-off chat reply, a direct invocation of an existing skill, "
                        "a multi-step artifact task, a multi-step repository-change task, "
                        "a skill-creation task, or a repair request for an existing skill.\n"
                        "Output strict JSON only with keys: decision, confidence, goal, risk_hints, skill_name, task_type, completion_mode.\n"
                        "decision must be 'reply_once', 'invoke_existing_skill', 'propose_artifact_task', 'propose_repo_task', 'create_skill', or 'repair_skill'.\n"
                        "If invoke_existing_skill, keep goal empty when possible and provide skill_name if obvious.\n"
                        "If propose_artifact_task, propose_repo_task, create_skill, or repair_skill, write a concise executable goal.\n"
                        "If create_skill or repair_skill, also provide skill_name as a hyphen-case slug.\n"
                        "Use repair_skill when the user is giving feedback on an existing skill or asking to fix/update one based on recent skill output.\n"
                        "If propose_artifact_task, task_type should be 'artifact' and completion_mode should be 'reply' or 'artifact'.\n"
                        "If propose_repo_task, task_type should be 'repo_change' and completion_mode should be 'merge'.\n"
                        "If create_skill or repair_skill, task_type should be 'skill_change' and completion_mode should be 'merge'.\n"
                        "If reply_once, goal can be empty string and skill_name should be empty string.\n"
                        "confidence must be a float between 0 and 1."
                    ),
                },
                {"role": "user", "content": (f"{context}\n\nCurrent user message:\n{message[:1500]}" if context else message[:1500])},
            ],
        }
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

        decision = str(parsed.get("decision", "reply_once")).strip().lower()
        if decision not in {"reply_once", "invoke_existing_skill", "propose_artifact_task", "propose_repo_task", "create_skill", "repair_skill", "propose_task"}:
            decision = "reply_once"
        if decision == "propose_task":
            decision = "propose_repo_task"

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
