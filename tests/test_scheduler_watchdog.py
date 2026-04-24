"""Tests for the central due-loop scanner, liveness tracking, and self-heal.

The scheduler uses a single wall-clock due loop instead of one asyncio task
per scheduled job. Tests cover:

- due-loop firing (cron + interval)
- overlap guard (no double-fire while in-flight)
- wall-clock recovery after simulated host suspend
- manual fire (preserves future scheduled next_fire_at; does not double-fire
  when the pinned target is already overdue)
- reload cancels in-flight fires; updates recompute next_fire
- restart_due_loop + rate-limit
- supervisor no longer restarts per-job on missed_fire
"""
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


def _mk_scheduler(
    tmp_path: Path,
    *,
    due_loop_max_tick_seconds: float = 30.0,
) -> Scheduler:
    storage = tmp_path / "automations"
    storage.mkdir(exist_ok=True)
    scheduler = Scheduler(
        storage_dir=storage,
        reload_interval_seconds=0.05,
        timezone=timezone.utc,
    )
    scheduler.due_loop_max_tick_seconds = due_loop_max_tick_seconds
    return scheduler


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


def _seed_interval_job(
    tmp_path: Path,
    name: str,
    *,
    interval_seconds: int,
    initial_delay_seconds: int = 0,
) -> Path:
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
        interval_seconds: {interval_seconds}
        initial_delay_seconds: {initial_delay_seconds}
        """,
    )
    return path


async def _stop(scheduler: Scheduler, run_task: asyncio.Task) -> None:
    scheduler.stop()
    await asyncio.wait_for(run_task, timeout=2.0)


# ----------------------------------------------------------------------
# evaluate_job_health
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_job_health_returns_empty_when_healthy(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        findings = scheduler.evaluate_job_health()
        assert findings == []
    finally:
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_evaluate_job_health_missed_fire_is_informational(tmp_path):
    """Under the due-loop model, missed_fire is informational — not a per-job restart signal."""
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
        missed = [
            f for f in findings
            if f.scope == "job" and f.name == "daily" and f.reason == "missed_fire"
        ]
        assert len(missed) == 1
        # Restart path removed — scheduler exposes no restart_job.
        assert not hasattr(scheduler, "restart_job")
    finally:
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_evaluate_job_health_long_running_fire_not_stale(tmp_path):
    """phase=firing is never flagged as missed_fire."""
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
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_reload_loop_stale_detection(tmp_path):
    """Reload loop no-progress beyond threshold is flagged."""
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        assert scheduler._reload_state is not None
        scheduler._reload_state.last_progress_at = datetime.now(timezone.utc) - timedelta(hours=1)
        reload_task = scheduler._reload_task
        if reload_task is not None:
            reload_task.cancel()
            await asyncio.gather(reload_task, return_exceptions=True)
            scheduler._reload_task = None
        findings = scheduler.evaluate_job_health()
        assert any(f.scope == "reload" for f in findings), findings
    finally:
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_due_loop_stale_detection(tmp_path):
    """Due loop no-progress beyond threshold is flagged."""
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        assert scheduler._due_loop_state is not None
        scheduler._due_loop_state.last_progress_at = datetime.now(timezone.utc) - timedelta(hours=1)
        due_task = scheduler._due_loop_task
        if due_task is not None:
            due_task.cancel()
            await asyncio.gather(due_task, return_exceptions=True)
        findings = scheduler.evaluate_job_health()
        assert any(f.scope == "due_loop" for f in findings), findings
    finally:
        await _stop(scheduler, run_task)


# ----------------------------------------------------------------------
# Due-loop firing
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_loop_fires_cron_job_when_due(tmp_path):
    """Force state.next_fire_at into the past; due loop dispatches on next tick."""
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)
    fired: asyncio.Event = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        fired.set()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
        state = scheduler._job_state["daily"]
        state.next_fire_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        scheduler._due_loop_wakeup.set()
        await asyncio.wait_for(fired.wait(), timeout=1.0)
    finally:
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_due_loop_fires_interval_job_with_initial_delay(tmp_path):
    _seed_interval_job(tmp_path, "tick", interval_seconds=1, initial_delay_seconds=0)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)
    fires: list[datetime] = []

    async def on_fire(job: ScheduledJob) -> None:
        fires.append(datetime.now(timezone.utc))

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        # Initial delay is 0, so first fire should happen ~immediately.
        deadline = asyncio.get_event_loop().time() + 1.0
        while not fires and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.02)
        assert fires, "expected first fire within 1s"
    finally:
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_due_loop_does_not_double_fire_in_flight_job(tmp_path):
    """While a fire is in flight, the due loop must not dispatch a second one."""
    _seed_interval_job(tmp_path, "tick", interval_seconds=1, initial_delay_seconds=0)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)
    fires = 0
    release = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        nonlocal fires
        fires += 1
        await release.wait()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        # Wait for first fire to start.
        deadline = asyncio.get_event_loop().time() + 1.0
        while fires == 0 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.02)
        assert fires == 1

        # Even if we force more due-loop ticks with an overdue next_fire_at
        # (hypothetically), phase=="firing" must block double-dispatch.
        state = scheduler._job_state["tick"]
        assert state.phase == "firing"
        # Artificially push next_fire_at into the past even though phase=firing.
        # The due loop's _collect_due_jobs should skip firing jobs.
        state.next_fire_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        scheduler._due_loop_wakeup.set()
        await asyncio.sleep(0.2)
        assert fires == 1, f"expected 1 fire while first was in flight, got {fires}"

        # Releasing the first fire lets the cycle progress.
        release.set()
    finally:
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_due_loop_advances_next_fire_after_completion(tmp_path):
    """Interval job next_fire_at = completion_time + interval (not start_time + interval)."""
    _seed_interval_job(tmp_path, "tick", interval_seconds=10, initial_delay_seconds=0)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)
    fired = asyncio.Event()
    completion_time: list[datetime] = []

    async def on_fire(job: ScheduledJob) -> None:
        await asyncio.sleep(0.2)
        completion_time.append(datetime.now(timezone.utc))
        fired.set()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.wait_for(fired.wait(), timeout=2.0)
        # Let finally block advance next_fire_at.
        await asyncio.sleep(0.1)
        state = scheduler._job_state["tick"]
        assert state.phase == "sleeping"
        assert state.next_fire_at is not None
        delta = (state.next_fire_at - completion_time[0]).total_seconds()
        assert 9.5 <= delta <= 10.5, f"expected ~10s from completion, got {delta:.2f}s"
    finally:
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_due_loop_recovers_after_simulated_host_suspend(tmp_path, monkeypatch):
    """Regression for Mac-laptop-suspend bug: wall-clock jump is recovered on next tick."""
    _seed_interval_job(tmp_path, "tick", interval_seconds=60, initial_delay_seconds=60)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)
    start_now = datetime.now(timezone.utc)
    clock = {"now": start_now}

    def fake_now() -> datetime:
        return clock["now"]

    monkeypatch.setattr(scheduler, "_now", fake_now)

    fired = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        fired.set()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        # Wait for initial due-loop iteration so state is stable.
        await asyncio.sleep(0.2)
        state = scheduler._job_state["tick"]
        assert state.phase == "sleeping"
        # next_fire_at was computed at start_now + 60s.
        assert state.next_fire_at is not None
        expected_next = start_now + timedelta(seconds=60)
        assert abs((state.next_fire_at - expected_next).total_seconds()) < 0.5

        # Simulate host wake: jump wall clock past next_fire_at.
        clock["now"] = expected_next + timedelta(seconds=1)
        scheduler._due_loop_wakeup.set()

        # Recovery must happen within one tick, not 60s.
        await asyncio.wait_for(fired.wait(), timeout=1.0)
    finally:
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_due_loop_wakeup_after_short_interval_fire(tmp_path):
    """Prove the wakeup-event kicks the loop so short-interval cadence isn't stretched by max_tick."""
    # max_tick intentionally large to isolate the wakeup mechanism.
    _seed_interval_job(tmp_path, "tick", interval_seconds=1, initial_delay_seconds=0)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=30.0)
    times: list[datetime] = []

    async def on_fire(job: ScheduledJob) -> None:
        times.append(datetime.now(timezone.utc))

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        deadline = asyncio.get_event_loop().time() + 3.0
        while len(times) < 2 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.05)
        assert len(times) >= 2, f"expected ≥2 fires within 3s, got {len(times)}"
        gap = (times[1] - times[0]).total_seconds()
        # Allow some slack but must be much less than max_tick=30.
        assert gap < 3.0, f"gap between fires={gap:.2f}s should be ≈1s, not ≈max_tick"
    finally:
        await _stop(scheduler, run_task)


