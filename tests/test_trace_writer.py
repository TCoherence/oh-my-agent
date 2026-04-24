"""Tests for the per-day JSONL trace writer."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from oh_my_agent.agents.events import (
    TextEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)
from oh_my_agent.trace import TraceWriter


@pytest.mark.asyncio
async def test_trace_writer_writes_jsonl_line_per_event(tmp_path) -> None:
    writer = TraceWriter(tmp_path)
    writer.start()
    ts = datetime(2026, 4, 24, 14, 3, 21, 123456)
    await writer.append(
        agent="claude",
        thread_id="discord:123:t9",
        event=ToolUseEvent(tool_id="tu_1", name="Read", input={"file_path": "/x"}),
        ts=ts,
    )
    await writer.append(
        agent="claude",
        thread_id="discord:123:t9",
        event=ToolResultEvent(tool_id="tu_1", output="42", is_error=False),
        ts=ts,
    )
    await writer.stop()

    path = tmp_path / "2026-04-24.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["type"] == "tool_use"
    assert first["agent"] == "claude"
    assert first["thread_id"] == "discord:123:t9"
    assert first["name"] == "Read"
    assert first["input"] == {"file_path": "/x"}
    assert "T14:03:21" in first["ts"]
    second = json.loads(lines[1])
    assert second["type"] == "tool_result" and second["is_error"] is False


@pytest.mark.asyncio
async def test_trace_writer_splits_files_across_days(tmp_path) -> None:
    writer = TraceWriter(tmp_path)
    writer.start()
    await writer.append(
        agent="codex",
        thread_id="t",
        event=TextEvent(text="hi"),
        ts=datetime(2026, 4, 24, 23, 59),
    )
    await writer.append(
        agent="codex",
        thread_id="t",
        event=TextEvent(text="bye"),
        ts=datetime(2026, 4, 25, 0, 0, 1),
    )
    await writer.stop()

    assert (tmp_path / "2026-04-24.jsonl").read_text().strip().count("\n") == 0
    assert (tmp_path / "2026-04-25.jsonl").read_text().strip().count("\n") == 0
    assert "hi" in (tmp_path / "2026-04-24.jsonl").read_text()
    assert "bye" in (tmp_path / "2026-04-25.jsonl").read_text()


@pytest.mark.asyncio
async def test_trace_writer_serializes_usage_event(tmp_path) -> None:
    writer = TraceWriter(tmp_path)
    writer.start()
    await writer.append(
        agent="claude",
        thread_id="t",
        event=UsageEvent(input_tokens=10, output_tokens=5, cost_usd=0.001),
        ts=datetime(2026, 4, 24, 10, 0, 0),
    )
    await writer.stop()
    payload = json.loads((tmp_path / "2026-04-24.jsonl").read_text().splitlines()[0])
    assert payload["type"] == "usage"
    assert payload["input_tokens"] == 10
    assert payload["cost_usd"] == 0.001


@pytest.mark.asyncio
async def test_trace_writer_preserves_order(tmp_path) -> None:
    writer = TraceWriter(tmp_path)
    writer.start()
    ts = datetime(2026, 4, 24, 12, 0, 0)
    for i in range(10):
        await writer.append(
            agent="codex",
            thread_id="t",
            event=ThinkingEvent(text=f"step-{i}"),
            ts=ts,
        )
    await writer.stop()
    lines = (tmp_path / "2026-04-24.jsonl").read_text().splitlines()
    texts = [json.loads(line)["text"] for line in lines]
    assert texts == [f"step-{i}" for i in range(10)]


@pytest.mark.asyncio
async def test_trace_writer_append_after_stop_noop(tmp_path) -> None:
    writer = TraceWriter(tmp_path)
    writer.start()
    await writer.stop()
    await writer.append(
        agent="claude",
        thread_id="t",
        event=TextEvent(text="ignored"),
        ts=datetime(2026, 4, 24, 10, 0, 0),
    )
    assert not (tmp_path / "2026-04-24.jsonl").exists()


@pytest.mark.asyncio
async def test_base_cli_agent_emit_trace_events_replays_stdout(tmp_path) -> None:
    """``_emit_trace_events`` must replay stream-json stdout through
    ``_parse_stream_line`` and feed the resulting events to the writer."""
    from oh_my_agent.agents.cli.claude import ClaudeAgent

    writer = TraceWriter(tmp_path)
    writer.start()
    agent = ClaudeAgent()
    agent.set_trace_writer(writer)

    stdout_lines = [
        json.dumps({
            "type": "system", "subtype": "init",
            "session_id": "sid-1", "model": "sonnet",
            "tools": ["Read", "Bash"],
        }),
        json.dumps({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hi"}]},
        }),
        json.dumps({
            "type": "result",
            "result": "hi",
            "usage": {"input_tokens": 5, "output_tokens": 2},
            "total_cost_usd": 0.0001,
        }),
    ]
    stdout_bytes = ("\n".join(stdout_lines) + "\n").encode("utf-8")
    await agent._emit_trace_events(stdout_bytes, thread_id="discord:x:t1")
    await writer.stop()

    lines = list((tmp_path).glob("*.jsonl"))
    assert len(lines) == 1
    records = [json.loads(line) for line in lines[0].read_text().splitlines()]
    types = [r["type"] for r in records]
    assert types == ["system_init", "text", "usage"]
    assert all(r["thread_id"] == "discord:x:t1" for r in records)
    assert all(r["agent"] == "claude" for r in records)
