from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

import pytest


def _load_module():
    path = Path("skills/market-intel-report/scripts/report_store.py")
    spec = importlib.util.spec_from_file_location("market_intel_report_store", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_report_paths_for_daily_and_weekly():
    module = _load_module()
    root = Path("/tmp/market-intel-test").resolve()

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


def test_scaffold_for_daily_ai_and_weekly_has_expected_sections():
    module = _load_module()

    ai_markdown = module.build_markdown_skeleton(
        mode="daily_digest",
        domain="ai",
        report_date=date(2026, 3, 15),
    )
    assert "# AI 日报｜2026-03-15" in ai_markdown
    assert "## Energy" in ai_markdown
    assert "## Chips" in ai_markdown
    assert "## Application" in ai_markdown
    assert "## 层间联动影响" in ai_markdown

    ai_json = module.build_json_scaffold(
        mode="daily_digest",
        domain="ai",
        report_date=date(2026, 3, 15),
    )
    assert ai_json["mode"] == "daily_digest"
    assert ai_json["domain"] == "ai"
    assert {section["slug"] for section in ai_json["sections"]} == {
        "energy",
        "chips",
        "infra",
        "model",
        "application",
        "cross-layer",
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
