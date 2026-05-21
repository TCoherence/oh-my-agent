from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import yaml

from oh_my_agent.memory.judge_store import (
    JudgeStore,
    MemoryEntry,
    MemoryStoreLoadError,
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
    stats = await store.apply_actions(actions, thread_id="t1", skill_name="market-briefing-ai")
    assert stats == {"add": 1, "strengthen": 0, "supersede": 0, "no_op": 1, "rejected": 0}
    active = store.get_active()
    assert len(active) == 1
    target = active[0]
    assert "market-briefing-ai" in target.source_skills

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
    ], skill_name="market-briefing-ai", source_workspace="/repo")
    # Skill match boosts the skill-scoped entry to top
    relevant = store.get_relevant(skill_name="market-briefing-ai", workspace="/repo", limit=10)
    assert relevant[0].scope in {"skill", "global_user"}
    # Without workspace match, the workspace entry is filtered out
    only_skill = store.get_relevant(skill_name="market-briefing-ai", workspace="/other", limit=10)
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


# =====================================================================
# M0 PR1 — automation memory + self-eval schema additions
# =====================================================================


def test_memory_entry_new_fields_default_to_none_or_empty():
    entry = MemoryEntry()
    assert entry.source_automation is None
    assert entry.feedback_source is None
    assert entry.signals == []
    assert entry.quality is None


def test_memory_entry_new_fields_round_trip():
    entry = MemoryEntry(
        summary="quality fail; reason=output too long",
        category="self_eval",
        scope="automation",
        source_automation="market-briefing-finance",
        feedback_source="llm_judge",
        signals=[{"source": "llm_judge", "value": "fail", "ts": "2026-05-21T00:00:00+00:00"}],
        quality="fail",
        confidence=0.5,
    )
    payload = entry.to_dict()
    assert payload["source_automation"] == "market-briefing-finance"
    assert payload["feedback_source"] == "llm_judge"
    assert payload["quality"] == "fail"
    assert payload["signals"][0]["source"] == "llm_judge"
    # Round-trip back
    revived = MemoryEntry.from_dict(payload)
    assert revived.source_automation == "market-briefing-finance"
    assert revived.feedback_source == "llm_judge"
    assert revived.quality == "fail"
    assert revived.signals[0]["value"] == "fail"


def test_memory_entry_rejects_self_eval_without_automation_scope():
    with pytest.raises(Exception):  # ValidationError wraps ValueError
        MemoryEntry(
            summary="x",
            category="self_eval",
            scope="global_user",  # wrong scope
            source_automation="foo",
        )


def test_memory_entry_rejects_self_eval_without_source_automation():
    with pytest.raises(Exception):
        MemoryEntry(
            summary="x",
            category="self_eval",
            scope="automation",
            source_automation=None,  # missing
        )


def test_memory_entry_rejects_automation_scope_without_self_eval():
    with pytest.raises(Exception):
        MemoryEntry(
            summary="x",
            category="fact",  # wrong category for scope=automation
            scope="automation",
            source_automation="foo",
        )


def test_memory_entry_invalid_feedback_source_coerces_to_none():
    entry = MemoryEntry(feedback_source="bogus")
    assert entry.feedback_source is None


def test_memory_entry_invalid_quality_coerces_to_none():
    entry = MemoryEntry(quality="amazing")
    assert entry.quality is None


@pytest.mark.asyncio
async def test_apply_add_writes_new_fields_for_self_eval(store_dir: Path):
    store = _build_store(store_dir)
    await store.load()
    stats = await store.apply_actions([
        {
            "op": "add",
            "summary": "reason=too long; suggested=cap at 500 words",
            "category": "self_eval",
            "scope": "automation",
            "source_automation": "market-briefing-finance",
            "feedback_source": "llm_judge",
            "quality": "fail",
            "signals": [{"source": "llm_judge", "value": "fail"}],
            "confidence": 0.6,
            "evidence": "agent self-eval",
        },
    ])
    assert stats["add"] == 1
    active = store.get_active()
    assert len(active) == 1
    entry = active[0]
    assert entry.category == "self_eval"
    assert entry.scope == "automation"
    assert entry.source_automation == "market-briefing-finance"
    assert entry.feedback_source == "llm_judge"
    assert entry.quality == "fail"
    assert entry.signals[0]["value"] == "fail"


@pytest.mark.asyncio
async def test_apply_add_rejects_invalid_self_eval_combination(store_dir: Path):
    store = _build_store(store_dir)
    await store.load()
    # category=self_eval without source_automation → model_validator rejects
    stats = await store.apply_actions([
        {
            "op": "add",
            "summary": "x",
            "category": "self_eval",
            "scope": "automation",
            # no source_automation
        },
    ])
    # _apply_add catches the ValidationError and returns False → rejected
    assert stats["add"] == 0
    assert stats["rejected"] == 1


