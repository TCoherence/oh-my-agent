"""Tests for SkillEvalService — stats/toggle/reaction paths."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from oh_my_agent.gateway.services.skill_eval_service import SkillEvalService
from oh_my_agent.gateway.services.types import (
    ServiceResult,
    SkillStatsResult,
    SkillToggleResult,
)


def _stat_row(skill_name: str, **overrides) -> dict:
    base = {
        "skill_name": skill_name,
        "auto_disabled": 0,
        "total_invocations": 7,
        "recent_invocations": 5,
        "recent_successes": 4,
        "recent_errors": 1,
        "recent_timeouts": 0,
        "recent_cancelled": 0,
        "recent_avg_latency_ms": 1500.0,
        "thumbs_up": 2,
        "thumbs_down": 0,
        "net_feedback": 2,
        "last_invoked_at": "2026-04-18T10:00:00Z",
        "merged_commit_hash": None,
        "auto_disabled_reason": None,
    }
    base.update(overrides)
    return base


# ── Construction / config ──────────────────────────────────────────── #

def test_default_feedback_emojis_match_discord_set():
    service = SkillEvalService(memory_store=MagicMock())
    assert service.feedback_emojis == {"👍", "👎"}
    assert service.is_feedback_emoji("👍") is True
    assert service.is_feedback_emoji("🎉") is False


def test_recent_days_clamped_to_at_least_one():
    service = SkillEvalService(memory_store=MagicMock(), recent_days=0)
    assert service.recent_days == 1


# ── get_stats ──────────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_get_stats_no_store_returns_disabled():
    service = SkillEvalService(memory_store=None)
    result = await service.get_stats()
    assert isinstance(result, SkillStatsResult)
    assert result.success is False
    assert "not configured" in result.message.lower()


@pytest.mark.asyncio
async def test_get_stats_store_lacking_method_returns_disabled():
    """A memory store without skill stats support degrades gracefully."""
    bare_store = object()
    service = SkillEvalService(memory_store=bare_store)
    result = await service.get_stats()
    assert result.success is False
    assert "not configured" in result.message.lower()


@pytest.mark.asyncio
async def test_get_stats_no_rows_returns_friendly_message():
    store = MagicMock()
    store.get_skill_stats = AsyncMock(return_value=[])
    service = SkillEvalService(memory_store=store, recent_days=14)

    result = await service.get_stats(skill="missing-skill")

    assert result.success is True
    assert result.stats == []
    assert result.recent_days == 14
    assert result.skill_filter == "missing-skill"
    assert "missing-skill" in result.message
    store.get_skill_stats.assert_awaited_once_with("missing-skill", recent_days=14)


@pytest.mark.asyncio
async def test_get_stats_aggregate_view_does_not_fetch_evaluations():
    store = MagicMock()
    store.get_skill_stats = AsyncMock(
        return_value=[_stat_row("skill-a"), _stat_row("skill-b", auto_disabled=1)]
    )
    store.get_latest_skill_evaluations = AsyncMock(return_value=[{"x": 1}])

    service = SkillEvalService(memory_store=store)
    result = await service.get_stats()

    assert result.success is True
    assert [r.skill_name for r in result.stats] == ["skill-a", "skill-b"]
    assert result.stats[0].auto_disabled is False
    assert result.stats[1].auto_disabled is True
    assert all(r.latest_evaluations == [] for r in result.stats)
    store.get_latest_skill_evaluations.assert_not_called()


@pytest.mark.asyncio
async def test_get_stats_single_skill_attaches_latest_evaluations():
    store = MagicMock()
    store.get_skill_stats = AsyncMock(return_value=[_stat_row("skill-a")])
    store.get_latest_skill_evaluations = AsyncMock(
        return_value=[
            {"evaluation_type": "manual", "status": "pass", "summary": "looks good"},
        ]
    )
    service = SkillEvalService(memory_store=store)
    result = await service.get_stats(skill="skill-a")

    assert result.success is True
    assert len(result.stats) == 1
    row = result.stats[0]
    assert row.skill_name == "skill-a"
    assert row.recent_invocations == 5
    assert row.recent_successes == 4
    assert row.thumbs_up == 2
    assert len(row.latest_evaluations) == 1
    store.get_latest_skill_evaluations.assert_awaited_once_with("skill-a")


@pytest.mark.asyncio
async def test_get_stats_handles_none_numeric_fields_safely():
    """SQLite returns NULL → None; row builder must coerce to 0 / 0.0."""
    store = MagicMock()
    store.get_skill_stats = AsyncMock(
        return_value=[
            {
                "skill_name": "skill-a",
                "auto_disabled": None,
                "total_invocations": None,
                "recent_invocations": None,
                "recent_successes": None,
                "recent_errors": None,
                "recent_timeouts": None,
                "recent_cancelled": None,
                "recent_avg_latency_ms": None,
                "thumbs_up": None,
                "thumbs_down": None,
                "net_feedback": None,
            }
        ]
    )
    service = SkillEvalService(memory_store=store)
    result = await service.get_stats()
    row = result.stats[0]
    assert row.auto_disabled is False
    assert row.total_invocations == 0
    assert row.recent_avg_latency_ms == 0.0


# ── enable ─────────────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_enable_no_store_returns_disabled():
    service = SkillEvalService(memory_store=None)
    result = await service.enable("skill-a")
    assert isinstance(result, SkillToggleResult)
    assert result.success is False
    assert result.skill_name == "skill-a"


@pytest.mark.asyncio
async def test_enable_unknown_skill_reports_not_found():
    store = MagicMock()
    store.get_skill_provenance = AsyncMock(return_value=None)
    service = SkillEvalService(memory_store=store)

    result = await service.enable("missing-skill")

    assert result.success is False
    assert result.skill_name == "missing-skill"
    assert "not found" in result.message.lower()


@pytest.mark.asyncio
async def test_enable_existing_skill_clears_auto_disabled():
    store = MagicMock()
    store.get_skill_provenance = AsyncMock(return_value={"skill_name": "skill-a"})
    store.set_skill_auto_disabled = AsyncMock()

    service = SkillEvalService(memory_store=store)
    result = await service.enable("skill-a")

    assert result.success is True
    assert result.skill_name == "skill-a"
    assert result.now_enabled is True
    store.set_skill_auto_disabled.assert_awaited_once_with("skill-a", disabled=False)


@pytest.mark.asyncio
async def test_enable_skill_when_setter_missing_still_succeeds():
    """Older stores without set_skill_auto_disabled still report success."""
    class _StoreWithoutSetter:
        async def get_skill_provenance(self, name):
            del name
            return {"skill_name": "skill-a"}

    service = SkillEvalService(memory_store=_StoreWithoutSetter())
    result = await service.enable("skill-a")
    assert result.success is True
    assert result.now_enabled is True


# ── record_reaction ────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_record_reaction_no_store_returns_disabled():
    service = SkillEvalService(memory_store=None)
    result = await service.record_reaction(
        message_id="m1",
        actor_id="u1",
        platform="discord",
        channel_id="c1",
        thread_id="t1",
        active_score=1,
    )
    assert isinstance(result, ServiceResult)
    assert result.success is False


@pytest.mark.asyncio
async def test_record_reaction_unknown_invocation_returns_no_op():
    store = MagicMock()
    store.get_skill_invocation_by_message = AsyncMock(return_value=None)
    service = SkillEvalService(memory_store=store)
    result = await service.record_reaction(
        message_id="m1",
        actor_id="u1",
        platform="discord",
        channel_id="c1",
        thread_id="t1",
        active_score=1,
    )
    assert result.success is False
    assert "no skill invocation" in result.message.lower()


@pytest.mark.asyncio
async def test_record_reaction_clears_when_score_is_none():
    store = MagicMock()
    store.get_skill_invocation_by_message = AsyncMock(
        return_value={"id": 42, "skill_name": "skill-a"}
    )
    store.delete_skill_feedback = AsyncMock()
    store.upsert_skill_feedback = AsyncMock()

    service = SkillEvalService(memory_store=store)
    result = await service.record_reaction(
        message_id="m1",
        actor_id="u1",
        platform="discord",
        channel_id="c1",
        thread_id="t1",
        active_score=None,
    )
    assert result.success is True
    store.delete_skill_feedback.assert_awaited_once_with(invocation_id=42, actor_id="u1")
    store.upsert_skill_feedback.assert_not_called()


@pytest.mark.asyncio
async def test_record_reaction_upserts_positive_score():
    store = MagicMock()
    store.get_skill_invocation_by_message = AsyncMock(
        return_value={"id": 42, "skill_name": "skill-a"}
    )
    store.delete_skill_feedback = AsyncMock()
    store.upsert_skill_feedback = AsyncMock()

    service = SkillEvalService(memory_store=store)
    result = await service.record_reaction(
        message_id="m1",
        actor_id="u1",
        platform="discord",
        channel_id="c1",
        thread_id="t1",
        active_score=1,
    )
    assert result.success is True
    store.upsert_skill_feedback.assert_awaited_once_with(
        invocation_id=42,
        actor_id="u1",
        platform="discord",
        channel_id="c1",
        thread_id="t1",
        score=1,
        source="reaction",
    )
    store.delete_skill_feedback.assert_not_called()


@pytest.mark.asyncio
async def test_record_reaction_upserts_negative_score():
    store = MagicMock()
    store.get_skill_invocation_by_message = AsyncMock(
        return_value={"id": 42, "skill_name": "skill-a"}
    )
    store.upsert_skill_feedback = AsyncMock()
    store.delete_skill_feedback = AsyncMock()

    service = SkillEvalService(memory_store=store)
    result = await service.record_reaction(
        message_id="m1",
        actor_id="u1",
        platform="discord",
        channel_id="c1",
        thread_id="t1",
        active_score=-1,
    )
    assert result.success is True
    call = store.upsert_skill_feedback.await_args
    assert call.kwargs["score"] == -1


@pytest.mark.asyncio
async def test_record_reaction_rejects_invalid_score():
    """Out-of-range scores must not silently dispatch as upserts."""
    store = MagicMock()
    store.get_skill_invocation_by_message = AsyncMock(
        return_value={"id": 42, "skill_name": "skill-a"}
    )
    store.upsert_skill_feedback = AsyncMock()
    store.delete_skill_feedback = AsyncMock()

    service = SkillEvalService(memory_store=store)
    result = await service.record_reaction(
        message_id="m1",
        actor_id="u1",
        platform="discord",
        channel_id="c1",
        thread_id="t1",
        active_score=5,
    )
    assert result.success is False
    store.upsert_skill_feedback.assert_not_called()
    store.delete_skill_feedback.assert_not_called()
