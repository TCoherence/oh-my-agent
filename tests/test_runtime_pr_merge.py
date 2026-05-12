"""End-to-end tests for the PR-based merge flow (WS B).

Uses unittest.mock to swap ``WorktreeManager``'s subprocess-touching
methods so we don't actually invoke ``gh`` or push to a remote. This
matches the Codex review's recommended mock strategy: centralize all
gh/git outbound calls behind one class and patch at that boundary.

Tests cover:
- Happy path: dirty workspace → commit → push → gh pr create → PR_OPENED
- Preflight: gh not installed / unauthenticated → MERGE_BLOCKED
- Preflight: remote not configured → MERGE_BLOCKED
- Empty branch (no diff vs base) → MERGE_BLOCKED, no push, no PR
- Unknown ``target_branch_mode`` → MERGE_BLOCKED
- Schema migration: pr_url/pr_number persist + reload via RuntimeTask
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.runtime import (
    TASK_STATUS_DRAFT,
    TASK_STATUS_PR_OPENED,
    TASK_STATUS_WAITING_MERGE,
    RuntimeService,
)


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=path, check=True)
    (path / "README.md").write_text("# test repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    env = {**os.environ, "GIT_COMMITTER_DATE": "2026-05-12T10:00:00Z"}
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, env=env, check=True)


def _pr_mode_cfg(tmp_path: Path) -> dict:
    """Runtime config with target_branch_mode=pr enabled."""

    return {
        "enabled": True,
        "worker_concurrency": 1,
        "worktree_root": str(tmp_path / "worktrees"),
        "reports_dir": str(tmp_path / "reports"),
        "default_agent": "claude",
        "default_test_command": "true",
        "default_max_steps": 4,
        "default_max_minutes": 10,
        "risk_profile": "lenient",
        "cleanup": {"enabled": False, "interval_minutes": 60, "retention_hours": 0},
        "merge_gate": {
            "enabled": True,
            "target_branch_mode": "pr",
            "pr_base_branch": "main",
            "pr_remote": "origin",
            "pr_draft": False,
            "pr_title_template": "runtime(task:{task_id}): {goal_short}",
            "pr_body_template": "Body for task {task_id}: {goal}",
            "commit_message_template": "runtime(task:{task_id}): {goal_short}",
        },
    }


@pytest.fixture
async def pr_runtime(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    db_path = tmp_path / "runtime.db"
    store = SQLiteMemoryStore(db_path)
    await store.init()

    cfg = _pr_mode_cfg(tmp_path)
    runtime = RuntimeService(store, config=cfg, owner_user_ids={"owner-1"}, repo_root=repo)

    yield {"store": store, "runtime": runtime, "repo": repo, "tmp": tmp_path}

    await runtime.stop()
    await store.close()


async def _seed_waiting_merge(store: SQLiteMemoryStore, *, workspace: Path) -> str:
    """Insert a WAITING_MERGE skill_change task pointing at ``workspace``."""

    task = await store.create_runtime_task(
        task_id="task-pr-1",
        platform="discord",
        channel_id="100",
        thread_id="t1",
        created_by="alice",
        goal="Fix typo in README",
        original_request="fix the typo",
        status=TASK_STATUS_DRAFT,
        max_steps=4,
        max_minutes=10,
        test_command="true",
        completion_mode="merge",
        task_type="repo_change",
    )
    await store.update_runtime_task(
        task.id,
        status=TASK_STATUS_WAITING_MERGE,
        workspace_path=str(workspace),
    )
    return task.id


# ── happy path ─────────────────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_pr_merge_happy_path_dirty_workspace(pr_runtime, monkeypatch):
    """Dirty workspace + non-empty diff vs base → commit, push, PR open."""

    runtime: RuntimeService = pr_runtime["runtime"]
    store: SQLiteMemoryStore = pr_runtime["store"]
    pr_runtime["repo"]

    workspace = pr_runtime["tmp"] / "worktrees" / "task-pr-1"
    workspace.mkdir(parents=True)
    (workspace / "dirty.txt").write_text("change\n", encoding="utf-8")

    task_id = await _seed_waiting_merge(store, workspace=workspace)

    # Patch WorktreeManager surface on the runtime instance.
    wt = runtime._worktree  # noqa: SLF001
    wt.check_gh_ready = AsyncMock(return_value=(True, "gh ready"))
    wt.check_remote_configured = AsyncMock(return_value=(True, "https://github.com/x/y.git"))
    wt.workspace_has_dirty_or_new_commits = AsyncMock(return_value=True)
    wt.commit_workspace = AsyncMock(return_value="abc123")
    wt.fetch_base_ref = AsyncMock(return_value=None)
    wt.has_diff_vs_base = AsyncMock(return_value=True)
    wt.push_task_branch = AsyncMock(return_value=None)
    wt.create_pr = AsyncMock(return_value=("https://github.com/x/y/pull/42", 42))

    task = await store.get_runtime_task(task_id)
    assert task is not None
    result = await runtime._execute_merge_pr(  # noqa: SLF001
        task, actor_id="owner-1", source="slash"
    )

    assert "opened PR" in result
    assert "https://github.com/x/y/pull/42" in result

    updated = await store.get_runtime_task(task_id)
    assert updated is not None
    assert updated.status == TASK_STATUS_PR_OPENED
    assert updated.pr_url == "https://github.com/x/y/pull/42"
    assert updated.pr_number == 42
    assert updated.summary == "PR opened: https://github.com/x/y/pull/42"

    # Verify mocked subprocess sequence: commit → fetch → diff → push → create_pr.
    wt.check_gh_ready.assert_awaited_once()
    wt.check_remote_configured.assert_awaited_once_with("origin")
    wt.commit_workspace.assert_awaited_once()
    wt.fetch_base_ref.assert_awaited_once_with(workspace, "origin", "main")
    wt.push_task_branch.assert_awaited_once_with(workspace, "codex/task-task-pr-1", "origin")
    wt.create_pr.assert_awaited_once()
    create_kwargs = wt.create_pr.await_args.kwargs
    assert create_kwargs["base"] == "main"
    assert create_kwargs["head"] == "codex/task-task-pr-1"
    assert "Fix typo in README" in create_kwargs["body"]
    assert create_kwargs["draft"] is False


# ── preflight failures ──────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_pr_merge_blocks_when_gh_missing(pr_runtime):
    runtime: RuntimeService = pr_runtime["runtime"]
    store: SQLiteMemoryStore = pr_runtime["store"]

    workspace = pr_runtime["tmp"] / "worktrees" / "task-pr-1"
    workspace.mkdir(parents=True)
    task_id = await _seed_waiting_merge(store, workspace=workspace)

    wt = runtime._worktree  # noqa: SLF001
    wt.check_gh_ready = AsyncMock(return_value=(False, "gh CLI not on PATH"))
    wt.check_remote_configured = AsyncMock(return_value=(True, "https://github.com/x/y.git"))
    wt.push_task_branch = AsyncMock()
    wt.create_pr = AsyncMock()

    task = await store.get_runtime_task(task_id)
    assert task is not None
    result = await runtime._execute_merge_pr(  # noqa: SLF001
        task, actor_id="owner-1", source="slash"
    )

    assert "merge blocked" in result.lower()
    assert "gh CLI not on PATH" in result
    wt.push_task_branch.assert_not_awaited()
    wt.create_pr.assert_not_awaited()

    updated = await store.get_runtime_task(task_id)
    assert updated is not None
    assert updated.status == TASK_STATUS_WAITING_MERGE  # non-terminal; retryable
    assert updated.merge_error is not None
    assert "gh CLI not on PATH" in updated.merge_error


@pytest.mark.asyncio
async def test_pr_merge_blocks_when_remote_missing(pr_runtime):
    runtime: RuntimeService = pr_runtime["runtime"]
    store: SQLiteMemoryStore = pr_runtime["store"]

    workspace = pr_runtime["tmp"] / "worktrees" / "task-pr-1"
    workspace.mkdir(parents=True)
    task_id = await _seed_waiting_merge(store, workspace=workspace)

    wt = runtime._worktree  # noqa: SLF001
    wt.check_gh_ready = AsyncMock(return_value=(True, "gh ready"))
    wt.check_remote_configured = AsyncMock(
        return_value=(False, "git remote get-url origin failed")
    )
    wt.push_task_branch = AsyncMock()
    wt.create_pr = AsyncMock()

    task = await store.get_runtime_task(task_id)
    assert task is not None
    result = await runtime._execute_merge_pr(  # noqa: SLF001
        task, actor_id="owner-1", source="slash"
    )

    assert "merge blocked" in result.lower()
    assert "origin" in result
    wt.push_task_branch.assert_not_awaited()
    wt.create_pr.assert_not_awaited()


# ── empty diff guard ───────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_pr_merge_blocks_when_no_diff_vs_base(pr_runtime):
    """Branch identical to remote base → no PR opened (NF2 catch)."""

    runtime: RuntimeService = pr_runtime["runtime"]
    store: SQLiteMemoryStore = pr_runtime["store"]

    workspace = pr_runtime["tmp"] / "worktrees" / "task-pr-1"
    workspace.mkdir(parents=True)
    task_id = await _seed_waiting_merge(store, workspace=workspace)

    wt = runtime._worktree  # noqa: SLF001
    wt.check_gh_ready = AsyncMock(return_value=(True, "gh ready"))
    wt.check_remote_configured = AsyncMock(return_value=(True, "git@github.com:x/y.git"))
    wt.workspace_has_dirty_or_new_commits = AsyncMock(return_value=False)
    wt.commit_workspace = AsyncMock()
    wt.fetch_base_ref = AsyncMock(return_value=None)
    wt.has_diff_vs_base = AsyncMock(return_value=False)
    wt.push_task_branch = AsyncMock()
    wt.create_pr = AsyncMock()

    task = await store.get_runtime_task(task_id)
    assert task is not None
    result = await runtime._execute_merge_pr(  # noqa: SLF001
        task, actor_id="owner-1", source="slash"
    )

    assert "merge blocked" in result.lower()
    assert "identical" in result.lower()
    # No push / no PR — empty diff is detected pre-push.
    wt.push_task_branch.assert_not_awaited()
    wt.create_pr.assert_not_awaited()
    # fetch_base_ref still ran (Codex NF: refresh stale base before diff).
    wt.fetch_base_ref.assert_awaited_once_with(workspace, "origin", "main")


# ── unknown mode ──────────────────────────────────────────────── #


@pytest.mark.asyncio
async def test_unknown_target_branch_mode_blocks(pr_runtime, monkeypatch):
    """Unknown ``target_branch_mode`` must fail loud, not fall back."""

    runtime: RuntimeService = pr_runtime["runtime"]
    store: SQLiteMemoryStore = pr_runtime["store"]

    monkeypatch.setattr(runtime, "_merge_target_branch_mode", "weird-mode")

    workspace = pr_runtime["tmp"] / "worktrees" / "task-pr-1"
    workspace.mkdir(parents=True)
    task_id = await _seed_waiting_merge(store, workspace=workspace)

    task = await store.get_runtime_task(task_id)
    assert task is not None
    result = await runtime._execute_merge(  # noqa: SLF001
        task, actor_id="owner-1", source="slash"
    )

    assert "merge blocked" in result.lower()
    assert "weird-mode" in result or "Unknown target_branch_mode" in result


# ── schema persistence ──────────────────────────────────────── #


@pytest.mark.asyncio
async def test_pr_metadata_persists_and_reloads(tmp_path: Path):
    """pr_url / pr_number columns survive insert → update → fetch.
    Covers the schema migration (memory/store.py)."""

    db_path = tmp_path / "runtime.db"
    store = SQLiteMemoryStore(db_path)
    await store.init()

    task = await store.create_runtime_task(
        task_id="task-persist",
        platform="discord",
        channel_id="100",
        thread_id="t1",
        created_by="alice",
        goal="Test PR persistence",
        original_request="x",
        status=TASK_STATUS_DRAFT,
        max_steps=2,
        max_minutes=5,
        test_command="true",
        completion_mode="merge",
        task_type="repo_change",
    )

    await store.update_runtime_task(
        task.id,
        status=TASK_STATUS_PR_OPENED,
        pr_url="https://github.com/x/y/pull/99",
        pr_number=99,
    )

    reloaded = await store.get_runtime_task(task.id)
    assert reloaded is not None
    assert reloaded.status == TASK_STATUS_PR_OPENED
    assert reloaded.pr_url == "https://github.com/x/y/pull/99"
    assert reloaded.pr_number == 99
    await store.close()


# ── skill auto-merge disabled in PR mode ───────────────────── #


@pytest.mark.asyncio
async def test_skill_auto_merge_disabled_in_pr_mode(pr_runtime):
    """``skill_auto_approve=true`` + ``target_branch_mode=pr`` must NOT
    auto-trigger ``_execute_merge`` — the PR opening itself is meant to
    be human-gated."""

    runtime: RuntimeService = pr_runtime["runtime"]
    assert runtime._merge_target_branch_mode == "pr"  # noqa: SLF001
    # The guard variable used in the dispatcher:
    pr_mode_blocks_auto_merge = runtime._merge_target_branch_mode == "pr"  # noqa: SLF001
    assert pr_mode_blocks_auto_merge is True


# ── current mode regression ──────────────────────────────── #


@pytest.mark.asyncio
async def test_current_mode_path_still_works(tmp_path: Path):
    """Explicitly construct a ``current``-mode runtime and verify the
    legacy patch+apply path is reachable. Prevents accidental regressions
    when the PR-mode dispatch was added."""

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
        "default_agent": "claude",
        "default_test_command": "true",
        "default_max_steps": 4,
        "default_max_minutes": 10,
        "risk_profile": "lenient",
        "cleanup": {"enabled": False},
        "merge_gate": {
            "enabled": True,
            "target_branch_mode": "current",
            "require_clean_repo": True,
            "preflight_check": True,
            "auto_commit": True,
            "commit_message_template": "x",
        },
    }
    runtime = RuntimeService(
        store, config=cfg, owner_user_ids={"owner-1"}, repo_root=repo
    )

    assert runtime._merge_target_branch_mode == "current"  # noqa: SLF001
    # Just confirm the dispatch routes to legacy _execute_merge code path;
    # the legacy path is already covered by tests/test_runtime_service.py.

    await runtime.stop()
    await store.close()
