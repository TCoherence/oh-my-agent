"""Typed streaming events emitted by CLI agents.

Each ``AgentEvent`` subclass corresponds to one semantic chunk the agent produced
during a run — a piece of visible assistant text, a tool invocation, a thinking
block, a session-init line, or a terminal error/complete marker.

Consumers (runtime progress, session diary, future TUI) iterate the async
stream and pattern-match on ``kind``. The events are designed to be
transport-independent: Claude's stream-json, Codex's JSONL event stream, and a
plaintext fallback for Gemini all converge on this schema.
"""

from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="ignore")


class TextEvent(_EventBase):
    kind: Literal["text"] = "text"
    text: str
    agent: str | None = None


class ThinkingEvent(_EventBase):
    kind: Literal["thinking"] = "thinking"
    text: str
    agent: str | None = None


class ToolUseEvent(_EventBase):
    kind: Literal["tool_use"] = "tool_use"
    tool_id: str = ""
    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    agent: str | None = None


class ToolResultEvent(_EventBase):
    kind: Literal["tool_result"] = "tool_result"
    tool_id: str = ""
    name: str = ""
    output: str = ""
    is_error: bool = False
    agent: str | None = None


class SystemInitEvent(_EventBase):
    """Emitted once when the CLI reports its session id / model / tools."""

    kind: Literal["system_init"] = "system_init"
    session_id: str | None = None
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    raw: dict[str, Any] | None = None
    agent: str | None = None


class UsageEvent(_EventBase):
    """Token usage report. Claude emits this in the final ``result`` frame."""

    kind: Literal["usage"] = "usage"
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cost_usd: float | None = None
    agent: str | None = None


class ErrorEvent(_EventBase):
    kind: Literal["error"] = "error"
    message: str
    error_kind: str | None = None
    agent: str | None = None


class CompleteEvent(_EventBase):
    """Terminal marker with aggregated text; yielded at the end of a stream."""

    kind: Literal["complete"] = "complete"
    text: str = ""
    session_id: str | None = None
    agent: str | None = None


AgentEvent = Union[
    TextEvent,
    ThinkingEvent,
    ToolUseEvent,
    ToolResultEvent,
    SystemInitEvent,
    UsageEvent,
    ErrorEvent,
    CompleteEvent,
]

__all__ = [
    "AgentEvent",
    "TextEvent",
    "ThinkingEvent",
    "ToolUseEvent",
    "ToolResultEvent",
    "SystemInitEvent",
    "UsageEvent",
    "ErrorEvent",
    "CompleteEvent",
]
