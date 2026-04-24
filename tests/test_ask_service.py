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
