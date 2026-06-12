import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.memory.compressor import _MAX_CONVERSATION_CHARS, HistoryCompressor
from oh_my_agent.memory.store import SQLiteMemoryStore


@pytest.fixture
async def store(tmp_path):
    s = SQLiteMemoryStore(tmp_path / "test.db")
    await s.init()
    yield s
    await s.close()


def _mock_registry(summary_text="Summary of conversation"):
    mock_agent = MagicMock()
    mock_agent.name = "gemini"
    registry = MagicMock()
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text=summary_text)))
    return registry


@pytest.mark.asyncio
async def test_no_compression_below_threshold(store):
    compressor = HistoryCompressor(store, max_turns=5)
    registry = _mock_registry()

    for i in range(3):
        await store.append("d", "c", "t1", {"role": "user", "content": f"msg-{i}"})

    result = await compressor.maybe_compress("d", "c", "t1", registry)
    assert result is False
    registry.run.assert_not_called()


@pytest.mark.asyncio
async def test_compression_triggered_above_threshold(store):
    compressor = HistoryCompressor(store, max_turns=5)
    registry = _mock_registry("Summarised old messages")

    for i in range(8):
        await store.append("d", "c", "t1", {"role": "user", "content": f"msg-{i}"})

    result = await compressor.maybe_compress("d", "c", "t1", registry, req_id="req123")
    assert result is True
    assert registry.run.call_args.kwargs["run_label"] == "history_compress req=req123 thread=t1"

    # Should now have 5 raw turns + summary accessible
    history = await store.load_history("d", "c", "t1")
    assert history[0]["role"] == "system"
    assert "Summarised" in history[0]["content"]
    # 5 remaining turns
    raw_turns = [h for h in history if h["role"] != "system"]
    assert len(raw_turns) == 5


@pytest.mark.asyncio
async def test_compression_fallback_on_agent_failure(store):
    compressor = HistoryCompressor(store, max_turns=3)

    # Agent fails
    mock_agent = MagicMock()
    mock_agent.name = "claude"
    registry = MagicMock()
    registry.run = AsyncMock(return_value=(mock_agent, AgentResponse(text="", error="quota exceeded")))

    for i in range(6):
        await store.append("d", "c", "t1", {"role": "user", "content": f"msg-{i}"})

    result = await compressor.maybe_compress("d", "c", "t1", registry)
    assert result is True

    # Falls back to truncation — old turns still removed
    history = await store.load_history("d", "c", "t1")
    assert history[0]["role"] == "system"
    assert "truncated" in history[0]["content"].lower()


@pytest.mark.asyncio
async def test_recompression_includes_previous_summary(store):
    """Each new summary must be cumulative: the prior summary turn is fed
    back into the summarization prompt, not silently discarded."""
    compressor = HistoryCompressor(store, max_turns=5)
    registry = _mock_registry("First summary: user likes apples")

    for i in range(8):
        await store.append("d", "c", "t1", {"role": "user", "content": f"msg-{i}"})
    assert await compressor.maybe_compress("d", "c", "t1", registry) is True

    registry2 = _mock_registry("Second summary")
    for i in range(8, 16):
        await store.append("d", "c", "t1", {"role": "user", "content": f"msg-{i}"})
    assert await compressor.maybe_compress("d", "c", "t1", registry2) is True

    second_prompt = registry2.run.call_args.args[0]
    assert "First summary: user likes apples" in second_prompt


@pytest.mark.asyncio
async def test_concurrent_compress_runs_once(store):
    """Concurrent maybe_compress calls for the same thread serialize; the
    second sees the post-compression count and skips."""
    compressor = HistoryCompressor(store, max_turns=5)
    registry = _mock_registry("Single summary")

    for i in range(8):
        await store.append("d", "c", "t1", {"role": "user", "content": f"msg-{i}"})

    results = await asyncio.gather(
        compressor.maybe_compress("d", "c", "t1", registry),
        compressor.maybe_compress("d", "c", "t1", registry),
    )
    assert sorted(results) == [False, True]
    assert registry.run.call_count == 1

    # Exactly one summary turn in the loaded history.
    history = await store.load_history("d", "c", "t1")
    summary_turns = [h for h in history if "_id" not in h]
    assert len(summary_turns) == 1


@pytest.mark.asyncio
async def test_conversation_text_is_capped(store):
    """The summariser prompt is bounded: oldest turns are dropped first and
    the omission is noted in the prompt."""
    compressor = HistoryCompressor(store, max_turns=5)
    registry = _mock_registry()

    big = "x" * 2000
    for i in range(30):
        await store.append("d", "c", "t1", {"role": "user", "content": f"msg-{i} {big}"})

    assert await compressor.maybe_compress("d", "c", "t1", registry) is True
    prompt = registry.run.call_args.args[0]
    # Budget plus the prompt template / truncation-note overhead.
    assert len(prompt) < _MAX_CONVERSATION_CHARS + 2000
    assert "omitted" in prompt
    # The newest of the compressed turns is kept (oldest-first truncation).
    assert "msg-24" in prompt
    assert "msg-0 " not in prompt
