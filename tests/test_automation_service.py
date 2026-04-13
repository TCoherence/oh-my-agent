from __future__ import annotations

from pathlib import Path

import pytest

from oh_my_agent.gateway.services.automation_service import AutomationService
from oh_my_agent.runtime.types import AutomationRuntimeState


class _AutomationRecord:
    def __init__(self, name: str, enabled: bool = True):
        self.name = name
        self.enabled = enabled
        self.cron = None
        self.interval_seconds = 3600
        self.delivery = "channel"
        self.thread_id = None
        self.target_user_id = None
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
    assert len(all_rows.automations) == 2


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
