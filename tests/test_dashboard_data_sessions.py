"""Unit coverage for the read-only session data helpers.

The dashboard API uses these helpers instead of :class:`SQLiteMemoryStore`
to keep dashboard reads on a separate ``mode=ro`` connection — concurrent
with the bot's writer, no WAL contention, no accidental writes from a
buggy frontend request.

Tests construct a real on-disk SQLite database against the same schema
as :mod:`oh_my_agent.memory.store` (we import it to ensure schema parity).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from oh_my_agent.dashboard.data_sessions import (
    _SEARCH_SNIPPET_CHARS,
    _decode_cursor,
    _encode_cursor,
    fetch_session_history,
    fetch_session_list,
    search_sessions,
)
from oh_my_agent.memory.store import _SCHEMA


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Create a fresh SQLite DB with the production schema applied."""

    path = tmp_path / "memory.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()
    return path


def _insert_turn(
    db_path: Path,
    *,
    platform: str,
    channel_id: str,
    thread_id: str,
    role: str,
    content: str,
    author: str | None = None,
    agent: str | None = None,
    created_at: str | None = None,
) -> int:
    """Insert one row and return its rowid."""

    conn = sqlite3.connect(db_path)
    try:
        if created_at is None:
            cur = conn.execute(
                "INSERT INTO turns(platform, channel_id, thread_id, role, content, author, agent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (platform, channel_id, thread_id, role, content, author, agent),
            )
        else:
            cur = conn.execute(
                "INSERT INTO turns(platform, channel_id, thread_id, role, content, author, agent, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (platform, channel_id, thread_id, role, content, author, agent, created_at),
            )
        conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid
    finally:
        conn.close()


# ── Cursor codec round-trip ──────────────────────────────────────────── #


def test_cursor_round_trip() -> None:
    encoded = _encode_cursor("2026-05-12T10:00:00", "thread-abc")
    assert _decode_cursor(encoded) == ("2026-05-12T10:00:00", "thread-abc")


def test_cursor_decode_handles_empty_and_garbage() -> None:
    assert _decode_cursor(None) is None
    assert _decode_cursor("") is None
    assert _decode_cursor("not-base64!!!") is None
    # base64 of "no-pipe-here"
    no_pipe = _encode_cursor("foo", "bar")
    # truncate to break the structure
    assert _decode_cursor(no_pipe[:4]) is None


# ── fetch_session_list ─────────────────────────────────────────────── #


def test_session_list_returns_empty_on_empty_db(db_path: Path) -> None:
    result = fetch_session_list(db_path)
    assert result == {"items": [], "next_cursor": None}


def test_session_list_returns_missing_db_error(tmp_path: Path) -> None:
    result = fetch_session_list(tmp_path / "nonexistent.db")
    assert "error" in result
    assert "memory db unavailable" in result["error"]


def test_session_list_groups_by_thread(db_path: Path) -> None:
    _insert_turn(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="t1",
        role="user",
        content="hi",
        created_at="2026-05-12T10:00:00",
    )
    _insert_turn(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="t1",
        role="assistant",
        content="hello",
        created_at="2026-05-12T10:00:05",
    )
    _insert_turn(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="t2",
        role="user",
        content="another",
        created_at="2026-05-12T11:00:00",
    )

    result = fetch_session_list(db_path)
    items = result["items"]
    assert len(items) == 2
    # ORDER BY last_turn_at DESC — t2 wins.
    assert items[0]["thread_id"] == "t2"
    assert items[0]["turn_count"] == 1
    assert items[0]["last_role"] == "user"
    assert items[1]["thread_id"] == "t1"
    assert items[1]["turn_count"] == 2
    assert items[1]["last_role"] == "assistant"


def test_session_list_pagination_composite_cursor_stable_on_equal_timestamps(
    db_path: Path,
) -> None:
    """Two threads with identical MAX(created_at) must paginate stably
    (Codex round-1 NF3: composite cursor with thread_id tie-breaker)."""

    same_ts = "2026-05-12T10:00:00"
    _insert_turn(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="thread-a",
        role="user",
        content="a",
        created_at=same_ts,
    )
    _insert_turn(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="thread-b",
        role="user",
        content="b",
        created_at=same_ts,
    )
    _insert_turn(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="thread-c",
        role="user",
        content="c",
        created_at=same_ts,
    )

    # Page 1 — limit 2.
    page1 = fetch_session_list(db_path, limit=2)
    assert len(page1["items"]) == 2
    assert page1["next_cursor"] is not None
    page1_threads = [it["thread_id"] for it in page1["items"]]

    # Page 2 — should see the remaining 1 thread, no overlap or skips.
    page2 = fetch_session_list(db_path, limit=2, cursor=page1["next_cursor"])
    page2_threads = [it["thread_id"] for it in page2["items"]]
    assert page2["next_cursor"] is None  # last page

    union = set(page1_threads) | set(page2_threads)
    assert union == {"thread-a", "thread-b", "thread-c"}
    intersection = set(page1_threads) & set(page2_threads)
    assert intersection == set()


def test_session_list_limit_clamped(db_path: Path) -> None:
    """``limit > 200`` clamps to 200; ``limit < 1`` clamps to 1."""

    result_high = fetch_session_list(db_path, limit=99999)
    assert result_high == {"items": [], "next_cursor": None}  # no rows, but no crash
    result_low = fetch_session_list(db_path, limit=0)
    assert result_low == {"items": [], "next_cursor": None}


