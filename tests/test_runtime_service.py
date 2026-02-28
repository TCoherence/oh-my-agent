from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from oh_my_agent.agents.base import AgentResponse, BaseAgent
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.gateway.base import IncomingMessage
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.runtime import (
    RuntimeService,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_DRAFT,
    TASK_STATUS_FAILED,
    TASK_STATUS_MERGED,
    TASK_STATUS_PAUSED,
    TASK_STATUS_STOPPED,
    TASK_STATUS_TIMEOUT,
    TASK_STATUS_WAITING_MERGE,
)


@dataclass
class _FakeChannel:
    platform: str = "discord"
    channel_id: str = "100"
    _next_msg_id: int = 1
    sent: list[tuple[str, str]] = field(default_factory=list)
    status_messages: dict[str, tuple[str, str]] = field(default_factory=dict)
    status_history: list[tuple[str, str, str]] = field(default_factory=list)
    drafts: list[dict] = field(default_factory=list)
    signals: list[tuple[str, str | None, str]] = field(default_factory=list)

    async def send(self, thread_id: str, text: str) -> str:
        self.sent.append((thread_id, text))
        msg_id = f"m-{self._next_msg_id}"
        self._next_msg_id += 1
        return msg_id

    async def upsert_status_message(
        self,
        thread_id: str,
        text: str,
        *,
        message_id: str | None = None,
    ) -> str:
        if message_id and message_id in self.status_messages:
            self.status_messages[message_id] = (thread_id, text)
            self.status_history.append(("edit", message_id, text))
            return message_id
        msg_id = f"s-{self._next_msg_id}"
        self._next_msg_id += 1
        self.status_messages[msg_id] = (thread_id, text)
        self.status_history.append(("send", msg_id, text))
        return msg_id

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
        msg_id = f"d-{self._next_msg_id}"
        self._next_msg_id += 1
        return msg_id

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


class _RootReadmeAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "root-readme-agent"

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
        (workspace_override / "README.md").write_text("# updated\n", encoding="utf-8")
        return AgentResponse(text="done\nTASK_STATE: DONE")


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
        (workspace_override / "config.yaml").write_text("oops: true\n", encoding="utf-8")
        return AgentResponse(text="changed sensitive file\nTASK_STATE: DONE")


class _SlowDoneAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "slow-done"

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
        await asyncio.sleep(0.25)
        out = workspace_override / "docs" / "slow.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("slow", encoding="utf-8")
        return AgentResponse(text=f"{prompt}\nTASK_STATE: DONE")


class _SandboxBlockedAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "sandbox-blocked"

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
        out = workspace_override / "docs" / "note.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("ok", encoding="utf-8")
        return AgentResponse(
            text=(
                f"{prompt}\n"
                "Local sandbox pytest hit PermissionError: [Errno 1] Operation not permitted on 127.0.0.1\n"
                "BLOCK_REASON: sandbox socket-bind restriction in local test environment\n"
                "TASK_STATE: BLOCKED"
            )
        )


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


