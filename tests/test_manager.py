import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from oh_my_agent.agents.base import AgentResponse, BaseAgent
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.automation import ScheduledJob
from oh_my_agent.gateway.base import IncomingMessage
from oh_my_agent.gateway.manager import GatewayManager
from oh_my_agent.gateway.router import RouteDecision
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.skills.skill_sync import SkillSync
from oh_my_agent.utils.errors import (
    USER_MSG_AGENT_CRASH,
    USER_MSG_INTERNAL,
    USER_MSG_STORE_FAILURE,
)


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
        channel.stop = AsyncMock()
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


class _ResumeClearingAgent(BaseAgent):
    def __init__(self, name: str = "codex") -> None:
        self._name = name
        self._session_ids: dict[str, str] = {}

    @property
    def name(self) -> str:
        return self._name

    def get_session_id(self, thread_id: str) -> str | None:
        return self._session_ids.get(thread_id)

    def set_session_id(self, thread_id: str, session_id: str) -> None:
        self._session_ids[thread_id] = session_id

    def clear_session(self, thread_id: str) -> None:
        self._session_ids.pop(thread_id, None)

    async def run(self, prompt, history=None, *, thread_id=None, workspace_override=None, log_path=None):
        if thread_id and self.get_session_id(thread_id):
            self.clear_session(thread_id)
        return AgentResponse(text="", error="session not found")


class _ThreadAwareOKAgent(BaseAgent):
    def __init__(self, name: str = "claude", response: str = "ok") -> None:
        self._name = name
        self._response = response

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt, history=None, *, thread_id=None, workspace_override=None, log_path=None):
        return AgentResponse(text=self._response)


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
async def test_handle_message_logs_direct_reply_purpose(caplog):
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
    registry.agents = [mock_agent]
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="hi")))

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([])

    with caplog.at_level("INFO"):
        await gm.handle_message(session, registry, _make_msg(thread_id="existing-thread", content="follow up"))

    assert "AGENT starting purpose=direct_reply" in caplog.text
    assert "AGENT_OK purpose=direct_reply agent=claude" in caplog.text


@pytest.mark.asyncio
async def test_handle_message_logs_explicit_skill_purpose(caplog, tmp_path):
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
    registry.agents = [mock_agent]
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="reply")))

    runtime = MagicMock()
    runtime.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime.maybe_handle_incoming = AsyncMock(return_value=False)
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

    with caplog.at_level("INFO"):
        await gm.handle_message(session, registry, msg)

    assert "AGENT starting purpose=explicit_skill preferred_agent='claude'" in caplog.text
    assert "AGENT_OK purpose=explicit_skill agent=claude" in caplog.text


@pytest.mark.asyncio
async def test_handle_message_injects_judge_store_relevant(tmp_path):
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
    registry.agents = [mock_agent]
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="reply")))

    session = _make_session(channel=channel, registry=registry)
    judge_store = MagicMock()
    judge_store.get_relevant = MagicMock(return_value=[])
    judge_store.get_active = MagicMock(return_value=[])

    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "top-5-daily-news"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("name: top-5-daily-news\n", encoding="utf-8")
    syncer = MagicMock()
    syncer._skills_path = skills_root  # noqa: SLF001

    gm = GatewayManager(
        [],
        judge_store=judge_store,
        repo_root=tmp_path,
        skill_syncer=syncer,
    )

    msg = _make_msg(thread_id="thread-1", content="/top-5-daily-news tell me the news")
    await gm.handle_message(session, registry, msg)

    kwargs = judge_store.get_relevant.call_args.kwargs
    assert kwargs["skill_name"] == "top-5-daily-news"
    assert kwargs["thread_id"] == "thread-1"
    assert kwargs["workspace"] == str(tmp_path)


@pytest.mark.asyncio
async def test_handle_message_passes_log_path_for_chat_runs(tmp_path):
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock()
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_agent = MagicMock()
    mock_agent.name = "codex"
    registry = MagicMock(spec=AgentRegistry)
    registry.agents = [mock_agent]
    async def _run(*args, **kwargs):
        callback = kwargs.get("on_agent_run")
        if callback is not None:
            await callback(
                agent=mock_agent,
                response=AgentResponse(text="hi"),
                log_path=tmp_path / "chat-thread-codex.log",
                duration_s=0.25,
            )
        return mock_agent, AgentResponse(text="hi")

    registry.run = AsyncMock(side_effect=_run)

    runtime = MagicMock()
    runtime.chat_agent_log_base_path.return_value = tmp_path / "chat-thread.log"
    runtime.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime.maybe_handle_incoming = AsyncMock(return_value=False)
    runtime.record_thread_agent_run = AsyncMock()

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], runtime_service=runtime)

    await gm.handle_message(session, registry, _make_msg(thread_id="thread-1", content="follow up"))

    assert registry.run.await_args.kwargs["log_path"] == tmp_path / "chat-thread.log"
    runtime.record_thread_agent_run.assert_awaited_once()
    assert runtime.record_thread_agent_run.await_args.kwargs["mode"] == "chat"


