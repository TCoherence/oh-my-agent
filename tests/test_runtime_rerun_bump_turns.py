from __future__ import annotations

from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.gateway.services.task_service import TaskService
from oh_my_agent.runtime.service import (
    _RERUN_BUMP_TURNS_DEFAULT,
    _RERUN_FALLBACK_BASE_TURNS,
    RuntimeService,
)
from oh_my_agent.runtime.types import RuntimeTask


def _task(**overrides) -> RuntimeTask:
    base = dict(
        id="parent-1",
        platform="discord",
        channel_id="100",
        thread_id="200",
        created_by="owner-1",
        goal="run the job",
        original_request="raw request text",
        preferred_agent="claude",
        status="FAILED",
        step_no=3,
        max_steps=8,
        max_minutes=20,
        agent_timeout_seconds=1500,
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
        automation_name="seattle-weekly",
        workspace_cleaned_at=None,
        created_at=None,
        started_at=None,
        updated_at=None,
        ended_at=None,
        task_type="artifact",
        skill_name="seattle-metro-housing-watch",
    )
    base.update(overrides)
    return RuntimeTask(**base)


def _rerun_stub(*, parent: RuntimeTask):
    """Build a stub exposing ``_rerun_task_with_bumped_turns`` bound to a mocked
    store + the real method implementation."""
    stub = SimpleNamespace()

    def _create(**kwargs) -> RuntimeTask:
        return _task(
            id=kwargs["task_id"],
            status=kwargs["status"],
            agent_max_turns=kwargs.get("agent_max_turns"),
            goal=kwargs["goal"],
            preferred_agent=kwargs.get("preferred_agent"),
            completion_mode=kwargs.get("completion_mode", "reply"),
            automation_name=kwargs.get("automation_name"),
            skill_name=kwargs.get("skill_name"),
            task_type=kwargs.get("task_type", "artifact"),
            created_by=kwargs["created_by"],
            max_steps=kwargs["max_steps"],
            max_minutes=kwargs["max_minutes"],
            test_command=kwargs["test_command"],
        )

    stub._store = SimpleNamespace(
        create_runtime_task=AsyncMock(side_effect=_create),
        add_runtime_event=AsyncMock(return_value=None),
    )
    stub._task_sources = {}
    stub._notify = AsyncMock(return_value=None)
    stub._signal_status_by_id = AsyncMock(return_value=None)
    stub._rerun_task_with_bumped_turns = MethodType(
        RuntimeService._rerun_task_with_bumped_turns, stub
    )
    return stub


@pytest.mark.asyncio
async def test_rerun_bump_turns_uses_fallback_when_parent_has_no_override():
    parent = _task(agent_max_turns=None)
    stub = _rerun_stub(parent=parent)

    result = await stub._rerun_task_with_bumped_turns(
        parent,
        actor_id="owner-1",
        source="button",
    )

    expected_turns = _RERUN_FALLBACK_BASE_TURNS + _RERUN_BUMP_TURNS_DEFAULT
    create_kwargs = stub._store.create_runtime_task.await_args.kwargs
    assert create_kwargs["agent_max_turns"] == expected_turns
    assert create_kwargs["status"] == "PENDING"
    assert create_kwargs["goal"] == parent.goal
    assert create_kwargs["preferred_agent"] == parent.preferred_agent
    assert create_kwargs["completion_mode"] == parent.completion_mode
    assert create_kwargs["automation_name"] == parent.automation_name
    assert create_kwargs["skill_name"] == parent.skill_name
    assert create_kwargs["task_type"] == parent.task_type
    assert create_kwargs["test_command"] == parent.test_command
    assert create_kwargs["original_request"] == parent.original_request
    assert f"max_turns={expected_turns}" in result


@pytest.mark.asyncio
async def test_rerun_bump_turns_bumps_explicit_parent_override():
    parent = _task(agent_max_turns=60)
    stub = _rerun_stub(parent=parent)

    await stub._rerun_task_with_bumped_turns(
        parent,
        actor_id="owner-1",
        source="button",
    )

    expected_turns = 60 + _RERUN_BUMP_TURNS_DEFAULT
    create_kwargs = stub._store.create_runtime_task.await_args.kwargs
    assert create_kwargs["agent_max_turns"] == expected_turns


@pytest.mark.asyncio
async def test_rerun_bump_turns_emits_lineage_events():
    parent = _task(agent_max_turns=25)
    stub = _rerun_stub(parent=parent)

    await stub._rerun_task_with_bumped_turns(
        parent,
        actor_id="owner-1",
        source="button",
    )

    event_calls = stub._store.add_runtime_event.await_args_list
    assert len(event_calls) == 2

    sibling_created_kwargs = event_calls[0].args
    assert sibling_created_kwargs[1] == "task.created"
    assert sibling_created_kwargs[2]["parent_task_id"] == parent.id
    assert sibling_created_kwargs[2]["source"] == "rerun_bump_turns:button"
    assert sibling_created_kwargs[2]["agent_max_turns"] == 55

    lineage_kwargs = event_calls[1].args
    assert lineage_kwargs[0] == parent.id
    assert lineage_kwargs[1] == "task.rerun_sibling_created"
    assert lineage_kwargs[2]["actor_id"] == "owner-1"
    assert lineage_kwargs[2]["source"] == "button"
    assert lineage_kwargs[2]["agent_max_turns"] == 55
    assert lineage_kwargs[2]["base_turns"] == 25


