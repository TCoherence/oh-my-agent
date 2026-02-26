from __future__ import annotations

import logging

from oh_my_agent.agents.api.base import BaseAPIAgent
from oh_my_agent.agents.base import AgentResponse

logger = logging.getLogger(__name__)


class OpenAIAPIAgent(BaseAPIAgent):
    """Agent that calls OpenAI API directly (supports multi-turn conversation).

    .. deprecated:: 0.4.0
        API agents are deprecated. Use CLI agents instead.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        max_tokens: int = 4096,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "openai-api"

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
    ) -> AgentResponse:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            return AgentResponse(
                text="",
                error="openai SDK not installed. Run: pip install openai",
            )

        messages = self._build_messages(prompt, history)

        try:
            client = AsyncOpenAI(api_key=self._api_key)
            response = await client.chat.completions.create(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=messages,
            )
            text = response.choices[0].message.content or ""
            return AgentResponse(text=text)
        except Exception as exc:
            logger.error("OpenAI API error: %s", exc)
            return AgentResponse(text="", error=str(exc))

    def _build_messages(
        self, prompt: str, history: list[dict] | None
    ) -> list[dict]:
        messages = []
        for turn in history or []:
            role = turn.get("role", "user")
            if role not in ("user", "assistant", "system"):
                role = "user"
            messages.append({"role": role, "content": turn["content"]})
        messages.append({"role": "user", "content": prompt})
        return messages