@pytest.mark.asyncio
async def test_handle_message_intercepts_auth_control_frame():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock()
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_agent = MagicMock()
    mock_agent.name = "codex"
    mock_agent.get_session_id.return_value = "sess-123"
    registry = MagicMock(spec=AgentRegistry)
    registry.agents = [mock_agent]
    registry.run = AsyncMock(
        return_value=(
            mock_agent,
            AgentResponse(
                text=(
                    "我先按 bilibili-video-summary 流程检查这个链接的字幕提取情况。\n\n"
                    '<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"auth_required",'
                    '"provider":"bilibili","reason":"login_required"}}</OMA_CONTROL>'
                )
            ),
        )
    )

    runtime = MagicMock()
    runtime.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime.maybe_handle_incoming = AsyncMock(return_value=False)
    runtime.mark_thread_auth_required = AsyncMock(return_value="Thread `thread-1` is waiting for `bilibili` login.")

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], runtime_service=runtime)

    await gm.handle_message(session, registry, _make_msg(thread_id="thread-1", content="follow up", author_id="owner-1"))

    runtime.mark_thread_auth_required.assert_awaited_once()
    assert channel.send.await_args_list[-1].args[1] == "Thread `thread-1` is waiting for `bilibili` login."
    first_message = channel.send.await_args_list[0].args[1]
    assert first_message.startswith("-# via **codex**\n我先按 bilibili-video-summary 流程检查这个链接的字幕提取情况。")
    history = await session.get_history("thread-1")
    assert [turn["role"] for turn in history] == ["user", "assistant"]
    assert history[-1]["content"] == "我先按 bilibili-video-summary 流程检查这个链接的字幕提取情况。"


@pytest.mark.asyncio
async def test_handle_message_intercepts_ask_user_control_frame():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock()
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_agent = MagicMock()
    mock_agent.name = "codex"
    mock_agent.get_session_id.return_value = "sess-123"
    registry = MagicMock(spec=AgentRegistry)
    registry.agents = [mock_agent]
    registry.run = AsyncMock(
        return_value=(
            mock_agent,
            AgentResponse(
                text=(
                    "我需要你先决定今天要看哪一条线。\n\n"
                    '<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"ask_user",'
                    '"question":"今天先跑哪份报告？","details":"单选即可。","choices":['
                    '{"id":"politics","label":"Politics daily","description":"关注地缘政治"},'
                    '{"id":"finance","label":"Finance daily","description":"关注财报和政策"}'
                    ']}}</OMA_CONTROL>'
                )
            ),
        )
    )

    runtime = MagicMock()
    runtime.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime.maybe_handle_incoming = AsyncMock(return_value=False)
    runtime.mark_thread_ask_user_required = AsyncMock(return_value="Thread `thread-1` is waiting for input.")

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], runtime_service=runtime)

    await gm.handle_message(session, registry, _make_msg(thread_id="thread-1", content="follow up", author_id="owner-1"))

    runtime.mark_thread_ask_user_required.assert_awaited_once()
    assert channel.send.await_count == 1
    first_message = channel.send.await_args_list[0].args[1]
    assert first_message.startswith("-# via **codex**\n我需要你先决定今天要看哪一条线。")
    history = await session.get_history("thread-1")
    assert [turn["role"] for turn in history] == ["user", "assistant"]
    assert history[-1]["content"] == "我需要你先决定今天要看哪一条线。"


@pytest.mark.asyncio
async def test_handle_message_logs_error_purpose(caplog):
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
    registry.agents = [mock_agent]
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="", error="boom")))

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([])

    with caplog.at_level("INFO"):
        await gm.handle_message(session, registry, _make_msg(thread_id="t1", content="oops"))

    assert "AGENT starting purpose=direct_reply" in caplog.text
    assert "AGENT_ERROR purpose=direct_reply agent=claude" in caplog.text


@pytest.mark.asyncio
async def test_handle_message_logs_running_elapsed_for_slow_direct_reply(caplog):
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
    registry.agents = [mock_agent]

    async def _slow_run(*args, **kwargs):
        await asyncio.sleep(0.02)
        return mock_agent, AgentResponse(text="hi")

    registry.run = AsyncMock(side_effect=_slow_run)

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([])
    gm._agent_progress_log_interval_seconds = 0.005

    with caplog.at_level("INFO"):
        await gm.handle_message(session, registry, _make_msg(thread_id="existing-thread", content="follow up"))

    assert "AGENT starting purpose=direct_reply" in caplog.text
    assert "AGENT_RUNNING purpose=direct_reply elapsed=" in caplog.text
    assert "AGENT_OK purpose=direct_reply agent=claude" in caplog.text