# ----------------------------------------------------------------------
# Reload semantics
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reload_remove_cancels_in_flight_fire(tmp_path):
    """Removing a firing job via reload cancels the in-flight task and drops state."""
    path = _seed_interval_job(tmp_path, "tick", interval_seconds=1, initial_delay_seconds=0)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)
    release = asyncio.Event()
    fired = asyncio.Event()
    cancelled = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        fired.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.wait_for(fired.wait(), timeout=1.5)
        assert "tick" in scheduler._fire_tasks

        # Remove the file and trigger reload.
        path.unlink()
        await scheduler.reload_now()

        # In-flight fire should have been cancelled; state dropped.
        await asyncio.wait_for(cancelled.wait(), timeout=1.0)
        assert "tick" not in scheduler._fire_tasks
        assert "tick" not in scheduler._job_state
    finally:
        release.set()
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_reload_add_does_not_disturb_unrelated_in_flight_fire(tmp_path):
    """A reload that only adds a new job must not cancel an unrelated in-flight fire."""
    _seed_interval_job(tmp_path, "tick", interval_seconds=1, initial_delay_seconds=0)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)
    release = asyncio.Event()
    fired = asyncio.Event()
    cancelled = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        if job.name == "tick":
            fired.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.wait_for(fired.wait(), timeout=1.5)
        original_task = scheduler._fire_tasks["tick"]

        # Add an unrelated new job on disk and trigger reload.
        _seed_interval_job(tmp_path, "other", interval_seconds=60, initial_delay_seconds=60)
        await scheduler.reload_now()

        # The in-flight "tick" task must still be the same object, not cancelled.
        assert scheduler._fire_tasks.get("tick") is original_task
        assert not original_task.cancelled()
        assert not cancelled.is_set()
        # And the new job should be registered.
        assert "other" in scheduler._job_state
    finally:
        release.set()
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_reload_update_recomputes_next_fire(tmp_path):
    """Changing a cron expression on disk recomputes next_fire_at."""
    path = _seed_cron_job(tmp_path, cron="0 8 * * *")
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        first = scheduler._job_state["daily"].next_fire_at
        assert first is not None and first.hour == 8

        _write_yaml(
            path,
            """
            name: daily
            enabled: true
            platform: discord
            channel_id: "100"
            thread_id: "200"
            prompt: summarize
            cron: "30 15 * * *"
            """,
        )
        await scheduler.reload_now()
        second = scheduler._job_state["daily"].next_fire_at
        assert second is not None
        assert second.hour == 15 and second.minute == 30
    finally:
        await _stop(scheduler, run_task)


