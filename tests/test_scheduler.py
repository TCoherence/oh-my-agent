import asyncio
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from oh_my_agent.automation import ScheduledJob, Scheduler, build_scheduler_from_config
from oh_my_agent.automation.scheduler import _next_cron_fire, _parse_cron_expression


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
        # Under the central due-loop model, "is the job active?" lives in
        # _job_state (not _fire_tasks, which only holds in-flight fires).
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
