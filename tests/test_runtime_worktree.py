"""Covers WorktreeManager create/success/error paths with a real-but-isolated git repo."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
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
async def test_changed_files_returns_literal_paths_for_space_and_unicode(manager):
    """Regression: the non-z porcelain format C-quotes paths with spaces or
    non-ASCII (e.g. "\\344\\270\\255.txt"), breaking downstream path guards.
    -z mode emits paths literally."""
    workspace = await manager.ensure_worktree("task-quoted")
    (workspace / "with space.txt").write_text("x\n")
    (workspace / "中文文件.txt").write_text("y\n")
    files = await manager.changed_files(workspace)
    assert "with space.txt" in files
    assert "中文文件.txt" in files
    # No C-quoted artifacts left over.
    assert not any(f.startswith('"') for f in files)


@pytest.mark.asyncio
async def test_changed_files_rename_reports_new_path_only(manager):
    """In -z mode a rename entry carries the NEW path; the OLD path follows as
    the next NUL record and must be consumed, not reported as a change."""
    workspace = await manager.ensure_worktree("task-renamed")
    _git("mv", "README.md", "renamed doc.md", cwd=workspace)
    (workspace / "extra.txt").write_text("z\n")
    files = await manager.changed_files(workspace)
    assert "renamed doc.md" in files
    assert "README.md" not in files
    # The record after the rename pair must still be parsed correctly.
    assert "extra.txt" in files


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


def _commit_author(repo: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%an|%ae"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


@pytest.mark.asyncio
async def test_commit_injects_configured_git_identity(git_repo, tmp_path):
    # `-c user.*` flags override any ambient/global git config, so the author
    # assertion holds regardless of the test machine's git settings.
    mgr = WorktreeManager(
        repo_root=git_repo,
        worktree_root=tmp_path / "wt",
        git_identity={"name": "Custom Bot", "email": "custom@bot.test"},
    )
    (git_repo / "new.txt").write_text("x\n")
    commit = await mgr.commit_repo_changes("test commit")
    assert commit
    assert _commit_author(git_repo) == "Custom Bot|custom@bot.test"


@pytest.mark.asyncio
async def test_commit_uses_default_identity_when_unset(git_repo, tmp_path):
    mgr = WorktreeManager(repo_root=git_repo, worktree_root=tmp_path / "wt")
    (git_repo / "new.txt").write_text("x\n")
    await mgr.commit_repo_changes("c")
    assert _commit_author(git_repo) == "oh-my-agent|oh-my-agent@users.noreply.github.com"


@pytest.mark.asyncio
async def test_discard_repo_changes_restores_clean_tree(manager, git_repo):
    (git_repo / "README.md").write_text("modified\n")  # tracked change
    (git_repo / "untracked.txt").write_text("new\n")    # untracked file
    assert await manager.repo_is_clean() is False
    await manager.discard_repo_changes()
    assert await manager.repo_is_clean() is True
    assert (git_repo / "README.md").read_text() == "hello\n"  # tracked restored
    assert not (git_repo / "untracked.txt").exists()          # untracked removed


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


async def _assert_pid_gone(pid: int, attempts: int = 60) -> None:
    """Poll until ``pid`` no longer exists (psutil-free liveness check).

    ``os.kill(pid, 0)`` succeeds for zombies, so allow a short retry window
    for the reparented child to be reaped after the group kill.
    """
    for _ in range(attempts):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.1)
    pytest.fail(f"process {pid} is still alive")


@pytest.mark.asyncio
async def test_run_shell_timeout_kills_whole_process_group(manager):
    """Regression: a grandchild spawned by the shell inherits the output pipes;
    a bare proc.kill() left it alive and the post-kill communicate() hung until
    the grandchild exited. The group kill must reach it, so run_shell returns
    well within the grace window and the grandchild is dead."""
    workspace = await manager.ensure_worktree("task-group-kill")
    loop = asyncio.get_running_loop()
    start = loop.time()
    rc, stdout, _, timed_out = await asyncio.wait_for(
        manager.run_shell(
            workspace,
            "sleep 30 & echo started-$!; wait",
            timeout_seconds=0.3,
        ),
        timeout=8.0,
    )
    assert timed_out is True
    assert loop.time() - start < 7.0  # never waits out the 30s sleep
    # Group kill closes the pipes promptly, so buffered output survives.
    match = re.search(r"started-(\d+)", stdout)
    assert match is not None, f"expected child pid marker in stdout, got {stdout!r}"
    await _assert_pid_gone(int(match.group(1)))


@pytest.mark.asyncio
async def test_run_shell_should_cancel_returns_promptly_not_timed_out(manager):
    workspace = await manager.ensure_worktree("task-cancel-probe")
    calls: list[str] = []

    async def on_hb(elapsed: float) -> None:
        calls.append("heartbeat")

    async def probe() -> bool:
        calls.append("should_cancel")
        return True

    loop = asyncio.get_running_loop()
    start = loop.time()
    rc, _, _, timed_out = await asyncio.wait_for(
        manager.run_shell(
            workspace,
            "sleep 30",
            heartbeat_seconds=0.1,
            on_heartbeat=on_hb,
            should_cancel=probe,
        ),
        timeout=8.0,
    )
    assert timed_out is False
    assert rc != 0  # killed, not a clean exit
    assert loop.time() - start < 7.0
    # Contract: probed after on_heartbeat so progress is still recorded.
    assert calls[:2] == ["heartbeat", "should_cancel"]


@pytest.mark.asyncio
async def test_run_shell_should_cancel_exception_treated_as_false(manager):
    workspace = await manager.ensure_worktree("task-cancel-raise")

    async def broken() -> bool:
        raise RuntimeError("probe exploded")

    rc, stdout, _, timed_out = await manager.run_shell(
        workspace,
        "sleep 0.3; echo done",
        heartbeat_seconds=0.1,
        should_cancel=broken,
    )
    assert rc == 0
    assert "done" in stdout
    assert timed_out is False


@pytest.mark.asyncio
async def test_run_shell_cancellation_kills_subprocess(manager, tmp_path):
    """Regression: cancelling the task awaiting run_shell (runtime shutdown)
    used to leak the subprocess — asyncio.shield kept it running unsupervised.
    Cancellation must group-kill the process before CancelledError propagates."""
    workspace = await manager.ensure_worktree("task-await-cancel")
    pid_file = tmp_path / "pid.txt"
    task = asyncio.create_task(
        manager.run_shell(workspace, f"echo $$ > '{pid_file}'; sleep 30")
    )
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("subprocess never wrote its pid")
    pid = int(pid_file.read_text().strip())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=8.0)
    await _assert_pid_gone(pid)


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


def _task_branches(repo: Path) -> list[str]:
    out = subprocess.run(
        [
            "git", "-C", str(repo),
            "branch", "--list", "codex/task-*", "--format=%(refname:short)",
        ],
        capture_output=True, text=True, check=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_remove_worktree_delete_branch_removes_task_branch(manager, git_repo):
    """Non-PR janitor cleanup passes delete_branch=True: the per-task local
    branch must be deleted along with the worktree (regression: branches
    were never deleted → unbounded local ref accumulation)."""
    workspace = await manager.ensure_worktree("del-branch")
    assert "codex/task-del-branch" in _task_branches(git_repo)
    await manager.remove_worktree(workspace, delete_branch=True)
    assert not workspace.exists()
    assert "codex/task-del-branch" not in _task_branches(git_repo)


@pytest.mark.asyncio
async def test_remove_worktree_default_keeps_task_branch(manager, git_repo):
    """Default delete_branch=False keeps the branch — the PR_OPENED cleanup
    path relies on this: the local ref is the safety copy of the pushed
    branch until the human merges the PR."""
    workspace = await manager.ensure_worktree("keep-branch")
    await manager.remove_worktree(workspace)
    assert not workspace.exists()
    assert "codex/task-keep-branch" in _task_branches(git_repo)


@pytest.mark.asyncio
async def test_remove_worktree_delete_branch_tolerates_missing_branch(manager, git_repo):
    """delete_branch=True must swallow `git branch -D` failure when the
    branch is already gone (second cleanup pass, manual deletion, ...)."""
    workspace = await manager.ensure_worktree("gone-branch")
    await manager.remove_worktree(workspace, delete_branch=True)
    assert "codex/task-gone-branch" not in _task_branches(git_repo)
    # Second pass: workspace and branch both gone — must not raise.
    await manager.remove_worktree(workspace, delete_branch=True)


@pytest.mark.asyncio
async def test_remove_worktree_rmtree_fallback_for_unregistered_dir(manager, tmp_path):
    """Smoke test for the to_thread-offloaded rmtree fallback: a directory
    git doesn't know about (worktree remove fails) is still deleted."""
    fake = tmp_path / "worktrees" / "not-a-worktree"
    (fake / "nested").mkdir(parents=True)
    (fake / "nested" / "junk.txt").write_text("x\n")
    await manager.remove_worktree(fake)
    assert not fake.exists()


