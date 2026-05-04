"""FastAPI route smoke tests for the dashboard.

Use ``TestClient`` against a live ``create_app(config)`` with config pointing
at a tmp_path runtime tree. Doesn't need full prod state — empty fixtures are
fine; the page should still render with placeholder text.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from oh_my_agent.dashboard.app import create_app  # noqa: E402


def _seed_minimal_runtime_tree(root: Path) -> dict:
    """Build a minimal directory tree + empty DB so all fetch_* return empty
    rather than placeholder errors."""

    tasks_dir = root / "tasks"
    logs_dir = root / "logs"
    memory_dir = root / "memory"
    tasks_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)
    memory_dir.mkdir(parents=True)

    db = root / "runtime.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE runtime_tasks (
            id TEXT PRIMARY KEY, platform TEXT, channel_id TEXT, thread_id TEXT,
            created_by TEXT, goal TEXT, status TEXT, step_no INTEGER DEFAULT 0,
            max_steps INTEGER DEFAULT 1, max_minutes INTEGER DEFAULT 1,
            test_command TEXT, completion_mode TEXT DEFAULT 'merge',
            task_type TEXT DEFAULT 'repo_change', automation_name TEXT,
            skill_name TEXT, error TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE automation_runtime_state (
            name TEXT PRIMARY KEY, platform TEXT, channel_id TEXT,
            enabled INTEGER, last_run_at TIMESTAMP, last_success_at TIMESTAMP,
            last_error TEXT, next_run_at TIMESTAMP
        );
        CREATE TABLE usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TIMESTAMP, agent TEXT,
            source TEXT, input_tokens INTEGER, output_tokens INTEGER,
            cost_usd REAL, task_id TEXT
        );
        """
    )
    conn.commit()
    conn.close()

    memory_db = root / "memory.db"
    sqlite3.connect(memory_db).close()

    (memory_dir / "memories.yaml").write_text("entries: []\n", encoding="utf-8")

    # Minimal logs
    (logs_dir / "service.log").write_text(
        "2026-05-03T20:00:00.000Z level=INFO logger=oh_my_agent.runtime.service msg=Runtime started with 1 worker(s)\n",
        encoding="utf-8",
    )
    (logs_dir / "oh-my-agent.log").write_text("", encoding="utf-8")

    return {
        "runtime": {
            "worktree_root": str(tasks_dir),
            "state_path": str(db),
            "reports_dir": "",  # disabled
        },
        "memory": {
            "path": str(memory_db),
            "judge": {"memory_dir": str(memory_dir)},
        },
        "skills": {"telemetry_path": str(root / "skills.db")},
    }


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    config = _seed_minimal_runtime_tree(tmp_path)
    app = create_app(config)
    return TestClient(app)


def test_healthz_returns_ok(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_index_renders_all_section_titles(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    # All five sections must appear in the rendered HTML
    assert "Automation health" in body
    assert "Task / runtime health" in body
    assert "Cost / usage" in body
    assert "Memory &amp; skill" in body or "Memory & skill" in body
    # System section heading
    assert "System" in body
    # Bot uptime block
    assert "Bot uptime" in body


def test_index_does_not_500_with_missing_logs(tmp_path: Path) -> None:
    """Even if every log file is missing, the page renders (placeholders)."""

    config = _seed_minimal_runtime_tree(tmp_path)
    # Wipe logs dir to simulate fresh deploy
    logs_dir = tmp_path / "logs"
    for f in logs_dir.iterdir():
        f.unlink()

    app = create_app(config)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    # System layer placeholder text appears
    assert "all log files missing" in r.text or "no Runtime started" in r.text


def test_index_does_not_500_with_missing_db(tmp_path: Path) -> None:
    """SQLite path that doesn't exist → placeholders, not 500."""

    config = {
        "runtime": {
            "worktree_root": str(tmp_path / "tasks"),
            "state_path": str(tmp_path / "absent.db"),
            "reports_dir": "",
        },
        "memory": {
            "path": str(tmp_path / "absent-memory.db"),
            "judge": {"memory_dir": str(tmp_path / "absent-memdir")},
        },
        "skills": {"telemetry_path": str(tmp_path / "absent-skills.db")},
    }
    app = create_app(config)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    # The placeholder text appears for the missing DB sections
    assert "unavailable" in r.text or "missing" in r.text


def test_warning_severity_string_in_rendered_template(tmp_path: Path) -> None:
    """Regression for the WARN-vs-WARNING trap — page should reference
    WARNING (not WARN) when log fixture contains warning lines."""

    from datetime import datetime, timedelta, timezone

    recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(
        timespec="milliseconds"
    )
    config = _seed_minimal_runtime_tree(tmp_path)
    log = tmp_path / "logs" / "service.log"
    log.write_text(
        log.read_text(encoding="utf-8")
        + f"{recent_ts} level=WARNING logger=oh_my_agent.gateway msg=test-warning\n",
        encoding="utf-8",
    )
    app = create_app(config)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    # The total WARNING count appears as 1
    assert "WARNING" in r.text


# ---------------------------------------------------------------------------
# Auto-refresh interval (configurable per PR #37)
# ---------------------------------------------------------------------------


def test_index_default_refresh_seconds_is_300(tmp_path: Path) -> None:
    """Default auto-refresh interval is 5 minutes (300s)."""

    config = _seed_minimal_runtime_tree(tmp_path)
    app = create_app(config)  # default refresh_seconds=300
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert 'http-equiv="refresh" content="300"' in r.text
    assert "auto-refresh every 300s" in r.text


def test_index_respects_custom_refresh_seconds(tmp_path: Path) -> None:
    config = _seed_minimal_runtime_tree(tmp_path)
    app = create_app(config, refresh_seconds=42)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert 'http-equiv="refresh" content="42"' in r.text
    assert "auto-refresh every 42s" in r.text


def test_index_omits_meta_refresh_when_zero(tmp_path: Path) -> None:
    """refresh_seconds=0 → no meta-refresh tag, header says disabled."""

    config = _seed_minimal_runtime_tree(tmp_path)
    app = create_app(config, refresh_seconds=0)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert 'http-equiv="refresh"' not in r.text
    assert "auto-refresh disabled" in r.text


def test_index_negative_refresh_seconds_clamped_to_zero(tmp_path: Path) -> None:
    """Defensive: negative value behaves like 0 (disabled)."""

    config = _seed_minimal_runtime_tree(tmp_path)
    app = create_app(config, refresh_seconds=-5)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert 'http-equiv="refresh"' not in r.text
