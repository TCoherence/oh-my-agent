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
