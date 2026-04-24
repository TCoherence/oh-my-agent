"""Idle-trigger scheduler for the memory Judge.

Tracks per-thread last-message timestamps. A background task wakes every
``poll_interval_seconds`` and fires ``on_fire(thread_key)`` for any thread that
has been silent for ``idle_seconds`` and hasn't been judged since its last
message.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class _ThreadState:
    last_message_ts: float = 0.0
    last_judge_ts: float = 0.0
    pending: bool = False  # True when a fire is in-flight
    metadata: dict = field(default_factory=dict)


# A thread "key" identifies a unique conversation across platforms — the
# manager passes a (platform, channel_id, thread_id) tuple flattened to a string.
ThreadKey = str
FireCallback = Callable[[ThreadKey, dict], Awaitable[None]]


class IdleTracker:
    """In-memory per-thread idle scheduler."""

    def __init__(
        self,
        on_fire: FireCallback,
        *,
        idle_seconds: float = 15 * 60,
        poll_interval_seconds: float = 60.0,
    ) -> None:
        if idle_seconds < 1:
            raise ValueError("idle_seconds must be ≥ 1")
        if poll_interval_seconds < 1:
            raise ValueError("poll_interval_seconds must be ≥ 1")
        self._on_fire = on_fire
        self._idle_seconds = float(idle_seconds)
        self._poll_interval = float(poll_interval_seconds)
        self._states: dict[ThreadKey, _ThreadState] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def idle_seconds(self) -> float:
        return self._idle_seconds

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop(), name="memory-idle-tracker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.TimeoutError:
            self._task.cancel()
        self._task = None

    async def touch(self, thread_key: ThreadKey, *, metadata: dict | None = None) -> None:
        """Record a message arrival on this thread (resets idle timer)."""
        async with self._lock:
            state = self._states.get(thread_key)
            if state is None:
                state = _ThreadState()
                self._states[thread_key] = state
            state.last_message_ts = time.time()
            state.pending = False
            if metadata:
                state.metadata.update(metadata)

    async def mark_judged(self, thread_key: ThreadKey) -> None:
        async with self._lock:
            state = self._states.get(thread_key)
            if state is not None:
                state.last_judge_ts = time.time()
                state.pending = False

    async def forget(self, thread_key: ThreadKey) -> None:
        async with self._lock:
            self._states.pop(thread_key, None)

    async def _run_loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
                    return
                except asyncio.TimeoutError:
                    pass
                await self._tick()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("memory idle tracker crashed: %s", exc)

    async def _tick(self) -> None:
        now = time.time()
        to_fire: list[tuple[ThreadKey, dict]] = []
        async with self._lock:
            for key, state in list(self._states.items()):
                if state.pending:
                    continue
                if state.last_message_ts <= 0:
                    continue
                if state.last_judge_ts >= state.last_message_ts:
                    continue
                if (now - state.last_message_ts) < self._idle_seconds:
                    continue
                state.pending = True
                to_fire.append((key, dict(state.metadata)))
        for key, meta in to_fire:
            try:
                await self._on_fire(key, meta)
            except Exception as exc:  # noqa: BLE001
                logger.warning("memory idle fire failed for %s: %s", key, exc)
            finally:
                async with self._lock:
                    refreshed = self._states.get(key)
                    if refreshed is not None:
                        refreshed.pending = False
                        refreshed.last_judge_ts = time.time()
