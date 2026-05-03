from __future__ import annotations

import importlib.util
import json
from datetime import UTC, date, datetime
from pathlib import Path


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
    assert "## 🎙️ 播客动态" in finance_markdown

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
        "podcasts",
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
    assert "## 🎙️ 播客动态" in ai_markdown
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
        "podcasts",
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


def test_persist_ai_daily_auto_records_people_pool(tmp_path):
    """persist_report for AI daily should auto-record candidates into the people pool state."""
    module = _load_module()

    seed_path = tmp_path / "references" / "ai_people_seed.yaml"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(
        "version: 1\ngroups:\n  ai-generalists:\n    label: AI generalists\n"
        "    description: ''\n    people: []\n",
        encoding="utf-8",
    )

    result = module._try_record_ai_pool(
        # We need to create a valid AI daily JSON file first
        _make_ai_json_with_candidate(tmp_path),
        root=tmp_path,
        as_of=date(2026, 4, 15),
    )

    # auto-record should succeed and find the candidate
    assert result is not None
    assert "new-person" in result["new_candidate_ids"]
    assert result["candidate_count"] == 1

    # verify state file was written
    state_path = tmp_path / "state" / "ai_people_pool.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "new-person" in state["candidates"]


def _make_ai_json_with_candidate(tmp_path: Path) -> Path:
    """Helper: write a minimal AI daily JSON with one new candidate and return its path."""
    json_path = tmp_path / "daily" / "2026-04-15" / "ai.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "mode": "daily_digest",
        "domain": "ai",
        "title": "AI test",
        "generated_at": "2026-04-15T00:00:00+00:00",
        "report_timezone": "UTC",
        "report_date": "2026-04-15",
        "period_start": "2026-04-15",
        "period_end": "2026-04-15",
        "summary": "",
        "key_takeaways": [],
        "source_mix_note": "",
        "verification_note": "",
        "sources": [],
        "sections": [],
        "new_candidate_people": [
            {
                "person_id": "new-person",
                "name": "New Person",
                "group": "ai-generalists",
                "reason": "released notable open-source AI tool",
                "evidence_urls": ["https://github.com/new-person/tool"],
            }
        ],
        "promoted_people": [],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path


# ---------------------------------------------------------------------------
# Stage 2.2: AI daily sub-section persistence + status
# ---------------------------------------------------------------------------


def _ai_section_payload(section: str) -> dict:
    """Build a minimal valid payload for each of the 4 AI sub-section schemas.
    Mirrors the body anchors in section_schemas.md — anything richer than
    these is the agent's responsibility, not section-status's gate."""
    bodies = {
        "frontier_radar": {
            "frontier_signal_summary": "",
            "labs": [],
            "unverified_frontier_signals": [],
        },
        "paper_layer": {
            "paper_digest_status": "consumed",
            "paper_digest_path": "/home/.oh-my-agent/reports/paper-digest/daily/2026-05-04.json",
            "papers_consumed_from_paper_digest": [],
            "technical_signals": [],
        },
        "people_pool": {
            "people_signal_summary": "",
            "tracked_people_signals": [],
            "new_candidate_people": [],
            "promoted_people": [],
            "candidate_queue_summary": "",
        },
        "macro_news": {
            "five_layer_signals": {
                "energy": [],
                "chips": [],
                "infra": [],
                "model": [],
                "application": [],
            },
            "cross_layer_links": [],
        },
    }
    return bodies[section]


def test_build_section_paths_layout(tmp_path):
    """Sub-sections live at <root>/daily/<date>/ai_sections/<name>.{md,json}.
    Mirrors deals-scanner's references/<source>.{md,json} layout."""
    module = _load_module()
    md_path, json_path = module.build_section_paths(
        domain="ai",
        section="frontier_radar",
        root=tmp_path,
        report_date=date(2026, 5, 4),
    )
    expected_dir = tmp_path / "daily" / "2026-05-04" / "ai_sections"
    assert md_path == expected_dir / "frontier_radar.md"
    assert json_path == expected_dir / "frontier_radar.json"


def test_build_section_paths_rejects_non_ai_domain(tmp_path):
    """Stage 2.2 ships AI-only. Non-AI domains should raise loudly so a
    caller doesn't silently scribble politics/finance sub-sections into
    a layout we haven't designed yet."""
    module = _load_module()
    import pytest as pt

    with pt.raises(ValueError, match="domain='ai'"):
        module.build_section_paths(
            domain="finance",
            section="frontier_radar",
            root=tmp_path,
            report_date=date(2026, 5, 4),
        )


def test_build_section_paths_rejects_unknown_section(tmp_path):
    module = _load_module()
    import pytest as pt

    with pt.raises(ValueError, match="unknown AI sub-section"):
        module.build_section_paths(
            domain="ai",
            section="not_a_real_section",
            root=tmp_path,
            report_date=date(2026, 5, 4),
        )


def test_persist_section_writes_pair_with_normalized_meta(tmp_path):
    """``persist_section`` writes both the .md and .json file pair, and
    overwrites/normalises the meta keys (version/section/domain/report_date/
    generated_at/report_timezone) so the agent can't accidentally write
    a half-formed header."""
    module = _load_module()
    payload = {
        "section": "frontier_radar",
        "domain": "ai",
        "report_date": "wrong",  # will be overwritten
        **_ai_section_payload("frontier_radar"),
    }

    md_path, json_path = module.persist_section(
        domain="ai",
        section="frontier_radar",
        markdown="## Frontier Labs\n\nplaceholder body\n",
        payload=payload,
        root=tmp_path,
        report_date=date(2026, 5, 4),
    )

    assert md_path.read_text(encoding="utf-8").startswith("## Frontier Labs")
    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert written["version"] == 1
    assert written["section"] == "frontier_radar"
    assert written["domain"] == "ai"
    assert written["report_date"] == "2026-05-04"
    assert written["labs"] == []  # body anchor preserved
    assert "generated_at" in written
    assert "report_timezone" in written


