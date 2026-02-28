from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from oh_my_agent.agents.base import AgentResponse, BaseAgent
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.runtime.service import RuntimeService
from oh_my_agent.runtime.types import TASK_STATUS_DRAFT, TASK_STATUS_MERGED, TASK_STATUS_PENDING, TASK_STATUS_WAITING_MERGE, TASK_TYPE_SKILL


@dataclass
class _FakeChannel:
    platform: str = "discord"
    channel_id: str = "100"
    _next_msg_id: int = 1
    sent: list[tuple[str, str]] = field(default_factory=list)
    drafts: list[dict] = field(default_factory=list)
    status_messages: dict[str, tuple[str, str]] = field(default_factory=dict)

    async def send(self, thread_id: str, text: str) -> str:
        self.sent.append((thread_id, text))
        msg_id = f"m-{self._next_msg_id}"
        self._next_msg_id += 1
        return msg_id

    async def upsert_status_message(self, thread_id: str, text: str, *, message_id: str | None = None) -> str:
        if message_id and message_id in self.status_messages:
            self.status_messages[message_id] = (thread_id, text)
            return message_id
        msg_id = f"s-{self._next_msg_id}"
        self._next_msg_id += 1
        self.status_messages[msg_id] = (thread_id, text)
        return msg_id

    async def send_task_draft(self, *, thread_id: str, draft_text: str, task_id: str, nonce: str, actions: list[str]) -> str | None:
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
        return None


class _ClaudeSkillAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "claude"

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
        skill_dir = workspace_override / "skills" / "weather"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: weather\ndescription: Check weather quickly\n---\n\nUse this skill for weather.\n",
            encoding="utf-8",
        )
        return AgentResponse(text=f"{prompt}\nTASK_STATE: DONE")


class _FakeSkillSyncer:
    def __init__(self) -> None:
        self.calls = 0
        self.workspace_refresh_calls: list[list[Path]] = []
        self.agents_md_roots: list[Path] = []

    def sync(self) -> int:
        self.calls += 1
        return 1

    def refresh_workspace_dirs(self, workspace_target_dirs=None) -> int:
        dirs = list(workspace_target_dirs or [])
        self.workspace_refresh_calls.append(dirs)
        for target_dir in dirs:
            target_dir.mkdir(parents=True, exist_ok=True)
            skill_dir = target_dir / "weather"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text("---\nname: weather\n---\n", encoding="utf-8")
        roots = {
            target_dir.parent.parent
            for target_dir in dirs
            if target_dir.name == "skills"
        }
        for root in roots:
            self.write_workspace_agents_md(root)
        return len(dirs)

    def write_workspace_agents_md(self, workspace_root: Path) -> Path:
        self.agents_md_roots.append(workspace_root)
        target = workspace_root / "AGENTS.md"
        target.write_text("# Workspace AGENTS.md\n", encoding="utf-8")
        return target


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("# skill repo\n", encoding="utf-8")
    quick_validate = root / "skills" / "skill-creator" / "scripts" / "quick_validate.py"
    quick_validate.parent.mkdir(parents=True, exist_ok=True)
    quick_validate.write_text(
        "import sys\nfrom pathlib import Path\n"
        "skill = Path(sys.argv[1])\n"
        "skill_md = skill / 'SKILL.md'\n"
        "sys.exit(0 if skill_md.exists() and skill_md.read_text(encoding='utf-8').startswith('---') else 1)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Skill Test"], cwd=root, check=True, capture_output=True)
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
async def skill_runtime_env(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)

    store = SQLiteMemoryStore(tmp_path / "runtime.db")
    await store.init()

    syncer = _FakeSkillSyncer()
    workspace_skills_dirs = [
        tmp_path / "agent-workspace" / ".claude" / "skills",
        tmp_path / "agent-workspace" / ".gemini" / "skills",
        tmp_path / "agent-workspace" / ".codex" / "skills",
    ]
    runtime = RuntimeService(
        store,
        config={
            "enabled": True,
            "worker_concurrency": 1,
            "worktree_root": str(tmp_path / "worktrees"),
            "default_agent": "codex",
            "default_test_command": "true",
            "cleanup": {"enabled": False},
            "merge_gate": {
                "enabled": True,
                "auto_commit": True,
                "require_clean_repo": True,
                "preflight_check": True,
                "target_branch_mode": "current",
                "commit_message_template": "runtime(task:{task_id}): {goal_short}",
            },
        },
        owner_user_ids={"owner-1"},
        repo_root=repo,
        skill_syncer=syncer,
        skills_path=repo / "skills",
        workspace_skills_dirs=workspace_skills_dirs,
    )
    channel = _FakeChannel()
    yield {
        "repo": repo,
        "store": store,
        "runtime": runtime,
        "syncer": syncer,
        "channel": channel,
        "workspace_skills_dirs": workspace_skills_dirs,
    }
    await runtime.stop()
    await store.close()


