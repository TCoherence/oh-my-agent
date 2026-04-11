from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from oh_my_agent.agents.base import AgentResponse, BaseAgent
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.gateway.base import IncomingMessage
from oh_my_agent.gateway.manager import GatewayManager
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.runtime import RuntimeService, TASK_STATUS_COMPLETED, TASK_STATUS_WAITING_USER_INPUT
from tests.test_runtime_service import _FakeChannel, _init_git_repo


class _ThreadEchoAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "echo-agent"

    async def run(self, prompt, history=None, *, thread_id=None, workspace_override=None, log_path=None):
        del history, workspace_override, log_path
        await asyncio.sleep(0.02)
        return AgentResponse(text=f"reply:{thread_id}:{prompt}")


class _HoldingDoneAgent(BaseAgent):
    def __init__(self, *, expected_starts: int = 2) -> None:
        self._expected_starts = expected_starts
        self._started_count = 0
        self._start_lock = asyncio.Lock()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.workspace_paths: list[Path] = []
        self.thread_ids: list[str | None] = []

    @property
    def name(self) -> str:
        return "done-agent"

    async def run(self, prompt, history=None, *, thread_id=None, workspace_override=None, log_path=None):
        del prompt, history, log_path
        assert workspace_override is not None
        async with self._start_lock:
            self.workspace_paths.append(workspace_override)
            self.thread_ids.append(thread_id)
            self._started_count += 1
            if self._started_count >= self._expected_starts:
                self.started.set()
        await self.release.wait()
        out = workspace_override / "src" / f"{thread_id or 'task'}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("ok", encoding="utf-8")
        return AgentResponse(text="TASK_STATE: DONE")


