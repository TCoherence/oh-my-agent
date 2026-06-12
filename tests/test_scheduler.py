import asyncio
import textwrap
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from oh_my_agent.automation import ScheduledJob, Scheduler, build_scheduler_from_config
from oh_my_agent.automation.scheduler import (
    _matches_cron,
    _next_cron_fire,
    _parse_cron_expression,
)


def _write_yaml(path, text: str) -> None:
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def test_build_scheduler_disabled_returns_none(tmp_path):
    assert build_scheduler_from_config(
        {"automations": {"enabled": False}},
        project_root=tmp_path,
    ) is None


def test_build_scheduler_defaults_to_enabled_when_storage_dir_is_set(tmp_path):
    storage_dir = tmp_path / "automations"
    scheduler = build_scheduler_from_config(
        {"automations": {"storage_dir": str(storage_dir)}},
        project_root=tmp_path,
    )
    assert scheduler is not None
    assert scheduler.jobs == []
    assert storage_dir.exists()
    assert scheduler.timezone_name.endswith("(local default)")


def test_build_scheduler_accepts_explicit_iana_timezone(tmp_path):
    storage_dir = tmp_path / "automations"
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "storage_dir": str(storage_dir),
                "timezone": "America/Los_Angeles",
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    assert scheduler.timezone_name == "America/Los_Angeles"
    assert isinstance(scheduler._timezone, ZoneInfo)


def test_build_scheduler_rejects_invalid_timezone(tmp_path):
    with pytest.raises(ValueError, match="automations.timezone"):
        build_scheduler_from_config(
            {
                "automations": {
                    "storage_dir": str(tmp_path / "automations"),
                    "timezone": "Mars/Olympus_Mons",
                }
            },
            project_root=tmp_path,
        )


def test_build_scheduler_uses_storage_dir_and_parses_interval_job(tmp_path):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "daily.yaml",
        """
        name: daily
        enabled: true
        platform: discord
        channel_id: "123"
        thread_id: "456"
        delivery: channel
        prompt: summarize
        agent: claude
        interval_seconds: 60
        initial_delay_seconds: 5
        timeout_seconds: 900
        max_turns: 40
        author: scheduler
        """,
    )
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "enabled": True,
                "storage_dir": str(storage_dir),
                "reload_interval_seconds": 5,
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    assert len(scheduler.jobs) == 1
    job = scheduler.jobs[0]
    assert job.name == "daily"
    assert job.platform == "discord"
    assert job.channel_id == "123"
    assert job.thread_id == "456"
    assert job.delivery == "channel"
    assert job.prompt == "summarize"
    assert job.agent == "claude"
    assert job.interval_seconds == 60
    assert job.initial_delay_seconds == 5
    assert job.timeout_seconds == 900
    assert job.max_turns == 40
    assert job.cron is None


def test_build_scheduler_rejects_invalid_timeout_or_max_turns(tmp_path, caplog):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "bad.yaml",
        """
        name: bad
        enabled: true
        platform: discord
        channel_id: "123"
        prompt: summarize
        interval_seconds: 60
        timeout_seconds: 0
        """,
    )
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "enabled": True,
                "storage_dir": str(storage_dir),
                "reload_interval_seconds": 5,
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    assert scheduler.jobs == []
    assert "timeout_seconds must be > 0" in caplog.text


def test_build_scheduler_warns_when_non_claude_agent_uses_max_turns(tmp_path, caplog):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "warn.yaml",
        """
        name: warn
        enabled: true
        platform: discord
        channel_id: "123"
        prompt: summarize
        interval_seconds: 60
        agent: gemini
        max_turns: 50
        """,
    )
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "enabled": True,
                "storage_dir": str(storage_dir),
                "reload_interval_seconds": 5,
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    assert len(scheduler.jobs) == 1
    assert "only Claude currently supports max_turns overrides" in caplog.text


