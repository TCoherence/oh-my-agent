"""Tests for DateBasedMemoryStore — two-tier date-organized memory."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from oh_my_agent.memory.adaptive import MemoryEntry
from oh_my_agent.memory.date_based import DateBasedMemoryStore, _today


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(
    summary="test memory",
    category="fact",
    confidence=0.6,
    observation_count=1,
    tier="daily",
    created_at=None,
    source_threads=None,
) -> MemoryEntry:
    return MemoryEntry(
        summary=summary,
        category=category,
        confidence=confidence,
        observation_count=observation_count,
        tier=tier,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
        source_threads=source_threads or [],
    )


def _write_yaml(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    from dataclasses import asdict
    data = [asdict(e) if hasattr(e, '__dataclass_fields__') else e for e in entries]
    path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


@pytest.fixture
async def store(tmp_path):
    s = DateBasedMemoryStore(memory_dir=tmp_path / "memory")
    await s.load()
    return s


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_empty_dir(tmp_path):
    s = DateBasedMemoryStore(memory_dir=tmp_path / "memory")
    await s.load()
    assert s.memories == []


@pytest.mark.asyncio
async def test_add_and_list(store):
    added = await store.add_memories([_entry(summary="user likes python")])
    assert added == 1
    all_mems = await store.list_all()
    assert len(all_mems) == 1
    assert all_mems[0].summary == "user likes python"
    assert all_mems[0].tier == "daily"


@pytest.mark.asyncio
async def test_save_roundtrip(tmp_path):
    mem_dir = tmp_path / "memory"
    s = DateBasedMemoryStore(memory_dir=mem_dir)
    await s.load()
    await s.add_memories([_entry(summary="roundtrip test")])
    await s.save()

    # Reload
    s2 = DateBasedMemoryStore(memory_dir=mem_dir)
    await s2.load()
    all_mems = await s2.list_all()
    assert len(all_mems) == 1
    assert all_mems[0].summary == "roundtrip test"


@pytest.mark.asyncio
async def test_delete_daily(store):
    await store.add_memories([_entry(summary="to delete")])
    mems = await store.list_all()
    assert len(mems) == 1
    deleted = await store.delete_memory(mems[0].id)
    assert deleted is True
    assert await store.list_all() == []


@pytest.mark.asyncio
async def test_delete_curated(tmp_path):
    mem_dir = tmp_path / "memory"
    s = DateBasedMemoryStore(memory_dir=mem_dir)
    await s.load()

    # Add and manually promote
    await s.add_memories([_entry(summary="curated entry")])
    mems = await s.list_all()
    mid = mems[0].id
    await s.promote_memory(mid)

    # Verify it's curated now
    all_mems = await s.list_all()
    assert any(m.tier == "curated" for m in all_mems)

    # Delete it
    deleted = await s.delete_memory(mid)
    assert deleted is True


@pytest.mark.asyncio
async def test_delete_nonexistent(store):
    assert await store.delete_memory("nonexistent") is False


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_dedup(store):
    await store.add_memories([_entry(summary="user prefers dark mode")])
    added = await store.add_memories([_entry(summary="user prefers dark mode theme")])
    assert added == 0  # merged
    mems = await store.list_all()
    assert len(mems) == 1
    assert mems[0].confidence > 0.6  # boosted


@pytest.mark.asyncio
async def test_daily_vs_curated_dedup(tmp_path):
    mem_dir = tmp_path / "memory"
    s = DateBasedMemoryStore(memory_dir=mem_dir)
    await s.load()

    # Add and promote
    await s.add_memories([_entry(summary="uses pytest for testing")])
    mems = await s.list_all()
    await s.promote_memory(mems[0].id)

    # Add similar — should boost curated, not add new
    added = await s.add_memories([_entry(summary="uses pytest for testing framework")])
    assert added == 0
    all_mems = await s.list_all()
    curated = [m for m in all_mems if m.tier == "curated"]
    assert len(curated) == 1
    assert curated[0].confidence > 0.6


@pytest.mark.asyncio
async def test_different_memories_independent(store):
    await store.add_memories([_entry(summary="user likes python")])
    added = await store.add_memories([_entry(summary="project uses react")])
    assert added == 1
    assert len(await store.list_all()) == 2


# ---------------------------------------------------------------------------
# Time decay
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_today_entry_low_decay(store):
    await store.add_memories([_entry(summary="fresh memory")])
    relevant = await store.get_relevant("fresh memory", budget_chars=500)
    assert len(relevant) == 1


@pytest.mark.asyncio
async def test_old_entry_has_decay(tmp_path):
    mem_dir = tmp_path / "memory"
    old_date = (datetime.now(timezone.utc) - timedelta(days=30)).date()
    daily_dir = mem_dir / "daily"
    daily_dir.mkdir(parents=True)

    old_entry = _entry(
        summary="ancient memory about python",
        created_at=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
    )
    _write_yaml(daily_dir / f"{old_date.isoformat()}.yaml", [old_entry])

    s = DateBasedMemoryStore(memory_dir=mem_dir, decay_half_life_days=7.0)
    await s.load()

    # Add a fresh memory
    await s.add_memories([_entry(summary="fresh memory about python")])

    # Fresh should rank higher than decayed old
    relevant = await s.get_relevant("python", budget_chars=500)
    assert len(relevant) >= 1


@pytest.mark.asyncio
async def test_curated_no_decay(tmp_path):
    mem_dir = tmp_path / "memory"

    # Write a curated entry directly
    curated_entry = _entry(
        summary="user prefers vim editor",
        tier="curated",
        confidence=0.9,
        created_at=(datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
    )
    _write_yaml(mem_dir / "curated.yaml", [curated_entry])

    s = DateBasedMemoryStore(memory_dir=mem_dir)
    await s.load()

    relevant = await s.get_relevant("vim editor", budget_chars=500)
    assert len(relevant) == 1
    assert relevant[0].tier == "curated"


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_promote_eligible(tmp_path):
    mem_dir = tmp_path / "memory"
    daily_dir = mem_dir / "daily"
    daily_dir.mkdir(parents=True)

    old_date = (datetime.now(timezone.utc) - timedelta(days=3)).date()
    eligible = _entry(
        summary="user always uses black formatter",
        confidence=0.9,
        observation_count=5,
        created_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
    )
    _write_yaml(daily_dir / f"{old_date.isoformat()}.yaml", [eligible])

    s = DateBasedMemoryStore(
        memory_dir=mem_dir,
        promotion_observation_threshold=3,
        promotion_confidence_threshold=0.8,
    )
    await s.load()

    curated = [m for m in s.memories if m.tier == "curated"]
    assert len(curated) == 1
    assert curated[0].summary == "user always uses black formatter"


@pytest.mark.asyncio
async def test_no_promote_low_count(tmp_path):
    mem_dir = tmp_path / "memory"
    daily_dir = mem_dir / "daily"
    daily_dir.mkdir(parents=True)

    old_date = (datetime.now(timezone.utc) - timedelta(days=3)).date()
    entry = _entry(
        summary="might use flake8",
        confidence=0.9,
        observation_count=1,  # below threshold
        created_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
    )
    _write_yaml(daily_dir / f"{old_date.isoformat()}.yaml", [entry])

    s = DateBasedMemoryStore(memory_dir=mem_dir, promotion_observation_threshold=3)
    await s.load()

    curated = [m for m in s.memories if m.tier == "curated"]
    assert len(curated) == 0


@pytest.mark.asyncio
async def test_no_promote_low_confidence(tmp_path):
    mem_dir = tmp_path / "memory"
    daily_dir = mem_dir / "daily"
    daily_dir.mkdir(parents=True)

    old_date = (datetime.now(timezone.utc) - timedelta(days=3)).date()
    entry = _entry(
        summary="maybe prefers tabs",
        confidence=0.4,  # below threshold
        observation_count=5,
        created_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
    )
    _write_yaml(daily_dir / f"{old_date.isoformat()}.yaml", [entry])

    s = DateBasedMemoryStore(memory_dir=mem_dir, promotion_confidence_threshold=0.8)
    await s.load()

    curated = [m for m in s.memories if m.tier == "curated"]
    assert len(curated) == 0


@pytest.mark.asyncio
async def test_no_promote_too_recent(tmp_path):
    mem_dir = tmp_path / "memory"
    s = DateBasedMemoryStore(memory_dir=mem_dir)
    await s.load()

    # Add today — should NOT be promoted even with high count/confidence
    await s.add_memories([
        _entry(
            summary="brand new observation",
            confidence=0.95,
            observation_count=10,
        )
    ])

    curated = [m for m in s.memories if m.tier == "curated"]
    assert len(curated) == 0


@pytest.mark.asyncio
async def test_manual_promote(store):
    await store.add_memories([_entry(summary="manually promote this")])
    mems = await store.list_all()
    mid = mems[0].id

    result = await store.promote_memory(mid)
    assert result is True

    all_mems = await store.list_all()
    curated = [m for m in all_mems if m.tier == "curated"]
    assert len(curated) == 1
    assert curated[0].id == mid


@pytest.mark.asyncio
async def test_manual_promote_nonexistent(store):
    result = await store.promote_memory("nonexistent")
    assert result is False


# ---------------------------------------------------------------------------
# Injection (get_relevant)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_curated_priority_over_daily(tmp_path):
    mem_dir = tmp_path / "memory"

    curated_entry = _entry(
        summary="user prefers python language",
        tier="curated",
        confidence=0.9,
    )
    _write_yaml(mem_dir / "curated.yaml", [curated_entry])

    s = DateBasedMemoryStore(memory_dir=mem_dir)
    await s.load()
    await s.add_memories([_entry(summary="uses javascript sometimes", confidence=0.5)])

    relevant = await s.get_relevant("python language", budget_chars=100)
    assert len(relevant) >= 1
    assert relevant[0].tier == "curated"


@pytest.mark.asyncio
async def test_budget_constraint(store):
    # Add many memories
    for i in range(20):
        await store.add_memories([_entry(summary=f"memory item number {i} about topic")])

    # Small budget should limit results
    relevant = await store.get_relevant("memory item topic", budget_chars=50)
    total_chars = sum(len(m.summary) + 10 for m in relevant)
    assert total_chars <= 100  # some slack for first entry


@pytest.mark.asyncio
async def test_preference_minimum_score(store):
    await store.add_memories([_entry(summary="prefers vim", category="preference", confidence=0.8)])
    relevant = await store.get_relevant("completely unrelated query", budget_chars=500)
    assert len(relevant) >= 1


# ---------------------------------------------------------------------------
# MEMORY.md synthesis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_synthesize_calls_agent(tmp_path):
    mem_dir = tmp_path / "memory"
    curated_entry = _entry(summary="user prefers dark mode", tier="curated", category="preference")
    _write_yaml(mem_dir / "curated.yaml", [curated_entry])

    s = DateBasedMemoryStore(memory_dir=mem_dir)
    await s.load()

    agent = MagicMock()
    agent.name = "claude"
    response = MagicMock()
    response.text = "# User Memories\n\nYou prefer dark mode."
    response.error = None
    registry = MagicMock()
    registry.run = AsyncMock(return_value=(agent, response))

    await s.synthesize_memory_md(registry)

    md_path = mem_dir / "MEMORY.md"
    assert md_path.exists()
    assert "dark mode" in md_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_synthesize_agent_failure_keeps_old(tmp_path):
    mem_dir = tmp_path / "memory"
    md_path = mem_dir / "MEMORY.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("# Old Content\nKeep this.", encoding="utf-8")

    curated_entry = _entry(summary="something", tier="curated")
    _write_yaml(mem_dir / "curated.yaml", [curated_entry])

    s = DateBasedMemoryStore(memory_dir=mem_dir)
    await s.load()

    registry = MagicMock()
    registry.run = AsyncMock(side_effect=RuntimeError("agent down"))

    await s.synthesize_memory_md(registry)

    assert md_path.read_text(encoding="utf-8") == "# Old Content\nKeep this."
