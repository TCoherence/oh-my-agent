"""Tests for the usage ledger (`usage_events` table + record/summary methods)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.utils.usage import record_usage_from_response


@pytest.fixture
async def store(tmp_path):
    s = SQLiteMemoryStore(tmp_path / "usage.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_record_usage_event_persists_signal(store):
    await store.record_usage_event(
        agent="claude",
        source="chat",
        platform="discord",
        channel_id="ch",
        thread_id="t",
        model="sonnet",
        input_tokens=123,
        output_tokens=45,
        cost_usd=0.001,
    )
    summary = await store.get_usage_summary(since_ts="1970-01-01 00:00:00")
    assert int(summary["total"]["events"]) == 1
    assert int(summary["total"]["input_tokens"]) == 123
    assert int(summary["total"]["output_tokens"]) == 45
    assert float(summary["total"]["cost_usd"]) == pytest.approx(0.001)


@pytest.mark.asyncio
async def test_record_usage_event_skips_when_no_numeric_signal(store):
    await store.record_usage_event(
        agent="claude",
        source="chat",
        platform="discord",
        channel_id="ch",
        thread_id="t",
    )
    summary = await store.get_usage_summary(since_ts="1970-01-01 00:00:00")
    assert int(summary["total"]["events"] or 0) == 0


@pytest.mark.asyncio
async def test_get_usage_summary_by_agent_and_source(store):
    await store.record_usage_event(
        agent="claude", source="chat", input_tokens=100, output_tokens=50, cost_usd=0.01
    )
    await store.record_usage_event(
        agent="codex", source="chat", input_tokens=200, output_tokens=80, cost_usd=0.02
    )
    await store.record_usage_event(
        agent="claude", source="runtime", input_tokens=300, output_tokens=150, cost_usd=0.03,
        task_id="t-1",
    )
    summary = await store.get_usage_summary(since_ts="1970-01-01 00:00:00")
    assert int(summary["total"]["events"]) == 3
    by_agent = {row["agent"]: row for row in summary["by_agent"]}
    assert int(by_agent["claude"]["events"]) == 2
    assert int(by_agent["codex"]["events"]) == 1
    assert int(by_agent["claude"]["input_tokens"]) == 400
    by_source = {row["source"]: row for row in summary["by_source"]}
    assert int(by_source["chat"]["events"]) == 2
    assert int(by_source["runtime"]["events"]) == 1


@pytest.mark.asyncio
async def test_get_usage_summary_time_window_and_scope(store):
    await store.record_usage_event(
        agent="claude", source="chat",
        platform="discord", channel_id="A", thread_id="t1",
        input_tokens=10, output_tokens=5, cost_usd=0.001,
    )
    await store.record_usage_event(
        agent="claude", source="chat",
        platform="discord", channel_id="A", thread_id="t2",
        input_tokens=20, output_tokens=10, cost_usd=0.002,
    )
    await store.record_usage_event(
        agent="claude", source="chat",
        platform="discord", channel_id="B", thread_id="tx",
        input_tokens=99, output_tokens=99, cost_usd=0.9,
    )

    # Thread scope filter
    t1_summary = await store.get_usage_summary(
        since_ts="1970-01-01 00:00:00",
        platform="discord", channel_id="A", thread_id="t1",
    )
    assert int(t1_summary["total"]["events"]) == 1
    assert int(t1_summary["total"]["input_tokens"]) == 10

    # Channel scope filter
    channel_summary = await store.get_usage_summary(
        since_ts="1970-01-01 00:00:00",
        platform="discord", channel_id="A",
    )
    assert int(channel_summary["total"]["events"]) == 2

    # Future since_ts excludes everything
    future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    empty = await store.get_usage_summary(since_ts=future)
    assert int(empty["total"]["events"] or 0) == 0


@pytest.mark.asyncio
async def test_record_usage_from_response_helper_handles_none(store):
    # None store -> no-op
    await record_usage_from_response(
        None,
        agent="claude", source="chat",
        response=AgentResponse(text="hi", usage={"input_tokens": 1}),
    )
    # Response without usage -> no-op
    await record_usage_from_response(
        store,
        agent="claude", source="chat",
        response=AgentResponse(text="hi", usage=None),
    )
    summary = await store.get_usage_summary(since_ts="1970-01-01 00:00:00")
    assert int(summary["total"]["events"] or 0) == 0


@pytest.mark.asyncio
async def test_record_usage_from_response_persists_from_agent_response(store):
    resp = AgentResponse(
        text="ok",
        usage={
            "input_tokens": 50,
            "output_tokens": 20,
            "cache_read_input_tokens": 5,
            "cache_creation_input_tokens": 2,
            "cost_usd": 0.0042,
        },
    )
    await record_usage_from_response(
        store,
        agent="claude",
        source="runtime",
        platform="discord",
        channel_id="ch",
        thread_id="t",
        response=resp,
        task_id="task-xyz",
    )
    summary = await store.get_usage_summary(since_ts="1970-01-01 00:00:00")
    assert int(summary["total"]["events"]) == 1
    row = summary["by_source"][0]
    assert row["source"] == "runtime"
    assert pytest.approx(float(row["cost_usd"])) == 0.0042
