from __future__ import annotations

import logging

from oh_my_agent.agents.api.base import BaseAPIAgent
from oh_my_agent.agents.base import AgentResponse

logger = logging.getLogger(__name__)


class AnthropicAPIAgent(BaseAPIAgent):
    """Agent that calls Anthropic API directly (supports multi-turn conversation).

    .. deprecated:: 0.4.0
        Use :class:`~oh_my_agent.agents.cli.claude.ClaudeAgent` instead.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 8192,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens

    @property
    def name(self) -> str:
        return "anthropic-api"

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
    ) -> AgentResponse:
        try:
            import anthropic
        except ImportError:
            return AgentResponse(
                text="",
                error="anthropic SDK not installed. Run: pip install anthropic",
            )

        messages = self._build_messages(prompt, history)

        try:
            client = anthropic.AsyncAnthropic(api_key=self._api_key)
            response = await client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=messages,
            )
            text = response.content[0].text if response.content else ""
            return AgentResponse(text=text, raw=response.model_dump())
        except Exception as exc:
            logger.error("Anthropic API error: %s", exc)
            return AgentResponse(text="", error=str(exc))

    def _build_messages(
        self, prompt: str, history: list[dict] | None
    ) -> list[dict]:
        messages = []
        for turn in history or []:
            role = turn.get("role", "user")
            if role not in ("user", "assistant"):
                role = "user"
            messages.append({"role": role, "content": turn["content"]})
        messages.append({"role": "user", "content": prompt})
        return messages
