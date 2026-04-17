#!/usr/bin/env python3
"""
Persist weekly YouTube podcast digest reports + manage per-episode TL;DR files.

Storage layout:
  ~/.oh-my-agent/reports/youtube-podcast-digest/weekly/<ISO-week>/
    ├── report.md
    ├── report.json
    └── _episodes/<group>__<video_id>.md

Commands:
  context  [--weeks N] [--root PATH]
      Dump a JSON object with recent weekly reports as context for the agent.
  persist  [--week YYYY-Www] [--md-path PATH] [--json-path PATH] [--root PATH]
      Copy/move md + json into the canonical weekly dir.
  write-episode [--week YYYY-Www] --slug STR [--md-path PATH] [--md-stdin] [--root PATH]
      Write a single-episode TL;DR markdown into _episodes/<slug>.md atomically.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REPORTS_ROOT_NAME = "youtube-podcast-digest"
DEFAULT_CONTEXT_WEEKS = 4


def resolve_report_timezone():
    for key in ("OMA_REPORT_TIMEZONE", "TZ"):
        raw = os.environ.get(key)
        if raw and raw.strip():
            try:
                return ZoneInfo(raw.strip())
            except ZoneInfoNotFoundError:
                continue
    return datetime.now().astimezone().tzinfo or UTC


def current_local_date() -> date:
    return datetime.now(resolve_report_timezone()).date()


def iso_week_for_date(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def _default_reports_root() -> Path:
    return Path.home() / ".oh-my-agent" / "reports" / REPORTS_ROOT_NAME


def resolve_reports_root(root: str | Path | None = None) -> Path:
    base = Path(root).expanduser() if root else _default_reports_root()
    return base.resolve()


def week_dir(iso_week: str, *, root: str | Path | None = None) -> Path:
    return resolve_reports_root(root) / "weekly" / iso_week


def report_paths(iso_week: str, *, root: str | Path | None = None) -> tuple[Path, Path]:
    wd = week_dir(iso_week, root=root)
    return wd / "report.md", wd / "report.json"


def episodes_dir(iso_week: str, *, root: str | Path | None = None) -> Path:
    return week_dir(iso_week, root=root) / "_episodes"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def _resolve_week(raw: str | None) -> str:
    if raw:
        return raw
    return iso_week_for_date(current_local_date())


def cmd_context(args: argparse.Namespace) -> int:
    weeks_to_load = max(1, int(args.weeks))
    root = resolve_reports_root(args.root)
    weekly_dir = root / "weekly"
    loaded: list[dict[str, Any]] = []
    if weekly_dir.exists():
        week_subdirs = sorted(
            [p for p in weekly_dir.iterdir() if p.is_dir()],
            key=lambda p: p.name,
            reverse=True,
        )
        for subdir in week_subdirs[:weeks_to_load]:
            json_path = subdir / "report.json"
            if not json_path.exists():
                continue
            try:
                loaded.append(json.loads(json_path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    out = {
        "skill": REPORTS_ROOT_NAME,
        "as_of": datetime.now(resolve_report_timezone()).isoformat(),
        "current_iso_week": iso_week_for_date(current_local_date()),
        "recent_weekly_reports": loaded,
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


def cmd_persist(args: argparse.Namespace) -> int:
    iso_week = _resolve_week(args.week)
    md_path_in = Path(args.md_path).expanduser().resolve() if args.md_path else None
    json_path_in = Path(args.json_path).expanduser().resolve() if args.json_path else None
    if md_path_in is None and json_path_in is None:
        print(
            "[report_store] persist requires at least --md-path or --json-path",
            file=sys.stderr,
        )
        return 2
    md_out, json_out = report_paths(iso_week, root=args.root)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    if md_path_in is not None:
        atomic_write_text(md_out, md_path_in.read_text(encoding="utf-8"))
    if json_path_in is not None:
        atomic_write_text(json_out, json_path_in.read_text(encoding="utf-8"))
    result = {
        "iso_week": iso_week,
        "md_path": str(md_out) if md_path_in is not None else None,
        "json_path": str(json_out) if json_path_in is not None else None,
    }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


def cmd_write_episode(args: argparse.Namespace) -> int:
    iso_week = _resolve_week(args.week)
    slug = args.slug.strip()
    if not slug or "/" in slug or slug.startswith("."):
        print(f"[report_store] invalid slug: {slug!r}", file=sys.stderr)
        return 2
    if args.md_stdin:
        content = sys.stdin.read()
    elif args.md_path:
        content = Path(args.md_path).expanduser().read_text(encoding="utf-8")
    else:
        print(
            "[report_store] write-episode requires --md-path or --md-stdin",
            file=sys.stderr,
        )
        return 2
    target = episodes_dir(iso_week, root=args.root) / f"{slug}.md"
    atomic_write_text(target, content)
    result = {"iso_week": iso_week, "slug": slug, "path": str(target)}
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YouTube podcast digest report store")
    parser.add_argument("--root", help="Override reports root (default: ~/.oh-my-agent/reports/youtube-podcast-digest)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ctx = sub.add_parser("context", help="Dump recent weekly reports as JSON context")
    p_ctx.add_argument("--weeks", type=int, default=DEFAULT_CONTEXT_WEEKS)

    p_persist = sub.add_parser("persist", help="Copy md/json into weekly dir")
    p_persist.add_argument("--week", help="ISO week label (e.g. 2026-W16); default: current week")
    p_persist.add_argument("--md-path", help="Source markdown path")
    p_persist.add_argument("--json-path", help="Source JSON path")

    p_ep = sub.add_parser("write-episode", help="Write per-episode TL;DR markdown")
    p_ep.add_argument("--week", help="ISO week label; default: current week")
    p_ep.add_argument("--slug", required=True, help="Episode slug: <group>__<video_id>")
    p_ep.add_argument("--md-path", help="Source markdown file")
    p_ep.add_argument("--md-stdin", action="store_true", help="Read markdown from stdin")

    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "context":
        return cmd_context(args)
    if args.command == "persist":
        return cmd_persist(args)
    if args.command == "write-episode":
        return cmd_write_episode(args)
    print(f"[report_store] unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
