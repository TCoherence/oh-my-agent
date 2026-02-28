import pytest

from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.runtime.types import (
    TASK_COMPLETION_MERGE,
    TASK_COMPLETION_REPLY,
    TASK_STATUS_DRAFT,
    TASK_STATUS_MERGED,
    TASK_STATUS_PENDING,
    TASK_STATUS_RUNNING,
    TASK_TYPE_ARTIFACT,
    TASK_TYPE_REPO_CHANGE,
)


@pytest.fixture
async def store(tmp_path):
    s = SQLiteMemoryStore(tmp_path / "runtime.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_runtime_task_crud_and_listing(store):
    created = await store.create_runtime_task(
        task_id="task-1",
        platform="discord",
        channel_id="100",
        thread_id="100",
        created_by="u1",
        goal="fix tests",
        preferred_agent="codex",
        status=TASK_STATUS_DRAFT,
        max_steps=8,
        max_minutes=20,
        test_command="pytest -q",
    )
    assert created.id == "task-1"
    assert created.status == TASK_STATUS_DRAFT

    loaded = await store.get_runtime_task("task-1")
    assert loaded is not None
    assert loaded.goal == "fix tests"
    assert loaded.task_type == TASK_TYPE_REPO_CHANGE
    assert loaded.completion_mode == TASK_COMPLETION_MERGE

    tasks = await store.list_runtime_tasks(platform="discord", channel_id="100", limit=10)
    assert len(tasks) == 1
    assert tasks[0].id == "task-1"

    updated = await store.update_runtime_task("task-1", status=TASK_STATUS_PENDING, step_no=1)
    assert updated is not None
    assert updated.status == TASK_STATUS_PENDING
    assert updated.step_no == 1


@pytest.mark.asyncio
async def test_runtime_claim_requeue_and_checkpoint(store):
    await store.create_runtime_task(
        task_id="task-2",
        platform="discord",
        channel_id="100",
        thread_id="100",
        created_by="u1",
        goal="implement feature",
        preferred_agent="codex",
        status=TASK_STATUS_PENDING,
        max_steps=8,
        max_minutes=20,
        test_command="pytest -q",
    )

    claimed = await store.claim_pending_runtime_task()
    assert claimed is not None
    assert claimed.id == "task-2"
    assert claimed.status == TASK_STATUS_RUNNING

    changed = await store.requeue_inflight_runtime_tasks()
    assert changed >= 1
    again = await store.get_runtime_task("task-2")
    assert again is not None
    assert again.status == TASK_STATUS_PENDING

    await store.add_runtime_checkpoint(
        task_id="task-2",
        step_no=1,
        status=TASK_STATUS_RUNNING,
        prompt_digest="prompt",
        agent_result="agent output",
        test_result="failing test",
        files_changed=["src/app.py"],
    )
    ckpt = await store.get_last_runtime_checkpoint("task-2")
    assert ckpt is not None
    assert ckpt["step_no"] == 1
    assert "failing test" in ckpt["test_result"]


@pytest.mark.asyncio
async def test_runtime_decision_nonce_lifecycle(store):
    await store.create_runtime_task(
        task_id="task-3",
        platform="discord",
        channel_id="100",
        thread_id="100",
        created_by="u1",
        goal="update docs",
        preferred_agent="codex",
        status=TASK_STATUS_DRAFT,
        max_steps=8,
        max_minutes=20,
        test_command="pytest -q",
    )

    nonce = await store.create_runtime_decision_nonce("task-3", ttl_minutes=30)
    assert len(nonce) == 8
    active = await store.get_active_runtime_decision_nonce("task-3")
    assert active == nonce

    ok = await store.consume_runtime_decision_nonce(
        task_id="task-3",
        nonce=nonce,
        action="approve",
        actor_id="owner-1",
        source="slash",
        result="accepted",
    )
    assert ok is True

    active_after = await store.get_active_runtime_decision_nonce("task-3")
    assert active_after is None


@pytest.mark.asyncio
async def test_runtime_cleanup_candidate_filter(store):
    await store.create_runtime_task(
        task_id="task-4",
        platform="discord",
        channel_id="100",
        thread_id="100",
        created_by="u1",
        goal="merge-ready task",
        preferred_agent="codex",
        status=TASK_STATUS_MERGED,
        max_steps=8,
        max_minutes=20,
        test_command="pytest -q",
    )
    await store.update_runtime_task(
        "task-4",
        workspace_path="/tmp/workspace-task-4",
        ended_at="2000-01-01 00:00:00",
    )

    candidates = await store.list_runtime_cleanup_candidates(
        statuses=[TASK_STATUS_MERGED],
        older_than_hours=1,
        limit=10,
    )
    assert len(candidates) == 1
    assert candidates[0].id == "task-4"


@pytest.mark.asyncio
async def test_runtime_task_stores_artifact_fields(store):
    created = await store.create_runtime_task(
        task_id="task-artifact",
        platform="discord",
        channel_id="100",
        thread_id="200",
        created_by="u1",
        goal="generate a daily report",
        preferred_agent="codex",
        status=TASK_STATUS_PENDING,
        max_steps=4,
        max_minutes=10,
        test_command="true",
        task_type=TASK_TYPE_ARTIFACT,
        completion_mode=TASK_COMPLETION_REPLY,
        output_summary="Artifacts (1): report.md",
        artifact_manifest=["report.md"],
    )
    assert created.task_type == TASK_TYPE_ARTIFACT
    loaded = await store.get_runtime_task("task-artifact")
    assert loaded is not None
    assert loaded.task_type == TASK_TYPE_ARTIFACT
    assert loaded.completion_mode == TASK_COMPLETION_REPLY
    assert loaded.output_summary == "Artifacts (1): report.md"
    assert loaded.artifact_manifest == ["report.md"]