def test_build_scheduler_includes_disabled_automation_in_operator_snapshot(tmp_path):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "disabled.yaml",
        """
        name: disabled-report
        enabled: false
        platform: discord
        channel_id: "123"
        delivery: channel
        prompt: summarize
        interval_seconds: 60
        """,
    )
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "enabled": True,
                "storage_dir": str(storage_dir),
                "reload_interval_seconds": 5,
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    assert scheduler.jobs == []
    records = scheduler.list_automations()
    assert len(records) == 1
    assert records[0].name == "disabled-report"
    assert records[0].enabled is False


def test_build_scheduler_resolves_relative_storage_dir(tmp_path):
    storage_dir = tmp_path / "relative-automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "cron.yaml",
        """
        name: daily-standup
        enabled: true
        platform: discord
        channel_id: "123"
        delivery: channel
        prompt: summarize
        cron: "0 9 * * *"
        """,
    )
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "enabled": True,
                "storage_dir": "relative-automations",
                "reload_interval_seconds": 5,
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    assert scheduler.jobs[0].source_path == (storage_dir / "cron.yaml").resolve()


def test_build_scheduler_dm_uses_default_target_user_id(tmp_path):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "dm.yaml",
        """
        name: dm-report
        enabled: true
        platform: discord
        channel_id: "123"
        delivery: dm
        prompt: report
        interval_seconds: 60
        """,
    )
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "enabled": True,
                "storage_dir": str(storage_dir),
                "reload_interval_seconds": 5,
            }
        },
        project_root=tmp_path,
        default_target_user_id="42",
    )
    assert scheduler is not None
    job = scheduler.jobs[0]
    assert job.delivery == "dm"
    assert job.target_user_id == "42"


def test_build_scheduler_invalid_job_is_logged_and_skipped(tmp_path, caplog):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "bad.yaml",
        """
        name: bad
        enabled: true
        platform: discord
        channel_id: "123"
        prompt: x
        """,
    )
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "enabled": True,
                "storage_dir": str(storage_dir),
                "reload_interval_seconds": 5,
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    assert scheduler.jobs == []
    assert "one of cron or interval_seconds is required" in caplog.text


def test_build_scheduler_duplicate_names_are_logged_and_skipped(tmp_path, caplog):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "a.yaml",
        """
        name: dup
        enabled: true
        platform: discord
        channel_id: "123"
        prompt: first
        interval_seconds: 60
        """,
    )
    _write_yaml(
        storage_dir / "b.yaml",
        """
        name: dup
        enabled: true
        platform: discord
        channel_id: "123"
        prompt: second
        interval_seconds: 60
        """,
    )
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "enabled": True,
                "storage_dir": str(storage_dir),
                "reload_interval_seconds": 5,
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    assert scheduler.jobs == []
    assert "Automation name conflict" in caplog.text


def test_build_scheduler_rejects_cron_with_initial_delay(tmp_path, caplog):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "cron.yaml",
        """
        name: bad-cron
        enabled: true
        platform: discord
        channel_id: "123"
        prompt: summarize
        cron: "0 9 * * *"
        initial_delay_seconds: 10
        """,
    )
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "enabled": True,
                "storage_dir": str(storage_dir),
                "reload_interval_seconds": 5,
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    assert scheduler.jobs == []
    assert "initial_delay_seconds is not supported with cron" in caplog.text


def test_build_scheduler_empty_dir_returns_live_scheduler(tmp_path):
    storage_dir = tmp_path / "automations"
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "enabled": True,
                "storage_dir": str(storage_dir),
                "reload_interval_seconds": 5,
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    assert scheduler.jobs == []
    assert storage_dir.exists()


def test_cron_parser_accepts_standard_fields():
    spec = _parse_cron_expression("*/15 9-17 * * 1-5")
    assert 0 in spec.minute
    assert 45 in spec.minute
    assert 9 in spec.hour
    assert 17 in spec.hour
    assert spec.day_wildcard is True
    assert spec.weekday_wildcard is False


def test_next_cron_fire_uses_expected_weekday_semantics():
    spec = _parse_cron_expression("0 9 * * MON-FRI")
    now = datetime(2026, 3, 7, 18, 30, tzinfo=timezone.utc)  # Saturday
    next_fire = _next_cron_fire(spec, now)
    assert next_fire == datetime(2026, 3, 9, 9, 0, tzinfo=timezone.utc)


