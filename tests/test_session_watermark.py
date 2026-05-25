"""Unit tests for the per-(thread, agent) seen-turn watermark protocol."""

import pytest

from oh_my_agent.memory.session_watermark import (
    advance_watermark_if_clean,
    can_advance_watermark,
    persisted_turn_ids,
    session_is_stale,
    should_resume,
)
from oh_my_agent.memory.store import SplitSQLiteMemoryStore

P = ("discord", "chan", "thr")


# ── pure decision logic ──────────────────────────────────────────────── #


def test_should_resume_clean_back_to_back():
    # last_seen=5; only new turn is this invocation's user turn 6 → resume.
    assert should_resume(
        session_exists=True, last_seen_turn_id=5,
        existing_turn_ids=[3, 4, 5, 6], owned_turn_ids={6},
    ) is True


def test_should_resume_foreign_turn_blocks():
    # A runtime task turn 6 landed before this user turn 7 → stale → fresh.
    assert should_resume(
        session_exists=True, last_seen_turn_id=5,
        existing_turn_ids=[5, 6, 7], owned_turn_ids={7},
    ) is False


def test_should_resume_no_session():
    assert should_resume(
        session_exists=False, last_seen_turn_id=5,
        existing_turn_ids=[5], owned_turn_ids=set(),
    ) is False


def test_should_resume_none_watermark_is_stale():
    # A restored session whose watermark predates the column → fresh once.
    assert should_resume(
        session_exists=True, last_seen_turn_id=None,
        existing_turn_ids=[1], owned_turn_ids=set(),
    ) is False


def test_session_is_stale_only_owned_above_baseline():
    assert session_is_stale(
        last_seen_turn_id=5, existing_turn_ids=[5, 6], owned_turn_ids={6},
    ) is False


def test_can_advance_clean_exchange():
    assert can_advance_watermark(
        baseline_id=5, candidate_id=7,
        existing_turn_ids=[5, 6, 7], owned_turn_ids={6, 7},
    ) is True


def test_can_advance_blocked_by_interleave():
    # Foreign turn 7 landed between user 6 and assistant 8 → do not advance.
    assert can_advance_watermark(
        baseline_id=5, candidate_id=8,
        existing_turn_ids=[5, 6, 7, 8], owned_turn_ids={6, 8},
    ) is False


def test_can_advance_control_frame_no_visible_text():
    # Only the user turn is owned (control frame emitted no visible reply).
    assert can_advance_watermark(
        baseline_id=5, candidate_id=6,
        existing_turn_ids=[5, 6], owned_turn_ids={6},
    ) is True


def test_can_advance_auth_resume_no_user_row():
    # Auth-suspended resume: owned is the output only; baseline is snapshot max.
    assert can_advance_watermark(
        baseline_id=6, candidate_id=8,
        existing_turn_ids=[5, 6, 8], owned_turn_ids={8},
    ) is True


# ── advance_watermark_if_clean against a real store ──────────────────── #


@pytest.fixture
async def store(tmp_path):
    st = SplitSQLiteMemoryStore(
        conversation_path=tmp_path / "c.db",
        runtime_state_path=tmp_path / "r.db",
        skills_telemetry_path=tmp_path / "s.db",
    )
    await st.init()
    yield st
    await st.close()


async def test_persisted_turn_ids_orders_ascending(store):
    a = await store.append(*P, {"role": "user", "content": "1", "author": "u"})
    b = await store.append(*P, {"role": "assistant", "content": "2", "agent": "claude"})
    assert await persisted_turn_ids(store, *P) == [a, b]


async def test_advance_persists_when_clean(store):
    uid = await store.append(*P, {"role": "user", "content": "q", "author": "u"})
    aid = await store.append(*P, {"role": "assistant", "content": "a", "agent": "claude"})
    await store.save_session(*P, "claude", "sess")
    advanced = await advance_watermark_if_clean(
        store=store, platform=P[0], channel_id=P[1], thread_id=P[2],
        agent="claude", baseline_id=uid, owned_turn_ids={uid, aid},
    )
    assert advanced is True
    _sid, last = await store.load_session_state(*P, "claude")
    assert last == aid


async def test_advance_blocked_by_foreign_interleave(store):
    """A runtime turn appended during the run must block the advance, so the
    next reply re-seeds fresh (round-3/round-5 invariant)."""
    uid = await store.append(*P, {"role": "user", "content": "q", "author": "u"})
    # Foreign task turn lands *during* the agent run (between user and assistant).
    foreign = await store.append(*P, {"role": "assistant", "content": "task", "agent": "runtime"})
    aid = await store.append(*P, {"role": "assistant", "content": "a", "agent": "claude"})
    await store.save_session(*P, "claude", "sess")
    advanced = await advance_watermark_if_clean(
        store=store, platform=P[0], channel_id=P[1], thread_id=P[2],
        agent="claude", baseline_id=uid, owned_turn_ids={uid, aid},
    )
    assert advanced is False
    assert foreign  # referenced
    _sid, last = await store.load_session_state(*P, "claude")
    assert last is None  # watermark stayed put → next gate goes fresh


async def test_advance_noop_without_owned(store):
    await store.append(*P, {"role": "user", "content": "q", "author": "u"})
    await store.save_session(*P, "claude", "sess")
    advanced = await advance_watermark_if_clean(
        store=store, platform=P[0], channel_id=P[1], thread_id=P[2],
        agent="claude", baseline_id=None, owned_turn_ids=set(),
    )
    assert advanced is False
