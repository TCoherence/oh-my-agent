from __future__ import annotations

import asyncio
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

    async def run_shell(self, workspace: Path, command: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")

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
