from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import yaml

from oh_my_agent.memory.judge_store import (
    JudgeStore,
    MemoryEntry,
    parse_judge_actions,
)


@pytest.fixture
def store_dir(tmp_path: Path) -> Path:
    return tmp_path / "memory"


def _build_store(path: Path, **kwargs) -> JudgeStore:
    return JudgeStore(memory_dir=path, **kwargs)


def test_memory_entry_from_dict_normalizes_invalid_fields():
    entry = MemoryEntry.from_dict({
        "summary": "  user prefers tea  ",
        "category": "bogus",
        "scope": "elsewhere",
        "confidence": "1.5",
        "observation_count": "x",
        "evidence_log": [{"thread_id": "t", "ts": "2026-01-01T00:00:00+00:00", "snippet": "tea"}],
    })
    assert entry.summary == "user prefers tea"
    assert entry.category == "fact"
    assert entry.scope == "global_user"
    assert entry.confidence == 1.0
    assert entry.observation_count == 1
    assert entry.evidence_log[0].snippet == "tea"


@pytest.mark.asyncio
async def test_apply_actions_add_strengthen_supersede_no_op(store_dir: Path):
    store = _build_store(store_dir)
    await store.load()
    actions = [
        {
            "op": "add",
            "summary": "user prefers concise responses",
            "category": "preference",
            "scope": "global_user",
            "confidence": 0.9,
            "evidence": "make it short",
        },
        {"op": "no_op", "reason": "nothing else"},
    ]
    stats = await store.apply_actions(actions, thread_id="t1", skill_name="market-briefing")
    assert stats == {"add": 1, "strengthen": 0, "supersede": 0, "no_op": 1, "rejected": 0}
    active = store.get_active()
    assert len(active) == 1
    target = active[0]
    assert "market-briefing" in target.source_skills

    strengthen = [{"op": "strengthen", "id": target.id, "evidence": "shorter please", "confidence_bump": 0.1}]
    stats2 = await store.apply_actions(strengthen, thread_id="t2")
    assert stats2["strengthen"] == 1
    refreshed = store.get_by_id(target.id)
    assert refreshed.observation_count == 2
    assert refreshed.confidence == pytest.approx(1.0, abs=1e-3)
    assert len(refreshed.evidence_log) == 2

    supersede = [
        {
            "op": "supersede",
            "old_id": target.id,
            "new_summary": "user wants medium-length responses",
            "category": "preference",
            "scope": "global_user",
            "confidence": 0.92,
            "evidence": "actually a bit longer is fine",
        }
    ]
    stats3 = await store.apply_actions(supersede, thread_id="t3")
    assert stats3["supersede"] == 1
    superseded = store.get_by_id(target.id)
    assert superseded.status == "superseded"
    assert superseded.superseded_by is not None
    new_entry = store.get_by_id(superseded.superseded_by)
    assert new_entry is not None
    assert new_entry.status == "active"
    assert new_entry.summary.startswith("user wants medium-length")


@pytest.mark.asyncio
async def test_apply_actions_rejects_invalid_payloads(store_dir: Path):
    store = _build_store(store_dir)
    await store.load()
    actions = [
        {"op": "add"},  # no summary
        {"op": "strengthen", "id": "missing"},  # unknown id
        {"op": "supersede", "old_id": "missing", "new_summary": "x", "category": "preference", "scope": "global_user", "confidence": 0.9, "evidence": ""},
        {"op": "??"},
        "not even a dict",
    ]
    stats = await store.apply_actions(actions)
    assert stats["rejected"] == 5
    assert stats["add"] == 0


@pytest.mark.asyncio
async def test_save_and_reload_round_trip(store_dir: Path):
    store = _build_store(store_dir)
    await store.load()
    await store.apply_actions([
        {
            "op": "add",
            "summary": "uses zsh",
            "category": "fact",
            "scope": "global_user",
            "confidence": 0.8,
            "evidence": "I prefer zsh",
        }
    ], thread_id="thread")
    # File written
    payload_path = store_dir / "memories.yaml"
    assert payload_path.exists()
    raw = yaml.safe_load(payload_path.read_text())
    assert isinstance(raw, list)
    assert raw[0]["summary"] == "uses zsh"
    assert raw[0]["status"] == "active"

    # New instance loads same data
    store2 = _build_store(store_dir)
    await store2.load()
    assert len(store2.get_active()) == 1
    assert store2.get_active()[0].summary == "uses zsh"


