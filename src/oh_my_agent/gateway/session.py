from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from oh_my_agent.memory.session_diary import Role

if TYPE_CHECKING:
    from oh_my_agent.agents.registry import AgentRegistry
    from oh_my_agent.gateway.base import BaseChannel
    from oh_my_agent.memory.session_diary import SessionDiaryWriter
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
    diary_writer: SessionDiaryWriter | None = None

    # In-memory cache: thread_id → list of turns
    _cache: dict[str, list[dict]] = field(default_factory=dict)
    # Per-thread load locks so two concurrent first-touches don't both load
    # and orphan one of the cached lists (turns appended to the loser would
    # silently vanish from the in-memory view).
    _load_locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    async def get_history(self, thread_id: str) -> list[dict]:
        """Return the conversation history for *thread_id*.

        Loads from the memory store on first access, then uses the cache.
        """
        if thread_id in self._cache:
            return self._cache[thread_id]

        lock = self._load_locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            # Double-checked: a concurrent first-touch may have loaded while
            # we waited on the lock.
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

    def invalidate(self, thread_id: str) -> None:
        """Drop the cached history so the next read reloads from the store."""
        self._cache.pop(thread_id, None)

    async def append_user(
        self,
        thread_id: str,
        content: str,
        author: str,
        attachments: list | None = None,
    ) -> int | None:
        """Append a user turn. Returns the persisted row id (None if no store)."""
        turn: dict = {"role": "user", "content": content, "author": author}
        if attachments:
            turn["attachments"] = [
                {"filename": a.filename, "content_type": a.content_type}
                for a in attachments
            ]
        history = await self.get_history(thread_id)
        history.append(turn)
        row_id: int | None = None
        if self.memory_store:
            row_id = await self.memory_store.append(
                self.platform, self.channel_id, thread_id, turn,
            )
            turn["_id"] = row_id
        if self.diary_writer is not None:
            try:
                await self.diary_writer.append(
                    role="user",
                    platform=self.platform,
                    channel_id=self.channel_id,
                    thread_id=thread_id,
                    author=author,
                    content=content,
                )
            except Exception:
                logger.debug("diary_writer.append(user) failed", exc_info=True)
        return row_id

    async def append_assistant(self, thread_id: str, content: str, agent_name: str) -> int | None:
        """Append an assistant turn. Returns the persisted row id (None if no store)."""
        turn: dict[str, Any] = {"role": "assistant", "content": content, "agent": agent_name}
        history = await self.get_history(thread_id)
        history.append(turn)
        row_id: int | None = None
        if self.memory_store:
            row_id = await self.memory_store.append(
                self.platform, self.channel_id, thread_id, turn,
            )
            turn["_id"] = row_id
        if self.diary_writer is not None:
            try:
                await self.diary_writer.append(
                    role="assistant",
                    platform=self.platform,
                    channel_id=self.channel_id,
                    thread_id=thread_id,
                    author=agent_name,
                    content=content,
                )
            except Exception:
                logger.debug("diary_writer.append(assistant) failed", exc_info=True)
        return row_id

    async def append_diary_only(
        self,
        thread_id: str,
        content: str,
        *,
        role: Role = "system",
        author: str = "runtime",
    ) -> None:
        """Write a turn to the diary without touching MemoryStore or _cache.

        Used by automation status pings so they remain operator-visible
        in the diary without polluting Judge memory inputs.
        """
        if self.diary_writer is None:
            return
        try:
            await self.diary_writer.append(
                role=role,
                platform=self.platform,
                channel_id=self.channel_id,
                thread_id=thread_id,
                author=author,
                content=content,
            )
        except Exception:
            logger.debug("diary_writer.append_diary_only failed", exc_info=True)

    async def delete_turn(self, thread_id: str, turn_id: int) -> None:
        """Remove a single turn by row id from both the cache and the store.

        Precise error-path cleanup: never a blind ``history.pop()`` (which can
        drop a concurrently-appended foreign turn instead of the intended one).
        """
        cached = self._cache.get(thread_id)
        if cached is not None:
            cached[:] = [t for t in cached if t.get("_id") != turn_id]
        if self.memory_store:
            await self.memory_store.delete_turn(
                self.platform, self.channel_id, thread_id, turn_id,
            )

    async def clear_history(self, thread_id: str) -> None:
        """Delete all history for a thread (cache + store)."""
        self._cache.pop(thread_id, None)
        if self.memory_store:
            await self.memory_store.delete_thread(
                self.platform, self.channel_id, thread_id,
            )
