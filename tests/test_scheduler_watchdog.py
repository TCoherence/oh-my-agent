"""Tests for the central due-loop scheduler: liveness, restart, manual fire."""
from __future__ import annotations

import asyncio
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oh_my_agent.automation.scheduler import (
    DueLoopRuntimeState,
    HealthFinding,
    JobRuntimeState,
    Scheduler,
    ScheduledJob,
)


def _write_yaml(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def _mk_scheduler(tmp_path: Path, *, reload_interval: float = 0.05) -> Scheduler:
    storage = tmp_path / "automations"
    storage.mkdir(exist_ok=True)
    return Scheduler(
        storage_dir=storage,
        reload_interval_seconds=reload_interval,
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


def _seed_interval_job(
    tmp_path: Path,
    name: str = "tick",
    interval_seconds: int = 60,
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
        prompt: tick
        interval_seconds: {interval_seconds}
        initial_delay_seconds: {initial_delay_seconds}
        """,
    )
    return path


# ---------------------------------------------------------------------------
# 1. Cron due-fire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_loop_fires_cron_job_when_due(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)
    scheduler.due_loop_max_tick_seconds = 0.05
    fired = asyncio.Event()

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
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 2. Interval initial_delay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_loop_fires_interval_job_with_initial_delay(tmp_path):
    _seed_interval_job(tmp_path, name="tick", interval_seconds=10, initial_delay_seconds=0)
    scheduler = _mk_scheduler(tmp_path)
    scheduler.due_loop_max_tick_seconds = 0.05
    fired = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        fired.set()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        # initial_delay=0 → fires on first due-loop tick.
        await asyncio.wait_for(fired.wait(), timeout=1.0)
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 3. Overlap guard: in-flight job not double-dispatched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_loop_does_not_double_fire_in_flight_job(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)
    scheduler.due_loop_max_tick_seconds = 0.05
    release = asyncio.Event()
    fire_count = 0

    async def on_fire(job: ScheduledJob) -> None:
        nonlocal fire_count
        fire_count += 1
        await release.wait()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
        state = scheduler._job_state["daily"]
        state.next_fire_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        scheduler._due_loop_wakeup.set()
        # Wait long enough for several due-loop ticks.
        await asyncio.sleep(0.5)
        assert fire_count == 1
        assert state.phase == "firing"
        # Force-mark next_fire_at overdue again — overlap guard must hold.
        state.next_fire_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        scheduler._due_loop_wakeup.set()
        await asyncio.sleep(0.3)
        assert fire_count == 1
    finally:
        release.set()
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 4. Post-completion advance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_loop_advances_next_fire_after_completion(tmp_path):
    _seed_interval_job(tmp_path, name="tick", interval_seconds=10, initial_delay_seconds=0)
    scheduler = _mk_scheduler(tmp_path)
    scheduler.due_loop_max_tick_seconds = 0.05
    fired = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        fired.set()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.wait_for(fired.wait(), timeout=1.0)
        # Wait for finally to run + state to flip back to sleeping.
        for _ in range(50):
            state = scheduler._job_state["tick"]
            if state.phase == "sleeping":
                break
            await asyncio.sleep(0.01)
        state = scheduler._job_state["tick"]
        assert state.phase == "sleeping"
        delta = (state.next_fire_at - datetime.now(timezone.utc)).total_seconds()
        # next_fire = completion_time + 10s; allow 0.5s slack.
        assert 9.5 < delta <= 10.0
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 5. Host-suspend recovery (regression for the reported bug)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_loop_recovers_after_simulated_host_suspend(tmp_path, monkeypatch):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)
    scheduler.due_loop_max_tick_seconds = 0.05

    real_now_fn = scheduler._now
    clock = {"now": real_now_fn()}
    monkeypatch.setattr(scheduler, "_now", lambda: clock["now"])

    fired = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        fired.set()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        # Wait for the scheduler to reach "sleeping" with a future fire.
        for _ in range(50):
            state = scheduler._job_state.get("daily")
            if state is not None and state.phase == "sleeping":
                break
            await asyncio.sleep(0.01)
        state = scheduler._job_state["daily"]
        # Pin the schedule 60s in the future.
        state.next_fire_at = clock["now"] + timedelta(seconds=60)

        # Simulate host wake skipping wall clock forward by 61s. NOTE we
        # do NOT advance asyncio's monotonic clock — the bug is that
        # monotonic time freezes during host suspend. Recovery must
        # happen on the next due-loop tick reading the new wall clock.
        clock["now"] = clock["now"] + timedelta(seconds=61)
        scheduler._due_loop_wakeup.set()

        await asyncio.wait_for(fired.wait(), timeout=1.0)
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 6. Reload-remove cancels in-flight fire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reload_remove_cancels_in_flight_fire(tmp_path):
    yaml_path = _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)
    scheduler.due_loop_max_tick_seconds = 0.05
    release = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        await release.wait()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
        state = scheduler._job_state["daily"]
        state.next_fire_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        scheduler._due_loop_wakeup.set()

        # Wait for the fire to become in-flight.
        for _ in range(50):
            if scheduler._job_state["daily"].phase == "firing":
                break
            await asyncio.sleep(0.01)
        assert "daily" in scheduler._fire_tasks

        # Trigger reload that removes the job.
        yaml_path.unlink()
        await scheduler.reload_now()

        assert "daily" not in scheduler._fire_tasks
        assert "daily" not in scheduler._job_state
    finally:
        release.set()
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 7. Reload-update recomputes next_fire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reload_update_recomputes_next_fire(tmp_path):
    yaml_path = _seed_cron_job(tmp_path, cron="0 8 * * *")
    scheduler = _mk_scheduler(tmp_path)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
        first_next = scheduler._job_state["daily"].next_fire_at
        assert first_next is not None
        assert first_next.hour == 8

        _write_yaml(
            yaml_path,
            """
            name: daily
            enabled: true
            platform: discord
            channel_id: "100"
            thread_id: "200"
            prompt: summarize
            cron: "30 14 * * *"
            """,
        )
        await scheduler.reload_now()
        new_next = scheduler._job_state["daily"].next_fire_at
        assert new_next is not None
        assert new_next.hour == 14
        assert new_next.minute == 30
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 8. fire_job_now returns "already_firing" when in-flight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_job_now_returns_already_firing_when_in_flight(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)
    scheduler.due_loop_max_tick_seconds = 0.05
    release = asyncio.Event()
    fire_count = 0

    async def on_fire(job: ScheduledJob) -> None:
        nonlocal fire_count
        fire_count += 1
        await release.wait()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
        state = scheduler._job_state["daily"]
        state.next_fire_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        scheduler._due_loop_wakeup.set()
        for _ in range(50):
            if scheduler._job_state["daily"].phase == "firing":
                break
            await asyncio.sleep(0.01)

        result = await scheduler.fire_job_now("daily")
        assert result == "already_firing"
        assert fire_count == 1
    finally:
        release.set()
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 9. fire_job_now returns "ok" / "not_found" / "scheduler_down"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_job_now_returns_ok_and_dispatches(tmp_path):
    _seed_interval_job(tmp_path, name="tick", interval_seconds=3600, initial_delay_seconds=3600)
    scheduler = _mk_scheduler(tmp_path)
    scheduler.due_loop_max_tick_seconds = 0.05
    fired = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        fired.set()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
        # Sleeping with next_fire_at far in the future.
        assert scheduler._job_state["tick"].phase == "sleeping"
        result = await scheduler.fire_job_now("tick")
        assert result == "ok"
        await asyncio.wait_for(fired.wait(), timeout=1.0)
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


@pytest.mark.asyncio
async def test_fire_job_now_returns_not_found_for_unknown(tmp_path):
    scheduler = _mk_scheduler(tmp_path)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
        result = await scheduler.fire_job_now("does-not-exist")
        assert result == "not_found"
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


@pytest.mark.asyncio
async def test_fire_job_now_returns_scheduler_down_when_not_running(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)
    # Manually seed _job_state since scheduler.run() never started.
    scheduler._job_state["daily"] = JobRuntimeState(
        name="daily",
        phase="sleeping",
        next_fire_at=datetime.now(timezone.utc) + timedelta(hours=1),
        fire_started_at=None,
        last_progress_at=datetime.now(timezone.utc),
    )
    result = await scheduler.fire_job_now("daily")
    assert result == "scheduler_down"


# ---------------------------------------------------------------------------
# 10. Supervisor restarts due loop when dead
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_restarts_due_loop_when_dead(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)
    scheduler._min_restart_interval_seconds = 0.0

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
        old_task = scheduler._due_loop_task
        assert old_task is not None

        # Externally kill the due-loop task.
        old_task.cancel()
        await asyncio.gather(old_task, return_exceptions=True)
        assert old_task.done()

        findings = scheduler.evaluate_job_health()
        due_findings = [f for f in findings if f.scope == "due_loop"]
        assert due_findings, findings
        assert due_findings[0].reason == "task_done_unexpectedly"

        ok = await scheduler.restart_due_loop(reason="task_done_unexpectedly")
        assert ok is True
        assert scheduler._due_loop_task is not old_task
        assert scheduler._due_loop_task is not None
        assert not scheduler._due_loop_task.done()
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 11. restart_due_loop is rate-limited
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_due_loop_is_rate_limited(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)
    scheduler._min_restart_interval_seconds = 60.0

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
        ok1 = await scheduler.restart_due_loop(reason="test")
        assert ok1 is True
        ok2 = await scheduler.restart_due_loop(reason="test")
        assert ok2 is False
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 12. Per-job restart is gone — missed_fire is informational only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_no_longer_restarts_per_job_on_missed_fire(tmp_path):
    """missed_fire findings are now informational (no per-job restart path)."""
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)

        # restart_job has been removed entirely under the new model.
        assert not hasattr(scheduler, "restart_job")

        # Simulate an overdue sleeping job — would have been "missed_fire"
        # under the old model. evaluate_job_health still surfaces it for
        # /doctor visibility, but the supervisor no longer restarts.
        state = scheduler._job_state["daily"]
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        state.phase = "sleeping"
        state.next_fire_at = past
        state.last_progress_at = past - timedelta(minutes=5)

        findings = scheduler.evaluate_job_health()
        job_findings = [f for f in findings if f.scope == "job"]
        assert any(f.reason == "missed_fire" for f in job_findings)
        # No restart machinery exists — informational only.
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 13. get_due_loop_runtime_state returns a snapshot copy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_due_loop_runtime_state_snapshot(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
        snap = scheduler.get_due_loop_runtime_state()
        assert isinstance(snap, DueLoopRuntimeState)
        assert snap.last_progress_at is not None
        assert snap.last_restart_at is None
        assert snap.last_restart_reason is None
        assert snap.restart_in_progress is False

        # Mutating the snapshot must not affect internal state.
        snap.last_restart_reason = "mutated"
        internal = scheduler._due_loop_state
        assert internal is not None
        assert internal.last_restart_reason is None
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 14. Wakeup event closes short-interval latency gap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_due_loop_wakeup_after_short_interval_fire(tmp_path):
    """Without _fire_job_once.finally setting the wakeup, this would
    take ~30s (the max_tick) instead of ~1s."""
    _seed_interval_job(tmp_path, name="tick", interval_seconds=1, initial_delay_seconds=0)
    scheduler = _mk_scheduler(tmp_path)
    # Intentionally large max_tick — the wakeup event is what makes
    # the second fire happen quickly, not the bounded tick.
    scheduler.due_loop_max_tick_seconds = 30.0
    fire_times: list[datetime] = []

    async def on_fire(job: ScheduledJob) -> None:
        fire_times.append(datetime.now(timezone.utc))

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        # Wait for two fires.
        for _ in range(300):
            if len(fire_times) >= 2:
                break
            await asyncio.sleep(0.05)
        assert len(fire_times) >= 2
        gap = (fire_times[1] - fire_times[0]).total_seconds()
        # interval=1s + slack; should NOT take ~30s.
        assert gap < 3.0, f"second fire took {gap}s — wakeup event missed?"
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 15. Manual fire preserves a future scheduled next_fire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_fire_preserves_future_scheduled_next_fire(tmp_path):
    _seed_interval_job(tmp_path, name="tick", interval_seconds=3600, initial_delay_seconds=3600)
    scheduler = _mk_scheduler(tmp_path)
    scheduler.due_loop_max_tick_seconds = 0.05
    fired = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        fired.set()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
        # Pin next_fire to 1800s in the future (mid-window).
        original_next = datetime.now(timezone.utc) + timedelta(seconds=1800)
        scheduler._job_state["tick"].next_fire_at = original_next

        result = await scheduler.fire_job_now("tick")
        assert result == "ok"
        await asyncio.wait_for(fired.wait(), timeout=1.0)

        # Wait for finally to flip back to sleeping.
        for _ in range(50):
            if scheduler._job_state["tick"].phase == "sleeping":
                break
            await asyncio.sleep(0.01)
        state = scheduler._job_state["tick"]
        assert state.phase == "sleeping"
        # Restored pinned next_fire — NOT now+3600.
        delta = (state.next_fire_at - original_next).total_seconds()
        assert abs(delta) < 0.5, f"next_fire shifted by {delta}s; should be pinned"
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 16. Manual fire advances when pinned was already overdue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_fire_advances_when_pinned_already_overdue(tmp_path):
    _seed_interval_job(tmp_path, name="tick", interval_seconds=10, initial_delay_seconds=600)
    scheduler = _mk_scheduler(tmp_path)
    scheduler.due_loop_max_tick_seconds = 0.05
    fired = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        fired.set()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
        # Make the pinned time already overdue (5s in the past).
        scheduler._job_state["tick"].next_fire_at = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        )
        # But the job is still phase=sleeping — fire_job_now will dispatch.
        # Note: since next_fire is in the past, the due loop could also
        # dispatch. To isolate the manual path, briefly mark firing then
        # back to sleeping... actually simpler: just call fire_job_now
        # quickly before the due loop catches up.

        # Cancel the due loop temporarily so only manual fires happen.
        scheduler._due_loop_task.cancel()
        await asyncio.gather(scheduler._due_loop_task, return_exceptions=True)
        scheduler._due_loop_task = None

        # Re-confirm the pinned overdue state (cancel didn't change it).
        scheduler._job_state["tick"].next_fire_at = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        )

        result = await scheduler.fire_job_now("tick")
        assert result == "ok"
        await asyncio.wait_for(fired.wait(), timeout=1.0)

        # Wait for finally.
        for _ in range(50):
            if scheduler._job_state["tick"].phase == "sleeping":
                break
            await asyncio.sleep(0.01)
        state = scheduler._job_state["tick"]
        assert state.phase == "sleeping"
        # Pinned was overdue → fall back to completion+10s, NOT pinned.
        delta = (state.next_fire_at - datetime.now(timezone.utc)).total_seconds()
        assert 9.0 < delta <= 10.0, f"expected ~10s, got {delta}s"
    finally:
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# 17. compute_display_next_run_at preserves pinned during manual fire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_display_next_run_at_preserves_pinned_during_firing(tmp_path):
    _seed_interval_job(tmp_path, name="tick", interval_seconds=3600, initial_delay_seconds=3600)
    scheduler = _mk_scheduler(tmp_path)
    scheduler.due_loop_max_tick_seconds = 0.05
    release = asyncio.Event()

    async def on_fire(job: ScheduledJob) -> None:
        await release.wait()

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
        original_next = datetime.now(timezone.utc) + timedelta(seconds=1800)
        scheduler._job_state["tick"].next_fire_at = original_next

        result = await scheduler.fire_job_now("tick")
        assert result == "ok"
        # Wait for the fire to be in-flight.
        for _ in range(50):
            if scheduler._job_state["tick"].phase == "firing":
                break
            await asyncio.sleep(0.01)
        assert scheduler._job_state["tick"].phase == "firing"

        # While firing, display should return pinned (NOT now+3600).
        displayed = scheduler.compute_display_next_run_at("tick")
        assert displayed is not None
        delta = (displayed - original_next).total_seconds()
        assert abs(delta) < 0.5, f"display drifted by {delta}s; should equal pinned"

        release.set()
        # Wait for the fire to complete.
        for _ in range(50):
            if scheduler._job_state["tick"].phase == "sleeping":
                break
            await asyncio.sleep(0.01)
        # After completion the in-memory next_fire is the restored pinned;
        # display still equals pinned.
        displayed_after = scheduler.compute_display_next_run_at("tick")
        assert displayed_after is not None
        delta_after = (displayed_after - original_next).total_seconds()
        assert abs(delta_after) < 0.5
    finally:
        release.set()
        scheduler.stop()
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# Surviving reload-loop coverage from the prior watchdog tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_reload_loop_replaces_task(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)
    scheduler._min_restart_interval_seconds = 0.0

    async def on_fire(job: ScheduledJob) -> None:
        pass

    run_task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
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
        await asyncio.sleep(0.05)
        snapshots = scheduler.list_job_runtime_state()
        assert len(snapshots) == 1
        assert snapshots[0].name == "daily"

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
        await asyncio.sleep(0.05)
        assert scheduler._reload_state is not None
        scheduler._reload_state.last_progress_at = datetime.now(timezone.utc) - timedelta(hours=1)

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


@pytest.mark.asyncio
async def test_evaluate_job_health_returns_empty_when_healthy(tmp_path):
    _seed_cron_job(tmp_path)
    scheduler = _mk_scheduler(tmp_path)
    fired = asyncio.Event()

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