@pytest.mark.asyncio
async def test_skill_invocation_is_recorded_and_binds_first_response_message(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "runtime.db")
    await store.init()
    try:
        channel = MagicMock()
        channel.platform = "discord"
        channel.channel_id = "100"
        channel.create_thread = AsyncMock(return_value="thread-1")
        channel.send = AsyncMock(side_effect=["msg-1"])
        channel.typing = MagicMock()
        channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
        channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_agent = MagicMock()
        mock_agent.name = "codex"
        mock_agent.get_session_id = MagicMock(return_value=None)
        registry = MagicMock(spec=AgentRegistry)
        registry.agents = [mock_agent]
        registry.run = AsyncMock(
            return_value=(
                mock_agent,
                AgentResponse(
                    text="weather reply",
                    usage={"input_tokens": 11, "output_tokens": 22},
                ),
            )
        )

        skills_root = tmp_path / "skills"
        skill_dir = skills_root / "weather"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: weather\ndescription: Check weather quickly\n---\n",
            encoding="utf-8",
        )
        syncer = MagicMock(spec=SkillSync)
        syncer._skills_path = skills_root  # noqa: SLF001

        session = ChannelSession(
            platform="discord",
            channel_id="100",
            channel=channel,
            registry=registry,
            memory_store=store,
        )
        gm = GatewayManager([], skill_syncer=syncer, skill_evaluation_config={"enabled": True})
        gm.set_memory_store(store)

        await gm.handle_message(session, registry, _make_msg(thread_id="thread-1", content="/weather", author_id="u-1"))

        stats = await store.get_skill_stats("weather", recent_days=7)
        assert len(stats) == 1
        assert stats[0]["total_invocations"] == 1
        invocation = await store.get_skill_invocation_by_message("msg-1")
        assert invocation is not None
        assert invocation["skill_name"] == "weather"
        assert invocation["route_source"] == "explicit"
        assert invocation["outcome"] == "success"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_auto_disabled_skills_are_hidden_from_router_entries_but_explicit_calls_still_work(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "runtime.db")
    await store.init()
    try:
        await store.set_skill_auto_disabled("weather", disabled=True, reason="too many failures")

        channel = MagicMock()
        channel.platform = "discord"
        channel.channel_id = "100"
        channel.create_thread = AsyncMock(return_value="thread-1")
        channel.send = AsyncMock(side_effect=["msg-1"])
        channel.typing = MagicMock()
        channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
        channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_agent = MagicMock()
        mock_agent.name = "codex"
        mock_agent.get_session_id = MagicMock(return_value=None)
        registry = MagicMock(spec=AgentRegistry)
        registry.agents = [mock_agent]
        registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="ok")))

        skills_root = tmp_path / "skills"
        skill_dir = skills_root / "weather"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: weather\ndescription: Check weather quickly\n---\n",
            encoding="utf-8",
        )
        syncer = MagicMock(spec=SkillSync)
        syncer._skills_path = skills_root  # noqa: SLF001

        session = ChannelSession(
            platform="discord",
            channel_id="100",
            channel=channel,
            registry=registry,
            memory_store=store,
        )
        gm = GatewayManager([], skill_syncer=syncer, skill_evaluation_config={"enabled": True})
        gm.set_memory_store(store)
        await gm._refresh_auto_disabled_skills()

        assert gm._known_skill_router_entries() == []
        assert "weather" in gm._known_skill_names()

        await gm.handle_message(session, registry, _make_msg(thread_id="thread-1", content="/weather", author_id="u-1"))
        registry.run.assert_awaited()
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_recompute_skill_health_auto_disables_unhealthy_skill(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "runtime.db")
    await store.init()
    try:
        gm = GatewayManager(
            [],
            skill_evaluation_config={
                "enabled": True,
                "auto_disable": {
                    "enabled": True,
                    "rolling_window": 5,
                    "min_invocations": 5,
                    "failure_rate_threshold": 0.60,
                },
            },
        )
        gm.set_memory_store(store)
        for idx in range(5):
            await store.record_skill_invocation(
                skill_name="weather",
                agent_name="codex",
                platform="discord",
                channel_id="100",
                thread_id="thread-1",
                user_id="u-1",
                route_source="explicit",
                request_id=f"req-{idx}",
                outcome="error" if idx < 4 else "success",
                error_kind="cli_error" if idx < 4 else None,
                error_text="boom" if idx < 4 else None,
                latency_ms=1000,
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            )

        await gm._recompute_skill_health("weather")

        disabled = await store.list_auto_disabled_skills()
        assert disabled == {"weather"}
        stats = await store.get_skill_stats("weather", recent_days=7)
        assert stats[0]["auto_disabled"] == 1
    finally:
        await store.close()


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
    assert sent == USER_MSG_AGENT_CRASH
    # History should be empty (failed turn was popped)
    assert await session.get_history("t1") == []


