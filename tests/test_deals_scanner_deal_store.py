from __future__ import annotations

import importlib.util
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest


def _load_module():
    path = Path("skills/deals-scanner/scripts/deal_store.py")
    spec = importlib.util.spec_from_file_location("deals_scanner_deal_store", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_report_paths_for_daily_and_weekly():
    module = _load_module()
    root = Path("/tmp/deals-scanner-test").resolve()

    daily_md, daily_json = module.build_report_paths(
        mode="daily_scan",
        source="credit-cards",
        root=root,
        report_date=date(2026, 4, 4),
    )
    assert daily_md == root / "daily" / "2026-04-04" / "references" / "credit-cards.md"
    assert daily_json == root / "daily" / "2026-04-04" / "references" / "credit-cards.json"

    summary_md, summary_json = module.build_report_paths(
        mode="daily_scan",
        source="summary",
        root=root,
        report_date=date(2026, 4, 4),
    )
    assert summary_md == root / "daily" / "2026-04-04" / "summary.md"
    assert summary_json == root / "daily" / "2026-04-04" / "summary.json"

    weekly_md, weekly_json = module.build_report_paths(
        mode="weekly_digest",
        source="all-sources",
        root=root,
        report_date=date(2026, 4, 4),
    )
    assert weekly_md == root / "weekly" / "2026-W14" / "all-sources.md"
    assert weekly_json == root / "weekly" / "2026-W14" / "all-sources.json"


def test_current_local_date_respects_report_timezone(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("OMA_REPORT_TIMEZONE", "America/Los_Angeles")
    monkeypatch.delenv("TZ", raising=False)

    now = datetime(2026, 4, 5, 1, 30, tzinfo=UTC)
    assert module.current_local_date(now) == date(2026, 4, 4)


def test_scaffold_for_all_sources_has_expected_sections():
    module = _load_module()

    summary_md = module.build_markdown_skeleton(
        mode="daily_scan", source="summary", report_date=date(2026, 4, 4),
    )
    assert "# 优惠扫描总览｜2026-04-04" in summary_md
    assert "## Apply now" in summary_md
    assert "## Buy now" in summary_md
    assert "## Stack now" in summary_md
    assert "## Watchlist" in summary_md
    assert "## Coverage / Confidence" in summary_md
    assert "[信用卡优惠](references/credit-cards.md)" in summary_md

    summary_json = module.build_json_scaffold(
        mode="daily_scan", source="summary", report_date=date(2026, 4, 4),
    )
    assert summary_json["source"] == "summary"
    assert summary_json["action_buckets"] == {
        "apply_now": [],
        "buy_now": [],
        "stack_now": [],
        "watchlist": [],
    }
    assert summary_json["source_snapshots"][0] == {
        "source": "credit-cards",
        "summary": "",
        "high_confidence_count": 0,
        "watchlist_count": 0,
        "met_floor": False,
    }
    assert summary_json["coverage_status"] == {
        "target_floor": 10,
        "sources_below_floor": [],
    }
    assert summary_json["reference_reports"][0]["markdown_path"] == "references/credit-cards.md"
    assert {s["slug"] for s in summary_json["sections"]} == {
        "judgement", "apply-now", "buy-now", "stack-now", "watchlist", "source-snapshots",
        "coverage-confidence", "reference-index",
    }

    # credit-cards daily
    cc_md = module.build_markdown_skeleton(
        mode="daily_scan", source="credit-cards", report_date=date(2026, 4, 4),
    )
    assert "# 信用卡优惠日报｜2026-04-04" in cc_md
    assert "## 开卡奖励（Sign-up Bonuses）" in cc_md
    assert "## 即将到期的优惠" in cc_md

    cc_json = module.build_json_scaffold(
        mode="daily_scan", source="credit-cards", report_date=date(2026, 4, 4),
    )
    assert cc_json["mode"] == "daily_scan"
    assert cc_json["source"] == "credit-cards"
    assert cc_json["lower_confidence_watchlist"] == []
    assert cc_json["high_confidence_count"] == 0
    assert cc_json["coverage_floor_met"] is False
    assert {s["slug"] for s in cc_json["sections"]} == {
        "signup-bonuses", "cashback-rewards", "fee-offers", "expiring",
    }

    # uscardforum daily
    ucf_md = module.build_markdown_skeleton(
        mode="daily_scan", source="uscardforum", report_date=date(2026, 4, 4),
    )
    assert "# 美卡论坛日报｜2026-04-04" in ucf_md
    assert "## 开卡审批经验" in ucf_md

    ucf_json = module.build_json_scaffold(
        mode="daily_scan", source="uscardforum", report_date=date(2026, 4, 4),
    )
    assert {s["slug"] for s in ucf_json["sections"]} == {
        "hot-discussions", "approval-experience", "redemption-strategy", "bank-policy",
    }

    # rakuten daily
    rk_md = module.build_markdown_skeleton(
        mode="daily_scan", source="rakuten", report_date=date(2026, 4, 4),
    )
    assert "# Rakuten 返现日报｜2026-04-04" in rk_md
    assert "## 今日高返现商家" in rk_md

    # slickdeals daily
    sd_json = module.build_json_scaffold(
        mode="daily_scan", source="slickdeals", report_date=date(2026, 4, 4),
    )
    assert {s["slug"] for s in sd_json["sections"]} == {
        "frontpage", "tech", "home-living", "broader-mix",
    }

    # dealmoon daily
    dm_json = module.build_json_scaffold(
        mode="daily_scan", source="dealmoon", report_date=date(2026, 4, 4),
    )
    assert {s["slug"] for s in dm_json["sections"]} == {
        "top-picks", "exclusive-codes", "beauty", "electronics", "home-living",
    }

    # weekly digest
    weekly_md = module.build_markdown_skeleton(
        mode="weekly_digest", source="all-sources", report_date=date(2026, 4, 4),
    )
    assert "# 优惠情报周报｜2026-W14" in weekly_md
    assert "## 跨渠道策略与趋势" in weekly_md
    assert "## 美卡论坛回顾" in weekly_md

    weekly_json = module.build_json_scaffold(
        mode="weekly_digest", source="all-sources", report_date=date(2026, 4, 4),
    )
    assert weekly_json["trend_summary"] == ""
    assert weekly_json["cross_source_highlights"] == []
    assert weekly_json["period_start"] == "2026-03-29"
    assert weekly_json["period_end"] == "2026-04-04"
    assert weekly_json["iso_week"] == "2026-W14"


def test_persist_report_writes_markdown_and_json_atomically(tmp_path, monkeypatch):
    module = _load_module()
    monkeypatch.setenv("OMA_REPORT_TIMEZONE", "America/Los_Angeles")
    monkeypatch.delenv("TZ", raising=False)

    md_path, json_path = module.persist_report(
        mode="daily_scan",
        source="rakuten",
        markdown="# Rakuten 返现日报\n",
        payload={
            "title": "Rakuten 返现日报",
            "generated_at": "2000-01-01T00:00:00Z",
            "report_timezone": "UTC",
            "report_date": "1999-01-01",
            "period_start": "2026-04-04",
            "period_end": "2026-04-04",
            "summary": "今日 Sephora 12% 返现",
            "top_deals": [],
            "source_mix_note": "deal-site",
            "sources": [],
            "sections": [],
        },
        root=tmp_path,
        report_date=date(2026, 4, 4),
    )

    assert md_path == tmp_path / "daily" / "2026-04-04" / "references" / "rakuten.md"
    assert json_path == tmp_path / "daily" / "2026-04-04" / "references" / "rakuten.json"
    assert md_path.read_text(encoding="utf-8") == "# Rakuten 返现日报\n"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["mode"] == "daily_scan"
    assert data["source"] == "rakuten"
    assert data["summary"] == "今日 Sephora 12% 返现"
    assert data["report_timezone"] == "America/Los_Angeles"
    assert data["report_date"] == "2026-04-04"
    assert data["generated_at"] != "2000-01-01T00:00:00Z"
    assert "T" in data["generated_at"]


def test_context_uses_recent_daily_reports_and_weekly_history(tmp_path):
    module = _load_module()

    # Populate daily reports for credit-cards and rakuten
    for day in range(1, 8):
        for src in ("credit-cards", "rakuten"):
            module.persist_report(
                mode="daily_scan",
                source=src,
                markdown=f"# daily {src} {day}\n",
                payload={
                    "title": f"{src} day {day}",
                    "period_start": f"2026-04-{day:02d}",
                    "period_end": f"2026-04-{day:02d}",
                    "summary": f"summary-{day}",
                    "top_deals": [],
                    "source_mix_note": "",
                    "sources": [],
                    "sections": [],
                },
                root=tmp_path,
                report_date=date(2026, 4, day),
            )

    module.persist_report(
        mode="daily_scan",
        source="summary",
        markdown="# summary 7\n",
        payload={
            "title": "summary day 7",
            "period_start": "2026-04-07",
            "period_end": "2026-04-07",
            "summary": "bundle summary",
            "top_deals": [],
            "source_mix_note": "",
            "sources": [],
            "sections": [],
            "reference_reports": [],
        },
        root=tmp_path,
        report_date=date(2026, 4, 7),
    )

    # Populate a weekly report
    module.persist_report(
        mode="weekly_digest",
        source="all-sources",
        markdown="# weekly old\n",
        payload={
            "title": "week 13",
            "period_start": "2026-03-23",
            "period_end": "2026-03-29",
            "summary": "old weekly",
            "top_deals": [],
            "source_mix_note": "",
            "sources": [],
            "sections": [],
            "trend_summary": "",
            "cross_source_highlights": [],
        },
        root=tmp_path,
        report_date=date(2026, 3, 29),
        iso_week="2026-W13",
    )

    # Test daily_scan context
    daily_context = module.build_context(
        mode="daily_scan",
        source="credit-cards",
        root=tmp_path,
        as_of=date(2026, 4, 7),
        days=7,
    )
    assert [item["period_end"] for item in daily_context["recent_daily"]] == [
        "2026-04-01",
        "2026-04-02",
        "2026-04-03",
        "2026-04-04",
        "2026-04-05",
        "2026-04-06",
        "2026-04-07",
    ]
    assert daily_context["recent_weekly"][0]["title"] == "week 13"

    summary_context = module.build_context(
        mode="daily_scan",
        source="summary",
        root=tmp_path,
        as_of=date(2026, 4, 7),
        days=7,
    )
    assert summary_context["current_references"]["credit-cards"]["title"] == "credit-cards day 7"
    assert summary_context["current_references"]["rakuten"]["title"] == "rakuten day 7"
    assert summary_context["recent_summary"][0]["title"] == "summary day 7"

    # Test weekly_digest context
    weekly_context = module.build_context(
        mode="weekly_digest",
        source="all-sources",
        root=tmp_path,
        as_of=date(2026, 4, 7),
        days=7,
    )
    assert len(weekly_context["recent_daily"]["credit-cards"]) == 7
    assert len(weekly_context["recent_daily"]["rakuten"]) == 7
    assert len(weekly_context["recent_daily"]["dealmoon"]) == 0  # no data
    assert weekly_context["recent_weekly"][0]["title"] == "week 13"


def test_invalid_mode_source_combinations_raise_value_error():
    module = _load_module()

    # daily_scan rejects all-sources
    with pytest.raises(ValueError, match="daily_scan does not accept"):
        module.build_report_paths(mode="daily_scan", source="all-sources")

    # weekly_digest rejects named sources
    with pytest.raises(ValueError, match="weekly_digest requires"):
        module.build_report_paths(mode="weekly_digest", source="credit-cards")

    # daily_scan rejects unknown source
    with pytest.raises(ValueError, match="unsupported source"):
        module.build_report_paths(mode="daily_scan", source="amazon")

    # daily_scan accepts summary as the internal aggregation target
    module.build_report_paths(mode="daily_scan", source="summary")

    # unsupported mode
    with pytest.raises(ValueError, match="unsupported mode"):
        module.build_report_paths(mode="bootstrap", source="rakuten")

    # validation also applies to scaffold and context
    with pytest.raises(ValueError):
        module.build_markdown_skeleton(mode="daily_scan", source="all-sources")

    with pytest.raises(ValueError):
        module.build_json_scaffold(mode="weekly_digest", source="slickdeals")

    with pytest.raises(ValueError):
        module.build_context(mode="daily_scan", source="all-sources")
