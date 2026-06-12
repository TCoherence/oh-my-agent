from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import filelock
import yaml

from oh_my_agent.config import _substitute

logger = logging.getLogger(__name__)


def _automation_lock(path: Path) -> filelock.FileLock:
    """Sidecar cross-process lock for an automation file."""
    return filelock.FileLock(str(path) + ".lock", timeout=10)


def _atomic_write_yaml_unlocked(path: Path, data: dict) -> None:
    """Temp-file-then-os.replace write WITHOUT acquiring the lock.

    Caller must already hold ``_automation_lock(path)`` (so read-modify-write
    transactions stay atomic). os.replace gives readers old-or-new file
    visibility, never partial.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".yaml.tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tf:
            tf.write(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
            tf.flush()
            os.fsync(tf.fileno())
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def _safe_write_yaml(path: Path, data: dict) -> None:
    """Atomically write ``data`` as YAML to ``path`` (M2 PR2), acquiring the lock.

    Cross-process-safe: the dashboard API (separate write site) and the
    scheduler's own reload both contend for these files.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with _automation_lock(path):
        _atomic_write_yaml_unlocked(path, data)

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
    """Liveness state for one scheduled job."""

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
    """One stale observation from evaluate_job_health."""

    scope: str  # "job" | "reload" | "due_loop"
    reason: str  # "task_done_unexpectedly" | "missed_fire" | "no_progress"
    name: str | None = None  # job name; None for reload/due_loop scope


FireJobResult = Literal["ok", "not_found", "scheduler_down", "already_firing"]


_DEFAULT_MIN_RESTART_INTERVAL_SECONDS = 120.0
_DEFAULT_STALE_GRACE_SECONDS = 90.0
_DEFAULT_RELOAD_STALE_FACTOR = 10.0  # reload loop stale if no progress for 10x reload_interval_seconds
_DEFAULT_DUE_LOOP_MAX_TICK_SECONDS = 30.0
_DEFAULT_DUE_LOOP_STALE_FACTOR = 10.0  # due loop stale if no progress for 10x max_tick
_DUE_LOOP_MIN_SLEEP_SECONDS = 0.05


