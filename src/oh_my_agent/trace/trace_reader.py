"""Read-only trace JSONL reader for the dashboard v1 API.

Counterpart to :class:`oh_my_agent.trace.trace_writer.TraceWriter` which
appends per-day JSONL files. The reader is **strictly day-bounded** —
this is a deliberate Codex round-1 catch: a "scan all days" fallback
would silently fan out to the entire trace dir under the wrong query
and is dangerous on long-running deployments. The v1 API enforces
``date=YYYY-MM-DD`` as a required parameter.

One JSONL line per :class:`oh_my_agent.agents.events.AgentEvent`. Every
line carries ``thread_id`` (set by the writer), so per-thread filtering
is a streaming line-scan with no auxiliary index. Cost: O(lines in one
day). For typical single-user deployments this is sub-second even on
multi-MB files.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _validate_date(date_str: str) -> str | None:
    """Return canonical YYYY-MM-DD or ``None`` if invalid.

    Defensive — caller can pass anything. Rejects timezone suffixes,
    ISO datetimes, etc. to keep the on-disk filename mapping unambiguous.
    """

    if not date_str or len(date_str) != 10 or date_str[4] != "-" or date_str[7] != "-":
        return None
    try:
        parsed = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%d")


def read_thread_trace(
    trace_dir: Path,
    *,
    thread_id: str,
    date: str,
    limit: int = 500,
) -> dict[str, Any]:
    """Return events for one thread on one day.

    ``date`` MUST be ``YYYY-MM-DD``. Missing files return an empty
    ``items`` list (not an error — the day might simply have no events
    yet, e.g. during the first run of the morning).

    Returns ``{"items": [...], "date": "...", "thread_id": "..."}`` on
    success, or ``{"error": str}`` on invalid input / IO failure.
    """

    canonical_date = _validate_date(date)
    if canonical_date is None:
        return {"error": f"invalid date: expected YYYY-MM-DD, got {date!r}"}

    if not thread_id:
        return {"error": "thread_id is required"}

    limit = max(1, min(int(limit), 2000))

    trace_dir = Path(trace_dir).expanduser()
    jsonl_path = trace_dir / f"{canonical_date}.jsonl"

    if not jsonl_path.exists():
        # No file for this date is a normal empty case (e.g. the bot
        # hasn't emitted any events yet today). Return an empty list
        # rather than 404 — the frontend renders "no tool calls yet".
        return {"items": [], "date": canonical_date, "thread_id": thread_id}

    items: list[dict[str, Any]] = []
    try:
        with jsonl_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    # Skip malformed lines silently — a partial write
                    # at the end of a crashed session shouldn't break
                    # the entire day's read.
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("thread_id") != thread_id:
                    continue
                items.append(obj)
                if len(items) >= limit:
                    break
    except OSError as exc:
        return {"error": f"trace read failed: {type(exc).__name__}: {exc}"}

    return {"items": items, "date": canonical_date, "thread_id": thread_id}