@dataclass
class _GatewayChannel:
    platform: str = "discord"
    channel_id: str = "100"
    sent: list[tuple[str, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.sent = []

    async def create_thread(self, msg: IncomingMessage, name: str) -> str:
        del msg, name
        return "thread-new"

    async def send(self, thread_id: str, text: str) -> str:
        self.sent.append((thread_id, text))
        return f"m-{len(self.sent)}"

    async def stop(self) -> None:
        return None

    class _Typing:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def typing(self, thread_id: str):
        del thread_id
        return self._Typing()


async def _build_concurrent_runtime(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    db_path = tmp_path / "runtime.db"
    store = SQLiteMemoryStore(db_path)
    await store.init()
    cfg = {
        "enabled": True,
        "worker_concurrency": 2,
        "worktree_root": str(tmp_path / "worktrees"),
        "default_agent": "done-agent",
        "default_test_command": "true",
        "default_max_steps": 2,
        "default_max_minutes": 5,
        "risk_profile": "strict",
        "path_policy_mode": "allow_all_with_denylist",
        "allowed_paths": ["src/**", "tests/**", "docs/**", "skills/**", "pyproject.toml"],
        "denied_paths": ["config.yaml", ".env", ".workspace/**", ".git/**"],
        "decision_ttl_minutes": 60,
        "agent_heartbeat_seconds": 0.1,
        "test_heartbeat_seconds": 0.1,
        "test_timeout_seconds": 1.0,
        "progress_notice_seconds": 0.1,
        "progress_persist_seconds": 0.1,
        "log_event_limit": 20,
        "log_tail_chars": 400,
        "cleanup": {"enabled": False, "interval_minutes": 60, "retention_hours": 0},
        "merge_gate": {"enabled": True, "auto_commit": True, "require_clean_repo": True, "preflight_check": True},
    }
    runtime = RuntimeService(store, config=cfg, owner_user_ids={"owner-1"}, repo_root=repo)
    channel = _FakeChannel()
    return repo, store, runtime, channel


async def _wait_for_statuses(store: SQLiteMemoryStore, task_ids: list[str], expected: str, timeout: float = 3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        states = []
        for task_id in task_ids:
            task = await store.get_runtime_task(task_id)
            states.append(task.status if task is not None else None)
        if all(state == expected for state in states):
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"Timed out waiting for {task_ids} to reach {expected}: {states}")
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_two_threads_isolated_history():
    channel = _GatewayChannel()
    registry = AgentRegistry([_ThreadEchoAgent()])
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)
    manager = GatewayManager([])

    await asyncio.gather(
        manager.handle_message(
            session,
            registry,
            IncomingMessage(
                platform="discord",
                channel_id="100",
                thread_id="thread-a",
                author="alice",
                author_id="owner-1",
                content="alpha",
            ),
        ),
        manager.handle_message(
            session,
            registry,
            IncomingMessage(
                platform="discord",
                channel_id="100",
                thread_id="thread-b",
                author="alice",
                author_id="owner-1",
                content="beta",
            ),
        ),
    )

    history_a = await session.get_history("thread-a")
    history_b = await session.get_history("thread-b")
    assert [turn["content"] for turn in history_a] == ["alpha", "reply:thread-a:alpha"]
    assert [turn["content"] for turn in history_b] == ["beta", "reply:thread-b:beta"]


@pytest.mark.asyncio
async def test_two_tasks_isolated_workspace(tmp_path: Path):
    repo, store, runtime, channel = await _build_concurrent_runtime(tmp_path)
    agent = _HoldingDoneAgent(expected_starts=2)
    registry = AgentRegistry([agent])
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)
    runtime.register_session(session, registry)
    await runtime.start()

    try:
        first = await runtime.create_artifact_task(
            session=session,
            registry=registry,
            thread_id="thread-a",
            goal="build first artifact",
            raw_request="build first artifact",
            created_by="owner-1",
            preferred_agent="done-agent",
            source="test",
        )
        second = await runtime.create_artifact_task(
            session=session,
            registry=registry,
            thread_id="thread-b",
            goal="build second artifact",
            raw_request="build second artifact",
            created_by="owner-1",
            preferred_agent="done-agent",
            source="test",
        )

        await asyncio.wait_for(agent.started.wait(), timeout=2)
        assert len(agent.workspace_paths) == 2
        assert agent.workspace_paths[0] != agent.workspace_paths[1]

        agent.release.set()
        await _wait_for_statuses(store, [first.id, second.id], TASK_STATUS_COMPLETED)

        first_task = await store.get_runtime_task(first.id)
        second_task = await store.get_runtime_task(second.id)
        assert first_task is not None and second_task is not None
        assert first_task.workspace_path != second_task.workspace_path
    finally:
        await runtime.stop()
        await store.close()


@pytest.mark.asyncio
async def test_two_tasks_isolated_hitl(tmp_path: Path):
    _, store, runtime, channel = await _build_concurrent_runtime(tmp_path)
    registry = AgentRegistry([_ThreadEchoAgent()])
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)
    runtime.register_session(session, registry)

    try:
        first = await store.create_runtime_task(
            task_id="task-hitl-1",
            platform="discord",
            channel_id="100",
            thread_id="thread-a",
            created_by="owner-1",
            goal="wait one",
            preferred_agent="done-agent",
            status="RUNNING",
            max_steps=2,
            max_minutes=5,
            test_command="true",
        )
        second = await store.create_runtime_task(
            task_id="task-hitl-2",
            platform="discord",
            channel_id="100",
            thread_id="thread-b",
            created_by="owner-1",
            goal="wait two",
            preferred_agent="done-agent",
            status="RUNNING",
            max_steps=2,
            max_minutes=5,
            test_command="true",
        )

        await asyncio.gather(
            runtime.mark_task_ask_user_required(
                first.id,
                question="pick one",
                details=None,
                choices=({"id": "a", "label": "A", "description": None},),
                control_envelope_json="{}",
            ),
            runtime.mark_task_ask_user_required(
                second.id,
                question="pick two",
                details=None,
                choices=({"id": "b", "label": "B", "description": None},),
                control_envelope_json="{}",
            ),
        )

        first_prompt = await store.get_active_hitl_prompt_for_task(first.id)
        second_prompt = await store.get_active_hitl_prompt_for_task(second.id)
        assert first_prompt is not None and second_prompt is not None

        await runtime.answer_hitl_prompt(first_prompt.id, choice_id="a", actor_id="owner-1")

        first_task = await store.get_runtime_task(first.id)
        second_task = await store.get_runtime_task(second.id)
        second_prompt_after = await store.get_hitl_prompt(second_prompt.id)
        assert first_task is not None and first_task.status != TASK_STATUS_WAITING_USER_INPUT
        assert second_task is not None and second_task.status == TASK_STATUS_WAITING_USER_INPUT
        assert second_prompt_after is not None and second_prompt_after.status == "waiting"
    finally:
        await runtime.stop()
        await store.close()


@pytest.mark.asyncio
async def test_automation_and_manual_task_isolation(tmp_path: Path):
    _, store, runtime, channel = await _build_concurrent_runtime(tmp_path)
    agent = _HoldingDoneAgent(expected_starts=2)
    registry = AgentRegistry([agent])
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)
    runtime.register_session(session, registry)
    await runtime.start()

    try:
        manual_task, automation_task = await asyncio.gather(
            runtime.create_artifact_task(
                session=session,
                registry=registry,
                thread_id="thread-manual",
                goal="manual run",
                raw_request="manual run",
                created_by="owner-1",
                preferred_agent="done-agent",
                source="test",
            ),
            runtime.enqueue_scheduler_task(
                session=session,
                registry=registry,
                thread_id="thread-auto",
                automation_name="daily-brief",
                prompt="automation run",
                author="scheduler",
                preferred_agent="done-agent",
            ),
        )

        assert automation_task is not None
        await asyncio.wait_for(agent.started.wait(), timeout=2)
        assert manual_task.id != automation_task.id
        assert manual_task.automation_name is None
        assert automation_task.automation_name == "daily-brief"

        agent.release.set()
        await _wait_for_statuses(store, [manual_task.id, automation_task.id], TASK_STATUS_COMPLETED)
    finally:
        await runtime.stop()
        await store.close()
