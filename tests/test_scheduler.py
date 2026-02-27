import asyncio

import pytest

from oh_my_agent.automation import ScheduledJob, Scheduler, build_scheduler_from_config


def test_build_scheduler_disabled_returns_none():
    assert build_scheduler_from_config({}) is None
    assert build_scheduler_from_config({"automations": {"enabled": False}}) is None


def test_build_scheduler_parses_jobs():
    config = {
        "automations": {
            "enabled": True,
            "jobs": [
                {
                    "name": "daily",
                    "platform": "discord",
                    "channel_id": "123",
                    "thread_id": "456",
                    "delivery": "channel",
                    "prompt": "summarize",
                    "agent": "codex",
                    "interval_seconds": 60,
                    "initial_delay_seconds": 5,
                }
            ],
        }
    }
    scheduler = build_scheduler_from_config(config)
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


def test_build_scheduler_invalid_interval_raises():
    config = {
        "automations": {
            "enabled": True,
            "jobs": [
                {
                    "name": "bad",
                    "platform": "discord",
                    "channel_id": "123",
                    "prompt": "x",
                    "interval_seconds": 0,
                }
            ],
        }
    }
    with pytest.raises(ValueError):
        build_scheduler_from_config(config)


def test_build_scheduler_skips_disabled_job():
    config = {
        "automations": {
            "enabled": True,
            "jobs": [
                {
                    "name": "off",
                    "enabled": False,
                    "platform": "discord",
                    "channel_id": "123",
                    "prompt": "x",
                    "interval_seconds": 60,
                }
            ],
        }
    }
    scheduler = build_scheduler_from_config(config)
    assert scheduler is not None
    assert scheduler.jobs == []


def test_build_scheduler_dm_uses_default_target_user_id():
    config = {
        "automations": {
            "enabled": True,
            "jobs": [
                {
                    "name": "dm-report",
                    "platform": "discord",
                    "channel_id": "123",
                    "delivery": "dm",
                    "prompt": "report",
                    "interval_seconds": 60,
                }
            ],
        }
    }
    scheduler = build_scheduler_from_config(config, default_target_user_id="42")
    assert scheduler is not None
    job = scheduler.jobs[0]
    assert job.delivery == "dm"
    assert job.target_user_id == "42"


def test_build_scheduler_dm_requires_target_when_no_default():
    config = {
        "automations": {
            "enabled": True,
            "jobs": [
                {
                    "name": "dm-report",
                    "platform": "discord",
                    "channel_id": "123",
                    "delivery": "dm",
                    "prompt": "report",
                    "interval_seconds": 60,
                }
            ],
        }
    }
    with pytest.raises(ValueError):
        build_scheduler_from_config(config)


def test_build_scheduler_invalid_delivery_raises():
    config = {
        "automations": {
            "enabled": True,
            "jobs": [
                {
                    "name": "bad-delivery",
                    "platform": "discord",
                    "channel_id": "123",
                    "delivery": "email",
                    "prompt": "x",
                    "interval_seconds": 60,
                }
            ],
        }
    }
    with pytest.raises(ValueError):
        build_scheduler_from_config(config)


@pytest.mark.asyncio
async def test_scheduler_fires_job():
    fired = asyncio.Event()
    calls: list[str] = []
    scheduler = Scheduler(
        [
            ScheduledJob(
                name="tick",
                platform="discord",
                channel_id="123",
                prompt="run",
                interval_seconds=60,
            )
        ]
    )

    async def on_fire(job: ScheduledJob) -> None:
        calls.append(job.name)
        fired.set()

    task = asyncio.create_task(scheduler.run(on_fire))
    await asyncio.wait_for(fired.wait(), timeout=1.0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert calls == ["tick"]
