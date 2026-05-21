from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from oh_my_agent.memory.judge import Judge
from oh_my_agent.memory.judge_store import JudgeStore


@dataclass
class FakeResponse:
    text: str
    error: str | None = None


@dataclass
class FakeAgent:
    name: str = "fake"


class StubRegistry:
    def __init__(self, responses: list[str | tuple[str, str | None]]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str | None]] = []

    async def run(self, prompt: str, run_label: str | None = None):
        self.calls.append((prompt, run_label))
        if not self._responses:
            return FakeAgent(), FakeResponse(text="", error="no_response")
        nxt = self._responses.pop(0)
        if isinstance(nxt, tuple):
            text, err = nxt
        else:
            text, err = nxt, None
        return FakeAgent(), FakeResponse(text=text, error=err)


@pytest.mark.asyncio
async def test_judge_explicit_short_circuits_llm(tmp_path: Path):
    store = JudgeStore(memory_dir=tmp_path)
    await store.load()
    judge = Judge(store)
    registry = StubRegistry([])  # must not be called

    result = await judge.run(
        conversation=[{"role": "user", "content": "记一下我喜欢喝绿茶"}],
        registry=registry,
        thread_id="t1",
        explicit_summary="user prefers green tea",
        explicit_scope="global_user",
    )
    assert registry.calls == []
    assert result.error is None
    assert result.stats["add"] == 1
    assert any(a.get("op") == "add" for a in result.actions)


@pytest.mark.asyncio
async def test_judge_runs_llm_and_applies_actions(tmp_path: Path):
    store = JudgeStore(memory_dir=tmp_path)
    await store.load()
    judge = Judge(store)
    response = json.dumps(
        {
            "actions": [
                {
                    "op": "add",
                    "summary": "user prefers terse answers",
                    "category": "preference",
                    "scope": "global_user",
                    "confidence": 0.92,
                    "evidence": "make it short",
                },
                {"op": "no_op", "reason": "rest of conversation was task-specific"},
            ]
        }
    )
    registry = StubRegistry([response])

    result = await judge.run(
        conversation=[
            {"role": "user", "content": "make it short"},
            {"role": "assistant", "content": "ok"},
        ],
        registry=registry,
        thread_id="t1",
    )
    assert result.error is None
    assert result.stats["add"] == 1
    assert result.stats["no_op"] == 1
    active = store.get_active()
    assert any("terse" in m.summary for m in active)


@pytest.mark.asyncio
async def test_judge_falls_back_to_simplified_prompt_on_empty_actions(tmp_path: Path):
    store = JudgeStore(memory_dir=tmp_path)
    await store.load()
    judge = Judge(store)
    bad_response = "this isn't JSON at all"
    good_response = json.dumps({"actions": [{"op": "no_op", "reason": "n/a"}]})
    registry = StubRegistry([bad_response, good_response])

    result = await judge.run(
        conversation=[{"role": "user", "content": "hi"}],
        registry=registry,
        thread_id="t1",
    )
    assert result.error is None
    assert result.stats["no_op"] == 1
    # Two prompts should have been issued.
    labels = [c[1] for c in registry.calls]
    assert labels == ["memory_judge", "memory_judge_simplified"]


@pytest.mark.asyncio
async def test_judge_returns_error_when_agent_errors(tmp_path: Path):
    store = JudgeStore(memory_dir=tmp_path)
    await store.load()
    judge = Judge(store)
    registry = StubRegistry([("", "model_unavailable")])

    result = await judge.run(
        conversation=[{"role": "user", "content": "hi"}],
        registry=registry,
        thread_id="t1",
    )
    assert result.error == "model_unavailable"
    assert store.get_active() == []


@pytest.mark.asyncio
async def test_judge_with_empty_conversation_returns_no_op(tmp_path: Path):
    store = JudgeStore(memory_dir=tmp_path)
    await store.load()
    judge = Judge(store)
    registry = StubRegistry([])

    result = await judge.run(conversation=[], registry=registry, thread_id="t1")
    assert result.actions[0]["op"] == "no_op"
    assert result.stats["no_op"] == 1
    assert registry.calls == []