@pytest.mark.asyncio
async def test_apply_supersede_inherits_new_fields(store_dir: Path):
    store = _build_store(store_dir)
    await store.load()
    await store.apply_actions([
        {
            "op": "add",
            "summary": "v1",
            "category": "self_eval",
            "scope": "automation",
            "source_automation": "auto-X",
            "feedback_source": "llm_judge",
            "quality": "borderline",
        },
    ])
    target = store.get_active()[0]
    await store.apply_actions([
        {
            "op": "supersede",
            "old_id": target.id,
            "new_summary": "v2 better reason",
            "category": "self_eval",
            "scope": "automation",
            # No source_automation in supersede action — should inherit from old
            "feedback_source": "explicit",  # override
            "quality": "fail",  # override
        },
    ])
    superseded = store.get_by_id(target.id)
    assert superseded.status == "superseded"
    new_entry = store.get_by_id(superseded.superseded_by)
    assert new_entry.source_automation == "auto-X"  # inherited
    assert new_entry.feedback_source == "explicit"  # overridden
    assert new_entry.quality == "fail"  # overridden


@pytest.mark.asyncio
async def test_get_relevant_automation_scope_strict_match(store_dir: Path):
    store = _build_store(store_dir)
    await store.load()
    await store.apply_actions([
        {
            "op": "add",
            "summary": "automation foo memory",
            "category": "self_eval",
            "scope": "automation",
            "source_automation": "foo",
            "quality": "pass",
        },
        {
            "op": "add",
            "summary": "automation bar memory",
            "category": "self_eval",
            "scope": "automation",
            "source_automation": "bar",
            "quality": "fail",
        },
    ])
    # Asking for "foo" → only foo entry
    foo_relevant = store.get_relevant(automation_name="foo", limit=10)
    assert len(foo_relevant) == 1
    assert foo_relevant[0].source_automation == "foo"
    # Asking for "bar" → only bar entry
    bar_relevant = store.get_relevant(automation_name="bar", limit=10)
    assert len(bar_relevant) == 1
    assert bar_relevant[0].source_automation == "bar"
    # Chat path (no automation_name) → neither
    chat_relevant = store.get_relevant(limit=10)
    auto_in_chat = [e for e in chat_relevant if e.scope == "automation"]
    assert auto_in_chat == []


@pytest.mark.asyncio
async def test_get_relevant_mixed_scopes_with_automation(store_dir: Path):
    store = _build_store(store_dir)
    await store.load()
    await store.apply_actions([
        {"op": "add", "summary": "global pref", "category": "preference", "scope": "global_user", "confidence": 0.8},
        {
            "op": "add",
            "summary": "auto self-eval",
            "category": "self_eval",
            "scope": "automation",
            "source_automation": "foo",
            "quality": "pass",
        },
    ])
    # Automation context: sees both (global_user + matching automation)
    both = store.get_relevant(automation_name="foo", limit=10)
    summaries = {m.summary for m in both}
    assert "global pref" in summaries
    assert "auto self-eval" in summaries
    # Chat context: only global_user
    chat = store.get_relevant(limit=10)
    summaries = {m.summary for m in chat}
    assert "global pref" in summaries
    assert "auto self-eval" not in summaries


@pytest.mark.asyncio
async def test_load_skips_malformed_entries_with_quarantine(store_dir: Path, caplog):
    # Pre-write a file with mixed valid + malformed entries
    store_dir.mkdir(parents=True, exist_ok=True)
    entries_path = store_dir / "memories.yaml"
    payload = [
        # Valid entry
        {"id": "abc", "summary": "valid one", "category": "fact", "scope": "global_user", "confidence": 0.7, "status": "active"},
        # Malformed: self_eval without automation (model_validator rejects)
        {"id": "bad1", "summary": "broken", "category": "self_eval", "scope": "global_user", "status": "active"},
        # Non-dict
        "not a dict at all",
    ]
    entries_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    store = _build_store(store_dir)
    await store.load()
    # Loaded valid one
    assert len(store.get_active()) == 1
    assert store.last_load_stats == {"loaded": 1, "skipped": 2}
    # Quarantine backup was written
    quarantines = list(store_dir.glob("memories.yaml.quarantine-*"))
    assert len(quarantines) == 1, f"expected exactly one quarantine backup, got {quarantines}"
    # Quarantine has the original contents
    revived = yaml.safe_load(quarantines[0].read_text())
    assert len(revived) == 3
    assert any(isinstance(x, dict) and x.get("id") == "bad1" for x in revived)
    # Store is NOT in read-only mode (per-entry skip doesn't flip it)
    assert store.is_readonly is False


@pytest.mark.asyncio
async def test_load_top_level_malformation_raises_and_engages_readonly(store_dir: Path):
    store_dir.mkdir(parents=True, exist_ok=True)
    entries_path = store_dir / "memories.yaml"
    # Top-level dict instead of list → fatal
    entries_path.write_text(yaml.safe_dump({"oops": "not a list"}), encoding="utf-8")

    store = _build_store(store_dir)
    with pytest.raises(MemoryStoreLoadError):
        await store.load()
    assert store.is_readonly is True


