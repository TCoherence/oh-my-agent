from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from oh_my_agent.gateway.services.automation_service import AutomationService
from oh_my_agent.runtime.types import (
    TASK_COMPLETION_MERGE,
    TASK_TYPE_REPO_CHANGE,
    AutomationRuntimeState,
    RuntimeTask,
)


def _runtime_task(**overrides: Any) -> RuntimeTask:
    """Build a RuntimeTask with sensible defaults so tests only set what matters."""
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
        "step_no": 0,
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


class _AutomationRecord:
    def __init__(self, name: str, enabled: bool = True):
        self.name = name
        self.enabled = enabled
        self.cron = None
        self.interval_seconds = 3600
        self.delivery = "channel"
        self.thread_id = None
        self.target_user_id = None
        self.platform = "discord"
        self.channel_id = "100"
        self.agent = "codex"
        self.author = "scheduler"
        self.skill_name = "daily-brief"
        self.timeout_seconds = 900
        self.max_turns = 40
        self.source_path = Path("/tmp") / f"{name}.yaml"


class _SchedulerStub:
    def __init__(self):
        self.timezone_name = "America/Los_Angeles"
        self.records = {
            "daily": _AutomationRecord("daily", True),
            "paused": _AutomationRecord("paused", False),
        }

    def get_automation(self, name: str):
        return self.records.get(name)

    def list_automations(self):
        return list(self.records.values())

    async def reload_now(self):
        return {"visible": 2, "active": 1, "added": 0, "updated": 1, "removed": 0}

    async def set_automation_enabled(self, name: str, *, enabled: bool):
        if name not in self.records:
            raise ValueError(f"automation {name!r} not found")
        self.records[name].enabled = enabled
        return self.records[name]


class _StoreStub:
    def __init__(self, tasks: list[RuntimeTask] | None = None):
        if tasks is None:
            tasks = [
                _runtime_task(id="r-1", automation_name="daily", status="RUNNING"),
                _runtime_task(id="r-2", automation_name="daily", status="COMPLETED"),
                _runtime_task(id="r-3", automation_name="other", status="RUNNING"),
            ]
        self._tasks = tasks
        self.calls: list[tuple[str, str]] = []

    async def list_automation_states(self):
        return [
            AutomationRuntimeState(
                name="daily",
                platform="discord",
                channel_id="100",
                enabled=True,
                last_run_at="2026-04-11T10:00:00Z",
                last_success_at="2026-04-11T10:01:00Z",
                last_error=None,
                last_task_id="task-1",
                next_run_at="2026-04-11T11:00:00Z",
                updated_at=None,
            )
        ]

    async def list_runtime_tasks(self, *, platform: str, channel_id: str, limit: int = 20, status: str | None = None):
        self.calls.append((platform, channel_id))
        if platform != "discord" or channel_id != "100":
            return []
        return list(self._tasks)


@pytest.mark.asyncio
async def test_get_status_supports_single_name_and_all():
    service = AutomationService(_SchedulerStub(), _StoreStub())

    single = await service.get_status(name="daily")
    all_rows = await service.get_status()

    assert single.success is True
    assert single.automations[0].last_task_id == "task-1"
    assert single.scheduler_timezone == "America/Los_Angeles"
    assert single.automations[0].timeout_seconds == 900
    assert single.automations[0].max_turns == 40
    assert len(single.automations[0].active_tasks) == 1
    assert single.automations[0].active_tasks[0].id == "r-1"
    assert len(all_rows.automations) == 2
    by_name = {info.name: info for info in all_rows.automations}
    assert len(by_name["daily"].active_tasks) == 1
    assert by_name["paused"].active_tasks == []


@pytest.mark.asyncio
async def test_get_status_filters_active_tasks_by_name_and_status():
    service = AutomationService(_SchedulerStub(), _StoreStub())

    result = await service.get_status(name="daily")

    daily = result.automations[0]
    assert [task.id for task in daily.active_tasks] == ["r-1"]


@pytest.mark.asyncio
async def test_get_status_handles_missing_store_for_active_tasks():
    service = AutomationService(_SchedulerStub(), memory_store=None)

    result = await service.get_status(name="daily")

    assert result.success is True
    assert result.automations[0].active_tasks == []


@pytest.mark.asyncio
async def test_get_status_queries_with_record_platform_and_channel():
    store = _StoreStub()
    service = AutomationService(_SchedulerStub(), store)

    await service.get_status()

    assert ("discord", "100") in store.calls


@pytest.mark.asyncio
async def test_reload_returns_summary_message():
    service = AutomationService(_SchedulerStub(), _StoreStub())

    result = await service.reload()

    assert result.success is True
    assert "Automation reload complete" in result.message


@pytest.mark.asyncio
async def test_set_enabled_toggles_record():
    scheduler = _SchedulerStub()
    service = AutomationService(scheduler, _StoreStub())

    result = await service.set_enabled("paused", enabled=True)

    assert result.success is True
    assert result.automations[0].enabled is True
    assert result.automations[0].active_tasks == []
