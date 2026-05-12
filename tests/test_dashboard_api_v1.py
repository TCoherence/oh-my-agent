"""End-to-end coverage for the v1 dashboard JSON API.

Uses FastAPI's TestClient to exercise the actual ASGI app instead of
just unit-testing the data helpers. This catches integration issues
(route prefix, auth whitelist, error → status code mapping, etc.) that
unit tests miss.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oh_my_agent.dashboard.app import create_app
from oh_my_agent.memory.store import _SCHEMA


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _seed_turn(
    db_path: Path,
    *,
    platform: str = "discord",
    channel_id: str = "100",
    thread_id: str = "t1",
    role: str = "user",
    content: str = "hello",
    agent: str | None = None,
    created_at: str | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        if created_at is None:
            conn.execute(
                "INSERT INTO turns(platform, channel_id, thread_id, role, content, agent) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (platform, channel_id, thread_id, role, content, agent),
            )
        else:
            conn.execute(
                "INSERT INTO turns(platform, channel_id, thread_id, role, content, agent, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (platform, channel_id, thread_id, role, content, agent, created_at),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def app_and_db(tmp_path: Path):
    """Build a FastAPI app pointed at a fresh tmp memory.db."""

    db = tmp_path / "memory.db"
    _make_db(db)
    config = {
        "memory": {
            "backend": "sqlite",
            "path": str(db),
        },
        # Don't enable experiment.tool_trace so the trace endpoint can
        # exercise its "disabled" branch.
        "experiment": {"tool_trace": {"enabled": False}},
    }
    app = create_app(config)
    return app, db


# ── /healthz endpoints ──────────────────────────────────────────────── #


def test_healthz_top_level_public(app_and_db) -> None:
    app, _ = app_and_db
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_healthz_api_v1_public(app_and_db) -> None:
    app, _ = app_and_db
    client = TestClient(app)
    r = client.get("/api/v1/healthz")
    assert r.status_code == 200


def test_api_v1_healthz_skips_auth(tmp_path: Path) -> None:
    """When the app is constructed with a bearer token, /api/v1/healthz
    still returns 200 without it (auth whitelist)."""

    db = tmp_path / "memory.db"
    _make_db(db)
    config = {"memory": {"backend": "sqlite", "path": str(db)}}
    app = create_app(config, auth_token="secret-token")
    client = TestClient(app)

    # /api/v1/healthz public.
    r_health = client.get("/api/v1/healthz")
    assert r_health.status_code == 200

    # /api/v1/sessions requires auth.
    r_sessions_no_auth = client.get("/api/v1/sessions")
    assert r_sessions_no_auth.status_code == 401

    # /api/v1/sessions with auth.
    r_sessions_auth = client.get(
        "/api/v1/sessions",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert r_sessions_auth.status_code == 200


# ── /api/v1/sessions ───────────────────────────────────────────────── #


def test_sessions_list_empty(app_and_db) -> None:
    app, _ = app_and_db
    client = TestClient(app)
    r = client.get("/api/v1/sessions")
    assert r.status_code == 200
    body = r.json()
    assert body == {"items": [], "next_cursor": None}


def test_sessions_list_returns_threads(app_and_db) -> None:
    app, db = app_and_db
    _seed_turn(db, thread_id="t1", role="user", created_at="2026-05-12T10:00:00")
    _seed_turn(db, thread_id="t1", role="assistant", agent="claude", created_at="2026-05-12T10:00:05")
    _seed_turn(db, thread_id="t2", role="user", created_at="2026-05-12T11:00:00")

    client = TestClient(app)
    r = client.get("/api/v1/sessions")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    # Most recent thread first.
    assert items[0]["thread_id"] == "t2"
    assert items[1]["thread_id"] == "t1"
    assert items[1]["turn_count"] == 2
    assert items[1]["last_role"] == "assistant"


def test_sessions_list_503_on_missing_db(tmp_path: Path) -> None:
    """First-run scenario: dashboard up before bot has created memory.db.
    Should be 503 (service-degraded) not 500 (code-crash)."""

    config = {
        "memory": {
            "backend": "sqlite",
            "path": str(tmp_path / "nonexistent.db"),
        },
    }
    app = create_app(config)
    client = TestClient(app)
    r = client.get("/api/v1/sessions")
    assert r.status_code == 503


# ── /api/v1/sessions/{p}/{ch}/{tid}/history ───────────────────────── #


def test_history_returns_turns(app_and_db) -> None:
    app, db = app_and_db
    _seed_turn(db, thread_id="t1", role="user", content="msg1", created_at="2026-05-12T10:00:00")
    _seed_turn(db, thread_id="t1", role="assistant", content="msg2", agent="claude", created_at="2026-05-12T10:00:05")
    _seed_turn(db, thread_id="t1", role="user", content="msg3", created_at="2026-05-12T10:00:10")

    client = TestClient(app)
    r = client.get("/api/v1/sessions/discord/100/t1/history")
    assert r.status_code == 200
    rows = r.json()
    assert [row["content"] for row in rows] == ["msg1", "msg2", "msg3"]


def test_history_empty_for_unknown_thread(app_and_db) -> None:
    app, _ = app_and_db
    client = TestClient(app)
    r = client.get("/api/v1/sessions/discord/100/missing/history")
    assert r.status_code == 200
    assert r.json() == []


def test_history_respects_limit_and_before_id(app_and_db) -> None:
    app, db = app_and_db
    for i in range(5):
        _seed_turn(db, thread_id="t1", content=f"msg-{i}", created_at=f"2026-05-12T10:0{i}:00")

    client = TestClient(app)
    page1 = client.get("/api/v1/sessions/discord/100/t1/history?limit=2").json()
    assert [r["content"] for r in page1] == ["msg-3", "msg-4"]

    older = client.get(
        f"/api/v1/sessions/discord/100/t1/history?limit=2&before_id={page1[0]['_id']}"
    ).json()
    assert [r["content"] for r in older] == ["msg-1", "msg-2"]


# ── /api/v1/sessions/{p}/{ch}/{tid}/trace ─────────────────────────── #


def test_trace_disabled_returns_empty_with_flag(app_and_db) -> None:
    """When experiment.tool_trace is disabled, the trace endpoint
    succeeds with empty items + enabled=false so the frontend can
    render a "trace disabled — enable in config" state."""

    app, _ = app_and_db
    client = TestClient(app)
    r = client.get("/api/v1/sessions/discord/100/t1/trace?date=2026-05-12")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["enabled"] is False
    assert body["date"] == "2026-05-12"


def test_trace_invalid_date_400(tmp_path: Path) -> None:
    db = tmp_path / "memory.db"
    _make_db(db)
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    config = {
        "memory": {"backend": "sqlite", "path": str(db)},
        "experiment": {"tool_trace": {"enabled": True, "path": str(trace_dir)}},
    }
    app = create_app(config)
    client = TestClient(app)
    r = client.get("/api/v1/sessions/discord/100/t1/trace?date=not-a-date")
    assert r.status_code == 400


def test_trace_enabled_reads_events(tmp_path: Path) -> None:
    db = tmp_path / "memory.db"
    _make_db(db)
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    (trace_dir / "2026-05-12.jsonl").write_text(
        json.dumps({"type": "tool_use", "thread_id": "t1", "name": "Read"}) + "\n",
        encoding="utf-8",
    )
    config = {
        "memory": {"backend": "sqlite", "path": str(db)},
        "experiment": {"tool_trace": {"enabled": True, "path": str(trace_dir)}},
    }
    app = create_app(config)
    client = TestClient(app)
    r = client.get("/api/v1/sessions/discord/100/t1/trace?date=2026-05-12")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "Read"


def test_trace_date_param_required(app_and_db) -> None:
    """Missing ``date`` must 422 (FastAPI validation), not silently
    scan all days (Codex round-1 catch)."""

    app, _ = app_and_db
    client = TestClient(app)
    r = client.get("/api/v1/sessions/discord/100/t1/trace")
    assert r.status_code == 422


# ── Legacy / regression ───────────────────────────────────────────── #


def test_legacy_root_jinja_still_works(app_and_db) -> None:
    """The Jinja monitoring page at ``/`` survives the API addition."""

    app, _ = app_and_db
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