@pytest.mark.asyncio
async def test_load_empty_file_is_clean(store_dir: Path):
    store_dir.mkdir(parents=True, exist_ok=True)
    entries_path = store_dir / "memories.yaml"
    entries_path.write_text("", encoding="utf-8")

    store = _build_store(store_dir)
    await store.load()  # no exception
    assert store.is_readonly is False
    assert store.get_active() == []


@pytest.mark.asyncio
async def test_save_short_circuits_in_readonly_mode(store_dir: Path):
    store_dir.mkdir(parents=True, exist_ok=True)
    entries_path = store_dir / "memories.yaml"
    original = "not a list"
    entries_path.write_text(yaml.safe_dump({"bad": original}), encoding="utf-8")
    pre_size = entries_path.stat().st_size

    store = _build_store(store_dir)
    with pytest.raises(MemoryStoreLoadError):
        await store.load()
    assert store.is_readonly is True

    # apply_actions should not corrupt the file even though in-memory is empty
    # We bypass the lock to force a save attempt
    await store.save()
    post_size = entries_path.stat().st_size
    assert post_size == pre_size  # file untouched


@pytest.mark.asyncio
async def test_load_garbled_yaml_engages_readonly(store_dir: Path):
    store_dir.mkdir(parents=True, exist_ok=True)
    entries_path = store_dir / "memories.yaml"
    # Invalid YAML
    entries_path.write_text("::: not valid yaml :::\n  - mixed\n - indentation", encoding="utf-8")

    store = _build_store(store_dir)
    await store.load()  # warning path, not raise
    # The read fails OR the parse fails → readonly engaged
    assert store.is_readonly is True
    # File preserved
    assert entries_path.exists()


@pytest.mark.asyncio
async def test_synthesize_memory_md_renders_self_eval_separately(store_dir: Path):
    store = _build_store(store_dir)
    await store.load()
    await store.apply_actions([
        {"op": "add", "summary": "user likes tea", "category": "preference", "scope": "global_user", "confidence": 0.9, "evidence": ""},
        {
            "op": "add",
            "summary": "reason=too long",
            "category": "self_eval",
            "scope": "automation",
            "source_automation": "market-briefing-finance",
            "feedback_source": "llm_judge",
            "quality": "fail",
        },
    ])

    class FakeAgent:
        name = "fake"

    class FakeResponse:
        text = "## preference\n- You like tea\n"
        error = None

    class FakeRegistry:
        async def run(self, prompt, run_label=None):
            return FakeAgent(), FakeResponse()

    ok = await store.synthesize_memory_md(FakeRegistry())
    assert ok is True
    md = (store_dir / "MEMORY.md").read_text()
    assert "You like tea" in md
    # Self-eval section rendered separately, non-LLM
    assert "Past Self-Evaluations" in md
    assert "market-briefing-finance" in md
    assert "[fail]" in md


@pytest.mark.asyncio
async def test_get_relevant_automation_no_match_excludes(store_dir: Path):
    store = _build_store(store_dir)
    await store.load()
    await store.apply_actions([
        {
            "op": "add",
            "summary": "auto foo",
            "category": "self_eval",
            "scope": "automation",
            "source_automation": "foo",
            "quality": "pass",
        },
    ])
    # Wrong automation name → excluded
    relevant = store.get_relevant(automation_name="other", limit=10)
    assert relevant == []


@pytest.mark.asyncio
async def test_apply_supersede_inherits_quality_when_action_omits_it(store_dir: Path):
    """Round-4/PR1: explicitly verify supersede inheritance vs override semantics."""
    store = _build_store(store_dir)
    await store.load()
    await store.apply_actions([
        {
            "op": "add",
            "summary": "v1",
            "category": "self_eval",
            "scope": "automation",
            "source_automation": "auto-Y",
            "feedback_source": "llm_judge",
            "quality": "borderline",
        },
    ])
    target = store.get_active()[0]

    # Supersede with NO quality / feedback_source / source_automation fields:
    # all three should inherit from old entry.
    await store.apply_actions([
        {
            "op": "supersede",
            "old_id": target.id,
            "new_summary": "v2 same observation, just rephrased",
            "category": "self_eval",
            "scope": "automation",
        },
    ])
    superseded = store.get_by_id(target.id)
    new_entry = store.get_by_id(superseded.superseded_by)
    assert new_entry.quality == "borderline"  # inherited
    assert new_entry.feedback_source == "llm_judge"  # inherited
    assert new_entry.source_automation == "auto-Y"  # inherited

    # Now supersede again WITH explicit overrides
    await store.apply_actions([
        {
            "op": "supersede",
            "old_id": new_entry.id,
            "new_summary": "v3 user disagreed",
            "category": "self_eval",
            "scope": "automation",
            "quality": "fail",  # override
            "feedback_source": "explicit",  # override
            # source_automation NOT provided → still inherits
        },
    ])
    v3 = store.get_by_id(store.get_by_id(new_entry.id).superseded_by)
    assert v3.quality == "fail"
    assert v3.feedback_source == "explicit"
    assert v3.source_automation == "auto-Y"  # still inherited
