import pytest

from oh_my_agent.memory.store import SQLiteMemoryStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteMemoryStore(tmp_path / "runtime.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_skill_provenance_merge_overrides_reverse_sync(store):
    await store.upsert_skill_provenance(
        "weather",
        source_task_id=None,
        created_by="agent-side-effect",
        agent_name="agent-side-effect",
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        validation_mode="reverse_sync",
        validated=0,
    )

    await store.upsert_skill_provenance(
        "weather",
        source_task_id="task-1",
        created_by="owner-1",
        agent_name="claude",
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        validation_mode="quick_validate",
        validated=1,
        validation_warnings=["warn-1"],
        merged_commit_hash="abc123",
    )

    row = await store.get_skill_provenance("weather")
    assert row is not None
    assert row["source_task_id"] == "task-1"
    assert row["created_by"] == "owner-1"
    assert row["agent_name"] == "claude"
    assert row["validated"] == 1
    assert row["merged_commit_hash"] == "abc123"
    assert row["validation_mode"] == "quick_validate"
    assert row["validation_warnings"] == ["warn-1"]


@pytest.mark.asyncio
async def test_skill_provenance_reverse_sync_does_not_override_merged_facts(store):
    await store.upsert_skill_provenance(
        "weather",
        source_task_id="task-1",
        created_by="owner-1",
        agent_name="claude",
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        validation_mode="quick_validate",
        validated=1,
        merged_commit_hash="abc123",
    )

    await store.upsert_skill_provenance(
        "weather",
        source_task_id=None,
        created_by="agent-side-effect",
        agent_name="agent-side-effect",
        validation_mode="reverse_sync",
        validated=0,
        validation_warnings=["warn-2"],
    )

    row = await store.get_skill_provenance("weather")
    assert row is not None
    assert row["source_task_id"] == "task-1"
    assert row["created_by"] == "owner-1"
    assert row["agent_name"] == "claude"
    assert row["validated"] == 1
    assert row["merged_commit_hash"] == "abc123"
    assert row["validation_mode"] == "quick_validate"
    assert row["validation_warnings"] == ["warn-2"]
