from unittest.mock import AsyncMock, MagicMock

import pytest

from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.gateway.base import IncomingMessage
from oh_my_agent.gateway.manager import GatewayManager
from oh_my_agent.gateway.router import RouteDecision
from oh_my_agent.gateway.session import ChannelSession


def _make_msg(content: str, *, thread_id: str = "thread-1") -> IncomingMessage:
    return IncomingMessage(
        platform="discord",
        channel_id="100",
        thread_id=thread_id,
        author="alice",
        author_id="owner-1",
        content=content,
    )


def _make_session_and_registry():
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.create_thread = AsyncMock(return_value="thread-1")
    channel.send = AsyncMock()
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = MagicMock()
    agent.name = "claude"
    registry = MagicMock(spec=AgentRegistry)
    registry.agents = [agent]
    registry.run = AsyncMock(return_value=(agent, AgentResponse(text="chat reply")))

    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    return session, registry, channel


@pytest.mark.asyncio
async def test_manager_routes_create_skill_from_router():
    session, registry, channel = _make_session_and_registry()
    runtime_service = MagicMock()
    runtime_service.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime_service.create_skill_task = AsyncMock()
    runtime_service.create_task = AsyncMock()
    runtime_service.maybe_handle_incoming = AsyncMock(return_value=False)
    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision="create_skill",
            confidence=0.9,
            goal="Create a reusable weather skill",
            risk_hints=[],
            raw_text="",
            skill_name="weather",
        )
    )
    gm = GatewayManager([], runtime_service=runtime_service, intent_router=router, owner_user_ids={"owner-1"})

    await gm.handle_message(session, registry, _make_msg("create a skill for weather"))

    runtime_service.create_skill_task.assert_awaited_once()
    runtime_service.create_task.assert_not_called()
    runtime_service.maybe_handle_incoming.assert_not_called()
    registry.run.assert_not_called()
    assert channel.send.await_count >= 1


@pytest.mark.asyncio
async def test_manager_high_confidence_reply_once_skips_skill_heuristic():
    session, registry, channel = _make_session_and_registry()
    runtime_service = MagicMock()
    runtime_service.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime_service.create_skill_task = AsyncMock()
    runtime_service.create_task = AsyncMock()
    runtime_service.maybe_handle_incoming = AsyncMock(return_value=False)
    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision="reply_once",
            confidence=0.95,
            goal="",
            risk_hints=[],
            raw_text="",
        )
    )
    gm = GatewayManager([], runtime_service=runtime_service, intent_router=router, owner_user_ids={"owner-1"})

    await gm.handle_message(session, registry, _make_msg("create a skill for weather"))

    runtime_service.create_skill_task.assert_not_called()
    runtime_service.maybe_handle_incoming.assert_not_called()
    registry.run.assert_called_once()
    assert channel.send.await_count >= 1


@pytest.mark.asyncio
async def test_manager_low_confidence_reply_once_falls_back_to_skill_heuristic():
    session, registry, _ = _make_session_and_registry()
    runtime_service = MagicMock()
    runtime_service.maybe_handle_thread_context = AsyncMock(return_value=False)
    runtime_service.create_skill_task = AsyncMock()
    runtime_service.create_task = AsyncMock()
    runtime_service.maybe_handle_incoming = AsyncMock(return_value=False)
    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock(
        return_value=RouteDecision(
            decision="reply_once",
            confidence=0.2,
            goal="",
            risk_hints=[],
            raw_text="",
        )
    )
    gm = GatewayManager([], runtime_service=runtime_service, intent_router=router, owner_user_ids={"owner-1"})

    await gm.handle_message(session, registry, _make_msg("create a skill for weather"))

    runtime_service.create_skill_task.assert_awaited_once()
    runtime_service.maybe_handle_incoming.assert_not_called()


@pytest.mark.asyncio
async def test_manager_thread_context_takes_priority_over_router():
    session, registry, channel = _make_session_and_registry()
    runtime_service = MagicMock()
    runtime_service.maybe_handle_thread_context = AsyncMock(return_value=True)
    runtime_service.create_skill_task = AsyncMock()
    runtime_service.create_task = AsyncMock()
    runtime_service.maybe_handle_incoming = AsyncMock(return_value=False)
    router = MagicMock()
    router.confidence_threshold = 0.55
    router.route = AsyncMock()
    gm = GatewayManager([], runtime_service=runtime_service, intent_router=router, owner_user_ids={"owner-1"})

    await gm.handle_message(session, registry, _make_msg("retry merge"))

    runtime_service.maybe_handle_thread_context.assert_awaited_once()
    router.route.assert_not_called()
    runtime_service.create_skill_task.assert_not_called()
    runtime_service.create_task.assert_not_called()
    runtime_service.maybe_handle_incoming.assert_not_called()
    registry.run.assert_not_called()
    channel.send.assert_not_called()
