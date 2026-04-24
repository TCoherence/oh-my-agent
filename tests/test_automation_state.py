"""Tests for automation runtime state persistence (Phase 2.1)."""

import pytest

from oh_my_agent.memory.store import SQLiteMemoryStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteMemoryStore(tmp_path / "test.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_upsert_creates_new_state(store):
    await store.upsert_automation_state(
        "daily-scan",
        platform="discord",
        channel_id="ch1",
        enabled=True,
        last_task_id="t-001",
        last_run_at="__NOW__",
    )
    state = await store.get_automation_state("daily-scan")
    assert state is not None
    assert state.name == "daily-scan"
    assert state.platform == "discord"
    assert state.channel_id == "ch1"
    assert state.enabled is True
    assert state.last_task_id == "t-001"
    assert state.last_run_at is not None
    assert state.updated_at is not None


@pytest.mark.asyncio
async def test_upsert_updates_existing_state(store):
    await store.upsert_automation_state(
        "daily-scan",
        platform="discord",
        channel_id="ch1",
        enabled=True,
        last_task_id="t-001",
        last_run_at="__NOW__",
    )
    await store.upsert_automation_state(
        "daily-scan",
        last_success_at="__NOW__",
        last_error=None,
    )
    state = await store.get_automation_state("daily-scan")
    assert state is not None
    assert state.last_task_id == "t-001"
    assert state.last_success_at is not None
    assert state.last_error is None


@pytest.mark.asyncio
async def test_upsert_writes_error_on_failure(store):
    await store.upsert_automation_state(
        "daily-scan",
        platform="discord",
        channel_id="ch1",
    )
    await store.upsert_automation_state(
        "daily-scan",
        last_error="task t-002 timed out",
    )
    state = await store.get_automation_state("daily-scan")
    assert state is not None
    assert state.last_error == "task t-002 timed out"


@pytest.mark.asyncio
async def test_upsert_clears_error_on_success(store):
    await store.upsert_automation_state(
        "daily-scan",
        platform="discord",
        channel_id="ch1",
        last_error="some error",
    )
    await store.upsert_automation_state(
        "daily-scan",
        last_success_at="__NOW__",
        last_error=None,
    )
    state = await store.get_automation_state("daily-scan")
    assert state is not None
    assert state.last_error is None
    assert state.last_success_at is not None


@pytest.mark.asyncio
async def test_disabled_next_run_at_null(store):
    await store.upsert_automation_state(
        "daily-scan",
        platform="discord",
        channel_id="ch1",
        enabled=False,
        next_run_at=None,
    )
    state = await store.get_automation_state("daily-scan")
    assert state is not None
    assert state.enabled is False
    assert state.next_run_at is None


@pytest.mark.asyncio
async def test_enabled_with_next_run_at(store):
    await store.upsert_automation_state(
        "daily-scan",
        platform="discord",
        channel_id="ch1",
        enabled=True,
        next_run_at="2026-04-10T09:00:00+08:00",
    )
    state = await store.get_automation_state("daily-scan")
    assert state is not None
    assert state.enabled is True
    assert state.next_run_at == "2026-04-10T09:00:00+08:00"


@pytest.mark.asyncio
async def test_list_automation_states(store):
    await store.upsert_automation_state(
        "alpha",
        platform="discord",
        channel_id="ch1",
    )
    await store.upsert_automation_state(
        "beta",
        platform="discord",
        channel_id="ch2",
    )
    states = await store.list_automation_states()
    assert len(states) == 2
    assert states[0].name == "alpha"
    assert states[1].name == "beta"


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(store):
    assert await store.get_automation_state("nope") is None


@pytest.mark.asyncio
async def test_delete_automation_state(store):
    await store.upsert_automation_state(
        "daily-scan",
        platform="discord",
        channel_id="ch1",
    )
    assert await store.get_automation_state("daily-scan") is not None
    await store.delete_automation_state("daily-scan")
    assert await store.get_automation_state("daily-scan") is None


@pytest.mark.asyncio
async def test_hot_reload_preserves_runtime_state(store):
    """Runtime state must survive upsert calls that only update enabled/next_run_at."""
    await store.upsert_automation_state(
        "daily-scan",
        platform="discord",
        channel_id="ch1",
        enabled=True,
        last_run_at="__NOW__",
        last_success_at="__NOW__",
        last_task_id="t-005",
    )
    # Simulate hot-reload: only updates enabled + next_run_at
    await store.upsert_automation_state(
        "daily-scan",
        enabled=True,
        next_run_at="2026-04-11T09:00:00+08:00",
    )
    state = await store.get_automation_state("daily-scan")
    assert state is not None
    assert state.last_run_at is not None
    assert state.last_success_at is not None
    assert state.last_task_id == "t-005"
    assert state.next_run_at == "2026-04-11T09:00:00+08:00"


@pytest.mark.asyncio
async def test_fire_failure_does_not_write_last_run_at(store):
    """Fire failure only writes last_error, not last_run_at."""
    await store.upsert_automation_state(
        "daily-scan",
        platform="discord",
        channel_id="ch1",
        last_error="no active channel",
    )
    state = await store.get_automation_state("daily-scan")
    assert state is not None
    assert state.last_error == "no active channel"
    assert state.last_run_at is None


@pytest.mark.asyncio
async def test_restart_reads_persisted_state(tmp_path):
    """After process restart, state is still readable."""
    db_path = tmp_path / "test.db"
    store1 = SQLiteMemoryStore(db_path)
    await store1.init()
    await store1.upsert_automation_state(
        "daily-scan",
        platform="discord",
        channel_id="ch1",
        enabled=True,
        last_run_at="__NOW__",
        last_success_at="__NOW__",
        last_task_id="t-010",
        next_run_at="2026-04-10T09:00:00+08:00",
    )
    await store1.close()

    # Simulate restart
    store2 = SQLiteMemoryStore(db_path)
    await store2.init()
    state = await store2.get_automation_state("daily-scan")
    await store2.close()

    assert state is not None
    assert state.last_task_id == "t-010"
    assert state.last_run_at is not None
    assert state.last_success_at is not None
    assert state.next_run_at == "2026-04-10T09:00:00+08:00"
