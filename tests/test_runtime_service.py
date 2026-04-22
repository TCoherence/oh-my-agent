from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from oh_my_agent.auth.types import AuthFlow, CredentialHandle
from oh_my_agent.agents.base import AgentResponse, BaseAgent
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.gateway.base import IncomingMessage
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.runtime import (
    RuntimeService,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_DRAFT,
    TASK_STATUS_FAILED,
    TASK_STATUS_MERGED,
    TASK_STATUS_PAUSED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_STOPPED,
    TASK_STATUS_TIMEOUT,
    TASK_STATUS_WAITING_USER_INPUT,
    TASK_STATUS_WAITING_MERGE,
)


@dataclass
class _FakeChannel:
    platform: str = "discord"
    channel_id: str = "100"
    _next_msg_id: int = 1
    sent: list[tuple[str, str]] = field(default_factory=list)
    dms: list[tuple[str, str]] = field(default_factory=list)
    status_messages: dict[str, tuple[str, str]] = field(default_factory=dict)
    status_history: list[tuple[str, str, str]] = field(default_factory=list)
    drafts: list[dict] = field(default_factory=list)
    signals: list[tuple[str, str | None, str]] = field(default_factory=list)
    attachments: list[tuple[str, str, Path, str | None]] = field(default_factory=list)
    hitl_prompts: list[dict] = field(default_factory=list)

    async def send(self, thread_id: str, text: str) -> str:
        self.sent.append((thread_id, text))
        msg_id = f"m-{self._next_msg_id}"
        self._next_msg_id += 1
        return msg_id

    def render_user_mention(self, user_id: str) -> str:
        return f"<@{user_id}>"

    async def send_dm(self, user_id: str, text: str) -> str:
        self.dms.append((user_id, text))
        msg_id = f"dm-{self._next_msg_id}"
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

    async def send_attachment(self, thread_id: str, attachment) -> str:
        self.attachments.append(
            (thread_id, attachment.filename, attachment.local_path, attachment.caption)
        )
        msg_id = f"a-{self._next_msg_id}"
        self._next_msg_id += 1
        return msg_id

    async def send_attachments(self, thread_id: str, attachments, *, text: str | None = None) -> list[str]:
        if text:
            await self.send(thread_id, text)
        message_ids: list[str] = []
        for attachment in attachments:
            message_id = await self.send_attachment(thread_id, attachment)
            if message_id:
                message_ids.append(message_id)
        return message_ids

    async def send_hitl_prompt(self, *, thread_id: str, prompt) -> str:
        self.hitl_prompts.append(
            {
                "thread_id": thread_id,
                "prompt_id": prompt.id,
                "question": prompt.question,
                "choices": list(prompt.choices),
            }
        )
        msg_id = f"h-{self._next_msg_id}"
        self._next_msg_id += 1
        return msg_id


class _FakeAuthService:
    def __init__(self) -> None:
        self.enabled = True
        self._listeners = []
        self._flows: dict[str, AuthFlow] = {}
        self._next_id = 1
        self.cleared: list[tuple[str, str]] = []
        self.credential = CredentialHandle(
            id="cred-1",
            provider="bilibili",
            owner_user_id="owner-1",
            scope_key="default",
            status="valid",
            storage_path="/tmp/cookies.txt",
            metadata={},
            last_verified_at="2026-03-03 00:00:00",
        )

    def add_listener(self, listener) -> None:
        self._listeners.append(listener)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def start_qr_flow(
        self,
        provider: str,
        *,
        owner_user_id: str,
        platform: str,
        channel_id: str,
        thread_id: str,
        linked_task_id: str | None,
        force_new: bool = False,
    ) -> AuthFlow:
        del force_new
        flow_id = f"flow-{self._next_id}"
        self._next_id += 1
        qr_path = Path("/tmp") / f"{flow_id}.png"
        flow = AuthFlow(
            id=flow_id,
            provider=provider,
            owner_user_id=owner_user_id,
            platform=platform,
            channel_id=channel_id,
            thread_id=thread_id,
            linked_task_id=linked_task_id,
            status="qr_ready",
            provider_flow_id=f"provider-{flow_id}",
            qr_payload="https://example.com/qr",
            qr_image_path=str(qr_path),
            error=None,
            expires_at="2026-03-03 00:03:00",
        )
        self._flows[flow.id] = flow
        return flow

    async def get_status(self, provider: str, owner_user_id: str) -> dict:
        active_flow = next(
            (flow for flow in reversed(list(self._flows.values())) if flow.provider == provider and flow.owner_user_id == owner_user_id),
            None,
        )
        return {
            "provider": provider,
            "credential": self.credential,
            "active_flow": active_flow,
        }

    async def clear_credential(self, provider: str, owner_user_id: str) -> None:
        self.cleared.append((provider, owner_user_id))

    async def emit(self, event_type: str, flow_id: str, message: str | None = None) -> None:
        flow = self._flows[flow_id]
        for listener in self._listeners:
            await listener(event_type, flow, self.credential, message)


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


class _ResumableAuthAgent(BaseAgent):
    def __init__(self) -> None:
        self._session_ids: dict[str, str] = {}
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "resume-agent"

    def get_session_id(self, thread_id: str) -> str | None:
        return self._session_ids.get(thread_id)

    def set_session_id(self, thread_id: str, session_id: str) -> None:
        self._session_ids[thread_id] = session_id

    def clear_session(self, thread_id: str) -> None:
        self._session_ids.pop(thread_id, None)

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
        workspace_override: Path | None = None,
        log_path: Path | None = None,
    ) -> AgentResponse:
        del history, workspace_override, log_path
        self.prompts.append(prompt)
        if thread_id and thread_id not in self._session_ids:
            self._session_ids[thread_id] = "sess-restored"
        return AgentResponse(
            text="final resumed answer",
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 20,
                "cache_creation_input_tokens": 3,
                "cost_usd": 0.1234,
            },
        )


class _ResumableAskUserAgent(BaseAgent):
    def __init__(self) -> None:
        self._session_ids: dict[str, str] = {}
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "ask-user-agent"

    def get_session_id(self, thread_id: str) -> str | None:
        return self._session_ids.get(thread_id)

    def set_session_id(self, thread_id: str, session_id: str) -> None:
        self._session_ids[thread_id] = session_id

    def clear_session(self, thread_id: str) -> None:
        self._session_ids.pop(thread_id, None)

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
        workspace_override: Path | None = None,
        log_path: Path | None = None,
    ) -> AgentResponse:
        del history, workspace_override, log_path
        self.prompts.append(prompt)
        if thread_id:
            self._session_ids.setdefault(thread_id, "sess-hitl")
        return AgentResponse(text="根据你的选择，我继续完成这次分析。")


class _ResumableAskUserChallengeAgent(BaseAgent):
    def __init__(self) -> None:
        self._session_ids: dict[str, str] = {}
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "ask-user-agent"

    def get_session_id(self, thread_id: str) -> str | None:
        return self._session_ids.get(thread_id)

    def set_session_id(self, thread_id: str, session_id: str) -> None:
        self._session_ids[thread_id] = session_id

    def clear_session(self, thread_id: str) -> None:
        self._session_ids.pop(thread_id, None)

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
        workspace_override: Path | None = None,
        log_path: Path | None = None,
    ) -> AgentResponse:
        del history, workspace_override, log_path
        self.prompts.append(prompt)
        if thread_id:
            self._session_ids.setdefault(thread_id, "sess-hitl")
        return AgentResponse(
            text=(
                "我需要再确认一次。\n"
                '<OMA_CONTROL>{"version":1,"type":"challenge","data":{"challenge_type":"ask_user",'
                '"question":"下一步怎么继续？","details":"还是单选。","choices":['
                '{"id":"deep","label":"Deep dive","description":"继续展开"},'
                '{"id":"brief","label":"Brief","description":"保持简洁"}'
                "]}}</OMA_CONTROL>"
            )
        )


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


class _ArtifactAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "artifact-agent"

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
        out = workspace_override / "reports" / "daily-news.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# Daily News\n\n- item 1\n", encoding="utf-8")
        return AgentResponse(text="artifact ready\nTASK_STATE: DONE")


