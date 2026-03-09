from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Awaitable, Callable

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

    @property
    def schedule_kind(self) -> str:
        return "cron" if self.cron else "interval"


@dataclass(frozen=True)
class _ParsedAutomation:
    job: ScheduledJob
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
    ) -> None:
        self._storage_dir = storage_dir.expanduser().resolve()
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._reload_interval_seconds = float(reload_interval_seconds)
        self._default_target_user_id = default_target_user_id
        self._timezone = timezone or datetime.now().astimezone().tzinfo
        self._jobs_by_name: dict[str, ScheduledJob] = {}
        self._job_tasks: dict[str, asyncio.Task] = {}
        self._snapshot: dict[Path, tuple[int, int]] = {}
        self._load_from_disk(initial=True)

    @property
    def jobs(self) -> list[ScheduledJob]:
        return [self._jobs_by_name[name] for name in sorted(self._jobs_by_name)]

    async def run(
        self,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        """Run active jobs and poll for filesystem changes until cancelled."""
        for job in self.jobs:
            self._start_job(job, on_fire)

        logger.info(
            "Scheduler watching %s (%d active job(s))",
            self._storage_dir,
            len(self._jobs_by_name),
        )

        reload_task = asyncio.create_task(self._reload_loop(on_fire), name="scheduler:reload")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise
        finally:
            reload_task.cancel()
            await asyncio.gather(reload_task, return_exceptions=True)
            await self._stop_all_jobs()

    async def _reload_loop(
        self,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        while True:
            try:
                await asyncio.sleep(self._reload_interval_seconds)
                snapshot = self._scan_snapshot()
                if snapshot == self._snapshot:
                    continue
                self._load_from_disk(snapshot=snapshot)
                await self._reconcile_running_jobs(on_fire)
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
            duplicates.setdefault(item.job.name, []).append(item.job.source_path or Path("<unknown>"))

        duplicate_names = {name for name, paths in duplicates.items() if len(paths) > 1}
        for name in sorted(duplicate_names):
            paths = ", ".join(str(path) for path in duplicates[name])
            logger.error(
                "Automation name conflict for %r; skipping all conflicting files: %s",
                name,
                paths,
            )

        jobs_by_name: dict[str, ScheduledJob] = {}
        for item in parsed:
            if item.job.name in duplicate_names:
                continue
            if item.enabled:
                jobs_by_name[item.job.name] = item.job

        self._jobs_by_name = jobs_by_name
        self._snapshot = snapshot
        if initial:
            logger.info("Loaded %d active automation job(s) from %s", len(jobs_by_name), self._storage_dir)

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

        return _ParsedAutomation(
            job=ScheduledJob(
                name=name,
                platform=platform,
                channel_id=channel_id,
                prompt=prompt,
                delivery=delivery,
                thread_id=(str(raw["thread_id"]) if raw.get("thread_id") is not None else None),
                target_user_id=target_user_id,
                agent=(str(raw["agent"]) if raw.get("agent") else None),
                author=str(raw.get("author", "scheduler")),
                cron=cron,
                interval_seconds=interval_value,
                initial_delay_seconds=initial_delay_seconds,
                source_path=source_path,
            ),
            enabled=enabled,
        )

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

        if removed or added or updated:
            logger.info(
                "Scheduler reloaded active jobs=%d added=%d updated=%d removed=%d",
                len(self._jobs_by_name),
                len(added),
                len(updated),
                len(removed),
            )

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

    return Scheduler(
        storage_dir=storage_dir,
        reload_interval_seconds=reload_interval_seconds,
        default_target_user_id=default_target_user_id,
    )


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
