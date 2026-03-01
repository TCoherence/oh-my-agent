import pytest
from pathlib import Path

from oh_my_agent.memory.adaptive import AdaptiveMemoryStore, MemoryEntry


@pytest.fixture
async def store(tmp_path):
    s = AdaptiveMemoryStore(tmp_path / "memories.yaml")
    await s.load()
    return s


@pytest.mark.asyncio
async def test_load_save_roundtrip(tmp_path):
    path = tmp_path / "memories.yaml"
    s = AdaptiveMemoryStore(path)
    await s.load()

    entries = [MemoryEntry(summary="User prefers dark mode", category="preference", confidence=0.8)]
    await s.add_memories(entries)

    # Reload from disk
    s2 = AdaptiveMemoryStore(path)
    await s2.load()
    assert len(s2.memories) == 1
    assert s2.memories[0].summary == "User prefers dark mode"
    assert s2.memories[0].category == "preference"


@pytest.mark.asyncio
async def test_load_missing_file(tmp_path):
    s = AdaptiveMemoryStore(tmp_path / "nonexistent.yaml")
    await s.load()
    assert s.memories == []


@pytest.mark.asyncio
async def test_dedup_and_boost(store):
    e1 = MemoryEntry(summary="user prefers python", category="preference", confidence=0.6)
    await store.add_memories([e1])
    assert len(store.memories) == 1
    original_confidence = store.memories[0].confidence

    # Add similar memory → should merge, not add
    e2 = MemoryEntry(summary="user prefers python language", category="preference", confidence=0.7)
    added = await store.add_memories([e2])
    assert added == 0  # no new entries
    assert len(store.memories) == 1
    assert store.memories[0].confidence > original_confidence
    assert store.memories[0].observation_count == 2


@pytest.mark.asyncio
async def test_distinct_memories_added(store):
    e1 = MemoryEntry(summary="user likes dark mode", category="preference")
    e2 = MemoryEntry(summary="project uses FastAPI framework", category="project_knowledge")
    added = await store.add_memories([e1, e2])
    assert added == 2
    assert len(store.memories) == 2


@pytest.mark.asyncio
async def test_cap_eviction(tmp_path):
    s = AdaptiveMemoryStore(tmp_path / "memories.yaml", max_memories=3)
    await s.load()

    entries = [
        MemoryEntry(summary=f"memory number {i}", confidence=0.4 + i * 0.1)
        for i in range(5)
    ]
    await s.add_memories(entries)
    assert len(s.memories) <= 3


@pytest.mark.asyncio
async def test_prune_low_confidence(tmp_path):
    s = AdaptiveMemoryStore(tmp_path / "memories.yaml", min_confidence=0.5)
    await s.load()

    entries = [
        MemoryEntry(summary="high conf", confidence=0.8),
        MemoryEntry(summary="low conf thing", confidence=0.2),
    ]
    await s.add_memories(entries)
    assert len(s.memories) == 1
    assert s.memories[0].summary == "high conf"


@pytest.mark.asyncio
async def test_relevance_scoring(store):
    await store.add_memories([
        MemoryEntry(summary="user prefers dark mode for UI", category="preference", confidence=0.9),
        MemoryEntry(summary="project uses PostgreSQL database", category="project_knowledge", confidence=0.9),
    ])

    results = await store.get_relevant("what database does the project use")
    assert len(results) >= 1
    # PostgreSQL memory should rank higher for this query
    summaries = [m.summary for m in results]
    assert any("PostgreSQL" in s for s in summaries)


@pytest.mark.asyncio
async def test_budget_limit(store):
    await store.add_memories([
        MemoryEntry(summary="a" * 200, confidence=0.9),
        MemoryEntry(summary="b" * 200, confidence=0.8),
        MemoryEntry(summary="c" * 200, confidence=0.7),
    ])

    results = await store.get_relevant("anything", budget_chars=250)
    # Should not fit all 3 at ~210 chars each
    assert len(results) < 3


@pytest.mark.asyncio
async def test_preference_always_included(store):
    await store.add_memories([
        MemoryEntry(summary="user prefers vim keybindings", category="preference", confidence=0.9),
        MemoryEntry(summary="database uses postgres", category="project_knowledge", confidence=0.9),
    ])

    # Query unrelated to preferences — preference should still appear
    results = await store.get_relevant("tell me about the weather", budget_chars=1000)
    summaries = [m.summary for m in results]
    assert any("vim" in s for s in summaries)


@pytest.mark.asyncio
async def test_delete_memory(store):
    e = MemoryEntry(id="test123", summary="to be deleted", confidence=0.8)
    await store.add_memories([e])
    assert len(store.memories) == 1

    deleted = await store.delete_memory("test123")
    assert deleted is True
    assert len(store.memories) == 0

    # Deleting non-existent returns False
    deleted = await store.delete_memory("nope")
    assert deleted is False


@pytest.mark.asyncio
async def test_jaccard_similarity():
    s = AdaptiveMemoryStore.__new__(AdaptiveMemoryStore)
    words_a = {"user", "prefers", "python"}
    words_b = {"user", "prefers", "python", "language"}
    score = s._similarity_score(words_a, words_b)
    assert score == pytest.approx(3 / 4)  # 3 intersection, 4 union

    # Empty sets
    assert s._similarity_score(set(), {"a"}) == 0.0
    assert s._similarity_score(set(), set()) == 0.0


@pytest.mark.asyncio
async def test_invalid_category_normalized(store):
    e = MemoryEntry(summary="test", category="invalid_cat", confidence=0.7)
    await store.add_memories([e])
    assert store.memories[0].category == "fact"


@pytest.mark.asyncio
async def test_confidence_clamped(store):
    e1 = MemoryEntry(summary="over confident", confidence=1.5)
    e2 = MemoryEntry(summary="negative confidence thing", confidence=-0.5)
    await store.add_memories([e1, e2])
    for m in store.memories:
        assert 0.0 <= m.confidence <= 1.0
