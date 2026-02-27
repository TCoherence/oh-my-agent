import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from oh_my_agent.gateway.manager import GatewayManager
from oh_my_agent.gateway.base import IncomingMessage
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.automation import ScheduledJob


def _make_msg(thread_id=None, content="hello", author_id=None, system=False) -> IncomingMessage:
    return IncomingMessage(
        platform="discord",
        channel_id="100",
        thread_id=thread_id,
        author="alice",
        author_id=author_id,
        content=content,
        system=system,
    )


def _make_session(channel=None, registry=None) -> ChannelSession:
    if channel is None:
        channel = MagicMock()
        channel.platform = "discord"
        channel.channel_id = "100"
        channel.create_thread = AsyncMock(return_value="thread-1")
        channel.send = AsyncMock()
        channel.typing = MagicMock()
        channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
        channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)
    if registry is None:
        registry = MagicMock(spec=AgentRegistry)
    return ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )


def test_thread_name_truncates_at_90_chars():
    long = "a" * 200
    name = GatewayManager._thread_name(long)
    assert len(name) <= 94  # 90 + "..."
    assert name.endswith("...")


def test_thread_name_short_message_unchanged():
    name = GatewayManager._thread_name("short message")
    assert name == "short message"


def test_thread_name_uses_first_line_only():
    name = GatewayManager._thread_name("line one\nline two")
    assert name == "line one"


@pytest.mark.asyncio
async def test_handle_message_creates_thread_when_no_thread_id():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="new-thread")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_agent = MagicMock()
    mock_agent.name = "claude"
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="reply")))

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([])

    msg = _make_msg(thread_id=None, content="new question")
    await gm.handle_message(session, registry, msg)

    channel.create_thread.assert_called_once()
    channel.send.assert_called_once()
    sent_text = channel.send.call_args[0][1]
    assert "claude" in sent_text
    assert "reply" in sent_text


@pytest.mark.asyncio
async def test_handle_message_uses_existing_thread_id():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock()
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_agent = MagicMock()
    mock_agent.name = "claude"
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="hi")))

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([])

    msg = _make_msg(thread_id="existing-thread", content="follow up")
    await gm.handle_message(session, registry, msg)

    channel.create_thread.assert_not_called()
    call_args = channel.send.call_args[0]
    assert call_args[0] == "existing-thread"


@pytest.mark.asyncio
async def test_handle_message_error_response_sent_and_history_cleaned():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="t1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_agent = MagicMock()
    mock_agent.name = "claude"
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="", error="boom")))

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([])

    msg = _make_msg(thread_id=None, content="oops")
    await gm.handle_message(session, registry, msg)

    sent = channel.send.call_args[0][1]
    assert "Error" in sent
    assert "boom" in sent
    # History should be empty (failed turn was popped)
    assert await session.get_history("t1") == []


@pytest.mark.asyncio
async def test_handle_message_appends_to_history():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="t1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_agent = MagicMock()
    mock_agent.name = "claude"
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="answer")))

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([])

    msg = _make_msg(thread_id=None, content="question")
    await gm.handle_message(session, registry, msg)

    history = await session.get_history("t1")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
    assert history[1]["agent"] == "claude"


@pytest.mark.asyncio
async def test_owner_gate_ignores_unauthorized_user():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="t1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_agent = MagicMock()
    mock_agent.name = "claude"
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="answer")))

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], owner_user_ids={"42"})

    msg = _make_msg(thread_id=None, content="question", author_id="99")
    await gm.handle_message(session, registry, msg)

    registry.run.assert_not_called()
    channel.create_thread.assert_not_called()
    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_owner_gate_allows_system_messages():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="t1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_agent = MagicMock()
    mock_agent.name = "claude"
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="answer")))

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], owner_user_ids={"42"})

    msg = _make_msg(thread_id=None, content="scheduled", author_id=None, system=True)
    await gm.handle_message(session, registry, msg)

    registry.run.assert_called_once()
    channel.send.assert_called()


@pytest.mark.asyncio
async def test_scheduler_dispatch_defaults_to_channel_id_when_thread_missing():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="t1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_agent = MagicMock()
    mock_agent.name = "codex"
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="done")))

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([])
    gm._sessions["discord:100"] = session

    job = ScheduledJob(
        name="tick",
        platform="discord",
        channel_id="100",
        thread_id=None,
        prompt="run",
        interval_seconds=60,
    )
    await gm._dispatch_scheduled_job(job)

    channel.create_thread.assert_not_called()
    call_args = channel.send.call_args[0]
    assert call_args[0] == "100"


@pytest.mark.asyncio
async def test_scheduler_dispatch_dm_uses_dm_channel_id():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="t1")
    channel.ensure_dm_channel = AsyncMock(return_value="dm-42")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_agent = MagicMock()
    mock_agent.name = "codex"
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="done")))

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([])
    gm._sessions["discord:100"] = session

    job = ScheduledJob(
        name="dm",
        platform="discord",
        channel_id="100",
        delivery="dm",
        target_user_id="42",
        prompt="run",
        interval_seconds=60,
    )
    await gm._dispatch_scheduled_job(job)

    channel.ensure_dm_channel.assert_called_once_with("42")
    channel.create_thread.assert_not_called()
    call_args = channel.send.call_args[0]
    assert call_args[0] == "dm-42"


@pytest.mark.asyncio
async def test_scheduler_dispatch_dm_skips_when_channel_unsupported():
    channel = MagicMock()
    channel.platform = "slack"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="t1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_agent = MagicMock()
    mock_agent.name = "codex"
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="done")))

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([])
    gm._sessions["discord:100"] = session

    job = ScheduledJob(
        name="dm",
        platform="discord",
        channel_id="100",
        delivery="dm",
        target_user_id="42",
        prompt="run",
        interval_seconds=60,
    )
    await gm._dispatch_scheduled_job(job)

    registry.run.assert_not_called()
    channel.send.assert_not_called()
