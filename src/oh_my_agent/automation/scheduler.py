from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledJob:
    """Single periodic automation job."""

    name: str
    platform: str
    channel_id: str
    prompt: str
    interval_seconds: int
    delivery: str = "channel"  # "channel" | "dm"
    thread_id: str | None = None
    target_user_id: str | None = None
    agent: str | None = None
    initial_delay_seconds: int = 0
    author: str = "scheduler"


class Scheduler:
    """Simple interval-based scheduler for automation jobs."""

    def __init__(self, jobs: list[ScheduledJob]) -> None:
        self._jobs = jobs

    @property
    def jobs(self) -> list[ScheduledJob]:
        return list(self._jobs)

    async def run(
        self,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        """Run all jobs forever until cancelled."""
        if not self._jobs:
            logger.info("Scheduler enabled with 0 jobs; nothing to run")
            return

        tasks = [
            asyncio.create_task(self._run_job(job, on_fire), name=f"scheduler:{job.name}")
            for job in self._jobs
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_job(
        self,
        job: ScheduledJob,
        on_fire: Callable[[ScheduledJob], Awaitable[None]],
    ) -> None:
        if job.initial_delay_seconds > 0:
            await asyncio.sleep(job.initial_delay_seconds)

        while True:
            try:
                logger.info(
                    "Scheduler firing job=%s platform=%s channel=%s thread=%s",
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
            await asyncio.sleep(job.interval_seconds)


def build_scheduler_from_config(
    config: dict,
    *,
    default_target_user_id: str | None = None,
) -> Scheduler | None:
    """Parse scheduler config and return a Scheduler if enabled."""
    sched_cfg = config.get("automations", {})
    if not sched_cfg.get("enabled", False):
        return None

    raw_jobs = sched_cfg.get("jobs", [])
    jobs: list[ScheduledJob] = []

    for idx, raw in enumerate(raw_jobs):
        if not isinstance(raw, dict):
            raise ValueError(f"automations.jobs[{idx}] must be a mapping")
        if not bool(raw.get("enabled", True)):
            continue
        name = str(raw.get("name", f"job-{idx + 1}")).strip()
        platform = str(raw["platform"]).strip()
        channel_id = str(raw["channel_id"]).strip()
        prompt = str(raw["prompt"]).strip()
        delivery = str(raw.get("delivery", "channel")).strip().lower()
        if delivery not in {"channel", "dm"}:
            raise ValueError(f"automations.jobs[{idx}].delivery must be 'channel' or 'dm'")

        target_user_id = None
        if delivery == "dm":
            target_user_id = (
                str(raw.get("target_user_id")).strip()
                if raw.get("target_user_id") is not None
                else None
            )
            if not target_user_id and default_target_user_id:
                target_user_id = default_target_user_id
            if not target_user_id:
                raise ValueError(
                    f"automations.jobs[{idx}].target_user_id is required for delivery='dm' "
                    "(or set access.owner_user_ids)"
                )

        interval_seconds = int(raw["interval_seconds"])
        if interval_seconds <= 0:
            raise ValueError(f"automations.jobs[{idx}].interval_seconds must be > 0")
        initial_delay_seconds = int(raw.get("initial_delay_seconds", 0))
        if initial_delay_seconds < 0:
            raise ValueError(f"automations.jobs[{idx}].initial_delay_seconds must be >= 0")

        jobs.append(
            ScheduledJob(
                name=name,
                platform=platform,
                channel_id=channel_id,
                delivery=delivery,
                thread_id=(str(raw["thread_id"]) if "thread_id" in raw and raw["thread_id"] is not None else None),
                target_user_id=target_user_id,
                prompt=prompt,
                agent=(str(raw["agent"]) if raw.get("agent") else None),
                interval_seconds=interval_seconds,
                initial_delay_seconds=initial_delay_seconds,
                author=str(raw.get("author", "scheduler")),
            )
        )

    return Scheduler(jobs)
