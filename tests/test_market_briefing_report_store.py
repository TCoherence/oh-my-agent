from __future__ import annotations

import importlib.util
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest


def _load_module():
    path = Path("skills/market-briefing/scripts/report_store.py")
    spec = importlib.util.spec_from_file_location("market_briefing_report_store", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_report_paths_for_daily_and_weekly():
    module = _load_module()
    root = Path("/tmp/market-briefing-test").resolve()

    daily_md, daily_json = module.build_report_paths(
        mode="daily_digest",
        domain="finance",
        root=root,
        report_date=date(2026, 3, 15),
    )
    assert daily_md == root / "daily" / "2026-03-15" / "finance.md"
    assert daily_json == root / "daily" / "2026-03-15" / "finance.json"

    weekly_md, weekly_json = module.build_report_paths(
        mode="weekly_synthesis",
        domain="cross-domain",
        root=root,
        report_date=date(2026, 3, 15),
    )
    assert weekly_md == root / "weekly" / "2026-W11" / "cross-domain.md"
    assert weekly_json == root / "weekly" / "2026-W11" / "cross-domain.json"


def test_current_local_date_respects_report_timezone(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("OMA_REPORT_TIMEZONE", "America/Los_Angeles")
    monkeypatch.delenv("TZ", raising=False)

    now = datetime(2026, 4, 5, 1, 30, tzinfo=UTC)
    assert module.current_local_date(now) == date(2026, 4, 4)


def test_default_reports_root_uses_market_briefing_path(monkeypatch, tmp_path):
    module = _load_module()
    monkeypatch.setenv("HOME", str(tmp_path))
    target_root = tmp_path / ".oh-my-agent" / "reports" / "market-briefing"

    resolved = module.resolve_reports_root()

    assert resolved == target_root.resolve()


def test_scaffold_for_finance_ai_and_weekly_has_expected_sections():
    module = _load_module()

    finance_markdown = module.build_markdown_skeleton(
        mode="daily_digest",
        domain="finance",
        report_date=date(2026, 3, 15),
    )
    assert "# 财经日报｜2026-03-15" in finance_markdown
    assert "## 中国宏观与政策" in finance_markdown
    assert "## 美国宏观与政策" in finance_markdown
    assert "## 美国市场波动与风险偏好" in finance_markdown
    assert "## 中国 / 香港市场脉搏" in finance_markdown
    assert "## 中国房地产政策与融资信号" in finance_markdown
    assert "## 重点持仓财报 / 管理层表态 / CEO 公开发言" in finance_markdown
    assert "## 市场与指数基金视角" in finance_markdown

    finance_json = module.build_json_scaffold(
        mode="daily_digest",
        domain="finance",
        report_date=date(2026, 3, 15),
    )
    assert finance_json["tracked_universe"] == ["NVDA", "MSFT", "AAPL", "AMZN", "GOOG", "TSLA", "META", "VOO", "SPY", "S&P 500"]
    assert finance_json["holdings_window_days"] == 7
    assert finance_json["china_macro_policy_summary"] == ""
    assert finance_json["us_macro_policy_summary"] == ""
    assert finance_json["market_index_view"] == ""
    assert finance_json["coverage_gaps"] == []
    assert finance_json["confidence_flags"] == []
    assert {section["slug"] for section in finance_json["sections"]} == {
        "cn-macro-policy",
        "us-macro-policy",
        "us-market-volatility",
        "china-market-pulse",
        "china-property-policy",
        "tracked-holdings",
        "market-index-view",
        "watchlist",
    }

    ai_markdown = module.build_markdown_skeleton(
        mode="daily_digest",
        domain="ai",
        report_date=date(2026, 3, 15),
    )
    assert "# AI 日报｜2026-03-15" in ai_markdown
    assert "## Frontier Labs / Frontier Model Radar" in ai_markdown
    assert "## 关键人物与社区信号" in ai_markdown
    assert "## Energy" in ai_markdown
    assert "## Chips" in ai_markdown
    assert "## Application" in ai_markdown
    assert "## 层间联动影响" in ai_markdown
    assert "## 候选池变化与后续关注" in ai_markdown

    ai_json = module.build_json_scaffold(
        mode="daily_digest",
        domain="ai",
        report_date=date(2026, 3, 15),
    )
    assert ai_json["mode"] == "daily_digest"
    assert ai_json["domain"] == "ai"
    assert ai_json["tracked_people_groups"] == [
        "claude-code-builders",
        "openai-builders",
        "oss-ai-builders",
        "ai-generalists",
    ]
    assert ai_json["tracked_people"] == []
    assert ai_json["people_signal_summary"] == ""
    assert ai_json["new_candidate_people"] == []
    assert ai_json["promoted_people"] == []
    assert ai_json["candidate_queue_summary"] == ""
    assert {section["slug"] for section in ai_json["sections"]} == {
        "frontier-radar",
        "people-signals",
        "energy",
        "chips",
        "infra",
        "model",
        "application",
        "cross-layer",
        "candidate-queue",
    }

    weekly_markdown = module.build_markdown_skeleton(
        mode="weekly_synthesis",
        domain="cross-domain",
        report_date=date(2026, 3, 15),
    )
    assert "# 市场情报周报｜2026-W11" in weekly_markdown
    assert "## 跨域联动与结构性趋势" in weekly_markdown

    weekly_json = module.build_json_scaffold(
        mode="weekly_synthesis",
        domain="cross-domain",
        report_date=date(2026, 3, 15),
    )
    assert weekly_json["trend_summary"] == ""
    assert weekly_json["cross_domain_links"] == []
    assert weekly_json["period_start"] == "2026-03-09"
    assert weekly_json["period_end"] == "2026-03-15"
    assert weekly_json["coverage_gaps"] == []
    assert weekly_json["confidence_flags"] == []


def test_persist_report_writes_markdown_and_json_atomically(tmp_path):
    module = _load_module()

    md_path, json_path = module.persist_report(
        mode="bootstrap_backfill",
        domain="politics",
        markdown="# politics bootstrap\n",
        payload={
            "title": "政治 bootstrap",
            "period_start": "2026-02-14",
            "period_end": "2026-03-15",
            "summary": "seed context",
            "key_takeaways": ["a", "b"],
            "source_mix_note": "official + analysis",
            "verification_note": "cross-checked",
            "sources": [],
            "sections": [],
        },
        root=tmp_path,
        report_date=date(2026, 3, 15),
    )

    assert md_path == tmp_path / "bootstrap" / "politics" / "2026-03-15.md"
    assert json_path == tmp_path / "bootstrap" / "politics" / "2026-03-15.json"
    assert md_path.read_text(encoding="utf-8") == "# politics bootstrap\n"
    data = json_path.read_text(encoding="utf-8")
    assert '"mode": "bootstrap_backfill"' in data
    assert '"domain": "politics"' in data


def test_persist_report_overrides_report_time_metadata(tmp_path):
    module = _load_module()

    _, json_path = module.persist_report(
        mode="daily_digest",
        domain="finance",
        markdown="# finance\n",
        payload={
            "title": "财经日报",
            "generated_at": "1999-01-01T00:00:00+00:00",
            "report_timezone": "UTC",
            "report_date": "1999-01-01",
            "period_start": "2026-03-15",
            "period_end": "2026-03-15",
            "summary": "summary",
            "key_takeaways": [],
            "source_mix_note": "",
            "verification_note": "",
            "sources": [],
            "sections": [],
        },
        root=tmp_path,
        report_date=date(2026, 3, 15),
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["report_date"] == "2026-03-15"
    assert payload["report_timezone"]
    assert payload["generated_at"] != "1999-01-01T00:00:00+00:00"


def test_context_uses_recent_daily_reports_bootstrap_and_weekly_history(tmp_path):
    module = _load_module()

    module.persist_report(
        mode="bootstrap_backfill",
        domain="politics",
        markdown="# bootstrap\n",
        payload={
            "title": "政治 bootstrap",
            "period_start": "2026-02-14",
            "period_end": "2026-03-14",
            "summary": "seed",
            "key_takeaways": [],
            "source_mix_note": "",
            "verification_note": "",
            "sources": [],
            "sections": [],
        },
        root=tmp_path,
        report_date=date(2026, 3, 14),
    )
    for day in range(9, 16):
        module.persist_report(
            mode="daily_digest",
            domain="politics",
            markdown=f"# daily {day}\n",
            payload={
                "title": f"政治日报 {day}",
                "period_start": f"2026-03-{day:02d}",
                "period_end": f"2026-03-{day:02d}",
                "summary": f"summary-{day}",
                "key_takeaways": [],
                "source_mix_note": "",
                "verification_note": "",
                "sources": [],
                "sections": [],
            },
            root=tmp_path,
            report_date=date(2026, 3, day),
        )
    module.persist_report(
        mode="weekly_synthesis",
        domain="cross-domain",
        markdown="# weekly old\n",
        payload={
            "title": "week 10",
            "period_start": "2026-03-02",
            "period_end": "2026-03-08",
            "summary": "old weekly",
            "key_takeaways": [],
            "source_mix_note": "",
            "verification_note": "",
            "sources": [],
            "sections": [],
            "trend_summary": "",
            "cross_domain_links": [],
        },
        root=tmp_path,
        report_date=date(2026, 3, 8),
        iso_week="2026-W10",
    )

    daily_context = module.build_context(
        mode="daily_digest",
        domain="politics",
        root=tmp_path,
        as_of=date(2026, 3, 15),
        days=7,
    )
    assert daily_context["latest_bootstrap"]["title"] == "政治 bootstrap"
    assert [item["period_end"] for item in daily_context["recent_daily"]] == [
        "2026-03-09",
        "2026-03-10",
        "2026-03-11",
        "2026-03-12",
        "2026-03-13",
        "2026-03-14",
        "2026-03-15",
    ]
    assert daily_context["recent_weekly"][0]["title"] == "week 10"

    weekly_context = module.build_context(
        mode="weekly_synthesis",
        domain="cross-domain",
        root=tmp_path,
        as_of=date(2026, 3, 15),
        days=7,
    )
    assert weekly_context["bootstrap"]["politics"]["title"] == "政治 bootstrap"
    assert len(weekly_context["recent_daily"]["politics"]) == 7
    assert weekly_context["recent_weekly"][0]["title"] == "week 10"
