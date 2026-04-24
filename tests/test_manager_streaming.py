"""Gateway-level integration tests for the streaming relay.

These exercise the ``gateway.streaming.enabled`` branch of
``GatewayManager.handle_message``. We use lightweight fake channel/agent
fixtures (no Discord) so the tests stay fast and hermetic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.gateway.base import IncomingMessage
from oh_my_agent.gateway.manager import GatewayManager
from oh_my_agent.gateway.session import ChannelSession


def _msg(thread_id="t1", content="hi") -> IncomingMessage:
    return IncomingMessage(
        platform="discord",
        channel_id="100",
        thread_id=thread_id,
        author="alice",
        author_id="u-1",
        content=content,
    )


class _FakeChannel:
    """Minimal channel stub that records send/edit calls."""

    platform = "discord"
    channel_id = "100"
    supports_streaming_edit = True

    def __init__(self) -> None:
        self.sends: list[tuple[str, str]] = []  # (thread_id, text)
        self.edits: list[tuple[str, str, str]] = []  # (thread_id, msg_id, text)
        self._msg_counter = 0

    async def send(self, thread_id: str, text: str) -> str:
        self._msg_counter += 1
        mid = f"m{self._msg_counter}"
        self.sends.append((thread_id, text))
        return mid

    async def edit_message(self, thread_id: str, message_id: str, text: str) -> None:
        self.edits.append((thread_id, message_id, text))

    async def create_thread(self, *args, **kwargs):
        return "t1"

    def typing(self, thread_id: str):
        class _Ctx:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()

    async def stop(self) -> None:
        return None


class _NoEditChannel(_FakeChannel):
    """Channel that claims no streaming-edit support."""

    supports_streaming_edit = False


def _session(channel) -> ChannelSession:
    registry = MagicMock(spec=AgentRegistry)
    return ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )


def _registry_with_partial(text: str, usage: dict | None = None, partial_chunks: list[str] | None = None) -> tuple[AgentRegistry, MagicMock]:
    """Return a mock registry whose run() replays partial text + final text."""
    agent = MagicMock()
    agent.name = "claude"

    reg = MagicMock(spec=AgentRegistry)
    reg.agents = [agent]

    async def _fake_run(*args, **kwargs):
        on_partial = kwargs.get("on_partial")
        if on_partial is not None and partial_chunks:
            for chunk in partial_chunks:
                await on_partial(chunk)
        return (agent, AgentResponse(text=text, usage=usage))

    reg.run = AsyncMock(side_effect=_fake_run)
    return reg, agent


@pytest.mark.asyncio
async def test_streaming_disabled_uses_send_path():
    channel = _FakeChannel()
    reg, _ = _registry_with_partial(text="final reply")
    session = _session(channel)

    gm = GatewayManager([], streaming_config={"enabled": False})
    await gm.handle_message(session, reg, _msg())

    # Streaming off → single send with attribution + reply body, no edits.
    assert channel.edits == []
    assert len(channel.sends) == 1
    _, body = channel.sends[0]
    assert "claude" in body
    assert "final reply" in body


@pytest.mark.asyncio
async def test_streaming_enabled_starts_anchor_and_edits_on_finalize():
    channel = _FakeChannel()
    reg, _ = _registry_with_partial(
        text="the full reply",
        partial_chunks=["partial 1", "partial 1\npartial 2"],
    )
    session = _session(channel)

    gm = GatewayManager(
        [],
        streaming_config={"enabled": True, "min_edit_interval_ms": 0},
    )
    await gm.handle_message(session, reg, _msg())

    # Placeholder sent once at start; no further sends (body fits in one msg).
    assert len(channel.sends) == 1
    assert "thinking" in channel.sends[0][1] or "⏳" in channel.sends[0][1]

    # At least one edit: the final body with the real text.
    assert channel.edits, "expected at least one edit_message call"
    _, _, last_body = channel.edits[-1]
    assert "the full reply" in last_body
    assert "claude" in last_body


@pytest.mark.asyncio
async def test_streaming_enabled_but_channel_unsupported_falls_back_to_send():
    channel = _NoEditChannel()
    reg, _ = _registry_with_partial(text="reply", partial_chunks=["p1"])
    session = _session(channel)

    gm = GatewayManager(
        [],
        streaming_config={"enabled": True, "min_edit_interval_ms": 0},
    )
    await gm.handle_message(session, reg, _msg())

    # Unsupported channel → behaves like streaming off.
    assert channel.edits == []
    assert len(channel.sends) == 1
    assert "reply" in channel.sends[0][1]


@pytest.mark.asyncio
async def test_streaming_error_path_edits_anchor_with_failure_text():
    channel = _FakeChannel()

    agent = MagicMock()
    agent.name = "claude"
    reg = MagicMock(spec=AgentRegistry)
    reg.agents = [agent]
    reg.run = AsyncMock(
        return_value=(agent, AgentResponse(text="", error="boom", error_kind="cli_error"))
    )

    session = _session(channel)
    gm = GatewayManager([], streaming_config={"enabled": True, "min_edit_interval_ms": 0})
    await gm.handle_message(session, reg, _msg())

    # Placeholder sent; final edit is the user-safe error banner.
    assert len(channel.sends) == 1
    assert channel.edits, "error path should edit the anchor, not send a new msg"
    _, _, last_body = channel.edits[-1]
    assert "claude" in last_body
    assert "error" in last_body.lower()


@pytest.mark.asyncio
async def test_streaming_overflow_spills_to_followup_sends():
    channel = _FakeChannel()
    # Build a reply that exceeds one Discord message (2000 char cap).
    huge = "x" * 4500
    reg, _ = _registry_with_partial(text=huge)
    session = _session(channel)

    gm = GatewayManager(
        [],
        streaming_config={"enabled": True, "min_edit_interval_ms": 0},
    )
    await gm.handle_message(session, reg, _msg())

    # One anchor edit + follow-up sends for overflow chunks.
    assert channel.edits, "expected at least one anchor edit"
    assert len(channel.sends) >= 2, (
        f"expected overflow sends, got {len(channel.sends)}: {[s[1][:20] for s in channel.sends]}"
    )


def test_streaming_min_edit_interval_ms_is_floored():
    """A misconfigured ``min_edit_interval_ms: 200`` would risk Discord 429s
    (5 edits / 5s per message). The config parser clamps it up to 500ms."""
    # Below floor — should be lifted to 0.5s.
    gm_low = GatewayManager(
        [],
        streaming_config={"enabled": True, "min_edit_interval_ms": 100},
    )
    assert gm_low._streaming_min_edit_interval == 0.5

    # Above floor — should be preserved.
    gm_ok = GatewayManager(
        [],
        streaming_config={"enabled": True, "min_edit_interval_ms": 1500},
    )
    assert gm_ok._streaming_min_edit_interval == 1.5

    # Explicit zero stays zero (opt-in to no throttle, e.g. for tests).
    gm_zero = GatewayManager(
        [],
        streaming_config={"enabled": True, "min_edit_interval_ms": 0},
    )
    assert gm_zero._streaming_min_edit_interval == 0.0


@pytest.mark.asyncio
async def test_streaming_threads_on_partial_through_registry():
    channel = _FakeChannel()
    reg, _ = _registry_with_partial(text="done", partial_chunks=["a", "ab"])
    session = _session(channel)

    gm = GatewayManager(
        [],
        streaming_config={"enabled": True, "min_edit_interval_ms": 0},
    )
    await gm.handle_message(session, reg, _msg())

    # registry.run should have been called with on_partial kwarg present.
    kwargs = reg.run.await_args.kwargs
    assert "on_partial" in kwargs
    assert callable(kwargs["on_partial"])


@pytest.mark.asyncio
async def test_streaming_threads_on_tool_use_through_registry():
    channel = _FakeChannel()
    reg, _ = _registry_with_partial(text="done")
    session = _session(channel)

    gm = GatewayManager(
        [],
        streaming_config={"enabled": True, "min_edit_interval_ms": 0},
    )
    await gm.handle_message(session, reg, _msg())

    # registry.run should also receive the tool-use hook.
    kwargs = reg.run.await_args.kwargs
    assert "on_tool_use" in kwargs
    assert callable(kwargs["on_tool_use"])


@pytest.mark.asyncio
async def test_streaming_disabled_does_not_pass_on_tool_use():
    channel = _FakeChannel()
    reg, _ = _registry_with_partial(text="done")
    session = _session(channel)

    gm = GatewayManager([], streaming_config={"enabled": False})
    await gm.handle_message(session, reg, _msg())

    kwargs = reg.run.await_args.kwargs
    # Either absent or explicitly None — both are acceptable ways of opting out.
    assert kwargs.get("on_tool_use") is None