# ----------------------------------------------------------------------
# fire_job_now
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_job_now_returns_ok_and_dispatches(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)
    fired: asyncio.Event = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        fired.set()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        result = await scheduler.fire_job_now("daily")
        assert result == "ok"
        await asyncio.wait_for(fired.wait(), timeout=1.0)
    finally:
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_fire_job_now_returns_not_found(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        assert await scheduler.fire_job_now("unknown") == "not_found"
    finally:
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_fire_job_now_returns_scheduler_down(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)
    # Job is configured (_jobs_by_name populated in __init__) but scheduler
    # has never been run — _on_fire is None and state is empty.
    assert await scheduler.fire_job_now("daily") == "scheduler_down"


@pytest.mark.asyncio
async def test_fire_job_now_returns_already_firing(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)
    release = asyncio.Event()
    fired = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        fired.set()
        await release.wait()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        # First manual fire starts.
        assert await scheduler.fire_job_now("daily") == "ok"
        await asyncio.wait_for(fired.wait(), timeout=1.0)
        assert scheduler._job_state["daily"].phase == "firing"
        # Second manual fire must be refused.
        assert await scheduler.fire_job_now("daily") == "already_firing"
    finally:
        release.set()
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_manual_fire_preserves_future_scheduled_next_fire(tmp_path):
    """Manual fire must not displace an already-scheduled future next_fire."""
    _seed_interval_job(tmp_path, "hourly", interval_seconds=3600, initial_delay_seconds=0)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)
    fired = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        fired.set()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        # Pin the scheduled next_fire 30 minutes into the future.
        future = datetime.now(timezone.utc) + timedelta(seconds=1800)
        scheduler._job_state["hourly"].next_fire_at = future

        assert await scheduler.fire_job_now("hourly") == "ok"
        await asyncio.wait_for(fired.wait(), timeout=1.0)
        await asyncio.sleep(0.1)  # let finally run

        state = scheduler._job_state["hourly"]
        assert state.phase == "sleeping"
        assert state.next_fire_at is not None
        # The pinned future time must be preserved, not advanced by +3600s.
        assert abs((state.next_fire_at - future).total_seconds()) < 0.5
    finally:
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_manual_fire_advances_when_pinned_already_overdue(tmp_path):
    """If the pinned next_fire is already in the past, fall back to completion + interval."""
    _seed_interval_job(tmp_path, "tick", interval_seconds=10, initial_delay_seconds=0)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)
    fired = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        fired.set()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        # Pinned next_fire in the past — manual fire can't "restore" it (double-fire risk).
        scheduler._job_state["tick"].next_fire_at = datetime.now(timezone.utc) - timedelta(seconds=5)

        assert await scheduler.fire_job_now("tick") == "ok"
        await asyncio.wait_for(fired.wait(), timeout=1.0)
        await asyncio.sleep(0.1)

        state = scheduler._job_state["tick"]
        assert state.phase == "sleeping"
        assert state.next_fire_at is not None
        delta = (state.next_fire_at - datetime.now(timezone.utc)).total_seconds()
        # Should be ~+10s, not a past time.
        assert 9.0 <= delta <= 11.0, f"expected ~10s in the future, got {delta:.2f}s"
    finally:
        await _stop(scheduler, run_task)