# =====================================================================
# Cron engine — _parse_cron_expression / _matches_cron / _next_cron_fire
# =====================================================================

_LA = ZoneInfo("America/Los_Angeles")


def test_next_cron_fire_spring_forward_gap_day():
    """US spring-forward 2026-03-08: wall times 02:00-02:59 don't exist in LA.

    The engine steps wall-clock minutes and matches on wall fields only, so a
    daily 02:30 job still matches wall 02:30 on the gap day; ZoneInfo resolves
    that nonexistent wall time via the pre-gap PST offset (fold=0), so the
    fire materializes at 03:30 PDT real time. Documented behavior: the job is
    not skipped on the gap day — it lands one hour later in real time.
    """
    spec = _parse_cron_expression("30 2 * * *")
    now = datetime(2026, 3, 8, 1, 0, tzinfo=_LA)
    next_fire = _next_cron_fire(spec, now)
    assert next_fire.replace(tzinfo=None) == datetime(2026, 3, 8, 2, 30)
    assert next_fire.astimezone(timezone.utc) == datetime(
        2026, 3, 8, 10, 30, tzinfo=timezone.utc
    )


def test_next_cron_fire_fall_back_ambiguous_hour():
    """US fall-back 2026-11-01: wall times 01:00-01:59 occur twice in LA.

    The engine matches the first wall occurrence (fold=0, PDT). Recomputing
    from that fire steps forward past 02:00 wall time, so the repeated
    fold=1 (PST) hour never produces a second fire. Documented behavior:
    exactly one fire per day on the ambiguous wall time.
    """
    spec = _parse_cron_expression("30 1 * * *")
    now = datetime(2026, 11, 1, 0, 50, tzinfo=_LA)
    next_fire = _next_cron_fire(spec, now)
    assert next_fire.replace(tzinfo=None) == datetime(2026, 11, 1, 1, 30)
    assert next_fire.fold == 0
    assert next_fire.astimezone(timezone.utc) == datetime(
        2026, 11, 1, 8, 30, tzinfo=timezone.utc
    )  # PDT (-7): the first occurrence

    # The post-fire recompute lands on tomorrow, not the repeated fold=1 hour.
    after_fire = _next_cron_fire(spec, next_fire)
    assert after_fire.replace(tzinfo=None) == datetime(2026, 11, 2, 1, 30)


@pytest.mark.parametrize(
    ("expr", "now", "expected"),
    [
        # day=13 AND weekday=FRI both restricted → vixie OR: fires on the
        # next Friday (2026-02-06) even though it isn't the 13th...
        (
            "0 0 13 * 5",
            datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 2, 6, 0, 0, tzinfo=timezone.utc),
        ),
        # ...and on the 13th (a Monday in April 2026) even though it isn't Friday.
        (
            "0 0 13 * 5",
            datetime(2026, 4, 11, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc),
        ),
        # day wildcard → weekday-only filter: next Friday.
        (
            "0 0 * * 5",
            datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 17, 0, 0, tzinfo=timezone.utc),
        ),
        # weekday wildcard → day-only filter: the next 13th.
        (
            "0 0 13 * *",
            datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 3, 13, 0, 0, tzinfo=timezone.utc),
        ),
    ],
)
def test_next_cron_fire_vixie_day_or_weekday(expr, now, expected):
    assert _next_cron_fire(_parse_cron_expression(expr), now) == expected


def test_matches_cron_or_semantics_direct():
    spec = _parse_cron_expression("0 0 13 * 5")
    # Friday that isn't the 13th.
    assert _matches_cron(spec, datetime(2026, 2, 6, 0, 0, tzinfo=timezone.utc)) is True
    # The 13th that isn't a Friday (Monday).
    assert _matches_cron(spec, datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc)) is True
    # Thursday the 12th — neither.
    assert _matches_cron(spec, datetime(2026, 2, 12, 0, 0, tzinfo=timezone.utc)) is False