@pytest.mark.asyncio
async def test_handle_message_deletes_stale_session_after_fallback_success(tmp_path):
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="t1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    flaky = _ResumeClearingAgent("codex")
    healthy = _ThreadAwareOKAgent("claude", "answer")
    registry = AgentRegistry([flaky, healthy])
    session = _make_session(channel=channel, registry=registry)

    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.init()

    gm = GatewayManager([])
    gm.set_memory_store(store)
    await store.save_session("discord", "100", "t1", "codex", "sess-stale")

    msg = _make_msg(thread_id="t1", content="follow up")
    await gm.handle_message(session, registry, msg)

    assert await store.load_session("discord", "100", "t1", "codex") is None
    assert channel.send.call_count >= 1
    await store.close()


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
    channel.platform = "telegram"
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
async def test_scheduler_dispatch_runtime_passes_automation_name():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.send = AsyncMock()

    registry = MagicMock(spec=AgentRegistry)
    session = _make_session(channel=channel, registry=registry)

    runtime = MagicMock()
    runtime.enabled = True
    runtime.enqueue_scheduler_task = AsyncMock()

    gm = GatewayManager([], runtime_service=runtime)
    gm._sessions["discord:100"] = session

    job = ScheduledJob(
        name="hello-from-codex",
        platform="discord",
        channel_id="100",
        prompt="run",
        interval_seconds=60,
    )
    await gm._dispatch_scheduled_job(job)

    runtime.enqueue_scheduler_task.assert_called_once()
    kwargs = runtime.enqueue_scheduler_task.call_args.kwargs
    assert kwargs["automation_name"] == "hello-from-codex"
    assert kwargs["thread_id"] == "100"


@pytest.mark.asyncio
async def test_scheduler_dispatch_runtime_passes_timeout_and_max_turns():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.send = AsyncMock()

    registry = MagicMock(spec=AgentRegistry)
    session = _make_session(channel=channel, registry=registry)

    runtime = MagicMock()
    runtime.enabled = True
    runtime.enqueue_scheduler_task = AsyncMock()

    gm = GatewayManager([], runtime_service=runtime)
    gm._sessions["discord:100"] = session

    job = ScheduledJob(
        name="hello-from-codex",
        platform="discord",
        channel_id="100",
        prompt="run",
        interval_seconds=60,
        timeout_seconds=900,
        max_turns=60,
    )
    await gm._dispatch_scheduled_job(job)

    kwargs = runtime.enqueue_scheduler_task.call_args.kwargs
    assert kwargs["timeout_seconds"] == 900
    assert kwargs["max_turns"] == 60


@pytest.mark.asyncio
async def test_handle_message_uses_short_workspace_override(tmp_path):
    base = tmp_path / "base-workspace"
    base.mkdir(parents=True, exist_ok=True)
    (base / "AGENTS.md").write_text("# workspace agents\n", encoding="utf-8")
    (base / ".claude").mkdir(exist_ok=True)
    (base / ".gemini").mkdir(exist_ok=True)
    (base / ".agents").mkdir(exist_ok=True)

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
    assert (ws / ".agents").exists()
    assert not (ws / ".codex").exists()


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


def test_prepare_workspace_compat_files_replaces_stale_entries(tmp_path):
    base = tmp_path / "base-workspace"
    base.mkdir(parents=True, exist_ok=True)
    (base / "AGENTS.md").write_text("# fresh agents\n", encoding="utf-8")
    (base / ".agents").mkdir(exist_ok=True)
    (base / ".agents" / "skills").mkdir(parents=True, exist_ok=True)
    (base / ".claude").mkdir(exist_ok=True)
    (base / ".gemini").mkdir(exist_ok=True)

    session_ws = tmp_path / "sessions" / "thread-1"
    session_ws.mkdir(parents=True, exist_ok=True)
    (session_ws / "AGENTS.md").write_text("# stale agents\n", encoding="utf-8")
    (session_ws / ".codex").mkdir(exist_ok=True)
    (session_ws / ".codex" / "old.txt").write_text("old", encoding="utf-8")

    gm = GatewayManager([], short_workspace={"base_workspace": str(base)})
    gm._prepare_workspace_compat_files(session_ws)

    assert (session_ws / "AGENTS.md").is_symlink()
    assert (session_ws / "AGENTS.md").resolve() == (base / "AGENTS.md")
    assert (session_ws / ".agents").is_symlink()
    assert (session_ws / ".agents").resolve() == (base / ".agents")
    assert not (session_ws / ".codex").exists()