class _AutomationNarrativeAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "automation-narrative"

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
        out = workspace_override / "response.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        text = (
            "I'll fetch the current local time from the shell and then provide the requested message.\n\n"
            "Hello! This is an automation smoke test, and the current local time is 2026-03-14 05:09:33 UTC.\n\n"
            "Implemented in `response.txt`.\n\n"
            "TASK_STATE: DONE"
        )
        out.write_text(text, encoding="utf-8")
        return AgentResponse(
            text=text,
            usage={
                "input_tokens": 4321,
                "output_tokens": 2109,
                "cache_read_input_tokens": 90000,
                "cache_creation_input_tokens": 12000,
                "cost_usd": 0.4821,
            },
        )


class _AutomationFailureAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "automation-failure"

    async def run(
        self,
        prompt: str,
        history: list[dict] | None = None,
        *,
        thread_id: str | None = None,
        workspace_override: Path | None = None,
    ) -> AgentResponse:
        del prompt, history, thread_id, workspace_override
        return AgentResponse(
            text="",
            error="automation-failure timed out",
            error_kind="timeout",
            partial_text="partial result before timeout",
            usage={
                "input_tokens": 321,
                "output_tokens": 123,
                "cache_read_input_tokens": 456,
                "cost_usd": 0.0456,
            },
        )


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
        "reports_dir": str(tmp_path / "reports"),
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
    draft_notifications = await store.list_active_notification_events(
        dedupe_key=f"task:{tasks[0].id}:draft",
        limit=10,
    )
    assert len(draft_notifications) == 1
    assert any(
        text.startswith("<@owner-1> **Action required**") and "Reason:" in text
        for _, text in channel.sent
    )
    assert any(user_id == "owner-1" and "Reason:" in text for user_id, text in channel.dms)

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
    assert await store.list_active_notification_events(
        dedupe_key=f"task:{tasks[0].id}:draft",
        limit=10,
    ) == []

    waiting = await _wait_for_status(store, tasks[0].id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.status == TASK_STATUS_WAITING_MERGE
    merge_notifications = await store.list_active_notification_events(
        dedupe_key=f"task:{tasks[0].id}:waiting_merge",
        limit=10,
    )
    assert len(merge_notifications) == 1
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
    assert await store.list_active_notification_events(
        dedupe_key=f"task:{tasks[0].id}:waiting_merge",
        limit=10,
    ) == []

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
async def test_artifact_task_completes_without_merge(runtime_env):
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_ArtifactAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)
    await runtime.start()

    task = await runtime.create_artifact_task(
        session=session,
        registry=registry,
        thread_id="thread-artifact",
        goal="Generate a markdown daily news brief",
        created_by="owner-1",
        source="router",
    )
    completed = await _wait_for_status(store, task.id, {TASK_STATUS_COMPLETED})
    assert completed.status == TASK_STATUS_COMPLETED
    assert completed.completion_mode == "reply"
    assert completed.task_type == "artifact"
    assert completed.output_summary
    assert completed.artifact_manifest == ["reports/daily-news.md"]
    assert channel.attachments
    assert all(draft["task_id"] != task.id for draft in channel.drafts)
    assert "waiting merge" not in (completed.summary or "").lower()
    logs = await runtime.get_task_logs(task.id)
    assert "Artifacts:" in logs
    assert "Thread log:" in logs

    reports_dir = runtime._reports_dir  # noqa: SLF001
    assert reports_dir is not None
    # Rule 2: workspace `reports/daily-news.md` is reports-shaped → published
    # to the canonical path `reports_dir/daily-news.md` (NOT under
    # `reports_dir/artifacts/`, which was the old flat-archive behavior).
    published_files = list(reports_dir.rglob("daily-news*.md"))
    assert len(published_files) == 1
    assert published_files[0] == reports_dir / "daily-news.md"
    # Should NOT also be duplicated under artifacts/ fallback dir.
    assert not (reports_dir / "artifacts" / "daily-news.md").exists()
    sent_texts = [text for _, text in channel.sent]
    assert any("Published to:" in text and str(published_files[0]) in text for text in sent_texts)


@pytest.mark.asyncio
async def test_publish_canonical_destination_overwrites_without_suffix(runtime_env):
    """Regression pin: two tasks produce the same canonical workspace path
    (e.g., both write `reports/daily-news.md`) → second call overwrites the
    first at the canonical destination. Exactly one file exists at
    `reports_dir/daily-news.md` and NO `…-<task_id[:8]>.md` suffix file is
    created. This guards against someone 'fixing' canonical collisions by
    reintroducing suffixes — the whole point of Rule 2 is that a canonical
    path identifies the same logical file, so suffixing would re-create the
    duplication we removed.
    """
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_ArtifactAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)
    await runtime.start()

    for thread_suffix in ("a", "b"):
        task = await runtime.create_artifact_task(
            session=session,
            registry=registry,
            thread_id=f"thread-canonical-{thread_suffix}",
            goal="Generate a markdown daily news brief",
            created_by="owner-1",
            source="router",
        )
        await _wait_for_status(store, task.id, {TASK_STATUS_COMPLETED})

    reports_dir = runtime._reports_dir  # noqa: SLF001
    assert reports_dir is not None
    published = sorted(reports_dir.rglob("daily-news*.md"))
    # Rule 2 canonical publish: same canonical path → overwrite → exactly 1 file.
    assert len(published) == 1
    assert published[0] == reports_dir / "daily-news.md"
    # No suffix-tagged sibling under the canonical dir.
    canonical_dir = reports_dir
    suffixed = [
        p for p in canonical_dir.glob("daily-news-*.md") if p != published[0]
    ]
    assert suffixed == []
    # No spillover into the flat-fallback dir either.
    assert not (reports_dir / "artifacts" / "daily-news.md").exists()


@pytest.mark.asyncio
async def test_publish_disabled_when_reports_dir_empty(runtime_env, tmp_path):
    runtime: RuntimeService = runtime_env["runtime"]
    runtime._reports_dir = None  # noqa: SLF001 — emulates `reports_dir: ""` config

    out = tmp_path / "src.md"
    out.write_text("# hi", encoding="utf-8")
    # New signature: workspace_path kwarg required.
    assert (
        runtime._publish_artifact_files(  # noqa: SLF001
            "task-xyz", [out], workspace_path=None
        )
        == []
    )


@pytest.mark.asyncio
async def test_publish_reuses_existing_reports_path(runtime_env, tmp_path):
    """Rule 1: artifact whose resolved path is already under ``reports_dir``
    (and not under workspace) is reused in place — no copy, no fallback dir."""
    runtime: RuntimeService = runtime_env["runtime"]
    reports_dir = runtime._reports_dir  # noqa: SLF001
    assert reports_dir is not None
    target = reports_dir / "market-briefing" / "2026-04-22.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("already stable\n", encoding="utf-8")

    workspace_root = tmp_path / "workspace-outside-reports"
    workspace_root.mkdir(parents=True)

    published = runtime._publish_artifact_files(  # noqa: SLF001
        "task-001", [target], workspace_path=str(workspace_root)
    )
    assert published == [str(target.resolve())]
    # Flat-fallback dir must not have been created for a rule-1 reuse.
    assert not (reports_dir / "artifacts").exists()


@pytest.mark.asyncio
async def test_publish_maps_workspace_reports_path_into_reports_dir(
    runtime_env, tmp_path
):
    """Rule 2 happy path: workspace `reports/<sub>/<file>` → published at
    `reports_dir/<sub>/<file>` preserving sub-tree."""
    runtime: RuntimeService = runtime_env["runtime"]
    reports_dir = runtime._reports_dir  # noqa: SLF001
    assert reports_dir is not None
    workspace = tmp_path / "ws-rule2"
    source = workspace / "reports" / "paper-digest" / "daily" / "2026-04-22.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("# paper digest\n", encoding="utf-8")

    published = runtime._publish_artifact_files(  # noqa: SLF001
        "task-002", [source], workspace_path=str(workspace)
    )
    canonical = reports_dir / "paper-digest" / "daily" / "2026-04-22.md"
    assert published == [str(canonical.resolve())]
    assert canonical.read_text(encoding="utf-8") == "# paper digest\n"
    # Not also at reports_dir/artifacts/ — that would be the old duplication.
    assert not (reports_dir / "artifacts" / "2026-04-22.md").exists()


