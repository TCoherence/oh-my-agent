#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


VALID_MODES = {"weekly_pulse", "market_snapshot", "area_deep_dive"}
CORE_AREAS = ["seattle", "bellevue", "redmond", "kirkland", "issaquah"]
OPTIONAL_AREAS = ["bothell", "lynnwood"]
ALL_AREAS = CORE_AREAS + OPTIONAL_AREAS
AREA_LABELS = {
    "seattle": "Seattle",
    "bellevue": "Bellevue",
    "redmond": "Redmond",
    "kirkland": "Kirkland",
    "issaquah": "Issaquah",
    "bothell": "Bothell",
    "lynnwood": "Lynnwood",
}
REPORTS_ROOT_NAME = "seattle-metro-housing-watch"
DEFAULT_BASELINE_DAYS = 60


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


def current_local_datetime(now: datetime | None = None) -> datetime:
    tz = resolve_report_timezone()
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(tz)


def current_local_date(now: datetime | None = None) -> date:
    return current_local_datetime(now).date()


def parse_report_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    return date.fromisoformat(raw)


def normalize_area(area: str | None) -> str | None:
    if area is None:
        return None
    normalized = area.strip().lower().replace(" ", "-").replace("_", "-")
    if normalized not in ALL_AREAS:
        raise ValueError(f"unsupported area: {area}")
    return normalized


def _default_reports_root() -> Path:
    return Path.home() / ".oh-my-agent" / "reports" / REPORTS_ROOT_NAME


def resolve_reports_root(root: str | Path | None = None) -> Path:
    if root is not None:
        base = Path(root).expanduser()
    else:
        base = _default_reports_root()
    return base.resolve()


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


def build_report_paths(
    *,
    mode: str,
    root: str | Path | None = None,
    report_date: date | None = None,
    area: str | None = None,
) -> tuple[Path, Path]:
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    store_root = resolve_reports_root(root)
    day = report_date or current_local_date()
    day_label = day.isoformat()

    if mode == "weekly_pulse":
        return (
            store_root / "weekly" / f"{day_label}.md",
            store_root / "weekly" / f"{day_label}.json",
        )
    if mode == "market_snapshot":
        return (
            store_root / "snapshot" / day_label / "seattle-metro.md",
            store_root / "snapshot" / day_label / "seattle-metro.json",
        )
    normalized_area = normalize_area(area)
    if normalized_area is None:
        raise ValueError("area_deep_dive requires --area")
    return (
        store_root / "areas" / day_label / f"{normalized_area}.md",
        store_root / "areas" / day_label / f"{normalized_area}.json",
    )


def _report_title(*, mode: str, day: date, area: str | None = None) -> str:
    if mode == "weekly_pulse":
        return f"西雅图房市周脉搏｜{day.isoformat()}"
    if mode == "market_snapshot":
        return f"西雅图房市快照｜{day.isoformat()}"
    normalized_area = normalize_area(area)
    if normalized_area is None:
        raise ValueError("area_deep_dive requires area")
    return f"{AREA_LABELS[normalized_area]} 房市深挖｜{day.isoformat()}"


