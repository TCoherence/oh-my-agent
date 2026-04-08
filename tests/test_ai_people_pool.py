from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path


def _load_module():
    path = Path("skills/market-briefing/scripts/ai_people_pool.py")
    spec = importlib.util.spec_from_file_location("market_briefing_ai_people_pool", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_seed(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """version: 1
groups:
  ai-generalists:
    label: "AI generalists"
    description: "Generalists"
    people:
      - person_id: "andrej-karpathy"
        name: "Andrej Karpathy"
        x_handle: "karpathy"
        role: "Generalist"
        why_track: "High-signal framing"
        search_terms: ["Andrej Karpathy"]
        aliases: []
""",
        encoding="utf-8",
    )


def test_context_merges_seed_and_runtime_state(tmp_path):
    module = _load_module()
    seed_path = tmp_path / "seed.yaml"
    state_path = tmp_path / "state.json"
    _write_seed(seed_path)
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-04-07T10:00:00-07:00",
                "report_timezone": "America/Los_Angeles",
                "candidates": {
                    "simon-willison": {
                        "person_id": "simon-willison",
                        "name": "Simon Willison",
                        "group": "oss-ai-builders",
                        "mention_count": 1,
                        "first_seen_date": "2026-04-07",
                        "last_seen_date": "2026-04-07",
                        "status": "candidate",
                    }
                },
                "tracked_runtime": {
                    "harrison-chase": {
                        "person_id": "harrison-chase",
                        "name": "Harrison Chase",
                        "group": "oss-ai-builders",
                        "status": "tracked_runtime",
                    }
                },
                "synced_seed": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    context = module.build_context(seed_file=seed_path, state_file=state_path)

    tracked_ids = {item["person_id"] for item in context["tracked_people"]}
    candidate_ids = {item["person_id"] for item in context["candidate_people"]}
    assert "andrej-karpathy" in tracked_ids
    assert "harrison-chase" in tracked_ids
    assert "simon-willison" in candidate_ids
    assert context["candidate_queue_summary"]["tracked_runtime_count"] == 1


def test_record_promotes_candidate_after_second_recent_mention(tmp_path):
    module = _load_module()
    seed_path = tmp_path / "seed.yaml"
    state_path = tmp_path / "state.json"
    report_path = tmp_path / "ai.json"
    _write_seed(seed_path)
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-04-07T10:00:00-07:00",
                "report_timezone": "America/Los_Angeles",
                "candidates": {
                    "simon-willison": {
                        "person_id": "simon-willison",
                        "name": "Simon Willison",
                        "x_handle": "simonw",
                        "group": "oss-ai-builders",
                        "reason": "First high-signal mention",
                        "evidence_urls": ["https://x.com/simonw/status/1"],
                        "mention_count": 1,
                        "first_seen_date": "2026-04-02",
                        "last_seen_date": "2026-04-02",
                        "status": "candidate",
                    }
                },
                "tracked_runtime": {},
                "synced_seed": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report_path.write_text(
        json.dumps(
            {
                "domain": "ai",
                "new_candidate_people": [
                    {
                        "person_id": "simon-willison",
                        "name": "Simon Willison",
                        "x_handle": "simonw",
                        "group": "oss-ai-builders",
                        "reason": "Second corroborated mention",
                        "evidence_urls": ["https://x.com/simonw/status/2"],
                    }
                ],
                "promoted_people": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = module.record_report(
        report_json=report_path,
        seed_file=seed_path,
        state_file=state_path,
        as_of=date(2026, 4, 7),
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert "simon-willison" in result["promoted_ids"]
    assert "simon-willison" not in state["candidates"]
    assert state["tracked_runtime"]["simon-willison"]["status"] == "tracked_runtime"
    assert state["tracked_runtime"]["simon-willison"]["mention_count"] == 2


def test_record_promotes_cross_checked_candidate_immediately(tmp_path):
    module = _load_module()
    seed_path = tmp_path / "seed.yaml"
    state_path = tmp_path / "state.json"
    report_path = tmp_path / "ai.json"
    _write_seed(seed_path)
    report_path.write_text(
        json.dumps(
            {
                "domain": "ai",
                "new_candidate_people": [
                    {
                        "person_id": "boris-cherny",
                        "name": "Boris Cherny",
                        "x_handle": "bcherny",
                        "group": "claude-code-builders",
                        "reason": "Claude Code release thread cross-checked by docs",
                        "evidence_urls": ["https://x.com/bcherny/status/1", "https://docs.anthropic.com"],
                        "cross_checked": True,
                        "promote_recommended": True,
                    }
                ],
                "promoted_people": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = module.record_report(
        report_json=report_path,
        seed_file=seed_path,
        state_file=state_path,
        as_of=date(2026, 4, 7),
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert "boris-cherny" in result["promoted_ids"]
    assert state["tracked_runtime"]["boris-cherny"]["promotion_reason"]


def test_sync_repo_moves_tracked_runtime_into_seed(tmp_path):
    module = _load_module()
    seed_path = tmp_path / "seed.yaml"
    state_path = tmp_path / "state.json"
    _write_seed(seed_path)
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-04-07T10:00:00-07:00",
                "report_timezone": "America/Los_Angeles",
                "candidates": {},
                "tracked_runtime": {
                    "harrison-chase": {
                        "person_id": "harrison-chase",
                        "name": "Harrison Chase",
                        "x_handle": "hwchase17",
                        "group": "oss-ai-builders",
                        "role": "LangChain builder",
                        "why_track": "Agent framework signals",
                        "search_terms": ["Harrison Chase LangChain"],
                        "aliases": [],
                        "status": "tracked_runtime",
                    }
                },
                "synced_seed": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = module.sync_repo_seed(seed_file=seed_path, state_file=state_path)
    seed = seed_path.read_text(encoding="utf-8")
    state = json.loads(state_path.read_text(encoding="utf-8"))

    assert "harrison-chase" in result["synced_ids"]
    assert "harrison-chase" in seed
    assert "harrison-chase" not in state["tracked_runtime"]
    assert state["synced_seed"]["harrison-chase"]["status"] == "synced_seed"