@pytest.mark.asyncio
async def test_publish_falls_back_to_flat_artifacts_for_non_reports_workspace_file(
    runtime_env, tmp_path
):
    """Rule 3: workspace file not under `reports/` → flat fallback at
    `reports_dir/artifacts/<basename>`."""
    runtime: RuntimeService = runtime_env["runtime"]
    reports_dir = runtime._reports_dir  # noqa: SLF001
    assert reports_dir is not None
    workspace = tmp_path / "ws-rule3"
    source = workspace / "notes.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("freeform\n", encoding="utf-8")

    published = runtime._publish_artifact_files(  # noqa: SLF001
        "task-003", [source], workspace_path=str(workspace)
    )
    expected = reports_dir / "artifacts" / "notes.txt"
    assert published == [str(expected.resolve())]
    assert expected.read_text(encoding="utf-8") == "freeform\n"


@pytest.mark.asyncio
async def test_publish_falls_back_to_flat_artifacts_for_absolute_outside_workspace(
    runtime_env, tmp_path
):
    """Rule 4: absolute path under neither `workspace_path` nor `reports_dir`
    → flat fallback at `reports_dir/artifacts/<basename>`."""
    runtime: RuntimeService = runtime_env["runtime"]
    reports_dir = runtime._reports_dir  # noqa: SLF001
    assert reports_dir is not None
    workspace = tmp_path / "ws-rule4"
    workspace.mkdir(parents=True)
    orphan_dir = tmp_path / "orphan"
    orphan_dir.mkdir(parents=True)
    source = orphan_dir / "snapshot.json"
    source.write_text("{}\n", encoding="utf-8")

    published = runtime._publish_artifact_files(  # noqa: SLF001
        "task-004", [source], workspace_path=str(workspace)
    )
    expected = reports_dir / "artifacts" / "snapshot.json"
    assert published == [str(expected.resolve())]


@pytest.mark.asyncio
async def test_publish_collision_suffixes_task_id_only_for_flat_fallback(
    runtime_env, tmp_path
):
    """Scoped to rules 3 / 4: two distinct tasks produce the same basename via
    the fallback path → second gets `-<task_id[:8]>` suffix. Must NOT
    accidentally generalize to rule 1 or rule 2 canonical destinations.
    """
    runtime: RuntimeService = runtime_env["runtime"]
    reports_dir = runtime._reports_dir  # noqa: SLF001
    assert reports_dir is not None

    # Two separate workspaces, each producing a top-level `notes.txt`.
    def _mk_workspace(name: str, content: str) -> Path:
        ws = tmp_path / name
        ws.mkdir(parents=True)
        (ws / "notes.txt").write_text(content, encoding="utf-8")
        return ws

    ws_a = _mk_workspace("ws-suffix-a", "from a\n")
    ws_b = _mk_workspace("ws-suffix-b", "from b\n")

    pub_a = runtime._publish_artifact_files(  # noqa: SLF001
        "aaaa1111-task", [ws_a / "notes.txt"], workspace_path=str(ws_a)
    )
    pub_b = runtime._publish_artifact_files(  # noqa: SLF001
        "bbbb2222-task", [ws_b / "notes.txt"], workspace_path=str(ws_b)
    )

    assert pub_a == [str((reports_dir / "artifacts" / "notes.txt").resolve())]
    # Second call is suffixed with the first 8 chars of the task id.
    assert pub_b == [
        str((reports_dir / "artifacts" / "notes-bbbb2222.txt").resolve())
    ]
    # Original file was not clobbered.
    assert (reports_dir / "artifacts" / "notes.txt").read_text(
        encoding="utf-8"
    ) == "from a\n"


@pytest.mark.asyncio
async def test_artifact_task_delivery_falls_back_to_paths_when_attachments_not_allowed(runtime_env):
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_ArtifactAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)
    runtime._artifact_attachment_max_bytes = 1  # noqa: SLF001
    await runtime.start()

    task = await runtime.create_artifact_task(
        session=session,
        registry=registry,
        thread_id="thread-artifact-path",
        goal="Generate a markdown daily news brief",
        created_by="owner-1",
        source="router",
    )
    completed = await _wait_for_status(store, task.id, {TASK_STATUS_COMPLETED})
    assert completed.status == TASK_STATUS_COMPLETED
    assert channel.attachments == []
    sent_texts = [text for _, text in channel.sent]
    # ``Published to:`` block carries the absolute canonical path as the primary
    # answer; ``Delivered via:`` is subordinate transport detail.
    assert any("Published to:" in text for text in sent_texts)
    assert any("Delivered via: `path`" in text for text in sent_texts)
    assert any("/reports/daily-news.md" in text for text in sent_texts)


@pytest.mark.asyncio
async def test_runtime_doctor_report_includes_counts_and_log_paths(runtime_env):
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    await store.create_runtime_task(
        task_id="doctor-active-1",
        platform="discord",
        channel_id="100",
        thread_id="thread-doctor",
        created_by="owner-1",
        goal="active task",
        preferred_agent="done-agent",
        status=TASK_STATUS_RUNNING,
        max_steps=1,
        max_minutes=5,
        test_command="true",
    )
    await store.create_runtime_task(
        task_id="doctor-complete-1",
        platform="discord",
        channel_id="100",
        thread_id="thread-doctor",
        created_by="owner-1",
        goal="completed task",
        preferred_agent="done-agent",
        status=TASK_STATUS_MERGED,
        max_steps=1,
        max_minutes=5,
        test_command="true",
    )
    report = await runtime.build_doctor_report(
        platform="discord",
        channel_id="100",
        scheduler=MagicMock(jobs=[], list_automations=MagicMock(return_value=[])),
    )
    assert "**Runtime health**" in report
    assert "Active tasks: `1`" in report
    assert "Recent tasks: `2`" in report
    assert f"`{TASK_STATUS_RUNNING}`: 1" in report
    assert "Service log:" in report
    assert "Thread log root:" in report
    assert "Active prompts:" in report


