from __future__ import annotations

import logging
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from oh_my_agent.agents.base import AgentResponse, BaseAgent
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.runtime.service import RuntimeService
from oh_my_agent.runtime.types import RuntimeTask


def _task(**overrides) -> RuntimeTask:
    base = dict(
        id="task-1",
        platform="discord",
        channel_id="100",
        thread_id="200",
        created_by="owner-1",
        goal="run seattle weekly",
        original_request=None,
        preferred_agent=None,
        status="RUNNING",
        step_no=1,
        max_steps=8,
        max_minutes=20,
        agent_timeout_seconds=None,
        agent_max_turns=None,
        test_command="true",
        workspace_path=None,
        decision_message_id=None,
        status_message_id=None,
        blocked_reason=None,
        error=None,
        summary=None,
        resume_instruction=None,
        merge_commit_hash=None,
        merge_error=None,
        completion_mode="reply",
        output_summary=None,
        artifact_manifest=None,
        automation_name=None,
        workspace_cleaned_at=None,
        created_at=None,
        started_at=None,
        updated_at=None,
        ended_at=None,
        task_type="artifact",
        skill_name=None,
    )
    base.update(overrides)
    return RuntimeTask(**base)


class _StubAgent(BaseAgent):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt, history=None, **kwargs):  # pragma: no cover - not used
        raise AssertionError("agent.run should not be called; _invoke_agent is stubbed")


def _service_stub(responses: list[AgentResponse]):
    """Build a minimal object exposing `_run_agent` bound to a stub with a mocked `_invoke_agent_with_retry`."""
    stub = SimpleNamespace()
    stub._invoke_agent_with_retry = AsyncMock(side_effect=responses)
    stub._run_agent = MethodType(RuntimeService._run_agent, stub)
    return stub


@pytest.mark.asyncio
async def test_run_agent_falls_back_after_non_max_turns_error(caplog):
    a = _StubAgent("claude")
    b = _StubAgent("codex")
    registry = AgentRegistry([a, b])

    stub = _service_stub([
        AgentResponse(text="", error="network blip", error_kind="cli_error"),
        AgentResponse(text="done"),
    ])

    caplog.set_level(logging.INFO, logger="oh_my_agent.runtime.service")
    name, response = await stub._run_agent(
        registry=registry,
        task=_task(),
        prompt="hi",
        workspace=Path("/tmp/ws"),
        step=1,
    )

    assert name == "codex"
    assert response.text == "done"
    assert stub._invoke_agent_with_retry.await_count == 2
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("Trying agent 'claude'" in m for m in messages)
    assert any("Trying agent 'codex'" in m for m in messages)
    assert any("trying next" in m for m in messages)


@pytest.mark.asyncio
async def test_run_agent_does_not_fall_back_on_max_turns(caplog):
    a = _StubAgent("claude")
    b = _StubAgent("codex")
    registry = AgentRegistry([a, b])

    stub = _service_stub([
        AgentResponse(
            text="",
            error="claude exited 1: max_turns",
            error_kind="max_turns",
        ),
        # second response should never be consumed
        AgentResponse(text="should-not-run"),
    ])

    caplog.set_level(logging.WARNING, logger="oh_my_agent.runtime.service")
    name, response = await stub._run_agent(
        registry=registry,
        task=_task(),
        prompt="hi",
        workspace=Path("/tmp/ws"),
        step=1,
    )

    assert name == "claude"
    assert response.error_kind == "max_turns"
    assert stub._invoke_agent_with_retry.await_count == 1
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("hit max turns" in m and "not falling back" in m for m in messages)


@pytest.mark.asyncio
async def test_run_agent_preferred_agent_short_circuits_fallback():
    a = _StubAgent("claude")
    b = _StubAgent("codex")
    registry = AgentRegistry([a, b])

    stub = _service_stub([
        AgentResponse(text="", error="boom", error_kind="cli_error"),
        # this should never be consumed even though the first failed
        AgentResponse(text="should-not-run"),
    ])

    name, response = await stub._run_agent(
        registry=registry,
        task=_task(preferred_agent="codex"),
        prompt="hi",
        workspace=Path("/tmp/ws"),
        step=1,
    )

    assert name == "codex"
    assert response.error == "boom"
    assert stub._invoke_agent_with_retry.await_count == 1
    # Verify the forced agent was codex, not claude
    forced_agent = stub._invoke_agent_with_retry.await_args_list[0].args[0]
    assert forced_agent.name == "codex"


@pytest.mark.asyncio
async def test_run_agent_all_fail_returns_last_response(caplog):
    a = _StubAgent("claude")
    b = _StubAgent("codex")
    registry = AgentRegistry([a, b])

    stub = _service_stub([
        AgentResponse(text="", error="first fail", error_kind="cli_error"),
        AgentResponse(text="", error="second fail", error_kind="cli_error"),
    ])

    caplog.set_level(logging.ERROR, logger="oh_my_agent.runtime.service")
    name, response = await stub._run_agent(
        registry=registry,
        task=_task(),
        prompt="hi",
        workspace=Path("/tmp/ws"),
        step=1,
    )

    assert name == "codex"
    assert response.error == "second fail"
    assert stub._invoke_agent_with_retry.await_count == 2
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("All agents failed" in m for m in messages)
