from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from oh_my_agent.agents.base import AgentResponse, BaseAgent
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.gateway.base import IncomingMessage
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.runtime import (
    RuntimeService,
    TASK_STATUS_APPLIED,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_DRAFT,
    TASK_STATUS_FAILED,
)


@dataclass
class _FakeChannel:
    platform: str = "discord"
    channel_id: str = "100"

    def __post_init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.drafts: list[dict] = []
        self.signals: list[tuple[str, str | None, str]] = []

    async def send(self, thread_id: str, text: str) -> None:
        self.sent.append((thread_id, text))

    async def send_task_draft(
        self,
        *,
        thread_id: str,
        draft_text: str,
        task_id: str,
        nonce: str,
        actions: list[str],
    ) -> str | None:
        self.drafts.append(
            {
                "thread_id": thread_id,
                "draft_text": draft_text,
                "task_id": task_id,
                "nonce": nonce,
                "actions": actions,
            }
        )
        return f"msg-{task_id}"

    async def signal_task_status(self, thread_id: str, message_id: str | None, emoji: str) -> None:
        self.signals.append((thread_id, message_id, emoji))


class _DoneAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "done-agent"

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
        workspace_override: Path | None = None,
    ) -> AgentResponse:
        del history, thread_id
        assert workspace_override is not None
        out = workspace_override / "src" / "runtime.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("ok", encoding="utf-8")
        return AgentResponse(text=f"{prompt}\nTASK_STATE: DONE")


class _BlockedOnceAgent(BaseAgent):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "blocked-once"

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
        workspace_override: Path | None = None,
    ) -> AgentResponse:
        del history, thread_id
        assert workspace_override is not None
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(text="Need extra context\nTASK_STATE: BLOCKED\nBLOCK_REASON: missing fixture")
        out = workspace_override / "src" / "unblocked.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("resumed", encoding="utf-8")
        return AgentResponse(text=f"{prompt}\nTASK_STATE: DONE")


class _DeniedPathAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "denied-agent"

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
        workspace_override: Path | None = None,
    ) -> AgentResponse:
        del prompt, history, thread_id
        assert workspace_override is not None
        # Should trigger denied path guard.
        (workspace_override / "config.yaml").write_text("oops: true\n", encoding="utf-8")
        return AgentResponse(text="changed sensitive file\nTASK_STATE: DONE")


def _init_git_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("# runtime test\n", encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Runtime Test"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)


async def _wait_for_status(store: SQLiteMemoryStore, task_id: str, expected: set[str], timeout: float = 8.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        task = await store.get_runtime_task(task_id)
        if task and task.status in expected:
            return task
        await asyncio.sleep(0.2)
    task = await store.get_runtime_task(task_id)
    raise AssertionError(f"Task {task_id} did not reach {expected}, got {task.status if task else 'missing'}")


@pytest.fixture
async def runtime_env(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    db_path = tmp_path / "runtime.db"
    store = SQLiteMemoryStore(db_path)
    await store.init()

    cfg = {
        "enabled": True,
        "worker_concurrency": 1,
        "worktree_root": str(tmp_path / "worktrees"),
        "default_agent": "done-agent",
        "default_test_command": "true",
        "default_max_steps": 8,
        "default_max_minutes": 20,
        "risk_profile": "strict",
        "allowed_paths": ["src/**", "tests/**", "docs/**", "skills/**", "pyproject.toml"],
        "denied_paths": ["config.yaml", ".env", ".workspace/**"],
        "decision_ttl_minutes": 60,
    }
    runtime = RuntimeService(store, config=cfg, owner_user_ids={"owner-1"}, repo_root=repo)
    channel = _FakeChannel()

    yield {
        "repo": repo,
        "store": store,
        "runtime": runtime,
        "channel": channel,
    }

    await runtime.stop()
    await store.close()


@pytest.mark.asyncio
async def test_runtime_message_intent_draft_and_approve_flow(runtime_env):
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_DoneAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)
    await runtime.start()

    msg = IncomingMessage(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        author="owner",
        author_id="owner-1",
        content="please fix and pip install missing deps then run tests",
    )
    handled = await runtime.maybe_handle_incoming(session, registry, msg, thread_id="thread-1")
    assert handled is True
    assert len(channel.drafts) == 1

    tasks = await store.list_runtime_tasks(platform="discord", channel_id="100")
    assert len(tasks) == 1
    assert tasks[0].status == TASK_STATUS_DRAFT

    event = await runtime.build_slash_decision_event(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        task_id=tasks[0].id,
        action="approve",
        actor_id="owner-1",
    )
    assert event is not None
    result = await runtime.handle_decision_event(event)
    assert "approved" in result.lower()

    done = await _wait_for_status(store, tasks[0].id, {TASK_STATUS_APPLIED})
    assert done.status == TASK_STATUS_APPLIED
    assert any(sig[2] == "✅" for sig in channel.signals)


@pytest.mark.asyncio
async def test_runtime_blocked_then_resume(runtime_env):
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    agent = _BlockedOnceAgent()
    registry = AgentRegistry([agent])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)
    await runtime.start()

    task = await runtime.create_task(
        session=session,
        registry=registry,
        thread_id="thread-2",
        goal="fix parser and run tests",
        created_by="owner-1",
        source="slash",
    )
    blocked = await _wait_for_status(store, task.id, {TASK_STATUS_BLOCKED})
    assert blocked.status == TASK_STATUS_BLOCKED

    resume = await runtime.resume_task(task.id, "fixture is available now", actor_id="owner-1")
    assert "resumed" in resume.lower()

    applied = await _wait_for_status(store, task.id, {TASK_STATUS_APPLIED})
    assert applied.status == TASK_STATUS_APPLIED
    assert agent.calls >= 2


@pytest.mark.asyncio
async def test_runtime_path_guard_marks_failed(runtime_env):
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_DeniedPathAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)
    await runtime.start()

    task = await runtime.create_task(
        session=session,
        registry=registry,
        thread_id="thread-3",
        goal="fix docs and tests",
        created_by="owner-1",
        source="slash",
    )
    failed = await _wait_for_status(store, task.id, {TASK_STATUS_FAILED})
    assert failed.status == TASK_STATUS_FAILED
    assert "forbidden path" in (failed.error or "").lower()