@pytest.mark.asyncio
async def test_resolve_short_workspace_refreshes_stale_base_workspace(tmp_path):
    project_root = tmp_path / "project"
    skills_root = project_root / "skills" / "test-skill"
    skills_root.mkdir(parents=True, exist_ok=True)
    (skills_root / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: A synced skill\n---\n",
        encoding="utf-8",
    )
    (project_root / "AGENTS.md").write_text("# Repo Rules\n\n- version one\n", encoding="utf-8")

    syncer = SkillSync(project_root / "skills", project_root=project_root)
    base = tmp_path / "base-workspace"
    syncer.refresh_workspace(base)

    (project_root / "AGENTS.md").write_text("# Repo Rules\n\n- version two\n", encoding="utf-8")

    gm = GatewayManager(
        [],
        skill_syncer=syncer,
        short_workspace={
            "enabled": True,
            "root": str(tmp_path / "sessions"),
            "base_workspace": str(base),
        },
        repo_root=project_root,
    )
    session = _make_session()

    workspace = await gm._resolve_short_workspace(session, "thread-1")

    assert workspace is not None
    assert "- version two" in (base / "AGENTS.md").read_text(encoding="utf-8")
    assert (workspace / "AGENTS.md").is_symlink()
    assert (workspace / "AGENTS.md").resolve() == (base / "AGENTS.md")


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
            decision="propose_repo_change",
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
            decision="oneoff_artifact",
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


@pytest.mark.asyncio
async def test_explicit_skill_invocation_passes_skill_timeout_override(tmp_path):
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="t1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_agent = MagicMock()
    mock_agent.name = "gemini"
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="report")))

    runtime = MagicMock()
    runtime.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime.maybe_handle_incoming = AsyncMock(return_value=False)
    runtime.create_task = AsyncMock()
    runtime.create_skill_task = AsyncMock()

    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock()

    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "market-briefing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: market-briefing\ndescription: long report\nmetadata:\n  timeout_seconds: 900\n  max_turns: 80\n---\n",
        encoding="utf-8",
    )
    syncer = MagicMock()
    syncer._skills_path = skills_root  # noqa: SLF001

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager(
        [],
        runtime_service=runtime,
        intent_router=router,
        skill_syncer=syncer,
    )

    msg = _make_msg(thread_id="thread-1", content="/market-briefing")
    await gm.handle_message(session, registry, msg)

    registry.run.assert_awaited_once()
    assert registry.run.call_args.kwargs["timeout_override_seconds"] == 900
    assert registry.run.call_args.kwargs["max_turns_override"] == 80


@pytest.mark.asyncio
async def test_explicit_skill_invocation_chunks_first_message_with_attribution_budget(tmp_path):
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="t1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    long_text = "A" * 5000
    mock_agent = MagicMock()
    mock_agent.name = "codex"
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock(
        return_value=(
            mock_agent,
            AgentResponse(
                text=long_text,
                usage={
                    "input_tokens": 1234,
                    "output_tokens": 567,
                    "cache_read_input_tokens": 8901,
                    "cache_creation_input_tokens": 234,
                    "cost_usd": 0.0123,
                },
            ),
        )
    )

    runtime = MagicMock()
    runtime.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime.maybe_handle_incoming = AsyncMock(return_value=False)
    runtime.create_task = AsyncMock()
    runtime.create_skill_task = AsyncMock()

    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "top-5-daily-news"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("name: top-5-daily-news\n", encoding="utf-8")
    syncer = MagicMock()
    syncer._skills_path = skills_root  # noqa: SLF001

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([], runtime_service=runtime, skill_syncer=syncer)

    msg = _make_msg(thread_id="thread-1", content="/top-5-daily-news")
    await gm.handle_message(session, registry, msg)

    sent_messages = [call.args[1] for call in channel.send.await_args_list]
    assert len(sent_messages) >= 2
    assert sent_messages[0].startswith("-# via **codex**")
    assert "1,234 in / 567 out" in sent_messages[0]
    assert "cache 8,901r/234w" in sent_messages[0]
    assert "$0.0123" in sent_messages[0]
    assert max(len(message) for message in sent_messages) <= 2000


