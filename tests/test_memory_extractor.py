import pytest
from unittest.mock import AsyncMock, MagicMock

from oh_my_agent.memory.adaptive import AdaptiveMemoryStore, MemoryEntry
from oh_my_agent.memory.extractor import MemoryExtractor


@pytest.fixture
async def store(tmp_path):
    s = AdaptiveMemoryStore(tmp_path / "memories.yaml")
    await s.load()
    return s


@pytest.fixture
def extractor(store):
    return MemoryExtractor(store)


def _mock_registry(response_text, error=None):
    agent = MagicMock()
    agent.name = "claude"
    response = MagicMock()
    response.text = response_text
    response.error = error
    registry = MagicMock()
    registry.run = AsyncMock(return_value=(agent, response))
    return registry


@pytest.mark.asyncio
async def test_valid_json_parsing(extractor, store):
    registry = _mock_registry('[{"summary": "User prefers dark mode", "category": "preference", "confidence": 0.9}]')
    turns = [
        {"role": "user", "content": "I always use dark mode"},
        {"role": "assistant", "content": "Noted!"},
    ]

    result = await extractor.extract(turns, registry, thread_id="t1")
    assert len(result) == 1
    assert result[0].summary == "User prefers dark mode"
    assert result[0].category == "preference"
    assert len(store.memories) == 1


@pytest.mark.asyncio
async def test_markdown_fence_stripping(extractor, store):
    response = '```json\n[{"summary": "Uses pytest for testing", "category": "workflow", "confidence": 0.7}]\n```'
    registry = _mock_registry(response)
    turns = [{"role": "user", "content": "run pytest"}]

    result = await extractor.extract(turns, registry, thread_id="t2")
    assert len(result) == 1
    assert result[0].summary == "Uses pytest for testing"


@pytest.mark.asyncio
async def test_agent_failure_graceful(extractor, store):
    registry = _mock_registry("", error="Agent timed out")
    turns = [{"role": "user", "content": "hello"}]

    result = await extractor.extract(turns, registry)
    assert result == []
    assert len(store.memories) == 0


@pytest.mark.asyncio
async def test_agent_exception_graceful(extractor, store):
    registry = MagicMock()
    registry.run = AsyncMock(side_effect=RuntimeError("connection failed"))
    turns = [{"role": "user", "content": "hello"}]

    result = await extractor.extract(turns, registry)
    assert result == []


@pytest.mark.asyncio
async def test_invalid_json_graceful(extractor, store):
    registry = _mock_registry("This is not JSON at all")
    turns = [{"role": "user", "content": "hello"}]

    result = await extractor.extract(turns, registry)
    assert result == []


@pytest.mark.asyncio
async def test_empty_array_response(extractor, store):
    registry = _mock_registry("[]")
    turns = [{"role": "user", "content": "hello"}]

    result = await extractor.extract(turns, registry)
    assert result == []


@pytest.mark.asyncio
async def test_merge_into_store(extractor, store):
    # Pre-populate store
    await store.add_memories([
        MemoryEntry(summary="user prefers python", category="preference", confidence=0.6)
    ])

    # Agent returns similar memory → should merge
    registry = _mock_registry('[{"summary": "user prefers python language", "category": "preference", "confidence": 0.8}]')
    turns = [{"role": "user", "content": "I love python"}]

    await extractor.extract(turns, registry, thread_id="t3")
    assert len(store.memories) == 1  # merged, not added
    assert store.memories[0].confidence > 0.6  # boosted


@pytest.mark.asyncio
async def test_empty_turns(extractor):
    registry = _mock_registry("[]")
    result = await extractor.extract([], registry)
    assert result == []


@pytest.mark.asyncio
async def test_thread_id_in_source(extractor, store):
    registry = _mock_registry('[{"summary": "Uses vim", "category": "preference", "confidence": 0.8}]')
    turns = [{"role": "user", "content": "I use vim"}]

    result = await extractor.extract(turns, registry, thread_id="thread-42")
    assert len(result) == 1
    assert "thread-42" in store.memories[0].source_threads


@pytest.mark.asyncio
async def test_parse_response_static():
    entries = MemoryExtractor._parse_response(
        '```\n[{"summary": "test", "category": "fact", "confidence": 0.5}]\n```',
        thread_id="t1",
    )
    assert len(entries) == 1
    assert entries[0].summary == "test"
    assert entries[0].source_threads == ["t1"]