@pytest.mark.asyncio
async def test_run_git_raises_worktree_error_on_failure(manager, git_repo):
    with pytest.raises(WorktreeError):
        await manager._run_git("-C", str(git_repo), "rev-parse", "does-not-exist")


@pytest.mark.asyncio
async def test_workspace_has_dirty_true_for_dirty_tree(manager):
    workspace = await manager.ensure_worktree("dirty-check")
    (workspace / "scratch.txt").write_text("x\n")
    assert await manager.workspace_has_dirty_or_new_commits(workspace) is True


@pytest.mark.asyncio
async def test_workspace_has_dirty_false_for_clean_tree_without_upstream(manager):
    """Regression: a previous version also ran `rev-list HEAD..HEAD@{u}`,
    which counted the WRONG direction and — since the branch has no upstream
    before first push — always raised WorktreeError that was silently
    swallowed. A clean tree with no upstream must be a plain False."""
    workspace = await manager.ensure_worktree("clean-check")
    assert await manager.workspace_has_dirty_or_new_commits(workspace) is False


@pytest.mark.asyncio
async def test_prune_worktrees_scoped_to_own_root(manager, git_repo, tmp_path):
    """Regression: a global `git worktree prune` deletes the admin entry of any
    worktree whose working tree is invisible to this process — which nukes
    host-side worktrees when the bot runs in Docker with the repo bind-mounted.
    prune_worktrees() must only remove orphans under its own worktree_root and
    leave foreign worktrees alone."""
    admin_root = git_repo / ".git" / "worktrees"

    # Live own worktree — must survive.
    live = await manager.ensure_worktree("task-live")
    assert live.exists()
    assert (admin_root / "task-live").is_dir()

    # Orphaned own worktree (working tree removed) — must be pruned.
    orphan = await manager.ensure_worktree("task-orphan")
    orphan_admin = admin_root / "task-orphan"
    assert orphan_admin.is_dir()
    shutil.rmtree(orphan)

    # Foreign worktree: admin entry pointing OUTSIDE worktree_root, working tree
    # absent (mimics a host worktree git can't see from inside a container).
    foreign_admin = admin_root / "foreign-host"
    foreign_admin.mkdir()
    (foreign_admin / "gitdir").write_text(
        str(tmp_path / "elsewhere" / "host-wt" / ".git") + "\n", encoding="utf-8"
    )

    await manager.prune_worktrees()

    assert (admin_root / "task-live").is_dir()   # live own worktree kept
    assert not orphan_admin.exists()             # orphaned own worktree pruned
    assert foreign_admin.is_dir()                # foreign worktree preserved


