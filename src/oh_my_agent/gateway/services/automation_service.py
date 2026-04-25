from __future__ import annotations

from typing import TYPE_CHECKING

from oh_my_agent.gateway.services.types import AutomationInfo, AutomationStatusResult, ServiceResult

if TYPE_CHECKING:
    from oh_my_agent.automation.scheduler import Scheduler
    from oh_my_agent.memory.store import MemoryStore


class AutomationService:
    def __init__(self, scheduler: Scheduler | None, memory_store: MemoryStore | None = None):
        self._scheduler = scheduler
        self._store = memory_store

    async def get_status(self, name: str | None = None) -> AutomationStatusResult:
        if self._scheduler is None:
            return AutomationStatusResult(success=False, message="Automation scheduler is not enabled.")
        scheduler_timezone = getattr(self._scheduler, "timezone_name", None)
        states = await self._load_runtime_states()
        if name:
            record = self._scheduler.get_automation(name.strip())
            if record is None:
                return AutomationStatusResult(success=False, message=f"Automation `{name}` not found.")
            return AutomationStatusResult(
                success=True,
                message=f"Found automation `{record.name}`.",
                automations=[self._to_info(record, states.get(record.name))],
                scheduler_timezone=scheduler_timezone,
            )
        records = self._scheduler.list_automations()
        return AutomationStatusResult(
            success=True,
            message=f"Found {len(records)} automation(s).",
            automations=[self._to_info(record, states.get(record.name)) for record in records],
            scheduler_timezone=scheduler_timezone,
        )

    async def reload(self) -> ServiceResult:
        if self._scheduler is None:
            return ServiceResult(success=False, message="Automation scheduler is not enabled.")
        summary = await self._scheduler.reload_now()
        return ServiceResult(
            success=True,
            message=(
                "**Automation reload complete**\n"
                f"- Visible: {summary['visible']}\n"
                f"- Active: {summary['active']}\n"
                f"- Added: {summary['added']}\n"
                f"- Updated: {summary['updated']}\n"
                f"- Removed: {summary['removed']}\n"
                "_Invalid or conflicting automation files remain log-visible only._"
            )[:1900],
        )

    async def fire(self, name: str) -> ServiceResult:
        """Manually fire an automation job by name."""
        if self._scheduler is None:
            return ServiceResult(success=False, message="Automation scheduler is not enabled.")
        record = self._scheduler.get_automation(name.strip())
        if record is None:
            return ServiceResult(success=False, message=f"Automation `{name}` not found.")
        if not record.enabled:
            return ServiceResult(success=False, message=f"Automation `{name}` is disabled. Enable it first.")
        result = await self._scheduler.fire_job_now(name.strip())
        if result == "ok":
            return ServiceResult(
                success=True,
                message=f"Dispatched `{name}` — fire queued, watch the channel for the result.",
            )
        if result == "already_firing":
            return ServiceResult(
                success=False,
                message=f"Automation `{name}` is already firing — manual run skipped.",
            )
        if result == "scheduler_down":
            return ServiceResult(success=False, message="Scheduler is not running.")
        return ServiceResult(success=False, message=f"Automation `{name}` not found.")

    async def set_enabled(self, name: str, enabled: bool) -> AutomationStatusResult:
        if self._scheduler is None:
            return AutomationStatusResult(success=False, message="Automation scheduler is not enabled.")
        try:
            record = await self._scheduler.set_automation_enabled(name.strip(), enabled=enabled)
        except ValueError as exc:
            return AutomationStatusResult(success=False, message=str(exc))
        states = await self._load_runtime_states()
        return AutomationStatusResult(
            success=True,
            message=f"Automation `{record.name}` {'enabled' if enabled else 'disabled'}.",
            automations=[self._to_info(record, states.get(record.name))],
        )

    async def _load_runtime_states(self) -> dict[str, object]:
        if self._store is None or not hasattr(self._store, "list_automation_states"):
            return {}
        try:
            return {state.name: state for state in await self._store.list_automation_states()}
        except Exception:
            return {}

    @staticmethod
    def _to_info(record, runtime_state) -> AutomationInfo:
        return AutomationInfo(
            name=record.name,
            enabled=record.enabled,
            schedule=(f"cron `{record.cron}`" if record.cron else f"interval `{record.interval_seconds}s`"),
            delivery=record.delivery,
            target=AutomationService._format_target(record),
            agent=record.agent or "fallback",
            skill_name=record.skill_name,
            timeout_seconds=getattr(record, "timeout_seconds", None),
            max_turns=getattr(record, "max_turns", None),
            last_run_at=getattr(runtime_state, "last_run_at", None),
            last_success_at=getattr(runtime_state, "last_success_at", None),
            last_error=getattr(runtime_state, "last_error", None),
            last_task_id=getattr(runtime_state, "last_task_id", None),
            next_run_at=getattr(runtime_state, "next_run_at", None),
            author=record.author,
            source_path=str(record.source_path) if record.source_path else None,
        )

    @staticmethod
    def _format_target(record) -> str:
        if record.delivery == "dm":
            return f"dm user `{record.target_user_id or '?'}` via channel `{record.channel_id}`"
        if record.thread_id:
            return f"channel `{record.channel_id}` thread `{record.thread_id}`"
        return f"channel `{record.channel_id}`"
