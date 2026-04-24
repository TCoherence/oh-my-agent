from __future__ import annotations

from pathlib import Path

import pytest

from oh_my_agent.agents.base import AgentResponse, BaseAgent
from oh_my_agent.agents.registry import AgentRegistry


class _RecordingAgent(BaseAgent):
    def __init__(self, name: str, *, error: str | None = None) -> None:
        self._name = name
        self._error = error
        self.log_paths: list[Path | None] = []

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt, history=None, *, thread_id=None, workspace_override=None, log_path=None, image_paths=None):
        self.log_paths.append(log_path)
        return AgentResponse(text="" if self._error else "ok", error=self._error)


@pytest.mark.asyncio
async def test_registry_derives_per_agent_log_paths(tmp_path):
    first = _RecordingAgent("codex", error="boom")
    second = _RecordingAgent("claude")
    registry = AgentRegistry([first, second])

    base_log = tmp_path / "chat-thread.log"
    agent, response = await registry.run("hello", log_path=base_log)

    assert agent.name == "claude"
    assert response.error is None
    assert first.log_paths == [tmp_path / "chat-thread-codex.log"]
    assert second.log_paths == [tmp_path / "chat-thread-claude.log"]


class _HookObservingAgent(BaseAgent):
    """Records whether on_partial / on_tool_use were forwarded by the registry."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.saw_on_partial: bool = False
        self.saw_on_tool_use: bool = False

    @property
    def name(self) -> str:
        return self._name

    async def run(
        self,
        prompt,
        history=None,
        *,
        thread_id=None,
        workspace_override=None,
        log_path=None,
        image_paths=None,
        on_partial=None,
        on_tool_use=None,
    ):
        self.saw_on_partial = on_partial is not None
        self.saw_on_tool_use = on_tool_use is not None
        return AgentResponse(text="ok")


class _LegacyAgent(BaseAgent):
    """No on_tool_use in signature — registry must not try to pass it."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt, history=None, *, thread_id=None):
        return AgentResponse(text="legacy ok")


@pytest.mark.asyncio
async def test_registry_forwards_on_tool_use_when_agent_accepts_it():
    agent = _HookObservingAgent("claude")
    registry = AgentRegistry([agent])

    async def _partial(_: str) -> None:
        pass

    async def _tool(_: str) -> None:
        pass

    _, response = await registry.run(
        "hi",
        on_partial=_partial,
        on_tool_use=_tool,
    )
    assert response.error is None
    assert agent.saw_on_partial is True
    assert agent.saw_on_tool_use is True


@pytest.mark.asyncio
async def test_registry_skips_on_tool_use_for_legacy_agents():
    """Agents whose ``run()`` has no ``on_tool_use`` param must not raise."""
    agent = _LegacyAgent("legacy")
    registry = AgentRegistry([agent])

    async def _tool(_: str) -> None:
        pass

    _, response = await registry.run("hi", on_tool_use=_tool)
    # No TypeError from an unexpected kwarg; the legacy agent simply ignores it.
    assert response.error is None
    assert response.text == "legacy ok"
