"""Session diary — human-readable daily markdown of every turn.

A chronological per-day log of conversations. Sits alongside the SQLite store
and MEMORY.md but is strictly append-only and never replayed by the agent.
The purpose is operator-visible: grep the diary when you want to see what a
given thread did on a given day without spelunking into the SQLite rows.

One instance per process. Writes go through an internal asyncio queue so
callers can fire-and-forget from hot paths without blocking on disk I/O and
without worrying about interleaving when two threads flush at once.

File layout (under ``runtime_root/diary/``)::

    diary/
        2026-04-24.md
        2026-04-25.md
        ...

Each entry renders as:

    ## 14:03:21 · discord#12345 · thread:t9 · user:alice
    > first line of the user message

    ## 14:03:24 · discord#12345 · thread:t9 · assistant:claude
    first line of the assistant response
    ...
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


Role = Literal["user", "assistant", "system"]


@dataclass
class DiaryEntry:
    """One turn worth of context for the diary file."""

    role: Role
    platform: str
    channel_id: str
    thread_id: str
    author: str  # user name or agent name
    content: str
    ts: datetime


class SessionDiaryWriter:
    """Queued markdown-diary writer.

    Single instance per process. ``append(entry)`` enqueues the entry and
    returns immediately; a background worker task drains the queue and appends
    to the appropriate per-day file.  Because the worker is single-threaded,
    writes never interleave even when multiple gateway callers flush at once.
    """

    def __init__(self, diary_dir: str | Path) -> None:
        self._diary_dir = Path(diary_dir).expanduser().resolve()
        self._queue: asyncio.Queue[DiaryEntry | None] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._started = False
        self._stopped = False

    @property
    def diary_dir(self) -> Path:
        return self._diary_dir

    def start(self) -> None:
        """Launch the background drain task (safe to call multiple times)."""
        if self._started or self._stopped:
            return
        self._started = True
        self._diary_dir.mkdir(parents=True, exist_ok=True)
        self._worker = asyncio.create_task(self._run(), name="session-diary:worker")

    async def stop(self) -> None:
        """Drain the queue and stop the worker."""
        if not self._started or self._stopped:
            return
        self._stopped = True
        await self._queue.put(None)
        if self._worker is not None:
            try:
                await asyncio.wait_for(self._worker, timeout=5)
            except asyncio.TimeoutError:
                self._worker.cancel()

    async def append(
        self,
        *,
        role: Role,
        platform: str,
        channel_id: str,
        thread_id: str,
        author: str,
        content: str,
        ts: datetime | None = None,
    ) -> None:
        if self._stopped:
            return
        if not self._started:
            # Auto-start on first append so callers don't have to remember.
            self.start()
        entry = DiaryEntry(
            role=role,
            platform=platform,
            channel_id=channel_id,
            thread_id=thread_id,
            author=author,
            content=content,
            ts=ts or datetime.now(),
        )
        await self._queue.put(entry)

    async def _run(self) -> None:
        while True:
            entry = await self._queue.get()
            if entry is None:
                return
            try:
                self._write_entry(entry)
            except Exception:
                logger.warning("SessionDiaryWriter failed to persist entry", exc_info=True)

    def _write_entry(self, entry: DiaryEntry) -> None:
        day = entry.ts.strftime("%Y-%m-%d")
        path = self._diary_dir / f"{day}.md"
        path.parent.mkdir(parents=True, exist_ok=True)

        body = self._format_entry(entry)
        # Atomic append — open in append mode, let the OS handle ordering (we
        # have one producer so no interleaving concern).
        with path.open("a", encoding="utf-8") as fh:
            fh.write(body)

    def _format_entry(self, entry: DiaryEntry) -> str:
        timestamp = entry.ts.strftime("%H:%M:%S")
        header = (
            f"## {timestamp} · {entry.platform}#{entry.channel_id} · "
            f"thread:{entry.thread_id} · {entry.role}:{entry.author}"
        )
        content = entry.content.rstrip()
        if entry.role == "user":
            # Quote user turns so they're visually distinct from assistant text.
            quoted = "\n".join(f"> {line}" for line in content.splitlines()) if content else "> (empty)"
            return f"{header}\n{quoted}\n\n"
        return f"{header}\n{content}\n\n"