async def _wait_for_draft_count(channel: _FakeChannel, count: int, timeout: float = 8.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if len(channel.drafts) >= count:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"Expected at least {count} draft message(s), got {len(channel.drafts)}")


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
        "path_policy_mode": "allow_all_with_denylist",
        "allowed_paths": ["src/**", "tests/**", "docs/**", "skills/**", "pyproject.toml"],
        "denied_paths": ["config.yaml", ".env", ".workspace/**", ".git/**"],
        "decision_ttl_minutes": 60,
        "agent_heartbeat_seconds": 0.1,
        "test_heartbeat_seconds": 0.1,
        "test_timeout_seconds": 0.6,
        "progress_notice_seconds": 0.1,
        "progress_persist_seconds": 0.1,
        "log_event_limit": 20,
        "log_tail_chars": 400,
        "cleanup": {
            "enabled": False,
            "interval_minutes": 60,
            "retention_hours": 0,
            "prune_git_worktrees": True,
        },
        "merge_gate": {
            "enabled": True,
            "auto_commit": True,
            "require_clean_repo": True,
            "preflight_check": True,
            "target_branch_mode": "current",
            "commit_message_template": "runtime(task:{task_id}): {goal_short}",
        },
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
async def test_runtime_message_intent_draft_to_merge(runtime_env):
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    repo: Path = runtime_env["repo"]

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

    approve_event = await runtime.build_slash_decision_event(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        task_id=tasks[0].id,
        action="approve",
        actor_id="owner-1",
    )
    assert approve_event is not None
    result = await runtime.handle_decision_event(approve_event)
    assert "approved" in result.lower()

    waiting = await _wait_for_status(store, tasks[0].id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.status == TASK_STATUS_WAITING_MERGE
    await _wait_for_draft_count(channel, 2)
    merge_draft = channel.drafts[-1]
    assert merge_draft["task_id"] == tasks[0].id
    assert "Ready to Merge" in merge_draft["draft_text"]
    assert "Changed files:" in merge_draft["draft_text"]
    assert "src/runtime.txt" in merge_draft["draft_text"]
    assert "Latest test result:" in merge_draft["draft_text"]
    assert "exit=0" in merge_draft["draft_text"]

    merge_event = await runtime.build_slash_decision_event(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        task_id=tasks[0].id,
        action="merge",
        actor_id="owner-1",
    )
    assert merge_event is not None
    merge_result = await runtime.handle_decision_event(merge_event)
    assert "merged successfully" in merge_result.lower()

    merged = await _wait_for_status(store, tasks[0].id, {TASK_STATUS_MERGED})
    assert merged.status == TASK_STATUS_MERGED
    assert merged.merge_commit_hash
    assert merged.workspace_path is None
    assert merged.workspace_cleaned_at is not None
    assert (repo / "src" / "runtime.txt").exists()


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

    waiting = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.status == TASK_STATUS_WAITING_MERGE
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


@pytest.mark.asyncio
async def test_runtime_allow_all_policy_permits_repo_root_changes(runtime_env):
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_RootReadmeAgent()])
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
        thread_id="thread-4",
        goal="fix typo in readme and run tests",
        created_by="owner-1",
        source="slash",
    )
    waiting = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.status == TASK_STATUS_WAITING_MERGE


@pytest.mark.asyncio
async def test_runtime_manual_cleanup_removes_workspace(runtime_env):
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

    task = await runtime.create_task(
        session=session,
        registry=registry,
        thread_id="thread-5",
        goal="touch code and run tests",
        created_by="owner-1",
        source="slash",
    )
    waiting = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.workspace_path
    ws = Path(waiting.workspace_path)
    assert ws.exists()

    discard_result = await runtime.discard_task(task.id, actor_id="owner-1")
    assert "discarded" in discard_result.lower()

    # Mark as old enough for retention window (0h in fixture, explicit timestamp for clarity).
    await store.update_runtime_task(task.id, ended_at="2000-01-01 00:00:00")
    cleanup_result = await runtime.cleanup_tasks(actor_id="owner-1")
    assert "completed" in cleanup_result.lower()

    cleaned = await store.get_runtime_task(task.id)
    assert cleaned is not None
    assert cleaned.workspace_path is None
    assert cleaned.workspace_cleaned_at is not None


@pytest.mark.asyncio
async def test_runtime_legacy_applied_is_mergeable(runtime_env):
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

    task = await runtime.create_task(
        session=session,
        registry=registry,
        thread_id="thread-6",
        goal="legacy applied merge compatibility",
        created_by="owner-1",
        source="slash",
    )
    waiting = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.workspace_path

    await store.update_runtime_task(task.id, status="APPLIED")
    result = await runtime.merge_task(task.id, actor_id="owner-1")
    assert "merged successfully" in result.lower()

    merged = await _wait_for_status(store, task.id, {TASK_STATUS_MERGED})
    assert merged.status == TASK_STATUS_MERGED
    assert merged.workspace_path is None
    assert merged.workspace_cleaned_at is not None


@pytest.mark.asyncio
async def test_runtime_logs_capture_heartbeats_and_tails(runtime_env):
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_SlowDoneAgent()])
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
        thread_id="thread-7",
        goal="create a slow doc artifact and run tests",
        created_by="owner-1",
        test_command="python -c \"import time; print('test-start'); time.sleep(0.25); print('test-end')\"",
        source="slash",
    )
    waiting = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.status == TASK_STATUS_WAITING_MERGE

    text = await runtime.get_task_logs(task.id)
    assert "Recent events" in text
    assert "agent_progress" in text or "test_progress" in text
    assert "test-start" in text or "test-end" in text
    assert any("still running" in text for _, _, text in channel.status_history)
    assert len(channel.status_messages) == 1