@pytest.mark.asyncio
async def test_should_synthesize_dirty_then_clean(store_dir: Path):
    store = _build_store(store_dir, synthesize_after_seconds=3600)
    await store.load()
    assert store.should_synthesize() is False  # nothing yet
    await store.apply_actions([
        {"op": "add", "summary": "x", "category": "fact", "scope": "global_user", "confidence": 0.7, "evidence": ""}
    ])
    assert store.should_synthesize() is True
    store.clear_synthesis_flag()
    # MEMORY.md missing → still True (because there are active entries)
    assert store.should_synthesize() is True

    md_path = store_dir / "MEMORY.md"
    md_path.write_text("hello", encoding="utf-8")
    assert store.should_synthesize() is False
    # Make file old → should re-synthesize
    old = time.time() - 7200
    os.utime(md_path, (old, old))
    assert store.should_synthesize() is True


@pytest.mark.asyncio
async def test_manual_supersede(store_dir: Path):
    store = _build_store(store_dir)
    await store.load()
    await store.apply_actions([
        {"op": "add", "summary": "x", "category": "fact", "scope": "global_user", "confidence": 0.7, "evidence": ""}
    ])
    entry = store.get_active()[0]
    ok = await store.manual_supersede(entry.id)
    assert ok is True
    refreshed = store.get_by_id(entry.id)
    assert refreshed.status == "superseded"
    assert refreshed.superseded_by is None
    # Idempotent: second call returns False
    again = await store.manual_supersede(entry.id)
    assert again is False


@pytest.mark.asyncio
async def test_get_relevant_scope_filtering(store_dir: Path):
    store = _build_store(store_dir)
    await store.load()
    await store.apply_actions([
        {"op": "add", "summary": "global preference", "category": "preference", "scope": "global_user", "confidence": 0.8, "evidence": ""},
        {"op": "add", "summary": "skill rule", "category": "workflow", "scope": "skill", "confidence": 0.85, "evidence": ""},
        {"op": "add", "summary": "workspace knowledge", "category": "project_knowledge", "scope": "workspace", "confidence": 0.75, "evidence": ""},
    ], skill_name="market-briefing", source_workspace="/repo")
    # Skill match boosts the skill-scoped entry to top
    relevant = store.get_relevant(skill_name="market-briefing", workspace="/repo", limit=10)
    assert relevant[0].scope in {"skill", "global_user"}
    # Without workspace match, the workspace entry is filtered out
    only_skill = store.get_relevant(skill_name="market-briefing", workspace="/other", limit=10)
    summaries = [m.summary for m in only_skill]
    assert "workspace knowledge" not in summaries


@pytest.mark.asyncio
async def test_synthesize_memory_md_writes_file(store_dir: Path):
    store = _build_store(store_dir)
    await store.load()
    await store.apply_actions([
        {"op": "add", "summary": "user likes tea", "category": "preference", "scope": "global_user", "confidence": 0.9, "evidence": ""},
    ])

    class FakeAgent:
        name = "fake"

    class FakeResponse:
        text = "# Memory\n\n## preference\n- You like tea\n"
        error = None

    class FakeRegistry:
        async def run(self, prompt, run_label=None):
            return FakeAgent(), FakeResponse()

    ok = await store.synthesize_memory_md(FakeRegistry())
    assert ok is True
    md_path = store_dir / "MEMORY.md"
    assert md_path.exists()
    assert "tea" in md_path.read_text()
    assert store.should_synthesize() is False


def test_parse_judge_actions_handles_fenced_and_object_forms():
    raw = """```json
{"actions": [{"op": "no_op", "reason": "ok"}]}
```"""
    actions = parse_judge_actions(raw)
    assert actions == [{"op": "no_op", "reason": "ok"}]

    raw_array = "[{\"op\":\"add\",\"summary\":\"x\",\"category\":\"fact\",\"scope\":\"global_user\",\"confidence\":0.7,\"evidence\":\"e\"}]"
    actions = parse_judge_actions(raw_array)
    assert actions[0]["op"] == "add"

    raw_with_prose = "Here is the result:\n{\"actions\":[{\"op\":\"no_op\",\"reason\":\"x\"}]}"
    actions = parse_judge_actions(raw_with_prose)
    assert actions == [{"op": "no_op", "reason": "x"}]

    assert parse_judge_actions("not json at all") == []
