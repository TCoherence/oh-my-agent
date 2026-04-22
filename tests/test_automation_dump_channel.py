"""Tests for the automation dump-channel opt-in routing feature.

Covers:
- ``automations.dump_channels`` config parsing (shape + platform/channel_id).
- Per-automation ``target_channel`` → resolved ``notify_channel_id``.
- ``_send_automation_terminal_message`` redirects the terminal message and
  the ``automation_posts`` row to the dump channel, while the source channel
  session continues to receive DRAFT / approval / progress traffic.
- ``register_session_alias`` makes ``_session_for_notify`` resolve the dump
  channel id back to the source BaseChannel (single bot/gateway model).
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from oh_my_agent.automation import (
    AutomationRecord,
    DumpChannelConfig,
    Scheduler,
    build_scheduler_from_config,
)
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.runtime.service import RuntimeService
from oh_my_agent.runtime.types import RuntimeTask


def _write_yaml(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


# ── config parsing ───────────────────────────────────────────────────── #


def test_build_scheduler_parses_dump_channels(tmp_path):
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "enabled": True,
                "storage_dir": str(tmp_path / "auto"),
                "reload_interval_seconds": 5,
                "dump_channels": {
                    "oma_dump": {
                        "platform": "discord",
                        "channel_id": "999",
                    }
                },
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    resolved = scheduler._dump_channels
    assert "oma_dump" in resolved
    assert resolved["oma_dump"] == DumpChannelConfig(platform="discord", channel_id="999")


def test_build_scheduler_rejects_dump_channel_missing_platform(tmp_path):
    with pytest.raises(ValueError, match="platform is required"):
        build_scheduler_from_config(
            {
                "automations": {
                    "storage_dir": str(tmp_path / "auto"),
                    "dump_channels": {"oma_dump": {"channel_id": "999"}},
                }
            },
            project_root=tmp_path,
        )


def test_build_scheduler_rejects_dump_channel_missing_channel_id(tmp_path):
    with pytest.raises(ValueError, match="channel_id is required"):
        build_scheduler_from_config(
            {
                "automations": {
                    "storage_dir": str(tmp_path / "auto"),
                    "dump_channels": {"oma_dump": {"platform": "discord"}},
                }
            },
            project_root=tmp_path,
        )


def test_build_scheduler_rejects_dump_channels_not_a_mapping(tmp_path):
    with pytest.raises(ValueError, match="must be a mapping"):
        build_scheduler_from_config(
            {
                "automations": {
                    "storage_dir": str(tmp_path / "auto"),
                    "dump_channels": ["not", "a", "mapping"],
                }
            },
            project_root=tmp_path,
        )


# ── per-automation target_channel ────────────────────────────────────── #


def test_target_channel_resolves_to_notify_channel_id(tmp_path):
    storage_dir = tmp_path / "auto"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "nightly.yaml",
        """
        name: nightly
        enabled: true
        platform: discord
        channel_id: "source-123"
        prompt: summarize
        interval_seconds: 60
        target_channel: oma_dump
        """,
    )
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "enabled": True,
                "storage_dir": str(storage_dir),
                "dump_channels": {
                    "oma_dump": {"platform": "discord", "channel_id": "dump-999"}
                },
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    jobs = scheduler.jobs
    assert len(jobs) == 1
    job = jobs[0]
    assert job.channel_id == "source-123"
    assert job.notify_channel_id == "dump-999"


def test_target_channel_unknown_rejected(tmp_path, caplog):
    storage_dir = tmp_path / "auto"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "bad.yaml",
        """
        name: bad
        platform: discord
        channel_id: "source-123"
        prompt: x
        interval_seconds: 60
        target_channel: missing_one
        """,
    )
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "storage_dir": str(storage_dir),
                "dump_channels": {
                    "oma_dump": {"platform": "discord", "channel_id": "dump-999"},
                },
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    assert scheduler.jobs == []
    assert "not configured under automations.dump_channels" in caplog.text


def test_target_channel_platform_mismatch_rejected(tmp_path, caplog):
    storage_dir = tmp_path / "auto"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "bad.yaml",
        """
        name: bad
        platform: discord
        channel_id: "source-123"
        prompt: x
        interval_seconds: 60
        target_channel: slack_dump
        """,
    )
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "storage_dir": str(storage_dir),
                "dump_channels": {
                    "slack_dump": {"platform": "slack", "channel_id": "C-1"},
                },
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    assert scheduler.jobs == []
    assert "does not match automation platform" in caplog.text


def test_missing_target_channel_yields_no_notify_channel_id(tmp_path):
    storage_dir = tmp_path / "auto"
    storage_dir.mkdir()
    _write_yaml(
        storage_dir / "plain.yaml",
        """
        name: plain
        platform: discord
        channel_id: "source-123"
        prompt: x
        interval_seconds: 60
        """,
    )
    scheduler = build_scheduler_from_config(
        {
            "automations": {
                "storage_dir": str(storage_dir),
                "dump_channels": {
                    "oma_dump": {"platform": "discord", "channel_id": "dump-999"},
                },
            }
        },
        project_root=tmp_path,
    )
    assert scheduler is not None
    job = scheduler.jobs[0]
    assert job.notify_channel_id is None


# ── runtime redirect ─────────────────────────────────────────────────── #


@pytest.fixture
async def store(tmp_path):
    s = SQLiteMemoryStore(tmp_path / "rt.db")
    await s.init()
    yield s
    await s.close()


def _make_channel(*, channel_id: str):
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = channel_id
    channel.send = AsyncMock(return_value="msg-100")
    channel.upsert_status_message = AsyncMock(return_value="msg-upsert")
    return channel


def _make_session(channel_id: str) -> tuple[ChannelSession, MagicMock]:
    channel = _make_channel(channel_id=channel_id)
    session = ChannelSession(
        platform="discord",
        channel_id=channel_id,
        channel=channel,
        registry=MagicMock(agents=[]),
    )
    return session, channel


@pytest.mark.asyncio
async def test_terminal_message_redirects_to_notify_channel(store, tmp_path):
    source_session, source_channel = _make_session("source-123")
    source_session.memory_store = store

    service = RuntimeService(store, config={"enabled": True, "worktree_root": str(tmp_path / "wt")})
    service.register_session(source_session, source_session.registry)
    # Same BaseChannel, aliased under the dump channel id.
    service.register_session_alias(
        session=source_session,
        registry=source_session.registry,
        platform="discord",
        channel_id="dump-999",
    )

    task = await store.create_runtime_task(
        task_id="t-abc",
        platform="discord",
        channel_id="source-123",
        thread_id="thread-1",
        created_by="tester",
        goal="do the thing",
        status="PENDING",
        max_steps=1,
        max_minutes=5,
        test_command="true",
        completion_mode="reply",
        automation_name="nightly",
        task_type="artifact",
        notify_channel_id="dump-999",
    )
    assert isinstance(task, RuntimeTask)
    assert task.notify_channel_id == "dump-999"

    await service._send_automation_terminal_message(
        task,
        "hello from automation",
        artifact_paths=["/abs/report.md"],
    )

    # Both the attribution chunk and remainder go through the shared channel
    # adapter, but always targeted at the dump channel id (no thread routing
    # since the dump channel is a top-level channel, not a thread).
    assert source_channel.send.await_count >= 1
    first_call = source_channel.send.await_args_list[0]
    assert first_call.args[0] == "dump-999"
    assert "automation `nightly`" in first_call.args[1]

    # automation_posts row is recorded against dump-999.
    post = await store.get_automation_post("discord", "dump-999", "msg-100")
    assert post is not None
    assert post.automation_name == "nightly"
    assert post.artifact_paths == ["/abs/report.md"]
    # Confirm the source channel is NOT carrying the post.
    assert await store.get_automation_post("discord", "source-123", "msg-100") is None


@pytest.mark.asyncio
async def test_terminal_message_falls_back_to_source_when_no_notify_channel(store, tmp_path):
    session, channel = _make_session("source-123")
    session.memory_store = store

    service = RuntimeService(store, config={"enabled": True, "worktree_root": str(tmp_path / "wt")})
    service.register_session(session, session.registry)

    task = await store.create_runtime_task(
        task_id="t-plain",
        platform="discord",
        channel_id="source-123",
        thread_id="thread-42",
        created_by="tester",
        goal="plain",
        status="PENDING",
        max_steps=1,
        max_minutes=5,
        test_command="true",
        completion_mode="reply",
        automation_name="nightly",
        task_type="artifact",
    )
    assert task.notify_channel_id is None

    await service._send_automation_terminal_message(task, "plain terminal")
    first_call = channel.send.await_args_list[0]
    assert first_call.args[0] == "thread-42"
    post = await store.get_automation_post("discord", "source-123", "msg-100")
    assert post is not None
