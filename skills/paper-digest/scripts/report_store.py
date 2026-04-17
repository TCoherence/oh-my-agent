#!/usr/bin/env python3
"""Persist and query paper-digest report files.

单 domain skill：所有报告都属于 paper-digest，无 sub-domain。
存储路径：~/.oh-my-agent/reports/paper-digest/daily/<YYYY-MM-DD>.md|json
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


REPORTS_ROOT_NAME = "paper-digest"
VALID_MODES = {"daily_digest"}
DOMAIN_LABEL = "paper-digest"


def resolve_report_timezone_label() -> str:
    tz_name = resolve_report_timezone_name()
    if tz_name:
        return tz_name
    tz = resolve_report_timezone()
    key = getattr(tz, "key", None)
    if isinstance(key, str) and key:
        return key
    tz_label = current_local_datetime().tzname()
    if tz_label:
        return tz_label
    return str(tz)


def resolve_report_timezone_name() -> str | None:
    for key in ("OMA_REPORT_TIMEZONE", "TZ"):
        raw = os.environ.get(key)
        if raw and raw.strip():
            return raw.strip()
    return None


def resolve_report_timezone():
    tz_name = resolve_report_timezone_name()
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            pass
    return datetime.now().astimezone().tzinfo or UTC


def current_local_datetime(now: datetime | None = None) -> datetime:
    tz = resolve_report_timezone()
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(tz)


def current_local_date(now: datetime | None = None) -> date:
    return current_local_datetime(now).date()


def _default_reports_root() -> Path:
    return Path.home() / ".oh-my-agent" / "reports" / REPORTS_ROOT_NAME


def resolve_reports_root(root: str | Path | None = None) -> Path:
    if root is not None:
        base = Path(root).expanduser()
    else:
        base = _default_reports_root()
    return base.resolve()


def parse_report_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    return date.fromisoformat(raw)


def build_report_paths(
    *,
    mode: str = "daily_digest",
    root: str | Path | None = None,
    report_date: date | None = None,
) -> tuple[Path, Path]:
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    store_root = resolve_reports_root(root)
    day = report_date or current_local_date()
    day_label = day.isoformat()
    md_path = store_root / "daily" / f"{day_label}.md"
    json_path = store_root / "daily" / f"{day_label}.json"
    return md_path, json_path


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


def build_markdown_skeleton(*, mode: str = "daily_digest", report_date: date | None = None) -> str:
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    day_label = (report_date or current_local_date()).isoformat()
    return "\n".join(
        [
            f"# 论文雷达日报｜{day_label}",
            "",
            "一句话结论：",
            "",
            "## 摘要",
            "",
            "## 📌 Top picks (交叉命中)",
            "",
            "## 🏷 Watchlist 分类命中",
            "",
            "## 🔗 延伸阅读 (Semantic Scholar 相似论文)",
            "",
            "## 🧑‍🔬 新出现的作者 / 团队",
            "",
            "## 📉 覆盖缺口与不确定性",
            "",
            "## 来源与交叉验证说明",
            "",
        ]
    )


def build_json_scaffold(
    *,
    mode: str = "daily_digest",
    report_date: date | None = None,
) -> dict[str, Any]:
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    day = report_date or current_local_date()
    payload: dict[str, Any] = {
        "version": 1,
        "mode": mode,
        "domain": DOMAIN_LABEL,
        "title": "",
        "generated_at": datetime.now(UTC).isoformat(),
        "report_timezone": resolve_report_timezone_label(),
        "report_date": day.isoformat(),
        "period_start": day.isoformat(),
        "period_end": day.isoformat(),
        "summary": "",
        "arxiv_categories": [],
        "top_picks": [],
        "category_hits": {},
        "extended_reading": [],
        "new_authors": [],
        "tracked_labs_seen": [],
        "coverage_gaps": [],
        "confidence_flags": [],
        "source_mix_note": "",
        "verification_note": "",
        "sources": [],
        "sections": [
            {"slug": "top-picks", "heading": "📌 Top picks (交叉命中)", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "category-hits", "heading": "🏷 Watchlist 分类命中", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "extended-reading", "heading": "🔗 延伸阅读 (Semantic Scholar 相似论文)", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "new-authors", "heading": "🧑‍🔬 新出现的作者 / 团队", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "coverage-gaps", "heading": "📉 覆盖缺口与不确定性", "summary": "", "bullets": [], "evidence_links": []},
        ],
    }
    return payload


def persist_report(
    *,
    mode: str = "daily_digest",
    markdown: str,
    payload: dict[str, Any],
    root: str | Path | None = None,
    report_date: date | None = None,
) -> tuple[Path, Path]:
    resolved_day = report_date or current_local_date()
    md_path, json_path = build_report_paths(
        mode=mode,
        root=root,
        report_date=resolved_day,
    )
    normalized = dict(payload)
    normalized["version"] = 1
    normalized["mode"] = mode
    normalized["domain"] = DOMAIN_LABEL
    normalized["generated_at"] = datetime.now(UTC).isoformat()
    normalized["report_timezone"] = resolve_report_timezone_label()
    normalized["report_date"] = resolved_day.isoformat()
    normalized.setdefault("period_start", resolved_day.isoformat())
    normalized.setdefault("period_end", resolved_day.isoformat())
    normalized.setdefault("coverage_gaps", [])
    normalized.setdefault("confidence_flags", [])
    atomic_write_text(md_path, markdown)
    atomic_write_text(json_path, json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")
    return md_path, json_path


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"report JSON must contain an object: {path}")
    data["_path"] = str(path)
    return data


def _iter_json_files(root: str | Path | None = None) -> list[Path]:
    store_root = resolve_reports_root(root)
    return sorted((store_root / "daily").glob("*.json"))


def list_reports(
    *,
    root: str | Path | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in _iter_json_files(root):
        try:
            data = _load_json(path)
        except Exception:
            continue
        items.append(data)
    items.sort(key=lambda item: item.get("report_date", item.get("period_end", "")), reverse=True)
    if limit is not None:
        return items[:limit]
    return items


def load_recent_daily_reports(
    *,
    root: str | Path | None = None,
    as_of: date | None = None,
    days: int = 7,
) -> list[dict[str, Any]]:
    end = as_of or current_local_date()
    start = end - timedelta(days=max(days - 1, 0))
    start_label = start.isoformat()
    end_label = end.isoformat()
    items = [
        item
        for item in list_reports(root=root)
        if start_label <= str(item.get("report_date", item.get("period_end", ""))) <= end_label
    ]
    items.sort(key=lambda item: item.get("report_date", item.get("period_end", "")))
    return items


def build_context(
    *,
    mode: str = "daily_digest",
    root: str | Path | None = None,
    as_of: date | None = None,
    days: int = 7,
) -> dict[str, Any]:
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    day = as_of or current_local_date()
    return {
        "mode": mode,
        "domain": DOMAIN_LABEL,
        "as_of": day.isoformat(),
        "reports_root": str(resolve_reports_root(root)),
        "recent_daily": load_recent_daily_reports(root=root, as_of=day, days=days),
    }


def _try_record_seen_pool(
    json_path: Path,
    *,
    root: str | Path | None = None,
    as_of: date | None = None,
) -> dict[str, Any] | None:
    """Auto-record paper-seen pool after persisting a daily report.

    Returns the record result dict on success, or ``None`` if the
    seen-pool module is unavailable or the recording fails.
    """
    try:
        import importlib.util

        pool_path = Path(__file__).resolve().parent / "paper_seen_pool.py"
        spec = importlib.util.spec_from_file_location("paper_seen_pool", pool_path)
        if spec is None or spec.loader is None:
            return None
        pool_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pool_mod)
        return pool_mod.record_report(report_json=str(json_path), root=root, as_of=as_of)
    except Exception:
        return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persist and query paper-digest report files")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold = subparsers.add_parser("scaffold", help="write a starter Markdown and JSON scaffold")
    scaffold.add_argument("--mode", default="daily_digest", choices=sorted(VALID_MODES))
    scaffold.add_argument("--report-date")
    scaffold.add_argument("--markdown-file", required=True)
    scaffold.add_argument("--json-file", required=True)

    persist = subparsers.add_parser("persist", help="persist report files into canonical storage")
    persist.add_argument("--mode", default="daily_digest", choices=sorted(VALID_MODES))
    persist.add_argument("--report-date")
    persist.add_argument("--root")
    persist.add_argument("--markdown-file", required=True)
    persist.add_argument("--json-file", required=True)

    context_cmd = subparsers.add_parser("context", help="load prior report context as JSON")
    context_cmd.add_argument("--mode", default="daily_digest", choices=sorted(VALID_MODES))
    context_cmd.add_argument("--root")
    context_cmd.add_argument("--as-of")
    context_cmd.add_argument("--days", type=int, default=7)

    list_cmd = subparsers.add_parser("list", help="list stored reports")
    list_cmd.add_argument("--root")
    list_cmd.add_argument("--limit", type=int)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "scaffold":
        report_day = parse_report_date(args.report_date)
        markdown = build_markdown_skeleton(mode=args.mode, report_date=report_day)
        payload = build_json_scaffold(mode=args.mode, report_date=report_day)
        atomic_write_text(Path(args.markdown_file), markdown)
        atomic_write_text(Path(args.json_file), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "status": "ok",
                    "markdown_file": str(Path(args.markdown_file).resolve()),
                    "json_file": str(Path(args.json_file).resolve()),
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "persist":
        report_day = parse_report_date(args.report_date)
        markdown = Path(args.markdown_file).read_text(encoding="utf-8")
        payload = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("report JSON must contain an object")
        md_path, json_path = persist_report(
            mode=args.mode,
            markdown=markdown,
            payload=payload,
            root=args.root,
            report_date=report_day,
        )
        output: dict[str, Any] = {
            "status": "ok",
            "markdown_path": str(md_path),
            "json_path": str(json_path),
            "seen_pool_update": _try_record_seen_pool(json_path, root=args.root, as_of=report_day),
        }
        print(json.dumps(output, ensure_ascii=False))
        return 0

    if args.command == "context":
        context = build_context(
            mode=args.mode,
            root=args.root,
            as_of=parse_report_date(args.as_of),
            days=args.days,
        )
        print(json.dumps(context, ensure_ascii=False, indent=2))
        return 0

    if args.command == "list":
        items = list_reports(root=args.root, limit=args.limit)
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0

    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