def build_markdown_skeleton(
    *,
    mode: str,
    report_date: date | None = None,
    area: str | None = None,
) -> str:
    day = report_date or current_local_date()
    title = _report_title(mode=mode, day=day, area=area)
    if mode == "weekly_pulse":
        sections = [
            "一句话结论：",
            "",
            "## 摘要",
            "",
            "## 数据新鲜度说明",
            "",
            "## 利率与融资环境",
            "",
            "## Seattle Metro 核心市场脉搏",
            "",
            "## 区域 Scoreboard",
            "",
            "## 分区域买方观察",
            "",
            "## 代表性挂牌样本",
            "",
            "## 后续观察点",
            "",
            "## 来源与交叉验证说明",
            "",
        ]
    elif mode == "market_snapshot":
        sections = [
            "一句话结论：",
            "",
            "## 摘要",
            "",
            "## 数据新鲜度说明",
            "",
            "## 利率与融资环境",
            "",
            "## Seattle Metro 核心市场脉搏",
            "",
            "## 区域 Scoreboard",
            "",
            "## 代表性挂牌样本",
            "",
            "## 来源与交叉验证说明",
            "",
        ]
    else:
        normalized_area = normalize_area(area)
        if normalized_area is None:
            raise ValueError("area_deep_dive requires area")
        sections = [
            "一句话结论：",
            "",
            "## 摘要",
            "",
            "## 数据新鲜度说明",
            "",
            f"## {AREA_LABELS[normalized_area]} 市场背景",
            "",
            f"## {AREA_LABELS[normalized_area]} 买方观察",
            "",
            f"## {AREA_LABELS[normalized_area]} 代表性挂牌样本",
            "",
            "## 相对 Seattle Metro 的位置判断",
            "",
            "## 后续观察点",
            "",
            "## 来源与交叉验证说明",
            "",
        ]
    return "\n".join([f"# {title}", ""] + sections)


def _blank_area_scoreboard(areas: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "area": area,
            "label": AREA_LABELS[area],
            "median_sale_price": None,
            "median_list_price": None,
            "inventory_signal": "",
            "days_on_market": None,
            "sale_to_list": None,
            "price_drop_signal": "",
            "notes": "",
        }
        for area in areas
    ]


def _section(slug: str, heading: str) -> dict[str, Any]:
    return {"slug": slug, "heading": heading, "summary": "", "bullets": [], "evidence_links": []}


def build_json_scaffold(
    *,
    mode: str,
    report_date: date | None = None,
    area: str | None = None,
    is_first_report: bool = False,
) -> dict[str, Any]:
    day = report_date or current_local_date()
    payload: dict[str, Any] = {
        "version": 1,
        "mode": mode,
        "title": _report_title(mode=mode, day=day, area=area),
        "generated_at": datetime.now(UTC).isoformat(),
        "report_timezone": resolve_report_timezone_label(),
        "report_date": day.isoformat(),
        "is_first_report": is_first_report,
        "data_freshness_note": "",
        "region_scope": CORE_AREAS if mode != "area_deep_dive" else [normalize_area(area)],
        "summary": "",
        "key_takeaways": [],
        "rate_context": "",
        "metro_context": "",
        "area_scoreboard": _blank_area_scoreboard(CORE_AREAS if mode != "area_deep_dive" else [normalize_area(area)]),
        "sample_listings": [],
        "source_mix_note": "",
        "verification_note": "",
        "coverage_gaps": [],
        "confidence_flags": [],
        "sources": [],
    }
    if mode == "weekly_pulse":
        payload["period_start"] = (day - timedelta(days=6)).isoformat()
        payload["period_end"] = day.isoformat()
        payload["sections"] = [
            _section("rates", "利率与融资环境"),
            _section("metro-pulse", "Seattle Metro 核心市场脉搏"),
            _section("area-scoreboard", "区域 Scoreboard"),
            _section("area-observations", "分区域买方观察"),
            _section("sample-listings", "代表性挂牌样本"),
            _section("watchpoints", "后续观察点"),
        ]
        return payload
    if mode == "market_snapshot":
        payload["period_start"] = day.isoformat()
        payload["period_end"] = day.isoformat()
        payload["sections"] = [
            _section("rates", "利率与融资环境"),
            _section("metro-pulse", "Seattle Metro 核心市场脉搏"),
            _section("area-scoreboard", "区域 Scoreboard"),
            _section("sample-listings", "代表性挂牌样本"),
        ]
        return payload
    normalized_area = normalize_area(area)
    if normalized_area is None:
        raise ValueError("area_deep_dive requires area")
    payload["area_focus"] = normalized_area
    payload["sections"] = [
        _section("area-background", f"{AREA_LABELS[normalized_area]} 市场背景"),
        _section("buyer-observations", f"{AREA_LABELS[normalized_area]} 买方观察"),
        _section("sample-listings", f"{AREA_LABELS[normalized_area]} 代表性挂牌样本"),
        _section("metro-relative", "相对 Seattle Metro 的位置判断"),
        _section("watchpoints", "后续观察点"),
    ]
    return payload


