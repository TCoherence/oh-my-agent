from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AgentResponse:
    """Result from an agent invocation."""

    text: str
    raw: dict | None = None
    error: str | None = None
    # Token usage and cost, populated by agents that support it (e.g. ClaudeAgent).
    # Keys: input_tokens, output_tokens, cache_read_input_tokens,
    #       cache_creation_input_tokens, cost_usd (all optional).
    usage: dict | None = None


class BaseAgent(ABC):
    """Interface for all AI agents (CLI or API)."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
    ) -> AgentResponse:
        """Execute the agent.

        Args:
            prompt: The current user message.
            history: Prior turns in the conversation.
                     Each entry: {"role": "user"|"assistant", "content": str,
                                  "author"?: str, "agent"?: str}
        """
        ...
