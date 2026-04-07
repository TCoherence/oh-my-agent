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


VALID_MODES = {"daily_scan", "weekly_digest"}
SOURCES = {"credit-cards", "uscardforum", "rakuten", "slickdeals", "dealmoon"}
WEEKLY_SOURCE = "all-sources"
DAILY_SUMMARY_SOURCE = "summary"
DAILY_REFERENCE_DIR = "references"
COVERAGE_FLOOR = 10


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


def resolve_reports_root(root: str | Path | None = None) -> Path:
    base = Path(root).expanduser() if root is not None else Path.home() / ".oh-my-agent" / "reports" / "deals-scanner"
    return base.resolve()


def parse_report_date(raw: str | None) -> date | None:
    if raw is None or not str(raw).strip():
        return None
    return date.fromisoformat(raw)


def iso_week_for_date(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def _validate_mode_source(mode: str, source: str) -> None:
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    if mode == "daily_scan":
        if source == WEEKLY_SOURCE:
            raise ValueError("daily_scan does not accept 'all-sources'; use a specific source")
        if source not in SOURCES and source != DAILY_SUMMARY_SOURCE:
            raise ValueError(
                "unsupported source for daily_scan: "
                f"{source}; expected one of {sorted(SOURCES | {DAILY_SUMMARY_SOURCE})}"
            )
    elif mode == "weekly_digest":
        if source != WEEKLY_SOURCE:
            raise ValueError(f"weekly_digest requires source='all-sources', got '{source}'")


def build_report_paths(
    *,
    mode: str,
    source: str,
    root: str | Path | None = None,
    report_date: date | None = None,
    iso_week: str | None = None,
) -> tuple[Path, Path]:
    _validate_mode_source(mode, source)
    store_root = resolve_reports_root(root)

    if mode == "weekly_digest":
        week_label = iso_week or iso_week_for_date(report_date or current_local_date())
        md_path = store_root / "weekly" / week_label / f"{WEEKLY_SOURCE}.md"
        json_path = store_root / "weekly" / week_label / f"{WEEKLY_SOURCE}.json"
        return md_path, json_path

    day = report_date or current_local_date()
    day_label = day.isoformat()
    if source == DAILY_SUMMARY_SOURCE:
        md_path = store_root / "daily" / day_label / f"{DAILY_SUMMARY_SOURCE}.md"
        json_path = store_root / "daily" / day_label / f"{DAILY_SUMMARY_SOURCE}.json"
        return md_path, json_path
    md_path = store_root / "daily" / day_label / DAILY_REFERENCE_DIR / f"{source}.md"
    json_path = store_root / "daily" / day_label / DAILY_REFERENCE_DIR / f"{source}.json"
    return md_path, json_path


def _daily_reference_pairs() -> list[tuple[str, str]]:
    return [
        ("credit-cards", "信用卡优惠"),
        ("uscardforum", "美卡论坛"),
        ("rakuten", "Rakuten 返现"),
        ("slickdeals", "Slickdeals"),
        ("dealmoon", "Dealmoon"),
    ]


def _ordered_sources() -> list[str]:
    return [source for source, _ in _daily_reference_pairs()]


def _daily_reference_index_lines() -> list[str]:
    return [
        f"- [{label}]({DAILY_REFERENCE_DIR}/{source}.md)"
        for source, label in _daily_reference_pairs()
    ]


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


# ---------------------------------------------------------------------------
# Markdown skeletons
# ---------------------------------------------------------------------------

def build_markdown_skeleton(*, mode: str, source: str, report_date: date | None = None, iso_week: str | None = None) -> str:
    _validate_mode_source(mode, source)
    day_label = (report_date or current_local_date()).isoformat()
    week_label = iso_week or iso_week_for_date(report_date or current_local_date())

    if mode == "daily_scan" and source == DAILY_SUMMARY_SOURCE:
        return "\n".join([
            f"# 优惠扫描总览｜{day_label}",
            "",
            "一句话结论：",
            "",
            "## 今日判断",
            "",
            "## Apply now",
            "",
            "## Buy now",
            "",
            "## Stack now",
            "",
            "## Watchlist",
            "",
            "## 各渠道一句话结论",
            "",
            "### 信用卡优惠",
            "",
            "### 美卡论坛",
            "",
            "### Rakuten 返现",
            "",
            "### Slickdeals",
            "",
            "### Dealmoon",
            "",
            "## Coverage / Confidence",
            "",
            "## Reference 索引",
            *_daily_reference_index_lines(),
            "",
            "## 来源与说明",
            "",
        ])

    if mode == "daily_scan" and source == "credit-cards":
        return "\n".join([
            f"# 信用卡优惠日报｜{day_label}",
            "",
            "一句话结论：",
            "",
            "## 摘要",
            "",
            "## 开卡奖励（Sign-up Bonuses）",
            "",
            "## 消费返现与积分活动",
            "",
            "## 年费减免与升降级优惠",
            "",
            "## 即将到期的优惠",
            "",
            "## 来源与说明",
            "",
        ])

    if mode == "daily_scan" and source == "uscardforum":
        return "\n".join([
            f"# 美卡论坛日报｜{day_label}",
            "",
            "一句话结论：",
            "",
            "## 摘要",
            "",
            "## 热门讨论与数据点",
            "",
            "## 开卡审批经验",
            "",
            "## 积分兑换策略",
            "",
            "## 银行政策变动",
            "",
            "## 来源与说明",
            "",
        ])

    if mode == "daily_scan" and source == "rakuten":
        return "\n".join([
            f"# Rakuten 返现日报｜{day_label}",
            "",
            "一句话结论：",
            "",
            "## 摘要",
            "",
            "## 今日高返现商家",
            "",
            "## 限时闪购返现",
            "",
            "## 新上线商家与活动",
            "",
            "## 值得关注的叠加策略",
            "",
            "## 来源与说明",
            "",
        ])

    if mode == "daily_scan" and source == "slickdeals":
        return "\n".join([
            f"# Slickdeals 精选日报｜{day_label}",
            "",
            "一句话结论：",
            "",
            "## 摘要",
            "",
            "## 今日热门 Frontpage 优惠",
            "",
            "## 电子产品与科技",
            "",
            "## 家居生活与日用",
            "",
            "## 服饰 / 户外 / 其他值得关注",
            "",
            "## 来源与说明",
            "",
        ])

    if mode == "daily_scan" and source == "dealmoon":
        return "\n".join([
            f"# 北美省钱快报日报｜{day_label}",
            "",
            "一句话结论：",
            "",
            "## 摘要",
            "",
            "## 今日精选折扣",
            "",
            "## 独家折扣码与优惠",
            "",
            "## 美妆个护",
            "",
            "## 电子数码",
            "",
            "## 家居生活",
            "",
            "## 来源与说明",
            "",
        ])

    if mode == "weekly_digest" and source == WEEKLY_SOURCE:
        return "\n".join([
            f"# 优惠情报周报｜{week_label}",
            "",
            "一句话结论：",
            "",
            "## 本周总览",
            "",
            "## 信用卡优惠回顾",
            "",
            "## 美卡论坛回顾",
            "",
            "## Rakuten 返现回顾",
            "",
            "## Slickdeals 热门回顾",
            "",
            "## Dealmoon 精选回顾",
            "",
            "## 跨渠道策略与趋势",
            "",
            "## 下周关注点",
            "",
            "## 来源与说明",
            "",
        ])

    raise ValueError(f"unsupported mode/source combination: {mode}/{source}")


# ---------------------------------------------------------------------------
# JSON scaffolds
# ---------------------------------------------------------------------------

def _base_json_payload(*, mode: str, source: str, report_date: date | None = None) -> dict[str, Any]:
    day = report_date or current_local_date()
    report_timezone = resolve_report_timezone_name() or str(resolve_report_timezone())
    return {
        "version": 1,
        "mode": mode,
        "source": source,
        "title": "",
        "generated_at": datetime.now(UTC).isoformat(),
        "report_timezone": report_timezone,
        "report_date": day.isoformat(),
        "period_start": day.isoformat(),
        "period_end": day.isoformat(),
        "summary": "",
        "top_deals": [],
        "source_mix_note": "",
        "sources": [],
        "sections": [],
        "lower_confidence_watchlist": [],
        "high_confidence_count": 0,
        "coverage_floor_met": False,
    }


def build_json_scaffold(
    *,
    mode: str,
    source: str,
    report_date: date | None = None,
    iso_week: str | None = None,
) -> dict[str, Any]:
    _validate_mode_source(mode, source)
    payload = _base_json_payload(mode=mode, source=source, report_date=report_date)

    if mode == "daily_scan" and source == DAILY_SUMMARY_SOURCE:
        payload["reference_reports"] = [
            {
                "source": item_source,
                "label": label,
                "markdown_path": f"{DAILY_REFERENCE_DIR}/{item_source}.md",
                "json_path": f"{DAILY_REFERENCE_DIR}/{item_source}.json",
            }
            for item_source, label in _daily_reference_pairs()
        ]
        payload["action_buckets"] = {
            "apply_now": [],
            "buy_now": [],
            "stack_now": [],
            "watchlist": [],
        }
        payload["source_snapshots"] = [
            {
                "source": item_source,
                "summary": "",
                "high_confidence_count": 0,
                "watchlist_count": 0,
                "met_floor": False,
            }
            for item_source in _ordered_sources()
        ]
        payload["coverage_status"] = {
            "target_floor": COVERAGE_FLOOR,
            "sources_below_floor": [],
        }
        payload["sections"] = [
            {"slug": "judgement", "heading": "今日判断", "summary": "", "deals": []},
            {"slug": "apply-now", "heading": "Apply now", "summary": "", "deals": []},
            {"slug": "buy-now", "heading": "Buy now", "summary": "", "deals": []},
            {"slug": "stack-now", "heading": "Stack now", "summary": "", "deals": []},
            {"slug": "watchlist", "heading": "Watchlist", "summary": "", "deals": []},
            {"slug": "source-snapshots", "heading": "各渠道一句话结论", "summary": "", "deals": []},
            {"slug": "coverage-confidence", "heading": "Coverage / Confidence", "summary": "", "deals": []},
            {"slug": "reference-index", "heading": "Reference 索引", "summary": "", "deals": []},
        ]
        return payload

    if mode == "daily_scan" and source == "credit-cards":
        payload["sections"] = [
            {"slug": "signup-bonuses", "heading": "开卡奖励（Sign-up Bonuses）", "summary": "", "deals": []},
            {"slug": "cashback-rewards", "heading": "消费返现与积分活动", "summary": "", "deals": []},
            {"slug": "fee-offers", "heading": "年费减免与升降级优惠", "summary": "", "deals": []},
            {"slug": "expiring", "heading": "即将到期的优惠", "summary": "", "deals": []},
        ]
        return payload

    if mode == "daily_scan" and source == "uscardforum":
        payload["sections"] = [
            {"slug": "hot-discussions", "heading": "热门讨论与数据点", "summary": "", "deals": []},
            {"slug": "approval-experience", "heading": "开卡审批经验", "summary": "", "deals": []},
            {"slug": "redemption-strategy", "heading": "积分兑换策略", "summary": "", "deals": []},
            {"slug": "bank-policy", "heading": "银行政策变动", "summary": "", "deals": []},
        ]
        return payload

    if mode == "daily_scan" and source == "rakuten":
        payload["sections"] = [
            {"slug": "high-cashback", "heading": "今日高返现商家", "summary": "", "deals": []},
            {"slug": "flash-deals", "heading": "限时闪购返现", "summary": "", "deals": []},
            {"slug": "new-merchants", "heading": "新上线商家与活动", "summary": "", "deals": []},
            {"slug": "stacking", "heading": "值得关注的叠加策略", "summary": "", "deals": []},
        ]
        return payload

    if mode == "daily_scan" and source == "slickdeals":
        payload["sections"] = [
            {"slug": "frontpage", "heading": "今日热门 Frontpage 优惠", "summary": "", "deals": []},
            {"slug": "tech", "heading": "电子产品与科技", "summary": "", "deals": []},
            {"slug": "home-living", "heading": "家居生活与日用", "summary": "", "deals": []},
            {"slug": "broader-mix", "heading": "服饰 / 户外 / 其他值得关注", "summary": "", "deals": []},
        ]
        return payload

    if mode == "daily_scan" and source == "dealmoon":
        payload["sections"] = [
            {"slug": "top-picks", "heading": "今日精选折扣", "summary": "", "deals": []},
            {"slug": "exclusive-codes", "heading": "独家折扣码与优惠", "summary": "", "deals": []},
            {"slug": "beauty", "heading": "美妆个护", "summary": "", "deals": []},
            {"slug": "electronics", "heading": "电子数码", "summary": "", "deals": []},
            {"slug": "home-living", "heading": "家居生活", "summary": "", "deals": []},
        ]
        return payload

    if mode == "weekly_digest" and source == WEEKLY_SOURCE:
        day = report_date or current_local_date()
        payload["iso_week"] = iso_week or iso_week_for_date(day)
        payload["trend_summary"] = ""
        payload["cross_source_highlights"] = []
        payload["sections"] = [
            {"slug": "overview", "heading": "本周总览", "summary": "", "deals": []},
            {"slug": "credit-cards", "heading": "信用卡优惠回顾", "summary": "", "deals": []},
            {"slug": "uscardforum", "heading": "美卡论坛回顾", "summary": "", "deals": []},
            {"slug": "rakuten", "heading": "Rakuten 返现回顾", "summary": "", "deals": []},
            {"slug": "slickdeals", "heading": "Slickdeals 热门回顾", "summary": "", "deals": []},
            {"slug": "dealmoon", "heading": "Dealmoon 精选回顾", "summary": "", "deals": []},
            {"slug": "cross-source", "heading": "跨渠道策略与趋势", "summary": "", "deals": []},
            {"slug": "watchlist", "heading": "下周关注点", "summary": "", "deals": []},
        ]
        start = day - timedelta(days=6)
        payload["period_start"] = start.isoformat()
        return payload

    raise ValueError(f"unsupported mode/source combination: {mode}/{source}")


# ---------------------------------------------------------------------------
# Persist
# ---------------------------------------------------------------------------

def persist_report(
    *,
    mode: str,
    source: str,
    markdown: str,
    payload: dict[str, Any],
    root: str | Path | None = None,
    report_date: date | None = None,
    iso_week: str | None = None,
) -> tuple[Path, Path]:
    md_path, json_path = build_report_paths(
        mode=mode,
        source=source,
        root=root,
        report_date=report_date,
        iso_week=iso_week,
    )
    normalized = dict(payload)
    effective_date = (
        report_date
        or parse_report_date(str(normalized.get("report_date") or normalized.get("period_end") or ""))
        or current_local_date()
    )
    normalized.setdefault("version", 1)
    normalized["mode"] = mode
    normalized["source"] = source
    normalized["generated_at"] = datetime.now(UTC).isoformat()
    normalized["report_timezone"] = resolve_report_timezone_name() or str(resolve_report_timezone())
    normalized["report_date"] = effective_date.isoformat()
    if mode == "weekly_digest":
        normalized["iso_week"] = iso_week or iso_week_for_date(effective_date)
    atomic_write_text(md_path, markdown)
    atomic_write_text(json_path, json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")
    return md_path, json_path


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"report JSON must contain an object: {path}")
    data["_path"] = str(path)
    return data


def _iter_json_files(mode: str, root: str | Path | None = None) -> list[Path]:
    store_root = resolve_reports_root(root)
    if mode == "daily_scan":
        return sorted(
            list((store_root / "daily").glob(f"*/{DAILY_SUMMARY_SOURCE}.json"))
            + list((store_root / "daily").glob(f"*/{DAILY_REFERENCE_DIR}/*.json"))
        )
    if mode == "weekly_digest":
        return sorted((store_root / "weekly").glob("*/*.json"))
    raise ValueError(f"unsupported mode: {mode}")


def list_reports(
    *,
    mode: str,
    source: str | None = None,
    root: str | Path | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        json_files = _iter_json_files(mode, root)
    except (ValueError, OSError):
        return items
    for path in json_files:
        try:
            data = _load_json(path)
        except Exception:
            continue
        if source is not None and data.get("source") != source:
            continue
        items.append(data)
    if mode == "daily_scan":
        items.sort(key=lambda item: item.get("period_end", ""), reverse=True)
    else:
        items.sort(key=lambda item: item.get("iso_week", ""), reverse=True)
    if limit is not None:
        return items[:limit]
    return items


def load_recent_daily_reports(
    source: str,
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
        for item in list_reports(mode="daily_scan", source=source, root=root)
        if start_label <= str(item.get("period_end", "")) <= end_label
    ]
    items.sort(key=lambda item: item.get("period_end", ""))
    return items


def load_recent_weekly_reports(
    *,
    root: str | Path | None = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    items = list_reports(mode="weekly_digest", source=WEEKLY_SOURCE, root=root, limit=limit)
    items.sort(key=lambda item: item.get("iso_week", ""))
    return items


def load_daily_reference_reports(
    *,
    root: str | Path | None = None,
    report_date: date | None = None,
) -> dict[str, dict[str, Any]]:
    day = (report_date or current_local_date()).isoformat()
    items: dict[str, dict[str, Any]] = {}
    for source in sorted(SOURCES):
        matches = [
            item
            for item in list_reports(mode="daily_scan", source=source, root=root)
            if item.get("period_end") == day
        ]
        if matches:
            items[source] = matches[0]
    return items


def build_context(
    *,
    mode: str,
    source: str,
    root: str | Path | None = None,
    as_of: date | None = None,
    days: int = 7,
    weekly_limit: int = 4,
) -> dict[str, Any]:
    _validate_mode_source(mode, source)
    day = as_of or current_local_date()
    context: dict[str, Any] = {
        "mode": mode,
        "source": source,
        "as_of": day.isoformat(),
        "reports_root": str(resolve_reports_root(root)),
    }
    if mode == "daily_scan":
        if source == DAILY_SUMMARY_SOURCE:
            context["current_references"] = load_daily_reference_reports(root=root, report_date=day)
            context["recent_summary"] = load_recent_daily_reports(
                DAILY_SUMMARY_SOURCE,
                root=root,
                as_of=day,
                days=days,
            )
            context["recent_weekly"] = load_recent_weekly_reports(root=root, limit=min(weekly_limit, 2))
            return context
        context["recent_daily"] = load_recent_daily_reports(source, root=root, as_of=day, days=days)
        context["recent_weekly"] = load_recent_weekly_reports(root=root, limit=min(weekly_limit, 2))
        return context
    if mode == "weekly_digest":
        context["recent_daily"] = {
            s: load_recent_daily_reports(s, root=root, as_of=day, days=days)
            for s in sorted(SOURCES)
        }
        context["recent_weekly"] = load_recent_weekly_reports(root=root, limit=weekly_limit)
        return context
    raise ValueError(f"unsupported mode: {mode}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persist and query deals-scanner report files")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold = subparsers.add_parser("scaffold", help="write a starter Markdown and JSON scaffold")
    scaffold.add_argument("--mode", required=True, choices=sorted(VALID_MODES))
    scaffold.add_argument("--source", required=True)
    scaffold.add_argument("--report-date")
    scaffold.add_argument("--iso-week")
    scaffold.add_argument("--markdown-file", required=True)
    scaffold.add_argument("--json-file", required=True)

    persist = subparsers.add_parser("persist", help="persist report files into canonical storage")
    persist.add_argument("--mode", required=True, choices=sorted(VALID_MODES))
    persist.add_argument("--source", required=True)
    persist.add_argument("--report-date")
    persist.add_argument("--iso-week")
    persist.add_argument("--root")
    persist.add_argument("--markdown-file", required=True)
    persist.add_argument("--json-file", required=True)

    context_cmd = subparsers.add_parser("context", help="load prior report context as JSON")
    context_cmd.add_argument("--mode", required=True, choices=sorted(VALID_MODES))
    context_cmd.add_argument("--source", required=True)
    context_cmd.add_argument("--root")
    context_cmd.add_argument("--as-of")
    context_cmd.add_argument("--days", type=int, default=7)
    context_cmd.add_argument("--weekly-limit", type=int, default=4)

    list_cmd = subparsers.add_parser("list", help="list stored reports")
    list_cmd.add_argument("--mode", required=True, choices=sorted(VALID_MODES))
    list_cmd.add_argument("--source")
    list_cmd.add_argument("--root")
    list_cmd.add_argument("--limit", type=int)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "scaffold":
        report_day = parse_report_date(args.report_date)
        markdown = build_markdown_skeleton(
            mode=args.mode,
            source=args.source,
            report_date=report_day,
            iso_week=args.iso_week,
        )
        payload = build_json_scaffold(
            mode=args.mode,
            source=args.source,
            report_date=report_day,
            iso_week=args.iso_week,
        )
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
            source=args.source,
            markdown=markdown,
            payload=payload,
            root=args.root,
            report_date=report_day,
            iso_week=args.iso_week,
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
            source=args.source,
            root=args.root,
            as_of=parse_report_date(args.as_of),
            days=args.days,
            weekly_limit=args.weekly_limit,
        )
        print(json.dumps(context, ensure_ascii=False, indent=2))
        return 0

    if args.command == "list":
        items = list_reports(
            mode=args.mode,
            source=args.source,
            root=args.root,
            limit=args.limit,
        )
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0

    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
