from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
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

    async def upsert_skill_provenance(self, skill_name: str, **kwargs) -> None:
        return None

    async def get_skill_provenance(self, skill_name: str) -> dict[str, Any] | None:
        return None

    async def record_skill_invocation(self, **kwargs) -> int | None:
        return None

    async def set_skill_invocation_response_message(
        self,
        invocation_id: int,
        message_id: str,
    ) -> None:
        return None

    async def get_skill_invocation_by_message(
        self,
        message_id: str,
    ) -> dict[str, Any] | None:
        return None

    async def upsert_skill_feedback(self, **kwargs) -> None:
        return None

    async def delete_skill_feedback(self, *, invocation_id: int, actor_id: str) -> None:
        return None

    async def list_recent_skill_invocations(self, skill_name: str, *, limit: int) -> list[dict[str, Any]]:
        return []

    async def get_skill_stats(self, skill_name: str | None = None, *, recent_days: int = 7) -> list[dict[str, Any]]:
        return []

    async def set_skill_auto_disabled(
        self,
        skill_name: str,
        *,
        disabled: bool,
        reason: str | None = None,
    ) -> None:
        return None

    async def list_auto_disabled_skills(self) -> set[str]:
        return set()

    async def add_skill_evaluation(self, **kwargs) -> None:
        return None

    async def get_latest_skill_evaluations(
        self,
        skill_name: str,
    ) -> list[dict[str, Any]]:
        return []


@dataclass
class SkillInvocationDelivery:
    invocation_id: int | None = None
    response_message_id: str | None = None


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
    original_request    TEXT,
    preferred_agent     TEXT,
    status              TEXT NOT NULL,
    step_no             INTEGER NOT NULL DEFAULT 0,
    max_steps           INTEGER NOT NULL,
    max_minutes         INTEGER NOT NULL,
    test_command        TEXT NOT NULL,
    workspace_path      TEXT,
    decision_message_id TEXT,
    status_message_id   TEXT,
    blocked_reason      TEXT,
    error               TEXT,
    summary             TEXT,
    resume_instruction  TEXT,
    merge_commit_hash   TEXT,
    merge_error         TEXT,
    completion_mode     TEXT NOT NULL DEFAULT 'merge',
    output_summary      TEXT,
    artifact_manifest   TEXT,
    workspace_cleaned_at TIMESTAMP,
    task_type           TEXT NOT NULL DEFAULT 'repo_change',
    skill_name          TEXT,
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