@pytest.mark.asyncio
async def test_create_skill_task_forces_draft_and_uses_runtime_default_agent(skill_runtime_env):
    runtime: RuntimeService = skill_runtime_env["runtime"]
    channel: _FakeChannel = skill_runtime_env["channel"]
    store: SQLiteMemoryStore = skill_runtime_env["store"]

    registry = AgentRegistry([_ClaudeSkillAgent()])
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)

    task = await runtime.create_skill_task(
        session=session,
        registry=registry,
        thread_id="thread-skill",
        goal="create a skill for weather checking",
        raw_request="create a skill for weather checking",
        created_by="owner-1",
        skill_name="weather",
        source="router",
    )
    loaded = await store.get_runtime_task(task.id)
    assert loaded is not None
    # skill_auto_approve defaults to True → skips draft, goes straight to PENDING
    assert loaded.status == TASK_STATUS_PENDING
    assert loaded.task_type == TASK_TYPE_SKILL
    assert loaded.skill_name == "weather"
    assert loaded.preferred_agent == "codex"
    assert "quick_validate.py skills/weather" in loaded.test_command


@pytest.mark.asyncio
async def test_create_skill_task_respects_explicit_preferred_agent(skill_runtime_env):
    runtime: RuntimeService = skill_runtime_env["runtime"]
    channel: _FakeChannel = skill_runtime_env["channel"]
    store: SQLiteMemoryStore = skill_runtime_env["store"]

    registry = AgentRegistry([_ClaudeSkillAgent()])
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)

    task = await runtime.create_skill_task(
        session=session,
        registry=registry,
        thread_id="thread-skill-explicit",
        goal="create a skill for weather checking",
        raw_request="create a skill for weather checking",
        created_by="owner-1",
        preferred_agent="claude",
        skill_name="weather",
        source="router",
    )
    loaded = await store.get_runtime_task(task.id)
    assert loaded is not None
    assert loaded.preferred_agent == "claude"


@pytest.mark.asyncio
async def test_skill_task_merge_records_provenance(skill_runtime_env, tmp_path):
    channel: _FakeChannel = skill_runtime_env["channel"]
    store: SQLiteMemoryStore = skill_runtime_env["store"]
    syncer: _FakeSkillSyncer = skill_runtime_env["syncer"]
    repo: Path = skill_runtime_env["repo"]
    workspace_skills_dirs: list[Path] = skill_runtime_env["workspace_skills_dirs"]

    # Use skill_auto_approve=False to test manual draft→approve→merge flow
    runtime = RuntimeService(
        store,
        config={
            "enabled": True,
            "worker_concurrency": 1,
            "worktree_root": str(tmp_path / "worktrees-merge"),
            "default_agent": "codex",
            "default_test_command": "true",
            "cleanup": {"enabled": False},
            "skill_auto_approve": False,
            "merge_gate": {
                "enabled": True,
                "auto_commit": True,
                "require_clean_repo": True,
                "preflight_check": True,
                "target_branch_mode": "current",
                "commit_message_template": "runtime(task:{task_id}): {goal_short}",
            },
        },
        owner_user_ids={"owner-1"},
        repo_root=repo,
        skill_syncer=syncer,
        skills_path=repo / "skills",
        workspace_skills_dirs=workspace_skills_dirs,
    )

    registry = AgentRegistry([_ClaudeSkillAgent()])
    session = ChannelSession(platform="discord", channel_id="100", channel=channel, registry=registry)
    runtime.register_session(session, registry)
    await runtime.start()

    task = await runtime.create_skill_task(
        session=session,
        registry=registry,
        thread_id="thread-skill-merge",
        goal="create a skill for checking the weather",
        raw_request="create a skill for checking the weather",
        created_by="owner-1",
        skill_name="weather",
        source="router",
    )
    assert task.status == TASK_STATUS_DRAFT

    approve_event = await runtime.build_slash_decision_event(
        platform="discord",
        channel_id="100",
        thread_id="thread-skill-merge",
        task_id=task.id,
        action="approve",
        actor_id="owner-1",
    )
    assert approve_event is not None
    await runtime.handle_decision_event(approve_event)

    waiting = await _wait_for_status(store, task.id, {TASK_STATUS_WAITING_MERGE})
    assert waiting.status == TASK_STATUS_WAITING_MERGE
    assert waiting.skill_name == "weather"

    merge_event = await runtime.build_slash_decision_event(
        platform="discord",
        channel_id="100",
        thread_id="thread-skill-merge",
        task_id=task.id,
        action="merge",
        actor_id="owner-1",
    )
    assert merge_event is not None
    result = await runtime.handle_decision_event(merge_event)
    assert "reload-skills" in result.lower()

    merged = await _wait_for_status(store, task.id, {TASK_STATUS_MERGED})
    assert merged.status == TASK_STATUS_MERGED
    assert (repo / "skills" / "weather" / "SKILL.md").exists()
    assert syncer.calls == 1
    assert syncer.workspace_refresh_calls
    for target in workspace_skills_dirs:
        assert (target / "weather" / "SKILL.md").exists()
    assert (workspace_skills_dirs[0].parent.parent / "AGENTS.md").exists()

    provenance = await store.get_skill_provenance("weather")
    assert provenance is not None
    assert provenance["source_task_id"] == task.id
    assert provenance["validated"] == 1
    assert provenance["agent_name"] == "claude"
    assert provenance["merged_commit_hash"]

    await runtime.stop()
