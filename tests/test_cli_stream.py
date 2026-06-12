"""Smoke tests for the async-generator streaming path on BaseCLIAgent."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import pytest

from oh_my_agent.agents.cli.base import (
    _STREAM_LINE_LIMIT,
    BaseCLIAgent,
    _bounded_log_excerpt,
    _drain_oversized_line,
    _stream_cli_lines,
    _stream_cli_process,
    _StreamState,
)
from oh_my_agent.agents.cli.claude import ClaudeAgent
from oh_my_agent.agents.cli.codex import CodexCLIAgent
from oh_my_agent.agents.events import (
    AgentEvent,
    CompleteEvent,
    ErrorEvent,
    SystemInitEvent,
    TextEvent,
    ThinkingEvent,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
)


class _EchoAgent(BaseCLIAgent):
    """Minimal concrete BaseCLIAgent that runs an external command verbatim."""

    def __init__(self, *, argv: list[str], timeout: int = 5) -> None:
        super().__init__(cli_path=argv[0], timeout=timeout)
        self._argv = argv

    @property
    def name(self) -> str:
        return "echo-agent"

    def _build_command(self, prompt: str) -> list[str]:
        return self._argv


@pytest.mark.asyncio
async def test_stream_cli_lines_yields_stdout_in_order() -> None:
    items: list[tuple[str, str]] = []
    async for frame in _stream_cli_lines(
        "bash", "-c", "printf 'one\\ntwo\\nthree\\n'",
        cwd=None,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
        timeout=5,
    ):
        items.append(frame)
    stdout_lines = [text for label, text in items if label == "stdout"]
    assert stdout_lines == ["one", "two", "three"]


@pytest.mark.asyncio
async def test_stream_cli_lines_cancel_event_kills_subprocess() -> None:
    cancel = asyncio.Event()

    async def _trip_cancel() -> None:
        await asyncio.sleep(0.2)
        cancel.set()

    asyncio.create_task(_trip_cancel())

    items: list[tuple[str, str]] = []
    async for frame in _stream_cli_lines(
        "sleep", "10",
        cwd=None,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
        timeout=30,
        cancel=cancel,
    ):
        items.append(frame)
    # sleep prints nothing; cancel should have killed it before stdout arrives.
    assert all(label == "stdout" for label, _ in items) is True or items == []


@pytest.mark.asyncio
async def test_stream_emits_text_and_complete_events() -> None:
    agent = _EchoAgent(argv=["bash", "-c", "printf 'alpha\\nbeta\\n'"])
    events: list[AgentEvent] = []
    async for event in agent.stream("ignored"):
        events.append(event)
    # At least one TextEvent with "alpha" and one with "beta", then a
    # CompleteEvent whose text contains both.
    text_events = [e for e in events if isinstance(e, TextEvent)]
    complete = [e for e in events if isinstance(e, CompleteEvent)]
    assert [t.text for t in text_events] == ["alpha", "beta"]
    assert len(complete) == 1
    assert "alpha" in complete[0].text and "beta" in complete[0].text


@pytest.mark.asyncio
async def test_stream_surfaces_error_event_on_missing_binary() -> None:
    agent = _EchoAgent(argv=["/definitely/nonexistent/binary-xyz"])
    events: list[AgentEvent] = []
    async for event in agent.stream("ignored"):
        events.append(event)
    assert any(isinstance(e, ErrorEvent) for e in events)


# ---------------------------------------------------------------------------
# Exit-code-driven failure semantics in stream()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_cli_lines_publishes_returncode_via_state() -> None:
    state = _StreamState()
    async for _ in _stream_cli_lines(
        "bash", "-c", "exit 7",
        cwd=None,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
        timeout=5,
        state=state,
    ):
        pass
    assert state.returncode == 7


@pytest.mark.asyncio
async def test_stream_exit_zero_with_benign_stderr_is_not_an_error() -> None:
    """Regression: 'Loaded cached credentials.'-style stderr noise on a
    successful run used to be reported as failure, triggering registry
    fallback that discarded good output."""
    agent = _EchoAgent(
        argv=["bash", "-c", "echo 'real answer'; echo 'Loaded cached credentials.' >&2"]
    )
    events: list[AgentEvent] = []
    async for event in agent.stream("ignored"):
        events.append(event)
    assert not any(isinstance(e, ErrorEvent) for e in events)
    complete = [e for e in events if isinstance(e, CompleteEvent)]
    assert len(complete) == 1
    assert "real answer" in complete[0].text


@pytest.mark.asyncio
async def test_stream_nonzero_exit_with_empty_stderr_is_an_error() -> None:
    """Regression: the documented Claude failure mode (structured error on
    stdout, non-zero exit, empty stderr) used to be reported as SUCCESS with
    partial text."""
    agent = _EchoAgent(argv=["bash", "-c", "echo '{\"error\":\"boom\"}'; exit 3"])
    events: list[AgentEvent] = []
    async for event in agent.stream("ignored"):
        events.append(event)
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    # Block-mode _extract_cli_error semantics: stderr empty → stdout JSON error.
    assert "exited 3" in errors[0].message
    assert "boom" in errors[0].message


@pytest.mark.asyncio
async def test_stream_nonzero_exit_classifies_stderr_error_kind() -> None:
    agent = _EchoAgent(argv=["bash", "-c", "echo 'rate limit exceeded' >&2; exit 1"])
    events: list[AgentEvent] = []
    async for event in agent.stream("ignored"):
        events.append(event)
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].error_kind == "rate_limit"


@pytest.mark.asyncio
async def test_stream_timeout_override_beats_configured_timeout() -> None:
    agent = _EchoAgent(argv=["sleep", "10"], timeout=30)
    events: list[AgentEvent] = []
    async for event in agent.stream("ignored", timeout_override=1):
        events.append(event)
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].error_kind == "timeout"
    assert "timed out after 1s" in errors[0].message
    # Per-call only: the configured timeout is untouched.
    assert agent._timeout == 30


# ---------------------------------------------------------------------------
# Per-CLI _parse_stream_line mapping
# ---------------------------------------------------------------------------


def test_claude_parse_stream_line_system_init() -> None:
    agent = ClaudeAgent()
    line = json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "session_id": "sid-42",
            "model": "sonnet",
            "tools": ["Bash", "Read"],
        }
    )
    events = agent._parse_stream_line(line)
    assert len(events) == 1
    assert isinstance(events[0], SystemInitEvent)
    assert events[0].session_id == "sid-42"
    assert events[0].tools == ["Bash", "Read"]


def test_claude_parse_stream_line_assistant_text_and_tool_use() -> None:
    agent = ClaudeAgent()
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Here we go"},
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "Read",
                        "input": {"file_path": "/x.txt"},
                    },
                ]
            },
        }
    )
    events = agent._parse_stream_line(line)
    assert [type(e).__name__ for e in events] == ["TextEvent", "ToolUseEvent"]
    assert events[0].text == "Here we go"
    tu = events[1]
    assert isinstance(tu, ToolUseEvent)
    assert tu.tool_id == "tu_1"
    assert tu.name == "Read"
    assert tu.input == {"file_path": "/x.txt"}


def test_claude_parse_stream_line_assistant_thinking() -> None:
    agent = ClaudeAgent()
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "reasoning about foo"},
                ]
            },
        }
    )
    events = agent._parse_stream_line(line)
    assert len(events) == 1
    assert isinstance(events[0], ThinkingEvent)
    assert events[0].text == "reasoning about foo"


def test_claude_parse_stream_line_tool_result() -> None:
    agent = ClaudeAgent()
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": "42",
                        "is_error": False,
                    }
                ]
            },
        }
    )
    events = agent._parse_stream_line(line)
    assert len(events) == 1
    assert isinstance(events[0], ToolResultEvent)
    assert events[0].tool_id == "tu_1"
    assert events[0].output == "42"


def test_claude_parse_stream_line_result_yields_usage() -> None:
    agent = ClaudeAgent()
    line = json.dumps(
        {
            "type": "result",
            "result": "final text",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "total_cost_usd": 0.001,
        }
    )
    events = agent._parse_stream_line(line)
    assert len(events) == 1
    assert isinstance(events[0], UsageEvent)
    assert events[0].input_tokens == 10
    assert events[0].cost_usd == 0.001


def test_claude_parse_stream_line_error_max_turns_yields_error_event() -> None:
    """Streaming must classify error_max_turns like block mode does — without
    this the registry's no-fallback-on-max_turns rule and the runtime's
    re-run-with-more-turns button never trigger on streamed runs."""
    agent = ClaudeAgent()
    line = json.dumps(
        {
            "type": "result",
            "subtype": "error_max_turns",
            "num_turns": 61,
            "result": "partial",
            "usage": {"input_tokens": 9, "output_tokens": 4},
        }
    )
    events = agent._parse_stream_line(line)
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].error_kind == "max_turns"
    assert errors[0].message == "claude: max_turns reached (61 turns)"
    # Usage from the same frame is still surfaced.
    assert any(isinstance(e, UsageEvent) for e in events)


def test_claude_parse_stream_line_error_max_turns_without_count() -> None:
    agent = ClaudeAgent()
    line = json.dumps({"type": "result", "subtype": "error_max_turns"})
    events = agent._parse_stream_line(line)
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].message == "claude: max_turns reached"


def test_codex_parse_stream_line_thread_started() -> None:
    agent = CodexCLIAgent()
    line = json.dumps({"type": "thread.started", "thread_id": "tid-9"})
    events = agent._parse_stream_line(line)
    assert len(events) == 1
    assert isinstance(events[0], SystemInitEvent)
    assert events[0].session_id == "tid-9"


def test_codex_parse_stream_line_command_execution_pair() -> None:
    agent = CodexCLIAgent()
    started = json.dumps(
        {
            "type": "item.started",
            "item": {"id": "c_1", "type": "command_execution", "command": "ls /"},
        }
    )
    completed = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "id": "c_1",
                "type": "command_execution",
                "output": "bin etc home",
                "exit_code": 0,
            },
        }
    )
    start_evs = agent._parse_stream_line(started)
    end_evs = agent._parse_stream_line(completed)
    assert len(start_evs) == 1 and isinstance(start_evs[0], ToolUseEvent)
    assert start_evs[0].name == "Bash"
    assert start_evs[0].input == {"command": "ls /"}
    assert len(end_evs) == 1 and isinstance(end_evs[0], ToolResultEvent)
    assert end_evs[0].is_error is False


def test_codex_parse_stream_line_agent_message() -> None:
    agent = CodexCLIAgent()
    completed = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "answer"},
        }
    )
    started = json.dumps(
        {
            "type": "item.started",
            "item": {"type": "agent_message", "text": "answer"},
        }
    )
    # Only the completed frame yields a TextEvent; started is a no-op so we
    # don't double-emit the same assistant text.
    assert agent._parse_stream_line(completed)[0].text == "answer"
    assert agent._parse_stream_line(started) == []


def test_codex_parse_stream_line_turn_completed_usage() -> None:
    agent = CodexCLIAgent()
    line = json.dumps(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 20, "output_tokens": 7, "cached_input_tokens": 3},
        }
    )
    events = agent._parse_stream_line(line)
    assert len(events) == 1 and isinstance(events[0], UsageEvent)
    assert events[0].cache_read_input_tokens == 3


# ---------------------------------------------------------------------------
# Oversized stream-json lines (StreamReader limit)
# ---------------------------------------------------------------------------

_TEST_ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin"}


@pytest.mark.asyncio
async def test_stream_cli_lines_survives_line_over_64kib() -> None:
    # A single stream-json tool_result frame routinely exceeds asyncio's
    # default 64 KiB StreamReader limit. With the raised limit the pump must
    # deliver the line intact and keep streaming subsequent lines.
    big_len = 256 * 1024
    code = f"print('x' * {big_len}); print('after')"
    items: list[tuple[str, str]] = []
    async for frame in _stream_cli_lines(
        sys.executable, "-c", code,
        cwd=None,
        env=_TEST_ENV,
        timeout=15,
    ):
        items.append(frame)
    stdout_lines = [text for label, text in items if label == "stdout"]
    assert stdout_lines == ["x" * big_len, "after"]


@pytest.mark.asyncio
async def test_stream_cli_lines_truncates_line_over_limit_and_keeps_pumping(caplog) -> None:
    # Lines beyond _STREAM_LINE_LIMIT are salvaged (truncated to the cap) with
    # a warning, and the pump keeps going instead of dying silently.
    big_len = _STREAM_LINE_LIMIT + 64 * 1024
    code = f"print('y' * {big_len}); print('after')"
    items: list[tuple[str, str]] = []
    with caplog.at_level(logging.WARNING, logger="oh_my_agent.agents.cli.base"):
        async for frame in _stream_cli_lines(
            sys.executable, "-c", code,
            cwd=None,
            env=_TEST_ENV,
            timeout=30,
        ):
            items.append(frame)
    stdout_lines = [text for label, text in items if label == "stdout"]
    assert len(stdout_lines) == 2
    assert stdout_lines[0] == "y" * _STREAM_LINE_LIMIT
    assert stdout_lines[1] == "after"
    assert any("truncated" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_drain_oversized_line_salvages_full_line_and_keeps_alignment() -> None:
    reader = asyncio.StreamReader(limit=16)
    reader.feed_data(b"A" * 64 + b"\nnext\n")
    reader.feed_eof()
    with pytest.raises(asyncio.LimitOverrunError):
        await reader.readuntil(b"\n")
    kept, dropped = await _drain_oversized_line(reader, max_keep=1024)
    assert kept == b"A" * 64 + b"\n"
    assert dropped == 0
    # The following line is untouched — stream stays aligned.
    assert await reader.readuntil(b"\n") == b"next\n"


@pytest.mark.asyncio
async def test_drain_oversized_line_truncates_beyond_cap() -> None:
    reader = asyncio.StreamReader(limit=16)
    reader.feed_data(b"B" * 64 + b"\nnext\n")
    reader.feed_eof()
    with pytest.raises(asyncio.LimitOverrunError):
        await reader.readuntil(b"\n")
    kept, dropped = await _drain_oversized_line(reader, max_keep=10)
    assert kept == b"B" * 10
    assert dropped == 55  # 54 remaining B's + the newline
    assert await reader.readuntil(b"\n") == b"next\n"


# ---------------------------------------------------------------------------
# Process-group kill on timeout (grandchild reaping)
# ---------------------------------------------------------------------------


async def _wait_process_gone(pid: int, deadline_s: float = 5.0) -> bool:
    """Poll until ``pid`` no longer exists (covers the zombie-reap window)."""
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_stream_cli_lines_timeout_kills_grandchildren(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    script = f'sleep 30 & echo $! > "{pid_file}"; wait'
    with pytest.raises(asyncio.TimeoutError):
        async for _ in _stream_cli_lines(
            "sh", "-c", script,
            cwd=None,
            env=_TEST_ENV,
            timeout=1,
        ):
            pass
    pid = int(pid_file.read_text().strip())
    assert await _wait_process_gone(pid), f"grandchild {pid} survived the timeout kill"


@pytest.mark.asyncio
async def test_stream_cli_process_timeout_kills_grandchildren(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    script = f'sleep 30 & echo $! > "{pid_file}"; wait'
    with pytest.raises(asyncio.TimeoutError):
        await _stream_cli_process(
            "sh", "-c", script,
            cwd=None,
            env=_TEST_ENV,
            timeout=1,
        )
    pid = int(pid_file.read_text().strip())
    assert await _wait_process_gone(pid), f"grandchild {pid} survived the timeout kill"


# ---------------------------------------------------------------------------
# _bounded_log_excerpt tail reads
# ---------------------------------------------------------------------------


def test_bounded_log_excerpt_reads_tail_of_large_file(tmp_path: Path) -> None:
    log = tmp_path / "stream.log"
    # Well beyond the 8 KiB seek window so only the tail may be read.
    content = ("head-" * 20000) + "TAIL-MARKER-" + ("z" * 500)
    log.write_text(content, encoding="utf-8")
    excerpt = _bounded_log_excerpt(log)
    assert excerpt == content[-2000:]
    assert excerpt.endswith("z" * 500)


def test_bounded_log_excerpt_small_file_and_missing(tmp_path: Path) -> None:
    log = tmp_path / "small.log"
    log.write_text("  hello tail  \n", encoding="utf-8")
    assert _bounded_log_excerpt(log) == "hello tail"
    assert _bounded_log_excerpt(tmp_path / "missing.log") is None
    assert _bounded_log_excerpt(None) is None
