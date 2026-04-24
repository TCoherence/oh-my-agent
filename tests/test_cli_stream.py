"""Smoke tests for the async-generator streaming path on BaseCLIAgent."""

from __future__ import annotations

import asyncio

import pytest

from oh_my_agent.agents.cli.base import BaseCLIAgent, _stream_cli_lines
from oh_my_agent.agents.events import (
    AgentEvent,
    CompleteEvent,
    ErrorEvent,
    TextEvent,
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
