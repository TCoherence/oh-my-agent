import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from oh_my_agent.gateway.manager import GatewayManager
from oh_my_agent.gateway.base import IncomingMessage
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.automation import ScheduledJob
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.gateway.router import RouteDecision


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


@pytest.mark.asyncio
async def test_handle_message_uses_short_workspace_override(tmp_path):
    base = tmp_path / "base-workspace"
    base.mkdir(parents=True, exist_ok=True)
    (base / "AGENTS.md").write_text("# workspace agents\n", encoding="utf-8")
    (base / ".claude").mkdir(exist_ok=True)
    (base / ".gemini").mkdir(exist_ok=True)
    (base / ".codex").mkdir(exist_ok=True)

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
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="answer")))

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager(
        [],
        short_workspace={
            "enabled": True,
            "ttl_hours": 24,
            "cleanup_interval_minutes": 1440,
            "root": str(tmp_path / "sessions"),
            "base_workspace": str(base),
        },
    )

    msg = _make_msg(thread_id="t1", content="hello")
    await gm.handle_message(session, registry, msg)

    assert registry.run.call_count == 1
    kwargs = registry.run.call_args.kwargs
    ws = kwargs.get("workspace_override")
    assert isinstance(ws, Path)
    assert ws.parent == (tmp_path / "sessions")
    assert (ws / "AGENTS.md").exists()
    assert (ws / ".codex").exists()


@pytest.mark.asyncio
async def test_short_workspace_cleanup_uses_db_ttl(tmp_path):
    base = tmp_path / "base-workspace"
    base.mkdir(parents=True, exist_ok=True)
    (base / "AGENTS.md").write_text("# workspace agents\n", encoding="utf-8")

    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="t1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    registry = MagicMock(spec=AgentRegistry)
    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager(
        [],
        short_workspace={
            "enabled": True,
            "ttl_hours": 24,
            "cleanup_interval_minutes": 1440,
            "root": str(tmp_path / "sessions"),
            "base_workspace": str(base),
        },
    )

    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.init()
    gm.set_memory_store(store)

    ws = await gm._resolve_short_workspace(session, "t-clean")
    assert ws is not None and ws.exists()

    db = await store._conn()  # noqa: SLF001
    await db.execute(
        "UPDATE ephemeral_workspaces SET last_used_at='2000-01-01 00:00:00' "
        "WHERE workspace_key=?",
        (gm._short_workspace_key("discord", "100", "t-clean"),),
    )
    await db.commit()

    cleaned = await gm._cleanup_expired_short_workspaces()
    assert cleaned == 1
    assert not ws.exists()

    await store.close()


@pytest.mark.asyncio
async def test_router_propose_task_creates_runtime_draft_and_skips_reply():
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
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="answer")))

    runtime = MagicMock()
    runtime.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime.maybe_handle_incoming = AsyncMock(return_value=False)
    runtime.create_repo_change_task = AsyncMock()

    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision="propose_repo_task",
            confidence=0.91,
            goal="create a new skill and validate it",
            risk_hints=[],
            raw_text="{}",
            task_type="repo_change",
            completion_mode="merge",
        )
    )

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], runtime_service=runtime, intent_router=router)
    msg = _make_msg(thread_id="t1", content="帮我创建一个skill并验证")
    await gm.handle_message(session, registry, msg)

    runtime.create_repo_change_task.assert_called_once()
    kwargs = runtime.create_repo_change_task.call_args.kwargs
    assert kwargs["source"] == "router"
    assert kwargs["force_draft"] is True
    assert kwargs["raw_request"] == "帮我创建一个skill并验证"
    registry.run.assert_not_called()


@pytest.mark.asyncio
async def test_router_propose_artifact_task_creates_artifact_runtime_draft():
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
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="answer")))

    runtime = MagicMock()
    runtime.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime.maybe_handle_incoming = AsyncMock(return_value=False)
    runtime.create_artifact_task = AsyncMock()

    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision="propose_artifact_task",
            confidence=0.91,
            goal="Generate a markdown daily news brief",
            risk_hints=[],
            raw_text="{}",
            task_type="artifact",
            completion_mode="reply",
        )
    )

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], runtime_service=runtime, intent_router=router)
    msg = _make_msg(thread_id="t1", content="帮我生成一份今日新闻速读 markdown")
    await gm.handle_message(session, registry, msg)

    runtime.create_artifact_task.assert_called_once()
    kwargs = runtime.create_artifact_task.call_args.kwargs
    assert kwargs["source"] == "router"
    assert kwargs["force_draft"] is True
    assert kwargs["raw_request"] == "帮我生成一份今日新闻速读 markdown"
    registry.run.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_skill_invocation_bypasses_router_and_runtime(tmp_path):
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
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="news reply")))

    runtime = MagicMock()
    runtime.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime.maybe_handle_incoming = AsyncMock(return_value=False)
    runtime.create_task = AsyncMock()
    runtime.create_skill_task = AsyncMock()

    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock()

    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "top-5-daily-news"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("name: top-5-daily-news\n", encoding="utf-8")
    syncer = MagicMock()
    syncer._skills_path = skills_root  # noqa: SLF001

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager(
        [],
        runtime_service=runtime,
        intent_router=router,
        skill_syncer=syncer,
    )

    msg = _make_msg(thread_id="thread-1", content="/top-5-daily-news")
    msg.preferred_agent = "claude"
    await gm.handle_message(session, registry, msg)

    router.route.assert_not_called()
    runtime.create_task.assert_not_called()
    runtime.create_skill_task.assert_not_called()
    runtime.maybe_handle_incoming.assert_not_called()
    registry.run.assert_awaited_once()
    assert registry.run.call_args.args[0] == "/top-5-daily-news"
    assert registry.run.call_args.kwargs["force_agent"] == "claude"
    assert channel.send.await_count >= 1