@pytest.mark.asyncio
async def test_router_repair_skill_creates_skill_task_with_thread_context(tmp_path):
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="t1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock()

    runtime = MagicMock()
    runtime.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime.maybe_handle_incoming = AsyncMock(return_value=False)
    runtime.create_skill_task = AsyncMock()

    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            # Canonical ``update_skill`` intent. Repair branch fires
            # because ``top-5-daily-news`` is registered as a real skill
            # in the syncer below.
            decision="update_skill",
            confidence=0.92,
            goal="Update existing skill 'top-5-daily-news' based on recent user feedback.",
            risk_hints=[],
            raw_text="",
            skill_name="top-5-daily-news",
            task_type="skill_change",
            completion_mode="merge",
        )
    )

    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "top-5-daily-news"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("name: top-5-daily-news\n", encoding="utf-8")
    syncer = MagicMock()
    syncer._skills_path = skills_root  # noqa: SLF001

    session = _make_session(channel=channel, registry=registry)
    await session.append_user("thread-1", "/top-5-daily-news", "alice")
    await session.append_assistant("thread-1", "some result", "claude")

    gm = GatewayManager([], runtime_service=runtime, intent_router=router, skill_syncer=syncer)
    msg = _make_msg(thread_id="thread-1", content="这个 skill 不太对，帮我修一下")
    await gm.handle_message(session, registry, msg)

    runtime.create_skill_task.assert_awaited_once()
    kwargs = runtime.create_skill_task.call_args.kwargs
    assert kwargs["skill_name"] == "top-5-daily-news"
    assert kwargs["source"] == "repair_skill"
    assert "Repair existing skill: top-5-daily-news" in kwargs["raw_request"]
    router.route.assert_awaited_once()
    routed_context = router.route.call_args.kwargs["context"]
    assert "/top-5-daily-news" in routed_context
    assert "some result" in routed_context
    registry.run.assert_not_called()


@pytest.mark.asyncio
async def test_router_invoke_existing_skill_uses_recent_merged_skill_context(tmp_path):
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
    registry.agents = [mock_agent]
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="analysis ready")))

    runtime = MagicMock()
    runtime.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime.maybe_handle_incoming = AsyncMock(return_value=False)

    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision="invoke_skill",
            confidence=0.93,
            goal="",
            risk_hints=[],
            raw_text="",
            skill_name="",
        )
    )

    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "bilibili-video-summarizer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bilibili-video-summarizer\ndescription: Summarize Bilibili video links, transcripts, or screenshots into concise Chinese notes.\n---\n",
        encoding="utf-8",
    )
    syncer = MagicMock()
    syncer._skills_path = skills_root  # noqa: SLF001

    session = _make_session(channel=channel, registry=registry)
    await session.append_user("thread-1", "https://www.bilibili.com/video/BV1eGZSB6EnL/", "alice")
    await session.append_assistant(
        "thread-1",
        "Task `abc123` merged successfully. Skill `bilibili-video-summarizer` merged and synced.",
        "runtime",
    )

    gm = GatewayManager(
        [],
        runtime_service=runtime,
        intent_router=router,
        skill_syncer=syncer,
        router_context_turns=4,
    )

    await gm.handle_message(session, registry, _make_msg(thread_id="thread-1", content="你现在可以分析了吗？"))

    router_context = router.route.call_args.kwargs["context"]
    assert "bilibili-video-summarizer" in router_context
    assert "Summarize Bilibili video links" in router_context
    registry.run.assert_awaited_once()
    prompt = registry.run.call_args.args[0]
    assert prompt.startswith("/bilibili-video-summarizer")
    assert "你现在可以分析了吗" in prompt


def test_router_context_turn_limit_is_configurable(tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Summarize demo links into a short brief.\n---\n",
        encoding="utf-8",
    )
    syncer = MagicMock()
    syncer._skills_path = skills_root  # noqa: SLF001

    gm = GatewayManager([], skill_syncer=syncer, router_context_turns=2)
    history = [
        {"role": "user", "content": "turn-1"},
        {"role": "assistant", "content": "turn-2"},
        {"role": "user", "content": "turn-3"},
    ]

    context = gm._build_router_context(history)  # noqa: SLF001

    assert "turn-1" not in context
    assert "turn-2" in context
    assert "turn-3" in context
    assert "demo-skill" in context
    assert "Summarize demo links into a short brief." in context


def test_router_context_includes_recent_thread_skill_with_description(tmp_path):
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "bilibili-video-summarizer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: bilibili-video-summarizer\ndescription: Summarize Bilibili links into concise Chinese notes.\n---\n",
        encoding="utf-8",
    )
    syncer = MagicMock()
    syncer._skills_path = skills_root  # noqa: SLF001

    gm = GatewayManager([], skill_syncer=syncer)
    gm._remember_thread_skill("discord", "100", "thread-1", "bilibili-video-summarizer")  # noqa: SLF001

    context = gm._build_router_context(  # noqa: SLF001
        [{"role": "user", "content": "请基于刚刚那个继续改进"}],
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
    )

    assert "Most recently invoked skill in this thread: bilibili-video-summarizer" in context
    assert "Summarize Bilibili links into concise Chinese notes." in context


