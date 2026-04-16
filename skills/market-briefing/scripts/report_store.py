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


VALID_MODES = {"bootstrap_backfill", "daily_digest", "weekly_synthesis"}
DOMAINS = {"politics", "finance", "ai"}
WEEKLY_DOMAIN = "cross-domain"
TRACKED_UNIVERSE = ["NVDA", "MSFT", "AAPL", "AMZN", "GOOG", "TSLA", "META", "VOO", "SPY", "S&P 500"]
HOLDINGS_WINDOW_DAYS = 7
REPORTS_ROOT_NAME = "market-briefing"


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


def iso_week_for_date(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def build_report_paths(
    *,
    mode: str,
    domain: str,
    root: str | Path | None = None,
    report_date: date | None = None,
    iso_week: str | None = None,
) -> tuple[Path, Path]:
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    store_root = resolve_reports_root(root)

    if mode == "weekly_synthesis":
        if domain != WEEKLY_DOMAIN:
            raise ValueError("weekly_synthesis requires domain='cross-domain'")
        week_label = iso_week or iso_week_for_date(report_date or current_local_date())
        md_path = store_root / "weekly" / week_label / f"{WEEKLY_DOMAIN}.md"
        json_path = store_root / "weekly" / week_label / f"{WEEKLY_DOMAIN}.json"
        return md_path, json_path

    if domain not in DOMAINS:
        raise ValueError(f"unsupported domain for {mode}: {domain}")

    day = report_date or current_local_date()
    day_label = day.isoformat()
    if mode == "bootstrap_backfill":
        md_path = store_root / "bootstrap" / domain / f"{day_label}.md"
        json_path = store_root / "bootstrap" / domain / f"{day_label}.json"
        return md_path, json_path
    if mode == "daily_digest":
        md_path = store_root / "daily" / day_label / f"{domain}.md"
        json_path = store_root / "daily" / day_label / f"{domain}.json"
        return md_path, json_path
    raise ValueError(f"unsupported mode: {mode}")


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


def build_markdown_skeleton(*, mode: str, domain: str, report_date: date | None = None, iso_week: str | None = None) -> str:
    day_label = (report_date or current_local_date()).isoformat()
    week_label = iso_week or iso_week_for_date(report_date or current_local_date())
    if mode == "bootstrap_backfill":
        if domain not in DOMAINS:
            raise ValueError(f"unsupported bootstrap domain: {domain}")
        return "\n".join(
            [
                f"# {domain} bootstrap dossier｜{day_label}",
                "",
                "一句话结论：",
                "",
                "## 范围与时间窗",
                "",
                "## 结构性主线",
                "",
                "## 关键事件与信号",
                "",
                "## 当前状态判断",
                "",
                "## 后续跟踪清单",
                "",
                "## 来源与交叉验证说明",
                "",
            ]
        )
    if mode == "daily_digest" and domain == "politics":
        return "\n".join(
            [
                f"# 政治日报｜{day_label}",
                "",
                "一句话结论：",
                "",
                "## 摘要",
                "",
                "## 中国中央政策与决策信号",
                "",
                "## 美国联邦政策与决策信号",
                "",
                "## 中美与地缘政治动态",
                "",
                "## 影响判断与后续观察点",
                "",
                "## 来源与交叉验证说明",
                "",
            ]
        )
    if mode == "daily_digest" and domain == "finance":
        return "\n".join(
            [
                f"# 财经日报｜{day_label}",
                "",
                "一句话结论：",
                "",
                "## 摘要",
                "",
                "## 中国宏观与政策",
                "",
                "## 美国宏观与政策",
                "",
                "## 美国市场波动与风险偏好",
                "",
                "## 中国 / 香港市场脉搏",
                "",
                "## 中国房地产政策与融资信号",
                "",
                "## 重点持仓财报 / 管理层表态 / CEO 公开发言",
                "",
                "## 市场与指数基金视角",
                "",
                "## 后续观察点",
                "",
                "## 来源与交叉验证说明",
                "",
            ]
        )
    if mode == "daily_digest" and domain == "ai":
        return "\n".join(
            [
                f"# AI 日报｜{day_label}",
                "",
                "一句话结论：",
                "",
                "## 摘要",
                "",
                "## Frontier Labs / Frontier Model Radar",
                "",
                "## 关键人物与社区信号",
                "",
                "## Energy",
                "",
                "## Chips",
                "",
                "## Infra",
                "",
                "## Model",
                "",
                "## Application",
                "",
                "## 层间联动影响",
                "",
                "## 🎙️ 播客动态",
                "",
                "## 候选池变化与后续关注",
                "",
                "## 来源与交叉验证说明",
                "",
            ]
        )
    if mode == "weekly_synthesis" and domain == WEEKLY_DOMAIN:
        return "\n".join(
            [
                f"# 市场情报周报｜{week_label}",
                "",
                "一句话结论：",
                "",
                "## 本周总览",
                "",
                "## 政治主线",
                "",
                "## 财经主线",
                "",
                "## AI 五层演进",
                "",
                "## 跨域联动与结构性趋势",
                "",
                "## 下周观察点",
                "",
                "## 来源与交叉验证说明",
                "",
            ]
        )
    raise ValueError(f"unsupported mode/domain combination: {mode}/{domain}")


def build_json_scaffold(
    *,
    mode: str,
    domain: str,
    report_date: date | None = None,
    iso_week: str | None = None,
) -> dict[str, Any]:
    day = report_date or current_local_date()
    payload: dict[str, Any] = {
        "version": 1,
        "mode": mode,
        "domain": domain,
        "title": "",
        "generated_at": datetime.now(UTC).isoformat(),
        "report_timezone": resolve_report_timezone_label(),
        "report_date": day.isoformat(),
        "period_start": day.isoformat(),
        "period_end": day.isoformat(),
        "summary": "",
        "key_takeaways": [],
        "source_mix_note": "",
        "verification_note": "",
        "coverage_gaps": [],
        "confidence_flags": [],
        "sources": [],
        "sections": [],
    }
    if mode == "bootstrap_backfill":
        payload["lookback_days"] = None
        payload["sections"] = [
            {"slug": "scope", "heading": "范围与时间窗", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "themes", "heading": "结构性主线", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "signals", "heading": "关键事件与信号", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "assessment", "heading": "当前状态判断", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "watchlist", "heading": "后续跟踪清单", "summary": "", "bullets": [], "evidence_links": []},
        ]
        return payload
    if mode == "daily_digest" and domain == "politics":
        payload["sections"] = [
            {"slug": "cn-policy", "heading": "中国中央政策与决策信号", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "us-policy", "heading": "美国联邦政策与决策信号", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "geopolitics", "heading": "中美与地缘政治动态", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "watchlist", "heading": "影响判断与后续观察点", "summary": "", "bullets": [], "evidence_links": []},
        ]
        return payload
    if mode == "daily_digest" and domain == "finance":
        payload["tracked_universe"] = TRACKED_UNIVERSE
        payload["holdings_window_days"] = HOLDINGS_WINDOW_DAYS
        payload["china_macro_policy_summary"] = ""
        payload["us_macro_policy_summary"] = ""
        payload["us_market_volatility_view"] = ""
        payload["china_market_pulse"] = ""
        payload["china_property_policy_view"] = ""
        payload["market_index_view"] = ""
        payload["sections"] = [
            {"slug": "cn-macro-policy", "heading": "中国宏观与政策", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "us-macro-policy", "heading": "美国宏观与政策", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "us-market-volatility", "heading": "美国市场波动与风险偏好", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "china-market-pulse", "heading": "中国 / 香港市场脉搏", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "china-property-policy", "heading": "中国房地产政策与融资信号", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "tracked-holdings", "heading": "重点持仓财报 / 管理层 / CEO 公开发言", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "market-index-view", "heading": "市场与指数基金视角", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "watchlist", "heading": "后续观察点", "summary": "", "bullets": [], "evidence_links": []},
        ]
        return payload
    if mode == "daily_digest" and domain == "ai":
        payload["tracked_people_groups"] = [
            "claude-code-builders",
            "openai-builders",
            "oss-ai-builders",
            "ai-generalists",
        ]
        payload["tracked_people"] = []
        payload["frontier_lab_watch"] = ["OpenAI", "Anthropic", "Google DeepMind", "Meta", "xAI", "Mistral", "Qwen", "DeepSeek"]
        payload["frontier_signal_summary"] = ""
        payload["unverified_frontier_signals"] = []
        payload["people_signal_summary"] = ""
        payload["new_candidate_people"] = []
        payload["promoted_people"] = []
        payload["candidate_queue_summary"] = ""
        payload["sections"] = [
            {"slug": "frontier-radar", "heading": "Frontier Labs / Frontier Model Radar", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "people-signals", "heading": "关键人物与社区信号", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "energy", "heading": "Energy", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "chips", "heading": "Chips", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "infra", "heading": "Infra", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "model", "heading": "Model", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "application", "heading": "Application", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "cross-layer", "heading": "层间联动影响", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "podcasts", "heading": "🎙️ 播客动态", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "candidate-queue", "heading": "候选池变化与后续关注", "summary": "", "bullets": [], "evidence_links": []},
        ]
        return payload
    if mode == "weekly_synthesis" and domain == WEEKLY_DOMAIN:
        payload["iso_week"] = iso_week or iso_week_for_date(day)
        payload["trend_summary"] = ""
        payload["cross_domain_links"] = []
        payload["sections"] = [
            {"slug": "overview", "heading": "本周总览", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "politics", "heading": "政治主线", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "finance", "heading": "财经主线", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "ai", "heading": "AI 五层演进", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "cross-domain", "heading": "跨域联动与结构性趋势", "summary": "", "bullets": [], "evidence_links": []},
            {"slug": "watchlist", "heading": "下周观察点", "summary": "", "bullets": [], "evidence_links": []},
        ]
        start = day - timedelta(days=6)
        payload["period_start"] = start.isoformat()
        return payload
    raise ValueError(f"unsupported mode/domain combination: {mode}/{domain}")


def persist_report(
    *,
    mode: str,
    domain: str,
    markdown: str,
    payload: dict[str, Any],
    root: str | Path | None = None,
    report_date: date | None = None,
    iso_week: str | None = None,
) -> tuple[Path, Path]:
    resolved_day = report_date or current_local_date()
    md_path, json_path = build_report_paths(
        mode=mode,
        domain=domain,
        root=root,
        report_date=resolved_day,
        iso_week=iso_week,
    )
    normalized = dict(payload)
    normalized["version"] = 1
    normalized["mode"] = mode
    normalized["domain"] = domain
    normalized["generated_at"] = datetime.now(UTC).isoformat()
    normalized["report_timezone"] = resolve_report_timezone_label()
    normalized["report_date"] = resolved_day.isoformat()
    normalized.setdefault("coverage_gaps", [])
    normalized.setdefault("confidence_flags", [])
    if mode == "weekly_synthesis":
        normalized.setdefault("iso_week", iso_week or iso_week_for_date(report_date or current_local_date()))
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
    if mode == "bootstrap_backfill":
        return sorted((store_root / "bootstrap").glob("*/*.json"))
    if mode == "daily_digest":
        return sorted((store_root / "daily").glob("*/*.json"))
    if mode == "weekly_synthesis":
        return sorted((store_root / "weekly").glob("*/*.json"))
    raise ValueError(f"unsupported mode: {mode}")


def list_reports(
    *,
    mode: str,
    domain: str | None = None,
    root: str | Path | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in _iter_json_files(mode, root):
        try:
            data = _load_json(path)
        except Exception:
            continue
        if domain is not None and data.get("domain") != domain:
            continue
        items.append(data)
    if mode in {"bootstrap_backfill", "daily_digest"}:
        items.sort(key=lambda item: item.get("period_end", ""), reverse=True)
    else:
        items.sort(key=lambda item: item.get("iso_week", ""), reverse=True)
    if limit is not None:
        return items[:limit]
    return items


def load_latest_bootstrap(domain: str, *, root: str | Path | None = None, as_of: date | None = None) -> dict[str, Any] | None:
    as_of_label = (as_of or current_local_date()).isoformat()
    for item in list_reports(mode="bootstrap_backfill", domain=domain, root=root):
        if str(item.get("period_end", "")) <= as_of_label:
            return item
    return None


def load_recent_daily_reports(
    domain: str,
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
        for item in list_reports(mode="daily_digest", domain=domain, root=root)
        if start_label <= str(item.get("period_end", "")) <= end_label
    ]
    items.sort(key=lambda item: item.get("period_end", ""))
    return items


def load_recent_weekly_reports(
    *,
    root: str | Path | None = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    items = list_reports(mode="weekly_synthesis", domain=WEEKLY_DOMAIN, root=root, limit=limit)
    items.sort(key=lambda item: item.get("iso_week", ""))
    return items


def build_context(
    *,
    mode: str,
    domain: str,
    root: str | Path | None = None,
    as_of: date | None = None,
    days: int = 7,
    weekly_limit: int = 4,
) -> dict[str, Any]:
    day = as_of or current_local_date()
    context: dict[str, Any] = {
        "mode": mode,
        "domain": domain,
        "as_of": day.isoformat(),
        "reports_root": str(resolve_reports_root(root)),
    }
    if mode == "bootstrap_backfill":
        return context
    if mode == "daily_digest":
        context["latest_bootstrap"] = load_latest_bootstrap(domain, root=root, as_of=day)
        context["recent_daily"] = load_recent_daily_reports(domain, root=root, as_of=day, days=days)
        context["recent_weekly"] = load_recent_weekly_reports(root=root, limit=min(weekly_limit, 2))
        return context
    if mode == "weekly_synthesis":
        context["bootstrap"] = {
            item_domain: load_latest_bootstrap(item_domain, root=root, as_of=day)
            for item_domain in sorted(DOMAINS)
        }
        context["recent_daily"] = {
            item_domain: load_recent_daily_reports(item_domain, root=root, as_of=day, days=days)
            for item_domain in sorted(DOMAINS)
        }
        context["recent_weekly"] = load_recent_weekly_reports(root=root, limit=weekly_limit)
        return context
    raise ValueError(f"unsupported mode: {mode}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Persist and query market briefing report files")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold = subparsers.add_parser("scaffold", help="write a starter Markdown and JSON scaffold")
    scaffold.add_argument("--mode", required=True, choices=sorted(VALID_MODES))
    scaffold.add_argument("--domain", required=True)
    scaffold.add_argument("--report-date")
    scaffold.add_argument("--iso-week")
    scaffold.add_argument("--markdown-file", required=True)
    scaffold.add_argument("--json-file", required=True)

    persist = subparsers.add_parser("persist", help="persist report files into canonical storage")
    persist.add_argument("--mode", required=True, choices=sorted(VALID_MODES))
    persist.add_argument("--domain", required=True)
    persist.add_argument("--report-date")
    persist.add_argument("--iso-week")
    persist.add_argument("--root")
    persist.add_argument("--markdown-file", required=True)
    persist.add_argument("--json-file", required=True)

    context_cmd = subparsers.add_parser("context", help="load prior report context as JSON")
    context_cmd.add_argument("--mode", required=True, choices=sorted(VALID_MODES))
    context_cmd.add_argument("--domain", required=True)
    context_cmd.add_argument("--root")
    context_cmd.add_argument("--as-of")
    context_cmd.add_argument("--days", type=int, default=7)
    context_cmd.add_argument("--weekly-limit", type=int, default=4)

    list_cmd = subparsers.add_parser("list", help="list stored reports")
    list_cmd.add_argument("--mode", required=True, choices=sorted(VALID_MODES))
    list_cmd.add_argument("--domain")
    list_cmd.add_argument("--root")
    list_cmd.add_argument("--limit", type=int)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "scaffold":
        report_day = parse_report_date(args.report_date)
        markdown = build_markdown_skeleton(
            mode=args.mode,
            domain=args.domain,
            report_date=report_day,
            iso_week=args.iso_week,
        )
        payload = build_json_scaffold(
            mode=args.mode,
            domain=args.domain,
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
            domain=args.domain,
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
            domain=args.domain,
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
            domain=args.domain,
            root=args.root,
            limit=args.limit,
        )
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0

    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
