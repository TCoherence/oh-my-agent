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
    registry = _mock_registry('[{"summary": "User prefers dark mode", "category": "preference", "confidence": 0.9, "explicitness": "explicit", "scope": "global_user", "durability": "long", "evidence": "I always use dark mode"}]')
    turns = [
        {"role": "user", "content": "I always use dark mode"},
        {"role": "assistant", "content": "Noted!"},
    ]

    result = await extractor.extract(turns, registry, thread_id="t1", req_id="req123")
    assert len(result) == 1
    assert result[0].summary == "User prefers dark mode"
    assert result[0].category == "preference"
    assert result[0].explicitness == "explicit"
    assert result[0].scope == "global_user"
    assert result[0].durability == "long"
    assert result[0].evidence == "I always use dark mode"
    assert len(store.memories) == 1
    assert registry.run.call_args.kwargs["run_label"] == "memory_extract req=req123 thread=t1"


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
async def test_extract_triggers_memory_synthesis_when_needed():
    store = MagicMock()
    store.list_all = AsyncMock(return_value=[])
    store.add_memories = AsyncMock(return_value=1)
    type(store).needs_synthesis = property(lambda _self: True)
    store.synthesize_memory_md = AsyncMock()
    store.clear_synthesis_flag = MagicMock()

    extractor = MemoryExtractor(store)
    registry = _mock_registry('[{"summary": "User prefers concise summaries", "category": "preference", "confidence": 0.9, "explicitness": "explicit", "evidence": "Keep it concise"}]')
    turns = [{"role": "user", "content": "Keep it concise"}]

    await extractor.extract(turns, registry, thread_id="thread-1")

    store.synthesize_memory_md.assert_awaited_once_with(registry)
    store.clear_synthesis_flag.assert_called_once()


@pytest.mark.asyncio
async def test_parse_response_static():
    entries, rejected, parse_failure = MemoryExtractor._parse_response(
        '```\n[{"summary": "test", "category": "fact", "confidence": 0.5, "explicitness": "inferred", "scope": "workspace", "durability": "medium", "evidence": "test"}]\n```',
        thread_id="t1",
    )
    assert len(entries) == 1
    assert rejected == 0
    assert parse_failure is False
    assert entries[0].summary == "test"
    assert entries[0].source_threads == ["t1"]
    assert entries[0].scope == "workspace"
    assert entries[0].durability == "medium"


def test_build_recent_window_keeps_recent_user_turns_and_truncates_assistant():
    turns = [
        {"role": "user", "content": "old preference"},
        {"role": "assistant", "content": "x" * 1200},
        {"role": "assistant", "content": "y" * 1200},
        {"role": "user", "content": "recent user one"},
        {"role": "assistant", "content": "short reply"},
        {"role": "user", "content": "recent user two"},
        {"role": "assistant", "content": "z" * 1200},
    ]

    window = MemoryExtractor._build_recent_window(turns)

    assert "[user] recent user one" in window
    assert "[user] recent user two" in window
    assert "z" * 900 not in window


@pytest.mark.asyncio
async def test_extract_retries_with_simplified_schema_on_parse_failure(store):
    extractor = MemoryExtractor(store)
    agent = MagicMock()
    agent.name = "claude"
    bad = MagicMock(text="not json", error=None)
    good = MagicMock(
        text='[{"summary": "User prefers concise answers", "category": "preference", "confidence": 0.9, "explicitness": "explicit", "evidence": "keep it short"}]',
        error=None,
    )
    registry = MagicMock()
    registry.run = AsyncMock(return_value=(agent, bad))
    registry.run.side_effect = [(agent, bad), (agent, good)]

    result = await extractor.extract(
        [
            {"role": "user", "content": "keep it short"},
            {"role": "assistant", "content": "ok"},
        ],
        registry,
        thread_id="t-retry",
    )

    assert len(result) == 1
    assert registry.run.await_count == 2


@pytest.mark.asyncio
async def test_extract_applies_context_defaults_for_scope_and_sources(store):
    extractor = MemoryExtractor(store)
    registry = _mock_registry(
        '[{"summary": "The project uses a strict changelog workflow", "category": "project_knowledge", "confidence": 0.82, "explicitness": "explicit", "evidence": "keep changelog updated"}]'
    )

    result = await extractor.extract(
        [
            {"role": "user", "content": "Please keep changelog updated for this repo"},
            {"role": "assistant", "content": "ok"},
        ],
        registry,
        thread_id="t-scope",
        skill_name="market-briefing",
        source_workspace="/repo/root",
        thread_topic="repo maintenance",
    )

    assert len(result) == 1
    assert result[0].scope == "workspace"
    assert result[0].durability == "long"
    assert result[0].source_skills == ["market-briefing"]
    assert result[0].source_workspace == "/repo/root"