@pytest.mark.asyncio
async def test_gateway_stop_rejects_new_messages():
    session = _make_session()
    gm = GatewayManager([(session.channel, session.registry)])

    await gm.stop()
    await gm.handle_message(session, session.registry, _make_msg(thread_id="thread-1", content="ignored"))

    session.channel.send.assert_not_called()
    session.channel.create_thread.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_stop_waits_for_inflight_messages():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock()
    channel.send = AsyncMock()
    channel.stop = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    started = asyncio.Event()
    release = asyncio.Event()
    mock_agent = MagicMock()
    mock_agent.name = "claude"
    registry = MagicMock(spec=AgentRegistry)
    registry.agents = [mock_agent]

    async def _run(*args, **kwargs):
        started.set()
        await release.wait()
        return mock_agent, AgentResponse(text="reply")

    registry.run = AsyncMock(side_effect=_run)
    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([(channel, registry)])

    message_task = asyncio.create_task(
        gm.handle_message(session, registry, _make_msg(thread_id="thread-1", content="slow"))
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    stop_task = asyncio.create_task(gm.stop(timeout=0.1))
    await asyncio.sleep(0.02)
    assert stop_task.done() is False

    release.set()
    await asyncio.gather(message_task, stop_task)
    channel.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_message_hides_agent_error_text():
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
    registry.agents = [mock_agent]
    registry.run = AsyncMock(
        return_value=(mock_agent, AgentResponse(text="", error="secret stack", error_kind="cli_error"))
    )

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([])

    await gm.handle_message(session, registry, _make_msg(thread_id="thread-1", content="trigger"))

    channel.send.assert_awaited_once_with("thread-1", USER_MSG_AGENT_CRASH)


@pytest.mark.asyncio
async def test_handle_message_surfaces_partial_excerpt_for_max_turns():
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
    registry.agents = [mock_agent]
    registry.run = AsyncMock(
        return_value=(
            mock_agent,
            AgentResponse(
                text="",
                error="budget hit",
                error_kind="max_turns",
                partial_text="partial answer",
            ),
        )
    )

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager([])

    await gm.handle_message(session, registry, _make_msg(thread_id="thread-1", content="trigger"))

    sent = channel.send.await_args.args[1]
    assert "max turn budget" in sent
    assert "partial answer" in sent


@pytest.mark.asyncio
async def test_handle_message_maps_storage_error_to_user_safe_message():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock()
    channel.send = AsyncMock()

    session = _make_session(channel=channel)
    session.get_history = AsyncMock(side_effect=sqlite3.OperationalError("db exploded"))
    gm = GatewayManager([])

    await gm.handle_message(session, session.registry, _make_msg(thread_id="thread-1", content="trigger"))

    channel.send.assert_awaited_once_with("thread-1", USER_MSG_STORE_FAILURE)


@pytest.mark.asyncio
async def test_handle_message_maps_unexpected_error_to_internal_message():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock()
    channel.send = AsyncMock()

    session = _make_session(channel=channel)
    session.get_history = AsyncMock(side_effect=RuntimeError("sensitive detail"))
    gm = GatewayManager([])

    await gm.handle_message(session, session.registry, _make_msg(thread_id="thread-1", content="trigger"))

    channel.send.assert_awaited_once_with("thread-1", USER_MSG_INTERNAL)


# ── Router borderline / autonomy-threshold behavior ─────────────────── #


def _make_router_border_channel():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="t1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)
    return channel


def _make_router_border_runtime():
    runtime = MagicMock()
    runtime.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime.maybe_handle_incoming = AsyncMock(return_value=False)
    runtime.create_skill_task = AsyncMock()
    return runtime


def _make_router_stub(*, decision: str, confidence: float, skill_name: str | None = None):
    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision=decision,
            confidence=confidence,
            goal="do the thing",
            risk_hints=[],
            raw_text="{}",
            skill_name=skill_name,
            task_type="skill_change",
            completion_mode="merge",
        )
    )
    return router


def _last_send_text(channel) -> str:
    assert channel.send.await_args is not None, "channel.send was not awaited"
    args = channel.send.await_args.args
    if len(args) >= 2:
        return args[1]
    return channel.send.await_args.kwargs.get("content", "")


@pytest.mark.asyncio
async def test_router_create_skill_borderline_forces_draft_and_confirm_text():
    channel = _make_router_border_channel()
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock()
    runtime = _make_router_border_runtime()
    router = _make_router_stub(decision="update_skill", confidence=0.70, skill_name="skill-x")

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager(
        [],
        runtime_service=runtime,
        intent_router=router,
        router_require_user_confirm=True,
        router_autonomy_threshold=0.90,
    )
    await gm.handle_message(session, registry, _make_msg(thread_id="t1", content="帮我做一件事"))

    runtime.create_skill_task.assert_awaited_once()
    kwargs = runtime.create_skill_task.call_args.kwargs
    assert kwargs["force_draft"] is True
    text = _last_send_text(channel)
    assert "/task_approve" in text
    assert "/task_reject" in text
    assert "draft" in text.lower() or "not confident" in text.lower()


