"""Covers that `_dispatch_scheduled_job` always refreshes `next_run_at`,
including on early-return paths (no session / DM missing target / DM unsupported)
and on both normal completion and exception paths.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.automation.scheduler import Scheduler, ScheduledJob
from oh_my_agent.gateway.manager import GatewayManager
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.memory.store import SQLiteMemoryStore


def _write_yaml(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def _build_scheduler(tmp_path: Path) -> Scheduler:
    storage = tmp_path / "automations"
    storage.mkdir(exist_ok=True)
    _write_yaml(
        storage / "daily.yaml",
        """
        name: daily
        enabled: true
        platform: discord
        channel_id: "100"
        thread_id: "200"
        prompt: summarize
        cron: "0 8 * * *"
        """,
    )
    return Scheduler(storage_dir=storage, reload_interval_seconds=5.0)


def _make_channel(channel_id: str = "100", *, dm_support: bool = True):
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = channel_id
    channel.send = AsyncMock()
    channel.create_thread = AsyncMock(return_value="thr")
    channel.typing = MagicMock()
    channel.typing.return_value.__aenter__ = AsyncMock(return_value=None)
    channel.typing.return_value.__aexit__ = AsyncMock(return_value=False)
    if dm_support:
        channel.ensure_dm_channel = AsyncMock(return_value="dm-thread")
    else:
        # No ensure_dm_channel attribute at all.
        if hasattr(channel, "ensure_dm_channel"):
            del channel.ensure_dm_channel
    return channel


def _make_channel_job(**over) -> ScheduledJob:
    defaults = dict(
        name="daily",
        platform="discord",
        channel_id="100",
        prompt="summarize",
        delivery="channel",
        thread_id="200",
        cron="0 8 * * *",
    )
    defaults.update(over)
    return ScheduledJob(**defaults)


def _make_dm_job(**over) -> ScheduledJob:
    defaults = dict(
        name="daily",
        platform="discord",
        channel_id="100",
        prompt="summarize",
        delivery="dm",
        cron="0 8 * * *",
    )
    defaults.update(over)
    return ScheduledJob(**defaults)


async def _seed_initial_state(store: SQLiteMemoryStore, name: str) -> None:
    await store.upsert_automation_state(
        name,
        platform="discord",
        channel_id="100",
        enabled=True,
        next_run_at="1999-01-01T00:00:00",  # deliberately stale
    )


async def _fresh_store(tmp_path: Path) -> SQLiteMemoryStore:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.init()
    return store


def _make_manager(
    *, scheduler: Scheduler, store: SQLiteMemoryStore, sessions: dict
) -> GatewayManager:
    gm = GatewayManager([], scheduler=scheduler)
    gm.set_memory_store(store)
    gm._sessions = sessions  # type: ignore[attr-defined]
    return gm


@pytest.mark.asyncio
async def test_refresh_next_run_at_on_no_session(tmp_path):
    scheduler = _build_scheduler(tmp_path)
    store = await _fresh_store(tmp_path)
    await _seed_initial_state(store, "daily")

    gm = _make_manager(scheduler=scheduler, store=store, sessions={})

    await gm._dispatch_scheduled_job(_make_channel_job())

    state = await store.get_automation_state("daily")
    assert state is not None
    # next_run_at should no longer be the stale 1999 sentinel.
    assert state.next_run_at is not None
    assert not state.next_run_at.startswith("1999")
    # Early-return's own last_error still recorded.
    assert state.last_error is not None
    assert "no active channel" in state.last_error
    await store.close()


@pytest.mark.asyncio
async def test_refresh_next_run_at_on_dm_missing_target(tmp_path):
    scheduler = _build_scheduler(tmp_path)
    store = await _fresh_store(tmp_path)
    await _seed_initial_state(store, "daily")

    channel = _make_channel()
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=MagicMock(spec=AgentRegistry),
    )
    gm = _make_manager(
        scheduler=scheduler, store=store, sessions={"discord:100": session}
    )

    await gm._dispatch_scheduled_job(_make_dm_job(target_user_id=None))

    state = await store.get_automation_state("daily")
    assert state is not None
    assert state.next_run_at is not None
    assert not state.next_run_at.startswith("1999")
    assert state.last_error is not None
    assert "target_user_id" in state.last_error
    await store.close()


@pytest.mark.asyncio
async def test_refresh_next_run_at_on_dm_unsupported_channel(tmp_path):
    scheduler = _build_scheduler(tmp_path)
    store = await _fresh_store(tmp_path)
    await _seed_initial_state(store, "daily")

    channel = _make_channel(dm_support=False)
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=MagicMock(spec=AgentRegistry),
    )
    gm = _make_manager(
        scheduler=scheduler, store=store, sessions={"discord:100": session}
    )

    await gm._dispatch_scheduled_job(_make_dm_job(target_user_id="user-1"))

    state = await store.get_automation_state("daily")
    assert state is not None
    assert state.next_run_at is not None
    assert not state.next_run_at.startswith("1999")
    assert state.last_error is not None
    assert "does not support DM delivery" in state.last_error
    await store.close()


@pytest.mark.asyncio
async def test_refresh_next_run_at_on_normal_completion(tmp_path):
    scheduler = _build_scheduler(tmp_path)
    store = await _fresh_store(tmp_path)
    await _seed_initial_state(store, "daily")

    channel = _make_channel()
    registry = MagicMock(spec=AgentRegistry)
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    gm = _make_manager(
        scheduler=scheduler, store=store, sessions={"discord:100": session}
    )
    gm.handle_message = AsyncMock(return_value=None)  # type: ignore[assignment]

    await gm._dispatch_scheduled_job(_make_channel_job())

    state = await store.get_automation_state("daily")
    assert state is not None
    assert state.next_run_at is not None
    assert not state.next_run_at.startswith("1999")
    assert state.last_run_at is not None
    gm.handle_message.assert_awaited_once()
    await store.close()


@pytest.mark.asyncio
async def test_refresh_next_run_at_on_fire_failure(tmp_path):
    scheduler = _build_scheduler(tmp_path)
    store = await _fresh_store(tmp_path)
    await _seed_initial_state(store, "daily")

    channel = _make_channel()
    registry = MagicMock(spec=AgentRegistry)
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    gm = _make_manager(
        scheduler=scheduler, store=store, sessions={"discord:100": session}
    )
    gm.handle_message = AsyncMock(side_effect=RuntimeError("agent boom"))  # type: ignore[assignment]

    await gm._dispatch_scheduled_job(_make_channel_job())

    state = await store.get_automation_state("daily")
    assert state is not None
    assert state.next_run_at is not None
    assert not state.next_run_at.startswith("1999")
    assert state.last_error is not None
    assert "agent boom" in state.last_error
    await store.close()


@pytest.mark.asyncio
async def test_refresh_helper_tolerates_exception_without_suppressing_main(tmp_path, caplog):
    """If the refresh helper itself fails, it logs a warning and does NOT hide
    the original dispatch-path exception. Here the exception is already
    swallowed by _dispatch_scheduled_job_body's try/except, so we verify
    that `refresh_next_run_at failed` is logged while the primary path
    still records `last_error`.
    """
    import logging
    scheduler = _build_scheduler(tmp_path)
    store = await _fresh_store(tmp_path)
    await _seed_initial_state(store, "daily")

    channel = _make_channel()
    registry = MagicMock(spec=AgentRegistry)
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    gm = _make_manager(
        scheduler=scheduler, store=store, sessions={"discord:100": session}
    )
    gm.handle_message = AsyncMock(side_effect=RuntimeError("agent boom"))  # type: ignore[assignment]

    # Make the refresh helper itself explode.
    scheduler.compute_job_next_run_at = MagicMock(  # type: ignore[assignment]
        side_effect=RuntimeError("refresh kaboom")
    )

    with caplog.at_level(logging.WARNING, logger="oh_my_agent.gateway.manager"):
        await gm._dispatch_scheduled_job(_make_channel_job())

    assert any("refresh_next_run_at failed" in rec.message for rec in caplog.records)
    state = await store.get_automation_state("daily")
    assert state is not None
    assert state.last_error is not None
    assert "agent boom" in state.last_error
    await store.close()