@pytest.mark.asyncio
async def test_enqueue_scheduler_task_uses_reply_artifact_shape(runtime_env):
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    store: SQLiteMemoryStore = runtime_env["store"]
    registry = AgentRegistry([_ArtifactAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)

    task = await runtime.enqueue_scheduler_task(
        session=session,
        registry=registry,
        thread_id="thread-scheduler",
        automation_name="daily-news",
        prompt="Generate a markdown daily news brief",
        author="scheduler",
        preferred_agent="artifact-agent",
    )
    assert task is not None
    stored = await store.get_runtime_task(task.id)
    assert stored is not None
    assert stored.task_type == "artifact"
    assert stored.completion_mode == "reply"
    assert stored.test_command == "true"
    assert stored.max_steps == 1
    assert stored.max_minutes == 10
    assert stored.automation_name == "daily-news"


@pytest.mark.asyncio
async def test_enqueue_scheduler_task_uses_automation_execution_overrides(runtime_env):
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    store: SQLiteMemoryStore = runtime_env["store"]
    registry = AgentRegistry([_ArtifactAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)

    task = await runtime.enqueue_scheduler_task(
        session=session,
        registry=registry,
        thread_id="thread-scheduler",
        automation_name="daily-news",
        prompt="Generate a markdown daily news brief",
        author="scheduler",
        preferred_agent="artifact-agent",
        timeout_seconds=901,
        max_turns=70,
    )
    assert task is not None
    stored = await store.get_runtime_task(task.id)
    assert stored is not None
    assert stored.agent_timeout_seconds == 901
    assert stored.agent_max_turns == 70
    assert stored.max_minutes == 16


@pytest.mark.asyncio
async def test_enqueue_scheduler_task_skips_when_same_automation_is_inflight(runtime_env):
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    store: SQLiteMemoryStore = runtime_env["store"]
    registry = AgentRegistry([_ArtifactAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)

    first = await runtime.enqueue_scheduler_task(
        session=session,
        registry=registry,
        thread_id="thread-scheduler",
        automation_name="daily-news",
        prompt="Generate a markdown daily news brief",
        author="scheduler",
        preferred_agent="artifact-agent",
    )
    assert first is not None

    second = await runtime.enqueue_scheduler_task(
        session=session,
        registry=registry,
        thread_id="thread-scheduler",
        automation_name="daily-news",
        prompt="Generate a markdown daily news brief",
        author="scheduler",
        preferred_agent="artifact-agent",
    )
    assert second is None

    tasks = await store.list_runtime_tasks(platform="discord", channel_id="100", limit=20)
    same_name = [task for task in tasks if task.automation_name == "daily-news"]
    assert len(same_name) == 1


@pytest.mark.asyncio
async def test_enqueue_scheduler_task_reminds_when_blocked_by_draft(runtime_env):
    """When scheduler skip is caused by a DRAFT task, a reminder with 5 buttons is posted."""
    runtime: RuntimeService = runtime_env["runtime"]
    store: SQLiteMemoryStore = runtime_env["store"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_ArtifactAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)

    first = await runtime.enqueue_scheduler_task(
        session=session,
        registry=registry,
        thread_id="thread-scheduler",
        automation_name="daily-news",
        prompt="Generate a markdown daily news brief",
        author="scheduler",
        preferred_agent="artifact-agent",
    )
    assert first is not None
    await store.update_runtime_task(first.id, status=TASK_STATUS_DRAFT)
    channel.drafts.clear()

    second = await runtime.enqueue_scheduler_task(
        session=session,
        registry=registry,
        thread_id="thread-scheduler",
        automation_name="daily-news",
        prompt="Generate a markdown daily news brief",
        author="scheduler",
        preferred_agent="artifact-agent",
    )
    assert second is None
    assert len(channel.drafts) == 1
    draft = channel.drafts[0]
    assert draft["task_id"] == first.id
    assert draft["actions"] == ["approve", "reject", "suggest", "discard", "replace"]
    assert "被跳过" in draft["draft_text"]


@pytest.mark.asyncio
async def test_enqueue_scheduler_task_does_not_remind_for_non_draft_block(runtime_env):
    """Scheduler skip for non-DRAFT active states (e.g. RUNNING) stays log-only."""
    runtime: RuntimeService = runtime_env["runtime"]
    store: SQLiteMemoryStore = runtime_env["store"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_ArtifactAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)

    first = await runtime.enqueue_scheduler_task(
        session=session,
        registry=registry,
        thread_id="thread-scheduler",
        automation_name="daily-news",
        prompt="Generate a markdown daily news brief",
        author="scheduler",
        preferred_agent="artifact-agent",
    )
    assert first is not None
    await store.update_runtime_task(first.id, status=TASK_STATUS_RUNNING)
    channel.drafts.clear()

    second = await runtime.enqueue_scheduler_task(
        session=session,
        registry=registry,
        thread_id="thread-scheduler",
        automation_name="daily-news",
        prompt="Generate a markdown daily news brief",
        author="scheduler",
        preferred_agent="artifact-agent",
    )
    assert second is None
    assert channel.drafts == []


@pytest.mark.asyncio
async def test_replace_draft_task_discards_and_returns_automation_name(runtime_env):
    runtime: RuntimeService = runtime_env["runtime"]
    store: SQLiteMemoryStore = runtime_env["store"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_ArtifactAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)

    task = await runtime.enqueue_scheduler_task(
        session=session,
        registry=registry,
        thread_id="thread-scheduler",
        automation_name="daily-news",
        prompt="Generate a markdown daily news brief",
        author="scheduler",
        preferred_agent="artifact-agent",
    )
    assert task is not None
    await store.update_runtime_task(task.id, status=TASK_STATUS_DRAFT)

    message, name = await runtime.replace_draft_task(task.id, actor_id="owner-1")
    assert name == "daily-news"
    assert "discarded" in message

    reloaded = await store.get_runtime_task(task.id)
    assert reloaded is not None
    assert reloaded.status == "DISCARDED"


@pytest.mark.asyncio
async def test_replace_draft_task_rejects_non_draft_status(runtime_env):
    runtime: RuntimeService = runtime_env["runtime"]
    store: SQLiteMemoryStore = runtime_env["store"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_ArtifactAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)

    task = await runtime.enqueue_scheduler_task(
        session=session,
        registry=registry,
        thread_id="thread-scheduler",
        automation_name="daily-news",
        prompt="Generate a markdown daily news brief",
        author="scheduler",
        preferred_agent="artifact-agent",
    )
    assert task is not None
    await store.update_runtime_task(task.id, status=TASK_STATUS_RUNNING)

    message, name = await runtime.replace_draft_task(task.id, actor_id="owner-1")
    assert name is None
    assert "not a DRAFT" in message


@pytest.mark.asyncio
async def test_replace_draft_task_requires_authorization(runtime_env):
    runtime: RuntimeService = runtime_env["runtime"]
    store: SQLiteMemoryStore = runtime_env["store"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_ArtifactAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)

    task = await runtime.enqueue_scheduler_task(
        session=session,
        registry=registry,
        thread_id="thread-scheduler",
        automation_name="daily-news",
        prompt="Generate a markdown daily news brief",
        author="scheduler",
        preferred_agent="artifact-agent",
    )
    assert task is not None
    await store.update_runtime_task(task.id, status=TASK_STATUS_DRAFT)

    message, name = await runtime.replace_draft_task(task.id, actor_id="not-owner")
    assert name is None
    assert "Only configured owners" in message


@pytest.mark.asyncio
async def test_scheduler_automation_posts_direct_result_without_status_spam(runtime_env):
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_ArtifactAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)
    await runtime.start()

    task = await runtime.enqueue_scheduler_task(
        session=session,
        registry=registry,
        thread_id="thread-automation",
        automation_name="daily-news",
        prompt="Generate a markdown daily news brief",
        author="scheduler",
        preferred_agent="artifact-agent",
    )
    assert task is not None

    completed = await _wait_for_status(store, task.id, {TASK_STATUS_COMPLETED})
    assert completed.status == TASK_STATUS_COMPLETED
    assert completed.artifact_manifest == ["reports/daily-news.md"]
    assert channel.status_messages == {}
    assert channel.signals == []
    assert any(
        f"automation `daily-news` · run `{task.id}` · via **artifact-agent**" in text and "# Daily News" in text
        for _, text in channel.sent
    )
    # Scratch/ephemeral dir is now labeled explicitly so it's not mistaken for
    # the primary artifact location.
    assert any(
        f"-# scratch (ephemeral): `_artifacts/{task.id}`" in text
        for _, text in channel.sent
    )
    assert not any(text.startswith("**Task Status**") for _, text in channel.sent)
    assert not any(text.startswith("**Task Update**") for _, text in channel.sent)


@pytest.mark.asyncio
async def test_scheduler_automation_formats_body_notes_and_done_footer(runtime_env):
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_AutomationNarrativeAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)
    await runtime.start()

    task = await runtime.enqueue_scheduler_task(
        session=session,
        registry=registry,
        thread_id="thread-automation-format",
        automation_name="hello-from-codex",
        prompt="Say hello with the current time.",
        author="scheduler",
        preferred_agent="automation-narrative",
    )
    assert task is not None

    completed = await _wait_for_status(store, task.id, {TASK_STATUS_COMPLETED})
    assert completed.status == TASK_STATUS_COMPLETED
    sent_texts = [text for _, text in channel.sent]
    assert any("**Output**" in text for text in sent_texts)
    assert any("Hello! This is an automation smoke test" in text for text in sent_texts)
    assert any("-# I'll fetch the current local time" in text for text in sent_texts)
    # ``response.txt`` is not under a ``reports/`` sub-tree in the workspace,
    # so rule 3 fires: published to ``reports_dir/artifacts/response.txt``.
    # The note line uses the absolute published path as the primary handle,
    # NOT the workspace-relative ``response.txt`` (which is ephemeral).
    reports_dir = runtime._reports_dir  # noqa: SLF001
    assert reports_dir is not None
    expected_published = reports_dir / "artifacts" / "response.txt"
    assert any(
        f"-# published: `{expected_published}`" in text for text in sent_texts
    )
    # The ephemeral scratch dir is labeled explicitly.
    assert any(
        f"-# scratch (ephemeral): `_artifacts/{task.id}`" in text
        for text in sent_texts
    )
    assert any(f"automation `hello-from-codex` · run `{task.id}` · via **automation-narrative**" in text for text in sent_texts)
    assert any("4,321 in / 2,109 out" in text for text in sent_texts)
    assert any("cache 90,000r/12,000w" in text for text in sent_texts)
    assert any("$0.4821" in text for text in sent_texts)
    assert any("-# ✅ automation run complete" in text for text in sent_texts)
    assert not any("TASK_STATE: DONE" in text for text in sent_texts)


@pytest.mark.asyncio
async def test_scheduler_automation_failure_includes_usage_audit(runtime_env):
    store: SQLiteMemoryStore = runtime_env["store"]
    runtime: RuntimeService = runtime_env["runtime"]
    channel: _FakeChannel = runtime_env["channel"]
    registry = AgentRegistry([_AutomationFailureAgent()])
    session = ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=registry,
    )
    runtime.register_session(session, registry)
    await runtime.start()

    task = await runtime.enqueue_scheduler_task(
        session=session,
        registry=registry,
        thread_id="thread-automation-fail",
        automation_name="daily-failure",
        prompt="Fail with a timeout.",
        author="scheduler",
        preferred_agent="automation-failure",
    )
    assert task is not None

    failed = await _wait_for_status(store, task.id, {TASK_STATUS_FAILED})
    assert failed.status == TASK_STATUS_FAILED
    sent_texts = [text for _, text in channel.sent]
    assert any(f"automation `daily-failure` · run `{task.id}` · via **automation-failure**" in text for text in sent_texts)
    assert any("321 in / 123 out" in text for text in sent_texts)
    assert any("cache 456r" in text for text in sent_texts)
    assert any("$0.0456" in text for text in sent_texts)


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
async def test_runtime_merge_sends_terminal_notification_and_records_history(runtime_env):
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
        memory_store=store,
    )
    runtime.register_session(session, registry)
    await runtime.start()

    task = await runtime.create_task(
        session=session,
        registry=registry,
        thread_id="thread-terminal",
        goal="create runtime file and merge it",
        created_by="owner-1",
        source="slash",
    )
    waiting = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.status == TASK_STATUS_WAITING_MERGE

    result = await runtime.merge_task(task.id, actor_id="owner-1")
    assert "merged successfully" in result.lower()
    assert any("merged successfully" in text.lower() for _, text in channel.sent)
    assert any(text.startswith("**Task Update**") for _, text in channel.sent)

    history = await session.get_history("thread-terminal")
    assistant_turns = [turn["content"] for turn in history if turn["role"] == "assistant"]
    assert any("queued" in text for text in assistant_turns)
    assert any("waiting merge decision" in text for text in assistant_turns)
    assert any("merged successfully" in text for text in assistant_turns)
    assert (repo / "src" / "runtime.txt").exists()


