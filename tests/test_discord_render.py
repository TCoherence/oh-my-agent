from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from oh_my_agent.gateway.platforms.discord import DiscordChannel, _relative_time
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


def test_list_view_enabled_shows_timing_detail_line():
    info = _automation_info(
        next_run_at="2999-01-01T00:00:00Z",
        last_run_at="2020-01-01T00:00:00Z",
        last_success_at="2020-01-01T00:00:00Z",
        skill_name="market-briefing-ai",
    )
    result = AutomationStatusResult(success=True, message="ok", automations=[info])

    output = _channel()._render_automation_status_result(result)

    assert "skill `market-briefing-ai`" in output
    assert "next `in " in output  # far-future timestamp → "in <n>d"
    assert "last run `" in output
    assert "ago`" in output  # far-past timestamp → "<n>d ago"


def test_list_view_enabled_shows_error_snippet_not_just_emoji():
    info = _automation_info(
        last_error="HTTP 502 from upstream provider while fetching the feed"
    )
    result = AutomationStatusResult(success=True, message="ok", automations=[info])

    output = _channel()._render_automation_status_result(result)

    assert "⚠️ HTTP 502 from upstream provider" in output


def test_list_view_enabled_truncates_with_overflow_note():
    infos = [_automation_info(name=f"auto-{i}") for i in range(13)]
    result = AutomationStatusResult(success=True, message="ok", automations=infos)

    output = _channel()._render_automation_status_result(result)

    assert "…and 3 more enabled" in output  # cap 10, 13 enabled


def test_list_view_disabled_shows_skill():
    info = _automation_info(enabled=False, skill_name="paper-digest")
    result = AutomationStatusResult(success=True, message="ok", automations=[info])

    output = _channel()._render_automation_status_result(result)

    assert "**Disabled**" in output
    assert "skill `paper-digest`" in output


def test_list_view_keeps_delivery_target():
    info = _automation_info(target="channel `999`")
    result = AutomationStatusResult(success=True, message="ok", automations=[info])

    output = _channel()._render_automation_status_result(result)

    assert "channel `999`" in output


def test_list_view_truncates_below_discord_cap():
    """Many automations with long names + errors must never exceed the
    1900-char hard slice (Discord rejects > 2000)."""

    infos = [
        _automation_info(
            name=f"automation-with-a-fairly-long-name-{i}",
            last_error="upstream returned HTTP 502 " * 10,
            skill_name="market-briefing-finance",
        )
        for i in range(40)
    ]
    result = AutomationStatusResult(
        success=True, message="ok", automations=infos, scheduler_timezone="PDT"
    )

    output = _channel()._render_automation_status_result(result)

    assert len(output) <= 1900


# --- _relative_time ------------------------------------------------------- #


def test_relative_time_missing_returns_dash():
    assert _relative_time(None) == "—"
    assert _relative_time("") == "—"


def test_relative_time_unparseable_returns_raw():
    assert _relative_time("not-a-timestamp") == "not-a-timestamp"


def test_relative_time_recent_past_is_just_now():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert _relative_time(ts) == "just now"


def test_relative_time_past_units():
    now = datetime.now(timezone.utc)
    assert _relative_time((now - timedelta(minutes=5)).isoformat()) == "5m ago"
    assert _relative_time((now - timedelta(hours=2)).isoformat()) == "2h ago"
    assert _relative_time((now - timedelta(days=3)).isoformat()) == "3d ago"


def test_relative_time_future_reads_in():
    ts = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    assert _relative_time(ts) == "in 3h"


def test_relative_time_naive_timestamp_assumed_utc():
    # No tzinfo and no 'Z' — runtime persists naive UTC strings.
    naive = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    assert _relative_time(naive) == "2h ago"


def test_relative_time_handles_z_suffix():
    z = (datetime.now(timezone.utc) - timedelta(days=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert _relative_time(z) == "2d ago"
