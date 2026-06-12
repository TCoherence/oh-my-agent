"""Covers NotificationManager.emit/resolve with a real SQLite store and a fake channel."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.runtime.notifications import NotificationManager
from oh_my_agent.runtime.types import NotificationEvent


def _make_session(*, channel=None) -> ChannelSession:
    if channel is None:
        channel = MagicMock()
        channel.platform = "discord"
        channel.channel_id = "100"
        channel.send = AsyncMock(return_value="thread-msg-1")
        channel.send_dm = AsyncMock(return_value="dm-msg-1")
        channel.render_user_mention = lambda uid: f"<@{uid}>"
    return ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=MagicMock(),
    )


def _make_event(**over) -> NotificationEvent:
    defaults = dict(
        kind="task_draft",
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        title="Task awaiting approval",
        body="Please approve",
        dedupe_key="task:abc:draft",
        task_id="abc",
        payload={"reason_text": "manual approval"},
    )
    defaults.update(over)
    return NotificationEvent(**defaults)


@pytest.fixture
async def store(tmp_path):
    s = SQLiteMemoryStore(tmp_path / "notif.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_emit_is_noop_without_owners(store):
    session = _make_session()
    mgr = NotificationManager(
        store,
        owner_user_ids=None,
        session_lookup=lambda p, c: session,
    )
    records = await mgr.emit(_make_event())
    assert records == []


@pytest.mark.asyncio
async def test_emit_creates_records_and_sends_thread_and_dm(store):
    session = _make_session()
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: session,
    )
    records = await mgr.emit(_make_event())
    assert len(records) == 1
    rec = records[0]
    assert rec.status == "active"
    assert rec.owner_user_id == "owner-1"
    assert rec.thread_message_id == "thread-msg-1"
    assert rec.dm_message_id == "dm-msg-1"
    session.channel.send.assert_awaited_once()
    session.channel.send_dm.assert_awaited_once()


@pytest.mark.asyncio
async def test_emit_dedupes_active_event(store):
    session = _make_session()
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: session,
    )
    first = await mgr.emit(_make_event())
    assert len(first) == 1

    # Reset mocks so we can detect if channel calls happen again.
    session.channel.send.reset_mock()
    session.channel.send_dm.reset_mock()

    second = await mgr.emit(_make_event())
    # Returns the existing active records, does not re-send.
    assert len(second) == 1
    assert second[0].id == first[0].id
    session.channel.send.assert_not_awaited()
    session.channel.send_dm.assert_not_awaited()


@pytest.mark.asyncio
async def test_emit_skipped_when_session_missing(store):
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: None,
    )
    records = await mgr.emit(_make_event())
    assert records == []


@pytest.mark.asyncio
async def test_emit_records_failed_when_both_deliveries_fail(store):
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.send = AsyncMock(side_effect=RuntimeError("network down"))
    channel.send_dm = AsyncMock(side_effect=RuntimeError("dm rejected"))
    channel.render_user_mention = lambda uid: f"<@{uid}>"
    session = _make_session(channel=channel)
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: session,
    )
    records = await mgr.emit(_make_event())
    assert len(records) == 1
    assert records[0].status == "failed"
    assert records[0].thread_message_id is None
    assert records[0].dm_message_id is None


@pytest.mark.asyncio
async def test_emit_handles_channel_without_dm_support(store):
    channel = MagicMock(spec=["platform", "channel_id", "send", "render_user_mention"])
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.send = AsyncMock(return_value="thread-msg-1")
    channel.render_user_mention = lambda uid: f"<@{uid}>"
    session = _make_session(channel=channel)
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: session,
    )
    records = await mgr.emit(_make_event())
    assert len(records) == 1
    # No DM capability, but thread delivery worked → active.
    assert records[0].status == "active"
    assert records[0].thread_message_id == "thread-msg-1"
    assert records[0].dm_message_id is None


@pytest.mark.asyncio
async def test_resolve_updates_store(store):
    session = _make_session()
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: session,
    )
    await mgr.emit(_make_event())

    resolved = await mgr.resolve("task:abc:draft")
    assert resolved == 1

    # A subsequent emit should go through again (previous resolved, not active).
    session.channel.send.reset_mock()
    records = await mgr.emit(_make_event())
    assert len(records) == 1
    session.channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_emit_includes_task_id_and_body_in_thread_message(store):
    session = _make_session()
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: session,
    )
    await mgr.emit(_make_event())
    sent_text = session.channel.send.call_args[0][1]
    assert "Task: `abc`" in sent_text
    assert "Please approve" in sent_text
    assert "manual approval" in sent_text
    assert "<@owner-1>" in sent_text


@pytest.mark.asyncio
async def test_concurrent_emits_same_dedupe_key_deliver_once(store):
    """TOCTOU regression: the dedupe read and the record insert are separated
    by channel awaits; without per-key serialization two concurrent emits
    both pass the read and deliver everything twice."""
    send_started = asyncio.Event()
    release = asyncio.Event()

    async def slow_send(thread_id, text):
        send_started.set()
        await release.wait()
        return "thread-msg-1"

    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.send = AsyncMock(side_effect=slow_send)
    channel.send_dm = AsyncMock(return_value="dm-msg-1")
    channel.render_user_mention = lambda uid: f"<@{uid}>"
    session = _make_session(channel=channel)
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: session,
    )

    first = asyncio.create_task(mgr.emit(_make_event()))
    await send_started.wait()  # first emit is mid-delivery, record not yet inserted
    second = asyncio.create_task(mgr.emit(_make_event()))
    await asyncio.sleep(0)  # let the second emit reach the keyed lock
    release.set()

    r1, r2 = await asyncio.gather(first, second)
    assert len(r1) == 1
    assert len(r2) == 1
    assert r2[0].id == r1[0].id  # second emit deduped against the first
    assert channel.send.await_count == 1
    assert channel.send_dm.await_count == 1
    # Lock entries are evicted once uncontended.
    assert mgr._emit_locks == {}  # noqa: SLF001


@pytest.mark.asyncio
async def test_emit_without_dedupe_key_skips_lock(store):
    session = _make_session()
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: session,
    )
    records = await mgr.emit(_make_event(dedupe_key=""))
    assert len(records) == 1
    assert mgr._emit_locks == {}  # noqa: SLF001


@pytest.mark.asyncio
async def test_reason_label_fallbacks():
    assert NotificationManager._reason_label("task_draft") == "draft"
    assert NotificationManager._reason_label("task_waiting_merge") == "waiting_merge"
    assert NotificationManager._reason_label("unknown_kind") == "unknown_kind"
    assert (
        NotificationManager._reason_label("task_draft", {"reason_text": "custom"})
        == "custom"
    )