@pytest.mark.asyncio
async def test_prune_skips_corrupt_gitdir_and_continues_sweep(manager, git_repo):
    """A corrupt (non-UTF-8) `gitdir` file used to raise UnicodeDecodeError which
    is not OSError, aborting the whole sweep mid-iteration. Catch it and keep
    going so other orphans still get pruned that cycle."""
    import os as _os

    admin_root = git_repo / ".git" / "worktrees"

    # Pre-existing live + orphan we want to prune normally.
    live = await manager.ensure_worktree("task-live")
    assert live.exists()
    orphan = await manager.ensure_worktree("task-orphan")
    shutil.rmtree(orphan)

    # Plant a corrupt admin entry whose `gitdir` is non-UTF-8. Use a sortable
    # name that lands BEFORE "task-orphan" so the sweep visits it first; that's
    # the worst case for an aborted iteration.
    corrupt = admin_root / "a-corrupt-entry"
    corrupt.mkdir()
    (corrupt / "gitdir").write_bytes(b"\xff\xfe\x00\xff\n")

    # Should not raise. Force a deterministic walk order if possible by sorting
    # via _os.listdir → adminroot.iterdir() ordering is FS-dependent; this test
    # asserts on the END state regardless of order.
    del _os  # only imported to document intent
    await manager.prune_worktrees()

    assert (admin_root / "task-live").is_dir()         # live preserved
    assert corrupt.is_dir()                             # corrupt skipped, not removed
    assert not (admin_root / "task-orphan").exists()    # sweep continued past corrupt


