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


class _ModelRecordingAgent(BaseAgent):
    """Accepts a per-call ``model_override`` (like ClaudeAgent) and records
    the effective model — WITHOUT mutating ``self._model`` (concurrency-safe).
    """

    def __init__(self, name: str = "claude", model: str = "sonnet-default") -> None:
        self._name = name
        self._model = model
        self.models_seen: list[str] = []

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
        model_override=None,
    ):
        # Compute effective model locally; never reassign self._model.
        self.models_seen.append(model_override or self._model)
        return AgentResponse(text="ok")


@pytest.mark.asyncio
async def test_registry_forwards_model_override_without_mutating_agent():
    agent = _ModelRecordingAgent(model="sonnet-default")
    registry = AgentRegistry([agent])

    # Call with override → agent's run() receives it as a kwarg
    await registry.run("p1", model_override="haiku-cheap")
    assert agent.models_seen == ["haiku-cheap"]
    # self._model is NEVER mutated (non-mutating plumbing)
    assert agent._model == "sonnet-default"

    # Call without override → agent uses its configured model
    await registry.run("p2")
    assert agent.models_seen == ["haiku-cheap", "sonnet-default"]
    assert agent._model == "sonnet-default"


@pytest.mark.asyncio
async def test_registry_model_override_skipped_for_agent_without_param():
    # _RecordingAgent.run() has no model_override param — registry's
    # signature dispatch skips it, no crash, agent runs on its own model.
    agent = _RecordingAgent("codex")
    registry = AgentRegistry([agent])
    _agent, response = await registry.run("p", model_override="whatever")
    assert response.error is None


@pytest.mark.asyncio
async def test_registry_model_override_concurrent_no_corruption():
    """Two concurrent runs with different overrides on the SAME agent must
    not corrupt each other — the whole point of the non-mutating fix."""
    import asyncio

    agent = _ModelRecordingAgent(model="base")
    registry = AgentRegistry([agent])
    await asyncio.gather(
        registry.run("a", model_override="m-a"),
        registry.run("b", model_override="m-b"),
    )
    # Both overrides observed; configured model untouched.
    assert sorted(agent.models_seen) == ["m-a", "m-b"]
    assert agent._model == "base"