@pytest.mark.asyncio
async def test_runtime_merge_blocked_can_wait_and_retry_from_thread(runtime_env):
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

    task = await runtime.create_task(
        session=session,
        registry=registry,
        thread_id="thread-merge-retry",
        goal="fix docs and run tests",
        created_by="owner-1",
        source="slash",
    )
    waiting = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.status == TASK_STATUS_WAITING_MERGE

    (repo / "README.md").write_text("# dirty\n", encoding="utf-8")
    merge_result = await runtime.merge_task(task.id, actor_id="owner-1")
    assert "merge blocked" in merge_result.lower()

    blocked_merge = await store.get_runtime_task(task.id)
    assert blocked_merge is not None
    assert blocked_merge.status == TASK_STATUS_WAITING_MERGE
    assert blocked_merge.merge_error
    assert any(draft["task_id"] == task.id for draft in channel.drafts[1:])

    wait_msg = IncomingMessage(
        platform="discord",
        channel_id="100",
        thread_id="thread-merge-retry",
        author="owner",
        author_id="owner-1",
        content="wait",
    )
    handled_wait = await runtime.maybe_handle_thread_context(session, wait_msg, thread_id="thread-merge-retry")
    assert handled_wait is True
    waiting_again = await store.get_runtime_task(task.id)
    assert waiting_again is not None
    assert waiting_again.status == TASK_STATUS_WAITING_MERGE

    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "clean repo for retry"], cwd=repo, check=True, capture_output=True)

    retry_msg = IncomingMessage(
        platform="discord",
        channel_id="100",
        thread_id="thread-merge-retry",
        author="owner",
        author_id="owner-1",
        content="retry merge",
    )
    handled_retry = await runtime.maybe_handle_thread_context(session, retry_msg, thread_id="thread-merge-retry")
    assert handled_retry is True

    merged = await _wait_for_status(store, task.id, {TASK_STATUS_MERGED})
    assert merged.status == TASK_STATUS_MERGED
    assert merged.merge_commit_hash


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
async def test_runtime_logs_include_live_agent_log_tail(runtime_env):
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
        thread_id="thread-live-log",
        goal="write a file and finish",
        created_by="owner-1",
        source="slash",
    )
    waiting = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.status == TASK_STATUS_WAITING_MERGE

    await store.update_runtime_task(task.id, status=TASK_STATUS_RUNNING, ended_at=None)
    live_log = runtime._agent_log_path(waiting, 1, "done-agent")  # noqa: SLF001
    live_log.parent.mkdir(parents=True, exist_ok=True)
    live_log.write_text("[stdout] line one\n[stdout] line two\n", encoding="utf-8")
    runtime._live_agent_logs[task.id] = live_log  # noqa: SLF001

    logs = await runtime.get_task_logs(task.id)
    assert "Live agent log:" in logs
    assert "Live agent log tail" in logs
    assert "line two" in logs


@pytest.mark.asyncio
async def test_runtime_logs_include_thread_excerpt_after_completion(runtime_env):
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
        thread_id="thread-thread-log",
        goal="write a file and finish",
        created_by="owner-1",
        source="slash",
    )
    waiting = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.status == TASK_STATUS_WAITING_MERGE

    logs = await runtime.get_task_logs(task.id)
    assert "Thread log:" in logs
    assert "Thread log excerpt" in logs
    assert f"task_id={task.id}" in logs
    thread_log = runtime._thread_log_path("thread-thread-log")  # noqa: SLF001
    text = thread_log.read_text(encoding="utf-8")
    assert "started_at=" in text
    assert "ended_at=" in text


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
        owner_user_ids={"owner-1", "owner-2"},
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


@pytest.mark.asyncio
async def test_runtime_start_prunes_stale_agent_logs(tmp_path, caplog):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    worktree_root = tmp_path / "worktrees"
    store = SQLiteMemoryStore(tmp_path / "cleanup-logs.db")
    await store.init()

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
                "retention_hours": 0,
                "prune_git_worktrees": True,
                "merged_immediate": True,
            },
        },
        owner_user_ids={"owner-1", "owner-2"},
        repo_root=repo,
    )

    stale_log = runtime._agent_logs_root / "old-step1-done-agent.log"  # noqa: SLF001
    stale_log.parent.mkdir(parents=True, exist_ok=True)
    stale_log.write_text("old log", encoding="utf-8")
    os.utime(stale_log, (1, 1))

    caplog.set_level("INFO")
    await runtime.start()
    assert not stale_log.exists()
    assert "pruned 1 stale agent log(s) on start" in caplog.text

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


