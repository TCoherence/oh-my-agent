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
    """Interface for a CLI-backed AI agent."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def run(self, prompt: str) -> AgentResponse: ...
