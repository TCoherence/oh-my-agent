"""M1 PR2 — FeedbackCollector unit tests.

Verifies emoji classification, automation_posts lookup, self_eval entry
writes, and the 24h no-reply scan worker. Uses real JudgeStore +
SQLiteMemoryStore so the model_validator (M0 PR1) enforces invariants.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from oh_my_agent.memory.feedback import (
    FeedbackCollector,
    FeedbackScanWorker,
    _parse_ts,
)
from oh_my_agent.memory.judge_store import JudgeStore
from oh_my_agent.memory.store import SQLiteMemoryStore


@pytest.fixture
async def memory_store(tmp_path: Path):
    store = SQLiteMemoryStore(tmp_path / "runtime.db")
    await store.init()
    yield store
    await store.close()


@pytest.fixture
async def judge_store(tmp_path: Path):
    store = JudgeStore(memory_dir=tmp_path / "memory")
    await store.load()
    return store


@pytest.fixture
async def collector(memory_store, judge_store):
    return FeedbackCollector(
        memory_store=memory_store, judge_store=judge_store, no_reply_window_hours=24
    )


def test_classify_emoji_positive_negative_unknown():
    assert FeedbackCollector.classify_emoji("👍") == "positive"
    assert FeedbackCollector.classify_emoji("✅") == "positive"
    assert FeedbackCollector.classify_emoji("👎") == "negative"
    assert FeedbackCollector.classify_emoji("❌") == "negative"
    assert FeedbackCollector.classify_emoji("⚠️") == "negative"
    # Random emoji → unknown (not signal)
    assert FeedbackCollector.classify_emoji("🚀") == "unknown"
    assert FeedbackCollector.classify_emoji("") == "unknown"


@pytest.mark.asyncio
async def test_record_reaction_unknown_emoji_is_skip(collector):
    ok = await collector.record_reaction(
        message_id="msg-1", emoji="🚀", action="add", actor_id="owner-1"
    )
    assert not ok


@pytest.mark.asyncio
async def test_record_reaction_no_matching_automation_post_is_skip(collector):
    """When message_id isn't an automation post, record_reaction is a no-op."""
    ok = await collector.record_reaction(
        message_id="orphan-msg", emoji="👍", action="add", actor_id="owner-1"
    )
    assert not ok


@pytest.mark.asyncio
async def test_record_reaction_positive_writes_self_eval(collector, memory_store, judge_store):
    # Seed an automation post
    await memory_store.record_automation_post(
        platform="discord",
        channel_id="ch-1",
        message_id="msg-foo",
        automation_name="auto-foo",
        task_id="task-abc",
    )
    ok = await collector.record_reaction(
        message_id="msg-foo", emoji="👍", action="add", actor_id="owner-1"
    )
    assert ok
    active = judge_store.get_active()
    assert len(active) == 1
    entry = active[0]
    assert entry.category == "self_eval"
    assert entry.scope == "automation"
    assert entry.source_automation == "auto-foo"
    assert entry.feedback_source == "implicit"
    assert entry.quality == "pass"
    assert "task-abc" in entry.summary


@pytest.mark.asyncio
async def test_record_reaction_negative_writes_fail(collector, memory_store, judge_store):
    await memory_store.record_automation_post(
        platform="discord",
        channel_id="ch-1",
        message_id="msg-bar",
        automation_name="auto-bar",
        task_id="task-xyz",
    )
    ok = await collector.record_reaction(
        message_id="msg-bar", emoji="❌", action="add", actor_id="owner-1"
    )
    assert ok
    entry = judge_store.get_active()[0]
    assert entry.quality == "fail"


@pytest.mark.asyncio
async def test_record_reaction_remove_flips_polarity(collector, memory_store, judge_store):
    """Removing 👍 = revoked endorsement = mild negative."""
    await memory_store.record_automation_post(
        platform="discord",
        channel_id="ch-1",
        message_id="msg-rev",
        automation_name="auto-rev",
        task_id="task-r",
    )
    ok = await collector.record_reaction(
        message_id="msg-rev", emoji="👍", action="remove", actor_id="owner-1"
    )
    assert ok
    entry = judge_store.get_active()[0]
    assert entry.quality == "fail"  # remove 👍 → fail (revoked endorsement)


@pytest.mark.asyncio
async def test_scan_no_reply_writes_weak_negative(memory_store, judge_store):
    """Posts older than the window get weak-negative entries."""
    # Pre-record an old automation post (manually pre-date fired_at)
    await memory_store.record_automation_post(
        platform="discord",
        channel_id="ch-1",
        message_id="msg-old",
        automation_name="auto-old",
        task_id="task-old",
    )
    # Backdate fired_at to 48h ago
    db = await memory_store._conn()
    await db.execute(
        "UPDATE automation_posts SET fired_at = datetime('now', '-48 hours') WHERE message_id=?",
        ("msg-old",),
    )
    await db.commit()
    # 1h window so the 48h-old post qualifies
    collector = FeedbackCollector(
        memory_store=memory_store, judge_store=judge_store, no_reply_window_hours=1
    )
    written = await collector.scan_no_reply_negative()
    assert written >= 1
    entries = judge_store.get_active()
    # At least one self_eval entry created
    self_eval_entries = [e for e in entries if e.category == "self_eval"]
    assert len(self_eval_entries) >= 1
    # All should be quality=fail with implicit source
    for entry in self_eval_entries:
        assert entry.quality == "fail"
        assert entry.feedback_source == "implicit"