# =====================================================================
# M0 PR3 — Judge.run_for_task (mode=completion + mode=self_eval)
# =====================================================================


@pytest.mark.asyncio
async def test_run_for_task_completion_persists_actions(tmp_path: Path):
    store = JudgeStore(memory_dir=tmp_path)
    await store.load()
    judge = Judge(store)
    response = json.dumps({
        "actions": [
            {
                "op": "add",
                "summary": "the foo API returns paginated data",
                "category": "fact",
                "scope": "global_user",
                "confidence": 0.9,
                "evidence": "agent output discovered pagination",
            },
        ]
    })
    registry = StubRegistry([response])
    result = await judge.run_for_task(
        mode="completion",
        registry=registry,
        task_prompt="fetch foo data",
        task_output="fetched 100 rows over 4 pages of 25 each",
        automation_name="auto-foo",
        skill_name="foo-fetcher",
        source_workspace=str(tmp_path),
        thread_id="automation:auto-foo",
    )
    assert result.error is None
    assert result.stats["add"] == 1
    # Verify label is task_completion (distinct from chat memory_judge)
    assert registry.calls[0][1] == "memory_judge_task_completion"


@pytest.mark.asyncio
async def test_run_for_task_self_eval_writes_quality_entry(tmp_path: Path):
    store = JudgeStore(memory_dir=tmp_path)
    await store.load()
    judge = Judge(store)
    response = json.dumps({
        "quality": "fail",
        "reason": "output cut off mid-sentence",
        "suggested_improvement": "increase max_turns",
    })
    registry = StubRegistry([response])
    result = await judge.run_for_task(
        mode="self_eval",
        registry=registry,
        task_prompt="summarize the paper",
        task_output="The paper discusses... [output truncated]",
        automation_name="paper-digest",
        skill_name="paper-digest",
        source_workspace=str(tmp_path),
        thread_id="automation:paper-digest",
    )
    assert result.error is None
    assert result.stats["add"] == 1
    active = store.get_active()
    assert len(active) == 1
    entry = active[0]
    assert entry.category == "self_eval"
    assert entry.scope == "automation"
    assert entry.source_automation == "paper-digest"
    assert entry.feedback_source == "llm_judge"
    assert entry.quality == "fail"
    assert "reason=output cut off" in entry.summary
    assert "suggested=" in entry.summary
    # Verify label
    assert registry.calls[0][1] == "memory_judge_self_eval"


@pytest.mark.asyncio
async def test_run_for_task_self_eval_no_automation_name_no_action(tmp_path: Path):
    """self_eval translation requires automation_name; manual task gets no entry."""
    store = JudgeStore(memory_dir=tmp_path)
    await store.load()
    judge = Judge(store)
    response = json.dumps({"quality": "pass", "reason": "good"})
    registry = StubRegistry([response])
    result = await judge.run_for_task(
        mode="self_eval",
        registry=registry,
        task_prompt="x",
        task_output="y",
        automation_name=None,  # manual /task_start
    )
    assert result.error is None
    assert result.stats == {"add": 0, "strengthen": 0, "supersede": 0, "no_op": 0, "rejected": 0}
    assert store.get_active() == []


@pytest.mark.asyncio
async def test_run_for_task_self_eval_invalid_quality_returns_error(tmp_path: Path):
    store = JudgeStore(memory_dir=tmp_path)
    await store.load()
    judge = Judge(store)
    response = json.dumps({"quality": "amazing", "reason": "x"})  # invalid quality
    registry = StubRegistry([response])
    result = await judge.run_for_task(
        mode="self_eval",
        registry=registry,
        task_prompt="x",
        task_output="y",
        automation_name="auto-x",
    )
    # _coerce returns None → actions empty → persist still runs but writes nothing
    assert result.stats["add"] == 0
    assert store.get_active() == []