@pytest.mark.asyncio
async def test_runtime_test_timeout_marks_timeout(runtime_env):
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

    task = await runtime.create_task(
        session=session,
        registry=registry,
        thread_id="thread-8",
        goal="write file and hang in tests",
        created_by="owner-1",
        test_command="python -c \"import time; print('before-timeout'); time.sleep(1.0)\"",
        source="slash",
    )
    timed_out = await _wait_for_status(store, task.id, {TASK_STATUS_TIMEOUT}, timeout=5.0)
    assert timed_out.status == TASK_STATUS_TIMEOUT
    assert "timed out" in (timed_out.summary or "").lower() or "timed out" in (timed_out.error or "").lower()
    text = await runtime.get_task_logs(task.id)
    assert "before-timeout" in text


@pytest.mark.asyncio
async def test_runtime_router_raw_request_preserved_and_visible_in_prompt(runtime_env):
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

    task = await runtime.create_task(
        session=session,
        registry=registry,
        thread_id="thread-raw",
        goal="Create docs/doc_test_hanzhi.md with specified content and run pytest -q",
        raw_request="请在当前仓库 docs 下新增一个 tiny 文档 docs/doc_test_hanzhi.md，内容只写三行：\\n1) router smoke\\n2) runtime draft confirm\\n3) done\\n然后运行 pytest -q，完成后停下等待我确认合并",
        created_by="owner-1",
        source="router",
    )
    waiting = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.status == TASK_STATUS_WAITING_MERGE

    loaded = await store.get_runtime_task(task.id)
    assert loaded is not None
    assert "runtime draft confirm" in (loaded.original_request or "")
    ckpt = await store.get_last_runtime_checkpoint(task.id)
    assert ckpt is not None
    assert "Original user request:" in ckpt["prompt_digest"]
    assert "runtime draft confirm" in ckpt["prompt_digest"]


@pytest.mark.asyncio
async def test_runtime_overrides_agent_block_when_runtime_tests_pass(runtime_env):
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_SandboxBlockedAgent()])
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
        thread_id="thread-sandbox",
        goal="write a docs note and run tests",
        raw_request="write docs/note.txt with a short line and then run pytest -q",
        created_by="owner-1",
        test_command="true",
        source="slash",
    )
    waiting = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.status == TASK_STATUS_WAITING_MERGE

    logs = await runtime.get_task_logs(task.id)
    assert "task.block_override" in logs


@pytest.mark.asyncio
async def test_runtime_formats_pytest_output_summary(runtime_env):
    runtime: RuntimeService = runtime_env["runtime"]
    output = (
        "..................................s..................................... [ 56%]\n"
        "........................................................                 [100%]\n"
        "126 passed, 1 skipped in 7.11s\n"
    )
    assert runtime._format_test_output(output) == "126 passed, 1 skipped in 7.11s"  # noqa: SLF001


