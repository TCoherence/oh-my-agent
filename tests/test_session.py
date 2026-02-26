import pytest
from unittest.mock import MagicMock


def _make_session():
    from oh_my_agent.gateway.session import ChannelSession
    return ChannelSession(
        platform="discord",
        channel_id="123",
        channel=MagicMock(),
        registry=MagicMock(),
    )


@pytest.mark.asyncio
async def test_get_history_returns_empty_list_for_new_thread():
    s = _make_session()
    assert await s.get_history("t1") == []


@pytest.mark.asyncio
async def test_get_history_creates_cache_entry_on_first_access():
    s = _make_session()
    await s.get_history("t1")
    assert "t1" in s._cache


@pytest.mark.asyncio
async def test_append_user_adds_correct_turn():
    s = _make_session()
    await s.append_user("t1", "hello", "alice")
    history = await s.get_history("t1")
    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "hello"
    assert history[0]["author"] == "alice"


@pytest.mark.asyncio
async def test_append_assistant_adds_correct_turn():
    s = _make_session()
    await s.append_assistant("t1", "world", "claude")
    history = await s.get_history("t1")
    assert len(history) == 1
    assert history[0]["role"] == "assistant"
    assert history[0]["content"] == "world"
    assert history[0]["agent"] == "claude"


@pytest.mark.asyncio
async def test_threads_are_independent():
    s = _make_session()
    await s.append_user("t1", "msg-1", "alice")
    await s.append_user("t2", "msg-2", "bob")
    assert len(await s.get_history("t1")) == 1
    assert len(await s.get_history("t2")) == 1
    h1 = await s.get_history("t1")
    h2 = await s.get_history("t2")
    assert h1[0]["content"] == "msg-1"
    assert h2[0]["content"] == "msg-2"


@pytest.mark.asyncio
async def test_multiple_turns_accumulate_in_order():
    s = _make_session()
    await s.append_user("t1", "question", "alice")
    await s.append_assistant("t1", "answer", "claude")
    await s.append_user("t1", "follow-up", "alice")
    history = await s.get_history("t1")
    assert len(history) == 3
    assert [h["role"] for h in history] == ["user", "assistant", "user"]


@pytest.mark.asyncio
async def test_clear_history():
    s = _make_session()
    await s.append_user("t1", "hello", "alice")
    assert len(await s.get_history("t1")) == 1
    await s.clear_history("t1")
    assert await s.get_history("t1") == []
