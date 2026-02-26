import pytest
from unittest.mock import MagicMock
from oh_my_agent.gateway.session import ChannelSession


def _make_session() -> ChannelSession:
    return ChannelSession(
        platform="discord",
        channel_id="123",
        channel=MagicMock(),
        registry=MagicMock(),
    )


def test_get_history_returns_empty_list_for_new_thread():
    s = _make_session()
    assert s.get_history("t1") == []


def test_get_history_creates_entry_on_first_access():
    s = _make_session()
    s.get_history("t1")
    assert "t1" in s.histories


def test_append_user_adds_correct_turn():
    s = _make_session()
    s.append_user("t1", "hello", "alice")
    history = s.get_history("t1")
    assert len(history) == 1
    assert history[0] == {"role": "user", "content": "hello", "author": "alice"}


def test_append_assistant_adds_correct_turn():
    s = _make_session()
    s.append_assistant("t1", "world", "claude")
    history = s.get_history("t1")
    assert len(history) == 1
    assert history[0] == {"role": "assistant", "content": "world", "agent": "claude"}


def test_threads_are_independent():
    s = _make_session()
    s.append_user("t1", "msg-1", "alice")
    s.append_user("t2", "msg-2", "bob")
    assert len(s.get_history("t1")) == 1
    assert len(s.get_history("t2")) == 1
    assert s.get_history("t1")[0]["content"] == "msg-1"
    assert s.get_history("t2")[0]["content"] == "msg-2"


def test_multiple_turns_accumulate_in_order():
    s = _make_session()
    s.append_user("t1", "question", "alice")
    s.append_assistant("t1", "answer", "claude")
    s.append_user("t1", "follow-up", "alice")
    history = s.get_history("t1")
    assert len(history) == 3
    assert [h["role"] for h in history] == ["user", "assistant", "user"]
