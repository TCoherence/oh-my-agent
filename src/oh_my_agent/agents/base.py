from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AgentResponse:
    """Result from an agent invocation."""

    text: str
    raw: dict | None = None
    error: str | None = None


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
