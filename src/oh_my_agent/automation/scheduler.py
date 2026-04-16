from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Awaitable, Callable
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


class Scheduler:
    """File-driven scheduler with polling-based hot reload."""

    def __init__(
        self,
        *,
        storage_dir: Path,
        reload_interval_seconds: float,
        default_target_user_id: str | None = None,
        timezone: tzinfo | None = None,
        timezone_name: str | None = None,
    ) -> None:
        self._storage_dir = storage_dir.expanduser().resolve()
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._reload_interval_seconds = float(reload_interval_seconds)
        self._default_target_user_id = default_target_user_id
        self._timezone = timezone or _resolve_local_timezone()
        self._timezone_name = timezone_name or _describe_timezone(self._timezone)
        self._records_by_name: dict[str, AutomationRecord] = {}
        self._jobs_by_name: dict[str, ScheduledJob] = {}
        self._duplicate_paths_by_name: dict[str, tuple[Path, ...]] = {}
        self._job_tasks: dict[str, asyncio.Task] = {}
        self._snapshot: dict[Path, tuple[int, int]] = {}
        self._reload_lock = asyncio.Lock()
        self._on_fire: Callable[[ScheduledJob], Awaitable[None]] | None = None
        self._on_reload: Callable[[], Awaitable[None]] | None = None
        self._stop_event = asyncio.Event()
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

    def compute_next_run_at(self, job: ScheduledJob) -> datetime | None:
        """Return the next fire time for *job* from now, or None for interval jobs on first fire."""
        now = datetime.now(self._timezone)
        if job.cron:
            spec = _parse_cron_expression(job.cron)
            return _next_cron_fire(spec, now)
        if job.interval_seconds:
            return now + timedelta(seconds=job.interval_seconds)
        return None

    def compute_all_next_run_at(self) -> dict[str, datetime | None]:
        """Return ``{name: next_fire_dt | None}`` for every *active* job."""
        return {name: self.compute_next_run_at(job) for name, job in self._jobs_by_name.items()}

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
        """Run active jobs and poll for filesystem changes until cancelled."""
        self._stop_event.clear()
        self._on_fire = on_fire
        for job in self.jobs:
            self._start_job(job, on_fire)

        logger.info(
            "Scheduler watching %s (%d active job(s))",
            self._storage_dir,
            len(self._jobs_by_name),
        )

        reload_task = asyncio.create_task(self._reload_loop(on_fire), name="scheduler:reload")
        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            raise
        finally:
            reload_task.cancel()
            await asyncio.gather(reload_task, return_exceptions=True)
            await self._stop_all_jobs()

    def stop(self) -> None:
        self._stop_event.set()

    async def fire_job_now(self, name: str) -> bool:
        """Manually trigger a job by name.  Returns True if fired."""
        job = self._task_job(name)
        if job is None:
            return False
        on_fire = getattr(self, "_on_fire", None)
        if on_fire is None:
            logger.warning("Cannot fire job %r: scheduler not running", name)
            return False
        logger.info("Manual fire for job %r", name)
        await on_fire(job)
        return True

    async def _reload_loop(
        self,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        while True:
            try:
                await asyncio.sleep(self._reload_interval_seconds)
                async with self._reload_lock:
                    snapshot = self._scan_snapshot()
                    if snapshot == self._snapshot:
                        continue
                    await self._apply_snapshot(snapshot)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Scheduler reload failed: %s", exc)

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
            await self._reconcile_running_jobs(self._on_fire)

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
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        current_names = set(self._job_tasks)
        desired_names = set(self._jobs_by_name)

        removed = current_names - desired_names
        added = desired_names - current_names
        updated = {
            name
            for name in (current_names & desired_names)
            if self._jobs_by_name[name] != self._task_job(name)
        }

        for name in sorted(removed | updated):
            await self._stop_job(name)
        for name in sorted(added | updated):
            self._start_job(self._jobs_by_name[name], on_fire)

    def _task_job(self, name: str) -> ScheduledJob | None:
        task = self._job_tasks.get(name)
        if task is None:
            return None
        return getattr(task, "_oma_job", None)

    def _start_job(
        self,
        job: ScheduledJob,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        runner = self._run_cron_job if job.cron else self._run_interval_job
        task = asyncio.create_task(runner(job, on_fire), name=f"scheduler:{job.name}")
        setattr(task, "_oma_job", job)
        self._job_tasks[job.name] = task

    async def _stop_job(self, name: str) -> None:
        task = self._job_tasks.pop(name, None)
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _stop_all_jobs(self) -> None:
        tasks = list(self._job_tasks.values())
        self._job_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_interval_job(
        self,
        job: ScheduledJob,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        if job.initial_delay_seconds > 0:
            await asyncio.sleep(job.initial_delay_seconds)

        while True:
            try:
                logger.info(
                    "Scheduler firing interval job=%s platform=%s channel=%s thread=%s",
                    job.name,
                    job.platform,
                    job.channel_id,
                    job.thread_id or "(new)",
                )
                await on_fire(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Scheduler job '%s' failed: %s", job.name, exc)
            await asyncio.sleep(job.interval_seconds or 0)

    async def _run_cron_job(
        self,
        job: ScheduledJob,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        spec = _parse_cron_expression(job.cron or "")

        while True:
            now = datetime.now(self._timezone)
            next_fire = _next_cron_fire(spec, now)
            delay = max((next_fire - now).total_seconds(), 0.0)
            await asyncio.sleep(delay)
            try:
                logger.info(
                    "Scheduler firing cron job=%s schedule=%s platform=%s channel=%s thread=%s",
                    job.name,
                    job.cron,
                    job.platform,
                    job.channel_id,
                    job.thread_id or "(new)",
                )
                await on_fire(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Scheduler job '%s' failed: %s", job.name, exc)


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

    return Scheduler(
        storage_dir=storage_dir,
        reload_interval_seconds=reload_interval_seconds,
        default_target_user_id=default_target_user_id,
        timezone=timezone_obj,
        timezone_name=timezone_name,
    )


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
