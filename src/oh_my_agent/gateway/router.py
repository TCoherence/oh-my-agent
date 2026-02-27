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
    decision: str  # "reply_once" | "propose_task"
    confidence: float
    goal: str
    risk_hints: list[str]
    raw_text: str


class OpenAICompatibleRouter:
    """LLM classifier for deciding chat reply vs runtime task proposal."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 3,
        confidence_threshold: float = 0.55,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = int(timeout_seconds)
        self._confidence_threshold = float(confidence_threshold)

    @property
    def confidence_threshold(self) -> float:
        return self._confidence_threshold

    async def route(self, message: str) -> RouteDecision | None:
        payload = {
            "model": self._model,
            "temperature": 0,
            "max_tokens": 300,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Classify whether the user message should be handled as "
                        "a one-off chat reply or a multi-step autonomous coding task.\n"
                        "Output strict JSON only with keys: decision, confidence, goal, risk_hints.\n"
                        "decision must be 'reply_once' or 'propose_task'.\n"
                        "If propose_task, write a concise executable goal.\n"
                        "If reply_once, goal can be empty string.\n"
                        "confidence must be a float between 0 and 1."
                    ),
                },
                {"role": "user", "content": message[:1500]},
            ],
        }
        try:
            data = await asyncio.to_thread(self._post_json, payload)
        except Exception as exc:
            logger.warning("Router request failed: %s", exc)
            return None

        text = self._extract_content(data)
        if not text:
            return None
        parsed = self._parse_json(text)
        if not parsed:
            return None

        decision = str(parsed.get("decision", "reply_once")).strip().lower()
        if decision not in {"reply_once", "propose_task"}:
            decision = "reply_once"

        confidence = self._to_float(parsed.get("confidence", 0.0))
        goal = str(parsed.get("goal", "")).strip()
        hints = parsed.get("risk_hints", [])
        risk_hints = [str(h).strip() for h in hints if str(h).strip()] if isinstance(hints, list) else []

        return RouteDecision(
            decision=decision,
            confidence=confidence,
            goal=goal,
            risk_hints=risk_hints,
            raw_text=text[:2000],
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
