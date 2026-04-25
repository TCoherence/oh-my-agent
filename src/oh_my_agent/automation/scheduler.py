from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Awaitable, Callable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from oh_my_agent.config import _substitute

logger = logging.getLogger(__name__)

_MONTH_NAMES = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

_WEEKDAY_NAMES = {
    "SUN": 0,
    "MON": 1,
    "TUE": 2,
    "WED": 3,
    "THU": 4,
    "FRI": 5,
    "SAT": 6,
}


FireJobResult = Literal["ok", "not_found", "scheduler_down", "already_firing"]


@dataclass(frozen=True)
class DumpChannelConfig:
    """Named dump/notify channel used for automation completion messages.

    Looked up by ``target_channel`` in an automation YAML; the resolved
    ``channel_id`` is threaded through the runtime task as ``notify_channel_id``
    and rewrites the destination of completion/archive messages while DRAFT,
    approval, and progress messages stay on the source channel.
    """

    platform: str
    channel_id: str


@dataclass(frozen=True)
class ScheduledJob:
    """Single periodic automation job."""

    name: str
    platform: str
    channel_id: str
    prompt: str
    delivery: str = "channel"  # "channel" | "dm"
    thread_id: str | None = None
    target_user_id: str | None = None
    agent: str | None = None
    author: str = "scheduler"
    cron: str | None = None
    interval_seconds: int | None = None
    initial_delay_seconds: int = 0
    source_path: Path | None = None
    skill_name: str | None = None
    timeout_seconds: int | None = None
    max_turns: int | None = None
    auto_approve: bool = False
    notify_channel_id: str | None = None

    @property
    def schedule_kind(self) -> str:
        return "cron" if self.cron else "interval"


@dataclass(frozen=True)
class AutomationRecord:
    name: str
    platform: str
    channel_id: str
    prompt: str
    enabled: bool
    delivery: str = "channel"
    thread_id: str | None = None
    target_user_id: str | None = None
    agent: str | None = None
    author: str = "scheduler"
    cron: str | None = None
    interval_seconds: int | None = None
    initial_delay_seconds: int = 0
    source_path: Path | None = None
    skill_name: str | None = None
    timeout_seconds: int | None = None
    max_turns: int | None = None
    auto_approve: bool = False
    notify_channel_id: str | None = None

    @property
    def schedule_kind(self) -> str:
        return "cron" if self.cron else "interval"

    def to_job(self) -> ScheduledJob:
        return ScheduledJob(
            name=self.name,
            platform=self.platform,
            channel_id=self.channel_id,
            prompt=self.prompt,
            delivery=self.delivery,
            thread_id=self.thread_id,
            target_user_id=self.target_user_id,
            agent=self.agent,
            author=self.author,
            cron=self.cron,
            interval_seconds=self.interval_seconds,
            initial_delay_seconds=self.initial_delay_seconds,
            source_path=self.source_path,
            skill_name=self.skill_name,
            timeout_seconds=self.timeout_seconds,
            max_turns=self.max_turns,
            auto_approve=self.auto_approve,
            notify_channel_id=self.notify_channel_id,
        )


@dataclass(frozen=True)
class _ParsedAutomation:
    record: AutomationRecord
    enabled: bool


@dataclass(frozen=True)
class _CronSpec:
    minute: frozenset[int]
    hour: frozenset[int]
    day: frozenset[int]
    month: frozenset[int]
    weekday: frozenset[int]
    day_wildcard: bool
    weekday_wildcard: bool


@dataclass
class JobRuntimeState:
    """Liveness state for one scheduled job. Updated by the central due loop."""

    name: str
    phase: str  # "sleeping" | "firing"
    next_fire_at: datetime | None
    fire_started_at: datetime | None
    last_progress_at: datetime
    last_restart_at: datetime | None = None
    last_restart_reason: str | None = None
    restart_in_progress: bool = False


@dataclass
class ReloadRuntimeState:
    """Liveness state for the file-watch reload loop."""

    last_progress_at: datetime
    last_restart_at: datetime | None = None
    last_restart_reason: str | None = None
    restart_in_progress: bool = False


@dataclass
class DueLoopRuntimeState:
    """Liveness state for the central due-loop scanner."""

    last_progress_at: datetime
    last_restart_at: datetime | None = None
    last_restart_reason: str | None = None
    restart_in_progress: bool = False


@dataclass(frozen=True)
class HealthFinding:
    """One stale observation from evaluate_job_health.

    Scopes:
      - "job": per-job sleeping/firing finding. ``reason="missed_fire"`` is
        **informational only** (no auto-restart) under the central due-loop
        model — overdue jobs are recovered on the next due-loop tick.
      - "reload": reload-loop staleness or task done. Restart via
        ``Scheduler.restart_reload_loop()``.
      - "due_loop": due-loop staleness or task done. Restart via
        ``Scheduler.restart_due_loop()``.
    """

    scope: str  # "job" | "reload" | "due_loop"
    reason: str  # "task_done_unexpectedly" | "missed_fire" | "no_progress"
    name: str | None = None  # job name; None for reload/due_loop scope


_DEFAULT_MIN_RESTART_INTERVAL_SECONDS = 120.0
_DEFAULT_STALE_GRACE_SECONDS = 90.0
_DEFAULT_RELOAD_STALE_FACTOR = 10.0  # reload loop stale if no progress for 10x reload_interval_seconds
_DEFAULT_DUE_LOOP_MAX_TICK_SECONDS = 30.0
_DEFAULT_DUE_LOOP_STALE_FACTOR = 5.0  # due loop stale if no progress for 5x max_tick
_DEFAULT_DUE_LOOP_MIN_SLEEP_SECONDS = 0.05