class Scheduler:
    """File-driven scheduler with a central wall-clock due-loop scanner."""

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
        # Only in-flight fire tasks live here; sleeping jobs have no task.
        self._fire_tasks: dict[str, asyncio.Task] = {}
        self._job_state: dict[str, JobRuntimeState] = {}
        self._reload_state: ReloadRuntimeState | None = None
        self._reload_task: asyncio.Task | None = None
        self._due_loop_state: DueLoopRuntimeState | None = None
        self._due_loop_task: asyncio.Task | None = None
        self._due_loop_wakeup: asyncio.Event = asyncio.Event()
        self._snapshot: dict[Path, tuple[int, int]] = {}
        self._reload_lock = asyncio.Lock()
        self._on_fire: Callable[[ScheduledJob], Awaitable[None]] | None = None
        self._on_reload: Callable[[], Awaitable[None]] | None = None
        self._stop_event = asyncio.Event()
        self._min_restart_interval_seconds: float = _DEFAULT_MIN_RESTART_INTERVAL_SECONDS
        self._stale_grace_seconds: float = _DEFAULT_STALE_GRACE_SECONDS
        self.due_loop_max_tick_seconds: float = _DEFAULT_DUE_LOOP_MAX_TICK_SECONDS
        self._load_from_disk(initial=True)

    # ------------------------------------------------------------------
    # Clock — single source of truth so tests can inject a fake clock.
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        return datetime.now(self._timezone)

    @property
    def jobs(self) -> list[ScheduledJob]:
        return [self._jobs_by_name[name] for name in sorted(self._jobs_by_name)]

    @property
    def storage_dir(self) -> Path:
        return self._storage_dir

    @property
    def timezone_name(self) -> str:
        return self._timezone_name

    @property
    def dump_channels(self) -> dict[str, DumpChannelConfig]:
        """Read-only snapshot of configured dump-channel aliases."""
        return dict(self._dump_channels)

    def compute_next_run_at(self, job: ScheduledJob) -> datetime | None:
        """Return the next fire time for *job* from now.

        None only when the job defines neither cron nor interval_seconds
        (interval jobs always get ``now + interval``, including before the
        first fire).
        """
        now = self._now()
        if job.cron:
            spec = _parse_cron_expression(job.cron)
            return _next_cron_fire(spec, now)
        if job.interval_seconds:
            return now + timedelta(seconds=job.interval_seconds)
        return None

    def compute_all_next_run_at(self) -> dict[str, datetime | None]:
        """Return ``{name: next_fire_dt | None}`` for every *active* job.

        A job whose next fire cannot be computed maps to None instead of
        propagating — this is called from the manager's reload binding, where
        one pathological spec must not take down the whole scheduler.
        """
        result: dict[str, datetime | None] = {}
        for name, job in self._jobs_by_name.items():
            try:
                result[name] = self.compute_next_run_at(job)
            except ValueError:
                logger.exception(
                    "Scheduler job %r: failed to compute next fire time", name
                )
                result[name] = None
        return result

    def list_automations(self) -> list[AutomationRecord]:
        return [self._records_by_name[name] for name in sorted(self._records_by_name)]

    def get_automation(self, name: str) -> AutomationRecord | None:
        return self._records_by_name.get(name)

    async def reload_now(self) -> dict[str, int]:
        async with self._reload_lock:
            return await self._reload_now_locked()

    async def patch_automation(self, name: str, updates: dict[str, Any]) -> AutomationRecord:
        """M2 PR2/PR3: atomically apply field updates to an automation file.

        Only whitelisted keys may be patched (``enabled`` / ``cron`` /
        ``interval_seconds``) — arbitrary YAML rewrites are rejected so the
        dashboard can't corrupt prompt/skill bindings.

        Codex M2 PR2 hardening:
        - Values are VALIDATED before any write (invalid cron / non-positive
          interval / cron+interval coexistence are rejected up-front, so a
          bad PATCH never makes the automation disappear post-reload).
        - The read → mutate → validate → write happens INSIDE one
          ``_automation_lock`` so a concurrent writer can't clobber the
          read-modify-write transaction. On a failed reload the original
          file content is restored (rollback).
        """
        allowed = {"enabled", "cron", "interval_seconds"}
        bad = set(updates) - allowed
        if bad:
            raise ValueError(f"patch_automation: disallowed keys {sorted(bad)}")
        async with self._reload_lock:
            await self._reload_now_locked()
            record = self._records_by_name.get(name)
            if record is None or record.source_path is None:
                raise ValueError(f"automation {name!r} not found")
            source_path = record.source_path
            # Whole read-modify-write transaction under the cross-process lock.
            with _automation_lock(source_path):
                try:
                    original_text = source_path.read_text(encoding="utf-8")
                    raw = yaml.safe_load(original_text)
                except Exception as exc:
                    raise ValueError(
                        f"failed to read automation file {source_path}: {exc}"
                    ) from exc
                if not isinstance(raw, dict):
                    raise ValueError(
                        f"automation file {source_path} must contain a YAML mapping"
                    )
                candidate = dict(raw)
                changed = False
                for key, value in updates.items():
                    if candidate.get(key) != value:
                        candidate[key] = value
                        changed = True
                if not changed:
                    return record
                # Validate candidate BEFORE persisting.
                self._validate_patch_candidate(candidate)
                _atomic_write_yaml_unlocked(source_path, candidate)
            # Reload outside the file lock (reload reads via its own scan).
            await self._reload_now_locked()
            updated = self._records_by_name.get(name)
            if updated is None:
                # Rollback: reload rejected the new content. Restore original.
                with _automation_lock(source_path):
                    _atomic_write_yaml_unlocked(source_path, raw)
                await self._reload_now_locked()
                raise ValueError(
                    f"automation {name!r} invalid after patch; rolled back to prior content"
                )
            return updated

    @staticmethod
    def _validate_patch_candidate(candidate: dict[str, Any]) -> None:
        """Reject value-level errors before writing a patched automation file.

        - cron + interval_seconds are mutually exclusive.
        - interval_seconds must be a positive int.
        - cron must parse.
        """
        has_cron = candidate.get("cron") not in (None, "")
        has_interval = candidate.get("interval_seconds") not in (None, "")
        if has_cron and has_interval:
            raise ValueError(
                "patch_automation: cron and interval_seconds are mutually exclusive"
            )
        if not has_cron and not has_interval:
            raise ValueError(
                "patch_automation: one of cron / interval_seconds is required"
            )
        if has_interval:
            try:
                interval = int(candidate["interval_seconds"])
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"patch_automation: interval_seconds must be an int "
                    f"(got {candidate['interval_seconds']!r})"
                ) from exc
            if interval <= 0:
                raise ValueError(
                    f"patch_automation: interval_seconds must be > 0 (got {interval})"
                )
        if has_cron:
            # Raises on invalid cron syntax.
            _parse_cron_expression(str(candidate["cron"]))

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
                # M2 PR2: atomic + cross-process-safe write. The dashboard
                # API may PATCH the same file concurrently; raw write_text
                # could expose a half-written file to the scheduler's
                # reload scan.
                _safe_write_yaml(source_path, raw)

            await self._reload_now_locked()
            updated = self._records_by_name.get(name)
            if updated is None:
                raise ValueError(f"automation {name!r} is no longer visible after reload")
            return updated

    async def run(
        self,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        """Run active jobs and poll for filesystem changes until cancelled."""
        self._stop_event.clear()
        self._on_fire = on_fire
        now = self._now()
        self._reload_state = ReloadRuntimeState(last_progress_at=now)
        self._due_loop_state = DueLoopRuntimeState(last_progress_at=now)
        for job in self.jobs:
            self._start_job(job, on_fire)

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
            # Order matters: due loop first (stops new dispatches), reload
            # loop next, in-flight fires last (drain). Clearing state
            # before tasks finish would break the race-guard identity check.
            if self._due_loop_task is not None:
                self._due_loop_task.cancel()
                await asyncio.gather(self._due_loop_task, return_exceptions=True)
                self._due_loop_task = None
            if self._reload_task is not None:
                self._reload_task.cancel()
                await asyncio.gather(self._reload_task, return_exceptions=True)
                self._reload_task = None
            fire_tasks = list(self._fire_tasks.values())
            for task in fire_tasks:
                task.cancel()
            if fire_tasks:
                await asyncio.gather(*fire_tasks, return_exceptions=True)
            self._fire_tasks.clear()
            self._job_state.clear()

    def stop(self) -> None:
        self._stop_event.set()
        # Wake the due loop immediately so it can observe the stop event without
        # waiting out its remaining sleep budget.
        self._due_loop_wakeup.set()

    async def fire_job_now(self, name: str) -> FireJobResult:
        """Manually dispatch a job by name. Returns a result code.

        Unlike the old synchronous behavior, this returns immediately after
        the fire task is dispatched — the fire itself runs in the background
        under the same short-lived-task model as scheduled fires. A manual
        fire does not displace the job's next scheduled fire time (the
        future ``next_fire_at`` is pinned and restored after completion).
        """
        job = self._jobs_by_name.get(name)
        if job is None:
            return "not_found"
        on_fire = self._on_fire
        state = self._job_state.get(name)
        if on_fire is None or state is None:
            logger.warning("Cannot fire job %r: scheduler not running", name)
            return "scheduler_down"
        if state.phase == "firing":
            logger.info("fire_job_now: %r already firing — skipping manual fire", name)
            return "already_firing"
        logger.info("fire_job_now: dispatching manual fire for job %r", name)
        self._dispatch_due_job(name, on_fire, preserve_next_fire=True)
        return "ok"

    # ------------------------------------------------------------------
    # Reload loop
    # ------------------------------------------------------------------

    async def _reload_loop(
        self,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        while not self._stop_event.is_set():
            try:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._reload_interval_seconds,
                    )
                    return
                except asyncio.TimeoutError:
                    pass
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

    # ------------------------------------------------------------------
    # Due loop — central wall-clock scanner
    # ------------------------------------------------------------------

    async def _due_loop(
        self,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        """Tick on a bounded interval, dispatch any job whose next_fire_at <= now.

        This loop is wall-clock driven: each iteration reads ``_now()`` fresh,
        so a host suspend that skips monotonic time forward is naturally
        recovered on the next tick (latency ≤ ``due_loop_max_tick_seconds``).
        """
        while not self._stop_event.is_set():
            try:
                now = self._now()
                due_names = self._collect_due_jobs(now)
                for name in due_names:
                    self._dispatch_due_job(name, on_fire)
                self._touch_due_loop_progress()
                # IMPORTANT: clear() BEFORE computing the sleep budget. If we
                # cleared after the wait had started, a wakeup that fires in
                # the window between compute-and-clear would be lost,
                # stretching a short-interval job's cadence to max_tick.
                self._due_loop_wakeup.clear()
                if self._stop_event.is_set():
                    return
                sleep_for = self._compute_due_loop_sleep(self._now())
                try:
                    await asyncio.wait_for(
                        self._due_loop_wakeup.wait(), timeout=sleep_for
                    )
                except asyncio.TimeoutError:
                    pass
                # stop() pokes _due_loop_wakeup on shutdown to break this wait
                # without relying on cancellation.
                if self._stop_event.is_set():
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler due_loop iteration failed")
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.due_loop_max_tick_seconds,
                    )
                    return
                except asyncio.TimeoutError:
                    pass

    def _collect_due_jobs(self, now: datetime) -> list[str]:
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

    def _compute_due_loop_sleep(self, now: datetime) -> float:
        """Sleep until the earliest sleeping-job next_fire_at, capped at max_tick.

        Firing jobs are intentionally excluded — their ``next_fire_at`` carries
        the prior schedule and would make the loop spin at the lower bound
        until the fire completes. Pinned-future ``next_fire_at`` (from manual
        fires with ``preserve_next_fire=True``) is only honored after the
        fire completes and ``_mark_job_sleeping`` restores it; during the
        fire itself the wakeup event drives reconvergence (set by
        ``_fire_job_once.finally``).
        """
        earliest: datetime | None = None
        for state in self._job_state.values():
            if state.phase != "sleeping":
                continue
            if state.next_fire_at is None:
                continue
            if earliest is None or state.next_fire_at < earliest:
                earliest = state.next_fire_at

        if earliest is None:
            return self.due_loop_max_tick_seconds

        delta = (earliest - now).total_seconds()
        if delta <= 0:
            # Already due — loop will dispatch on the next iteration. Keep
            # sleep short but non-zero so we don't starve the event loop.
            return _DUE_LOOP_MIN_SLEEP_SECONDS
        return max(_DUE_LOOP_MIN_SLEEP_SECONDS, min(delta, self.due_loop_max_tick_seconds))

    def _dispatch_due_job(
        self,
        name: str,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
        *,
        preserve_next_fire: bool = False,
    ) -> None:
        state = self._job_state.get(name)
        job = self._jobs_by_name.get(name)
        if state is None or job is None:
            return
        if state.phase == "firing":
            # Defense in depth: callers (due loop, fire_job_now) already
            # filter on phase, so hitting this branch means a double-dispatch
            # attempt. Log and skip rather than silently no-op.
            logger.debug(
                "_dispatch_due_job: %r already firing — skipping (preserve=%s)",
                name,
                preserve_next_fire,
            )
            return
        # Capture the pre-fire next_fire_at so manual fires can restore it
        # instead of advancing the regular schedule.
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
            kind = "cron" if job.cron else "interval"
            logger.info(
                "Scheduler firing %s job=%s platform=%s channel=%s thread=%s",
                kind,
                job.name,
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
            # Race guard: a reload/update may have replaced the task and
            # state while we were running. Only advance state if this task
            # still owns the slot.
            current = asyncio.current_task()
            if self._fire_tasks.get(job.name) is current:
                self._fire_tasks.pop(job.name, None)
                state = self._job_state.get(job.name)
                if state is not None:
                    post_now = self._now()
                    next_fire: datetime | None
                    if pinned_next_fire is not None and pinned_next_fire > post_now:
                        next_fire = pinned_next_fire
                    else:
                        try:
                            next_fire = self._compute_next_fire_after_completion(
                                job, post_now
                            )
                        except ValueError:
                            # Must still leave phase="firing" — a raise here
                            # would wedge the job (due loop, fire_job_now,
                            # and health checks all skip firing jobs).
                            logger.exception(
                                "Scheduler job %r: cannot compute next fire "
                                "time after completion; job will not "
                                "auto-fire until its schedule is fixed",
                                job.name,
                            )
                            next_fire = None
                            # _mark_job_sleeping keeps the prior next_fire_at
                            # when passed None; clear it explicitly so the due
                            # loop doesn't re-dispatch the stale (past) time.
                            state.next_fire_at = None
                    self._mark_job_sleeping(job.name, next_fire_at=next_fire)
                    self._due_loop_wakeup.set()

    def _compute_next_fire_after_completion(
        self,
        job: ScheduledJob,
        post_now: datetime,
    ) -> datetime:
        if job.cron:
            return _next_cron_fire(_parse_cron_expression(job.cron), post_now)
        interval = job.interval_seconds or 0
        return post_now + timedelta(seconds=interval)

    def _touch_due_loop_progress(self) -> None:
        if self._due_loop_state is not None:
            self._due_loop_state.last_progress_at = self._now()

    # ------------------------------------------------------------------
    # Load / snapshot / reconcile
    # ------------------------------------------------------------------

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
            # The 'cron xor interval_seconds' guard above ensures non-None here.
            assert interval_seconds is not None
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
        for name in sorted(removed | updated):
            await self._stop_job(name)
        for name in sorted(added | updated):
            job = self._jobs_by_name.get(name)
            if job is not None:
                self._start_job(job, on_fire)
        if added or removed or updated:
            self._due_loop_wakeup.set()

    def _start_job(
        self,
        job: ScheduledJob,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        """Initialize ``JobRuntimeState`` for a new/updated job. No task created here.

        The central due loop polls state and dispatches fires; long sleepers
        are gone.
        """
        now = self._now()
        initial_next_fire: datetime | None
        if job.cron:
            try:
                initial_next_fire = self.compute_next_run_at(job)
            except ValueError:
                # A pathological spec disables only this job: state is kept
                # (visible to /doctor, manually fireable) but next_fire_at
                # stays None so the due loop never auto-dispatches it.
                logger.exception(
                    "Scheduler job %r: cannot compute next fire time; "
                    "job will not auto-fire until its schedule is fixed",
                    job.name,
                )
                initial_next_fire = None
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
        # Order: remove our slot first so the in-flight race guard treats
        # this cancel as "no longer owns the slot", then drop state after
        # the task has drained.
        task = self._fire_tasks.pop(name, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._job_state.pop(name, None)
        self._due_loop_wakeup.set()

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

    # ------------------------------------------------------------------
    # Read-only introspection
    # ------------------------------------------------------------------

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
        """Read-only health evaluation. Returns stale-job / stale-loop findings.

        Rules:
          - Per-job ``missed_fire`` (phase=="sleeping" AND
            now > next_fire_at + grace AND last_progress_at < next_fire_at)
            is emitted as **informational only** — the central due loop is
            the recovery mechanism, so there is no per-job task to restart.
            Supervisor uses this finding for logging / ``/doctor`` rendering.
          - Reload loop: ``task_done_unexpectedly`` or ``no_progress``
            triggers ``restart_reload_loop``.
          - Due loop: ``task_done_unexpectedly`` or ``no_progress`` triggers
            ``restart_due_loop``. No-progress threshold is
            ``due_loop_max_tick_seconds * _DEFAULT_DUE_LOOP_STALE_FACTOR``.
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
                # Informational only — due loop handles recovery.
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

        due_task = self._due_loop_task
        due_state = self._due_loop_state
        if due_state is not None:
            if not stop_set and due_task is not None and due_task.done():
                findings.append(
                    HealthFinding(
                        scope="due_loop", name=None, reason="task_done_unexpectedly"
                    )
                )
            else:
                stale_threshold = timedelta(
                    seconds=self.due_loop_max_tick_seconds * _DEFAULT_DUE_LOOP_STALE_FACTOR
                )
                if now - due_state.last_progress_at > stale_threshold:
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
        """Self-heal a stalled due loop. Rate-limited by min_restart_interval.

        The old task is cancelled and fully drained before the new task is
        created — otherwise two loops could dispatch the same job back to
        back.
        """
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
            logger.warning("Scheduler restart due loop reason=%s", reason)
            task = self._due_loop_task
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            self._due_loop_task = asyncio.create_task(
                self._due_loop(on_fire), name="scheduler:due"
            )
            if due_state is not None:
                due_state.last_progress_at = now
                due_state.last_restart_at = now
                due_state.last_restart_reason = reason
            # Kick the newly-created loop so it re-evaluates immediately.
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


def _parse_positive_optional_int(raw: Any, *, field_name: str) -> int | None:
    if raw is None:
        return None
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return value


# Maximum length each month can ever reach (February counts leap years).
_MAX_MONTH_LENGTHS = {
    1: 31,
    2: 29,
    3: 31,
    4: 30,
    5: 31,
    6: 30,
    7: 31,
    8: 31,
    9: 30,
    10: 31,
    11: 30,
    12: 31,
}


def _parse_cron_expression(expr: str) -> _CronSpec:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron must be a 5-field expression: minute hour day month weekday")

    minute, hour, day, month, weekday = parts
    spec = _CronSpec(
        minute=_parse_cron_field(minute, 0, 59),
        hour=_parse_cron_field(hour, 0, 23),
        day=_parse_cron_field(day, 1, 31),
        month=_parse_cron_field(month, 1, 12, names=_MONTH_NAMES),
        weekday=_parse_cron_field(weekday, 0, 6, names=_WEEKDAY_NAMES, allow_7_as_0=True),
        day_wildcard=day.strip() == "*",
        weekday_wildcard=weekday.strip() == "*",
    )
    _check_cron_feasibility(spec)
    return spec


def _check_cron_feasibility(spec: _CronSpec) -> None:
    """Reject field-valid specs that can never fire (e.g. ``0 0 31 2 *``).

    Without this, ``_next_cron_fire`` walks its full 5-year search window
    minute-by-minute on the event loop before raising. Per the vixie
    day-OR-weekday semantics in ``_matches_cron``, a restricted weekday field
    can always match on its own (every month contains every weekday), so only
    the weekday-wildcard / day-restricted shape can be infeasible.
    """
    if spec.day_wildcard or not spec.weekday_wildcard:
        return
    max_month_len = max(_MAX_MONTH_LENGTHS[m] for m in spec.month)
    if min(spec.day) > max_month_len:
        raise ValueError(
            f"cron day-of-month value(s) {sorted(spec.day)} never occur in "
            f"month(s) {sorted(spec.month)}"
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