def test_cron_parser_named_months_and_weekdays():
    assert _parse_cron_expression("0 9 * JAN,JUL *").month == frozenset({1, 7})
    # Names are case-insensitive.
    assert _parse_cron_expression("0 9 * * mon-fri").weekday == frozenset({1, 2, 3, 4, 5})
    assert _parse_cron_expression("0 9 * * SUN").weekday == frozenset({0})


def test_cron_parser_normalizes_7_as_sunday():
    assert _parse_cron_expression("0 9 * * 7").weekday == frozenset({0})
    assert _parse_cron_expression("0 9 * * 0").weekday == frozenset({0})


def test_cron_parser_rejects_names_in_numeric_fields():
    with pytest.raises(ValueError):
        _parse_cron_expression("JAN 9 * * *")


@pytest.mark.parametrize(
    "expr",
    [
        "0 0 31 2 *",  # Feb 31
        "0 0 30 2 *",  # Feb 30 (leap years stop at 29)
        "0 0 31 4 *",  # Apr 31
        "0 0 31 4,6,9,11 *",  # only 30-day months allowed
    ],
)
def test_cron_parser_rejects_infeasible_day_month(expr):
    with pytest.raises(ValueError, match="never occur"):
        _parse_cron_expression(expr)


@pytest.mark.parametrize(
    "expr",
    [
        "0 0 29 2 *",  # Feb 29 exists in leap years
        "0 0 31 2,3 *",  # March rescues day 31
        "0 0 31 2 5",  # restricted weekday → vixie OR can fire on Fridays
        "0 0 31 * *",  # wildcard month includes 31-day months
        "0 0 */31 2 *",  # stepped field (not a literal '*') still covers day 1
    ],
)
def test_cron_parser_accepts_feasible_edge_specs(expr):
    _parse_cron_expression(expr)  # must not raise


def test_build_scheduler_rejects_infeasible_cron(tmp_path, caplog):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "bad-cron.yaml",
        """
        name: bad-cron
        enabled: true
        platform: discord
        channel_id: "123"
        prompt: summarize
        cron: "0 0 31 2 *"
        """,
    )
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "enabled": True,
                "storage_dir": str(storage_dir),
                "reload_interval_seconds": 5,
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    assert scheduler.jobs == []
    assert "never occur" in caplog.text


def test_compute_all_next_run_at_isolates_uncomputable_job(tmp_path, caplog):
    """One pathological spec maps to None instead of killing the whole call."""
    scheduler = Scheduler(
        storage_dir=tmp_path / "automations",
        reload_interval_seconds=60,
    )
    # Inject directly — file validation rejects infeasible crons up front.
    scheduler._jobs_by_name = {
        "good": ScheduledJob(
            name="good",
            platform="discord",
            channel_id="123",
            prompt="run",
            interval_seconds=60,
        ),
        "bad": ScheduledJob(
            name="bad",
            platform="discord",
            channel_id="123",
            prompt="run",
            cron="0 0 31 2 *",
        ),
    }
    result = scheduler.compute_all_next_run_at()
    assert result["bad"] is None
    assert result["good"] is not None  # interval jobs always get now + interval
    assert "failed to compute next fire time" in caplog.text