@pytest.mark.asyncio
async def test_suggest_handler_writes_budget_overrides(runtime_env):
    """Fix 2: suggest handler honors ``max_turns`` / ``timeout_seconds`` by
    mutating the task row before approval. Event payload records the override
    values; the draft surface mentions the override in the ``Budget override:``
    footer line.
    """
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

    draft = await store.create_runtime_task(
        task_id="task-sb",
        platform="discord",
        channel_id="100",
        thread_id="thread-sb",
        created_by="owner-1",
        goal="tighten docs",
        preferred_agent="done-agent",
        status=TASK_STATUS_DRAFT,
        max_steps=5,
        max_minutes=15,
        test_command="true",
    )
    assert draft.agent_max_turns is None
    assert draft.agent_timeout_seconds is None

    event = await runtime.build_slash_decision_event(
        platform="discord",
        channel_id="100",
        thread_id="thread-sb",
        task_id="task-sb",
        action="suggest",
        actor_id="owner-1",
        suggestion="please narrow to README only",
        max_turns=50,
        timeout_seconds=1200,
    )
    assert event is not None
    result = await runtime.handle_decision_event(event)
    assert "suggestion recorded" in result.lower()

    updated = await store.get_runtime_task("task-sb")
    assert updated is not None
    assert updated.agent_max_turns == 50
    assert updated.agent_timeout_seconds == 1200
    assert updated.resume_instruction == "please narrow to README only"

    events = await store.list_runtime_events("task-sb")
    suggested = [e for e in events if e["event_type"] == "task.suggested"]
    assert suggested, "task.suggested event should be recorded"
    payload = suggested[-1]["payload"]
    assert payload["max_turns_override"] == 50
    assert payload["timeout_seconds_override"] == 1200

    # Decision surface carries the override hint for the approver, and makes
    # clear this is the per-call inner budget (not the outer max_steps loop).
    latest_draft = channel.drafts[-1]
    assert "Per-call budget override" in latest_draft["draft_text"]
    assert "max_turns → 50" in latest_draft["draft_text"]
    assert "timeout → 1200s" in latest_draft["draft_text"]
    # And the footer explicitly reminds the owner that outer budgets are untouched.
    assert "max_steps" in latest_draft["draft_text"]


@pytest.mark.asyncio
async def test_suggest_handler_without_budget_leaves_fields(runtime_env):
    """Control: suggest with no budget overrides does NOT mutate the existing
    ``agent_max_turns`` / ``agent_timeout_seconds`` fields.
    """
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

    await store.create_runtime_task(
        task_id="task-sb2",
        platform="discord",
        channel_id="100",
        thread_id="thread-sb2",
        created_by="owner-1",
        goal="tighten docs",
        preferred_agent="done-agent",
        status=TASK_STATUS_DRAFT,
        max_steps=5,
        max_minutes=15,
        test_command="true",
        agent_max_turns=33,
        agent_timeout_seconds=444,
    )

    event = await runtime.build_slash_decision_event(
        platform="discord",
        channel_id="100",
        thread_id="thread-sb2",
        task_id="task-sb2",
        action="suggest",
        actor_id="owner-1",
        suggestion="go ahead",
    )
    assert event is not None
    await runtime.handle_decision_event(event)

    updated = await store.get_runtime_task("task-sb2")
    assert updated is not None
    # Unchanged.
    assert updated.agent_max_turns == 33
    assert updated.agent_timeout_seconds == 444
    # No ``Per-call budget override`` footer when neither kwarg was set.
    latest_draft = channel.drafts[-1]
    assert "budget override" not in latest_draft["draft_text"].lower()


@pytest.mark.asyncio
async def test_start_auth_login_sends_qr_prompt(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    store = SQLiteMemoryStore(tmp_path / "auth-runtime.db")
    await store.init()
    auth = _FakeAuthService()
    runtime = RuntimeService(
        store,
        config={
            "enabled": True,
            "worker_concurrency": 1,
            "worktree_root": str(tmp_path / "worktrees"),
            "default_agent": "done-agent",
            "default_test_command": "true",
            "risk_profile": "strict",
            "cleanup": {"enabled": False},
            "merge_gate": {"enabled": True},
        },
        owner_user_ids={"owner-1", "owner-2"},
        repo_root=repo,
        auth_service=auth,
    )
    channel = _FakeChannel()
    registry = AgentRegistry([_DoneAgent()])
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)
    runtime.register_session(session, registry)

    result = await runtime.start_auth_login(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        provider="bilibili",
        actor_id="owner-1",
    )

    assert "QR code sent" in result
    assert channel.sent
    assert channel.attachments

    await runtime.stop()
    await store.close()


@pytest.mark.asyncio
async def test_mark_task_auth_required_waits_then_resumes(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    store = SQLiteMemoryStore(tmp_path / "auth-runtime.db")
    await store.init()
    auth = _FakeAuthService()
    runtime = RuntimeService(
        store,
        config={
            "enabled": True,
            "worker_concurrency": 1,
            "worktree_root": str(tmp_path / "worktrees"),
            "default_agent": "done-agent",
            "default_test_command": "true",
            "risk_profile": "strict",
            "cleanup": {"enabled": False},
            "merge_gate": {"enabled": True},
        },
        owner_user_ids={"owner-1", "owner-2"},
        repo_root=repo,
        auth_service=auth,
    )
    channel = _FakeChannel()
    registry = AgentRegistry([_DoneAgent()])
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)
    runtime.register_session(session, registry)

    task = await store.create_runtime_task(
        task_id="task-auth-1",
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        created_by="owner-1",
        goal="wait for auth",
        preferred_agent="done-agent",
        status="RUNNING",
        max_steps=4,
        max_minutes=5,
        test_command="true",
    )

    result = await runtime.mark_task_auth_required(
        task.id,
        provider="bilibili",
        reason="login_required",
    )
    waiting = await store.get_runtime_task(task.id)

    assert "waiting for `bilibili` login" in result
    assert waiting is not None
    assert waiting.status == TASK_STATUS_WAITING_USER_INPUT
    notifications = await store.list_active_notification_events(
        dedupe_key=f"task:{task.id}:auth_required",
        limit=10,
    )
    assert len(notifications) == 2
    assert any(
        text.startswith("<@owner-1> <@owner-2> **Action required**") and "Reason: auth_required" in text
        for _, text in channel.sent
    )

    await auth.emit("approved", "flow-1")
    resumed = await store.get_runtime_task(task.id)
    assert resumed is not None
    assert resumed.status == "PENDING"
    assert await store.list_active_notification_events(
        dedupe_key=f"task:{task.id}:auth_required",
        limit=10,
    ) == []

    await runtime.stop()
    await store.close()


@pytest.mark.asyncio
async def test_mark_thread_auth_required_waits_then_resumes(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    store = SQLiteMemoryStore(tmp_path / "auth-thread.db")
    await store.init()
    auth = _FakeAuthService()
    runtime = RuntimeService(
        store,
        config={
            "enabled": True,
            "worker_concurrency": 1,
            "worktree_root": str(tmp_path / "worktrees"),
            "default_agent": "resume-agent",
            "default_test_command": "true",
            "risk_profile": "strict",
            "cleanup": {"enabled": False},
            "merge_gate": {"enabled": True},
        },
        owner_user_ids={"owner-1"},
        repo_root=repo,
        auth_service=auth,
    )
    channel = _FakeChannel()
    agent = _ResumableAuthAgent()
    registry = AgentRegistry([agent])
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)
    session.memory_store = store
    await session.append_user("thread-1", "summarize the video", "alice")
    runtime.register_session(session, registry)

    result = await runtime.mark_thread_auth_required(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        provider="bilibili",
        reason="login_required",
        actor_id="owner-1",
        agent_name="resume-agent",
        control_envelope_json=(
            '{"version":1,"type":"challenge","data":{"challenge_type":"auth_required","provider":"bilibili","reason":"login_required"}}'
        ),
        session_id_snapshot="sess-123",
        resume_context={"agent_prompt": "summarize the video", "skill_name": "bilibili-video-summary"},
    )

    assert "waiting for `bilibili` login" in result
    run = await store.get_active_suspended_agent_run(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        provider="bilibili",
    )
    assert run is not None
    assert run.status == "waiting_auth"
    notifications = await store.list_active_notification_events(
        dedupe_key="thread:thread-1:auth_required",
        limit=10,
    )
    assert len(notifications) == 1
    assert any(
        text.startswith("<@owner-1> **Action required**") and "Reason: auth_required" in text
        for _, text in channel.sent
    )

    await auth.emit("approved", "flow-1")
    for _ in range(10):
        await asyncio.sleep(0.05)
        run = await store.get_suspended_agent_run(run.id)
        if run is not None and run.status == "completed":
            break

    run = await store.get_suspended_agent_run(run.id)
    assert run is not None
    assert run.status == "completed"
    assert channel.sent[-1][1].endswith("final resumed answer")
    assert "10 in / 5 out" in channel.sent[-1][1]
    assert "cache 20r/3w" in channel.sent[-1][1]
    assert "$0.1234" in channel.sent[-1][1]
    assert any("--cookies-path '/tmp/cookies.txt'" in prompt for prompt in agent.prompts)
    history = await session.get_history("thread-1")
    assert history[-1]["role"] == "assistant"
    assert history[-1]["content"] == "final resumed answer"
    assert await store.list_active_notification_events(
        dedupe_key="thread:thread-1:auth_required",
        limit=10,
    ) == []

    await runtime.stop()
    await store.close()


