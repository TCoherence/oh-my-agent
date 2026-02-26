import pytest
from unittest.mock import AsyncMock, MagicMock
from oh_my_agent.memory.compressor import HistoryCompressor
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.agents.base import AgentResponse


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

    result = await compressor.maybe_compress("d", "c", "t1", registry)
    assert result is True

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
