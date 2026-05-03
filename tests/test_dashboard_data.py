"""Tests for ``oh_my_agent.dashboard.data``.

Each ``fetch_*`` function gets explicit fixture data + assertions on the
aggregated output. Log parsing covers the WARNING-vs-WARN trap, two-file
merge, missing files, and traceback-line graceful skip.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# Skip all tests in this module if FastAPI deps aren't installed.
# (Dashboard is opt-in via [project.optional-dependencies] dashboard.)
pytest.importorskip("fastapi")

from oh_my_agent.dashboard import data  # noqa: E402

# ---------------------------------------------------------------------------
# Schema setup helpers
# ---------------------------------------------------------------------------


_RUNTIME_TASKS_DDL = """
CREATE TABLE runtime_tasks (
    id                  TEXT PRIMARY KEY,
    platform            TEXT NOT NULL,
    channel_id          TEXT NOT NULL,
    thread_id           TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    goal                TEXT NOT NULL,
    status              TEXT NOT NULL,
    step_no             INTEGER NOT NULL DEFAULT 0,
    max_steps           INTEGER NOT NULL DEFAULT 1,
    max_minutes         INTEGER NOT NULL DEFAULT 1,
    test_command        TEXT NOT NULL DEFAULT 'true',
    completion_mode     TEXT NOT NULL DEFAULT 'merge',
    task_type           TEXT NOT NULL DEFAULT 'repo_change',
    automation_name     TEXT,
    skill_name          TEXT,
    error               TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at          TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at            TIMESTAMP
);
"""

_AUTOMATION_STATE_DDL = """
CREATE TABLE automation_runtime_state (
    name             TEXT PRIMARY KEY,
    platform         TEXT NOT NULL,
    channel_id       TEXT NOT NULL,
    enabled          INTEGER NOT NULL DEFAULT 1,
    last_run_at      TIMESTAMP,
    last_success_at  TIMESTAMP,
    last_error       TEXT,
    last_task_id     TEXT,
    next_run_at      TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_USAGE_DDL = """
CREATE TABLE usage_events (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    platform                    TEXT,
    channel_id                  TEXT,
    thread_id                   TEXT,
    agent                       TEXT NOT NULL,
    model                       TEXT,
    source                      TEXT NOT NULL,
    input_tokens                INTEGER,
    output_tokens               INTEGER,
    cache_read_input_tokens     INTEGER,
    cache_creation_input_tokens INTEGER,
    cost_usd                    REAL,
    task_id                     TEXT
);
"""


@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    """Bare runtime.db with no rows, so queries succeed but return empty."""

    db = tmp_path / "runtime.db"
    conn = sqlite3.connect(db)
    conn.executescript(_RUNTIME_TASKS_DDL + _AUTOMATION_STATE_DDL + _USAGE_DDL)
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    """Fully populated runtime.db with diverse fixture rows."""

    db = tmp_path / "runtime.db"
    conn = sqlite3.connect(db)
    conn.executescript(_RUNTIME_TASKS_DDL + _AUTOMATION_STATE_DDL + _USAGE_DDL)

    # Two automations: one healthy, one with recent failure.
    conn.executemany(
        """
        INSERT INTO automation_runtime_state
        (name, platform, channel_id, enabled, last_run_at, last_success_at, last_error, next_run_at)
        VALUES (?, 'discord', 'ch1', ?, ?, ?, ?, ?)
        """,
        [
            (
                "automation-good",
                1,
                "2026-05-03T09:00:00",
                "2026-05-03T09:00:00",
                None,
                "2026-05-04T09:00:00",
            ),
            (
                "automation-bad",
                0,
                "2026-05-03T10:00:00",
                "2026-05-02T10:00:00",
                "HTTP 502 from upstream",
                None,
            ),
        ],
    )

    # Tasks: 2 success per automation, 1 failure for bad, 1 RUNNING.
    base_args = ("discord", "ch1", "th1", "user", "goal", 1, 1)
    rows = [
        ("t-ok-1", *base_args, "COMPLETED", "automation-good", None, None),
        ("t-ok-2", *base_args, "MERGED", "automation-good", None, None),
        ("t-bad-1", *base_args, "FAILED", "automation-bad", None, "Connection refused: socket"),
        ("t-bad-2", *base_args, "TIMEOUT", "automation-bad", None, "claude CLI timed out after 1500s"),
        ("t-running-1", *base_args, "RUNNING", None, None, None),
        ("t-skill-success", *base_args, "COMPLETED", None, "market-briefing", None),
        ("t-skill-fail", *base_args, "FAILED", None, "market-briefing", "boom"),
    ]
    conn.executemany(
        """
        INSERT INTO runtime_tasks
        (id, platform, channel_id, thread_id, created_by, goal, max_steps, max_minutes,
         status, automation_name, skill_name, error,
         test_command, created_at, ended_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'true', datetime('now', '-1 days'), datetime('now', '-1 days'))
        """,
        rows,
    )

    # Usage events — today + yesterday, multiple sources/skills.
    today_rows = [
        ("automation_run", 1000, 200, 0.05, "t-ok-1"),
        ("automation_run", 500, 100, 0.02, "t-skill-success"),
        ("chat", 200, 50, 0.005, None),
    ]
    for src, in_t, out_t, cost, task_id in today_rows:
        conn.execute(
            """
            INSERT INTO usage_events (ts, agent, source, input_tokens, output_tokens, cost_usd, task_id)
            VALUES (datetime('now'), 'claude', ?, ?, ?, ?, ?)
            """,
            (src, in_t, out_t, cost, task_id),
        )
    conn.execute(
        """
        INSERT INTO usage_events (ts, agent, source, input_tokens, output_tokens, cost_usd, task_id)
        VALUES (datetime('now', '-1 days'), 'claude', 'automation_run', 800, 150, 0.04, 't-ok-2')
        """
    )

    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# fetch_automation_health
# ---------------------------------------------------------------------------


def test_fetch_automation_health_missing_db(tmp_path: Path) -> None:
    result = data.fetch_automation_health(tmp_path / "does-not-exist.db")
    assert len(result) == 1
    assert "error" in result[0]


def test_fetch_automation_health_empty(empty_db: Path) -> None:
    assert data.fetch_automation_health(empty_db) == []


def test_fetch_automation_health_populated(populated_db: Path) -> None:
    result = data.fetch_automation_health(populated_db)
    by_name = {row["name"]: row for row in result}

    assert by_name["automation-good"]["enabled"] is True
    assert by_name["automation-good"]["success_count_7d"] == 2
    assert by_name["automation-good"]["total_count_7d"] == 2
    assert by_name["automation-good"]["success_rate_7d"] == 1.0

    assert by_name["automation-bad"]["enabled"] is False
    assert by_name["automation-bad"]["total_count_7d"] == 2
    assert by_name["automation-bad"]["success_count_7d"] == 0
    assert by_name["automation-bad"]["success_rate_7d"] == 0.0
    assert "HTTP 502" in by_name["automation-bad"]["last_error"]


# ---------------------------------------------------------------------------
# fetch_task_health
# ---------------------------------------------------------------------------


def test_fetch_task_health_missing_db(tmp_path: Path) -> None:
    result = data.fetch_task_health(tmp_path / "missing.db")
    assert "error" in result


def test_fetch_task_health_populated(populated_db: Path) -> None:
    result = data.fetch_task_health(populated_db)
    assert result["current_status"]["RUNNING"] == 1
    assert result["current_status"]["FAILED"] == 2  # t-bad-1 + t-skill-fail
    assert result["current_status"]["COMPLETED"] == 2

    assert result["terminal_7d"]["COMPLETED"] == 2
    assert result["terminal_7d"]["FAILED"] == 2
    assert result["terminal_7d"]["TIMEOUT"] == 1

    failures = result["recent_failures"]
    failure_ids = {f["id"] for f in failures}
    assert "t-bad-1" in failure_ids
    assert "t-bad-2" in failure_ids
    # Errors get truncated to 120 chars but ours are short — should appear intact.
    bad_1 = next(f for f in failures if f["id"] == "t-bad-1")
    assert "Connection refused" in bad_1["error"]


# ---------------------------------------------------------------------------
# fetch_cost_usage
# ---------------------------------------------------------------------------


def test_fetch_cost_usage_missing_db(tmp_path: Path) -> None:
    result = data.fetch_cost_usage(tmp_path / "missing.db")
    assert "error" in result


def test_fetch_cost_usage_populated(populated_db: Path) -> None:
    result = data.fetch_cost_usage(populated_db)

    # 7-day daily — should have at least today + yesterday
    assert len(result["daily_7d"]) >= 1

    # Today by source — automation_run and chat both today
    today_sources = {row["source"] for row in result["today_by_source"]}
    assert "automation_run" in today_sources
    assert "chat" in today_sources

    # Top skill today — only market-briefing (one event)
    skills = result["today_top_skills"]
    assert len(skills) == 1
    assert skills[0]["skill"] == "market-briefing"


# ---------------------------------------------------------------------------
# fetch_memory_summary
# ---------------------------------------------------------------------------


def test_fetch_memory_summary_missing(tmp_path: Path) -> None:
    result = data.fetch_memory_summary(tmp_path / "memories.yaml")
    assert "error" in result


def test_fetch_memory_summary_active_vs_superseded(tmp_path: Path) -> None:
    yaml_path = tmp_path / "memories.yaml"
    yaml_path.write_text(
        """
entries:
  - id: m-1
    summary: Active preference
    status: active
    category: preference
    scope: global_user
    created_at: "2026-05-03T00:00:00+00:00"
  - id: m-2
    summary: Superseded fact
    status: superseded
    category: fact
    scope: workspace
    created_at: "2025-01-01T00:00:00+00:00"
  - id: m-3
    summary: Active workflow
    status: active
    category: workflow
    scope: skill
    created_at: "2026-04-29T00:00:00+00:00"
""",
        encoding="utf-8",
    )
    r = data.fetch_memory_summary(yaml_path)
    assert r["total"] == 3
    assert r["active"] == 2
    assert r["superseded"] == 1
    assert r["by_category"]["preference"] == 1
    assert r["by_category"]["fact"] == 1
    assert r["by_category"]["workflow"] == 1
    assert r["by_scope"]["global_user"] == 1
    # m-1 and m-3 created within last 7 days
    assert r["new_7d"] >= 1


# ---------------------------------------------------------------------------
# fetch_skill_stats
# ---------------------------------------------------------------------------


def test_fetch_skill_stats_populated(populated_db: Path) -> None:
    result = data.fetch_skill_stats(populated_db)
    by_skill = {row["skill"]: row for row in result}
    assert "market-briefing" in by_skill
    assert by_skill["market-briefing"]["total"] == 2
    assert by_skill["market-briefing"]["success_count"] == 1
    assert by_skill["market-briefing"]["success_rate"] == 0.5


# ---------------------------------------------------------------------------
# fetch_log_health — the WARNING-vs-WARN trap + two-file aggregation
# ---------------------------------------------------------------------------


def _write_log(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_fetch_log_health_severity_string_is_warning_not_warn(tmp_path: Path) -> None:
    """Codex-flagged trap: Python logging emits ``level=WARNING``, not ``WARN``.

    If the parser regressed to looking for ``WARN`` it would silently miss every
    warning line. This fixture uses ONLY ``WARNING`` and asserts it's counted.
    """

    log = tmp_path / "service.log"
    _write_log(
        log,
        [
            "2026-05-03T20:00:00.000Z level=INFO logger=oh_my_agent.gateway msg=hello",
            "2026-05-03T20:01:00.000Z level=WARNING logger=oh_my_agent.runtime msg=slow",
            "2026-05-03T20:02:00.000Z level=ERROR logger=oh_my_agent.runtime msg=boom",
            "2026-05-03T20:03:00.000Z level=WARNING logger=oh_my_agent.gateway msg=again",
        ],
    )
    r = data.fetch_log_health([log])
    assert r["total_error"] == 1
    assert r["total_warning"] == 2  # 0 if regex regressed to WARN
    assert len(r["recent_errors"]) == 1


def test_fetch_log_health_two_file_aggregation(tmp_path: Path) -> None:
    log_a = tmp_path / "service.log"
    log_b = tmp_path / "oh-my-agent.log"
    _write_log(
        log_a,
        ["2026-05-03T20:00:00.000Z level=ERROR logger=a msg=err-a"],
    )
    _write_log(
        log_b,
        [
            "2026-05-03T20:00:00.000Z level=WARNING logger=b msg=warn-b",
            "2026-05-03T20:01:00.000Z level=ERROR logger=b msg=err-b",
        ],
    )
    r = data.fetch_log_health([log_a, log_b])
    assert r["total_error"] == 2
    assert r["total_warning"] == 1
    assert len(r["files_read"]) == 2


def test_fetch_log_health_one_file_missing_other_present(tmp_path: Path) -> None:
    log = tmp_path / "oh-my-agent.log"
    _write_log(log, ["2026-05-03T20:00:00.000Z level=ERROR logger=x msg=err-x"])
    r = data.fetch_log_health([tmp_path / "service.log", log])
    assert r["total_error"] == 1
    assert r["files_missing"] == [str(tmp_path / "service.log")]


def test_fetch_log_health_all_files_missing(tmp_path: Path) -> None:
    r = data.fetch_log_health([tmp_path / "a.log", tmp_path / "b.log"])
    assert "error" in r
    assert "all log files missing" in r["error"]


def test_fetch_log_health_skips_traceback_lines(tmp_path: Path) -> None:
    """Multi-line tracebacks have lines without level= — must skip silently."""

    log = tmp_path / "service.log"
    _write_log(
        log,
        [
            "2026-05-03T20:00:00.000Z level=ERROR logger=x msg=oops",
            "Traceback (most recent call last):",
            '  File "/src/foo.py", line 42, in handler',
            "    raise ValueError(...)",
            "ValueError: bad",
            "2026-05-03T20:01:00.000Z level=INFO logger=x msg=ok",
        ],
    )
    r = data.fetch_log_health([log])
    assert r["total_error"] == 1
    # The traceback lines didn't contribute false ERRORs
    assert len(r["recent_errors"]) == 1


# ---------------------------------------------------------------------------
# fetch_disk_usage
# ---------------------------------------------------------------------------


def test_fetch_disk_usage_mixed_paths(tmp_path: Path) -> None:
    f = tmp_path / "a.log"
    f.write_text("hello", encoding="utf-8")
    d = tmp_path / "subdir"
    d.mkdir()
    (d / "x.bin").write_bytes(b"\x00" * 100)
    missing = tmp_path / "nope.db"

    result = data.fetch_disk_usage([f, d, missing])
    by_path = {row["path"]: row for row in result}

    assert by_path[str(f)]["kind"] == "file"
    assert by_path[str(f)]["size_bytes"] == 5

    assert by_path[str(d)]["kind"] == "dir"
    assert by_path[str(d)]["size_bytes"] == 100

    assert by_path[str(missing)]["exists"] is False
    assert by_path[str(missing)]["kind"] == "missing"


# ---------------------------------------------------------------------------
# fetch_bot_uptime
# ---------------------------------------------------------------------------


def test_fetch_bot_uptime_finds_last_runtime_started(tmp_path: Path) -> None:
    log = tmp_path / "service.log"
    _write_log(
        log,
        [
            "2026-05-03T19:00:00.000Z level=INFO logger=oh_my_agent.gateway msg=Bot starting",
            "2026-05-03T19:00:01.000Z level=INFO logger=oh_my_agent.runtime.service msg=Runtime started with 1 worker(s) + janitor",
            # Log lives across restarts — make sure we pick the LAST one
            "2026-05-03T20:00:00.000Z level=INFO logger=oh_my_agent.gateway msg=heartbeat",
            "2026-05-03T20:30:00.000Z level=INFO logger=oh_my_agent.runtime.service msg=Runtime started with 2 worker(s) + janitor",
            "2026-05-03T20:30:01.000Z level=INFO logger=oh_my_agent.gateway msg=connected",
        ],
    )
    r = data.fetch_bot_uptime(log)
    assert "error" not in r
    assert "20:30:00" in (r.get("started_at") or "")  # last match wins
    assert r.get("uptime_seconds") is not None


def test_fetch_bot_uptime_no_match(tmp_path: Path) -> None:
    log = tmp_path / "service.log"
    _write_log(log, ["2026-05-03T20:00:00.000Z level=INFO logger=x msg=hello"])
    r = data.fetch_bot_uptime(log)
    assert "error" in r and "no Runtime started" in r["error"]


def test_fetch_bot_uptime_missing_file(tmp_path: Path) -> None:
    r = data.fetch_bot_uptime(tmp_path / "missing.log")
    assert "error" in r