@pytest.mark.asyncio
async def test_mark_thread_ask_user_waits_then_resumes(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    store = SQLiteMemoryStore(tmp_path / "hitl-thread.db")
    await store.init()
    runtime = RuntimeService(
        store,
        config={
            "enabled": True,
            "worker_concurrency": 1,
            "worktree_root": str(tmp_path / "worktrees"),
            "default_agent": "ask-user-agent",
            "default_test_command": "true",
            "risk_profile": "strict",
            "cleanup": {"enabled": False},
            "merge_gate": {"enabled": True},
        },
        owner_user_ids={"owner-1", "owner-2"},
        repo_root=repo,
    )
    channel = _FakeChannel()
    agent = _ResumableAskUserAgent()
    registry = AgentRegistry([agent])
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)
    session.memory_store = store
    await session.append_user("thread-1", "帮我继续这个分析", "alice")
    runtime.register_session(session, registry)

    result = await runtime.mark_thread_ask_user_required(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        actor_id="owner-1",
        agent_name="ask-user-agent",
        question="今天先看哪一条线？",
        details="只能单选。",
        choices=(
            {"id": "politics", "label": "Politics daily", "description": "关注地缘政治"},
            {"id": "finance", "label": "Finance daily", "description": "关注财报"},
        ),
        control_envelope_json=(
            '{"version":1,"type":"challenge","data":{"challenge_type":"ask_user","question":"今天先看哪一条线？","details":"只能单选。","choices":[{"id":"politics","label":"Politics daily","description":"关注地缘政治"},{"id":"finance","label":"Finance daily","description":"关注财报"}]}}'
        ),
        session_id_snapshot="sess-hitl",
        resume_context={"agent_prompt": "继续完成分析", "original_user_content": "帮我继续这个分析"},
    )

    assert "waiting for input" in result
    assert channel.hitl_prompts
    notifications = await store.list_active_notification_events(
        dedupe_key="thread:thread-1:ask_user",
        limit=10,
    )
    assert len(notifications) == 2
    assert any(
        text.startswith("<@owner-1> <@owner-2> **Action required**") and "Reason: ask_user" in text
        for _, text in channel.sent
    )
    assert {user_id for user_id, _ in channel.dms} == {"owner-1", "owner-2"}

    prompt = await store.get_active_hitl_prompt_for_thread(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
    )
    assert prompt is not None
    assert prompt.status == "waiting"

    resolved = await runtime.answer_hitl_prompt(prompt.id, choice_id="finance", actor_id="owner-1")
    assert "resumed successfully" in resolved

    prompt = await store.get_hitl_prompt(prompt.id)
    assert prompt is not None
    assert prompt.status == "completed"
    assert prompt.selected_choice_id == "finance"
    assert await store.list_active_notification_events(
        dedupe_key="thread:thread-1:ask_user",
        limit=10,
    ) == []

    history = await session.get_history("thread-1")
    resolved_prompt = await store.get_hitl_prompt(prompt.id)
    assert resolved_prompt is not None
    assert resolved_prompt.resume_context["last_hitl_answer"]["choice_id"] == "finance"
    assert history[-2]["role"] == "user"
    assert history[-2]["content"].startswith("[HITL Answer]")
    assert "Selected choice id: finance" in history[-2]["content"]
    assert history[-1]["role"] == "assistant"
    assert history[-1]["content"] == "根据你的选择，我继续完成这次分析。"
    assert any("The previous run paused because it required a single explicit user choice." in prompt for prompt in agent.prompts)
    assert any("Structured HITL answer payload:" in prompt for prompt in agent.prompts)
    assert any('"choice_id": "finance"' in prompt for prompt in agent.prompts)

    await runtime.stop()
    await store.close()


@pytest.mark.asyncio
async def test_thread_hitl_nested_prompt_keeps_structured_answer_payload(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    store = SQLiteMemoryStore(tmp_path / "hitl-thread-nested.db")
    await store.init()
    runtime = RuntimeService(
        store,
        config={
            "enabled": True,
            "worker_concurrency": 1,
            "worktree_root": str(tmp_path / "worktrees"),
            "default_agent": "ask-user-agent",
            "default_test_command": "true",
            "risk_profile": "strict",
            "cleanup": {"enabled": False},
            "merge_gate": {"enabled": True},
        },
        owner_user_ids={"owner-1"},
        repo_root=repo,
    )
    channel = _FakeChannel()
    agent = _ResumableAskUserChallengeAgent()
    registry = AgentRegistry([agent])
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)
    session.memory_store = store
    await session.append_user("thread-1", "继续做这个判断", "alice")
    runtime.register_session(session, registry)

    result = await runtime.mark_thread_ask_user_required(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        actor_id="owner-1",
        agent_name="ask-user-agent",
        question="先按哪个方向？",
        details="只能单选。",
        choices=(
            {"id": "politics", "label": "Politics daily", "description": "关注地缘政治"},
            {"id": "finance", "label": "Finance daily", "description": "关注财报"},
        ),
        control_envelope_json=(
            '{"version":1,"type":"challenge","data":{"challenge_type":"ask_user","question":"先按哪个方向？","details":"只能单选。","choices":[{"id":"politics","label":"Politics daily","description":"关注地缘政治"},{"id":"finance","label":"Finance daily","description":"关注财报"}]}}'
        ),
        session_id_snapshot="sess-hitl",
        resume_context={"agent_prompt": "继续做这个判断", "original_user_content": "继续做这个判断"},
    )

    assert "waiting for input" in result
    first_prompt = await store.get_active_hitl_prompt_for_thread(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
    )
    assert first_prompt is not None

    resumed = await runtime.answer_hitl_prompt(first_prompt.id, choice_id="finance", actor_id="owner-1")
    assert "waiting for input" in resumed

    second_prompt = await store.get_active_hitl_prompt_for_thread(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
    )
    assert second_prompt is not None
    assert second_prompt.id != first_prompt.id
    assert isinstance(second_prompt.resume_context["last_hitl_answer"], dict)
    assert second_prompt.resume_context["last_hitl_answer"]["choice_id"] == "finance"
    assert second_prompt.resume_context["last_hitl_answer"]["target_kind"] == "thread"

    await runtime.stop()
    await store.close()


@pytest.mark.asyncio
async def test_thread_hitl_resume_logs_progress_and_honors_skill_timeout_override(tmp_path, caplog):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    skills_path = tmp_path / "skills"
    skill_dir = skills_path / "market-briefing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: market-briefing
metadata:
  timeout_seconds: 900
  max_turns: 80