@pytest.mark.asyncio
async def test_prune_respects_git_worktree_lock_marker(manager, git_repo):
    """`git worktree lock` writes a ``locked`` file under the admin dir; the
    real ``git worktree prune`` honors it. Scoped prune must too — operators
    may lock a runtime worktree mid-debug to stop the janitor from wiping it
    when the working tree disappears."""
    admin_root = git_repo / ".git" / "worktrees"
    ws = await manager.ensure_worktree("task-locked")
    locked_admin = admin_root / "task-locked"
    (locked_admin / "locked").write_text("user-locked\n", encoding="utf-8")
    shutil.rmtree(ws)  # working tree gone → would normally be pruned

    await manager.prune_worktrees()

    assert locked_admin.is_dir()                    # locked admin preserved
    assert (locked_admin / "locked").exists()


@pytest.mark.asyncio
async def test_prune_handles_relative_gitdir_against_admin_dir(manager, git_repo, tmp_path):
    """Git ≥2.40 with ``worktree.useRelativePaths=true`` writes ``gitdir`` as a
    path relative to the admin directory. Resolve against ``admin`` (not the
    process CWD), or scope detection mis-classifies."""
    import os as _os

    admin_root = git_repo / ".git" / "worktrees"
    ws = await manager.ensure_worktree("task-rel")
    rel_admin = admin_root / "task-rel"

    # Rewrite gitdir as relative path (admin → <ws>/.git).
    rel_target = _os.path.relpath(str(ws / ".git"), str(rel_admin))
    (rel_admin / "gitdir").write_text(rel_target + "\n", encoding="utf-8")

    # Working tree still exists → must NOT be pruned even though path is relative.
    await manager.prune_worktrees()
    assert rel_admin.is_dir()

    # Now orphan it → relative-path resolution must still classify it as ours
    # and prune.
    shutil.rmtree(ws)
    await manager.prune_worktrees()
    assert not rel_admin.exists()


@pytest.mark.asyncio
async def test_prune_skips_empty_or_missing_gitdir(manager, git_repo):
    """Admin entries with empty or missing gitdir (partial-write recovery,
    half-initialised git worktree add) must not crash and must not be deleted."""
    admin_root = git_repo / ".git" / "worktrees"
    live = await manager.ensure_worktree("task-live2")
    assert live.exists()

    empty_admin = admin_root / "no-gitdir"
    empty_admin.mkdir()
    # No gitdir file at all.

    blank_admin = admin_root / "blank-gitdir"
    blank_admin.mkdir()
    (blank_admin / "gitdir").write_text("\n", encoding="utf-8")

    await manager.prune_worktrees()

    assert (admin_root / "task-live2").is_dir()
    assert empty_admin.is_dir()
    assert blank_admin.is_dir()
