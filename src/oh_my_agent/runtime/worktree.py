from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


class WorktreeError(RuntimeError):
    pass


class WorktreeManager:
    def __init__(self, repo_root: Path, worktree_root: Path) -> None:
        self._repo_root = repo_root
        self._worktree_root = worktree_root
        self._worktree_root.mkdir(parents=True, exist_ok=True)

    async def ensure_worktree(self, task_id: str) -> Path:
        workspace = self._worktree_root / task_id
        if workspace.exists():
            return workspace

        branch = f"codex/task-{task_id}"
        await self._run_git("worktree", "add", "-B", branch, str(workspace), "HEAD")
        return workspace

    async def changed_files(self, workspace: Path) -> list[str]:
        out = await self._run_git("-C", str(workspace), "status", "--porcelain")
        files: list[str] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            # Format: XY <path> or XY <old> -> <new>
            raw_path = line[3:].strip()
            if " -> " in raw_path:
                raw_path = raw_path.split(" -> ", 1)[1]
            files.append(raw_path)
        return files

    async def run_shell(
        self,
        workspace: Path,
        command: str,
        *,
        timeout_seconds: float | None = None,
        heartbeat_seconds: float | None = None,
        on_heartbeat=None,
    ) -> tuple[int, str, str, bool]:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        communicate_task = asyncio.create_task(proc.communicate())
        started = asyncio.get_running_loop().time()
        interval = heartbeat_seconds if heartbeat_seconds and heartbeat_seconds > 0 else None

        while True:
            now = asyncio.get_running_loop().time()
            wait_timeout = interval
            if timeout_seconds is not None:
                remaining = float(timeout_seconds) - (now - started)
                if remaining <= 0:
                    proc.kill()
                    stdout, stderr = await communicate_task
                    return (
                        proc.returncode,
                        stdout.decode(errors="replace"),
                        stderr.decode(errors="replace"),
                        True,
                    )
                wait_timeout = remaining if wait_timeout is None else min(wait_timeout, remaining)

            try:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.shield(communicate_task),
                    timeout=wait_timeout,
                )
                return (
                    proc.returncode,
                    stdout.decode(errors="replace"),
                    stderr.decode(errors="replace"),
                    False,
                )
            except asyncio.TimeoutError:
                elapsed = asyncio.get_running_loop().time() - started
                if timeout_seconds is not None and elapsed >= float(timeout_seconds):
                    proc.kill()
                    stdout, stderr = await communicate_task
                    return (
                        proc.returncode,
                        stdout.decode(errors="replace"),
                        stderr.decode(errors="replace"),
                        True,
                    )
                if on_heartbeat is not None:
                    await on_heartbeat(elapsed)

    async def repo_is_clean(self) -> bool:
        out = await self._run_git("-C", str(self._repo_root), "status", "--porcelain")
        return not bool(out.strip())

    async def create_patch(self, workspace: Path) -> str:
        await self._run_git("-C", str(workspace), "add", "-A")
        return await self._run_git("-C", str(workspace), "diff", "--cached", "--binary", "HEAD")

    async def apply_patch_check(self, patch: str) -> None:
        await self._run_git_with_input(
            patch,
            "-C",
            str(self._repo_root),
            "apply",
            "--check",
            "--whitespace=nowarn",
            "-",
        )

    async def apply_patch(self, patch: str) -> None:
        await self._run_git_with_input(
            patch,
            "-C",
            str(self._repo_root),
            "apply",
            "--whitespace=nowarn",
            "-",
        )

    async def commit_repo_changes(self, message: str) -> str:
        await self._run_git("-C", str(self._repo_root), "add", "-A")
        await self._run_git("-C", str(self._repo_root), "commit", "-m", message)
        commit_hash = await self._run_git("-C", str(self._repo_root), "rev-parse", "HEAD")
        return commit_hash.strip()

    async def list_workspace_changes(self, workspace: Path, *, limit: int = 200) -> list[str]:
        await self._run_git("-C", str(workspace), "add", "-A")
        out = await self._run_git("-C", str(workspace), "diff", "--cached", "--name-status", "HEAD")
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        return lines[:limit]

    async def remove_worktree(self, workspace: Path) -> None:
        if not workspace.exists():
            return
        try:
            await self._run_git("worktree", "remove", "--force", str(workspace))
        except WorktreeError:
            # Fall back to filesystem cleanup if git metadata is already stale.
            shutil.rmtree(workspace, ignore_errors=True)

    async def prune_worktrees(self) -> None:
        await self._run_git("worktree", "prune")

    async def _run_git(self, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(self._repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise WorktreeError(
                f"git {' '.join(args)} failed ({proc.returncode}): "
                f"{stderr.decode(errors='replace').strip()}"
            )
        return stdout.decode(errors="replace")

    async def _run_git_with_input(self, stdin: str, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(self._repo_root),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(stdin.encode("utf-8"))
        if proc.returncode != 0:
            raise WorktreeError(
                f"git {' '.join(args)} failed ({proc.returncode}): "
                f"{stderr.decode(errors='replace').strip()}"
            )
        return stdout.decode(errors="replace")