---
""",
        encoding="utf-8",
    )

    store = SQLiteMemoryStore(tmp_path / "hitl-thread-logging.db")
    await store.init()
    runtime = RuntimeService(
        store,
        config={
            "enabled": True,
            "worker_concurrency": 1,
            "worktree_root": str(tmp_path / "worktrees"),
            "default_agent": "ask-user-agent",
            "default_test_command": "true",
            "risk_profile": "strict",
            "cleanup": {"enabled": False},
            "merge_gate": {"enabled": True},
        },
        owner_user_ids={"owner-1"},
        repo_root=repo,
        skills_path=skills_path,
    )
    runtime._agent_heartbeat_seconds = 0.005
    channel = _FakeChannel()
    agent = _ResumableAskUserAgent()
    registry = MagicMock(spec=AgentRegistry)
    registry.get_agent.return_value = agent

    async def _slow_resume(*args, **kwargs):
        await asyncio.sleep(0.02)
        return agent, AgentResponse(text="根据你的选择，我继续完成这次分析。")

    registry.run = AsyncMock(side_effect=_slow_resume)
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)
    session.memory_store = store
    await session.append_user("thread-1", "帮我继续这个分析", "alice")
    runtime.register_session(session, registry)

    await runtime.mark_thread_ask_user_required(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        actor_id="owner-1",
        agent_name="ask-user-agent",
        question="今天先看哪一条线？",
        details="只能单选。",
        choices=(
            {"id": "finance", "label": "Finance daily", "description": "关注财报"},
            {"id": "ai", "label": "AI daily", "description": "关注五层结构"},
        ),
        control_envelope_json=(
            '{"version":1,"type":"challenge","data":{"challenge_type":"ask_user","question":"今天先看哪一条线？","details":"只能单选。","choices":[{"id":"finance","label":"Finance daily","description":"关注财报"},{"id":"ai","label":"AI daily","description":"关注五层结构"}]}}'
        ),
        session_id_snapshot="sess-hitl",
        resume_context={
            "agent_prompt": "继续完成分析",
            "original_user_content": "帮我继续这个分析",
            "skill_name": "market-briefing",
        },
    )
    prompt = await store.get_active_hitl_prompt_for_thread(
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
    )
    assert prompt is not None

    with caplog.at_level("INFO"):
        result = await runtime.answer_hitl_prompt(prompt.id, choice_id="ai", actor_id="owner-1")

    assert "resumed successfully" in result
    assert registry.run.await_args.kwargs["timeout_override_seconds"] == 900
    assert registry.run.await_args.kwargs["max_turns_override"] == 80
    assert "THREAD_AGENT_RUNNING purpose=hitl_resume" in caplog.text
    assert "THREAD_AGENT_DONE purpose=hitl_resume" in caplog.text

    await runtime.stop()
    await store.close()


@pytest.mark.asyncio
async def test_mark_task_ask_user_waits_and_answer_requeues_task(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    store = SQLiteMemoryStore(tmp_path / "hitl-task.db")
    await store.init()
    runtime = RuntimeService(
        store,
        config={
            "enabled": True,
            "worker_concurrency": 1,
            "worktree_root": str(tmp_path / "worktrees"),
            "default_agent": "done-agent",
            "default_test_command": "true",
            "risk_profile": "strict",
            "cleanup": {"enabled": False},
            "merge_gate": {"enabled": True},
        },
        owner_user_ids={"owner-1"},
        repo_root=repo,
    )
    channel = _FakeChannel()
    registry = AgentRegistry([_DoneAgent()])
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)
    runtime.register_session(session, registry)

    task = await store.create_runtime_task(
        task_id="task-hitl-1",
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        created_by="owner-1",
        goal="wait for input",
        preferred_agent="done-agent",
        status="RUNNING",
        max_steps=2,
        max_minutes=5,
        test_command="true",
    )

    result = await runtime.mark_task_ask_user_required(
        task.id,
        question="今天先跑哪份报告？",
        details="单选。",
        choices=(
            {"id": "politics", "label": "Politics daily", "description": "关注政策"},
            {"id": "ai", "label": "AI daily", "description": "关注五层结构"},
        ),
        control_envelope_json=(
            '{"version":1,"type":"challenge","data":{"challenge_type":"ask_user","question":"今天先跑哪份报告？","details":"单选。","choices":[{"id":"politics","label":"Politics daily","description":"关注政策"},{"id":"ai","label":"AI daily","description":"关注五层结构"}]}}'
        ),
    )
    assert "waiting for owner input" in result

    waiting = await store.get_runtime_task(task.id)
    assert waiting is not None
    assert waiting.status == TASK_STATUS_WAITING_USER_INPUT
    assert waiting.blocked_reason.startswith("Awaiting user choice:")
    prompt = await store.get_active_hitl_prompt_for_task(task.id)
    assert prompt is not None
    assert channel.hitl_prompts
    notifications = await store.list_active_notification_events(
        dedupe_key=f"task:{task.id}:ask_user",
        limit=10,
    )
    assert len(notifications) == 1
    assert any(
        text.startswith("<@owner-1> **Action required**") and "Reason: ask_user" in text
        for _, text in channel.sent
    )

    resumed = await runtime.answer_hitl_prompt(prompt.id, choice_id="ai", actor_id="owner-1")
    assert "re-queued" in resumed

    task_after = await store.get_runtime_task(task.id)
    assert task_after is not None
    assert task_after.status == "PENDING"
    assert task_after.resume_instruction is not None
    assert task_after.resume_instruction.startswith("[HITL Answer]")
    assert "Selected choice id: ai" in task_after.resume_instruction

    prompt_after = await store.get_hitl_prompt(prompt.id)
    assert prompt_after is not None
    assert prompt_after.status == "completed"
    assert prompt_after.resume_context["last_hitl_answer"]["choice_id"] == "ai"
    events = await store.list_runtime_events(task.id, limit=10)
    answered = [event for event in events if event["event_type"] == "task.ask_user_answered"]
    assert answered
    assert answered[-1]["payload"]["choice_id"] == "ai"
    assert answered[-1]["payload"]["target_kind"] == "task"
    assert await store.list_active_notification_events(
        dedupe_key=f"task:{task.id}:ask_user",
        limit=10,
    ) == []

    await runtime.stop()
    await store.close()


@pytest.mark.asyncio
async def test_cancel_task_hitl_prompt_blocks_task(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    store = SQLiteMemoryStore(tmp_path / "hitl-task-cancel.db")
    await store.init()
    runtime = RuntimeService(
        store,
        config={
            "enabled": True,
            "worker_concurrency": 1,
            "worktree_root": str(tmp_path / "worktrees"),
            "default_agent": "done-agent",
            "default_test_command": "true",
            "risk_profile": "strict",
            "cleanup": {"enabled": False},
            "merge_gate": {"enabled": True},
        },
        owner_user_ids={"owner-1"},
        repo_root=repo,
    )
    channel = _FakeChannel()
    registry = AgentRegistry([_DoneAgent()])
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)
    runtime.register_session(session, registry)

    task = await store.create_runtime_task(
        task_id="task-hitl-cancel-1",
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        created_by="owner-1",
        goal="wait for input",
        preferred_agent="done-agent",
        status="RUNNING",
        max_steps=2,
        max_minutes=5,
        test_command="true",
    )

    await runtime.mark_task_ask_user_required(
        task.id,
        question="今天先跑哪份报告？",
        details=None,
        choices=(
            {"id": "politics", "label": "Politics daily", "description": None},
        ),
        control_envelope_json=(
            '{"version":1,"type":"challenge","data":{"challenge_type":"ask_user","question":"今天先跑哪份报告？","choices":[{"id":"politics","label":"Politics daily"}]}}'
        ),
    )
    prompt = await store.get_active_hitl_prompt_for_task(task.id)
    assert prompt is not None

    cancelled = await runtime.cancel_hitl_prompt(prompt.id, actor_id="owner-1")
    assert "cancelled" in cancelled

    task_after = await store.get_runtime_task(task.id)
    assert task_after is not None
    assert task_after.status == TASK_STATUS_BLOCKED
    assert task_after.blocked_reason == "User cancelled HITL prompt."

    prompt_after = await store.get_hitl_prompt(prompt.id)
    assert prompt_after is not None
    assert prompt_after.status == "cancelled"
    assert await store.list_active_notification_events(
        dedupe_key=f"task:{task.id}:ask_user",
        limit=10,
    ) == []

    await runtime.stop()
    await store.close()