@pytest.mark.asyncio
async def test_router_create_skill_high_confidence_skips_borderline_and_auto_runs():
    channel = _make_router_border_channel()
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock()
    runtime = _make_router_border_runtime()
    router = _make_router_stub(decision="update_skill", confidence=0.95, skill_name="skill-y")

    session = _make_session(channel=channel, registry=registry)
    gm = GatewayManager(
        [],
        runtime_service=runtime,
        intent_router=router,
        router_require_user_confirm=True,
        router_autonomy_threshold=0.90,
    )
    await gm.handle_message(session, registry, _make_msg(thread_id="t1", content="create a daily AI news skill"))

    runtime.create_skill_task.assert_awaited_once()
    kwargs = runtime.create_skill_task.call_args.kwargs
    # Non-borderline: manager must not override; force_draft passed through as None.
    assert kwargs.get("force_draft") is None
    text = _last_send_text(channel)
    assert "/task_stop" in text
    assert "started execution" in text.lower() or "already started" in text.lower()


@pytest.mark.asyncio
async def test_router_repair_skill_borderline_forces_draft_and_confirm_text(tmp_path):
    channel = _make_router_border_channel()
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock()
    runtime = _make_router_border_runtime()
    router = _make_router_stub(decision="update_skill", confidence=0.70, skill_name="paper-digest")

    skills_root = tmp_path / "skills"
    (skills_root / "paper-digest").mkdir(parents=True)
    (skills_root / "paper-digest" / "SKILL.md").write_text("name: paper-digest\n", encoding="utf-8")
    syncer = MagicMock()
    syncer._skills_path = skills_root

    session = _make_session(channel=channel, registry=registry)
    await session.append_user("t1", "/paper-digest", "alice")
    await session.append_assistant("t1", "result here", "claude")
    gm = GatewayManager(
        [],
        runtime_service=runtime,
        intent_router=router,
        skill_syncer=syncer,
        router_require_user_confirm=True,
        router_autonomy_threshold=0.90,
    )
    await gm.handle_message(session, registry, _make_msg(thread_id="t1", content="这个 skill 有点问题"))

    runtime.create_skill_task.assert_awaited_once()
    kwargs = runtime.create_skill_task.call_args.kwargs
    assert kwargs["force_draft"] is True
    assert kwargs["source"] == "repair_skill"
    text = _last_send_text(channel)
    assert "/task_approve" in text
    assert "/task_reject" in text


@pytest.mark.asyncio
async def test_router_repair_skill_high_confidence_skips_borderline_and_auto_runs(tmp_path):
    channel = _make_router_border_channel()
    registry = MagicMock(spec=AgentRegistry)
    registry.run = AsyncMock()
    runtime = _make_router_border_runtime()
    router = _make_router_stub(decision="update_skill", confidence=0.95, skill_name="paper-digest")

    skills_root = tmp_path / "skills"
    (skills_root / "paper-digest").mkdir(parents=True)
    (skills_root / "paper-digest" / "SKILL.md").write_text("name: paper-digest\n", encoding="utf-8")
    syncer = MagicMock()
    syncer._skills_path = skills_root

    session = _make_session(channel=channel, registry=registry)
    await session.append_user("t1", "/paper-digest", "alice")
    await session.append_assistant("t1", "result here", "claude")
    gm = GatewayManager(
        [],
        runtime_service=runtime,
        intent_router=router,
        skill_syncer=syncer,
        router_require_user_confirm=True,
        router_autonomy_threshold=0.90,
    )
    await gm.handle_message(session, registry, _make_msg(thread_id="t1", content="fix the summary length"))

    runtime.create_skill_task.assert_awaited_once()
    kwargs = runtime.create_skill_task.call_args.kwargs
    assert kwargs.get("force_draft") is None
    text = _last_send_text(channel)
    assert "/task_stop" in text


@pytest.mark.asyncio
async def test_shutdown_event_wakes_short_workspace_janitor(tmp_path):
    """Setting _shutdown_event lets the janitor exit cooperatively, no cancel."""
    gm = GatewayManager([])
    gm._short_workspace_enabled = True
    gm._short_workspace_root = tmp_path / "ws"
    gm._short_workspace_root.mkdir(parents=True, exist_ok=True)
    # 60 minutes — would block effectively forever on a plain asyncio.sleep.
    gm._short_workspace_cleanup_interval_minutes = 60

    task = asyncio.create_task(gm._run_short_workspace_janitor())
    # let the loop run one iteration and enter the wait
    await asyncio.sleep(0.05)
    assert not task.done(), "janitor should be sleeping on the shutdown event"

    gm._shutdown_event.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert task.done() and task.exception() is None