@pytest.mark.asyncio
async def test_run_for_task_self_eval_parse_failure_returns_error(tmp_path: Path):
    store = JudgeStore(memory_dir=tmp_path)
    await store.load()
    judge = Judge(store)
    registry = StubRegistry(["not json at all"])
    result = await judge.run_for_task(
        mode="self_eval",
        registry=registry,
        task_prompt="x",
        task_output="y",
        automation_name="auto-x",
    )
    assert result.error == "self_eval_parse_failed"


@pytest.mark.asyncio
async def test_run_for_task_completion_truncates_huge_inputs(tmp_path: Path):
    """Verify large prompts/outputs are truncated with a marker before being fed to LLM."""
    from oh_my_agent.memory.judge import _TASK_OUTPUT_MAX_CHARS, _TASK_PROMPT_MAX_CHARS

    store = JudgeStore(memory_dir=tmp_path)
    await store.load()
    judge = Judge(store)
    response = json.dumps({"actions": [{"op": "no_op", "reason": "ok"}]})
    registry = StubRegistry([response])
    huge_prompt = "P" * (_TASK_PROMPT_MAX_CHARS * 3)
    huge_output = "O" * (_TASK_OUTPUT_MAX_CHARS * 3)
    await judge.run_for_task(
        mode="completion",
        registry=registry,
        task_prompt=huge_prompt,
        task_output=huge_output,
        automation_name="auto-x",
    )
    sent_prompt = registry.calls[0][0]
    assert "[truncated]" in sent_prompt
    assert len(sent_prompt) < (_TASK_PROMPT_MAX_CHARS + _TASK_OUTPUT_MAX_CHARS + 5000)


def test_run_for_task_unknown_mode_raises(tmp_path: Path):
    import asyncio as aio
    store = JudgeStore(memory_dir=tmp_path)
    judge = Judge(store)
    registry = StubRegistry([])
    with pytest.raises(ValueError, match="unknown Judge.run_for_task mode"):
        aio.run(
            judge.run_for_task(
                mode="bogus",  # type: ignore[arg-type]
                registry=registry,
                task_prompt="x",
                task_output="y",
            )
        )


def test_coerce_self_eval_to_action_shapes():
    """_coerce_self_eval_to_action: structured-only happy path + invalid quality."""
    action = Judge._coerce_self_eval_to_action(
        {"quality": "fail", "reason": "r", "suggested_improvement": "s"},
        automation_name="auto-Y",
    )
    assert action is not None
    assert action["op"] == "add"
    assert action["category"] == "self_eval"
    assert action["scope"] == "automation"
    assert action["source_automation"] == "auto-Y"
    assert action["feedback_source"] == "llm_judge"
    assert action["quality"] == "fail"
    assert action["confidence"] == 0.6
    assert "reason=r; suggested=s" in action["summary"]

    none_action = Judge._coerce_self_eval_to_action(
        {"quality": "bogus"}, automation_name="auto-Y"
    )
    assert none_action is None


def test_try_parse_self_eval_handles_fenced_and_prose():
    from oh_my_agent.memory.judge import _try_parse_self_eval

    raw = '```json\n{"quality":"pass","reason":"ok"}\n```'
    assert _try_parse_self_eval(raw) == {"quality": "pass", "reason": "ok"}

    raw_with_prose = 'Here is the verdict:\n{"quality":"fail","reason":"x"}'
    assert _try_parse_self_eval(raw_with_prose) == {"quality": "fail", "reason": "x"}

    assert _try_parse_self_eval("nothing here") is None
    assert _try_parse_self_eval('{"no_quality":"oops"}') is None
    assert _try_parse_self_eval("") is None


def test_truncate_helper():
    from oh_my_agent.memory.judge import _truncate

    assert _truncate("", 100) == ""
    assert _truncate("short", 100) == "short"
    long = "x" * 200
    out = _truncate(long, 100)
    assert len(out) <= 100
    assert "[truncated]" in out
