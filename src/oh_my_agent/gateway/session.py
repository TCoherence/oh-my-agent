from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oh_my_agent.agents.registry import AgentRegistry
    from oh_my_agent.gateway.base import BaseChannel


@dataclass
class ChannelSession:
    """Per-channel state: bound agent registry + per-thread conversation histories."""

    platform: str
    channel_id: str
    channel: BaseChannel
    registry: AgentRegistry

    # thread_id → list of conversation turns
    # Each turn: {"role": "user"|"assistant", "content": str,
    #             "author"?: str, "agent"?: str}
    histories: dict[str, list[dict]] = field(default_factory=dict)

    def get_history(self, thread_id: str) -> list[dict]:
        return self.histories.setdefault(thread_id, [])

    def append_user(self, thread_id: str, content: str, author: str) -> None:
        self.get_history(thread_id).append(
            {"role": "user", "content": content, "author": author}
        )

    def append_assistant(self, thread_id: str, content: str, agent_name: str) -> None:
        self.get_history(thread_id).append(
            {"role": "assistant", "content": content, "agent": agent_name}
        )
