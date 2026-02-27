from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import aiosqlite

from oh_my_agent.runtime.types import RuntimeTask

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

    # -- session persistence (optional, concrete no-op defaults) -----------

    async def save_session(
        self, platform: str, channel_id: str, thread_id: str, agent: str, session_id: str
    ) -> None:
        """Persist an agent CLI session ID so it can be resumed after restart."""

    async def load_session(
        self, platform: str, channel_id: str, thread_id: str, agent: str
    ) -> str | None:
        """Load a persisted agent session ID. Returns None if not found."""
        return None

    async def delete_session(
        self, platform: str, channel_id: str, thread_id: str, agent: str
    ) -> None:
        """Remove a persisted session (e.g. after a failed resume)."""

    # -- runtime task persistence (optional, concrete no-op defaults) -----

    async def create_runtime_task(self, **kwargs) -> RuntimeTask:
        raise NotImplementedError

    async def get_runtime_task(self, task_id: str) -> RuntimeTask | None:
        return None

    async def list_runtime_tasks(
        self,
        *,
        platform: str,
        channel_id: str,
        status: str | None = None,
        limit: int = 20,
    ) -> list[RuntimeTask]:
        return []

    async def update_runtime_task(self, task_id: str, **updates) -> RuntimeTask | None:
        return None

    async def claim_pending_runtime_task(self) -> RuntimeTask | None:
        return None

    async def requeue_inflight_runtime_tasks(self) -> int:
        return 0

    async def add_runtime_event(self, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
        return None

    async def list_runtime_events(self, task_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return []

    async def add_runtime_checkpoint(
        self,
        *,
        task_id: str,
        step_no: int,
        status: str,
        prompt_digest: str,
        agent_result: str,
        test_result: str,
        files_changed: list[str],
    ) -> None:
        return None

    async def get_last_runtime_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        return None

    async def create_runtime_decision_nonce(self, task_id: str, *, ttl_minutes: int) -> str:
        raise NotImplementedError

    async def get_active_runtime_decision_nonce(self, task_id: str) -> str | None:
        return None

    async def consume_runtime_decision_nonce(
        self,
        *,
        task_id: str,
        nonce: str,
        action: str,
        actor_id: str,
        source: str,
        result: str,
    ) -> bool:
        return False

    async def list_runtime_cleanup_candidates(
        self,
        *,
        statuses: list[str],
        older_than_hours: int,
        limit: int = 100,
    ) -> list[RuntimeTask]:
        return []

    # -- short-conversation ephemeral workspaces -------------------------

    async def upsert_ephemeral_workspace(self, workspace_key: str, workspace_path: str) -> None:
        return None

    async def list_expired_ephemeral_workspaces(
        self,
        *,
        ttl_hours: int,
        limit: int = 200,
    ) -> list[dict[str, str]]:
        return []

    async def mark_ephemeral_workspace_cleaned(self, workspace_key: str) -> None:
        return None


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

CREATE TABLE IF NOT EXISTS agent_sessions (
    platform    TEXT NOT NULL,
    channel_id  TEXT NOT NULL,
    thread_id   TEXT NOT NULL,
    agent       TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (platform, channel_id, thread_id, agent)
);

CREATE TABLE IF NOT EXISTS runtime_tasks (
    id                  TEXT PRIMARY KEY,
    platform            TEXT NOT NULL,
    channel_id          TEXT NOT NULL,
    thread_id           TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    goal                TEXT NOT NULL,
    preferred_agent     TEXT,
    status              TEXT NOT NULL,
    step_no             INTEGER NOT NULL DEFAULT 0,
    max_steps           INTEGER NOT NULL,
    max_minutes         INTEGER NOT NULL,
    test_command        TEXT NOT NULL,
    workspace_path      TEXT,
    decision_message_id TEXT,
    blocked_reason      TEXT,
    error               TEXT,
    summary             TEXT,
    resume_instruction  TEXT,
    merge_commit_hash   TEXT,
    merge_error         TEXT,
    workspace_cleaned_at TIMESTAMP,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at          TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at            TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_runtime_tasks_status
    ON runtime_tasks(status, updated_at);
CREATE INDEX IF NOT EXISTS idx_runtime_tasks_channel
    ON runtime_tasks(platform, channel_id, created_at);

CREATE TABLE IF NOT EXISTS runtime_task_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_runtime_task_events_task
    ON runtime_task_events(task_id, seq);

CREATE TABLE IF NOT EXISTS runtime_task_checkpoints (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id             TEXT NOT NULL,
    step_no             INTEGER NOT NULL,
    status              TEXT NOT NULL,
    prompt_digest       TEXT NOT NULL,
    agent_result        TEXT NOT NULL,
    test_result         TEXT NOT NULL,
    files_changed_json  TEXT NOT NULL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_runtime_task_checkpoints_task
    ON runtime_task_checkpoints(task_id, step_no);

CREATE TABLE IF NOT EXISTS runtime_task_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    nonce       TEXT NOT NULL,
    action      TEXT,
    actor_id    TEXT,
    source      TEXT,
    result      TEXT,
    consumed    INTEGER NOT NULL DEFAULT 0,
    expires_at  TIMESTAMP NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    consumed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_runtime_task_decisions_task
    ON runtime_task_decisions(task_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_task_decisions_nonce
    ON runtime_task_decisions(task_id, nonce);

CREATE TABLE IF NOT EXISTS ephemeral_workspaces (
    workspace_key    TEXT PRIMARY KEY,
    workspace_path   TEXT NOT NULL,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    cleaned_at       TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ephemeral_workspaces_active
    ON ephemeral_workspaces(cleaned_at, last_used_at);
"""


class SQLiteMemoryStore(MemoryStore):
    """SQLite-backed memory store with FTS5 full-text search."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._runtime_write_lock = asyncio.Lock()

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
        await self._migrate_runtime_schema()
        await db.commit()
        logger.info("Memory store initialised at %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def _migrate_runtime_schema(self) -> None:
        await self._ensure_column("runtime_tasks", "merge_commit_hash", "TEXT")
        await self._ensure_column("runtime_tasks", "merge_error", "TEXT")
        await self._ensure_column("runtime_tasks", "workspace_cleaned_at", "TIMESTAMP")

    async def _ensure_column(self, table: str, column: str, ddl_type: str) -> None:
        db = await self._conn()
        cursor = await db.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
        existing = {str(row["name"]) for row in rows}
        if column in existing:
            return
        await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")

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
        await db.execute(
            "DELETE FROM agent_sessions WHERE platform=? AND channel_id=? AND thread_id=?",
            (platform, channel_id, thread_id),
        )
        await db.commit()

    async def save_session(
        self, platform: str, channel_id: str, thread_id: str, agent: str, session_id: str
    ) -> None:
        db = await self._conn()
        await db.execute(
            "INSERT OR REPLACE INTO agent_sessions "
            "(platform, channel_id, thread_id, agent, session_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (platform, channel_id, thread_id, agent, session_id),
        )
        await db.commit()

    async def load_session(
        self, platform: str, channel_id: str, thread_id: str, agent: str
    ) -> str | None:
        db = await self._conn()
        cursor = await db.execute(
            "SELECT session_id FROM agent_sessions "
            "WHERE platform=? AND channel_id=? AND thread_id=? AND agent=?",
            (platform, channel_id, thread_id, agent),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def delete_session(
        self, platform: str, channel_id: str, thread_id: str, agent: str
    ) -> None:
        db = await self._conn()
        await db.execute(
            "DELETE FROM agent_sessions "
            "WHERE platform=? AND channel_id=? AND thread_id=? AND agent=?",
            (platform, channel_id, thread_id, agent),
        )
        await db.commit()

    # -- runtime tasks -----------------------------------------------------

    async def create_runtime_task(self, **kwargs) -> RuntimeTask:
        async with self._runtime_write_lock:
            db = await self._conn()
            await db.execute(
                "INSERT INTO runtime_tasks "
                "(id, platform, channel_id, thread_id, created_by, goal, preferred_agent, "
                " status, max_steps, max_minutes, test_command) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    kwargs["task_id"],
                    kwargs["platform"],
                    kwargs["channel_id"],
                    kwargs["thread_id"],
                    kwargs["created_by"],
                    kwargs["goal"],
                    kwargs.get("preferred_agent"),
                    kwargs["status"],
                    int(kwargs["max_steps"]),
                    int(kwargs["max_minutes"]),
                    kwargs["test_command"],
                ),
            )
            await db.commit()
        task = await self.get_runtime_task(kwargs["task_id"])
        if task is None:
            raise RuntimeError("Failed to create runtime task")
        return task

    async def get_runtime_task(self, task_id: str) -> RuntimeTask | None:
        db = await self._conn()
        cursor = await db.execute(
            "SELECT * FROM runtime_tasks WHERE id=?",
            (task_id,),
        )
        row = await cursor.fetchone()
        return RuntimeTask.from_row(dict(row)) if row else None

    async def list_runtime_tasks(
        self,
        *,
        platform: str,
        channel_id: str,
        status: str | None = None,
        limit: int = 20,
    ) -> list[RuntimeTask]:
        db = await self._conn()
        if status:
            cursor = await db.execute(
                "SELECT * FROM runtime_tasks "
                "WHERE platform=? AND channel_id=? AND status=? "
                "ORDER BY created_at DESC LIMIT ?",
                (platform, channel_id, status, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM runtime_tasks "
                "WHERE platform=? AND channel_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (platform, channel_id, limit),
            )
        rows = await cursor.fetchall()
        return [RuntimeTask.from_row(dict(r)) for r in rows]

    async def upsert_ephemeral_workspace(self, workspace_key: str, workspace_path: str) -> None:
        db = await self._conn()
        await db.execute(
            "INSERT INTO ephemeral_workspaces (workspace_key, workspace_path, last_used_at, cleaned_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP, NULL) "
            "ON CONFLICT(workspace_key) DO UPDATE SET "
            "workspace_path=excluded.workspace_path, "
            "last_used_at=CURRENT_TIMESTAMP, "
            "cleaned_at=NULL",
            (workspace_key, workspace_path),
        )
        await db.commit()

    async def list_expired_ephemeral_workspaces(
        self,
        *,
        ttl_hours: int,
        limit: int = 200,
    ) -> list[dict[str, str]]:
        db = await self._conn()
        cursor = await db.execute(
            "SELECT workspace_key, workspace_path FROM ephemeral_workspaces "
            "WHERE cleaned_at IS NULL "
            "AND last_used_at <= datetime('now', ?) "
            "ORDER BY last_used_at ASC LIMIT ?",
            (f"-{int(ttl_hours)} hours", int(limit)),
        )
        rows = await cursor.fetchall()
        return [
            {
                "workspace_key": str(row["workspace_key"]),
                "workspace_path": str(row["workspace_path"]),
            }
            for row in rows
        ]

    async def mark_ephemeral_workspace_cleaned(self, workspace_key: str) -> None:
        db = await self._conn()
        await db.execute(
            "UPDATE ephemeral_workspaces "
            "SET cleaned_at=CURRENT_TIMESTAMP "
            "WHERE workspace_key=?",
            (workspace_key,),
        )
        await db.commit()

    async def update_runtime_task(self, task_id: str, **updates) -> RuntimeTask | None:
        if not updates:
            return await self.get_runtime_task(task_id)
        async with self._runtime_write_lock:
            db = await self._conn()

            sets: list[str] = []
            values: list[Any] = []
            ended_at_now = bool(updates.pop("ended_at_now", False))
            if ended_at_now:
                updates["ended_at"] = "__NOW__"

            for key, value in updates.items():
                if value == "__NOW__":
                    sets.append(f"{key}=CURRENT_TIMESTAMP")
                else:
                    sets.append(f"{key}=?")
                    values.append(value)
            sets.append("updated_at=CURRENT_TIMESTAMP")

            values.append(task_id)
            await db.execute(
                f"UPDATE runtime_tasks SET {', '.join(sets)} WHERE id=?",
                tuple(values),
            )
            await db.commit()
        return await self.get_runtime_task(task_id)

    async def claim_pending_runtime_task(self) -> RuntimeTask | None:
        async with self._runtime_write_lock:
            db = await self._conn()
            await db.execute("BEGIN IMMEDIATE")
            try:
                cursor = await db.execute(
                    "SELECT id FROM runtime_tasks "
                    "WHERE status=? "
                    "ORDER BY created_at ASC LIMIT 1",
                    ("PENDING",),
                )
                row = await cursor.fetchone()
                if row is None:
                    await db.commit()
                    return None

                task_id = row["id"]
                await db.execute(
                    "UPDATE runtime_tasks "
                    "SET status=?, started_at=COALESCE(started_at, CURRENT_TIMESTAMP), updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status=?",
                    ("RUNNING", task_id, "PENDING"),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return await self.get_runtime_task(task_id)

    async def requeue_inflight_runtime_tasks(self) -> int:
        async with self._runtime_write_lock:
            db = await self._conn()
            cursor = await db.execute(
                "UPDATE runtime_tasks SET status='PENDING', updated_at=CURRENT_TIMESTAMP "
                "WHERE status IN ('RUNNING', 'VALIDATING')"
            )
            await db.commit()
        return int(cursor.rowcount or 0)

    async def add_runtime_event(self, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
        async with self._runtime_write_lock:
            db = await self._conn()
            cursor = await db.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM runtime_task_events WHERE task_id=?",
                (task_id,),
            )
            row = await cursor.fetchone()
            next_seq = int(row[0] if row else 1)
            await db.execute(
                "INSERT INTO runtime_task_events (task_id, seq, event_type, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (task_id, next_seq, event_type, json.dumps(payload, ensure_ascii=False)),
            )
            await db.commit()

    async def list_runtime_events(self, task_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        db = await self._conn()
        cursor = await db.execute(
            "SELECT seq, event_type, payload_json, created_at "
            "FROM runtime_task_events WHERE task_id=? "
            "ORDER BY seq DESC LIMIT ?",
            (task_id, int(limit)),
        )
        rows = await cursor.fetchall()
        items: list[dict[str, Any]] = []
        for row in reversed(rows):
            payload: dict[str, Any]
            try:
                payload = json.loads(str(row["payload_json"]))
            except Exception:
                payload = {"raw": row["payload_json"]}
            items.append(
                {
                    "seq": int(row["seq"]),
                    "event_type": str(row["event_type"]),
                    "payload": payload,
                    "created_at": str(row["created_at"]),
                }
            )
        return items

    async def add_runtime_checkpoint(
        self,
        *,
        task_id: str,
        step_no: int,
        status: str,
        prompt_digest: str,
        agent_result: str,
        test_result: str,
        files_changed: list[str],
    ) -> None:
        async with self._runtime_write_lock:
            db = await self._conn()
            await db.execute(
                "INSERT INTO runtime_task_checkpoints "
                "(task_id, step_no, status, prompt_digest, agent_result, test_result, files_changed_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    step_no,
                    status,
                    prompt_digest,
                    agent_result,
                    test_result,
                    json.dumps(files_changed, ensure_ascii=False),
                ),
            )
            await db.commit()

    async def get_last_runtime_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        db = await self._conn()
        cursor = await db.execute(
            "SELECT * FROM runtime_task_checkpoints "
            "WHERE task_id=? ORDER BY step_no DESC, id DESC LIMIT 1",
            (task_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def create_runtime_decision_nonce(self, task_id: str, *, ttl_minutes: int) -> str:
        async with self._runtime_write_lock:
            db = await self._conn()
            nonce = uuid.uuid4().hex[:8]
            await db.execute(
                "INSERT INTO runtime_task_decisions (task_id, nonce, expires_at) "
                "VALUES (?, ?, datetime('now', ?))",
                (task_id, nonce, f"+{int(ttl_minutes)} minutes"),
            )
            await db.commit()
        return nonce

    async def get_active_runtime_decision_nonce(self, task_id: str) -> str | None:
        db = await self._conn()
        cursor = await db.execute(
            "SELECT nonce FROM runtime_task_decisions "
            "WHERE task_id=? AND consumed=0 AND expires_at > CURRENT_TIMESTAMP "
            "ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        row = await cursor.fetchone()
        return str(row["nonce"]) if row else None

    async def consume_runtime_decision_nonce(
        self,
        *,
        task_id: str,
        nonce: str,
        action: str,
        actor_id: str,
        source: str,
        result: str,
    ) -> bool:
        async with self._runtime_write_lock:
            db = await self._conn()
            cursor = await db.execute(
                "UPDATE runtime_task_decisions "
                "SET consumed=1, action=?, actor_id=?, source=?, result=?, consumed_at=CURRENT_TIMESTAMP "
                "WHERE task_id=? AND nonce=? AND consumed=0 AND expires_at > CURRENT_TIMESTAMP",
                (action, actor_id, source, result, task_id, nonce),
            )
            await db.commit()
        return int(cursor.rowcount or 0) > 0

    async def list_runtime_cleanup_candidates(
        self,
        *,
        statuses: list[str],
        older_than_hours: int,
        limit: int = 100,
    ) -> list[RuntimeTask]:
        if not statuses:
            return []
        db = await self._conn()
        placeholders = ", ".join("?" for _ in statuses)
        cursor = await db.execute(
            f"SELECT * FROM runtime_tasks "
            f"WHERE status IN ({placeholders}) "
            "AND workspace_path IS NOT NULL "
            "AND workspace_cleaned_at IS NULL "
            "AND ended_at IS NOT NULL "
            "AND ended_at <= datetime('now', ?) "
            "ORDER BY ended_at ASC LIMIT ?",
            (*statuses, f"-{int(older_than_hours)} hours", int(limit)),
        )
        rows = await cursor.fetchall()
        return [RuntimeTask.from_row(dict(r)) for r in rows]

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
