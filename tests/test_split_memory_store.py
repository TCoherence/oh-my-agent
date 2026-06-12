from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
import sqlite3
from pathlib import Path

import pytest

from oh_my_agent.memory.store import (
    CONVERSATION_FTS_SHADOW_TABLES,
    CONVERSATION_TABLES,
    RUNTIME_STATE_TABLES,
    SKILLS_TELEMETRY_TABLES,
    SplitSQLiteMemoryStore,
    SQLiteMemoryStore,
    SQLiteRuntimeStateStore,
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
    await legacy.set_skill_override("deals-scanner", enabled=False)
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

    # Manual skill overrides must survive the split: row lands in the skills
    # DB (the isdisjoint checks above prove the table left the other two).
    with sqlite3.connect(skills_path) as conn:
        override_rows = conn.execute(
            "SELECT skill_name, enabled FROM skill_overrides"
        ).fetchall()
    assert override_rows == [("deals-scanner", 0)]

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
        assert await store.list_manual_disabled_skills() == {"deals-scanner"}
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


@pytest.mark.asyncio
async def test_existing_runtime_db_creates_missing_tables_on_init(tmp_path):
    """Regression: an existing runtime.db missing newly added tables
    (e.g. automation_runtime_state) must auto-create them on init."""
    runtime_path = tmp_path / "runtime.db"

    # Create a minimal runtime DB with only agent_sessions
    with sqlite3.connect(runtime_path) as conn:
        conn.execute(
            """CREATE TABLE agent_sessions (
                platform TEXT, channel_id TEXT, thread_id TEXT,
                agent TEXT, session_id TEXT,
                PRIMARY KEY (platform, channel_id, thread_id, agent))"""
        )
        conn.execute(
            "INSERT INTO agent_sessions VALUES ('discord','100','t1','codex','s1')"
        )
        conn.commit()

    # Before init: table does NOT exist
    pre_tables = _tables(runtime_path)
    assert "automation_runtime_state" not in pre_tables

    store = SQLiteRuntimeStateStore(runtime_path)
    await store.init()
    try:
        # After init: table MUST exist
        post_tables = _tables(runtime_path)
        assert "automation_runtime_state" in post_tables

        # And the table is functional
        await store.upsert_automation_state(
            name="test-job",
            platform="discord",
            channel_id="100",
            enabled=True,
            next_run_at="2026-04-12T00:00:00",
        )
        state = await store.get_automation_state("test-job")
        assert state is not None
        assert state.name == "test-job"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_split_store_forwards_get_task_statuses(tmp_path):
    """Regression: _RUNTIME_METHODS must include get_task_statuses so the
    SplitSQLiteMemoryStore.__getattr__ wrapper forwards it to the runtime
    state store. Otherwise production cleanup paths AttributeError."""
    store = SplitSQLiteMemoryStore(
        conversation_path=tmp_path / "memory.db",
        runtime_state_path=tmp_path / "runtime.db",
        skills_telemetry_path=tmp_path / "skills.db",
    )
    await store.init()
    try:
        await store.create_runtime_task(
            task_id="ttt-1",
            platform="discord",
            channel_id="100",
            thread_id="thread-1",
            created_by="user",
            goal="do",
            status="PENDING",
            max_steps=1,
            max_minutes=10,
            test_command="true",
        )
        statuses = await store.get_task_statuses(["ttt-1", "missing"])
        assert statuses == {"ttt-1": "PENDING"}
        assert await store.get_task_statuses([]) == {}
    finally:
        await store.close()


def test_split_store_dispatch_covers_every_public_store_method():
    """Recurrence guard: every public async method on SQLiteMemoryStore must
    be reachable through SplitSQLiteMemoryStore — boot always constructs the
    split store, so a method missing from the __getattr__ dispatch sets is a
    production AttributeError, not a test-only gap."""
    dispatch_sets = {
        "_CONVERSATION_METHODS": SplitSQLiteMemoryStore._CONVERSATION_METHODS,
        "_RUNTIME_METHODS": SplitSQLiteMemoryStore._RUNTIME_METHODS,
        "_SKILLS_METHODS": SplitSQLiteMemoryStore._SKILLS_METHODS,
    }
    # Methods intentionally NOT routed through __getattr__. Keep empty unless
    # a method is conversation-store-internal by design; document the reason
    # next to each entry.
    intentionally_unrouted: set[str] = set()

    public_async = {
        name
        for name, _member in inspect.getmembers(
            SQLiteMemoryStore, predicate=inspect.iscoroutinefunction
        )
        if not name.startswith("_")
    }
    implemented_on_split = set(vars(SplitSQLiteMemoryStore)) & public_async
    routed: set[str] = set().union(*dispatch_sets.values())

    missing = sorted(public_async - routed - implemented_on_split - intentionally_unrouted)
    assert not missing, (
        "SQLiteMemoryStore public async methods unreachable through "
        f"SplitSQLiteMemoryStore.__getattr__: {missing}. Add each to exactly "
        "one of _CONVERSATION_METHODS / _RUNTIME_METHODS / _SKILLS_METHODS, "
        "implement it on SplitSQLiteMemoryStore, or document it in "
        "intentionally_unrouted above."
    )

    phantoms = sorted(routed - public_async)
    assert not phantoms, (
        "Dispatch sets route names that are not public async methods of "
        f"SQLiteMemoryStore (typos?): {phantoms}"
    )

    for (name_a, set_a), (name_b, set_b) in itertools.combinations(dispatch_sets.items(), 2):
        overlap = sorted(set_a & set_b)
        assert not overlap, (
            f"{name_a} and {name_b} both route {overlap}; each method must "
            "belong to exactly one dispatch set"
        )


@pytest.mark.asyncio
async def test_split_store_forwards_late_added_methods(tmp_path):
    """Regression: skill override + feedback-collector automation-post helpers
    were implemented on SQLiteMemoryStore but missing from the dispatch sets,
    so the split store raised AttributeError in production."""
    runtime_path = tmp_path / "runtime.db"
    store = SplitSQLiteMemoryStore(
        conversation_path=tmp_path / "memory.db",
        runtime_state_path=runtime_path,
        skills_telemetry_path=tmp_path / "skills.db",
    )
    await store.init()
    try:
        await store.set_skill_override("deals-scanner", enabled=False)
        assert await store.list_manual_disabled_skills() == {"deals-scanner"}

        await store.record_automation_post(
            platform="discord",
            channel_id="100",
            message_id="msg-1",
            automation_name="daily-brief",
            task_id="task-1",
        )
        by_message = await store.get_automation_post_by_message(message_id="msg-1")
        assert by_message is not None
        assert by_message["automation_name"] == "daily-brief"
        by_task = await store.get_automation_post_by_task(task_id="task-1")
        assert by_task is not None
        assert by_task["message_id"] == "msg-1"

        assert await store.list_automation_posts_older_than(hours=1) == []
        with sqlite3.connect(runtime_path) as conn:
            conn.execute("UPDATE automation_posts SET fired_at=datetime('now', '-3 hours')")
            conn.commit()
        older = await store.list_automation_posts_older_than(hours=1)
        assert [post["message_id"] for post in older] == ["msg-1"]
    finally:
        await store.close()


_STRAY_SKILL_OVERRIDES_DDL = (
    "CREATE TABLE skill_overrides ("
    " skill_name TEXT PRIMARY KEY,"
    " enabled INTEGER NOT NULL DEFAULT 1,"
    " updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
)


@pytest.mark.asyncio
async def test_existing_split_layout_with_stray_skill_overrides_boots(tmp_path):
    """Regression: before skill_overrides joined SKILLS_TELEMETRY_TABLES, the
    schema script leaked an empty copy into every split DB. Boot must tolerate
    those strays (drop them on init) instead of failing the unexpected-table
    guards."""
    memory_path = tmp_path / "memory.db"
    runtime_path = tmp_path / "runtime.db"
    skills_path = tmp_path / "skills.db"

    store = SplitSQLiteMemoryStore(
        conversation_path=memory_path,
        runtime_state_path=runtime_path,
        skills_telemetry_path=skills_path,
    )
    await store.init()
    await store.set_skill_override("deals-scanner", enabled=False)
    await store.close()

    # Mimic the older-build leak: empty stray copies in the wrong DBs.
    for path in (memory_path, runtime_path):
        assert "skill_overrides" not in _tables(path)
        with sqlite3.connect(path) as conn:
            conn.execute(_STRAY_SKILL_OVERRIDES_DDL)
            conn.commit()

    migrated = await maybe_split_legacy_memory_db(
        memory_path=memory_path,
        runtime_state_path=runtime_path,
        skills_telemetry_path=skills_path,
        logger=logging.getLogger("test"),
    )
    assert migrated is False

    reopened = SplitSQLiteMemoryStore(
        conversation_path=memory_path,
        runtime_state_path=runtime_path,
        skills_telemetry_path=skills_path,
    )
    await reopened.init()
    try:
        assert await reopened.list_manual_disabled_skills() == {"deals-scanner"}
    finally:
        await reopened.close()

    assert "skill_overrides" not in _tables(memory_path)
    assert "skill_overrides" not in _tables(runtime_path)
    assert "skill_overrides" in _tables(skills_path)


@pytest.mark.asyncio
async def test_non_empty_stray_skill_overrides_still_raises(tmp_path):
    """A stray skill_overrides table WITH rows is not a known-benign leak —
    the guard must keep refusing rather than silently drop data."""
    runtime_path = tmp_path / "runtime.db"
    store = SQLiteRuntimeStateStore(runtime_path)
    await store.init()
    await store.close()

    with sqlite3.connect(runtime_path) as conn:
        conn.execute(_STRAY_SKILL_OVERRIDES_DDL)
        conn.execute("INSERT INTO skill_overrides (skill_name, enabled) VALUES ('x', 0)")
        conn.commit()

    reopened = SQLiteRuntimeStateStore(runtime_path)
    try:
        with pytest.raises(RuntimeError, match="unexpected tables"):
            await reopened.init()
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_split_conversation_db_keeps_single_summary_row_and_index(tmp_path):
    store = SplitSQLiteMemoryStore(
        conversation_path=tmp_path / "memory.db",
        runtime_state_path=tmp_path / "runtime.db",
        skills_telemetry_path=tmp_path / "skills.db",
    )
    await store.init()
    try:
        ids = [
            await store.append("discord", "100", "thread-1", {"role": "user", "content": f"m{i}"})
            for i in range(4)
        ]
        await store.save_summary(
            "discord", "100", "thread-1", summary="s1", turns_start=ids[0], turns_end=ids[1]
        )
        await store.save_summary(
            "discord", "100", "thread-1", summary="s2", turns_start=ids[2], turns_end=ids[3]
        )
    finally:
        await store.close()

    with sqlite3.connect(tmp_path / "memory.db") as conn:
        indexes = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        rows = conn.execute("SELECT summary FROM summaries").fetchall()
    assert "idx_summaries_thread" in indexes
    assert [str(row[0]) for row in rows] == ["s2"]