class Scheduler:
    """File-driven scheduler with polling-based hot reload.

    Architecture: a single central *due loop* scans ``_job_state`` on every
    tick (≤ ``due_loop_max_tick_seconds``) and dispatches a short-lived fire
    task for each job whose ``next_fire_at`` has passed wall-clock now.
    Per-job long-sleeping asyncio tasks are intentionally avoided so that
    monotonic-clock pauses (e.g. Docker-on-Mac host suspend) cannot cause
    fires to be silently skipped — recovery is bounded by one tick.
    """

    def __init__(
        self,
        *,
        storage_dir: Path,
        reload_interval_seconds: float,
        default_target_user_id: str | None = None,
        timezone: tzinfo | None = None,
        timezone_name: str | None = None,
        dump_channels: dict[str, "DumpChannelConfig"] | None = None,
    ) -> None:
        self._storage_dir = storage_dir.expanduser().resolve()
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._reload_interval_seconds = float(reload_interval_seconds)
        self._default_target_user_id = default_target_user_id
        self._timezone = timezone or _resolve_local_timezone()
        self._timezone_name = timezone_name or _describe_timezone(self._timezone)
        self._dump_channels: dict[str, DumpChannelConfig] = dict(dump_channels or {})
        self._records_by_name: dict[str, AutomationRecord] = {}
        self._jobs_by_name: dict[str, ScheduledJob] = {}
        self._duplicate_paths_by_name: dict[str, tuple[Path, ...]] = {}
        # Holds in-flight fire tasks only. Sleeping jobs live in _job_state.
        self._fire_tasks: dict[str, asyncio.Task] = {}
        self._job_state: dict[str, JobRuntimeState] = {}
        self._reload_state: ReloadRuntimeState | None = None
        self._reload_task: asyncio.Task | None = None
        self._due_loop_state: DueLoopRuntimeState | None = None
        self._due_loop_task: asyncio.Task | None = None
        self._due_loop_wakeup: asyncio.Event = asyncio.Event()
        self.due_loop_max_tick_seconds: float = _DEFAULT_DUE_LOOP_MAX_TICK_SECONDS
        self._snapshot: dict[Path, tuple[int, int]] = {}
        self._reload_lock = asyncio.Lock()
        self._on_fire: Callable[[ScheduledJob], Awaitable[None]] | None = None
        self._on_reload: Callable[[], Awaitable[None]] | None = None
        self._stop_event = asyncio.Event()
        self._min_restart_interval_seconds: float = _DEFAULT_MIN_RESTART_INTERVAL_SECONDS
        self._stale_grace_seconds: float = _DEFAULT_STALE_GRACE_SECONDS
        self._load_from_disk(initial=True)

    @property
    def jobs(self) -> list[ScheduledJob]:
        return [self._jobs_by_name[name] for name in sorted(self._jobs_by_name)]

    @property
    def storage_dir(self) -> Path:
        return self._storage_dir

    @property
    def timezone_name(self) -> str:
        return self._timezone_name

    def _now(self) -> datetime:
        """Single source of truth for wall-clock reads inside the scheduler."""
        return datetime.now(self._timezone)

    def compute_next_run_at(self, job: ScheduledJob) -> datetime | None:
        """Return the next fire time for *job* from now, or None for interval jobs on first fire."""
        now = self._now()
        if job.cron:
            spec = _parse_cron_expression(job.cron)
            return _next_cron_fire(spec, now)
        if job.interval_seconds:
            return now + timedelta(seconds=job.interval_seconds)
        return None

    def compute_all_next_run_at(self) -> dict[str, datetime | None]:
        """Return ``{name: next_fire_dt | None}`` for every *active* job."""
        return {name: self.compute_next_run_at(job) for name, job in self._jobs_by_name.items()}

    def compute_display_next_run_at(self, name: str) -> datetime | None:
        """Resolve the user-facing 'next run' time for *name*.

        Trusts the in-memory ``state.next_fire_at`` whenever the scheduler has
        an active runtime entry for the job. That value reflects what the
        scheduler actually intends to do next — including pinned manual-fire
        schedules that should not be displaced by a fresh ``now+interval``
        recomputation. Falls back to ``compute_job_next_run_at`` only when no
        runtime state exists (e.g. the job is disabled or scheduler hasn't
        started yet).
        """
        state = self._job_state.get(name)
        if state is not None and state.next_fire_at is not None:
            return state.next_fire_at
        return self.compute_job_next_run_at(name)

    def list_automations(self) -> list[AutomationRecord]:
        return [self._records_by_name[name] for name in sorted(self._records_by_name)]

    def get_automation(self, name: str) -> AutomationRecord | None:
        return self._records_by_name.get(name)

    async def reload_now(self) -> dict[str, int]:
        async with self._reload_lock:
            return await self._reload_now_locked()

    async def set_automation_enabled(self, name: str, *, enabled: bool) -> AutomationRecord:
        async with self._reload_lock:
            await self._reload_now_locked()
            if name in self._duplicate_paths_by_name:
                conflict_paths = ", ".join(str(path) for path in self._duplicate_paths_by_name[name])
                raise ValueError(
                    f"automation name conflict for {name!r}; resolve duplicate files first: {conflict_paths}"
                )

            record = self._records_by_name.get(name)
            if record is None or record.source_path is None:
                raise ValueError(f"automation {name!r} not found")

            source_path = record.source_path
            try:
                raw = yaml.safe_load(source_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ValueError(f"failed to read automation file {source_path}: {exc}") from exc

            if not isinstance(raw, dict):
                raise ValueError(f"automation file {source_path} must contain a YAML mapping")

            if bool(raw.get("enabled", True)) != enabled:
                raw["enabled"] = enabled
                source_path.write_text(
                    yaml.safe_dump(raw, sort_keys=False, allow_unicode=False),
                    encoding="utf-8",
                )

            await self._reload_now_locked()
            updated = self._records_by_name.get(name)
            if updated is None:
                raise ValueError(f"automation {name!r} is no longer visible after reload")
            return updated

    async def run(
        self,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        """Run the central due-loop scanner and reload loop until cancelled."""
        self._stop_event.clear()
        self._on_fire = on_fire
        now = self._now()
        self._reload_state = ReloadRuntimeState(last_progress_at=now)
        self._due_loop_state = DueLoopRuntimeState(last_progress_at=now)
        # Re-arm the wakeup event in the running event loop.
        self._due_loop_wakeup = asyncio.Event()
        for job in self.jobs:
            self._start_job(job)

        logger.info(
            "Scheduler watching %s (%d active job(s))",
            self._storage_dir,
            len(self._jobs_by_name),
        )

        self._reload_task = asyncio.create_task(self._reload_loop(on_fire), name="scheduler:reload")
        self._due_loop_task = asyncio.create_task(self._due_loop(on_fire), name="scheduler:due")
        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            raise
        finally:
            # 1. Cancel the due loop FIRST so no new fires get dispatched.
            if self._due_loop_task is not None:
                self._due_loop_task.cancel()
                await asyncio.gather(self._due_loop_task, return_exceptions=True)
                self._due_loop_task = None
            # 2. Cancel the reload loop.
            if self._reload_task is not None:
                self._reload_task.cancel()
                await asyncio.gather(self._reload_task, return_exceptions=True)
                self._reload_task = None
            # 3. Drain any in-flight fire tasks and clear state.
            await self._stop_all_jobs()

    def stop(self) -> None:
        self._stop_event.set()

    async def fire_job_now(self, name: str) -> FireJobResult:
        """Manually trigger a job by name. Returns a result code.

        Returns:
            "ok": dispatched (fire-and-forget; scheduled cadence preserved).
            "not_found": no such automation, or scheduler not initialized.
            "scheduler_down": scheduler is not running.
            "already_firing": a scheduled or earlier manual fire is in-flight;
                the manual fire is refused (no concurrent same-job runs).
        """
        job = self._jobs_by_name.get(name)
        state = self._job_state.get(name)
        if job is None or state is None:
            return "not_found"
        on_fire = self._on_fire
        if on_fire is None:
            return "scheduler_down"
        if state.phase == "firing":
            logger.info("fire_job_now: %r already firing — skipping manual fire", name)
            return "already_firing"
        logger.info("Manual fire for job %r", name)
        self._dispatch_due_job(name, on_fire, preserve_next_fire=True)
        return "ok"

    async def _reload_loop(
        self,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        del on_fire  # not used directly; _apply_snapshot consults self._on_fire
        while True:
            try:
                await asyncio.sleep(self._reload_interval_seconds)
                async with self._reload_lock:
                    snapshot = self._scan_snapshot()
                    if snapshot != self._snapshot:
                        await self._apply_snapshot(snapshot)
                self._touch_reload_progress()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Scheduler reload failed: %s", exc)

    def _touch_reload_progress(self) -> None:
        if self._reload_state is not None:
            self._reload_state.last_progress_at = self._now()

    def _touch_due_loop_progress(self) -> None:
        if self._due_loop_state is not None:
            self._due_loop_state.last_progress_at = self._now()

    def _load_from_disk(
        self,
        *,
        initial: bool = False,
        snapshot: dict[Path, tuple[int, int]] | None = None,
    ) -> None:
        snapshot = snapshot or self._scan_snapshot()
        parsed: list[_ParsedAutomation] = []

        for path in sorted(snapshot, key=lambda item: str(item)):
            item = self._parse_automation_file(path)
            if item is not None:
                parsed.append(item)

        duplicates: dict[str, list[Path]] = {}
        for item in parsed:
            duplicates.setdefault(item.record.name, []).append(item.record.source_path or Path("<unknown>"))

        duplicate_names = {name for name, paths in duplicates.items() if len(paths) > 1}
        for name in sorted(duplicate_names):
            paths = ", ".join(str(path) for path in duplicates[name])
            logger.error(
                "Automation name conflict for %r; skipping all conflicting files: %s",
                name,
                paths,
            )

        records_by_name: dict[str, AutomationRecord] = {}
        jobs_by_name: dict[str, ScheduledJob] = {}
        for item in parsed:
            if item.record.name in duplicate_names:
                continue
            records_by_name[item.record.name] = item.record
            if item.enabled:
                jobs_by_name[item.record.name] = item.record.to_job()

        self._records_by_name = records_by_name
        self._jobs_by_name = jobs_by_name
        self._duplicate_paths_by_name = {
            name: tuple(paths) for name, paths in duplicates.items() if name in duplicate_names
        }
        self._snapshot = snapshot
        if initial:
            logger.info(
                "Loaded %d visible automation(s), %d active job(s) from %s",
                len(records_by_name),
                len(jobs_by_name),
                self._storage_dir,
            )

    def _scan_snapshot(self) -> dict[Path, tuple[int, int]]:
        snapshot: dict[Path, tuple[int, int]] = {}
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(
            [*self._storage_dir.glob("*.yaml"), *self._storage_dir.glob("*.yml")],
            key=lambda item: str(item),
        ):
            if not path.is_file():
                continue
            stat = path.stat()
            snapshot[path.resolve()] = (stat.st_mtime_ns, stat.st_size)
        return snapshot

    def _parse_automation_file(self, path: Path) -> _ParsedAutomation | None:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Failed to read automation file %s: %s", path, exc)
            return None

        if not isinstance(raw, dict):
            logger.error("Automation file %s must contain a YAML mapping", path)
            return None

        data = _substitute(raw)
        try:
            return self._build_parsed_automation(data, source_path=path)
        except ValueError as exc:
            logger.error("Invalid automation file %s: %s", path, exc)
            return None

    def _build_parsed_automation(
        self,
        raw: dict,
        *,
        source_path: Path,
    ) -> _ParsedAutomation:
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError("name is required")

        enabled = bool(raw.get("enabled", True))
        platform = str(raw.get("platform", "")).strip()
        channel_id = str(raw.get("channel_id", "")).strip()
        prompt = str(raw.get("prompt", "")).strip()
        if not platform:
            raise ValueError("platform is required")
        if not channel_id:
            raise ValueError("channel_id is required")
        if not prompt:
            raise ValueError("prompt is required")

        delivery = str(raw.get("delivery", "channel")).strip().lower()
        if delivery not in {"channel", "dm"}:
            raise ValueError("delivery must be 'channel' or 'dm'")

        target_user_id = None
        if delivery == "dm":
            target_user_id = (
                str(raw.get("target_user_id")).strip()
                if raw.get("target_user_id") is not None
                else None
            )
            if not target_user_id and self._default_target_user_id:
                target_user_id = self._default_target_user_id
            if not target_user_id:
                raise ValueError(
                    "target_user_id is required for delivery='dm' unless access.owner_user_ids is configured"
                )

        cron = str(raw.get("cron")).strip() if raw.get("cron") is not None else None
        interval_seconds = raw.get("interval_seconds")
        if cron and interval_seconds is not None:
            raise ValueError("cron and interval_seconds are mutually exclusive")
        if not cron and interval_seconds is None:
            raise ValueError("one of cron or interval_seconds is required")

        interval_value: int | None = None
        initial_delay_seconds = 0
        if cron:
            if "initial_delay_seconds" in raw:
                raise ValueError("initial_delay_seconds is not supported with cron")
            _parse_cron_expression(cron)
        else:
            interval_value = int(interval_seconds)
            if interval_value <= 0:
                raise ValueError("interval_seconds must be > 0")
            initial_delay_seconds = int(raw.get("initial_delay_seconds", 0))
            if initial_delay_seconds < 0:
                raise ValueError("initial_delay_seconds must be >= 0")

        skill_name = str(raw["skill_name"]).strip() if raw.get("skill_name") else None
        timeout_seconds = _parse_positive_optional_int(raw.get("timeout_seconds"), field_name="timeout_seconds")
        max_turns = _parse_positive_optional_int(raw.get("max_turns"), field_name="max_turns")
        agent_name = (str(raw["agent"]).strip() if raw.get("agent") else None) or None
        if max_turns is not None and agent_name is not None and agent_name.lower() != "claude":
            logger.warning(
                "Automation %r configured max_turns=%s for agent=%r, but only Claude currently supports max_turns overrides",
                name,
                max_turns,
                agent_name,
            )

        auto_approve = bool(raw.get("auto_approve", False))

        notify_channel_id: str | None = None
        target_channel_raw = raw.get("target_channel")
        if target_channel_raw is not None:
            target_channel = str(target_channel_raw).strip()
            if target_channel:
                dump = self._dump_channels.get(target_channel)
                if dump is None:
                    raise ValueError(
                        f"target_channel {target_channel!r} is not configured under "
                        "automations.dump_channels"
                    )
                if dump.platform != platform:
                    raise ValueError(
                        f"target_channel {target_channel!r} platform {dump.platform!r} "
                        f"does not match automation platform {platform!r}"
                    )
                notify_channel_id = dump.channel_id

        record = AutomationRecord(
                name=name,
                platform=platform,
                channel_id=channel_id,
                prompt=prompt,
                enabled=enabled,
                delivery=delivery,
                thread_id=(str(raw["thread_id"]) if raw.get("thread_id") is not None else None),
                target_user_id=target_user_id,
                agent=agent_name,
                author=str(raw.get("author", "scheduler")),
                cron=cron,
                interval_seconds=interval_value,
                initial_delay_seconds=initial_delay_seconds,
                source_path=source_path,
                skill_name=skill_name,
                timeout_seconds=timeout_seconds,
                max_turns=max_turns,
                auto_approve=auto_approve,
                notify_channel_id=notify_channel_id,
            )
        return _ParsedAutomation(
            record=record,
            enabled=enabled,
        )

    async def _reload_now_locked(self) -> dict[str, int]:
        snapshot = self._scan_snapshot()
        return await self._apply_snapshot(snapshot)

    async def _apply_snapshot(self, snapshot: dict[Path, tuple[int, int]]) -> dict[str, int]:
        old_jobs = dict(self._jobs_by_name)
        self._load_from_disk(snapshot=snapshot)

        added = {
            name for name in self._jobs_by_name.keys() - old_jobs.keys()
        }
        removed = {
            name for name in old_jobs.keys() - self._jobs_by_name.keys()
        }
        updated = {
            name
            for name in (old_jobs.keys() & self._jobs_by_name.keys())
            if old_jobs[name] != self._jobs_by_name[name]
        }

        if self._on_fire is not None:
            await self._reconcile_running_jobs(added, removed, updated, self._on_fire)

        if added or updated or removed:
            logger.info(
                "Scheduler reloaded visible=%d active=%d added=%d updated=%d removed=%d",
                len(self._records_by_name),
                len(self._jobs_by_name),
                len(added),
                len(updated),
                len(removed),
            )
            if self._on_reload is not None:
                try:
                    await self._on_reload()
                except Exception:
                    logger.exception("Scheduler on_reload callback failed")

        return {
            "visible": len(self._records_by_name),
            "active": len(self._jobs_by_name),
            "added": len(added),
            "updated": len(updated),
            "removed": len(removed),
        }

    async def _reconcile_running_jobs(
        self,
        added: set[str],
        removed: set[str],
        updated: set[str],
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        del on_fire  # central due loop owns dispatch; signature kept for symmetry
        for name in sorted(removed | updated):
            await self._stop_job(name)
        for name in sorted(added | updated):
            self._start_job(self._jobs_by_name[name])
        # Kick the due loop so the new schedule is picked up immediately.
        self._due_loop_wakeup.set()

    def _start_job(self, job: ScheduledJob) -> None:
        """Initialize ``JobRuntimeState`` for *job*. The central due loop dispatches fires."""
        now = self._now()
        if job.cron:
            spec = _parse_cron_expression(job.cron)
            initial_next_fire: datetime | None = _next_cron_fire(spec, now)
        else:
            initial_next_fire = now + timedelta(seconds=job.initial_delay_seconds)

        prior = self._job_state.get(job.name)
        self._job_state[job.name] = JobRuntimeState(
            name=job.name,
            phase="sleeping",
            next_fire_at=initial_next_fire,
            fire_started_at=None,
            last_progress_at=now,
            last_restart_at=prior.last_restart_at if prior else None,
            last_restart_reason=prior.last_restart_reason if prior else None,
        )
        self._due_loop_wakeup.set()

    async def _stop_job(self, name: str) -> None:
        # Order matters: pop the fire-task slot BEFORE cancelling, so the
        # task's race-guard sees the slot is no longer ours and won't
        # resurrect _job_state via _mark_job_sleeping.
        task = self._fire_tasks.pop(name, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # Drop state after the task is fully gone.
        self._job_state.pop(name, None)
        self._due_loop_wakeup.set()

    async def _stop_all_jobs(self) -> None:
        tasks = list(self._fire_tasks.values())
        self._fire_tasks.clear()
        self._job_state.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _mark_job_firing(self, name: str) -> None:
        state = self._job_state.get(name)
        if state is None:
            return
        now = self._now()
        state.phase = "firing"
        state.fire_started_at = now
        state.last_progress_at = now

    def _mark_job_sleeping(self, name: str, *, next_fire_at: datetime | None) -> None:
        state = self._job_state.get(name)
        if state is None:
            return
        now = self._now()
        state.phase = "sleeping"
        state.fire_started_at = None
        state.last_progress_at = now
        if next_fire_at is not None:
            state.next_fire_at = next_fire_at

    def _collect_due_jobs(self, now: datetime) -> list[str]:
        """Return names of sleeping jobs whose ``next_fire_at <= now``."""
        due: list[str] = []
        for name in sorted(self._job_state):
            state = self._job_state[name]
            if state.phase != "sleeping":
                continue
            if state.next_fire_at is None:
                continue
            if state.next_fire_at <= now:
                due.append(name)
        return due

    def _compute_next_fire_after_completion(
        self, job: ScheduledJob, post_now: datetime
    ) -> datetime:
        if job.cron:
            return _next_cron_fire(_parse_cron_expression(job.cron), post_now)
        interval = job.interval_seconds or 0
        return post_now + timedelta(seconds=interval)

    def _compute_due_loop_sleep(self, now: datetime) -> float:
        """Compute the next due-loop wait, bounded by ``due_loop_max_tick_seconds``.

        Only considers ``phase=="sleeping"`` jobs. Firing jobs may carry a
        stale ``next_fire_at`` from before — including them would make the
        loop spin at the lower bound until the fire completes.
        """
        max_tick = self.due_loop_max_tick_seconds
        soonest: datetime | None = None
        for state in self._job_state.values():
            if state.phase != "sleeping":
                continue
            if state.next_fire_at is None:
                continue
            if soonest is None or state.next_fire_at < soonest:
                soonest = state.next_fire_at
        if soonest is None:
            return max_tick
        delta = (soonest - now).total_seconds()
        if delta < _DEFAULT_DUE_LOOP_MIN_SLEEP_SECONDS:
            return _DEFAULT_DUE_LOOP_MIN_SLEEP_SECONDS
        return min(max_tick, delta)

    def _dispatch_due_job(
        self,
        name: str,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
        *,
        preserve_next_fire: bool = False,
    ) -> None:
        state = self._job_state.get(name)
        job = self._jobs_by_name.get(name)
        if state is None or job is None or state.phase == "firing":
            return
        # Capture the current scheduled next_fire so manual fires can
        # restore it after completion (instead of advancing the regular
        # cadence by the manual-fire's duration).
        pinned_next_fire = state.next_fire_at if preserve_next_fire else None
        self._mark_job_firing(name)
        task = asyncio.create_task(
            self._fire_job_once(job, on_fire, pinned_next_fire=pinned_next_fire),
            name=f"scheduler:fire:{name}",
        )
        self._fire_tasks[name] = task

    async def _fire_job_once(
        self,
        job: ScheduledJob,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
        *,
        pinned_next_fire: datetime | None = None,
    ) -> None:
        try:
            logger.info(
                "Scheduler firing job=%s schedule=%s platform=%s channel=%s thread=%s",
                job.name,
                job.cron or f"interval={job.interval_seconds}s",
                job.platform,
                job.channel_id,
                job.thread_id or "(new)",
            )
            await on_fire(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Scheduler job %r failed: %s", job.name, exc)
        finally:
            # Race guard: a reload/update may have replaced our slot
            # with a different task while we were running; in that case
            # the new task owns the entry — don't touch state here.
            current = asyncio.current_task()
            if self._fire_tasks.get(job.name) is current:
                self._fire_tasks.pop(job.name, None)
                if self._job_state.get(job.name) is not None:
                    post_now = self._now()
                    if pinned_next_fire is not None and pinned_next_fire > post_now:
                        # Manual fire didn't displace a future scheduled fire.
                        next_fire: datetime = pinned_next_fire
                    else:
                        next_fire = self._compute_next_fire_after_completion(job, post_now)
                    self._mark_job_sleeping(job.name, next_fire_at=next_fire)
                    self._due_loop_wakeup.set()

    async def _due_loop(
        self,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        while True:
            try:
                now = self._now()
                due_names = self._collect_due_jobs(now)
                for name in due_names:
                    self._dispatch_due_job(name, on_fire)
                self._touch_due_loop_progress()
                # Clear BEFORE compute so a wakeup arriving in the
                # window between compute and clear can't be silently
                # dropped — leading to a missed short-interval fire.
                self._due_loop_wakeup.clear()
                sleep_for = self._compute_due_loop_sleep(self._now())
                try:
                    await asyncio.wait_for(self._due_loop_wakeup.wait(), timeout=sleep_for)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler due_loop iteration failed")
                await asyncio.sleep(self.due_loop_max_tick_seconds)

    def list_job_runtime_state(self) -> list[JobRuntimeState]:
        """Return a snapshot (copy) of liveness state for every active job."""
        return [
            JobRuntimeState(
                name=state.name,
                phase=state.phase,
                next_fire_at=state.next_fire_at,
                fire_started_at=state.fire_started_at,
                last_progress_at=state.last_progress_at,
                last_restart_at=state.last_restart_at,
                last_restart_reason=state.last_restart_reason,
                restart_in_progress=state.restart_in_progress,
            )
            for state in (self._job_state[name] for name in sorted(self._job_state))
        ]

    def get_job_runtime_state(self, name: str) -> JobRuntimeState | None:
        """Return a snapshot (copy) of one job's liveness state, or None if unknown."""
        state = self._job_state.get(name)
        if state is None:
            return None
        return JobRuntimeState(
            name=state.name,
            phase=state.phase,
            next_fire_at=state.next_fire_at,
            fire_started_at=state.fire_started_at,
            last_progress_at=state.last_progress_at,
            last_restart_at=state.last_restart_at,
            last_restart_reason=state.last_restart_reason,
            restart_in_progress=state.restart_in_progress,
        )

    def get_reload_runtime_state(self) -> ReloadRuntimeState | None:
        """Return a snapshot (copy) of reload loop liveness, or None if scheduler not running."""
        state = self._reload_state
        if state is None:
            return None
        return ReloadRuntimeState(
            last_progress_at=state.last_progress_at,
            last_restart_at=state.last_restart_at,
            last_restart_reason=state.last_restart_reason,
            restart_in_progress=state.restart_in_progress,
        )

    def get_due_loop_runtime_state(self) -> DueLoopRuntimeState | None:
        """Return a snapshot (copy) of due-loop liveness, or None if scheduler not running."""
        state = self._due_loop_state
        if state is None:
            return None
        return DueLoopRuntimeState(
            last_progress_at=state.last_progress_at,
            last_restart_at=state.last_restart_at,
            last_restart_reason=state.last_restart_reason,
            restart_in_progress=state.restart_in_progress,
        )

    def compute_job_next_run_at(self, name: str) -> datetime | None:
        """Compute the next cron/interval fire time for a known job from now."""
        job = self._jobs_by_name.get(name)
        if job is None:
            return None
        return self.compute_next_run_at(job)

    def evaluate_job_health(self, now: datetime | None = None) -> list[HealthFinding]:
        """Read-only health evaluation. Returns stale findings for /doctor + supervisor.

        Job scope:
          - ``missed_fire`` — ``phase=="sleeping"`` past ``next_fire_at + grace``
            with ``last_progress_at < next_fire_at``. **Informational only**;
            the central due loop natively recovers overdue jobs on its next
            tick (no auto-restart). Surfaced here for /doctor visibility.

        Reload scope:
          - ``task_done_unexpectedly`` — reload task done while not stopped.
          - ``no_progress`` — reload loop hasn't ticked for
            ``reload_interval_seconds * _DEFAULT_RELOAD_STALE_FACTOR``.
          - Both restart via ``restart_reload_loop()``.

        Due-loop scope:
          - ``task_done_unexpectedly`` — due-loop task done while not stopped.
          - ``no_progress`` — due loop hasn't ticked for
            ``due_loop_max_tick_seconds * _DEFAULT_DUE_LOOP_STALE_FACTOR``.
          - Both restart via ``restart_due_loop()``.
        """
        if now is None:
            now = self._now()
        findings: list[HealthFinding] = []
        stop_set = self._stop_event.is_set()
        grace = timedelta(seconds=self._stale_grace_seconds)

        for name in sorted(self._job_state):
            state = self._job_state[name]
            if state.phase == "firing":
                continue
            if state.next_fire_at is None:
                continue
            if now > state.next_fire_at + grace and state.last_progress_at < state.next_fire_at:
                findings.append(HealthFinding(scope="job", name=name, reason="missed_fire"))

        reload_task = self._reload_task
        reload_state = self._reload_state
        if reload_state is not None:
            if not stop_set and reload_task is not None and reload_task.done():
                findings.append(
                    HealthFinding(
                        scope="reload", name=None, reason="task_done_unexpectedly"
                    )
                )
            else:
                stale_threshold = timedelta(
                    seconds=self._reload_interval_seconds * _DEFAULT_RELOAD_STALE_FACTOR
                )
                if now - reload_state.last_progress_at > stale_threshold:
                    findings.append(
                        HealthFinding(scope="reload", name=None, reason="no_progress")
                    )

        due_loop_task = self._due_loop_task
        due_loop_state = self._due_loop_state
        if due_loop_state is not None:
            if not stop_set and due_loop_task is not None and due_loop_task.done():
                findings.append(
                    HealthFinding(
                        scope="due_loop", name=None, reason="task_done_unexpectedly"
                    )
                )
            else:
                stale_threshold = timedelta(
                    seconds=self.due_loop_max_tick_seconds * _DEFAULT_DUE_LOOP_STALE_FACTOR
                )
                if now - due_loop_state.last_progress_at > stale_threshold:
                    findings.append(
                        HealthFinding(scope="due_loop", name=None, reason="no_progress")
                    )

        return findings

    async def restart_reload_loop(self, *, reason: str) -> bool:
        """Self-heal a stalled reload loop. Rate-limited by min_restart_interval."""
        reload_state = self._reload_state
        now = self._now()
        if reload_state is not None:
            if reload_state.restart_in_progress:
                logger.debug("restart_reload_loop: already in progress")
                return False
            if (
                reload_state.last_restart_at is not None
                and (now - reload_state.last_restart_at).total_seconds()
                < self._min_restart_interval_seconds
            ):
                logger.debug(
                    "restart_reload_loop: rate-limited (last=%s)",
                    reload_state.last_restart_at.isoformat(),
                )
                return False
            reload_state.restart_in_progress = True

        try:
            on_fire = self._on_fire
            if on_fire is None:
                logger.warning("restart_reload_loop: scheduler not running")
                return False

            logger.warning("Scheduler restart reload loop reason=%s", reason)
            task = self._reload_task
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            self._reload_task = asyncio.create_task(
                self._reload_loop(on_fire), name="scheduler:reload"
            )
            if reload_state is not None:
                reload_state.last_progress_at = now
                reload_state.last_restart_at = now
                reload_state.last_restart_reason = reason
            return True
        finally:
            if self._reload_state is not None:
                self._reload_state.restart_in_progress = False

    async def restart_due_loop(self, *, reason: str) -> bool:
        """Self-heal a stalled due-loop. Rate-limited by min_restart_interval."""
        on_fire = self._on_fire
        if on_fire is None:
            logger.warning("restart_due_loop: scheduler not running")
            return False
        due_state = self._due_loop_state
        now = self._now()
        if due_state is not None:
            if due_state.restart_in_progress:
                logger.debug("restart_due_loop: already in progress")
                return False
            if (
                due_state.last_restart_at is not None
                and (now - due_state.last_restart_at).total_seconds()
                < self._min_restart_interval_seconds
            ):
                logger.debug(
                    "restart_due_loop: rate-limited (last=%s)",
                    due_state.last_restart_at.isoformat(),
                )
                return False
            due_state.restart_in_progress = True

        try:
            logger.warning("Scheduler restart due_loop reason=%s", reason)
            task = self._due_loop_task
            if task is not None:
                task.cancel()
                # Must complete cancellation BEFORE creating the new task
                # to avoid two due loops dispatching simultaneously.
                await asyncio.gather(task, return_exceptions=True)

            self._due_loop_task = asyncio.create_task(
                self._due_loop(on_fire), name="scheduler:due"
            )
            if due_state is not None:
                due_state.last_progress_at = now
                due_state.last_restart_at = now
                due_state.last_restart_reason = reason
            # Kick the new loop into immediate iteration.
            self._due_loop_wakeup.set()
            return True
        finally:
            if self._due_loop_state is not None:
                self._due_loop_state.restart_in_progress = False


def build_scheduler_from_config(
    config: dict,
    *,
    default_target_user_id: str | None = None,
    project_root: Path | None = None,
) -> Scheduler | None:
    """Build the file-driven scheduler from global automation config."""
    sched_cfg = config.get("automations", {})
    if not sched_cfg.get("enabled", True):
        return None

    storage_dir = Path(str(sched_cfg.get("storage_dir", "~/.oh-my-agent/automations"))).expanduser()
    if not storage_dir.is_absolute():
        base = project_root or Path.cwd()
        storage_dir = (base / storage_dir).resolve()
    else:
        storage_dir = storage_dir.resolve()

    reload_interval_seconds = float(sched_cfg.get("reload_interval_seconds", 5))
    if reload_interval_seconds <= 0:
        raise ValueError("automations.reload_interval_seconds must be > 0")

    configured_timezone = sched_cfg.get("timezone")
    timezone_obj, timezone_name = _resolve_configured_timezone(configured_timezone)

    dump_channels = _parse_dump_channels(sched_cfg.get("dump_channels"))

    return Scheduler(
        storage_dir=storage_dir,
        reload_interval_seconds=reload_interval_seconds,
        default_target_user_id=default_target_user_id,
        timezone=timezone_obj,
        timezone_name=timezone_name,
        dump_channels=dump_channels,
    )


def _parse_dump_channels(raw: object) -> dict[str, DumpChannelConfig]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("automations.dump_channels must be a mapping")
    resolved: dict[str, DumpChannelConfig] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise ValueError(
                f"automations.dump_channels.{name} must be a mapping with platform/channel_id"
            )
        platform = str(entry.get("platform", "")).strip()
        channel_id = str(entry.get("channel_id", "")).strip()
        if not platform:
            raise ValueError(f"automations.dump_channels.{name}.platform is required")
        if not channel_id:
            raise ValueError(f"automations.dump_channels.{name}.channel_id is required")
        resolved[str(name)] = DumpChannelConfig(platform=platform, channel_id=channel_id)
    return resolved


def _resolve_local_timezone() -> tzinfo:
    local_tz = datetime.now().astimezone().tzinfo
    return local_tz or timezone.utc


def _describe_timezone(tz: tzinfo) -> str:
    key = getattr(tz, "key", None)
    if isinstance(key, str) and key:
        return key
    now = datetime.now(tz)
    name = now.tzname()
    if name:
        return name
    return str(tz)


def _resolve_configured_timezone(raw: object) -> tuple[tzinfo, str]:
    if raw is None:
        local_tz = _resolve_local_timezone()
        return local_tz, f"{_describe_timezone(local_tz)} (local default)"

    value = str(raw).strip()
    if not value or value.lower() == "local":
        local_tz = _resolve_local_timezone()
        return local_tz, f"{_describe_timezone(local_tz)} (local default)"

    try:
        tz = ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            "automations.timezone must be 'local' or a valid IANA timezone such as "
            "'America/Los_Angeles'"
        ) from exc
    return tz, value


def _parse_positive_optional_int(raw: object, *, field_name: str) -> int | None:
    if raw is None:
        return None
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return value


def _parse_cron_expression(expr: str) -> _CronSpec:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron must be a 5-field expression: minute hour day month weekday")

    minute, hour, day, month, weekday = parts
    return _CronSpec(
        minute=_parse_cron_field(minute, 0, 59),
        hour=_parse_cron_field(hour, 0, 23),
        day=_parse_cron_field(day, 1, 31),
        month=_parse_cron_field(month, 1, 12, names=_MONTH_NAMES),
        weekday=_parse_cron_field(weekday, 0, 6, names=_WEEKDAY_NAMES, allow_7_as_0=True),
        day_wildcard=day.strip() == "*",
        weekday_wildcard=weekday.strip() == "*",
    )


def _parse_cron_field(
    raw: str,
    min_value: int,
    max_value: int,
    *,
    names: dict[str, int] | None = None,
    allow_7_as_0: bool = False,
) -> frozenset[int]:
    values: set[int] = set()
    for item in raw.split(","):
        part = item.strip().upper()
        if not part:
            raise ValueError(f"invalid cron field {raw!r}")

        step = 1
        if "/" in part:
            base, step_text = part.split("/", 1)
            step = int(step_text)
            if step <= 0:
                raise ValueError(f"invalid cron step {part!r}")
        else:
            base = part

        if base == "*":
            values.update(range(min_value, max_value + 1, step))
            continue

        if "-" in base:
            start_text, end_text = base.split("-", 1)
            start = _parse_cron_value(
                start_text,
                min_value=min_value,
                max_value=max_value,
                names=names,
                allow_7_as_0=allow_7_as_0,
            )
            end = _parse_cron_value(
                end_text,
                min_value=min_value,
                max_value=max_value,
                names=names,
                allow_7_as_0=allow_7_as_0,
            )
            if start > end:
                raise ValueError(f"invalid cron range {part!r}")
            values.update(range(start, end + 1, step))
            continue

        if step != 1:
            raise ValueError(f"invalid stepped cron field {part!r}")
        values.add(
            _parse_cron_value(
                base,
                min_value=min_value,
                max_value=max_value,
                names=names,
                allow_7_as_0=allow_7_as_0,
            )
        )

    return frozenset(sorted(values))


def _parse_cron_value(
    raw: str,
    *,
    min_value: int,
    max_value: int,
    names: dict[str, int] | None = None,
    allow_7_as_0: bool = False,
) -> int:
    token = raw.strip().upper()
    if names and token in names:
        value = names[token]
    else:
        value = int(token)
    if allow_7_as_0 and value == 7:
        value = 0
    if value < min_value or value > max_value:
        raise ValueError(f"cron value {raw!r} out of range [{min_value}, {max_value}]")
    return value


def _matches_cron(spec: _CronSpec, dt: datetime) -> bool:
    cron_weekday = (dt.weekday() + 1) % 7
    if dt.minute not in spec.minute or dt.hour not in spec.hour or dt.month not in spec.month:
        return False

    day_match = dt.day in spec.day
    weekday_match = cron_weekday in spec.weekday
    if spec.day_wildcard and spec.weekday_wildcard:
        return True
    if spec.day_wildcard:
        return weekday_match
    if spec.weekday_wildcard:
        return day_match
    return day_match or weekday_match


def _next_cron_fire(spec: _CronSpec, now: datetime) -> datetime:
    candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    max_iterations = 60 * 24 * 366 * 5
    for _ in range(max_iterations):
        if _matches_cron(spec, candidate):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError("could not find next cron fire time within 5 years")