@pytest.mark.asyncio
async def test_runtime_start_cleans_stale_merged_workspace(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    db_path = tmp_path / "runtime.db"
    store = SQLiteMemoryStore(db_path)
    await store.init()

    worktree_root = tmp_path / "worktrees"
    stale_workspace = worktree_root / "stale-merged"
    stale_workspace.mkdir(parents=True, exist_ok=True)
    (stale_workspace / "leftover.txt").write_text("old", encoding="utf-8")

    await store.create_runtime_task(
        task_id="stale-merged",
        platform="discord",
        channel_id="100",
        thread_id="thread-stale",
        created_by="owner-1",
        goal="old merged task",
        preferred_agent="done-agent",
        status=TASK_STATUS_MERGED,
        max_steps=8,
        max_minutes=20,
        test_command="true",
    )
    await store.update_runtime_task(
        "stale-merged",
        workspace_path=str(stale_workspace),
        ended_at="2000-01-01 00:00:00",
    )

    runtime = RuntimeService(
        store,
        config={
            "enabled": True,
            "worker_concurrency": 1,
            "worktree_root": str(worktree_root),
            "default_agent": "done-agent",
            "default_test_command": "true",
            "cleanup": {
                "enabled": True,
                "interval_minutes": 60,
                "retention_hours": 72,
                "prune_git_worktrees": True,
                "merged_immediate": True,
            },
        },
        owner_user_ids={"owner-1"},
        repo_root=repo,
    )

    await runtime.start()
    cleaned = await store.get_runtime_task("stale-merged")
    assert cleaned is not None
    assert cleaned.workspace_path is None
    assert cleaned.workspace_cleaned_at is not None
    assert not stale_workspace.exists()

    await runtime.stop()
    await store.close()


# ---------------------------------------------------------------------------
# Helpers for new behaviour tests
# ---------------------------------------------------------------------------


class _StoppableSlowAgent(BaseAgent):
    """Agent that sleeps for a long time, allowing stop/pause to interrupt it."""

    def __init__(self, sleep_seconds: float = 5.0) -> None:
        self._sleep = sleep_seconds

    @property
    def name(self) -> str:
        return "stoppable-slow"

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
        await asyncio.sleep(self._sleep)
        out = workspace_override / "src" / "slow.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("done", encoding="utf-8")
        return AgentResponse(text="TASK_STATE: DONE")


# ---------------------------------------------------------------------------
# New tests: PAUSED state, true interruption, message-driven control, summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runtime_parse_control_intent():
    """_parse_control_intent correctly maps text to (action, instruction)."""
    from oh_my_agent.runtime.service import RuntimeService as RS

    assert RS._parse_control_intent("stop") == ("stop", "")
    assert RS._parse_control_intent("Stop") == ("stop", "")
    assert RS._parse_control_intent("stop the task") == ("stop", "")
    assert RS._parse_control_intent("cancel") == ("stop", "")
    assert RS._parse_control_intent("pause") == ("pause", "")
    assert RS._parse_control_intent("Pause the task") == ("pause", "")
    assert RS._parse_control_intent("resume add tests for auth") == ("resume", "add tests for auth")
    assert RS._parse_control_intent("Continue with fixture from env") == ("resume", "with fixture from env")
    assert RS._parse_control_intent("please fix the parser bug") is None
    assert RS._parse_control_intent("") is None


@pytest.mark.asyncio
async def test_runtime_stop_while_running_interrupts_agent(runtime_env):
    """Calling stop_task while agent is running cancels the agent within one heartbeat."""
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]

    registry = AgentRegistry([_StoppableSlowAgent(sleep_seconds=5.0)])
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
        thread_id="thread-stop",
        goal="fix tests and install dependencies then run all tests",
        created_by="owner-1",
        source="slash",
    )

    # Wait for task to start running, then stop it
    await asyncio.sleep(0.3)
    stop_result = await runtime.stop_task(task.id, actor_id="owner-1")
    assert "stopped" in stop_result.lower()

    stopped = await _wait_for_status(store, task.id, {TASK_STATUS_STOPPED}, timeout=3.0)
    assert stopped.status == TASK_STATUS_STOPPED


@pytest.mark.asyncio
async def test_runtime_message_stop_while_running(runtime_env):
    """Sending 'stop' as a message while task is running stops it via maybe_handle_incoming."""
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]

    registry = AgentRegistry([_StoppableSlowAgent(sleep_seconds=5.0)])
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
        thread_id="thread-msg-stop",
        goal="fix tests and install dependencies then run all tests",
        created_by="owner-1",
        source="slash",
    )

    # Wait for it to reach running state
    await asyncio.sleep(0.3)

    # Send "stop" as a message in the same thread
    stop_msg = IncomingMessage(
        platform="discord",
        channel_id="100",
        thread_id="thread-msg-stop",
        author="owner",
        author_id="owner-1",
        content="stop",
    )
    handled = await runtime.maybe_handle_incoming(session, registry, stop_msg, thread_id="thread-msg-stop")
    assert handled is True

    stopped = await _wait_for_status(store, task.id, {TASK_STATUS_STOPPED}, timeout=4.0)
    assert stopped.status == TASK_STATUS_STOPPED


