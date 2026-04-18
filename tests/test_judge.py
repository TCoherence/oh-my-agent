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
