from __future__ import annotations

import json
import logging
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Abstract base                                                               #
# --------------------------------------------------------------------------- #


class MemoryStore(ABC):
    """Persistent storage for thread conversation histories."""

    @abstractmethod
    async def init(self) -> None:
        """Create tables / indexes if they don't exist."""
        ...

    @abstractmethod
    async def load_history(
        self,
        platform: str,
        channel_id: str,
        thread_id: str,
        *,
        limit: int | None = None,
    ) -> list[dict]:
        """Load conversation turns for a thread, most-recent last."""
        ...

    @abstractmethod
    async def append(
        self,
        platform: str,
        channel_id: str,
        thread_id: str,
        turn: dict,
    ) -> int:
        """Append a single turn and return its row id."""
        ...

    @abstractmethod
    async def save_summary(
        self,
        platform: str,
        channel_id: str,
        thread_id: str,
        summary: str,
        turns_start: int,
        turns_end: int,
    ) -> None:
        """Store a summary that replaces the turns in [turns_start, turns_end]."""
        ...

    @abstractmethod
    async def delete_thread(
        self,
        platform: str,
        channel_id: str,
        thread_id: str,
    ) -> None:
        ...

    @abstractmethod
    async def search(self, query: str, *, limit: int = 20) -> list[dict]:
        """Full-text search across all threads."""
        ...

    @abstractmethod
    async def count_turns(
        self,
        platform: str,
        channel_id: str,
        thread_id: str,
    ) -> int:
        ...

    @abstractmethod
    async def export_data(self) -> dict[str, Any]:
        """Export all turns and summaries as a JSON-serialisable dict."""
        ...

    @abstractmethod
    async def import_data(self, data: dict[str, Any]) -> int:
        """Import turns and summaries from a previously exported dict.

        Returns the number of turns imported.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        ...


# --------------------------------------------------------------------------- #
#  SQLite implementation                                                        #
# --------------------------------------------------------------------------- #

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform    TEXT NOT NULL,
    channel_id  TEXT NOT NULL,
    thread_id   TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    author      TEXT,
    agent       TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_thread
    ON turns(platform, channel_id, thread_id);

CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts
    USING fts5(content, content=turns, content_rowid=id);

-- triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS turns_au AFTER UPDATE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO turns_fts(turns_fts, rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform    TEXT NOT NULL,
    channel_id  TEXT NOT NULL,
    thread_id   TEXT NOT NULL,
    summary     TEXT NOT NULL,
    turns_start INTEGER NOT NULL,
    turns_end   INTEGER NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class SQLiteMemoryStore(MemoryStore):
    """SQLite-backed memory store with FTS5 full-text search."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = await aiosqlite.connect(str(self._db_path))
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA foreign_keys=ON")
        return self._db

    # -- lifecycle ---------------------------------------------------------

    async def init(self) -> None:
        db = await self._conn()
        await db.executescript(_SCHEMA)
        await db.commit()
        logger.info("Memory store initialised at %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # -- read --------------------------------------------------------------

    async def load_history(
        self,
        platform: str,
        channel_id: str,
        thread_id: str,
        *,
        limit: int | None = None,
    ) -> list[dict]:
        """Return turns for a thread.

        If a summary exists, prepend a synthetic ``system`` turn with the
        summary text, then return the raw turns that come *after* the
        summarised range.
        """
        db = await self._conn()

        # Latest summary for this thread (if any)
        cursor = await db.execute(
            "SELECT summary, turns_end FROM summaries "
            "WHERE platform=? AND channel_id=? AND thread_id=? "
            "ORDER BY id DESC LIMIT 1",
            (platform, channel_id, thread_id),
        )
        summary_row = await cursor.fetchone()

        turns: list[dict] = []
        if summary_row:
            turns.append({
                "role": "system",
                "content": f"[Summary of earlier conversation]\n{summary_row['summary']}",
            })
            after_id = summary_row["turns_end"]
            sql = (
                "SELECT * FROM turns "
                "WHERE platform=? AND channel_id=? AND thread_id=? AND id > ? "
                "ORDER BY id ASC"
            )
            params: tuple = (platform, channel_id, thread_id, after_id)
        else:
            sql = (
                "SELECT * FROM turns "
                "WHERE platform=? AND channel_id=? AND thread_id=? "
                "ORDER BY id ASC"
            )
            params = (platform, channel_id, thread_id)

        if limit is not None:
            sql += f" LIMIT {int(limit)}"

        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        for r in rows:
            turn: dict = {"role": r["role"], "content": r["content"]}
            if r["author"]:
                turn["author"] = r["author"]
            if r["agent"]:
                turn["agent"] = r["agent"]
            turn["_id"] = r["id"]
            turns.append(turn)

        return turns

    # -- write -------------------------------------------------------------

    async def append(
        self,
        platform: str,
        channel_id: str,
        thread_id: str,
        turn: dict,
    ) -> int:
        db = await self._conn()
        cursor = await db.execute(
            "INSERT INTO turns (platform, channel_id, thread_id, role, content, author, agent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                platform,
                channel_id,
                thread_id,
                turn["role"],
                turn["content"],
                turn.get("author"),
                turn.get("agent"),
            ),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def save_summary(
        self,
        platform: str,
        channel_id: str,
        thread_id: str,
        summary: str,
        turns_start: int,
        turns_end: int,
    ) -> None:
        db = await self._conn()
        await db.execute(
            "INSERT INTO summaries (platform, channel_id, thread_id, summary, turns_start, turns_end) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (platform, channel_id, thread_id, summary, turns_start, turns_end),
        )
        # Delete the raw turns that have been summarised
        await db.execute(
            "DELETE FROM turns WHERE platform=? AND channel_id=? AND thread_id=? "
            "AND id BETWEEN ? AND ?",
            (platform, channel_id, thread_id, turns_start, turns_end),
        )
        await db.commit()
        logger.info(
            "Compressed turns %d–%d into summary for thread %s",
            turns_start,
            turns_end,
            thread_id,
        )

    # -- delete ------------------------------------------------------------

    async def delete_thread(
        self,
        platform: str,
        channel_id: str,
        thread_id: str,
    ) -> None:
        db = await self._conn()
        await db.execute(
            "DELETE FROM turns WHERE platform=? AND channel_id=? AND thread_id=?",
            (platform, channel_id, thread_id),
        )
        await db.execute(
            "DELETE FROM summaries WHERE platform=? AND channel_id=? AND thread_id=?",
            (platform, channel_id, thread_id),
        )
        await db.commit()

    # -- search ------------------------------------------------------------

    async def search(self, query: str, *, limit: int = 20) -> list[dict]:
        db = await self._conn()
        cursor = await db.execute(
            "SELECT t.platform, t.channel_id, t.thread_id, t.role, t.content, "
            "       t.author, t.agent, t.created_at "
            "FROM turns_fts f "
            "JOIN turns t ON f.rowid = t.id "
            "WHERE turns_fts MATCH ? "
            "ORDER BY rank "
            "LIMIT ?",
            (query, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # -- stats -------------------------------------------------------------

    async def count_turns(
        self,
        platform: str,
        channel_id: str,
        thread_id: str,
    ) -> int:
        db = await self._conn()
        cursor = await db.execute(
            "SELECT COUNT(*) FROM turns "
            "WHERE platform=? AND channel_id=? AND thread_id=?",
            (platform, channel_id, thread_id),
        )
        row = await cursor.fetchone()
        return row[0]  # type: ignore[index]

    # -- export / import ---------------------------------------------------

    async def export_data(self) -> dict[str, Any]:
        db = await self._conn()

        cursor = await db.execute(
            "SELECT platform, channel_id, thread_id, role, content, "
            "author, agent, created_at FROM turns ORDER BY id"
        )
        turns = [dict(r) for r in await cursor.fetchall()]

        cursor = await db.execute(
            "SELECT platform, channel_id, thread_id, summary, "
            "turns_start, turns_end, created_at FROM summaries ORDER BY id"
        )
        summaries = [dict(r) for r in await cursor.fetchall()]

        return {"version": 1, "turns": turns, "summaries": summaries}

    async def import_data(self, data: dict[str, Any]) -> int:
        db = await self._conn()
        count = 0

        for turn in data.get("turns", []):
            await db.execute(
                "INSERT INTO turns (platform, channel_id, thread_id, role, content, author, agent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    turn["platform"],
                    turn["channel_id"],
                    turn["thread_id"],
                    turn["role"],
                    turn["content"],
                    turn.get("author"),
                    turn.get("agent"),
                ),
            )
            count += 1

        for summary in data.get("summaries", []):
            await db.execute(
                "INSERT INTO summaries (platform, channel_id, thread_id, summary, turns_start, turns_end) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    summary["platform"],
                    summary["channel_id"],
                    summary["thread_id"],
                    summary["summary"],
                    summary["turns_start"],
                    summary["turns_end"],
                ),
            )

        await db.commit()
        logger.info("Imported %d turns and %d summaries", count, len(data.get("summaries", [])))
        return count