@pytest.mark.asyncio
async def test_scheduler_fires_interval_job(tmp_path):
    fired = asyncio.Event()
    calls: list[str] = []
    scheduler = Scheduler(
        storage_dir=tmp_path / "automations",
        reload_interval_seconds=60,
    )
    scheduler._jobs_by_name = {
        "tick": ScheduledJob(
            name="tick",
            platform="discord",
            channel_id="123",
            prompt="run",
            interval_seconds=60,
        )
    }

    async def on_fire(job: ScheduledJob) -> None:
        calls.append(job.name)
        fired.set()

    task = asyncio.create_task(scheduler.run(on_fire))
    await asyncio.wait_for(fired.wait(), timeout=1.0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert calls == ["tick"]


@pytest.mark.asyncio
async def test_scheduler_hot_reloads_add_modify_delete(tmp_path):
    storage_dir = tmp_path / "automations"
    scheduler = Scheduler(
        storage_dir=storage_dir,
        reload_interval_seconds=0.05,
    )
    seen: list[str] = []

    async def on_fire(job: ScheduledJob) -> None:
        seen.append(job.prompt)

    task = asyncio.create_task(scheduler.run(on_fire))
    try:
        _write_yaml(
            storage_dir / "hello.yaml",
            """
            name: hello
            enabled: true
            platform: discord
            channel_id: "123"
            prompt: first
            interval_seconds: 3600
            """,
        )
        await asyncio.wait_for(_wait_for_count(seen, 1), timeout=1.0)
        assert scheduler.jobs[0].prompt == "first"

        _write_yaml(
            storage_dir / "hello.yaml",
            """
            name: hello
            enabled: true
            platform: discord
            channel_id: "123"
            prompt: second
            interval_seconds: 3600
            """,
        )
        await asyncio.wait_for(_wait_for_count(seen, 2), timeout=1.0)
        assert scheduler.jobs[0].prompt == "second"

        (storage_dir / "hello.yaml").unlink()
        await asyncio.sleep(0.2)
        assert scheduler.jobs == []
        assert seen == ["first", "second"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_scheduler_reload_now_updates_visible_and_active_state(tmp_path):
    storage_dir = tmp_path / "automations"
    scheduler = Scheduler(
        storage_dir=storage_dir,
        reload_interval_seconds=60,
    )

    assert scheduler.list_automations() == []

    _write_yaml(
        storage_dir / "hello.yaml",
        """
        name: hello
        enabled: true
        platform: discord
        channel_id: "123"
        prompt: first
        interval_seconds: 3600
        """,
    )
    summary = await scheduler.reload_now()
    assert summary == {
        "visible": 1,
        "active": 1,
        "added": 1,
        "updated": 0,
        "removed": 0,
    }
    assert scheduler.get_automation("hello") is not None
    assert scheduler.jobs[0].prompt == "first"

    _write_yaml(
        storage_dir / "hello.yaml",
        """
        name: hello
        enabled: false
        platform: discord
        channel_id: "123"
        prompt: second
        interval_seconds: 3600
        """,
    )
    summary = await scheduler.reload_now()
    assert summary == {
        "visible": 1,
        "active": 0,
        "added": 0,
        "updated": 0,
        "removed": 1,
    }
    assert scheduler.get_automation("hello") is not None
    assert scheduler.get_automation("hello").enabled is False
    assert scheduler.jobs == []

    (storage_dir / "hello.yaml").unlink()
    summary = await scheduler.reload_now()
    assert summary == {
        "visible": 0,
        "active": 0,
        "added": 0,
        "updated": 0,
        "removed": 0,
    }
    assert scheduler.list_automations() == []


@pytest.mark.asyncio
async def test_scheduler_set_enabled_rewrites_file_and_updates_snapshot(tmp_path):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "toggle.yaml",
        """
        name: toggle-me
        enabled: false
        platform: discord
        channel_id: "123"
        prompt: summarize
        interval_seconds: 60
        """,
    )
    scheduler = Scheduler(
        storage_dir=storage_dir,
        reload_interval_seconds=60,
    )

    updated = await scheduler.set_automation_enabled("toggle-me", enabled=True)
    assert updated.enabled is True
    assert scheduler.jobs[0].name == "toggle-me"
    assert "enabled: true" in (storage_dir / "toggle.yaml").read_text(encoding="utf-8")

    updated = await scheduler.set_automation_enabled("toggle-me", enabled=False)
    assert updated.enabled is False
    assert scheduler.jobs == []
    assert "enabled: false" in (storage_dir / "toggle.yaml").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_scheduler_set_enabled_rejects_duplicate_name(tmp_path):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "a.yaml",
        """
        name: dup
        enabled: true
        platform: discord
        channel_id: "123"
        prompt: first
        interval_seconds: 60
        """,
    )
    _write_yaml(
        storage_dir / "b.yaml",
        """
        name: dup
        enabled: false
        platform: discord
        channel_id: "123"
        prompt: second
        interval_seconds: 60
        """,
    )
    scheduler = Scheduler(
        storage_dir=storage_dir,
        reload_interval_seconds=60,
    )

    with pytest.raises(ValueError, match="name conflict"):
        await scheduler.set_automation_enabled("dup", enabled=True)