def persist_report(
    *,
    mode: str,
    markdown: str,
    payload: dict[str, Any],
    root: str | Path | None = None,
    report_date: date | None = None,
    area: str | None = None,
) -> tuple[Path, Path]:
    resolved_day = report_date or current_local_date()
    md_path, json_path = build_report_paths(
        mode=mode,
        root=root,
        report_date=resolved_day,
        area=area,
    )
    normalized = dict(payload)
    normalized["version"] = 1
    normalized["mode"] = mode
    normalized["generated_at"] = datetime.now(UTC).isoformat()
    normalized["report_timezone"] = resolve_report_timezone_label()
    normalized["report_date"] = resolved_day.isoformat()
    normalized.setdefault("coverage_gaps", [])
    normalized.setdefault("confidence_flags", [])
    if mode == "area_deep_dive":
        normalized["area_focus"] = normalize_area(area)
    atomic_write_text(md_path, markdown)
    atomic_write_text(json_path, json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")
    return md_path, json_path


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"report JSON must contain an object: {path}")
    data["_path"] = str(path)
    return data


def _iter_json_files(mode: str, root: str | Path | None = None) -> list[Path]:
    store_root = resolve_reports_root(root)
    if mode == "weekly_pulse":
        return sorted((store_root / "weekly").glob("*.json"))
    if mode == "market_snapshot":
        return sorted((store_root / "snapshot").glob("*/*.json"))
    if mode == "area_deep_dive":
        return sorted((store_root / "areas").glob("*/*.json"))
    raise ValueError(f"unsupported mode: {mode}")


