import asyncio
import textwrap
from datetime import datetime, timezone
from pathlib import Path

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
        agent: codex
        interval_seconds: 60
        initial_delay_seconds: 5
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
    assert job.agent == "codex"
    assert job.interval_seconds == 60
    assert job.initial_delay_seconds == 5
    assert job.cron is None


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


async def _wait_for_count(items: list[str], expected: int) -> None:
    while len(items) < expected:
        await asyncio.sleep(0.01)
