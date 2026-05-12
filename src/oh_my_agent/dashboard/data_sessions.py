"""Read-only session data access for the dashboard's v1 API.

Mirrors the read-only-SQLite pattern from :mod:`oh_my_agent.dashboard.data`
(``sqlite3.connect("file:...?mode=ro", uri=True)``) so concurrent reads
never contend with the bot's writer. **Intentionally does NOT reuse
:class:`SQLiteMemoryStore`** — that store opens a normal RW connection
and sets WAL pragmas, which is wrong for a read-only API.

Public surface:

- :func:`fetch_session_list`: paginated list of (platform, channel_id,
  thread_id) groups with turn counts and last-activity timestamps.
  Composite cursor ``(last_turn_at, thread_id)`` makes pagination
  stable when multiple threads share the same ``MAX(created_at)``.
- :func:`fetch_session_history`: turns for a single thread, ordered
  oldest-first, with optional ``before_id`` for backward scrolling.

Both functions return ``{"error": str}`` shaped dicts on failure to match
the existing dashboard pattern. The caller (``api/v1.py``) translates
them into appropriate HTTP responses.
"""

from __future__ import annotations

import base64
import sqlite3
from pathlib import Path
from typing import Any


def _ro_connect(db_path: Path) -> sqlite3.Connection:
    """Open SQLite in read-only URI mode. Mirrors ``data._ro_connect``."""

    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------- #
# Cursor encoding                                                             #
# --------------------------------------------------------------------------- #


def _encode_cursor(last_turn_at: str, thread_id: str) -> str:
    """Encode ``(last_turn_at, thread_id)`` composite cursor as base64.

    ``thread_id`` is part of the cursor as a tie-breaker — multiple threads
    can share an exact ``MAX(created_at)`` (especially at small data
    volumes), and a timestamp-only cursor would either skip or repeat
    rows across pages.
    """

    payload = f"{last_turn_at}|{thread_id}".encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[str, str] | None:
    """Reverse of :func:`_encode_cursor`. Returns ``None`` on invalid input.

    Defensive against malformed cursors (caller can present anything) —
    returns ``None`` for any decode failure, and the SQL handles ``NULL``
    cursor as "first page".
    """

    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if "|" not in raw:
        return None
    ts, _, thread = raw.partition("|")
    if not ts or not thread:
        return None
    return ts, thread


# --------------------------------------------------------------------------- #
# Session list                                                                #
# --------------------------------------------------------------------------- #


def fetch_session_list(
    db_path: Path,
    *,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Return one page of session rows ordered by last-activity DESC.

    Each row aggregates the ``turns`` table by ``(platform, channel_id,
    thread_id)`` and includes:
    - ``turn_count`` (COUNT)
    - ``last_turn_at`` (MAX(created_at), ISO string)
    - ``last_role`` (the role of the most-recent turn — user / assistant /
      system; tells the UI whether the user is waiting or the agent is)

    Returns ``{"items": [...], "next_cursor": str | None}``. When
    ``items`` is shorter than ``limit``, ``next_cursor`` is ``None``.
    """

    limit = max(1, min(int(limit), 200))
    cursor_pair = _decode_cursor(cursor)
    cursor_ts: str | None = None
    cursor_thread: str | None = None
    if cursor_pair is not None:
        cursor_ts, cursor_thread = cursor_pair

    try:
        conn = _ro_connect(db_path)
    except sqlite3.OperationalError as exc:
        return {"error": f"memory db unavailable: {type(exc).__name__}: {exc}"}

    # HAVING (not WHERE) because we filter on the post-aggregation
    # ``last_turn_at``. Composite cursor (last_turn_at, thread_id) — same
    # ts allowed across threads, so we tie-break on thread_id DESC for a
    # stable pagination order matching the ORDER BY.
    sql = """
        SELECT t1.platform, t1.channel_id, t1.thread_id,
               COUNT(*) AS turn_count,
               MAX(t1.created_at) AS last_turn_at,
               (SELECT role FROM turns t2
                WHERE t2.platform = t1.platform
                  AND t2.channel_id = t1.channel_id
                  AND t2.thread_id = t1.thread_id
                ORDER BY t2.id DESC
                LIMIT 1) AS last_role
        FROM turns AS t1
        GROUP BY t1.platform, t1.channel_id, t1.thread_id
        HAVING (:cursor_ts IS NULL
                OR MAX(t1.created_at) < :cursor_ts
                OR (MAX(t1.created_at) = :cursor_ts
                    AND t1.thread_id < :cursor_thread))
        ORDER BY last_turn_at DESC, t1.thread_id DESC
        LIMIT :limit
    """

    try:
        rows = conn.execute(
            sql,
            {
                "cursor_ts": cursor_ts,
                "cursor_thread": cursor_thread,
                "limit": limit + 1,  # over-fetch by 1 to detect "has more"
            },
        ).fetchall()
    except sqlite3.OperationalError as exc:
        return {"error": f"session list query failed: {type(exc).__name__}: {exc}"}
    finally:
        conn.close()

    items: list[dict[str, Any]] = []
    for row in rows[:limit]:
        items.append(
            {
                "platform": row["platform"],
                "channel_id": row["channel_id"],
                "thread_id": row["thread_id"],
                "turn_count": int(row["turn_count"]),
                "last_turn_at": row["last_turn_at"],
                "last_role": row["last_role"],
            }
        )

    next_cursor: str | None = None
    if len(rows) > limit and items:
        last = items[-1]
        next_cursor = _encode_cursor(last["last_turn_at"], last["thread_id"])

    return {"items": items, "next_cursor": next_cursor}


# --------------------------------------------------------------------------- #
# Session history                                                             #
# --------------------------------------------------------------------------- #


def fetch_session_history(
    db_path: Path,
    *,
    platform: str,
    channel_id: str,
    thread_id: str,
    limit: int = 200,
    before_id: int | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Return one thread's turns, oldest-first by default.

    ``before_id`` enables backward pagination (load older turns when the
    user scrolls up) — we fetch the page with ``id < before_id`` then
    reverse it so the caller still gets oldest-first within the page.

    Each returned row: ``{role, content, author?, agent?, _id, created_at}``.

    Returns ``{"error": str}`` shape on db / sql failure.
    """

    limit = max(1, min(int(limit), 500))

    try:
        conn = _ro_connect(db_path)
    except sqlite3.OperationalError as exc:
        return {"error": f"memory db unavailable: {type(exc).__name__}: {exc}"}

    # Strategy:
    # - default (no before_id): newest page → ORDER BY id DESC LIMIT N,
    #   then reverse client-side so output is oldest-first
    # - before_id given: same query with extra ``id < before_id`` clause
    #   for backward scrolling
    # Reversing keeps the chat-UI rendering pattern simple: append new
    # rows from the end, prepend older pages to the top.
    where_clause = "platform = ? AND channel_id = ? AND thread_id = ?"
    params: list[Any] = [platform, channel_id, thread_id]
    if before_id is not None:
        where_clause += " AND id < ?"
        params.append(int(before_id))
    params.append(limit)

    sql = f"""
        SELECT id, role, content, author, agent, created_at
        FROM turns
        WHERE {where_clause}
        ORDER BY id DESC
        LIMIT ?
    """

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        return {"error": f"history query failed: {type(exc).__name__}: {exc}"}
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    # Reverse to oldest-first (we queried DESC for LIMIT efficiency).
    for row in reversed(rows):
        out.append(
            {
                "_id": int(row["id"]),
                "role": row["role"],
                "content": row["content"],
                "author": row["author"],
                "agent": row["agent"],
                "created_at": row["created_at"],
            }
        )
    return out
