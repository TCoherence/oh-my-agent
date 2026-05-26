"""M0 PR2 — RuntimeService memory injection tests.

Verifies that RuntimeService._invoke_agent prepends a ``[Remembered context]``
block to the agent prompt when a JudgeStore is wired in, using the same
scope-filter semantics as the chat path (skill / workspace / automation
filters via JudgeStore.get_relevant).

These tests don't drive the full task lifecycle — they exercise the injection
hook directly via a stub agent that captures the prompt it receives.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from oh_my_agent.agents.base import AgentResponse, BaseAgent
from oh_my_agent.memory.judge_store import JudgeStore
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.runtime.service import RuntimeService
from oh_my_agent.runtime.types import RuntimeTask


class _CapturingAgent(BaseAgent):
    """Stub agent that records the prompt + ambient_context it was invoked with."""

    def __init__(self) -> None:
        self.captured_prompt: str | None = None
        self.captured_ambient: str | None = None

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
        ambient_context: str | None = None,
    ) -> AgentResponse:
        del history, thread_id, workspace_override
        self.captured_prompt = prompt
        self.captured_ambient = ambient_context
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

    # Memory now rides ambient_context (delivered out-of-band per agent), NOT
    # baked into the user prompt — so claude can route it to a system channel.
    assert agent.captured_ambient is not None
    assert agent.captured_ambient.startswith("[Remembered context]\n")
    assert "- user prefers terse" in agent.captured_ambient
    # User prompt stays clean.
    assert agent.captured_prompt == "please do X"


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

    assert "cap at 500 words" in (agent_match.captured_ambient or "")
    assert "cap at 500 words" not in (agent_mismatch.captured_ambient or "")
    assert agent_mismatch.captured_ambient is None
    assert agent_mismatch.captured_prompt == "p2"
    # Manual task (no automation_name) does NOT see automation-scope entries
    assert "cap at 500 words" not in (agent_no_auto.captured_ambient or "")
    assert agent_no_auto.captured_ambient is None
    assert agent_no_auto.captured_prompt == "p3"


@pytest.mark.asyncio
async def test_invoke_thread_agent_recomputes_ambient_on_continuation(
    tmp_path: Path, monkeypatch
):
    """Auth/HITL continuation re-supplies CURRENT memory via ambient_context
    (recomputed at resume time), not whatever was active when the run paused.
    The stored resume prompt itself stays clean (memory delivered out-of-band)."""
    judge_store = JudgeStore(memory_dir=tmp_path / "memory")
    await judge_store.load()
    await judge_store.apply_actions([
        {
            "op": "add",
            "summary": "likes bullet points",
            "category": "preference",
            "scope": "global_user",
            "confidence": 0.9,
        },
    ])
    runtime, store = await _make_runtime(tmp_path, judge_store)

    async def _noop_record(**_kwargs):
        return None

    monkeypatch.setattr(runtime, "_record_thread_agent_run", _noop_record)

    class _Sess:
        platform = "discord"
        channel_id = "1"

        async def get_history(self, _tid):
            return []

    from oh_my_agent.agents.registry import AgentRegistry

    try:
        agent1 = _CapturingAgent()
        await runtime._invoke_thread_agent(
            registry=AgentRegistry([agent1]),
            session=_Sess(),
            prompt="continue now",
            thread_id="thread-1",
            force_agent="capture-agent",
            log_path=None,
            purpose="auth_resume",
        )
        first = agent1.captured_ambient

        # Memory changes between pause and continuation.
        await judge_store.apply_actions([
            {
                "op": "add",
                "summary": "prefers Chinese replies",
                "category": "preference",
                "scope": "global_user",
                "confidence": 0.9,
            },
        ])
        agent2 = _CapturingAgent()
        await runtime._invoke_thread_agent(
            registry=AgentRegistry([agent2]),
            session=_Sess(),
            prompt="continue again",
            thread_id="thread-1",
            force_agent="capture-agent",
            log_path=None,
            purpose="auth_resume",
        )
        second = agent2.captured_ambient
    finally:
        await runtime.stop()
        await store.close()

    assert first is not None and "likes bullet points" in first
    assert "prefers Chinese replies" not in first
    # Recomputed at continuation → reflects the newer memory.
    assert second is not None and "prefers Chinese replies" in second
    # Stored resume prompt stays clean (memory delivered out-of-band).
    assert agent1.captured_prompt == "continue now"


# =====================================================================
# M0 PR3 — _spawn_post_completion_judge + task.judge_extracted event
# =====================================================================


class _RecordingJudge:
    """Stand-in for memory.judge.Judge that records the call args + returns
    a configurable JudgeResult."""

    def __init__(self, *, stats=None, error=None, actions=None, raise_exc=None):
        from oh_my_agent.memory.judge import JudgeResult

        self.calls: list[dict] = []
        self._stats = stats or {"add": 1, "strengthen": 0, "supersede": 0, "no_op": 0, "rejected": 0}
        self._error = error
        self._actions = actions or [{"op": "add", "summary": "x"}]
        self._raise = raise_exc
        self.JudgeResult = JudgeResult

    async def run_for_task(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise is not None:
            raise self._raise
        return self.JudgeResult(
            actions=self._actions,
            stats=self._stats,
            raw_response="",
            error=self._error,
        )


@pytest.mark.asyncio
async def test_spawn_post_completion_judge_no_op_when_judge_none(tmp_path: Path):
    """No memory_judge wired → spawn helper is a no-op (no crash)."""
    runtime, store = await _make_runtime(tmp_path, judge_store=None)
    try:
        from oh_my_agent.agents.registry import AgentRegistry

        registry = AgentRegistry([_CapturingAgent()])
        task = _make_task(automation_name="auto-x")
        # Should silently do nothing
        runtime._spawn_post_completion_judge(
            task=task, registry=registry, output_text="anything"
        )
        # No background task spawned
        assert runtime._background_judge_tasks == set()
    finally:
        await runtime.stop()
        await store.close()


@pytest.mark.asyncio
async def test_spawn_post_completion_judge_no_op_when_output_empty(tmp_path: Path):
    """Empty output → judge not invoked (nothing to extract from)."""
    runtime, store = await _make_runtime(tmp_path, judge_store=None)
    runtime._memory_judge = _RecordingJudge()
    try:
        from oh_my_agent.agents.registry import AgentRegistry

        registry = AgentRegistry([_CapturingAgent()])
        task = _make_task(automation_name="auto-x")
        runtime._spawn_post_completion_judge(
            task=task, registry=registry, output_text="   "
        )
        assert runtime._memory_judge.calls == []  # type: ignore[union-attr]
    finally:
        await runtime.stop()
        await store.close()


@pytest.mark.asyncio
async def test_spawn_post_completion_judge_writes_event(tmp_path: Path):
    """Happy path: judge runs, task.judge_extracted event lands."""
    runtime, store = await _make_runtime(tmp_path, judge_store=None)
    recorder = _RecordingJudge(stats={"add": 2, "strengthen": 0, "supersede": 0, "no_op": 0, "rejected": 0})
    runtime._memory_judge = recorder
    try:
        from oh_my_agent.agents.registry import AgentRegistry

        registry = AgentRegistry([_CapturingAgent()])
        task = _make_task(
            id="t-judge-1",
            automation_name="auto-foo",
            skill_name="foo-skill",
            goal="do thing",
        )
        # Pre-create the task row so add_runtime_event can land
        await store.create_runtime_task(
            task_id=task.id,
            platform=task.platform,
            channel_id=task.channel_id,
            thread_id=task.thread_id,
            created_by="test",
            goal=task.goal,
            original_request=task.original_request,
            preferred_agent=None,
            status="COMPLETED",
            max_steps=task.max_steps,
            max_minutes=task.max_minutes,
            test_command=task.test_command,
            task_type=task.task_type,
            completion_mode=task.completion_mode,
            skill_name=task.skill_name,
            automation_name=task.automation_name,
        )
        runtime._spawn_post_completion_judge(
            task=task, registry=registry, output_text="some output"
        )
        # Wait for background task to complete
        await asyncio.gather(*runtime._background_judge_tasks, return_exceptions=True)
    finally:
        await runtime.stop()
        await store.close()

    # Verify the judge was called with expected kwargs
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["mode"] == "completion"
    assert call["automation_name"] == "auto-foo"
    assert call["skill_name"] == "foo-skill"
    assert call["task_prompt"] == "do thing"
    assert call["task_output"] == "some output"
    assert call["thread_id"] == "automation:auto-foo"

    # Verify the event landed in store
    events = await store.list_runtime_events(task.id)
    judge_events = [e for e in events if e["event_type"] == "task.judge_extracted"]
    assert len(judge_events) == 1
    assert judge_events[0]["payload"]["actions_count"] == 1
    assert judge_events[0]["payload"]["stats"]["add"] == 2
    assert judge_events[0]["payload"]["mode"] == "completion"


@pytest.mark.asyncio
async def test_spawn_post_completion_judge_handles_exception(tmp_path: Path):
    """Judge raises → event still written with error field, lifecycle never crashes."""
    runtime, store = await _make_runtime(tmp_path, judge_store=None)
    recorder = _RecordingJudge(raise_exc=RuntimeError("synthetic"))
    runtime._memory_judge = recorder
    try:
        from oh_my_agent.agents.registry import AgentRegistry

        registry = AgentRegistry([_CapturingAgent()])
        task = _make_task(id="t-judge-err", automation_name="auto-x")
        await store.create_runtime_task(
            task_id=task.id,
            platform=task.platform,
            channel_id=task.channel_id,
            thread_id=task.thread_id,
            created_by="test",
            goal=task.goal,
            original_request=task.original_request,
            preferred_agent=None,
            status="COMPLETED",
            max_steps=task.max_steps,
            max_minutes=task.max_minutes,
            test_command=task.test_command,
            task_type=task.task_type,
            completion_mode=task.completion_mode,
            automation_name=task.automation_name,
        )
        runtime._spawn_post_completion_judge(
            task=task, registry=registry, output_text="output"
        )
        await asyncio.gather(*runtime._background_judge_tasks, return_exceptions=True)
    finally:
        await runtime.stop()
        await store.close()

    events = await store.list_runtime_events(task.id)
    judge_events = [e for e in events if e["event_type"] == "task.judge_extracted"]
    assert len(judge_events) == 1
    assert "synthetic" in judge_events[0]["payload"]["error"]
    assert judge_events[0]["payload"]["actions_count"] == 0


@pytest.mark.asyncio
async def test_runtime_stop_drains_background_judge_tasks(tmp_path: Path):
    """Round-1 codex catch: stop() must drain pending judge tasks so they
    don't race the store being closed by boot."""
    runtime, store = await _make_runtime(tmp_path, judge_store=None)

    class _BlockingJudge:
        """Judge that blocks until released, used to test shutdown drain."""

        def __init__(self):
            self.released = asyncio.Event()
            self.entered = asyncio.Event()

        async def run_for_task(self, **kwargs):
            from oh_my_agent.memory.judge import JudgeResult

            self.entered.set()
            # Stays blocked unless released; shutdown should cancel us.
            await self.released.wait()
            return JudgeResult(actions=[], stats={"add": 0, "strengthen": 0, "supersede": 0, "no_op": 0, "rejected": 0})

    blocking = _BlockingJudge()
    runtime._memory_judge = blocking
    try:
        from oh_my_agent.agents.registry import AgentRegistry

        registry = AgentRegistry([_CapturingAgent()])
        task = _make_task(id="t-block-1", automation_name="auto-y")
        await store.create_runtime_task(
            task_id=task.id,
            platform=task.platform,
            channel_id=task.channel_id,
            thread_id=task.thread_id,
            created_by="test",
            goal=task.goal,
            original_request=task.original_request,
            preferred_agent=None,
            status="COMPLETED",
            max_steps=task.max_steps,
            max_minutes=task.max_minutes,
            test_command=task.test_command,
            task_type=task.task_type,
            completion_mode=task.completion_mode,
            automation_name=task.automation_name,
        )
        runtime._spawn_post_completion_judge(
            task=task, registry=registry, output_text="output"
        )
        # Confirm the judge actually started before we stop
        await asyncio.wait_for(blocking.entered.wait(), timeout=2)
        assert len(runtime._background_judge_tasks) == 1
        # Stop with judge still blocked. Should not hang; should cancel.
        # Use shorter sleep here by tweaking the timeout via overriding the
        # _background drain to be faster — actually rely on the 5s in code.
        await asyncio.wait_for(runtime.stop(), timeout=7)
        # All judge tasks resolved (cancelled or completed) after stop
        pending = [t for t in runtime._background_judge_tasks if not t.done()]
        assert pending == []
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_spawn_post_completion_judge_manual_task_uses_task_thread(tmp_path: Path):
    """Manual /task_start (no automation_name) → synthetic thread is task:<id>."""
    runtime, store = await _make_runtime(tmp_path, judge_store=None)
    recorder = _RecordingJudge()
    runtime._memory_judge = recorder
    try:
        from oh_my_agent.agents.registry import AgentRegistry

        registry = AgentRegistry([_CapturingAgent()])
        task = _make_task(id="t-manual-1", automation_name=None)
        await store.create_runtime_task(
            task_id=task.id,
            platform=task.platform,
            channel_id=task.channel_id,
            thread_id=task.thread_id,
            created_by="test",
            goal=task.goal,
            original_request=task.original_request,
            preferred_agent=None,
            status="COMPLETED",
            max_steps=task.max_steps,
            max_minutes=task.max_minutes,
            test_command=task.test_command,
            task_type=task.task_type,
            completion_mode=task.completion_mode,
        )
        runtime._spawn_post_completion_judge(
            task=task, registry=registry, output_text="manual output"
        )
        await asyncio.gather(*runtime._background_judge_tasks, return_exceptions=True)
    finally:
        await runtime.stop()
        await store.close()

    assert recorder.calls[0]["thread_id"] == "task:t-manual-1"
    assert recorder.calls[0]["automation_name"] is None


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
