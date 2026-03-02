import pytest
import tempfile
from pathlib import Path

from oh_my_agent.memory.store import SQLiteMemoryStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteMemoryStore(tmp_path / "test.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_append_and_load(store):
    turn = {"role": "user", "content": "hello", "author": "alice"}
    row_id = await store.append("discord", "ch1", "t1", turn)
    assert row_id > 0

    history = await store.load_history("discord", "ch1", "t1")
    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "hello"
    assert history[0]["author"] == "alice"


@pytest.mark.asyncio
async def test_multiple_turns_in_order(store):
    await store.append("discord", "ch1", "t1", {"role": "user", "content": "q1"})
    await store.append("discord", "ch1", "t1", {"role": "assistant", "content": "a1", "agent": "claude"})
    await store.append("discord", "ch1", "t1", {"role": "user", "content": "q2"})

    history = await store.load_history("discord", "ch1", "t1")
    assert len(history) == 3
    assert [h["role"] for h in history] == ["user", "assistant", "user"]
    assert history[1]["agent"] == "claude"


@pytest.mark.asyncio
async def test_threads_are_isolated(store):
    await store.append("discord", "ch1", "t1", {"role": "user", "content": "msg-t1"})
    await store.append("discord", "ch1", "t2", {"role": "user", "content": "msg-t2"})

    h1 = await store.load_history("discord", "ch1", "t1")
    h2 = await store.load_history("discord", "ch1", "t2")
    assert len(h1) == 1
    assert len(h2) == 1
    assert h1[0]["content"] == "msg-t1"
    assert h2[0]["content"] == "msg-t2"


@pytest.mark.asyncio
async def test_delete_thread(store):
    await store.append("discord", "ch1", "t1", {"role": "user", "content": "hello"})
    await store.delete_thread("discord", "ch1", "t1")
    history = await store.load_history("discord", "ch1", "t1")
    assert history == []


@pytest.mark.asyncio
async def test_count_turns(store):
    for i in range(5):
        await store.append("discord", "ch1", "t1", {"role": "user", "content": f"msg-{i}"})
    count = await store.count_turns("discord", "ch1", "t1")
    assert count == 5


@pytest.mark.asyncio
async def test_save_summary_and_load(store):
    ids = []
    for i in range(10):
        rid = await store.append("discord", "ch1", "t1", {"role": "user", "content": f"msg-{i}"})
        ids.append(rid)

    # Summarise first 5 turns
    await store.save_summary(
        "discord", "ch1", "t1",
        summary="User discussed messages 0-4",
        turns_start=ids[0],
        turns_end=ids[4],
    )

    history = await store.load_history("discord", "ch1", "t1")
    # Should have: 1 summary + 5 remaining raw turns
    assert len(history) == 6
    assert history[0]["role"] == "system"
    assert "messages 0-4" in history[0]["content"]
    assert history[1]["content"] == "msg-5"

    # Original summarised turns should be deleted
    count = await store.count_turns("discord", "ch1", "t1")
    assert count == 5


@pytest.mark.asyncio
async def test_fts_search(store):
    await store.append("discord", "ch1", "t1", {"role": "user", "content": "the weather in Seattle is rainy"})
    await store.append("discord", "ch1", "t1", {"role": "user", "content": "hello world"})

    results = await store.search("Seattle weather")
    assert len(results) >= 1
    assert "Seattle" in results[0]["content"]


@pytest.mark.asyncio
async def test_empty_thread_returns_empty(store):
    history = await store.load_history("discord", "ch1", "nonexistent")
    assert history == []


@pytest.mark.asyncio
async def test_ephemeral_workspace_lifecycle(store, tmp_path):
    ws = tmp_path / "ws-a"
    await store.upsert_ephemeral_workspace("discord:100:t1", str(ws))

    # Manually age the row for deterministic expiry test.
    db = await store._conn()  # noqa: SLF001
    await db.execute(
        "UPDATE ephemeral_workspaces SET last_used_at='2000-01-01 00:00:00' "
        "WHERE workspace_key=?",
        ("discord:100:t1",),
    )
    await db.commit()

    rows = await store.list_expired_ephemeral_workspaces(ttl_hours=24, limit=10)
    assert len(rows) == 1
    assert rows[0]["workspace_key"] == "discord:100:t1"

    await store.mark_ephemeral_workspace_cleaned("discord:100:t1")
    rows_after = await store.list_expired_ephemeral_workspaces(ttl_hours=24, limit=10)
    assert rows_after == []


@pytest.mark.asyncio
async def test_skill_invocation_stats_include_feedback_without_provenance(store):
    first = await store.record_skill_invocation(
        skill_name="weather",
        agent_name="codex",
        platform="discord",
        channel_id="ch1",
        thread_id="t1",
        user_id="u1",
        route_source="explicit",
        request_id="req-1",
        outcome="success",
        error_kind=None,
        error_text=None,
        latency_ms=1200,
        input_tokens=10,
        output_tokens=20,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    second = await store.record_skill_invocation(
        skill_name="weather",
        agent_name="codex",
        platform="discord",
        channel_id="ch1",
        thread_id="t1",
        user_id="u1",
        route_source="router",
        request_id="req-2",
        outcome="error",
        error_kind="cli_error",
        error_text="boom",
        latency_ms=800,
        input_tokens=9,
        output_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    await store.set_skill_invocation_response_message(first, "m-1")
    await store.upsert_skill_feedback(
        invocation_id=first,
        actor_id="owner-1",
        platform="discord",
        channel_id="ch1",
        thread_id="t1",
        score=1,
        source="reaction",
    )
    await store.upsert_skill_feedback(
        invocation_id=second,
        actor_id="owner-2",
        platform="discord",
        channel_id="ch1",
        thread_id="t1",
        score=-1,
        source="reaction",
    )

    by_message = await store.get_skill_invocation_by_message("m-1")
    assert by_message is not None
    assert by_message["id"] == first

    stats = await store.get_skill_stats("weather", recent_days=7)
    assert len(stats) == 1
    row = stats[0]
    assert row["skill_name"] == "weather"
    assert row["total_invocations"] == 2
    assert row["recent_invocations"] == 2
    assert row["recent_successes"] == 1
    assert row["recent_errors"] == 1
    assert row["thumbs_up"] == 1
    assert row["thumbs_down"] == 1
    assert row["net_feedback"] == 0


@pytest.mark.asyncio
async def test_skill_auto_disabled_persists_without_existing_provenance(store):
    await store.set_skill_auto_disabled("weather", disabled=True, reason="failure_rate=0.80 over last 5 invocations")

    disabled = await store.list_auto_disabled_skills()
    assert disabled == {"weather"}

    provenance = await store.get_skill_provenance("weather")
    assert provenance is not None
    assert provenance["auto_disabled"] == 1
    assert provenance["auto_disabled_reason"] == "failure_rate=0.80 over last 5 invocations"

    await store.set_skill_auto_disabled("weather", disabled=False)
    disabled_after = await store.list_auto_disabled_skills()
    assert disabled_after == set()


@pytest.mark.asyncio
async def test_skill_evaluations_return_latest_per_type(store):
    await store.add_skill_evaluation(
        skill_name="weather",
        source_task_id="task-1",
        evaluation_type="overlap",
        status="review_required",
        summary="too similar",
        details_json={"similarity": 0.8},
    )
    await store.add_skill_evaluation(
        skill_name="weather",
        source_task_id="task-2",
        evaluation_type="overlap",
        status="pass",
        summary="better now",
        details_json={"similarity": 0.2},
    )
    await store.add_skill_evaluation(
        skill_name="weather",
        source_task_id="task-2",
        evaluation_type="source_grounded",
        status="review_required",
        summary="missing metadata",
        details_json={"missing_fields": ["metadata.source_urls"]},
    )

    rows = await store.get_latest_skill_evaluations("weather")
    assert len(rows) == 2
    overlap = next(row for row in rows if row["evaluation_type"] == "overlap")
    source = next(row for row in rows if row["evaluation_type"] == "source_grounded")
    assert overlap["status"] == "pass"
    assert overlap["summary"] == "better now"
    assert source["details_json"]["missing_fields"] == ["metadata.source_urls"]
