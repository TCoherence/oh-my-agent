"""Tests for Scheduler liveness tracking, evaluate_job_health, and self-heal."""
from __future__ import annotations

import asyncio
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oh_my_agent.automation.scheduler import (
    HealthFinding,
    JobRuntimeState,
    Scheduler,
    ScheduledJob,
)


def _write_yaml(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def _mk_scheduler(tmp_path: Path) -> Scheduler:
    storage = tmp_path / "automations"
    storage.mkdir(exist_ok=True)
    return Scheduler(
        storage_dir=storage,
        reload_interval_seconds=0.05,
        timezone=timezone.utc,
    )


def _seed_cron_job(tmp_path: Path, name: str = "daily", cron: str = "0 8 * * *") -> Path:
    storage = tmp_path / "automations"
    storage.mkdir(exist_ok=True)
    path = storage / f"{name}.yaml"
    _write_yaml(
        path,
        f"""
        name: {name}
        enabled: true
        platform: discord
        channel_id: "100"
        thread_id: "200"
        prompt: summarize
        cron: "{cron}"
        """,
    )
    return path


@pytest.mark.asyncio
async def test_evaluate_job_health_returns_empty_when_healthy(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)
    fired: asyncio.Event = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        fired.set()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        findings = scheduler.evaluate_job_health()
        assert findings == []
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


@pytest.mark.asyncio
async def test_evaluate_job_health_rule_A_task_done_unexpectedly(tmp_path):
    """Rule A: task.done() is True but stop not set → task_done_unexpectedly."""
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        task = scheduler._job_tasks["daily"]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert task.done()
        findings = scheduler.evaluate_job_health()
        rule_a = [f for f in findings if f.scope == "job" and f.reason == "task_done_unexpectedly"]
        assert len(rule_a) == 1
        assert rule_a[0].name == "daily"
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


@pytest.mark.asyncio
async def test_evaluate_job_health_rule_B_missed_fire(tmp_path):
    """Rule B: phase sleeping, now > next_fire_at + grace, last_progress_at < next_fire_at."""
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        state = scheduler._job_state["daily"]
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        state.phase = "sleeping"
        state.next_fire_at = past
        state.last_progress_at = past - timedelta(minutes=5)
        findings = scheduler.evaluate_job_health()
        assert any(
            f.scope == "job" and f.name == "daily" and f.reason == "missed_fire"
            for f in findings
        )
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


@pytest.mark.asyncio
async def test_evaluate_job_health_long_running_fire_not_stale(tmp_path):
    """Reverse case: phase firing with ancient fire_started_at should NOT be flagged."""
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        state = scheduler._job_state["daily"]
        state.phase = "firing"
        state.fire_started_at = datetime.now(timezone.utc) - timedelta(hours=3)
        state.next_fire_at = datetime.now(timezone.utc) - timedelta(hours=3)
        findings = scheduler.evaluate_job_health()
        missed = [f for f in findings if f.name == "daily" and f.reason == "missed_fire"]
        assert missed == []
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


@pytest.mark.asyncio
async def test_evaluate_job_health_long_sleep_not_stale(tmp_path):
    """Reverse case: daily/weekly cron naturally sleeps long; next_fire_at is future."""
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        state = scheduler._job_state["daily"]
        state.phase = "sleeping"
        state.next_fire_at = datetime.now(timezone.utc) + timedelta(hours=8)
        findings = scheduler.evaluate_job_health()
        assert [f for f in findings if f.name == "daily"] == []
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


@pytest.mark.asyncio
async def test_restart_job_records_restart_history_and_rate_limits(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)
    scheduler._min_restart_interval_seconds = 60.0

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        ok = await scheduler.restart_job("daily", reason="task_done_unexpectedly")
        assert ok is True

        state = scheduler.get_job_runtime_state("daily")
        assert state is not None
        assert state.last_restart_reason == "task_done_unexpectedly"
        assert state.last_restart_at is not None

        # Second immediate restart is rate-limited.
        ok2 = await scheduler.restart_job("daily", reason="task_done_unexpectedly")
        assert ok2 is False
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


@pytest.mark.asyncio
async def test_restart_reload_loop_replaces_task(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)
    scheduler._min_restart_interval_seconds = 0.0

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        prior_task = scheduler._reload_task
        assert prior_task is not None

        ok = await scheduler.restart_reload_loop(reason="task_done_unexpectedly")
        assert ok is True
        assert scheduler._reload_task is not prior_task
        reload_state = scheduler.get_reload_runtime_state()
        assert reload_state is not None
        assert reload_state.last_restart_reason == "task_done_unexpectedly"
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


@pytest.mark.asyncio
async def test_list_and_get_job_runtime_state_return_copies(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        snapshots = scheduler.list_job_runtime_state()
        assert len(snapshots) == 1
        assert snapshots[0].name == "daily"

        # Mutating the snapshot must NOT change internal state.
        snapshots[0].phase = "firing"
        assert scheduler._job_state["daily"].phase == "sleeping"

        single = scheduler.get_job_runtime_state("daily")
        assert single is not None
        assert single.phase == "sleeping"
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


@pytest.mark.asyncio
async def test_compute_job_next_run_at_uses_cron(tmp_path):
    _seed_cron_job(tmp_path, cron="30 9 * * *")
    scheduler = _mk_scheduler(tmp_path)
    next_fire = scheduler.compute_job_next_run_at("daily")
    assert next_fire is not None
    assert next_fire.minute == 30
    assert next_fire.hour == 9


@pytest.mark.asyncio
async def test_reload_loop_stale_detection(tmp_path):
    """If the reload loop makes no progress for reload_interval * factor, flag it."""
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        assert scheduler._reload_state is not None
        scheduler._reload_state.last_progress_at = datetime.now(timezone.utc) - timedelta(hours=1)

        # Also cancel the reload task to avoid it touching progress.
        reload_task = scheduler._reload_task
        if reload_task is not None:
            reload_task.cancel()
            await asyncio.gather(reload_task, return_exceptions=True)
            scheduler._reload_task = None

        findings = scheduler.evaluate_job_health()
        reload_findings = [f for f in findings if f.scope == "reload"]
        assert reload_findings, findings
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)
