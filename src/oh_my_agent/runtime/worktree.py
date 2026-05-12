from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path


class WorktreeError(RuntimeError):
    pass


class GhError(RuntimeError):
    """Raised when the ``gh`` CLI is missing, unauthenticated, or
    returns a non-zero exit code. Distinct from :class:`WorktreeError`
    so the service layer can format actionable user-facing messages
    (e.g. ``"gh auth status failed: ..."``) without swallowing them
    as generic git errors.
    """


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
                        proc.returncode if proc.returncode is not None else -1,
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
                    proc.returncode if proc.returncode is not None else -1,
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
                        proc.returncode if proc.returncode is not None else -1,
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

    # ── PR-based merge flow (target_branch_mode=pr) ───────────────────── #

    async def commit_workspace(self, workspace: Path, message: str) -> str:
        """Commit dirty changes inside the task worktree branch.

        Distinct from :meth:`commit_repo_changes` which commits the
        applied patch in the *main* repo. The PR-mode flow needs the
        commit to live on the worktree-specific branch (``codex/task-<id>``)
        so we can push that branch directly to the remote.

        Returns the commit hash.
        """
        await self._run_git("-C", str(workspace), "add", "-A")
        await self._run_git("-C", str(workspace), "commit", "-m", message)
        commit_hash = await self._run_git("-C", str(workspace), "rev-parse", "HEAD")
        return commit_hash.strip()

    async def workspace_has_dirty_or_new_commits(self, workspace: Path) -> bool:
        """True if ``git status --porcelain`` has any changes OR the
        worktree branch is ahead of HEAD~ (i.e. the agent already
        committed something).

        Used by the PR merge path to decide whether ``commit_workspace``
        is needed before push. Be careful with the "ahead of HEAD~"
        check: the worktree branch was created from HEAD at task start,
        but HEAD itself isn't moved during the task run, so commit
        ancestry from the worktree's POV is the right signal.
        """
        # Dirty working tree.
        porcelain = await self._run_git("-C", str(workspace), "status", "--porcelain")
        if porcelain.strip():
            return True
        # New commits beyond the original task base. The branch was
        # created with ``-B <branch> HEAD`` at task start, so anything
        # past the initial HEAD is bot work that should ship in the PR.
        # We check against the worktree's first-parent ancestry via
        # rev-list --count against HEAD (which the worktree shares with
        # the main repo at task-creation time).
        try:
            out = await self._run_git(
                "-C", str(workspace), "rev-list", "--count", "HEAD..HEAD@{u}"
            )
            if int(out.strip() or "0") > 0:
                return True
        except WorktreeError:
            # Branch may have no upstream yet (never pushed) — treat as
            # "no remote ahead-count to report", fall through.
            pass
        return False

    async def fetch_base_ref(
        self,
        workspace: Path,
        remote: str,
        base_branch: str,
    ) -> None:
        """``git fetch <remote> <base_branch>`` — required before the
        3-dot diff check so stale local base refs don't make us think a
        non-empty branch is empty.

        Codex round-3 NF catch: without this, a stale ``origin/main``
        ref (typical after a long task run while teammates merged) would
        let the empty-diff guard pass incorrectly.
        """
        await self._run_git(
            "-C", str(workspace), "fetch", remote, base_branch
        )

    async def has_diff_vs_base(
        self,
        workspace: Path,
        remote: str,
        base_branch: str,
    ) -> bool:
        """True iff HEAD has any 3-dot diff vs ``<remote>/<base_branch>``.

        3-dot diff (``A...B``) compares ``B`` against the merge-base of
        ``A`` and ``B`` — so this is "what would the PR introduce",
        not "what is different right now". An empty 3-dot diff means
        the branch is fully merged into base (or never had any
        changes) and there's nothing to PR.

        Call :meth:`fetch_base_ref` first to refresh the remote-tracking
        ref.
        """
        # ``git diff --quiet`` exits 0 = no diff, 1 = diff present. We
        # call _run_git which raises on nonzero, so catch the diff-
        # present case explicitly via the exit code path.
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(workspace),
            "diff",
            "--quiet",
            f"{remote}/{base_branch}...HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        # exit 0 → no diff, exit 1 → diff present. Anything else =
        # an actual git failure (bad ref, etc.) — surface that.
        if proc.returncode == 0:
            return False
        if proc.returncode == 1:
            return True
        raise WorktreeError(
            f"git diff --quiet exited unexpectedly ({proc.returncode}) "
            f"comparing {remote}/{base_branch}...HEAD"
        )

    async def push_task_branch(
        self,
        workspace: Path,
        branch: str,
        remote: str,
    ) -> None:
        """``git push -u <remote> <branch>`` from inside the worktree.

        ``-u`` sets the upstream so subsequent ``git status`` from the
        worktree (e.g. for diagnostic purposes) shows ahead/behind.
        Force-push is intentionally NOT used — if the branch already
        exists on the remote and has diverged, fail loud so the operator
        sees the conflict instead of silently overwriting.
        """
        await self._run_git("-C", str(workspace), "push", "-u", remote, branch)

    async def check_remote_configured(
        self,
        remote: str,
    ) -> tuple[bool, str]:
        """Return ``(configured, url_or_reason)``.

        Used by the PR merge path's preflight. A False return must
        produce a clear ``MERGE_BLOCKED`` message — never silently
        fall back to ``current`` mode (Codex round-1 NF5 catch).
        """
        try:
            url = await self._run_git(
                "-C", str(self._repo_root), "remote", "get-url", remote
            )
        except WorktreeError as exc:
            return False, f"git remote get-url {remote} failed: {exc}"
        url = url.strip()
        if not url:
            return False, f"git remote '{remote}' has empty URL"
        return True, url

    async def check_gh_ready(self) -> tuple[bool, str]:
        """Return ``(ready, reason)``. Probes ``gh --version`` and
        ``gh auth status``.

        Note: ``gh auth status`` writes to stderr by design (not stdout),
        and exit 0 = authenticated. We check exit code only; the actual
        stdout/stderr noise is suppressed in the success path.
        """
        if shutil.which("gh") is None:
            return False, "gh CLI not on PATH (install: https://cli.github.com)"
        # gh --version is a cheap binary-functional check.
        try:
            await self._run_subprocess("gh", "--version")
        except WorktreeError as exc:
            return False, f"gh --version failed: {exc}"
        # gh auth status — exit 0 means logged in.
        try:
            await self._run_subprocess("gh", "auth", "status")
        except WorktreeError as exc:
            return (
                False,
                f"gh not authenticated: {exc}. Run `gh auth login` (note that "
                "the bot's environment may have different gh auth state than your shell)",
            )
        return True, "gh ready"

    async def create_pr(
        self,
        workspace: Path,
        *,
        base: str,
        head: str,
        title: str,
        body: str,
        draft: bool,
    ) -> tuple[str, int]:
        """Run ``gh pr create --json url,number`` and return ``(url, number)``.

        ``head`` is just the branch name (gh resolves the current
        repo's owner automatically). ``base`` is the target branch
        (``main``/``master``/...). ``draft=True`` adds ``--draft``.

        Raises :class:`GhError` on any non-zero exit so callers can
        translate to ``MERGE_BLOCKED`` with a user-readable reason.
        """
        args = [
            "gh",
            "pr",
            "create",
            "--base",
            base,
            "--head",
            head,
            "--title",
            title,
            "--body-file",
            "-",
        ]
        if draft:
            args.append("--draft")
        # gh pr create prints the URL on success when --json is used
        # with --jq, but we want both url + number. Use --json url,number
        # and parse JSON.
        # However --json is for `gh pr list` / `gh pr view`; for `gh pr
        # create` the output IS the URL. So we create first, then look
        # up via `gh pr view` to get the number.
        out_url = await self._run_gh_with_stdin(body, *args, cwd=workspace)
        url = out_url.strip().splitlines()[-1] if out_url.strip() else ""
        if not url.startswith("http"):
            raise GhError(f"gh pr create did not return a URL: {out_url!r}")
        # Fetch number via gh pr view <branch> --json number,url.
        # ``_run_subprocess`` raises ``WorktreeError`` on non-zero exit;
        # wrap that as ``GhError`` so callers can keep a single
        # exception type for the whole gh interaction (callers only
        # need ``except GhError`` rather than tracking both classes).
        try:
            view_out = await self._run_subprocess(
                "gh",
                "pr",
                "view",
                head,
                "--json",
                "number,url",
                cwd=str(workspace),
            )
        except WorktreeError as exc:
            raise GhError(f"gh pr view {head} failed: {exc}") from exc
        try:
            data = json.loads(view_out)
        except json.JSONDecodeError as exc:
            raise GhError(f"gh pr view returned invalid JSON: {view_out!r}") from exc
        number = int(data.get("number", 0))
        if number <= 0:
            raise GhError(f"gh pr view returned invalid number: {data!r}")
        return url, number

    async def _run_subprocess(self, *args: str, cwd: str | None = None) -> str:
        """Generic subprocess helper for non-git binaries (gh, etc.).
        Mirrors :meth:`_run_git` error-raising semantics.
        """
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd or str(self._repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise WorktreeError(
                f"{args[0]} {' '.join(args[1:])} failed ({proc.returncode}): "
                f"{stderr.decode(errors='replace').strip()}"
            )
        return stdout.decode(errors="replace")

    async def _run_gh_with_stdin(self, stdin: str, *args: str, cwd: Path) -> str:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(stdin.encode("utf-8"))
        if proc.returncode != 0:
            raise GhError(
                f"gh {' '.join(args[1:])} failed ({proc.returncode}): "
                f"{stderr.decode(errors='replace').strip()}"
            )
        return stdout.decode(errors="replace")

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
