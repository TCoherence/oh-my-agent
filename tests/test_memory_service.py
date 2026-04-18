"""Tests for MemoryService — covers list/forget/memorize success + error paths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from oh_my_agent.gateway.services.memory_service import MemoryService
from oh_my_agent.gateway.services.types import (
    MemoryActionResult,
    MemoryListResult,
)
from oh_my_agent.memory.judge_store import JudgeStore, MemoryEntry


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> JudgeStore:
    s = JudgeStore(memory_dir=tmp_path / "memory")
    await s.load()
    return s


# ── list_entries ─────────────────────────────────────────────────────── #

def test_list_entries_no_store_returns_disabled_message():
    service = MemoryService(judge_store=None)
    result = service.list_entries()
    assert isinstance(result, MemoryListResult)
    assert result.success is False
    assert "not enabled" in result.message.lower()
    assert result.entries == []


@pytest.mark.asyncio
async def test_list_entries_empty_store_returns_friendly_message(store: JudgeStore):
    service = MemoryService(judge_store=store)
    result = service.list_entries()
    assert result.success is True
    assert result.entries == []
    assert result.total_active == 0
    assert "no memories" in result.message.lower()


@pytest.mark.asyncio
async def test_list_entries_returns_active_only_sorted_by_scope(store: JudgeStore):
    store._memories = [
        MemoryEntry(
            id="aaa", summary="thread fact", scope="thread",
            confidence=0.9, observation_count=3, category="fact",
        ),
        MemoryEntry(
            id="bbb", summary="global pref", scope="global_user",
            confidence=0.8, observation_count=2, category="preference",
        ),
        MemoryEntry(
            id="ccc", summary="superseded entry", scope="workspace",
            confidence=0.7, status="superseded", category="workflow",
        ),
        MemoryEntry(
            id="ddd", summary="workspace knowledge", scope="workspace",
            confidence=0.95, observation_count=5, category="project_knowledge",
        ),
    ]

    service = MemoryService(judge_store=store)
    result = service.list_entries()

    assert result.success is True
    assert result.total_active == 3  # superseded excluded
    ids = [e.memory_id for e in result.entries]
    # Sort key is (scope, -confidence, -observation_count) — alphabetical scope.
    assert ids == ["bbb", "aaa", "ddd"]
    # Round-trip key fields
    bbb = next(e for e in result.entries if e.memory_id == "bbb")
    assert bbb.category == "preference"
    assert bbb.scope == "global_user"
    assert bbb.confidence == 0.8
    assert bbb.observation_count == 2


@pytest.mark.asyncio
async def test_list_entries_filters_by_category_keeps_total_active(store: JudgeStore):
    store._memories = [
        MemoryEntry(id="aaa", summary="a", scope="global_user", category="preference"),
        MemoryEntry(id="bbb", summary="b", scope="global_user", category="fact"),
        MemoryEntry(id="ccc", summary="c", scope="global_user", category="preference"),
    ]
    service = MemoryService(judge_store=store)
    result = service.list_entries(category="preference")
    assert result.success is True
    assert result.total_active == 3
    assert result.category_filter == "preference"
    assert {e.memory_id for e in result.entries} == {"aaa", "ccc"}


@pytest.mark.asyncio
async def test_list_entries_filter_with_no_matches_uses_category_in_message(store: JudgeStore):
    store._memories = [
        MemoryEntry(id="aaa", summary="a", scope="global_user", category="fact"),
    ]
    service = MemoryService(judge_store=store)
    result = service.list_entries(category="preference")
    assert result.success is True
    assert result.entries == []
    assert "preference" in result.message


# ── forget ──────────────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_forget_no_store_returns_disabled_message():
    service = MemoryService(judge_store=None)
    result = await service.forget("nonexistent")
    assert isinstance(result, MemoryActionResult)
    assert result.success is False
    assert "not enabled" in result.message.lower()


@pytest.mark.asyncio
async def test_forget_unknown_id_returns_not_found(store: JudgeStore):
    service = MemoryService(judge_store=store)
    result = await service.forget("never-seen")
    assert result.success is False
    assert result.memory_id == "never-seen"
    assert "not found" in result.message.lower() or "inactive" in result.message.lower()


@pytest.mark.asyncio
async def test_forget_active_entry_marks_superseded_and_triggers_synth(store: JudgeStore):
    entry = MemoryEntry(id="abc123", summary="forget me", scope="global_user")
    store._memories = [entry]

    gateway = MagicMock()
    gateway._try_memory_md_synth = AsyncMock()
    registry = object()

    service = MemoryService(
        judge_store=store,
        gateway_manager=gateway,
        registry=registry,
    )
    result = await service.forget("abc123")

    assert result.success is True
    assert result.memory_id == "abc123"
    assert store.get_by_id("abc123").status == "superseded"
    gateway._try_memory_md_synth.assert_awaited_once_with(registry)


@pytest.mark.asyncio
async def test_forget_synth_failure_is_swallowed(store: JudgeStore):
    """A synth crash must not mask the successful supersede."""
    entry = MemoryEntry(id="abc123", summary="forget me", scope="global_user")
    store._memories = [entry]

    gateway = MagicMock()
    gateway._try_memory_md_synth = AsyncMock(side_effect=RuntimeError("agent down"))

    service = MemoryService(
        judge_store=store,
        gateway_manager=gateway,
        registry=object(),
    )
    result = await service.forget("abc123")

    assert result.success is True
    assert store.get_by_id("abc123").status == "superseded"


@pytest.mark.asyncio
async def test_forget_no_registry_skips_synth(store: JudgeStore):
    """When the channel has no registry yet, forget still works but no synth."""
    entry = MemoryEntry(id="abc123", summary="forget me", scope="global_user")
    store._memories = [entry]

    gateway = MagicMock()
    gateway._try_memory_md_synth = AsyncMock()
    service = MemoryService(judge_store=store, gateway_manager=gateway, registry=None)
    result = await service.forget("abc123")

    assert result.success is True
    gateway._try_memory_md_synth.assert_not_called()


# ── memorize ────────────────────────────────────────────────────────── #

@pytest.mark.asyncio
async def test_memorize_no_gateway_returns_disabled():
    service = MemoryService(judge_store=None)
    result = await service.memorize(
        platform="discord", channel_id="100", thread_id="200",
    )
    assert result.success is False
    assert "not enabled" in result.message.lower()


@pytest.mark.asyncio
async def test_memorize_judge_unavailable_when_gateway_returns_none(store: JudgeStore):
    gateway = MagicMock()
    gateway.request_memorize = AsyncMock(return_value=None)
    service = MemoryService(judge_store=store, gateway_manager=gateway)
    result = await service.memorize(
        platform="discord", channel_id="100", thread_id="200",
    )
    assert result.success is False
    assert "not available" in result.message.lower()


@pytest.mark.asyncio
async def test_memorize_propagates_gateway_error(store: JudgeStore):
    gateway = MagicMock()
    gateway.request_memorize = AsyncMock(return_value={"error": "no active session"})
    service = MemoryService(judge_store=store, gateway_manager=gateway)
    result = await service.memorize(
        platform="discord", channel_id="100", thread_id="200",
    )
    assert result.success is False
    assert "no active session" in result.message


@pytest.mark.asyncio
async def test_memorize_handles_gateway_exception(store: JudgeStore):
    gateway = MagicMock()
    gateway.request_memorize = AsyncMock(side_effect=RuntimeError("boom"))
    service = MemoryService(judge_store=store, gateway_manager=gateway)
    result = await service.memorize(
        platform="discord", channel_id="100", thread_id="200",
    )
    assert result.success is False
    assert "boom" in result.message


@pytest.mark.asyncio
async def test_memorize_success_returns_judge_stats_summary(store: JudgeStore):
    gateway = MagicMock()
    gateway.request_memorize = AsyncMock(
        return_value={
            "stats": {"add": 2, "strengthen": 1, "supersede": 0, "no_op": 1},
            "actions": [
                {"op": "add"},
                {"op": "add"},
                {"op": "strengthen"},
                {"op": "no_op"},
            ],
        }
    )
    service = MemoryService(judge_store=store, gateway_manager=gateway)
    result = await service.memorize(
        platform="discord",
        channel_id="100",
        thread_id="200",
        explicit_summary="user prefers concise replies",
        explicit_scope="global_user",
    )

    assert result.success is True
    assert result.judge_action_count == 4
    assert result.judge_stats == {"add": 2, "strengthen": 1, "supersede": 0, "no_op": 1}
    assert "actions=4" in result.message
    assert "add=2" in result.message
    gateway.request_memorize.assert_awaited_once_with(
        platform="discord",
        channel_id="100",
        thread_id="200",
        explicit_summary="user prefers concise replies",
        explicit_scope="global_user",
    )
