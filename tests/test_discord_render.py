from __future__ import annotations

from typing import Any

from oh_my_agent.gateway.platforms.discord import DiscordChannel
from oh_my_agent.gateway.services.types import AutomationInfo, AutomationStatusResult
from oh_my_agent.runtime.types import (
    TASK_COMPLETION_MERGE,
    TASK_TYPE_REPO_CHANGE,
    RuntimeTask,
)


def _runtime_task(**overrides: Any) -> RuntimeTask:
    base: dict[str, Any] = {
        "id": "task-default",
        "platform": "discord",
        "channel_id": "100",
        "thread_id": "",
        "created_by": "scheduler",
        "goal": "scheduled run",
        "original_request": None,
        "preferred_agent": None,
        "status": "RUNNING",
        "step_no": 2,
        "max_steps": 8,
        "max_minutes": 20,
        "agent_timeout_seconds": None,
        "agent_max_turns": None,
        "test_command": "pytest -q",
        "workspace_path": None,
        "decision_message_id": None,
        "status_message_id": None,
        "blocked_reason": None,
        "error": None,
        "summary": None,
        "resume_instruction": None,
        "merge_commit_hash": None,
        "merge_error": None,
        "completion_mode": TASK_COMPLETION_MERGE,
        "output_summary": None,
        "artifact_manifest": None,
        "automation_name": "daily",
        "workspace_cleaned_at": None,
        "created_at": "2026-04-26T17:00:00Z",
        "started_at": "2026-04-26T17:00:01Z",
        "updated_at": None,
        "ended_at": None,
        "task_type": TASK_TYPE_REPO_CHANGE,
        "skill_name": None,
        "notify_channel_id": None,
    }
    base.update(overrides)
    return RuntimeTask(**base)


def _automation_info(**overrides: Any) -> AutomationInfo:
    base: dict[str, Any] = {
        "name": "daily",
        "enabled": True,
        "schedule": "cron `0 10 * * 0`",
        "delivery": "channel",
        "target": "channel `100`",
        "agent": "claude",
    }
    base.update(overrides)
    return AutomationInfo(**base)


def _channel() -> DiscordChannel:
    return DiscordChannel(token="x", channel_id="100")


def test_named_view_renders_active_tasks_block():
    info = _automation_info(active_tasks=[_runtime_task(id="r-running-1")])
    result = AutomationStatusResult(
        success=True,
        message="Found automation `daily`.",
        automations=[info],
        scheduler_timezone="PDT",
    )

    output = _channel()._render_automation_status_result(result, name="daily")

    assert "**Active tasks** (1)" in output
    assert "r-running-1" in output
    assert "[RUNNING]" in output
    assert "step 2" in output


def test_named_view_omits_active_tasks_when_empty():
    info = _automation_info()
    result = AutomationStatusResult(
        success=True,
        message="Found automation `daily`.",
        automations=[info],
    )

    output = _channel()._render_automation_status_result(result, name="daily")

    assert "**Active tasks**" not in output


def test_named_view_renders_active_tasks_without_runtime_state():
    info = _automation_info(active_tasks=[_runtime_task(id="r-fresh")])
    result = AutomationStatusResult(
        success=True,
        message="Found automation `daily`.",
        automations=[info],
    )

    output = _channel()._render_automation_status_result(result, name="daily")

    assert "**Active tasks** (1)" in output
    assert "**Runtime state**" not in output


def test_named_view_active_tasks_falls_back_to_created_at_label():
    info = _automation_info(
        active_tasks=[
            _runtime_task(
                id="r-pending",
                status="DRAFT",
                step_no=0,
                started_at=None,
                created_at="2026-04-26T17:05:00Z",
            )
        ]
    )
    result = AutomationStatusResult(success=True, message="ok", automations=[info])

    output = _channel()._render_automation_status_result(result, name="daily")

    assert "created `2026-04-26T17:05:00Z`" in output


def test_list_view_marks_active_count_for_enabled_records():
    info = _automation_info(
        active_tasks=[_runtime_task(id="r-1"), _runtime_task(id="r-2")]
    )
    result = AutomationStatusResult(
        success=True,
        message="ok",
        automations=[info],
    )

    output = _channel()._render_automation_status_result(result)

    assert "· 2 active" in output


def test_list_view_marks_active_count_for_disabled_records():
    info = _automation_info(enabled=False, active_tasks=[_runtime_task(id="r-1")])
    result = AutomationStatusResult(
        success=True,
        message="ok",
        automations=[info],
    )

    output = _channel()._render_automation_status_result(result)

    assert "**Disabled**" in output
    assert "· 1 active" in output


def test_list_view_omits_active_marker_when_empty():
    info = _automation_info()
    result = AutomationStatusResult(success=True, message="ok", automations=[info])

    output = _channel()._render_automation_status_result(result)

    assert "active" not in output