# ----------------------------------------------------------------------
# restart_due_loop + supervisor semantics
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_due_loop_replaces_task(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)
    scheduler._min_restart_interval_seconds = 0.0

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        prior = scheduler._due_loop_task
        assert prior is not None

        ok = await scheduler.restart_due_loop(reason="task_done_unexpectedly")
        assert ok is True
        assert scheduler._due_loop_task is not prior
        snapshot = scheduler.get_due_loop_runtime_state()
        assert snapshot is not None
        assert snapshot.last_restart_reason == "task_done_unexpectedly"
    finally:
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_restart_due_loop_is_rate_limited(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)
    scheduler._min_restart_interval_seconds = 60.0

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        ok1 = await scheduler.restart_due_loop(reason="test")
        ok2 = await scheduler.restart_due_loop(reason="test")
        assert ok1 is True
        assert ok2 is False  # rate-limited
    finally:
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_get_due_loop_runtime_state_snapshot_is_copy(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path, due_loop_max_tick_seconds=0.05)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.1)
        snapshot = scheduler.get_due_loop_runtime_state()
        assert snapshot is not None
        snapshot.last_restart_reason = "mutated"
        internal = scheduler._due_loop_state
        assert internal is not None
        assert internal.last_restart_reason is None
    finally:
        await _stop(scheduler, run_task)


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
        snapshots[0].phase = "firing"
        assert scheduler._job_state["daily"].phase == "sleeping"

        single = scheduler.get_job_runtime_state("daily")
        assert single is not None
        assert single.phase == "sleeping"
    finally:
        await _stop(scheduler, run_task)


@pytest.mark.asyncio
async def test_compute_job_next_run_at_uses_cron(tmp_path):
    _seed_cron_job(tmp_path, cron="30 9 * * *")
    scheduler = _mk_scheduler(tmp_path)
    next_fire = scheduler.compute_job_next_run_at("daily")
    assert next_fire is not None
    assert next_fire.minute == 30
    assert next_fire.hour == 9