def list_reports(
    *,
    mode: str,
    area: str | None = None,
    root: str | Path | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    normalized_area = normalize_area(area)
    items: list[dict[str, Any]] = []
    for path in _iter_json_files(mode, root):
        try:
            data = _load_json(path)
        except Exception:
            continue
        if normalized_area is not None and data.get("area_focus") != normalized_area:
            continue
        items.append(data)
    items.sort(key=lambda item: item.get("report_date", ""), reverse=True)
    if limit is not None:
        return items[:limit]
    return items


def build_context(
    *,
    mode: str,
    root: str | Path | None = None,
    as_of: date | None = None,
    area: str | None = None,
    weekly_limit: int = 4,
    snapshot_limit: int = 3,
    area_limit: int = 3,
    baseline_days: int = DEFAULT_BASELINE_DAYS,
) -> dict[str, Any]:
    day = as_of or current_local_date()
    normalized_area = normalize_area(area)
    recent_weekly = list_reports(mode="weekly_pulse", root=root, limit=weekly_limit)
    recent_snapshots = list_reports(mode="market_snapshot", root=root, limit=snapshot_limit)
    context: dict[str, Any] = {
        "mode": mode,
        "as_of": day.isoformat(),
        "reports_root": str(resolve_reports_root(root)),
        "core_areas": CORE_AREAS,
        "optional_expansion_areas": OPTIONAL_AREAS,
        "recent_weekly": recent_weekly,
        "recent_snapshots": recent_snapshots,
    }
    if mode == "area_deep_dive":
        if normalized_area is None:
            raise ValueError("area_deep_dive requires --area")
        recent_area_reports = list_reports(mode="area_deep_dive", area=normalized_area, root=root, limit=area_limit)
        is_first_report = len(recent_area_reports) == 0
        context.update(
            {
                "area": normalized_area,
                "recent_area_reports": recent_area_reports,
                "is_first_report": is_first_report,
                "implicit_baseline_window_days": baseline_days if is_first_report else 0,
                "implicit_baseline_start_date": (
                    (day - timedelta(days=baseline_days - 1)).isoformat() if is_first_report else None
                ),
                "implicit_baseline_note": (
                    "No prior persisted area reports were found. Use a bounded recent public baseline and mark the report as first-run."
                    if is_first_report
                    else ""
                ),
            }
        )
        return context
    is_first_report = len(recent_weekly) == 0 and len(recent_snapshots) == 0
    context.update(
        {
            "region_scope": CORE_AREAS,
            "is_first_report": is_first_report,
            "implicit_baseline_window_days": baseline_days if is_first_report else 0,
            "implicit_baseline_start_date": (
                (day - timedelta(days=baseline_days - 1)).isoformat() if is_first_report else None
            ),
            "implicit_baseline_note": (
                "No prior persisted metro reports were found. Use the latest 1-2 months of public context as a first-run baseline and avoid fake week-over-week continuity."
                if is_first_report
                else ""
            ),
        }
    )
    return context


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persist and query Seattle metro housing watch reports")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold = subparsers.add_parser("scaffold", help="write a starter Markdown and JSON scaffold")
    scaffold.add_argument("--mode", required=True, choices=sorted(VALID_MODES))
    scaffold.add_argument("--area")
    scaffold.add_argument("--report-date")
    scaffold.add_argument("--root")
    scaffold.add_argument("--markdown-file", required=True)
    scaffold.add_argument("--json-file", required=True)

    persist = subparsers.add_parser("persist", help="persist report files into canonical storage")
    persist.add_argument("--mode", required=True, choices=sorted(VALID_MODES))
    persist.add_argument("--area")
    persist.add_argument("--report-date")
    persist.add_argument("--root")
    persist.add_argument("--markdown-file", required=True)
    persist.add_argument("--json-file", required=True)

    context_cmd = subparsers.add_parser("context", help="load prior report context as JSON")
    context_cmd.add_argument("--mode", required=True, choices=sorted(VALID_MODES))
    context_cmd.add_argument("--area")
    context_cmd.add_argument("--root")
    context_cmd.add_argument("--as-of")
    context_cmd.add_argument("--weekly-limit", type=int, default=4)
    context_cmd.add_argument("--snapshot-limit", type=int, default=3)
    context_cmd.add_argument("--area-limit", type=int, default=3)
    context_cmd.add_argument("--baseline-days", type=int, default=DEFAULT_BASELINE_DAYS)

    list_cmd = subparsers.add_parser("list", help="list stored reports")
    list_cmd.add_argument("--mode", required=True, choices=sorted(VALID_MODES))
    list_cmd.add_argument("--area")
    list_cmd.add_argument("--root")
    list_cmd.add_argument("--limit", type=int)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "scaffold":
        report_day = parse_report_date(args.report_date)
        context = build_context(
            mode=args.mode,
            root=args.root,
            as_of=report_day,
            area=args.area,
        )
        markdown = build_markdown_skeleton(
            mode=args.mode,
            report_date=report_day,
            area=args.area,
        )
        payload = build_json_scaffold(
            mode=args.mode,
            report_date=report_day,
            area=args.area,
            is_first_report=bool(context.get("is_first_report", False)),
        )
        payload["data_freshness_note"] = str(context.get("implicit_baseline_note", ""))
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
            area=args.area,
        )
        print(
            json.dumps(
                {
                    "status": "ok",
                    "markdown_path": str(md_path),
                    "json_path": str(json_path),
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "context":
        context = build_context(
            mode=args.mode,
            root=args.root,
            as_of=parse_report_date(args.as_of),
            area=args.area,
            weekly_limit=args.weekly_limit,
            snapshot_limit=args.snapshot_limit,
            area_limit=args.area_limit,
            baseline_days=args.baseline_days,
        )
        print(json.dumps(context, ensure_ascii=False, indent=2))
        return 0

    if args.command == "list":
        items = list_reports(
            mode=args.mode,
            area=args.area,
            root=args.root,
            limit=args.limit,
        )
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0

    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
