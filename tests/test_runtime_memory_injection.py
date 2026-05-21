"""M0 PR2 — RuntimeService memory injection tests.

Verifies that RuntimeService._invoke_agent prepends a ``[Remembered context]``
block to the agent prompt when a JudgeStore is wired in, using the same
scope-filter semantics as the chat path (skill / workspace / automation
filters via JudgeStore.get_relevant).

These tests don't drive the full task lifecycle — they exercise the injection
hook directly via a stub agent that captures the prompt it receives.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from oh_my_agent.agents.base import AgentResponse, BaseAgent
from oh_my_agent.memory.judge_store import JudgeStore
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.runtime.service import RuntimeService
from oh_my_agent.runtime.types import RuntimeTask


class _CapturingAgent(BaseAgent):
    """Stub agent that records the prompt it was invoked with."""

    def __init__(self) -> None:
        self.captured_prompt: str | None = None

    @property
    def name(self) -> str:
        return "capture-agent"

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
        workspace_override: Path | None = None,
    ) -> AgentResponse:
        del history, thread_id, workspace_override
        self.captured_prompt = prompt
        return AgentResponse(text="ok\nTASK_STATE: DONE")


async def _make_runtime(tmp_path: Path, judge_store: JudgeStore | None):
    """Wire a minimal RuntimeService with optional judge_store."""
    db_path = tmp_path / "runtime.db"
    store = SQLiteMemoryStore(db_path)
    await store.init()
    runtime = RuntimeService(
        store,
        config={
            "enabled": True,
            "worker_concurrency": 1,
            "worktree_root": str(tmp_path / "worktrees"),
            "reports_dir": str(tmp_path / "reports"),
            "default_agent": "capture-agent",
            "default_test_command": "true",
            "default_max_steps": 4,
            "agent_heartbeat_seconds": 0.1,
        },
        repo_root=tmp_path,
        judge_store=judge_store,
        memory_inject_limit=5,
    )
    return runtime, store


def _make_task(**overrides: Any) -> RuntimeTask:
    """Build a RuntimeTask shell via from_row (which fills missing fields)."""
    row: dict[str, Any] = dict(
        id="t-test",
        platform="discord",
        channel_id="1",
        thread_id="thread-1",
        task_type="artifact",
        goal="do thing",
        original_request="do thing",
        status="RUNNING",
        max_steps=4,
        max_minutes=10,
        test_command="true",
        completion_mode="reply",
        created_at="2026-05-21T00:00:00+00:00",
    )
    row.update(overrides)
    return RuntimeTask.from_row(row)


@pytest.mark.asyncio
async def test_invoke_agent_injects_remembered_context(tmp_path: Path):
    judge_store = JudgeStore(memory_dir=tmp_path / "memory")
    await judge_store.load()
    # Add a global_user fact (always-injected baseline)
    await judge_store.apply_actions([
        {
            "op": "add",
            "summary": "user prefers terse",
            "category": "preference",
            "scope": "global_user",
            "confidence": 0.9,
        },
    ])

    runtime, store = await _make_runtime(tmp_path, judge_store)
    try:
        agent = _CapturingAgent()
        task = _make_task()
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)
        await runtime._invoke_agent(
            agent,
            prompt="please do X",
            workspace=workspace,
            runtime_thread_id="thread-1",
            task=task,
            step=1,
        )
    finally:
        await runtime.stop()
        await store.close()

    assert agent.captured_prompt is not None
    assert agent.captured_prompt.startswith("[Remembered context]\n")
    assert "- user prefers terse" in agent.captured_prompt
    # Original prompt preserved after the block
    assert agent.captured_prompt.endswith("please do X")


@pytest.mark.asyncio
async def test_invoke_agent_no_judge_store_no_injection(tmp_path: Path):
    runtime, store = await _make_runtime(tmp_path, judge_store=None)
    try:
        agent = _CapturingAgent()
        task = _make_task()
        await runtime._invoke_agent(
            agent,
            prompt="raw prompt",
            workspace=tmp_path,
            runtime_thread_id="thread-1",
            task=task,
            step=1,
        )
    finally:
        await runtime.stop()
        await store.close()

    assert agent.captured_prompt == "raw prompt"


@pytest.mark.asyncio
async def test_invoke_agent_no_relevant_memories_no_block(tmp_path: Path):
    """Empty judge_store → no block prepended even though judge_store is wired."""
    judge_store = JudgeStore(memory_dir=tmp_path / "memory")
    await judge_store.load()
    runtime, store = await _make_runtime(tmp_path, judge_store)
    try:
        agent = _CapturingAgent()
        task = _make_task()
        await runtime._invoke_agent(
            agent,
            prompt="raw",
            workspace=tmp_path,
            runtime_thread_id="thread-1",
            task=task,
            step=1,
        )
    finally:
        await runtime.stop()
        await store.close()

    assert agent.captured_prompt == "raw"


@pytest.mark.asyncio
async def test_invoke_agent_automation_scope_strict_filter(tmp_path: Path):
    """scope=automation entries only inject when task.automation_name matches."""
    judge_store = JudgeStore(memory_dir=tmp_path / "memory")
    await judge_store.load()
    await judge_store.apply_actions([
        {
            "op": "add",
            "summary": "auto-foo learned: cap at 500 words",
            "category": "self_eval",
            "scope": "automation",
            "source_automation": "auto-foo",
            "quality": "fail",
        },
    ])

    runtime, store = await _make_runtime(tmp_path, judge_store)
    try:
        agent_match = _CapturingAgent()
        task_match = _make_task(automation_name="auto-foo")
        await runtime._invoke_agent(
            agent_match,
            prompt="p1",
            workspace=tmp_path,
            runtime_thread_id="thread-1",
            task=task_match,
            step=1,
        )

        agent_mismatch = _CapturingAgent()
        task_mismatch = _make_task(automation_name="auto-bar")
        await runtime._invoke_agent(
            agent_mismatch,
            prompt="p2",
            workspace=tmp_path,
            runtime_thread_id="thread-1",
            task=task_mismatch,
            step=1,
        )

        agent_no_auto = _CapturingAgent()
        task_no_auto = _make_task(automation_name=None)  # manual task
        await runtime._invoke_agent(
            agent_no_auto,
            prompt="p3",
            workspace=tmp_path,
            runtime_thread_id="thread-1",
            task=task_no_auto,
            step=1,
        )
    finally:
        await runtime.stop()
        await store.close()

    assert "cap at 500 words" in (agent_match.captured_prompt or "")
    assert "cap at 500 words" not in (agent_mismatch.captured_prompt or "")
    assert agent_mismatch.captured_prompt == "p2"
    # Manual task (no automation_name) does NOT see automation-scope entries
    assert "cap at 500 words" not in (agent_no_auto.captured_prompt or "")
    assert agent_no_auto.captured_prompt == "p3"


@pytest.mark.asyncio
async def test_invoke_agent_injection_failure_does_not_crash(tmp_path: Path):
    """If judge_store.get_relevant raises, the agent still gets the raw prompt."""

    class _BrokenJudgeStore:
        def get_relevant(self, **_kwargs):
            raise RuntimeError("synthetic failure")

        @staticmethod
        def format_memory_block(entries):
            return ""

    runtime, store = await _make_runtime(tmp_path, judge_store=_BrokenJudgeStore())
    try:
        agent = _CapturingAgent()
        task = _make_task()
        await runtime._invoke_agent(
            agent,
            prompt="survive",
            workspace=tmp_path,
            runtime_thread_id="thread-1",
            task=task,
            step=1,
        )
    finally:
        await runtime.stop()
        await store.close()

    assert agent.captured_prompt == "survive"
