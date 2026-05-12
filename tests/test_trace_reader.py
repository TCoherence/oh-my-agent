"""Unit coverage for the read-only trace JSONL reader.

Counterpart to :mod:`oh_my_agent.trace.trace_writer`. The reader is
strictly day-bounded — no "scan all days" fallback — and filters by
``thread_id`` so the dashboard can render per-thread tool chains.
"""

from __future__ import annotations

import json
from pathlib import Path

from oh_my_agent.trace.trace_reader import _validate_date, read_thread_trace


def test_validate_date_accepts_iso() -> None:
    assert _validate_date("2026-05-12") == "2026-05-12"


def test_validate_date_rejects_garbage() -> None:
    for bad in ("", "12-05-2026", "2026/05/12", "2026-13-01", "2026-05-12T10:00", None):
        assert _validate_date(bad or "") is None


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def test_read_thread_trace_filters_by_thread_id(tmp_path: Path) -> None:
    jsonl = tmp_path / "2026-05-12.jsonl"
    _write_jsonl(
        jsonl,
        [
            {"type": "tool_use", "thread_id": "t1", "name": "Read", "ts": "..."},
            {"type": "tool_use", "thread_id": "t2", "name": "Bash", "ts": "..."},
            {"type": "tool_use", "thread_id": "t1", "name": "Edit", "ts": "..."},
        ],
    )
    result = read_thread_trace(tmp_path, thread_id="t1", date="2026-05-12")
    assert result["date"] == "2026-05-12"
    assert result["thread_id"] == "t1"
    items = result["items"]
    assert len(items) == 2
    assert [it["name"] for it in items] == ["Read", "Edit"]


def test_read_thread_trace_missing_file_returns_empty(tmp_path: Path) -> None:
    """Missing file is a valid empty case (e.g. day with no tool activity)."""

    result = read_thread_trace(tmp_path, thread_id="t1", date="2026-05-12")
    assert result == {"items": [], "date": "2026-05-12", "thread_id": "t1"}


def test_read_thread_trace_invalid_date_returns_error(tmp_path: Path) -> None:
    result = read_thread_trace(tmp_path, thread_id="t1", date="not-a-date")
    assert "error" in result
    assert "invalid date" in result["error"].lower()


def test_read_thread_trace_empty_thread_id_returns_error(tmp_path: Path) -> None:
    result = read_thread_trace(tmp_path, thread_id="", date="2026-05-12")
    assert "error" in result


def test_read_thread_trace_skips_malformed_lines(tmp_path: Path) -> None:
    """Partial / crashed-mid-write lines must not break the entire day."""

    jsonl = tmp_path / "2026-05-12.jsonl"
    jsonl.write_text(
        "\n".join(
            [
                json.dumps({"type": "tool_use", "thread_id": "t1", "name": "Read"}),
                "this is not json",  # corrupted line
                json.dumps({"type": "tool_use", "thread_id": "t1", "name": "Edit"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = read_thread_trace(tmp_path, thread_id="t1", date="2026-05-12")
    assert len(result["items"]) == 2


def test_read_thread_trace_respects_limit(tmp_path: Path) -> None:
    jsonl = tmp_path / "2026-05-12.jsonl"
    _write_jsonl(
        jsonl,
        [{"type": "tool_use", "thread_id": "t1", "name": f"Tool{i}"} for i in range(20)],
    )
    result = read_thread_trace(tmp_path, thread_id="t1", date="2026-05-12", limit=5)
    assert len(result["items"]) == 5
    assert result["items"][0]["name"] == "Tool0"