@pytest.mark.asyncio
async def test_scheduler_set_enabled_reconciles_running_jobs(tmp_path):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "toggle.yaml",
        """
        name: live-toggle
        enabled: false
        platform: discord
        channel_id: "123"
        prompt: summarize
        interval_seconds: 3600
        """,
    )
    scheduler = Scheduler(
        storage_dir=storage_dir,
        reload_interval_seconds=60,
    )
    fired: list[str] = []

    async def on_fire(job: ScheduledJob) -> None:
        fired.append(job.name)

    task = asyncio.create_task(scheduler.run(on_fire))
    try:
        await asyncio.sleep(0.05)
        assert "live-toggle" not in scheduler._job_state

        await scheduler.set_automation_enabled("live-toggle", enabled=True)
        await asyncio.sleep(0.05)
        assert "live-toggle" in scheduler._job_state

        await scheduler.set_automation_enabled("live-toggle", enabled=False)
        await asyncio.sleep(0.05)
        assert "live-toggle" not in scheduler._job_state
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _wait_for_count(items: list[str], expected: int) -> None:
    while len(items) < expected:
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_run_returns_promptly_after_stop_under_long_intervals(tmp_path):
    """scheduler.run() must return sub-second after stop() even when reload+due
    intervals are very long. Validates the cooperative-shutdown contract: the
    long-sleep loops respond to the stop event without relying on cancellation.
    """
    scheduler = Scheduler(
        storage_dir=tmp_path / "automations",
        # 1h reload interval — old code blocked here on asyncio.sleep until cancel.
        reload_interval_seconds=3600,
    )
    # 1h due-loop max tick — old code only woke for due fires or cancel.
    scheduler.due_loop_max_tick_seconds = 3600.0

    async def on_fire(job: ScheduledJob) -> None:
        pass

    task = asyncio.create_task(scheduler.run(on_fire))
    await asyncio.sleep(0.1)  # let _reload_loop and _due_loop enter their waits
    loop = asyncio.get_event_loop()
    start = loop.time()
    scheduler.stop()
    await asyncio.wait_for(task, timeout=2.0)
    elapsed = loop.time() - start
    assert elapsed < 1.0, f"scheduler.run() took {elapsed:.2f}s to return; expected <1s"
    assert task.exception() is None


# =====================================================================
# M2 PR2 — atomic YAML write + patch_automation
# =====================================================================


def test_safe_write_yaml_atomic_round_trip(tmp_path):
    import yaml as _yaml

    from oh_my_agent.automation.scheduler import _safe_write_yaml

    target = tmp_path / "sub" / "auto.yaml"  # parent doesn't exist yet
    _safe_write_yaml(target, {"name": "x", "enabled": True, "interval_seconds": 60})
    assert target.exists()
    loaded = _yaml.safe_load(target.read_text())
    assert loaded["name"] == "x"
    assert loaded["enabled"] is True
    # No leftover temp / lock noise that looks like an automation file
    siblings = list(target.parent.glob("*.yaml"))
    assert siblings == [target]


def test_safe_write_yaml_overwrites_existing(tmp_path):
    import yaml as _yaml

    from oh_my_agent.automation.scheduler import _safe_write_yaml

    target = tmp_path / "auto.yaml"
    _safe_write_yaml(target, {"v": 1})
    _safe_write_yaml(target, {"v": 2})
    assert _yaml.safe_load(target.read_text())["v"] == 2


