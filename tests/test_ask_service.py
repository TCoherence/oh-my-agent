from __future__ import annotations

import pytest

from oh_my_agent.gateway.services.ask_service import AskService


class _RegistryStub:
    def __init__(self):
        self.agents = [type("Agent", (), {"name": "claude"})(), type("Agent", (), {"name": "codex"})()]

    def get_agent(self, name: str):
        return next((agent for agent in self.agents if agent.name == name), None)


class _SessionStub:
    def __init__(self):
        self.cleared: list[str] = []
        self.history = [
            {"role": "user", "author": "Coherence", "content": "hello"},
            {"role": "assistant", "agent": "codex", "content": "world"},
        ]

    async def clear_history(self, thread_id: str) -> None:
        self.cleared.append(thread_id)

    async def get_history(self, thread_id: str):
        return self.history if thread_id == "thread-1" else []


@pytest.mark.asyncio
async def test_reset_history_calls_session():
    service = AskService()
    session = _SessionStub()

    result = await service.reset_history(session, "thread-1")

    assert result.success is True
    assert session.cleared == ["thread-1"]


@pytest.mark.asyncio
async def test_get_history_formats_turns():
    service = AskService()
    session = _SessionStub()

    result = await service.get_history(session, "thread-1")

    assert result.success is True
    assert "**Thread history**" in result.message
    assert "Coherence" in result.message
    assert "codex" in result.message


@pytest.mark.asyncio
async def test_list_agents_returns_fallback_order():
    service = AskService()
    registry = _RegistryStub()

    result = await service.list_agents(registry)

    assert result.success is True
    assert "`claude`" in result.message
    assert "`codex`" in result.message


class _ResumeAgentStub:
    name = "claude"

    def __init__(self):
        self._sessions = {"thread-1": "sess-abc"}

    def get_session_id(self, thread_id: str):
        return self._sessions.get(thread_id)

    def clear_session(self, thread_id: str) -> None:
        self._sessions.pop(thread_id, None)


@pytest.mark.asyncio
async def test_reset_history_clears_in_memory_agent_sessions():
    """A cached CLI session id must not survive /reset — otherwise the next
    message resumes the conversation the user just cleared."""
    service = AskService()
    session = _SessionStub()
    agent = _ResumeAgentStub()
    registry = _RegistryStub()
    registry.agents = [agent]

    result = await service.reset_history(session, "thread-1", registry)

    assert result.success is True
    assert session.cleared == ["thread-1"]
    assert agent.get_session_id("thread-1") is None


@pytest.mark.asyncio
async def test_reset_history_tolerates_agents_without_clear_session():
    service = AskService()
    session = _SessionStub()
    registry = _RegistryStub()  # agents expose no clear_session

    result = await service.reset_history(session, "thread-1", registry)

    assert result.success is True
    assert session.cleared == ["thread-1"]