def test_persist_section_validates_body_anchor(tmp_path):
    """Each AI sub-section has a required body anchor (e.g., `labs` for
    `frontier_radar`). Missing it should raise — sub-section files
    without their anchor are unsafe for the aggregator to consume."""
    module = _load_module()
    import pytest as pt

    bad_payload = {
        "section": "frontier_radar",
        "domain": "ai",
        "report_date": "2026-05-04",
        # missing `labs` — the body anchor for frontier_radar
        "frontier_signal_summary": "",
    }

    with pt.raises(ValueError, match="body anchor"):
        module.persist_section(
            domain="ai",
            section="frontier_radar",
            markdown="placeholder",
            payload=bad_payload,
            root=tmp_path,
            report_date=date(2026, 5, 4),
        )


def test_section_status_marks_complete_only_when_both_files_valid(tmp_path):
    """Checkpoint recovery hinges on this: a section is "complete" only
    when both files exist AND the JSON parses AND the body anchor is
    present. Half-written or schema-invalid sections must be flagged
    incomplete so the bumped re-run redoes them instead of skipping."""
    module = _load_module()
    report_date = date(2026, 5, 4)

    # 1. frontier_radar: persist normally → complete
    module.persist_section(
        domain="ai",
        section="frontier_radar",
        markdown="ok",
        payload={
            "section": "frontier_radar",
            "domain": "ai",
            "report_date": report_date.isoformat(),
            **_ai_section_payload("frontier_radar"),
        },
        root=tmp_path,
        report_date=report_date,
    )

    # 2. paper_layer: only .md exists, no .json → incomplete (json_missing)
    paper_md, paper_json = module.build_section_paths(
        domain="ai",
        section="paper_layer",
        root=tmp_path,
        report_date=report_date,
    )
    paper_md.parent.mkdir(parents=True, exist_ok=True)
    paper_md.write_text("only md exists", encoding="utf-8")

    # 3. people_pool: both files exist, but JSON is missing the body anchor
    pp_md, pp_json = module.build_section_paths(
        domain="ai",
        section="people_pool",
        root=tmp_path,
        report_date=report_date,
    )
    pp_md.parent.mkdir(parents=True, exist_ok=True)
    pp_md.write_text("md", encoding="utf-8")
    pp_json.write_text(
        json.dumps({
            "version": 1,
            "section": "people_pool",
            "domain": "ai",
            "report_date": report_date.isoformat(),
            # missing `people_signal_summary` body anchor
        }),
        encoding="utf-8",
    )

    # 4. macro_news: not written at all → incomplete (md_missing)

    status = module.section_status(
        domain="ai",
        root=tmp_path,
        report_date=report_date,
    )

    assert status["domain"] == "ai"
    assert status["report_date"] == "2026-05-04"
    assert status["total"] == 4
    assert status["complete_count"] == 1

    sections = status["sections"]
    assert sections["frontier_radar"]["complete"] is True
    assert sections["paper_layer"]["complete"] is False
    assert sections["paper_layer"]["reason"] == "json_missing"
    assert sections["people_pool"]["complete"] is False
    assert "json_schema_invalid" in sections["people_pool"]["reason"]
    assert sections["macro_news"]["complete"] is False
    assert sections["macro_news"]["reason"] == "md_missing"


def test_section_status_handles_unparseable_json(tmp_path):
    """A `.json` that is not parseable (truncated mid-write, etc.) must
    surface as `json_parse_failed`, not crash the whole section-status
    call. Otherwise an in-flight kill could brick the re-run path."""
    module = _load_module()
    report_date = date(2026, 5, 4)

    md_path, json_path = module.build_section_paths(
        domain="ai",
        section="frontier_radar",
        root=tmp_path,
        report_date=report_date,
    )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("md", encoding="utf-8")
    json_path.write_text("{not valid json", encoding="utf-8")

    status = module.section_status(
        domain="ai",
        root=tmp_path,
        report_date=report_date,
    )
    fr = status["sections"]["frontier_radar"]
    assert fr["complete"] is False
    assert fr["reason"].startswith("json_parse_failed")


def test_persist_section_then_section_status_round_trip_all_four(tmp_path):
    """End-to-end smoke: persist all 4 sub-sections with valid payloads,
    verify section-status reports complete_count == 4."""
    module = _load_module()
    report_date = date(2026, 5, 4)

    for section in ("frontier_radar", "paper_layer", "people_pool", "macro_news"):
        module.persist_section(
            domain="ai",
            section=section,
            markdown=f"# {section}\n",
            payload={
                "section": section,
                "domain": "ai",
                "report_date": report_date.isoformat(),
                **_ai_section_payload(section),
            },
            root=tmp_path,
            report_date=report_date,
        )

    status = module.section_status(
        domain="ai",
        root=tmp_path,
        report_date=report_date,
    )
    assert status["complete_count"] == 4
    assert status["total"] == 4
    for section_status_entry in status["sections"].values():
        assert section_status_entry["complete"] is True