CREATE TABLE IF NOT EXISTS skill_provenance (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name          TEXT NOT NULL UNIQUE,
    source_task_id      TEXT,
    created_by          TEXT,
    agent_name          TEXT,
    platform            TEXT,
    channel_id          TEXT,
    thread_id           TEXT,
    validation_mode     TEXT,
    validated           INTEGER NOT NULL DEFAULT 0,
    validation_warnings TEXT,
    merged_commit_hash  TEXT,
    auto_disabled       INTEGER NOT NULL DEFAULT 0,
    auto_disabled_reason TEXT,
    auto_disabled_at    TIMESTAMP,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_skill_provenance_task
    ON skill_provenance(source_task_id);

CREATE TABLE IF NOT EXISTS skill_invocations (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name                TEXT NOT NULL,
    agent_name                TEXT NOT NULL,
    platform                  TEXT,
    channel_id                TEXT,
    thread_id                 TEXT,
    user_id                   TEXT,
    route_source              TEXT NOT NULL,
    request_id                TEXT,
    response_message_id       TEXT,
    outcome                   TEXT NOT NULL,
    error_kind                TEXT,
    error_text                TEXT,
    latency_ms                INTEGER NOT NULL,
    input_tokens              INTEGER,
    output_tokens             INTEGER,
    cache_read_input_tokens   INTEGER,
    cache_creation_input_tokens INTEGER,
    created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_skill_invocations_skill_created
    ON skill_invocations(skill_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_skill_invocations_message
    ON skill_invocations(response_message_id);
CREATE INDEX IF NOT EXISTS idx_skill_invocations_thread_created
    ON skill_invocations(thread_id, created_at DESC);

CREATE TABLE IF NOT EXISTS skill_feedback (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    invocation_id INTEGER NOT NULL,
    actor_id      TEXT NOT NULL,
    platform      TEXT,
    channel_id    TEXT,
    thread_id     TEXT,
    score         INTEGER NOT NULL,
    source        TEXT NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(invocation_id, actor_id)
);

CREATE INDEX IF NOT EXISTS idx_skill_feedback_invocation
    ON skill_feedback(invocation_id);

CREATE TABLE IF NOT EXISTS skill_evaluations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name      TEXT NOT NULL,
    source_task_id  TEXT,
    evaluation_type TEXT NOT NULL,
    status          TEXT NOT NULL,
    summary         TEXT NOT NULL,
    details_json    TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_skill_evaluations_skill_created
    ON skill_evaluations(skill_name, created_at DESC);
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
        await self._ensure_column("runtime_tasks", "original_request", "TEXT")
        await self._ensure_column("runtime_tasks", "status_message_id", "TEXT")
        await self._ensure_column("runtime_tasks", "merge_commit_hash", "TEXT")
        await self._ensure_column("runtime_tasks", "merge_error", "TEXT")
        await self._ensure_column("runtime_tasks", "completion_mode", "TEXT NOT NULL DEFAULT 'merge'")
        await self._ensure_column("runtime_tasks", "output_summary", "TEXT")
        await self._ensure_column("runtime_tasks", "artifact_manifest", "TEXT")
        await self._ensure_column("runtime_tasks", "workspace_cleaned_at", "TIMESTAMP")
        await self._ensure_column("runtime_tasks", "task_type", "TEXT NOT NULL DEFAULT 'repo_change'")
        await self._ensure_column("runtime_tasks", "skill_name", "TEXT")
        await self._ensure_column("skill_provenance", "auto_disabled", "INTEGER NOT NULL DEFAULT 0")
        await self._ensure_column("skill_provenance", "auto_disabled_reason", "TEXT")
        await self._ensure_column("skill_provenance", "auto_disabled_at", "TIMESTAMP")
        db = await self._conn()
        await db.execute("UPDATE runtime_tasks SET task_type='repo_change' WHERE task_type='code'")
        await db.execute("UPDATE runtime_tasks SET task_type='skill_change' WHERE task_type='skill'")
        await db.commit()

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
                "(id, platform, channel_id, thread_id, created_by, goal, original_request, preferred_agent, "
                " status, max_steps, max_minutes, test_command, completion_mode, output_summary, artifact_manifest, task_type, skill_name) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    kwargs["task_id"],
                    kwargs["platform"],
                    kwargs["channel_id"],
                    kwargs["thread_id"],
                    kwargs["created_by"],
                    kwargs["goal"],
                    kwargs.get("original_request"),
                    kwargs.get("preferred_agent"),
                    kwargs["status"],
                    int(kwargs["max_steps"]),
                    int(kwargs["max_minutes"]),
                    kwargs["test_command"],
                    kwargs.get("completion_mode", "merge"),
                    kwargs.get("output_summary"),
                    json.dumps(kwargs.get("artifact_manifest"), ensure_ascii=False)
                    if kwargs.get("artifact_manifest") is not None
                    else None,
                    kwargs.get("task_type", "repo_change"),
                    kwargs.get("skill_name"),
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
        return RuntimeTask.from_row(self._normalize_runtime_task_row(dict(row))) if row else None

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
        return [RuntimeTask.from_row(self._normalize_runtime_task_row(dict(r))) for r in rows]

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
                    if key == "artifact_manifest" and value is not None:
                        value = json.dumps(value, ensure_ascii=False)
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

    async def upsert_skill_provenance(self, skill_name: str, **kwargs) -> None:
        async with self._runtime_write_lock:
            db = await self._conn()
            warnings = kwargs.get("validation_warnings")
            warnings_json = (
                json.dumps(warnings, ensure_ascii=False)
                if warnings is not None
                else None
            )
            await db.execute(
                "INSERT INTO skill_provenance ("
                " skill_name, source_task_id, created_by, agent_name, platform, channel_id, thread_id,"
                " validation_mode, validated, validation_warnings, merged_commit_hash, auto_disabled,"
                " auto_disabled_reason, auto_disabled_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(skill_name) DO UPDATE SET "
                "source_task_id=CASE WHEN excluded.source_task_id IS NOT NULL THEN excluded.source_task_id ELSE source_task_id END, "
                "created_by=CASE WHEN excluded.source_task_id IS NOT NULL THEN excluded.created_by ELSE created_by END, "
                "agent_name=CASE WHEN excluded.source_task_id IS NOT NULL THEN excluded.agent_name ELSE agent_name END, "
                "validated=CASE WHEN excluded.source_task_id IS NOT NULL THEN excluded.validated ELSE validated END, "
                "merged_commit_hash=CASE WHEN excluded.merged_commit_hash IS NOT NULL THEN excluded.merged_commit_hash ELSE merged_commit_hash END, "
                "validation_mode=CASE WHEN excluded.source_task_id IS NOT NULL THEN excluded.validation_mode ELSE validation_mode END, "
                "validation_warnings=COALESCE(excluded.validation_warnings, validation_warnings), "
                "platform=COALESCE(excluded.platform, platform), "
                "channel_id=COALESCE(excluded.channel_id, channel_id), "
                "thread_id=COALESCE(excluded.thread_id, thread_id), "
                "updated_at=CURRENT_TIMESTAMP",
                (
                    skill_name,
                    kwargs.get("source_task_id"),
                    kwargs.get("created_by"),
                    kwargs.get("agent_name"),
                    kwargs.get("platform"),
                    kwargs.get("channel_id"),
                    kwargs.get("thread_id"),
                    kwargs.get("validation_mode"),
                    int(bool(kwargs.get("validated", False))),
                    warnings_json,
                    kwargs.get("merged_commit_hash"),
                    int(bool(kwargs.get("auto_disabled", False))),
                    kwargs.get("auto_disabled_reason"),
                    kwargs.get("auto_disabled_at"),
                ),
            )
            await db.commit()

    async def get_skill_provenance(self, skill_name: str) -> dict[str, Any] | None:
        db = await self._conn()
        cursor = await db.execute(
            "SELECT * FROM skill_provenance WHERE skill_name=?",
            (skill_name,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        data = dict(row)
        raw_warnings = data.get("validation_warnings")
        if raw_warnings:
            try:
                data["validation_warnings"] = json.loads(raw_warnings)
            except Exception:
                data["validation_warnings"] = [str(raw_warnings)]
        else:
            data["validation_warnings"] = []
        return data

    async def record_skill_invocation(self, **kwargs) -> int | None:
        async with self._runtime_write_lock:
            db = await self._conn()
            cursor = await db.execute(
                "INSERT INTO skill_invocations ("
                " skill_name, agent_name, platform, channel_id, thread_id, user_id, route_source, request_id,"
                " response_message_id, outcome, error_kind, error_text, latency_ms, input_tokens, output_tokens,"
                " cache_read_input_tokens, cache_creation_input_tokens"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    kwargs["skill_name"],
                    kwargs["agent_name"],
                    kwargs.get("platform"),
                    kwargs.get("channel_id"),
                    kwargs.get("thread_id"),
                    kwargs.get("user_id"),
                    kwargs["route_source"],
                    kwargs.get("request_id"),
                    kwargs.get("response_message_id"),
                    kwargs["outcome"],
                    kwargs.get("error_kind"),
                    kwargs.get("error_text"),
                    int(kwargs.get("latency_ms", 0)),
                    kwargs.get("input_tokens"),
                    kwargs.get("output_tokens"),
                    kwargs.get("cache_read_input_tokens"),
                    kwargs.get("cache_creation_input_tokens"),
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def set_skill_invocation_response_message(
        self,
        invocation_id: int,
        message_id: str,
    ) -> None:
        db = await self._conn()
        await db.execute(
            "UPDATE skill_invocations SET response_message_id=? WHERE id=?",
            (message_id, int(invocation_id)),
        )
        await db.commit()

    async def get_skill_invocation_by_message(
        self,
        message_id: str,
    ) -> dict[str, Any] | None:
        db = await self._conn()
        cursor = await db.execute(
            "SELECT * FROM skill_invocations WHERE response_message_id=? ORDER BY id DESC LIMIT 1",
            (message_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def upsert_skill_feedback(self, **kwargs) -> None:
        db = await self._conn()
        await db.execute(
            "INSERT INTO skill_feedback ("
            " invocation_id, actor_id, platform, channel_id, thread_id, score, source, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(invocation_id, actor_id) DO UPDATE SET "
            "score=excluded.score, platform=excluded.platform, channel_id=excluded.channel_id, "
            "thread_id=excluded.thread_id, source=excluded.source, updated_at=CURRENT_TIMESTAMP",
            (
                int(kwargs["invocation_id"]),
                kwargs["actor_id"],
                kwargs.get("platform"),
                kwargs.get("channel_id"),
                kwargs.get("thread_id"),
                int(kwargs["score"]),
                kwargs.get("source", "reaction"),
            ),
        )
        await db.commit()

    async def delete_skill_feedback(self, *, invocation_id: int, actor_id: str) -> None:
        db = await self._conn()
        await db.execute(
            "DELETE FROM skill_feedback WHERE invocation_id=? AND actor_id=?",
            (int(invocation_id), actor_id),
        )
        await db.commit()

    async def list_recent_skill_invocations(self, skill_name: str, *, limit: int) -> list[dict[str, Any]]:
        db = await self._conn()
        cursor = await db.execute(
            "SELECT * FROM skill_invocations WHERE skill_name=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (skill_name, int(limit)),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_skill_stats(self, skill_name: str | None = None, *, recent_days: int = 7) -> list[dict[str, Any]]:
        db = await self._conn()
        params: list[Any] = [f"-{int(recent_days)} days"]
        skill_filter = ""
        if skill_name:
            skill_filter = "WHERE s.skill_name = ?"
            params.append(skill_name)
        query = f"""
WITH recent AS (
    SELECT
        skill_name,
        COUNT(*) AS recent_invocations,
        SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) AS recent_successes,
        SUM(CASE WHEN outcome='error' THEN 1 ELSE 0 END) AS recent_errors,
        SUM(CASE WHEN outcome='timeout' THEN 1 ELSE 0 END) AS recent_timeouts,
        SUM(CASE WHEN outcome='cancelled' THEN 1 ELSE 0 END) AS recent_cancelled,
        AVG(latency_ms) AS recent_avg_latency_ms,
        MAX(created_at) AS last_invoked_at
    FROM skill_invocations
    WHERE created_at >= datetime('now', ?)
    GROUP BY skill_name
),
overall AS (
    SELECT
        skill_name,
        COUNT(*) AS total_invocations
    FROM skill_invocations
    GROUP BY skill_name
),
feedback AS (
    SELECT
        si.skill_name AS skill_name,
        SUM(CASE WHEN sf.score > 0 THEN 1 ELSE 0 END) AS thumbs_up,
        SUM(CASE WHEN sf.score < 0 THEN 1 ELSE 0 END) AS thumbs_down,
        COALESCE(SUM(sf.score), 0) AS net_feedback
    FROM skill_feedback sf
    JOIN skill_invocations si ON si.id = sf.invocation_id
    GROUP BY si.skill_name
),
skills AS (
    SELECT skill_name FROM skill_provenance
    UNION
    SELECT skill_name FROM skill_invocations
)
SELECT
    s.skill_name,
    sp.agent_name,
    sp.validated,
    sp.validation_mode,
    sp.validation_warnings,
    sp.merged_commit_hash,
    sp.auto_disabled,
    sp.auto_disabled_reason,
    sp.auto_disabled_at,
    COALESCE(o.total_invocations, 0) AS total_invocations,
    COALESCE(r.recent_invocations, 0) AS recent_invocations,
    COALESCE(r.recent_successes, 0) AS recent_successes,
    COALESCE(r.recent_errors, 0) AS recent_errors,
    COALESCE(r.recent_timeouts, 0) AS recent_timeouts,
    COALESCE(r.recent_cancelled, 0) AS recent_cancelled,
    COALESCE(r.recent_avg_latency_ms, 0) AS recent_avg_latency_ms,
    r.last_invoked_at AS last_invoked_at,
    COALESCE(f.thumbs_up, 0) AS thumbs_up,
    COALESCE(f.thumbs_down, 0) AS thumbs_down,
    COALESCE(f.net_feedback, 0) AS net_feedback
FROM skills s
LEFT JOIN skill_provenance sp ON sp.skill_name = s.skill_name
LEFT JOIN recent r ON r.skill_name = s.skill_name
LEFT JOIN overall o ON o.skill_name = s.skill_name
LEFT JOIN feedback f ON f.skill_name = s.skill_name
{skill_filter}
ORDER BY recent_invocations DESC, s.skill_name ASC
"""
        cursor = await db.execute(query, tuple(params))
        rows = await cursor.fetchall()
        items = []
        for row in rows:
            item = dict(row)
            raw_warnings = item.get("validation_warnings")
            if raw_warnings:
                try:
                    item["validation_warnings"] = json.loads(raw_warnings)
                except Exception:
                    item["validation_warnings"] = [str(raw_warnings)]
            else:
                item["validation_warnings"] = []
            items.append(item)
        return items

    async def set_skill_auto_disabled(
        self,
        skill_name: str,
        *,
        disabled: bool,
        reason: str | None = None,
    ) -> None:
        db = await self._conn()
        if disabled:
            await db.execute(
                "INSERT INTO skill_provenance (skill_name, auto_disabled, auto_disabled_reason, auto_disabled_at, updated_at) "
                "VALUES (?, 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                "ON CONFLICT(skill_name) DO UPDATE SET "
                "auto_disabled=1, auto_disabled_reason=excluded.auto_disabled_reason, "
                "auto_disabled_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP",
                (skill_name, reason),
            )
        else:
            await db.execute(
                "INSERT INTO skill_provenance (skill_name, auto_disabled, updated_at) "
                "VALUES (?, 0, CURRENT_TIMESTAMP) "
                "ON CONFLICT(skill_name) DO UPDATE SET "
                "auto_disabled=0, auto_disabled_reason=NULL, auto_disabled_at=NULL, updated_at=CURRENT_TIMESTAMP",
                (skill_name,),
            )
        await db.commit()

    async def list_auto_disabled_skills(self) -> set[str]:
        db = await self._conn()
        cursor = await db.execute(
            "SELECT skill_name FROM skill_provenance WHERE auto_disabled=1"
        )
        rows = await cursor.fetchall()
        return {str(row["skill_name"]) for row in rows}

    async def add_skill_evaluation(self, **kwargs) -> None:
        db = await self._conn()
        details_json = (
            json.dumps(kwargs.get("details_json"), ensure_ascii=False)
            if kwargs.get("details_json") is not None and not isinstance(kwargs.get("details_json"), str)
            else kwargs.get("details_json")
        )
        await db.execute(
            "INSERT INTO skill_evaluations (skill_name, source_task_id, evaluation_type, status, summary, details_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                kwargs["skill_name"],
                kwargs.get("source_task_id"),
                kwargs["evaluation_type"],
                kwargs["status"],
                kwargs["summary"],
                details_json,
            ),
        )
        await db.commit()

    async def get_latest_skill_evaluations(
        self,
        skill_name: str,
    ) -> list[dict[str, Any]]:
        db = await self._conn()
        cursor = await db.execute(
            """
            SELECT se.*
            FROM skill_evaluations se
            JOIN (
                SELECT evaluation_type, MAX(id) AS latest_id
                FROM skill_evaluations
                WHERE skill_name=?
                GROUP BY evaluation_type
            ) latest ON latest.latest_id = se.id
            ORDER BY se.evaluation_type ASC
            """,
            (skill_name,),
        )
        rows = await cursor.fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            raw_details = item.get("details_json")
            if raw_details:
                try:
                    item["details_json"] = json.loads(raw_details)
                except Exception:
                    pass
            items.append(item)
        return items

    @staticmethod
    def _normalize_runtime_task_row(data: dict[str, Any]) -> dict[str, Any]:
        raw_manifest = data.get("artifact_manifest")
        if raw_manifest:
            try:
                data["artifact_manifest"] = json.loads(raw_manifest)
            except Exception:
                pass
        return data

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
