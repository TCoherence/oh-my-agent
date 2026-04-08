from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

import pytest

from oh_my_agent.memory.store import (
    CONVERSATION_TABLES,
    CONVERSATION_FTS_SHADOW_TABLES,
    RUNTIME_STATE_TABLES,
    SKILLS_TELEMETRY_TABLES,
    SQLiteMemoryStore,
    SplitSQLiteMemoryStore,
    maybe_split_legacy_memory_db,
)


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    return {str(row[0]) for row in rows}


@pytest.mark.asyncio
async def test_split_migrates_legacy_monolith_into_three_dbs(tmp_path):
    memory_path = tmp_path / "memory.db"
    runtime_path = tmp_path / "runtime.db"
    skills_path = tmp_path / "skills.db"

    legacy = SQLiteMemoryStore(memory_path)
    await legacy.init()
    await legacy.append("discord", "100", "thread-1", {"role": "user", "content": "hello"})
    await legacy.save_session("discord", "100", "thread-1", "codex", "sess-1")
    await legacy.create_runtime_task(
        task_id="task-1",
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        created_by="user",
        goal="do something",
        status="PENDING",
        max_steps=1,
        max_minutes=10,
        test_command="true",
        completion_mode="reply",
        task_type="artifact",
        skill_name="deals-scanner",
    )
    await legacy.create_auth_flow(
        flow_id="flow-1",
        provider="bilibili",
        owner_user_id="owner-1",
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        linked_task_id="task-1",
        status="qr_ready",
        provider_flow_id="provider-flow-1",
        qr_payload="https://example.com/qr",
        qr_image_path=str(tmp_path / "flow-1.png"),
        expires_at="2026-03-09 00:03:00",
    )
    await legacy.create_hitl_prompt(
        prompt_id="prompt-1",
        target_kind="thread",
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        task_id=None,
        agent_name="codex",
        status="waiting",
        question="Pick one",
        details="details",
        choices_json=[{"id": "a", "label": "A"}],
        control_envelope_json="{}",
        resume_context_json={},
        session_id_snapshot="sess-1",
        prompt_message_id="msg-1",
        created_by="agent",
    )
    await legacy.record_skill_invocation(
        skill_name="deals-scanner",
        agent_name="codex",
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        user_id="owner-1",
        route_source="explicit",
        request_id="req-1",
        response_message_id="resp-1",
        outcome="success",
        error_kind=None,
        error_text=None,
        latency_ms=1200,
        input_tokens=10,
        output_tokens=20,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    await legacy.close()

    migrated = await maybe_split_legacy_memory_db(
        memory_path=memory_path,
        runtime_state_path=runtime_path,
        skills_telemetry_path=skills_path,
        logger=logging.getLogger("test"),
    )
    assert migrated is True
    assert memory_path.with_name("memory.db.monolith.bak").exists()

    conversation_tables = _tables(memory_path)
    assert CONVERSATION_TABLES <= conversation_tables
    assert conversation_tables.isdisjoint(RUNTIME_STATE_TABLES)
    assert conversation_tables.isdisjoint(SKILLS_TELEMETRY_TABLES)

    runtime_tables = _tables(runtime_path)
    assert RUNTIME_STATE_TABLES <= runtime_tables
    assert runtime_tables.isdisjoint(CONVERSATION_TABLES)
    assert runtime_tables.isdisjoint(CONVERSATION_FTS_SHADOW_TABLES)
    assert runtime_tables.isdisjoint(SKILLS_TELEMETRY_TABLES)

    skills_tables = _tables(skills_path)
    assert SKILLS_TELEMETRY_TABLES <= skills_tables
    assert skills_tables.isdisjoint(CONVERSATION_FTS_SHADOW_TABLES)
    assert skills_tables.isdisjoint(RUNTIME_STATE_TABLES)

    store = SplitSQLiteMemoryStore(
        conversation_path=memory_path,
        runtime_state_path=runtime_path,
        skills_telemetry_path=skills_path,
    )
    await store.init()
    try:
        history = await store.load_history("discord", "100", "thread-1")
        assert history[0]["content"] == "hello"
        search_hits = await store.search("hello", limit=5)
        assert len(search_hits) == 1
        assert await store.load_session("discord", "100", "thread-1", "codex") == "sess-1"
        tasks = await store.list_runtime_tasks(platform="discord", channel_id="100", limit=10)
        assert tasks[0].id == "task-1"
        flow = await store.get_auth_flow("flow-1")
        assert flow is not None
        assert flow.provider == "bilibili"
        prompt = await store.get_active_hitl_prompt_for_thread(
            platform="discord",
            channel_id="100",
            thread_id="thread-1",
        )
        assert prompt is not None
        assert prompt.question == "Pick one"
        invocations = await store.list_recent_skill_invocations("deals-scanner", limit=5)
        assert len(invocations) == 1
    finally:
        await store.close()

    migrated_again = await maybe_split_legacy_memory_db(
        memory_path=memory_path,
        runtime_state_path=runtime_path,
        skills_telemetry_path=skills_path,
        logger=logging.getLogger("test"),
    )
    assert migrated_again is False


@pytest.mark.asyncio
async def test_split_store_routes_conversation_and_runtime_writes_without_transaction_conflict(tmp_path):
    store = SplitSQLiteMemoryStore(
        conversation_path=tmp_path / "memory.db",
        runtime_state_path=tmp_path / "runtime.db",
        skills_telemetry_path=tmp_path / "skills.db",
    )
    await store.init()
    try:
        await store.create_runtime_task(
            task_id="task-1",
            platform="discord",
            channel_id="100",
            thread_id="thread-1",
            created_by="user",
            goal="do something",
            status="PENDING",
            max_steps=1,
            max_minutes=10,
            test_command="true",
            completion_mode="reply",
            task_type="artifact",
            skill_name="deals-scanner",
        )

        row_id, claimed = await asyncio.gather(
            store.append("discord", "100", "thread-1", {"role": "user", "content": "hello"}),
            store.claim_pending_runtime_task(),
        )

        assert row_id > 0
        assert claimed is not None
        assert claimed.id == "task-1"
        updated = await store.get_runtime_task("task-1")
        assert updated is not None
        assert updated.status == "RUNNING"
        history = await store.load_history("discord", "100", "thread-1")
        assert history[0]["content"] == "hello"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_split_delete_thread_cleans_conversation_history_and_sessions(tmp_path):
    store = SplitSQLiteMemoryStore(
        conversation_path=tmp_path / "memory.db",
        runtime_state_path=tmp_path / "runtime.db",
        skills_telemetry_path=tmp_path / "skills.db",
    )
    await store.init()
    try:
        await store.append("discord", "100", "thread-1", {"role": "user", "content": "hello"})
        await store.save_session("discord", "100", "thread-1", "codex", "sess-1")

        await store.delete_thread("discord", "100", "thread-1")

        history = await store.load_history("discord", "100", "thread-1")
        assert history == []
        assert await store.load_session("discord", "100", "thread-1", "codex") is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_split_migration_refuses_partial_layout(tmp_path):
    memory_path = tmp_path / "memory.db"
    runtime_path = tmp_path / "runtime.db"
    skills_path = tmp_path / "skills.db"

    conversation_only = SplitSQLiteMemoryStore(
        conversation_path=memory_path,
        runtime_state_path=runtime_path,
        skills_telemetry_path=skills_path,
    )
    await conversation_only.init()
    await conversation_only.close()

    runtime_path.unlink()
    skills_path.unlink()

    with pytest.raises(RuntimeError, match="partial split layout"):
        await maybe_split_legacy_memory_db(
            memory_path=memory_path,
            runtime_state_path=runtime_path,
            skills_telemetry_path=skills_path,
            logger=logging.getLogger("test"),
        )


@pytest.mark.asyncio
async def test_split_migration_backfills_runtime_task_defaults_and_recovers_from_backup_only_state(tmp_path):
    memory_path = tmp_path / "memory.db"
    runtime_path = tmp_path / "runtime.db"
    skills_path = tmp_path / "skills.db"

    with sqlite3.connect(memory_path) as conn:
        conn.execute(
            """
            CREATE TABLE turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                author TEXT,
                agent TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                turns_start INTEGER NOT NULL,
                turns_end INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE runtime_tasks (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                created_by TEXT NOT NULL,
                goal TEXT NOT NULL,
                original_request TEXT,
                preferred_agent TEXT,
                status TEXT NOT NULL,
                step_no INTEGER NOT NULL DEFAULT 0,
                max_steps INTEGER NOT NULL,
                max_minutes INTEGER NOT NULL,
                test_command TEXT NOT NULL,
                workspace_path TEXT,
                decision_message_id TEXT,
                status_message_id TEXT,
                blocked_reason TEXT,
                error TEXT,
                summary TEXT,
                resume_instruction TEXT,
                merge_commit_hash TEXT,
                merge_error TEXT,
                completion_mode TEXT,
                output_summary TEXT,
                artifact_manifest TEXT,
                automation_name TEXT,
                workspace_cleaned_at TIMESTAMP,
                task_type TEXT,
                skill_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO turns (platform, channel_id, thread_id, role, content) VALUES (?, ?, ?, ?, ?)",
            ("discord", "100", "thread-legacy", "user", "legacy hello"),
        )
        conn.execute(
            """
            INSERT INTO runtime_tasks (
                id, platform, channel_id, thread_id, created_by, goal, status, max_steps, max_minutes,
                test_command, completion_mode, task_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task-legacy",
                "discord",
                "100",
                "thread-legacy",
                "user",
                "legacy task",
                "PENDING",
                1,
                10,
                "true",
                None,
                None,
            ),
        )
        conn.commit()

    backup_db = memory_path.with_name("memory.db.monolith.bak")
    memory_path.rename(backup_db)

    migrated = await maybe_split_legacy_memory_db(
        memory_path=memory_path,
        runtime_state_path=runtime_path,
        skills_telemetry_path=skills_path,
        logger=logging.getLogger("test"),
    )
    assert migrated is True
    assert memory_path.exists()
    assert runtime_path.exists()
    assert skills_path.exists()

    store = SplitSQLiteMemoryStore(
        conversation_path=memory_path,
        runtime_state_path=runtime_path,
        skills_telemetry_path=skills_path,
    )
    await store.init()
    try:
        history = await store.load_history("discord", "100", "thread-legacy")
        assert history[0]["content"] == "legacy hello"
        task = await store.get_runtime_task("task-legacy")
        assert task is not None
        assert task.task_type == "repo_change"
        assert task.completion_mode == "merge"
    finally:
        await store.close()