@pytest.mark.asyncio
async def test_scan_no_reply_dedupes_existing(memory_store, judge_store):
    """Scan won't double-write if a self_eval already exists for the task."""
    await memory_store.record_automation_post(
        platform="discord",
        channel_id="ch-1",
        message_id="msg-dup",
        automation_name="auto-dup",
        task_id="task-dup",
    )
    db = await memory_store._conn()
    await db.execute(
        "UPDATE automation_posts SET fired_at = datetime('now', '-48 hours') WHERE message_id=?",
        ("msg-dup",),
    )
    await db.commit()
    collector = FeedbackCollector(
        memory_store=memory_store, judge_store=judge_store, no_reply_window_hours=1
    )
    # First scan writes one
    n1 = await collector.scan_no_reply_negative()
    # Second scan should be a no-op (already has self_eval for that task)
    n2 = await collector.scan_no_reply_negative()
    assert n1 >= 1
    assert n2 == 0


@pytest.mark.asyncio
async def test_scan_skips_engaged_posts(memory_store, judge_store):
    """Codex round-1 catch: posts with follow_up_thread_id are user-engaged
    and must NOT receive a no-reply weak-negative."""
    await memory_store.record_automation_post(
        platform="discord",
        channel_id="ch-1",
        message_id="msg-engaged",
        automation_name="auto-eng",
        task_id="task-eng",
    )
    # Mark engaged (user replied → follow-up thread exists)
    await memory_store.set_automation_post_follow_up_thread(
        platform="discord",
        channel_id="ch-1",
        message_id="msg-engaged",
        follow_up_thread_id="thread-1234",
    )
    db = await memory_store._conn()
    await db.execute(
        "UPDATE automation_posts SET fired_at = datetime('now', '-48 hours') WHERE message_id=?",
        ("msg-engaged",),
    )
    await db.commit()
    collector = FeedbackCollector(
        memory_store=memory_store, judge_store=judge_store, no_reply_window_hours=1
    )
    n = await collector.scan_no_reply_negative()
    assert n == 0  # engaged → skip
    assert judge_store.get_active() == []


@pytest.mark.asyncio
async def test_scan_skips_overage_posts(memory_store, judge_store):
    """Codex round-1 catch: startup must not backfill ancient posts."""
    await memory_store.record_automation_post(
        platform="discord",
        channel_id="ch-1",
        message_id="msg-ancient",
        automation_name="auto-anc",
        task_id="task-anc",
    )
    # 30 days old
    db = await memory_store._conn()
    await db.execute(
        "UPDATE automation_posts SET fired_at = datetime('now', '-30 days') WHERE message_id=?",
        ("msg-ancient",),
    )
    await db.commit()
    collector = FeedbackCollector(
        memory_store=memory_store,
        judge_store=judge_store,
        no_reply_window_hours=1,
        no_reply_max_age_hours=24 * 7,  # 7 days cap
    )
    n = await collector.scan_no_reply_negative()
    assert n == 0  # too old → skip
    assert judge_store.get_active() == []


@pytest.mark.asyncio
async def test_scan_skips_when_store_lacks_method(judge_store, tmp_path: Path):
    """Stores without list_automation_posts_older_than are silently skipped."""
    class _BareStore:
        # No list_automation_posts_older_than method
        pass

    collector = FeedbackCollector(
        memory_store=_BareStore(),
        judge_store=judge_store,
        no_reply_window_hours=24,
    )
    n = await collector.scan_no_reply_negative()
    assert n == 0


@pytest.mark.asyncio
async def test_feedback_scan_worker_runs_and_stops(memory_store, judge_store):
    collector = FeedbackCollector(
        memory_store=memory_store, judge_store=judge_store, no_reply_window_hours=24
    )
    worker = FeedbackScanWorker(collector, interval_seconds=1)
    worker.start()
    # Let it tick at least once
    await asyncio.sleep(0.1)
    await worker.stop()
    # No exceptions raised, no orphan task
    assert worker._task is not None
    assert worker._task.done()


def test_parse_ts_handles_iso_and_sql_formats():
    from datetime import datetime, timezone

    # Naive ISO → UTC
    dt = _parse_ts("2026-05-21 18:30:00")
    assert dt is not None
    assert dt.tzinfo == timezone.utc

    # ISO with offset
    dt2 = _parse_ts("2026-05-21T18:30:00+00:00")
    assert dt2 is not None

    # Garbage
    assert _parse_ts("not a date") is None
    assert _parse_ts(None) is None

    # datetime passes through
    aware = datetime(2026, 5, 21, tzinfo=timezone.utc)
    assert _parse_ts(aware) == aware
