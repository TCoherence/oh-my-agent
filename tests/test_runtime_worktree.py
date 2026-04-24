"""Covers WorktreeManager create/success/error paths with a real-but-isolated git repo."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from oh_my_agent.runtime.worktree import WorktreeError, WorktreeManager


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": "/usr/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(cwd),
        },
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    (repo / "README.md").write_text("hello\n")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "initial", cwd=repo)
    return repo


@pytest.fixture
def manager(git_repo: Path, tmp_path: Path) -> WorktreeManager:
    worktree_root = tmp_path / "worktrees"
    return WorktreeManager(repo_root=git_repo, worktree_root=worktree_root)


@pytest.mark.asyncio
async def test_ensure_worktree_creates_new_workspace(manager, git_repo):
    workspace = await manager.ensure_worktree("abc123")
    assert workspace.exists()
    assert (workspace / "README.md").read_text() == "hello\n"


@pytest.mark.asyncio
async def test_ensure_worktree_returns_existing_workspace(manager):
    ws1 = await manager.ensure_worktree("abc123")
    ws2 = await manager.ensure_worktree("abc123")
    assert ws1 == ws2


@pytest.mark.asyncio
async def test_changed_files_detects_new_and_modified(manager):
    workspace = await manager.ensure_worktree("task-1")
    (workspace / "new.txt").write_text("added")
    (workspace / "README.md").write_text("modified\n")
    files = await manager.changed_files(workspace)
    assert "new.txt" in files
    assert "README.md" in files


@pytest.mark.asyncio
async def test_repo_is_clean_true_on_fresh_repo(manager):
    assert await manager.repo_is_clean() is True


@pytest.mark.asyncio
async def test_repo_is_clean_false_when_files_modified(manager, git_repo):
    (git_repo / "README.md").write_text("dirty\n")
    assert await manager.repo_is_clean() is False


@pytest.mark.asyncio
async def test_run_shell_returns_stdout_and_exit_zero(manager):
    workspace = await manager.ensure_worktree("task-shell")
    rc, stdout, stderr, timed_out = await manager.run_shell(workspace, "echo hello-world")
    assert rc == 0
    assert "hello-world" in stdout
    assert timed_out is False


@pytest.mark.asyncio
async def test_run_shell_surfaces_nonzero_exit(manager):
    workspace = await manager.ensure_worktree("task-fail")
    rc, _, _, timed_out = await manager.run_shell(workspace, "exit 7")
    assert rc == 7
    assert timed_out is False


@pytest.mark.asyncio
async def test_run_shell_times_out(manager):
    workspace = await manager.ensure_worktree("task-timeout")
    rc, _, _, timed_out = await manager.run_shell(
        workspace, "sleep 5", timeout_seconds=0.2
    )
    assert timed_out is True


@pytest.mark.asyncio
async def test_run_shell_heartbeat_fires(manager):
    workspace = await manager.ensure_worktree("task-hb")
    beats: list[float] = []

    async def on_hb(elapsed: float) -> None:
        beats.append(elapsed)

    await manager.run_shell(
        workspace,
        "sleep 0.3",
        heartbeat_seconds=0.1,
        on_heartbeat=on_hb,
    )
    assert len(beats) >= 1


@pytest.mark.asyncio
async def test_create_patch_and_apply_check(manager):
    workspace = await manager.ensure_worktree("task-patch")
    (workspace / "added.txt").write_text("content\n")
    patch = await manager.create_patch(workspace)
    assert "added.txt" in patch
    # apply_check against the repo_root should succeed (file does not exist there yet).
    await manager.apply_patch_check(patch)


@pytest.mark.asyncio
async def test_list_workspace_changes_returns_name_status(manager):
    workspace = await manager.ensure_worktree("task-changes")
    (workspace / "added.txt").write_text("new\n")
    changes = await manager.list_workspace_changes(workspace)
    assert any("added.txt" in line for line in changes)


@pytest.mark.asyncio
async def test_remove_worktree_cleans_up(manager):
    workspace = await manager.ensure_worktree("task-rm")
    assert workspace.exists()
    await manager.remove_worktree(workspace)
    assert not workspace.exists()


@pytest.mark.asyncio
async def test_remove_worktree_missing_is_noop(manager, tmp_path):
    ghost = tmp_path / "does-not-exist"
    # Should silently succeed.
    await manager.remove_worktree(ghost)


@pytest.mark.asyncio
async def test_run_git_raises_worktree_error_on_failure(manager, git_repo):
    with pytest.raises(WorktreeError):
        await manager._run_git("-C", str(git_repo), "rev-parse", "does-not-exist")