@pytest.mark.asyncio
async def test_patch_automation_updates_enabled_and_interval(tmp_path):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "job.yaml",
        """
        name: job
        enabled: true
        platform: discord
        channel_id: "123"
        thread_id: "456"
        delivery: channel
        prompt: do thing
        agent: claude
        interval_seconds: 60
        author: scheduler
        """,
    )
    scheduler = build_scheduler_from_config(
        {"automations": {"enabled": True, "storage_dir": str(storage_dir)}},
        project_root=tmp_path,
    )
    assert scheduler is not None
    rec = await scheduler.patch_automation("job", {"enabled": False, "interval_seconds": 120})
    assert rec.enabled is False
    # Re-read from disk to confirm persistence
    import yaml as _yaml

    raw = _yaml.safe_load((storage_dir / "job.yaml").read_text())
    assert raw["enabled"] is False
    assert raw["interval_seconds"] == 120


@pytest.mark.asyncio
async def test_patch_automation_rejects_disallowed_keys(tmp_path):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "job2.yaml",
        """
        name: job2
        enabled: true
        platform: discord
        channel_id: "123"
        thread_id: "456"
        delivery: channel
        prompt: do thing
        agent: claude
        interval_seconds: 60
        author: scheduler
        """,
    )
    scheduler = build_scheduler_from_config(
        {"automations": {"enabled": True, "storage_dir": str(storage_dir)}},
        project_root=tmp_path,
    )
    assert scheduler is not None
    with pytest.raises(ValueError, match="disallowed keys"):
        await scheduler.patch_automation("job2", {"prompt": "malicious rewrite"})


@pytest.mark.asyncio
async def test_patch_automation_rejects_invalid_interval(tmp_path):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "job3.yaml",
        """
        name: job3
        enabled: true
        platform: discord
        channel_id: "123"
        thread_id: "456"
        delivery: channel
        prompt: do thing
        agent: claude
        interval_seconds: 60
        author: scheduler
        """,
    )
    scheduler = build_scheduler_from_config(
        {"automations": {"enabled": True, "storage_dir": str(storage_dir)}},
        project_root=tmp_path,
    )
    assert scheduler is not None
    with pytest.raises(ValueError, match="interval_seconds must be > 0"):
        await scheduler.patch_automation("job3", {"interval_seconds": 0})
    # File unchanged (rejected before write)
    import yaml as _yaml

    raw = _yaml.safe_load((storage_dir / "job3.yaml").read_text())
    assert raw["interval_seconds"] == 60


@pytest.mark.asyncio
async def test_patch_automation_rejects_cron_interval_coexist(tmp_path):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "job4.yaml",
        """
        name: job4
        enabled: true
        platform: discord
        channel_id: "123"
        thread_id: "456"
        delivery: channel
        prompt: do thing
        agent: claude
        interval_seconds: 60
        author: scheduler
        """,
    )
    scheduler = build_scheduler_from_config(
        {"automations": {"enabled": True, "storage_dir": str(storage_dir)}},
        project_root=tmp_path,
    )
    assert scheduler is not None
    # Adding cron while interval_seconds is still set → mutually exclusive
    with pytest.raises(ValueError, match="mutually exclusive"):
        await scheduler.patch_automation("job4", {"cron": "0 9 * * *"})


@pytest.mark.asyncio
async def test_patch_automation_rejects_infeasible_cron(tmp_path):
    storage_dir = tmp_path / "automations"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "job5.yaml",
        """
        name: job5
        enabled: true
        platform: discord
        channel_id: "123"
        thread_id: "456"
        delivery: channel
        prompt: do thing
        agent: claude
        cron: "0 9 * * *"
        author: scheduler
        """,
    )
    scheduler = build_scheduler_from_config(
        {"automations": {"enabled": True, "storage_dir": str(storage_dir)}},
        project_root=tmp_path,
    )
    assert scheduler is not None
    # Feasibility is enforced at validation time — rejected before any write.
    with pytest.raises(ValueError, match="never occur"):
        await scheduler.patch_automation("job5", {"cron": "0 0 31 2 *"})
    import yaml as _yaml

    raw = _yaml.safe_load((storage_dir / "job5.yaml").read_text())
    assert raw["cron"] == "0 9 * * *"
