from __future__ import annotations

import logging
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from oh_my_agent.agents.base import AgentResponse, BaseAgent
from oh_my_agent.runtime.service import RuntimeService
from oh_my_agent.runtime.types import RuntimeTask


def _task(**overrides) -> RuntimeTask:
    base = dict(
        id="task-1",
        platform="discord",
        channel_id="100",
        thread_id="200",
        created_by="owner-1",
        goal="run",
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
    def __init__(self, name: str = "claude") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def run(self, prompt, history=None, **kwargs):  # pragma: no cover
        raise AssertionError("agent.run should not be reached")


def _retry_stub(responses: list[AgentResponse]):
    """Build a stub exposing `_invoke_agent_with_retry` bound to a mocked `_invoke_agent` + store."""
    stub = SimpleNamespace()
    stub._invoke_agent = AsyncMock(side_effect=responses)
    stub._store = SimpleNamespace(add_runtime_event=AsyncMock(return_value=None))
    stub._invoke_agent_with_retry = MethodType(
        RuntimeService._invoke_agent_with_retry, stub
    )
    return stub


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Stub asyncio.sleep so retry backoffs don't slow the suite."""
    async def _fast(_seconds):
        return None
    monkeypatch.setattr("oh_my_agent.runtime.service.asyncio.sleep", _fast)


async def _call(stub, agent=None):
    return await stub._invoke_agent_with_retry(
        agent or _StubAgent(),
        "prompt",
        Path("/tmp/ws"),
        "thread-1",
        _task(),
        1,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_kind", ["max_turns", "auth", "cli_error"])
async def test_terminal_kinds_do_not_retry(terminal_kind):
    stub = _retry_stub([
        AgentResponse(text="", error="boom", error_kind=terminal_kind),
        # would be an assertion error if consumed
        AgentResponse(text="should-not-run"),
    ])

    response = await _call(stub)

    assert response.error_kind == terminal_kind
    assert stub._invoke_agent.await_count == 1
    assert stub._store.add_runtime_event.await_count == 0


@pytest.mark.asyncio
async def test_rate_limit_retries_then_succeeds(caplog):
    stub = _retry_stub([
        AgentResponse(text="", error="429 rate limit", error_kind="rate_limit"),
        AgentResponse(text="ok"),
    ])

    caplog.set_level(logging.INFO, logger="oh_my_agent.runtime.service")
    response = await _call(stub)

    assert response.text == "ok"
    assert stub._invoke_agent.await_count == 2
    assert stub._store.add_runtime_event.await_count == 1
    event_args = stub._store.add_runtime_event.await_args_list[0]
    assert event_args.args[1] == "task.agent_retry"
    payload = event_args.args[2]
    assert payload["kind"] == "rate_limit"
    assert payload["attempt"] == 1
    assert payload["backoff_seconds"] == 10
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("retry=1/3 kind=rate_limit backoff=10s" in m for m in messages)


@pytest.mark.asyncio
async def test_api_5xx_uses_escalating_backoff_until_success():
    stub = _retry_stub([
        AgentResponse(text="", error="503 service unavailable", error_kind="api_5xx"),
        AgentResponse(text="", error="503 again", error_kind="api_5xx"),
        AgentResponse(text="ok"),
    ])

    response = await _call(stub)

    assert response.text == "ok"
    assert stub._invoke_agent.await_count == 3
    backoffs = [
        call.args[2]["backoff_seconds"]
        for call in stub._store.add_runtime_event.await_args_list
    ]
    assert backoffs == [5, 15]


@pytest.mark.asyncio
async def test_timeout_retries_once_then_gives_up(caplog):
    stub = _retry_stub([
        AgentResponse(text="", error="timed out", error_kind="timeout"),
        AgentResponse(text="", error="timed out again", error_kind="timeout"),
    ])

    caplog.set_level(logging.WARNING, logger="oh_my_agent.runtime.service")
    response = await _call(stub)

    assert response.error == "timed out again"
    assert response.error_kind == "timeout"
    assert stub._invoke_agent.await_count == 2
    assert stub._store.add_runtime_event.await_count == 1
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("per-kind retry exhausted" in m and "timeout" in m for m in messages)


@pytest.mark.asyncio
async def test_per_kind_cap_fires_before_total_cap():
    # rate_limit allows 2 retries; 3rd rate_limit should be terminal even
    # though the global cap (3) has room for another attempt.
    stub = _retry_stub([
        AgentResponse(text="", error="429 a", error_kind="rate_limit"),
        AgentResponse(text="", error="429 b", error_kind="rate_limit"),
        AgentResponse(text="", error="429 c", error_kind="rate_limit"),
    ])

    response = await _call(stub)

    assert response.error == "429 c"
    assert stub._invoke_agent.await_count == 3
    assert stub._store.add_runtime_event.await_count == 2


@pytest.mark.asyncio
async def test_total_cap_fires_when_mixing_kinds():
    # Mix rate_limit + api_5xx: 2 + 2 would allow 4 retries per-kind, but the
    # global cap is 3 so the 4th attempt's retry must be rejected.
    stub = _retry_stub([
        AgentResponse(text="", error="429", error_kind="rate_limit"),
        AgentResponse(text="", error="503", error_kind="api_5xx"),
        AgentResponse(text="", error="429 again", error_kind="rate_limit"),
        AgentResponse(text="", error="503 again", error_kind="api_5xx"),
        # The next response would be a success but should never be reached.
        AgentResponse(text="should-not-run"),
    ])

    response = await _call(stub)

    # 1 initial + 3 retries = 4 agent calls. The 5th (success) isn't reached
    # because the 4th failure hits _MAX_TOTAL_RETRIES.
    assert stub._invoke_agent.await_count == 4
    assert response.error_kind == "api_5xx"
    assert stub._store.add_runtime_event.await_count == 3


@pytest.mark.asyncio
async def test_first_call_success_skips_retry_machinery():
    stub = _retry_stub([AgentResponse(text="done")])

    response = await _call(stub)

    assert response.text == "done"
    assert stub._invoke_agent.await_count == 1
    assert stub._store.add_runtime_event.await_count == 0


@pytest.mark.asyncio
async def test_missing_error_kind_treated_as_cli_error():
    # Agent returned an error but no error_kind — wrapper falls back to
    # treating it as the terminal cli_error.
    stub = _retry_stub([
        AgentResponse(text="", error="mystery", error_kind=None),
    ])

    response = await _call(stub)

    assert response.error == "mystery"
    assert stub._invoke_agent.await_count == 1
    assert stub._store.add_runtime_event.await_count == 0
