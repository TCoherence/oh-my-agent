"""Pure-function data layer for the dashboard.

Every ``fetch_*`` function takes explicit ``Path`` inputs and returns a plain
``dict`` / ``list``. No global state, no caching, no ORM. Easy to test with
``tmp_path`` fixtures.

All functions self-contain error handling: on ``sqlite3.OperationalError``,
``FileNotFoundError``, ``yaml.YAMLError``, or any IO exception they return a
placeholder dict with an ``error`` key rather than raising. The template
renders the error string verbatim instead of crashing the page.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

LOG_TAIL_BYTES = 64 * 1024
LOG_ERROR_LINE_TRUNCATE = 120
LOG_BUCKET_MINUTES = 5
LOG_BUCKET_WINDOW_MINUTES = 60
RECENT_FAILURE_LIMIT = 10
SKILL_STATS_LOOKBACK_DAYS = 30
COST_LOOKBACK_DAYS = 7
MEMORY_NEW_LOOKBACK_DAYS = 7

# RuntimeService terminal states considered "successful" for success-rate calc.
SUCCESS_STATES = ("COMPLETED", "MERGED")
TERMINAL_STATES = ("COMPLETED", "MERGED", "FAILED", "CANCELLED", "TIMEOUT")

# Regex matching service.log structured line:
#   <ISO> level=<LEVEL> logger=<name> msg=<text>
# Lines that don't match (multi-line tracebacks etc.) are skipped silently.
_LOG_LINE_RE = re.compile(
    r"^(?P<ts>\S+)\s+level=(?P<level>\w+)\s+logger=(?P<logger>\S+)\s+msg=(?P<msg>.*)$"
)

# Pattern for the bot-startup line emitted by RuntimeService.start()
_RUNTIME_STARTED_RE = re.compile(r"Runtime started with \d+ worker")


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def _ro_connect(db_path: Path) -> sqlite3.Connection:
    """Open SQLite file in read-only URI mode.

    WAL-safe: concurrent reads do not block writers, and reading does not
    create -journal/-wal files. Raises ``sqlite3.OperationalError`` when the
    database file is missing — callers translate that into a placeholder
    error dict.
    """

    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _error_placeholder(label: str, exc: Exception) -> dict:
    return {"error": f"{label}: {type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Section 1 — Automation health
# ---------------------------------------------------------------------------


def fetch_automation_health(db_path: Path) -> list[dict]:
    """Each row: name / enabled / success_rate_7d / last_run / last_success /
    next_run / last_error (truncated). Returns ``[{"error": ...}]`` on failure.
    """

    try:
        conn = _ro_connect(db_path)
    except sqlite3.OperationalError as exc:
        return [_error_placeholder("automation_runtime_state unavailable", exc)]

    try:
        states = conn.execute(
            """
            SELECT name, enabled, last_run_at, last_success_at, last_error, next_run_at
            FROM automation_runtime_state
            ORDER BY name
            """
        ).fetchall()

        rate_rows = conn.execute(
            f"""
            SELECT automation_name AS name,
                   SUM(CASE WHEN status IN ({",".join("?" * len(SUCCESS_STATES))}) THEN 1 ELSE 0 END) AS ok,
                   COUNT(*) AS total
            FROM runtime_tasks
            WHERE automation_name IS NOT NULL
              AND created_at > datetime('now', '-7 days')
            GROUP BY automation_name
            """,
            SUCCESS_STATES,
        ).fetchall()
    except sqlite3.OperationalError as exc:
        conn.close()
        return [_error_placeholder("automation query failed", exc)]
    finally:
        conn.close()

    rates = {row["name"]: (row["ok"], row["total"]) for row in rate_rows}

    out: list[dict] = []
    for state in states:
        ok, total = rates.get(state["name"], (0, 0))
        rate = (ok / total) if total else None
        last_error = (state["last_error"] or "")[:80] if state["last_error"] else ""
        out.append(
            {
                "name": state["name"],
                "enabled": bool(state["enabled"]),
                "success_rate_7d": rate,
                "success_count_7d": ok,
                "total_count_7d": total,
                "last_run_at": state["last_run_at"],
                "last_success_at": state["last_success_at"],
                "next_run_at": state["next_run_at"],
                "last_error": last_error,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Section 2 — Task / runtime health
# ---------------------------------------------------------------------------


def fetch_task_health(db_path: Path) -> dict:
    """Current status distribution + 7-day terminal counts + recent failures."""

    try:
        conn = _ro_connect(db_path)
    except sqlite3.OperationalError as exc:
        return _error_placeholder("runtime_tasks unavailable", exc)

    try:
        current = conn.execute(
            "SELECT status, COUNT(*) AS n FROM runtime_tasks GROUP BY status"
        ).fetchall()

        terminal_7d = conn.execute(
            f"""
            SELECT status, COUNT(*) AS n
            FROM runtime_tasks
            WHERE status IN ({",".join("?" * len(TERMINAL_STATES))})
              AND created_at > datetime('now', '-7 days')
            GROUP BY status
            """,
            TERMINAL_STATES,
        ).fetchall()

        recent_failures = conn.execute(
            """
            SELECT id, automation_name, error, ended_at, status
            FROM runtime_tasks
            WHERE status IN ('FAILED', 'TIMEOUT')
            ORDER BY COALESCE(ended_at, updated_at) DESC
            LIMIT ?
            """,
            (RECENT_FAILURE_LIMIT,),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        conn.close()
        return _error_placeholder("runtime_tasks query failed", exc)
    finally:
        conn.close()

    return {
        "current_status": {row["status"]: row["n"] for row in current},
        "terminal_7d": {row["status"]: row["n"] for row in terminal_7d},
        "recent_failures": [
            {
                "id": row["id"],
                "automation_name": row["automation_name"],
                "error": (row["error"] or "")[:120],
                "ended_at": row["ended_at"],
                "status": row["status"],
            }
            for row in recent_failures
        ],
    }


# ---------------------------------------------------------------------------
# Section 3 — Cost / usage
# ---------------------------------------------------------------------------


def fetch_cost_usage(db_path: Path) -> dict:
    """7-day daily totals + today by source + today top 5 by skill."""

    try:
        conn = _ro_connect(db_path)
    except sqlite3.OperationalError as exc:
        return _error_placeholder("usage_events unavailable", exc)

    try:
        daily = conn.execute(
            f"""
            SELECT date(ts) AS day,
                   COALESCE(SUM(input_tokens), 0) AS in_tok,
                   COALESCE(SUM(output_tokens), 0) AS out_tok,
                   COALESCE(SUM(cost_usd), 0.0) AS cost
            FROM usage_events
            WHERE ts > datetime('now', '-{COST_LOOKBACK_DAYS} days')
            GROUP BY day
            ORDER BY day
            """
        ).fetchall()

        today_by_source = conn.execute(
            """
            SELECT source,
                   COALESCE(SUM(input_tokens), 0) AS in_tok,
                   COALESCE(SUM(output_tokens), 0) AS out_tok,
                   COALESCE(SUM(cost_usd), 0.0) AS cost
            FROM usage_events
            WHERE date(ts) = date('now')
            GROUP BY source
            ORDER BY cost DESC
            """
        ).fetchall()

        today_by_skill = conn.execute(
            """
            SELECT rt.skill_name AS skill,
                   COALESCE(SUM(ue.input_tokens), 0) AS in_tok,
                   COALESCE(SUM(ue.output_tokens), 0) AS out_tok,
                   COALESCE(SUM(ue.cost_usd), 0.0) AS cost
            FROM usage_events ue
            LEFT JOIN runtime_tasks rt ON rt.id = ue.task_id
            WHERE date(ue.ts) = date('now')
              AND rt.skill_name IS NOT NULL
            GROUP BY rt.skill_name
            ORDER BY cost DESC
            LIMIT 5
            """
        ).fetchall()
    except sqlite3.OperationalError as exc:
        conn.close()
        return _error_placeholder("usage_events query failed", exc)
    finally:
        conn.close()

    daily_list = [
        {
            "day": row["day"],
            "in_tok": int(row["in_tok"] or 0),
            "out_tok": int(row["out_tok"] or 0),
            "cost": float(row["cost"] or 0.0),
        }
        for row in daily
    ]

    return {
        "daily_7d": daily_list,
        "today_total_cost": sum(d["cost"] for d in daily_list if d["day"] == _today_str()),
        "today_by_source": [
            {
                "source": row["source"],
                "in_tok": int(row["in_tok"] or 0),
                "out_tok": int(row["out_tok"] or 0),
                "cost": float(row["cost"] or 0.0),
            }
            for row in today_by_source
        ],
        "today_top_skills": [
            {
                "skill": row["skill"],
                "in_tok": int(row["in_tok"] or 0),
                "out_tok": int(row["out_tok"] or 0),
                "cost": float(row["cost"] or 0.0),
            }
            for row in today_by_skill
        ],
    }


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Section 4 — Memory & skill
# ---------------------------------------------------------------------------


def fetch_memory_summary(memories_yaml: Path) -> dict:
    """Counts from memories.yaml: total, active, superseded, by category, by scope, 7d new."""

    try:
        text = memories_yaml.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        return _error_placeholder("memories.yaml missing", exc)
    except OSError as exc:
        return _error_placeholder("memories.yaml unreadable", exc)

    try:
        parsed = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        return _error_placeholder("memories.yaml parse failed", exc)

    # memories.yaml accepts either a top-level list of entries or
    # ``{"entries": [...]}`` mapping. Normalize to a list.
    if isinstance(parsed, list):
        entries: list[Any] = parsed
    elif isinstance(parsed, dict):
        raw_entries = parsed.get("entries", [])
        entries = raw_entries if isinstance(raw_entries, list) else []
    else:
        entries = []

    total = len(entries)
    active = sum(1 for e in entries if isinstance(e, dict) and e.get("status", "active") == "active")
    superseded = total - active

    cat_counter: Counter[str] = Counter()
    scope_counter: Counter[str] = Counter()
    new_count = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=MEMORY_NEW_LOOKBACK_DAYS)

    for e in entries:
        if not isinstance(e, dict):
            continue
        cat_counter[str(e.get("category", "unknown"))] += 1
        scope_counter[str(e.get("scope", "unknown"))] += 1
        created = e.get("created_at")
        if isinstance(created, str):
            try:
                ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    new_count += 1
            except ValueError:
                pass

    return {
        "total": total,
        "active": active,
        "superseded": superseded,
        "by_category": dict(cat_counter),
        "by_scope": dict(scope_counter),
        "new_7d": new_count,
    }


def fetch_skill_stats(db_path: Path) -> list[dict]:
    """Per-skill 30-day invocation count, success rate, last-invoked timestamp."""

    try:
        conn = _ro_connect(db_path)
    except sqlite3.OperationalError as exc:
        return [_error_placeholder("skill stats unavailable", exc)]

    try:
        rows = conn.execute(
            f"""
            SELECT skill_name AS skill,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status IN ({",".join("?" * len(SUCCESS_STATES))}) THEN 1 ELSE 0 END) AS ok,
                   MAX(COALESCE(ended_at, updated_at)) AS last_at
            FROM runtime_tasks
            WHERE skill_name IS NOT NULL
              AND created_at > datetime('now', '-{SKILL_STATS_LOOKBACK_DAYS} days')
            GROUP BY skill_name
            ORDER BY total DESC
            """,
            SUCCESS_STATES,
        ).fetchall()
    except sqlite3.OperationalError as exc:
        conn.close()
        return [_error_placeholder("skill stats query failed", exc)]
    finally:
        conn.close()

    return [
        {
            "skill": row["skill"],
            "total": int(row["total"]),
            "success_count": int(row["ok"] or 0),
            "success_rate": (row["ok"] / row["total"]) if row["total"] else None,
            "last_at": row["last_at"],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Section 5 — System layer
# ---------------------------------------------------------------------------


def fetch_log_health(log_paths: list[Path]) -> dict:
    """Aggregate ERROR/WARNING counts across all provided log files.

    Reads the trailing ``LOG_TAIL_BYTES`` of each file (cheap, bounded). Lines
    that don't match the structured format (multi-line tracebacks, free-form
    output) are silently skipped.

    Severity strings come from Python ``logging``: ``ERROR`` / ``WARNING`` /
    ``INFO`` / ``DEBUG`` / ``CRITICAL`` — NOT ``WARN``. Filtering on ``WARN``
    would silently miss ``WARNING`` lines (Codex review insight).
    """

    # Window cutoff is computed once up front. Filtering happens at the
    # log-line timestamp level, NOT at the bucket-start-time level — Codex
    # round 2 caught that quantizing first then comparing bucket-start to
    # cutoff would silently drop lines whose timestamp falls within the
    # window but whose bucket starts before it (5-minute quantization
    # spans the cutoff for up to LOG_BUCKET_MINUTES - 1 minutes).
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=LOG_BUCKET_WINDOW_MINUTES)
    bucket_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"ERROR": 0, "WARNING": 0})
    error_lines: list[dict[str, Any]] = []
    parsed_lines = 0
    files_read: list[str] = []
    files_missing: list[str] = []

    for path in log_paths:
        try:
            lines = _tail_lines(path)
        except FileNotFoundError:
            files_missing.append(str(path))
            continue
        except OSError as exc:
            return _error_placeholder(f"log read failed for {path.name}", exc)

        files_read.append(str(path))
        for raw in lines:
            m = _LOG_LINE_RE.match(raw.strip())
            if not m:
                continue
            parsed_lines += 1
            level = m.group("level")
            if level not in ("ERROR", "WARNING"):
                continue

            # Parse the line's actual timestamp; reject if unparseable or
            # outside the window. Window-scoped totals + buckets +
            # recent_errors all derive from this filter.
            try:
                line_ts = datetime.fromisoformat(m.group("ts").replace("Z", "+00:00"))
            except ValueError:
                continue
            if line_ts.tzinfo is None:
                line_ts = line_ts.replace(tzinfo=timezone.utc)
            if line_ts < cutoff:
                continue

            bucket_key = _bucket_key(m.group("ts"))
            if bucket_key:
                bucket_counts[bucket_key][level] += 1
            if level == "ERROR" and len(error_lines) < 5:
                error_lines.append(
                    {
                        "ts": m.group("ts"),
                        "logger": m.group("logger"),
                        "msg": (m.group("msg") or "")[:LOG_ERROR_LINE_TRUNCATE],
                    }
                )

    if not files_read and files_missing:
        return {
            "error": f"all log files missing: {', '.join(files_missing)}",
            "files_missing": files_missing,
        }

    sorted_buckets = sorted(bucket_counts.items())

    return {
        "files_read": files_read,
        "files_missing": files_missing,
        "parsed_lines": parsed_lines,
        "buckets": [
            {"bucket": k, "error": v["ERROR"], "warning": v["WARNING"]} for k, v in sorted_buckets
        ],
        "recent_errors": error_lines,
        "total_error": sum(v["ERROR"] for _, v in sorted_buckets),
        "total_warning": sum(v["WARNING"] for _, v in sorted_buckets),
    }


def _tail_lines(path: Path, tail_bytes: int = LOG_TAIL_BYTES) -> list[str]:
    """Read the last ``tail_bytes`` bytes of ``path`` and split into lines.

    Drops the first line (likely incomplete due to mid-line seek). Empty file
    returns ``[]``.
    """

    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > tail_bytes:
            fh.seek(size - tail_bytes)
            data = fh.read()
        else:
            data = fh.read()
    if not data:
        return []
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if size > tail_bytes and lines:
        # First line may be truncated mid-message.
        lines = lines[1:]
    return lines


def _bucket_key(ts_str: str) -> str | None:
    """Quantize an ISO timestamp to ``LOG_BUCKET_MINUTES``-minute bucket."""

    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    minute = (ts.minute // LOG_BUCKET_MINUTES) * LOG_BUCKET_MINUTES
    return ts.replace(minute=minute, second=0, microsecond=0).isoformat()


def fetch_disk_usage(paths_to_measure: list[Path]) -> list[dict]:
    """Per-path size in bytes. Each entry: {path, exists, size_bytes, kind}.

    For directories the size is the sum of file sizes (``os.walk``). For files
    the size comes from ``stat()``.
    """

    out: list[dict] = []
    for path in paths_to_measure:
        try:
            if not path.exists():
                out.append({"path": str(path), "exists": False, "size_bytes": 0, "kind": "missing"})
                continue
            if path.is_file():
                out.append(
                    {
                        "path": str(path),
                        "exists": True,
                        "size_bytes": path.stat().st_size,
                        "kind": "file",
                    }
                )
            elif path.is_dir():
                total = 0
                for child in path.rglob("*"):
                    try:
                        if child.is_file():
                            total += child.stat().st_size
                    except OSError:
                        continue
                out.append({"path": str(path), "exists": True, "size_bytes": total, "kind": "dir"})
            else:
                out.append({"path": str(path), "exists": True, "size_bytes": 0, "kind": "other"})
        except OSError as exc:
            out.append({"path": str(path), "exists": False, "size_bytes": 0, "kind": f"error: {exc}"})
    return out


def fetch_bot_uptime(service_log_path: Path) -> dict:
    """Parse the most recent ``Runtime started with N worker(s)`` line.

    Returns ``{started_at: ISO, uptime_seconds: int}`` or ``{error: ...}``.
    """

    try:
        lines = _tail_lines(service_log_path, tail_bytes=LOG_TAIL_BYTES * 4)
    except FileNotFoundError as exc:
        return _error_placeholder("service.log missing", exc)
    except OSError as exc:
        return _error_placeholder("service.log read failed", exc)

    last_match: tuple[str, str] | None = None
    for raw in lines:
        m = _LOG_LINE_RE.match(raw.strip())
        if not m:
            continue
        if _RUNTIME_STARTED_RE.search(m.group("msg") or ""):
            last_match = (m.group("ts"), m.group("msg"))

    if not last_match:
        return {"error": "no Runtime started line found", "started_at": None, "uptime_seconds": None}

    ts_str, _msg = last_match
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError as exc:
        return _error_placeholder("uptime ts parse failed", exc)

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return {
        "started_at": ts.isoformat(),
        "uptime_seconds": max(0, int((now - ts).total_seconds())),
    }