@pytest.mark.asyncio
async def test_runtime_pause_and_resume(runtime_env):
    """pause_task sets PAUSED; resume_task re-queues it and it completes."""
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]

    # Use a short-sleep stoppable agent so we can intercept it
    registry = AgentRegistry([_StoppableSlowAgent(sleep_seconds=3.0)])
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
        thread_id="thread-pause",
        goal="fix tests and install dependencies then run all tests",
        created_by="owner-1",
        source="slash",
    )

    # Wait briefly then pause
    await asyncio.sleep(0.3)
    pause_result = await runtime.pause_task(task.id, actor_id="owner-1")
    assert "paused" in pause_result.lower()

    paused = await _wait_for_status(store, task.id, {TASK_STATUS_PAUSED}, timeout=3.0)
    assert paused.status == TASK_STATUS_PAUSED

    # Now swap in a fast agent and resume
    fast_registry = AgentRegistry([_DoneAgent()])
    runtime.register_session(session, fast_registry)

    resume_result = await runtime.resume_task(task.id, "use the mock fixture", actor_id="owner-1")
    assert "resumed" in resume_result.lower()

    # Task should reach WAITING_MERGE now with the fast agent
    done = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE}, timeout=8.0)
    assert done.status == TASK_STATUS_WAITING_MERGE


@pytest.mark.asyncio
async def test_runtime_blocked_thread_message_auto_resumes(runtime_env):
    """A plain reply to a blocked thread automatically resumes the task."""
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
        thread_id="thread-auto-resume",
        goal="fix parser and run tests",
        created_by="owner-1",
        source="slash",
    )
    blocked = await _wait_for_status(store, task.id, {TASK_STATUS_BLOCKED})
    assert blocked.status == TASK_STATUS_BLOCKED

    # Send a plain message (not a control word, not a long task intent)
    reply = IncomingMessage(
        platform="discord",
        channel_id="100",
        thread_id="thread-auto-resume",
        author="owner",
        author_id="owner-1",
        content="the fixture is now available in conftest.py",
    )
    handled = await runtime.maybe_handle_incoming(session, registry, reply, thread_id="thread-auto-resume")
    assert handled is True

    waiting = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE}, timeout=8.0)
    assert waiting.status == TASK_STATUS_WAITING_MERGE


@pytest.mark.asyncio
async def test_runtime_completion_summary_has_files_and_timing(runtime_env):
    """Completion summary stored in task.summary includes file list and timing metrics."""
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

    task = await runtime.create_task(
        session=session,
        registry=registry,
        thread_id="thread-summary",
        goal="fix tests and install dependencies then run all tests",
        created_by="owner-1",
        source="slash",
    )
    waiting = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.status == TASK_STATUS_WAITING_MERGE

    loaded = await store.get_runtime_task(task.id)
    assert loaded is not None
    summary = loaded.summary or ""
    # Should mention changed files and timing
    assert "src/runtime.txt" in summary or "Changed files" in summary
    assert "Timing:" in summary

    # Check task.completed event has latency fields
    events = await store.list_runtime_events(task.id, limit=50)
    completed = [e for e in events if e.get("event_type") == "task.completed"]
    assert completed, "Expected task.completed event"
    payload = completed[-1].get("payload", {})
    assert "total_agent_s" in payload
    assert "total_test_s" in payload
    assert "total_elapsed_s" in payload


@pytest.mark.asyncio
async def test_runtime_suggest_action_resends_decision_surface(runtime_env):
    """The 'suggest' decision action re-sends a decision surface with the suggestion shown."""
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
        thread_id="thread-suggest",
        author="owner",
        author_id="owner-1",
        content="please fix and pip install missing deps then run tests",
    )
    await runtime.maybe_handle_incoming(session, registry, msg, thread_id="thread-suggest")

    tasks = await store.list_runtime_tasks(platform="discord", channel_id="100")
    draft_task = next(t for t in tasks if t.thread_id == "thread-suggest")
    assert draft_task.status == TASK_STATUS_DRAFT

    suggest_event = await runtime.build_slash_decision_event(
        platform="discord",
        channel_id="100",
        thread_id="thread-suggest",
        task_id=draft_task.id,
        action="suggest",
        actor_id="owner-1",
        suggestion="add --no-build-isolation flag",
    )
    assert suggest_event is not None
    drafts_before = len(channel.drafts)
    result = await runtime.handle_decision_event(suggest_event)
    assert "suggestion recorded" in result.lower()

    # A new decision surface should have been sent with the suggestion text
    assert len(channel.drafts) > drafts_before
    latest_draft = channel.drafts[-1]
    assert "add --no-build-isolation flag" in latest_draft["draft_text"]
    assert "Suggestion Recorded" in latest_draft["draft_text"]