# ── fetch_session_history ──────────────────────────────────────────── #


def test_history_returns_empty_for_unknown_thread(db_path: Path) -> None:
    out = fetch_session_history(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="missing",
    )
    assert out == []


def test_history_ordered_oldest_first(db_path: Path) -> None:
    _insert_turn(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="t1",
        role="user",
        content="msg1",
        created_at="2026-05-12T10:00:00",
    )
    _insert_turn(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="t1",
        role="assistant",
        content="msg2",
        agent="claude",
        created_at="2026-05-12T10:00:05",
    )
    _insert_turn(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="t1",
        role="user",
        content="msg3",
        created_at="2026-05-12T10:00:10",
    )

    history = fetch_session_history(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="t1",
    )
    assert isinstance(history, list)
    assert [r["content"] for r in history] == ["msg1", "msg2", "msg3"]
    assert history[1]["agent"] == "claude"


def test_history_before_id_pagination(db_path: Path) -> None:
    ids: list[int] = []
    for i in range(5):
        ids.append(
            _insert_turn(
                db_path,
                platform="discord",
                channel_id="100",
                thread_id="t1",
                role="user",
                content=f"msg-{i}",
                created_at=f"2026-05-12T10:0{i}:00",
            )
        )

    # Default page: last 200 (all 5).
    full = fetch_session_history(db_path, platform="discord", channel_id="100", thread_id="t1")
    assert isinstance(full, list)
    assert len(full) == 5

    # Limit 2: should be the LAST 2 (most recent) ordered oldest-first
    # (rows reversed client-side).
    page = fetch_session_history(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="t1",
        limit=2,
    )
    assert isinstance(page, list)
    assert [r["content"] for r in page] == ["msg-3", "msg-4"]

    # Backward page using before_id = first row of previous page.
    older = fetch_session_history(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="t1",
        limit=2,
        before_id=page[0]["_id"],
    )
    assert isinstance(older, list)
    assert [r["content"] for r in older] == ["msg-1", "msg-2"]


def test_history_missing_db_returns_error(tmp_path: Path) -> None:
    out = fetch_session_history(
        tmp_path / "missing.db",
        platform="discord",
        channel_id="100",
        thread_id="t1",
    )
    assert isinstance(out, dict)
    assert "error" in out


# ── search_sessions (FTS5) ───────────────────────────────────────────── #


def test_search_empty_query_returns_no_items(db_path: Path) -> None:
    assert search_sessions(db_path, query="") == {"items": [], "query": ""}
    assert search_sessions(db_path, query="   ") == {"items": [], "query": ""}


def test_search_matches_content_with_thread_coords(db_path: Path) -> None:
    _insert_turn(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="t1",
        role="user",
        content="please summarize the bilibili video",
    )
    _insert_turn(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="t2",
        role="assistant",
        content="unrelated content here",
        agent="claude",
    )

    res = search_sessions(db_path, query="bilibili")
    assert res["query"] == "bilibili"
    assert len(res["items"]) == 1
    hit = res["items"][0]
    assert hit["thread_id"] == "t1"
    assert hit["platform"] == "discord"
    assert hit["channel_id"] == "100"
    assert "bilibili" in hit["snippet"]
    assert hit["role"] == "user"


def test_search_special_chars_do_not_raise(db_path: Path) -> None:
    _insert_turn(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="t1",
        role="user",
        content="check the a-b:c* path and AND OR NEAR operators",
    )
    # These would all be FTS5 syntax errors if passed raw to MATCH. The
    # quoted-phrase wrapping must keep them as plain text → no error key.
    for q in ['a-b:c*', 'AND OR NEAR', 'quote " inside', "trailing*"]:
        res = search_sessions(db_path, query=q)
        assert "error" not in res, f"{q!r} leaked an FTS5 error"


def test_search_snippet_is_truncated(db_path: Path) -> None:
    long_body = "needle " + ("x" * 1000)
    _insert_turn(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="t1",
        role="assistant",
        content=long_body,
        agent="claude",
    )
    res = search_sessions(db_path, query="needle")
    assert len(res["items"]) == 1
    assert len(res["items"][0]["snippet"]) <= _SEARCH_SNIPPET_CHARS


def test_search_limit_clamped(db_path: Path) -> None:
    for i in range(5):
        _insert_turn(
            db_path,
            platform="discord",
            channel_id="100",
            thread_id=f"t{i}",
            role="user",
            content=f"shared keyword turn {i}",
        )
    res = search_sessions(db_path, query="keyword", limit=999)
    assert len(res["items"]) == 5  # clamp doesn't error; all 5 returned


def test_search_nul_byte_is_stripped_not_errored(db_path: Path) -> None:
    _insert_turn(
        db_path,
        platform="discord",
        channel_id="100",
        thread_id="t1",
        role="user",
        content="needle in the haystack",
    )
    # A NUL survives quote-doubling and makes FTS5 raise "unterminated
    # string" → spurious 503. It must be stripped, not error.
    res = search_sessions(db_path, query="need\x00le")
    assert "error" not in res
    assert "\x00" not in res["query"]
    assert len(res["items"]) == 1


def test_search_only_nul_is_treated_as_empty(db_path: Path) -> None:
    assert search_sessions(db_path, query="\x00\x00") == {"items": [], "query": ""}


def test_search_missing_db_returns_error(tmp_path: Path) -> None:
    res = search_sessions(tmp_path / "missing.db", query="anything")
    assert "error" in res