@pytest.mark.asyncio
async def test_rerun_bump_turns_notifies_and_signals_sibling():
    parent = _task(agent_max_turns=25)
    stub = _rerun_stub(parent=parent)

    await stub._rerun_task_with_bumped_turns(
        parent,
        actor_id="owner-1",
        source="slash",
    )

    assert stub._notify.await_count == 1
    notify_args = stub._notify.await_args
    sibling_task = notify_args.args[0]
    notify_text = notify_args.args[1]
    assert sibling_task.id != parent.id
    assert "max_turns=55" in notify_text
    assert parent.id in notify_text

    assert stub._signal_status_by_id.await_count == 1
    signaled_task, signaled_status = stub._signal_status_by_id.await_args.args
    assert signaled_task.id == sibling_task.id
    assert signaled_status == "PENDING"
    assert stub._task_sources[sibling_task.id] == "rerun_bump_turns:slash"


def test_disable_actions_for_failed_task_exposes_rerun_button():
    failed = _task(status="FAILED")
    assert TaskService.disable_actions(failed) == ["rerun_bump_turns"]


def test_disable_actions_for_non_failed_task_does_not_expose_rerun_button():
    for status in ("RUNNING", "COMPLETED", "MERGED", "PAUSED", "DISCARDED"):
        assert "rerun_bump_turns" not in TaskService.disable_actions(_task(status=status))


@pytest.mark.asyncio
async def test_fail_surfaces_rerun_button_only_on_max_turns():
    """Smoke test: verify the max_turns branch gates the surface call."""
    from oh_my_agent.runtime.service import RuntimeService as RS

    parent = _task(status="RUNNING")

    async def _make_stub(response: AgentResponse | None):
        stub = SimpleNamespace()
        stub._store = SimpleNamespace(
            update_runtime_task=AsyncMock(return_value=None),
            add_runtime_event=AsyncMock(return_value=None),
            upsert_automation_state=AsyncMock(return_value=None),
        )
        stub._notify = AsyncMock(return_value=None)
        stub._signal_status_by_id = AsyncMock(return_value=None)
        stub._format_agent_failure_text = lambda r, prefix: f"{prefix} {r.error or ''}"
        stub._surface_rerun_bump_turns_button = AsyncMock(return_value=None)
        stub._emit_automation_terminal_push = MagicMock(return_value=None)
        stub._fail = MethodType(RS._fail, stub)
        await stub._fail(parent, "boom", response=response)
        return stub

    surfaced = await _make_stub(AgentResponse(text="", error="max_turns", error_kind="max_turns"))
    assert surfaced._surface_rerun_bump_turns_button.await_count == 1

    not_surfaced = await _make_stub(AgentResponse(text="", error="boom", error_kind="cli_error"))
    assert not_surfaced._surface_rerun_bump_turns_button.await_count == 0

    no_response = await _make_stub(None)
    assert no_response._surface_rerun_bump_turns_button.await_count == 0


@pytest.mark.asyncio
async def test_surface_button_includes_owner_mentions_and_ttl():
    """Regression: the rerun button surface must @-mention configured owners
    so the thread actually produces a notification, and advertise its TTL so
    users know how long they have to click it."""
    from oh_my_agent.runtime.service import RuntimeService as RS

    parent = _task(agent_max_turns=25)
    stub = SimpleNamespace()
    stub._store = SimpleNamespace(
        create_runtime_decision_nonce=AsyncMock(return_value="nonce-xyz"),
    )
    channel = SimpleNamespace(render_user_mention=lambda uid: f"<@{uid}>")
    session = SimpleNamespace(channel=channel)
    stub._session_for = lambda task: session
    stub._owner_user_ids = {"111", "222"}
    stub._decision_ttl_minutes = 1440

    captured: dict = {}

    async def _capture(session_arg, thread_id, text, task_id, nonce, actions):
        captured["text"] = text
        captured["actions"] = actions
        captured["task_id"] = task_id
        captured["nonce"] = nonce

    stub._send_decision_surface = _capture
    stub._surface_rerun_bump_turns_button = MethodType(
        RS._surface_rerun_bump_turns_button, stub
    )

    await stub._surface_rerun_bump_turns_button(parent)

    text = captured["text"]
    assert "<@111>" in text and "<@222>" in text, text
    assert text.index("<@111>") < text.index("Task `"), "mentions must precede body"
    assert "expires in ~24h" in text, text
    assert captured["actions"] == ["rerun_bump_turns"]
    assert captured["nonce"] == "nonce-xyz"


@pytest.mark.asyncio
async def test_surface_button_omits_mentions_when_no_owners_configured():
    """When ``owner_user_ids`` is empty, the surface text must not contain a
    stray leading space or partial mention syntax."""
    from oh_my_agent.runtime.service import RuntimeService as RS

    parent = _task(agent_max_turns=25)
    stub = SimpleNamespace()
    stub._store = SimpleNamespace(
        create_runtime_decision_nonce=AsyncMock(return_value="nonce"),
    )
    channel = SimpleNamespace(render_user_mention=lambda uid: f"<@{uid}>")
    session = SimpleNamespace(channel=channel)
    stub._session_for = lambda task: session
    stub._owner_user_ids = set()
    stub._decision_ttl_minutes = 60

    captured: dict = {}

    async def _capture(session_arg, thread_id, text, task_id, nonce, actions):
        captured["text"] = text

    stub._send_decision_surface = _capture
    stub._surface_rerun_bump_turns_button = MethodType(
        RS._surface_rerun_bump_turns_button, stub
    )

    await stub._surface_rerun_bump_turns_button(parent)

    text = captured["text"]
    assert not text.startswith(" "), f"leading space: {text!r}"
    assert "<@" not in text
    assert "expires in ~1h" in text
