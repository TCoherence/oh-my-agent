from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeAlias

# Streaming hook: called with the *accumulated* assistant text each time the
# agent emits a new TextEvent. Agents that don't support streaming just never
# call it. Callers are expected to be cheap / async-safe — agents do not block
# their own progress on this callback.
PartialTextHook: TypeAlias = Callable[[str], Awaitable[None]]

# Tool-use hook: called with the tool name each time the agent emits a
# ToolUseEvent. Lets the chat layer surface "⚙ using Read" / tool count in
# the live anchor without leaking full tool input/output. Optional: agents
# that don't emit tool events (e.g. Gemini) simply never call it.
ToolUseHook: TypeAlias = Callable[[str], Awaitable[None]]


@dataclass
class AgentResponse:
    """Result from an agent invocation.

    ``error_kind`` classifies failures so callers can decide retry policy.
    Known values:

    - ``max_turns``    — model exhausted its turn budget (structural; bump budget to retry)
    - ``timeout``      — subprocess wall-clock exceeded (often transient)
    - ``rate_limit``   — provider returned 429 / quota / rate-limit signal (transient; backoff)
    - ``api_5xx``      — provider returned 5xx / overloaded / upstream error (transient; backoff)
    - ``auth``         — credentials missing / invalid / expired (structural; no retry)
    - ``cli_error``    — catch-all for non-classified CLI failures (structural by default)

    Agents should populate ``error_kind`` whenever ``error`` is set. Unknown
    failures fall back to ``cli_error``.
    """

    text: str
    raw: dict | None = None
    error: str | None = None
    # Token usage and cost, populated by agents that support it (e.g. ClaudeAgent).
    # Keys: input_tokens, output_tokens, cache_read_input_tokens,
    #       cache_creation_input_tokens, cost_usd (all optional).
    usage: dict | None = None
    error_kind: str | None = None
    partial_text: str | None = None
    terminal_reason: str | None = None


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
