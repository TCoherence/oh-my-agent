from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oh_my_agent.agents.registry import AgentRegistry
    from oh_my_agent.gateway.base import BaseChannel
    from oh_my_agent.memory.store import MemoryStore

logger = logging.getLogger(__name__)


@dataclass
class ChannelSession:
    """Per-channel state: bound agent registry + per-thread conversation histories.

    When a ``memory_store`` is provided, histories are loaded from and
    persisted to the store.  An in-memory cache avoids repeated DB reads
    within the same bot lifetime.
    """

    platform: str
    channel_id: str
    channel: BaseChannel
    registry: AgentRegistry
    memory_store: MemoryStore | None = None

    # In-memory cache: thread_id → list of turns
    _cache: dict[str, list[dict]] = field(default_factory=dict)

    async def get_history(self, thread_id: str) -> list[dict]:
        """Return the conversation history for *thread_id*.

        Loads from the memory store on first access, then uses the cache.
        """
        if thread_id in self._cache:
            return self._cache[thread_id]

        if self.memory_store:
            turns = await self.memory_store.load_history(
                self.platform, self.channel_id, thread_id,
            )
            self._cache[thread_id] = turns
            return turns

        self._cache[thread_id] = []
        return self._cache[thread_id]

    async def append_user(self, thread_id: str, content: str, author: str) -> None:
        turn = {"role": "user", "content": content, "author": author}
        history = await self.get_history(thread_id)
        history.append(turn)
        if self.memory_store:
            row_id = await self.memory_store.append(
                self.platform, self.channel_id, thread_id, turn,
            )
            turn["_id"] = row_id

    async def append_assistant(self, thread_id: str, content: str, agent_name: str) -> None:
        turn = {"role": "assistant", "content": content, "agent": agent_name}
        history = await self.get_history(thread_id)
        history.append(turn)
        if self.memory_store:
            row_id = await self.memory_store.append(
                self.platform, self.channel_id, thread_id, turn,
            )
            turn["_id"] = row_id

    async def clear_history(self, thread_id: str) -> None:
        """Delete all history for a thread (cache + store)."""
        self._cache.pop(thread_id, None)
        if self.memory_store:
            await self.memory_store.delete_thread(
                self.platform, self.channel_id, thread_id,
            )
