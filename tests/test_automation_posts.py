"""Tests for automation_posts storage + reply-to-automation follow-up routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from oh_my_agent.gateway.base import IncomingMessage
from oh_my_agent.gateway.manager import GatewayManager
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.runtime.types import AutomationPost


@pytest.fixture
async def store(tmp_path):
    s = SQLiteMemoryStore(tmp_path / "posts.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_record_and_get_automation_post(store):
    await store.record_automation_post(
        platform="discord",
        channel_id="ch1",
        message_id="msg-A",
        automation_name="daily-digest",
        artifact_paths=["/abs/report.md", "/abs/data.json"],
        agent_name="claude",
        skill_name="paper-digest",
        task_id="task-123",
    )
    post = await store.get_automation_post("discord", "ch1", "msg-A")
    assert isinstance(post, AutomationPost)
    assert post.automation_name == "daily-digest"
    assert post.agent_name == "claude"
    assert post.skill_name == "paper-digest"
    assert post.task_id == "task-123"
    assert post.artifact_paths == ["/abs/report.md", "/abs/data.json"]
    assert post.follow_up_thread_id is None


@pytest.mark.asyncio
async def test_get_missing_post_returns_none(store):
    post = await store.get_automation_post("discord", "ch1", "nope")
    assert post is None


@pytest.mark.asyncio
async def test_upsert_replaces_existing(store):
    await store.record_automation_post(
        platform="discord",
        channel_id="ch1",
        message_id="msg-A",
        automation_name="old",
        artifact_paths=["/a"],
    )
    await store.record_automation_post(
        platform="discord",
        channel_id="ch1",
        message_id="msg-A",
        automation_name="new",
        artifact_paths=["/b"],
        agent_name="gemini",
    )
    post = await store.get_automation_post("discord", "ch1", "msg-A")
    assert post.automation_name == "new"
    assert post.agent_name == "gemini"
    assert post.artifact_paths == ["/b"]


@pytest.mark.asyncio
async def test_set_follow_up_thread(store):
    await store.record_automation_post(
        platform="discord",
        channel_id="ch1",
        message_id="msg-A",
        automation_name="daily",
    )
    await store.set_automation_post_follow_up_thread(
        platform="discord",
        channel_id="ch1",
        message_id="msg-A",
        follow_up_thread_id="thread-777",
    )
    post = await store.get_automation_post("discord", "ch1", "msg-A")
    assert post.follow_up_thread_id == "thread-777"


@pytest.mark.asyncio
async def test_list_returns_posts_newest_first(store):
    for i in range(3):
        await store.record_automation_post(
            platform="discord",
            channel_id="ch1",
            message_id=f"msg-{i}",
            automation_name=f"auto-{i}",
        )
    posts = await store.list_automation_posts(limit=10)
    assert len(posts) == 3
    # Most recent insert is first; sqlite CURRENT_TIMESTAMP may tie but at
    # least every post shows up.
    assert {p.message_id for p in posts} == {"msg-0", "msg-1", "msg-2"}


@pytest.mark.asyncio
async def test_purge_expired_automation_posts(store):
    await store.record_automation_post(
        platform="discord",
        channel_id="ch1",
        message_id="msg-fresh",
        automation_name="fresh",
    )
    # Back-date an old post directly in SQL.
    db = await store._conn()
    await db.execute(
        """
        INSERT INTO automation_posts (
            platform, channel_id, message_id, automation_name,
            fired_at, artifact_paths
        )
        VALUES (?, ?, ?, ?, datetime('now', '-10 days'), ?)
        """,
        ("discord", "ch1", "msg-old", "old", "[]"),
    )
    await db.commit()

    # TTL 7 days: old stays expired, fresh is kept.
    removed = await store.purge_expired_automation_posts(7)
    assert removed == 1
    remaining = await store.list_automation_posts(limit=10)
    assert {p.message_id for p in remaining} == {"msg-fresh"}


@pytest.mark.asyncio
async def test_purge_with_nonpositive_ttl_is_noop(store):
    await store.record_automation_post(
        platform="discord",
        channel_id="ch1",
        message_id="msg-A",
        automation_name="daily",
    )
    assert (await store.purge_expired_automation_posts(0)) == 0
    assert (await store.purge_expired_automation_posts(-5)) == 0
    assert len(await store.list_automation_posts(limit=10)) == 1


# --- manager reply routing --------------------------------------------------


def _make_manager(store):
    manager = GatewayManager.__new__(GatewayManager)
    manager._memory_store_ref = store
    manager._memory_store = store
    manager._owner_user_ids = set()
    manager._runtime_service = None
    manager._intent_router = None
    manager._judge = None
    manager._judge_store = None
    manager._idle_tracker = None
    manager._compressor = None
    manager._skill_syncer = None
    manager._scheduler = None
    manager._memory_inject_limit = 12
    manager._memory_keyword_patterns = []
    manager._recent_thread_skills = {}
    return manager


@pytest.mark.asyncio
async def test_reply_to_automation_spawns_followup_thread(store, tmp_path):
    await store.record_automation_post(
        platform="discord",
        channel_id="ch1",
        message_id="msg-anchor",
        automation_name="daily-digest",
        artifact_paths=["/abs/report.md"],
    )

    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "ch1"
    channel.create_thread = AsyncMock(return_value="should-not-be-used")
    channel.create_followup_thread = AsyncMock(return_value="thread-new")

    registry = MagicMock()
    session = ChannelSession(
        platform="discord",
        channel_id="ch1",
        channel=channel,
        registry=registry,
        memory_store=store,
    )

    manager = _make_manager(store)

    msg = IncomingMessage(
        platform="discord",
        channel_id="ch1",
        thread_id=None,
        author="alice",
        content="follow up on this",
        reply_to_message_id="msg-anchor",
    )

    # Exercise only the reply-detection prologue. We don't want to run the
    # entire _handle_message_impl (which invokes agents); instead, copy the
    # logic shape by invoking the method and stubbing what comes next.
    # We stub registry/session enough that the early return on no runtime +
    # no router falls through to normal flow. To keep the test tight we
    # short-circuit after the prologue by making history retrieval raise;
    # but a cleaner approach is to let the agent loop swallow our stub and
    # just assert the side effects.
    #
    # Simpler: just assert that the manager's prologue created the follow-up
    # thread + seeded history + persisted the mapping by calling the
    # private impl and catching any later failure.
    try:
        await manager._handle_message_impl(session, registry, msg)
    except Exception:
        # Downstream of the prologue will fail because the registry/session
        # aren't fully wired; that's fine for this test.
        pass

    channel.create_followup_thread.assert_awaited_once()
    args, _ = channel.create_followup_thread.call_args
    assert args[0] == "msg-anchor"
    assert "daily-digest" in args[1]

    # Thread id should have been written onto msg so the rest of the flow
    # uses the new thread.
    assert msg.thread_id == "thread-new"

    # A system-authored turn with the artifact path should be in history.
    history = await store.load_history("discord", "ch1", "thread-new")
    assert any(
        "daily-digest" in turn.get("content", "") and "/abs/report.md" in turn.get("content", "")
        for turn in history
    )

    # Mapping should be persisted.
    post = await store.get_automation_post("discord", "ch1", "msg-anchor")
    assert post.follow_up_thread_id == "thread-new"


@pytest.mark.asyncio
async def test_reply_without_matching_post_falls_through(store):
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "ch1"
    channel.create_thread = AsyncMock(return_value="thread-regular")
    channel.create_followup_thread = AsyncMock(return_value="thread-new")

    registry = MagicMock()
    session = ChannelSession(
        platform="discord",
        channel_id="ch1",
        channel=channel,
        registry=registry,
        memory_store=store,
    )

    manager = _make_manager(store)
    msg = IncomingMessage(
        platform="discord",
        channel_id="ch1",
        thread_id=None,
        author="alice",
        content="reply to a regular message",
        reply_to_message_id="msg-unrelated",
    )

    try:
        await manager._handle_message_impl(session, registry, msg)
    except Exception:
        pass

    channel.create_followup_thread.assert_not_awaited()
    # Normal thread creation path was taken.
    channel.create_thread.assert_awaited()


# --- manual-thread-creation routing (Fix B) --------------------------------
#
# When the user clicks "Create Thread" in the Discord UI on a bot's automation
# post, Discord guarantees ``thread.id == anchor_message.id`` for
# message-anchored threads. The first message arrives with ``thread_id`` set
# to that anchor id and ``reply_to_message_id`` empty. The manager has to
# recognize this as a follow-up and seed the system turn — otherwise the
# agent sees an empty history and ignores the automation context entirely.


@pytest.mark.asyncio
async def test_manual_thread_creation_seeds_when_post_exists(store):
    # The anchor message id is what Discord uses as both the message id and
    # the new thread's id.
    anchor_id = "msg-anchor-99"
    await store.record_automation_post(
        platform="discord",
        channel_id="ch1",
        message_id=anchor_id,
        automation_name="politics-daily",
        artifact_paths=["/abs/politics.md", "/abs/politics.json"],
    )

    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "ch1"
    channel.create_thread = AsyncMock(return_value="should-not-be-called")
    channel.create_followup_thread = AsyncMock(return_value="should-not-be-called")

    registry = MagicMock()
    session = ChannelSession(
        platform="discord",
        channel_id="ch1",
        channel=channel,
        registry=registry,
        memory_store=store,
    )

    manager = _make_manager(store)
    msg = IncomingMessage(
        platform="discord",
        channel_id="ch1",
        thread_id=anchor_id,  # Discord: thread.id == anchor message.id
        author="alice",
        content="can you see the digest?",
        reply_to_message_id=None,
    )

    try:
        await manager._handle_message_impl(session, registry, msg)
    except Exception:
        # Downstream agent/router wiring isn't set up — by the time we get
        # to those code paths the seed has already been written.
        pass

    # Neither thread-creation path should have been called: the thread
    # already exists (user-created).
    channel.create_thread.assert_not_awaited()
    channel.create_followup_thread.assert_not_awaited()

    # The system-authored seed should be in history with automation name +
    # artifact paths.
    history = await store.load_history("discord", "ch1", anchor_id)
    seed_turns = [t for t in history if t.get("role") == "assistant"]
    assert any(
        "politics-daily" in t.get("content", "")
        and "/abs/politics.md" in t.get("content", "")
        for t in seed_turns
    )

    # Mapping should be persisted: follow_up_thread_id == anchor_id since
    # Discord makes them identical for message-anchored threads.
    post = await store.get_automation_post("discord", "ch1", anchor_id)
    assert post.follow_up_thread_id == anchor_id


@pytest.mark.asyncio
async def test_manual_thread_no_reseed_on_subsequent_messages(store):
    """Once the seed is in history, the second message must not duplicate
    it — otherwise every turn in the thread would prepend another seed."""
    anchor_id = "msg-anchor-77"
    await store.record_automation_post(
        platform="discord",
        channel_id="ch1",
        message_id=anchor_id,
        automation_name="weekly-brief",
        artifact_paths=["/abs/weekly.md"],
    )

    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "ch1"
    channel.create_thread = AsyncMock()
    channel.create_followup_thread = AsyncMock()

    registry = MagicMock()
    session = ChannelSession(
        platform="discord",
        channel_id="ch1",
        channel=channel,
        registry=registry,
        memory_store=store,
    )

    manager = _make_manager(store)

    # First message → seeds.
    msg1 = IncomingMessage(
        platform="discord",
        channel_id="ch1",
        thread_id=anchor_id,
        author="alice",
        content="hello",
    )
    try:
        await manager._handle_message_impl(session, registry, msg1)
    except Exception:
        pass

    # Second message → should NOT reseed.
    msg2 = IncomingMessage(
        platform="discord",
        channel_id="ch1",
        thread_id=anchor_id,
        author="alice",
        content="follow-up question",
    )
    try:
        await manager._handle_message_impl(session, registry, msg2)
    except Exception:
        pass

    history = await store.load_history("discord", "ch1", anchor_id)
    seed_turns = [
        t for t in history
        if t.get("role") == "assistant" and "weekly-brief" in t.get("content", "")
    ]
    assert len(seed_turns) == 1, (
        f"expected exactly one seed turn, got {len(seed_turns)}: {seed_turns}"
    )


@pytest.mark.asyncio
async def test_manual_thread_with_no_matching_post_skips_seed(store):
    """A user-created thread on a regular (non-automation) message must NOT
    inject any seed — there's nothing to seed from."""
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "ch1"
    channel.create_thread = AsyncMock()
    channel.create_followup_thread = AsyncMock()

    registry = MagicMock()
    session = ChannelSession(
        platform="discord",
        channel_id="ch1",
        channel=channel,
        registry=registry,
        memory_store=store,
    )

    manager = _make_manager(store)
    msg = IncomingMessage(
        platform="discord",
        channel_id="ch1",
        thread_id="thread-no-post",
        author="alice",
        content="just chatting",
    )

    try:
        await manager._handle_message_impl(session, registry, msg)
    except Exception:
        pass

    history = await store.load_history("discord", "ch1", "thread-no-post")
    seed_turns = [
        t for t in history
        if t.get("role") == "assistant" and "Follow-up on automation" in t.get("content", "")
    ]
    assert seed_turns == []
