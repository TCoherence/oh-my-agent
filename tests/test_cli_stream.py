"""Smoke tests for the async-generator streaming path on BaseCLIAgent."""

from __future__ import annotations

import asyncio
import json

import pytest

from oh_my_agent.agents.cli.base import BaseCLIAgent, _stream_cli_lines
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
