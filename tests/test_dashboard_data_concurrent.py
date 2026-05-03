"""WAL concurrent-read sentinel for the dashboard data layer.

Codex review (round 1) flagged "trust SQLite docs" as insufficient for the
read-during-write claim. This test pins the actual behavior we depend on:

- A writer thread inserts rows continuously into ``runtime_tasks``.
- A reader thread queries ``automation_runtime_state`` (a different table)
  via the dashboard's ``mode=ro`` URI connection.
- Reads must succeed without errors; rows seen are monotonically
  non-decreasing; writer keeps making progress (i.e. reads don't block writes).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from oh_my_agent.dashboard import data  # noqa: E402

_DDL = """
CREATE TABLE runtime_tasks (
    id            TEXT PRIMARY KEY,
    platform      TEXT NOT NULL,
    channel_id    TEXT NOT NULL,
    thread_id     TEXT NOT NULL,
    created_by    TEXT NOT NULL,
    goal          TEXT NOT NULL,
    status        TEXT NOT NULL,
    step_no       INTEGER NOT NULL DEFAULT 0,
    max_steps     INTEGER NOT NULL DEFAULT 1,
    max_minutes   INTEGER NOT NULL DEFAULT 1,
    test_command  TEXT NOT NULL DEFAULT 'true',
    completion_mode TEXT NOT NULL DEFAULT 'merge',
    task_type     TEXT NOT NULL DEFAULT 'repo_change',
    automation_name TEXT,
    skill_name    TEXT,
    error         TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at    TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at      TIMESTAMP
);

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

CREATE TABLE usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    agent TEXT NOT NULL,
    source TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    task_id TEXT
);
"""


def test_dashboard_read_does_not_block_writer(tmp_path: Path) -> None:
    """Writer keeps inserting; reader can query without errors and writer
    finishes its planned batch within a reasonable wall-clock budget.

    Test budget is generous (5 s for 200 inserts) — the assertion is
    "doesn't block / doesn't crash", not "fast on this machine".
    """

    db = tmp_path / "wal.db"
    setup_conn = sqlite3.connect(db)
    setup_conn.executescript(_DDL)
    setup_conn.execute("PRAGMA journal_mode=WAL")
    setup_conn.commit()
    setup_conn.close()

    n_writes = 200
    write_done = threading.Event()
    write_count = 0
    write_error: list[Exception] = []

    def writer() -> None:
        nonlocal write_count
        try:
            conn = sqlite3.connect(db, timeout=5.0)
            conn.execute("PRAGMA journal_mode=WAL")
            for i in range(n_writes):
                conn.execute(
                    """
                    INSERT INTO runtime_tasks
                    (id, platform, channel_id, thread_id, created_by, goal, status,
                     max_steps, max_minutes, test_command)
                    VALUES (?, 'discord', 'ch1', 'th1', 'u', 'g', 'COMPLETED', 1, 1, 'true')
                    """,
                    (f"task-{i:04d}",),
                )
                conn.commit()
                write_count += 1
            conn.close()
        except Exception as exc:  # pragma: no cover (failure-mode reporting)
            write_error.append(exc)
        finally:
            write_done.set()

    read_count = 0
    read_errors: list[Exception] = []

    def reader() -> None:
        nonlocal read_count
        # Loop while writer is still running. Each iteration: open ro-uri
        # connection, call the actual dashboard helper.
        while not write_done.is_set():
            try:
                # fetch_automation_health touches a different table
                # (automation_runtime_state) — should always succeed.
                data.fetch_automation_health(db)
                # fetch_task_health touches runtime_tasks (the table being
                # written to) — also must succeed under WAL.
                result = data.fetch_task_health(db)
                if "error" in result:
                    read_errors.append(RuntimeError(result["error"]))
                read_count += 1
            except Exception as exc:  # pragma: no cover
                read_errors.append(exc)
            time.sleep(0.001)

    t_start = time.monotonic()
    w = threading.Thread(target=writer, name="writer")
    r = threading.Thread(target=reader, name="reader")
    w.start()
    r.start()
    w.join(timeout=30.0)
    r.join(timeout=5.0)
    elapsed = time.monotonic() - t_start

    assert not write_error, f"writer raised: {write_error}"
    assert not read_errors, f"reader raised: {read_errors}"
    assert write_count == n_writes, f"writer made {write_count}/{n_writes} inserts"
    assert read_count > 0, "reader never executed (timing error)"
    # Generous budget: 200 inserts + concurrent reads should finish well under 30 s.
    assert elapsed < 30.0, f"took {elapsed:.1f}s — reader probably blocked writer"


def test_dashboard_read_observes_growing_task_count(tmp_path: Path) -> None:
    """Reads should see monotonic non-decreasing task count as writes accumulate."""

    db = tmp_path / "monotonic.db"
    conn = sqlite3.connect(db)
    conn.executescript(_DDL)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()

    counts: list[int] = []
    for i in range(20):
        conn.execute(
            """
            INSERT INTO runtime_tasks
            (id, platform, channel_id, thread_id, created_by, goal, status,
             max_steps, max_minutes, test_command)
            VALUES (?, 'discord', 'ch1', 'th1', 'u', 'g', 'RUNNING', 1, 1, 'true')
            """,
            (f"task-{i:04d}",),
        )
        conn.commit()
        snapshot = data.fetch_task_health(db)
        counts.append(sum(snapshot["current_status"].values()))

    conn.close()

    # Each read should see >= the previous read's count.
    for prev, cur in zip(counts, counts[1:]):
        assert cur >= prev, f"task count went backwards: {prev} -> {cur}"
    # And the final read must reflect all 20 writes.
    assert counts[-1] == 20
