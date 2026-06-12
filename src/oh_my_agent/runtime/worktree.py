from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# Bounded grace window for draining communicate() after a kill. A grandchild
# that escaped the kill (or a wedged pipe) must never block a runtime worker
# forever — after this window we abandon the pipes and return what we have.
_KILL_GRACE_SECONDS = 5.0


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
    def __init__(
        self,
        repo_root: Path,
        worktree_root: Path,
        git_identity: dict[str, str] | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._worktree_root = worktree_root
        self._worktree_root.mkdir(parents=True, exist_ok=True)
        # Default identity so `git commit` never fails with "Author identity
        # unknown" in environments (e.g. Docker) that don't configure git.
        ident = git_identity or {}
        self._git_name = str(ident.get("name") or "oh-my-agent")
        self._git_email = str(ident.get("email") or "oh-my-agent@users.noreply.github.com")

    def _commit_identity_args(self) -> list[str]:
        """`git -c user.name=… -c user.email=…` flags for commit invocations."""
        return [
            "-c", f"user.name={self._git_name}",
            "-c", f"user.email={self._git_email}",
        ]

    async def ensure_worktree(self, task_id: str) -> Path:
        workspace = self._worktree_root / task_id
        if workspace.exists():
            return workspace

        branch = f"codex/task-{task_id}"
        await self._run_git("worktree", "add", "-B", branch, str(workspace), "HEAD")
        return workspace

    async def changed_files(self, workspace: Path) -> list[str]:
        # ``-z``: NUL-separated records with paths emitted literally — the
        # default format C-quotes paths containing spaces / non-ASCII
        # (e.g. ``"\344\270\255.txt"``), which broke downstream path guards.
        out = await self._run_git("-C", str(workspace), "status", "--porcelain", "-z")
        files: list[str] = []
        records = iter(out.split("\0"))
        for record in records:
            if not record:
                continue
            # Record format: "XY <path>". For rename/copy entries the entry
            # path is the NEW path; the ORIGINAL path follows as the next
            # NUL-separated record (no "old -> new" in -z mode).
            status, path = record[:2], record[3:]
            files.append(path)
            if "R" in status or "C" in status:
                next(records, None)
        return files

    @staticmethod
    def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
        """SIGKILL the subprocess's whole process group.

        The subprocess is spawned with ``start_new_session=True`` so its pid
        doubles as the pgid. A bare ``proc.kill()`` only hits the shell —
        grandchildren survive holding the stdout/stderr pipes open, which
        makes the post-kill ``communicate()`` hang until they exit.
        """
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            # Already exited in the check/kill race window.
            return
        except OSError:
            pass
        try:
            proc.kill()
        except ProcessLookupError:
            pass

    async def _kill_and_drain(
        self,
        proc: asyncio.subprocess.Process,
        communicate_task: asyncio.Task[tuple[bytes, bytes]],
    ) -> tuple[bytes, bytes]:
        """Group-kill, then drain ``communicate()`` within a bounded grace
        window. Returns whatever output was captured — empty on a wedged
        pipe, so the caller never blocks forever.
        """
        self._kill_process_group(proc)
        try:
            return await asyncio.wait_for(communicate_task, timeout=_KILL_GRACE_SECONDS)
        except asyncio.TimeoutError:
            # wait_for already cancelled communicate_task; abandon the pipes.
            return b"", b""

    @staticmethod
    async def _check_should_cancel(should_cancel: Callable[[], Awaitable[bool]]) -> bool:
        try:
            return bool(await should_cancel())
        except Exception:
            # A broken cancel probe must not take the shell run down with it.
            logger.exception("run_shell should_cancel probe raised; treating as False")
            return False

    async def run_shell(
        self,
        workspace: Path,
        command: str,
        *,
        timeout_seconds: float | None = None,
        heartbeat_seconds: float | None = None,
        on_heartbeat: Callable[[float], Awaitable[None]] | None = None,
        should_cancel: Callable[[], Awaitable[bool]] | None = None,
    ) -> tuple[int, str, str, bool]:
        """Run ``command`` in ``workspace``; returns
        ``(returncode, stdout, stderr, timed_out)``.

        ``should_cancel`` is polled once per heartbeat wakeup (after
        ``on_heartbeat``, so progress is still recorded); when it returns
        True the process group is killed and the normal 4-tuple is returned
        with ``timed_out=False``. Note it is only ever polled when a wakeup
        exists, i.e. ``heartbeat_seconds`` and/or ``timeout_seconds`` is set.
        """
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        communicate_task = asyncio.create_task(proc.communicate())
        started = asyncio.get_running_loop().time()
        interval = heartbeat_seconds if heartbeat_seconds and heartbeat_seconds > 0 else None

        try:
            while True:
                now = asyncio.get_running_loop().time()
                wait_timeout = interval
                if timeout_seconds is not None:
                    remaining = float(timeout_seconds) - (now - started)
                    if remaining <= 0:
                        stdout, stderr = await self._kill_and_drain(proc, communicate_task)
                        return (
                            proc.returncode if proc.returncode is not None else -1,
                            stdout.decode(errors="replace"),
                            stderr.decode(errors="replace"),
                            True,
                        )
                    wait_timeout = (
                        remaining if wait_timeout is None else min(wait_timeout, remaining)
                    )

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
                        stdout, stderr = await self._kill_and_drain(proc, communicate_task)
                        return (
                            proc.returncode if proc.returncode is not None else -1,
                            stdout.decode(errors="replace"),
                            stderr.decode(errors="replace"),
                            True,
                        )
                    if on_heartbeat is not None:
                        await on_heartbeat(elapsed)
                    if should_cancel is not None and await self._check_should_cancel(
                        should_cancel
                    ):
                        stdout, stderr = await self._kill_and_drain(proc, communicate_task)
                        return (
                            proc.returncode if proc.returncode is not None else -1,
                            stdout.decode(errors="replace"),
                            stderr.decode(errors="replace"),
                            False,
                        )
        except asyncio.CancelledError:
            # Worker cancellation (e.g. runtime shutdown) lands here: the
            # shield() above keeps the subprocess alive unsupervised, so kill
            # the group and bound the drain before propagating.
            self._kill_process_group(proc)
            communicate_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(communicate_task, timeout=_KILL_GRACE_SECONDS)
            raise

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
        await self._run_git(
            "-C", str(self._repo_root), *self._commit_identity_args(), "commit", "-m", message
        )
        commit_hash = await self._run_git("-C", str(self._repo_root), "rev-parse", "HEAD")
        return commit_hash.strip()

    async def discard_repo_changes(self) -> None:
        """Restore the main repo working tree to a clean ``HEAD`` — drops a
        half-applied current-mode patch (tracked + untracked) so a failed merge
        can't poison /repo. Caller must only invoke this when the repo was
        verified clean BEFORE the patch was applied (no user edits to lose).
        """
        await self._run_git("-C", str(self._repo_root), "checkout", "--", ".")
        await self._run_git("-C", str(self._repo_root), "clean", "-fd")

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
        await self._run_git(
            "-C", str(workspace), *self._commit_identity_args(), "commit", "-m", message
        )
        commit_hash = await self._run_git("-C", str(workspace), "rev-parse", "HEAD")
        return commit_hash.strip()

    async def workspace_has_dirty_or_new_commits(self, workspace: Path) -> bool:
        """True if ``git status --porcelain`` shows uncommitted changes.

        Used by the PR merge path to decide whether ``commit_workspace``
        is needed before push. Committed-but-unpushed work is deliberately
        NOT detected here: the caller (``_execute_merge_pr``) follows the
        commit step with :meth:`fetch_base_ref` + :meth:`has_diff_vs_base`
        (3-dot diff vs the fetched remote base), which is the authoritative
        "does the PR introduce anything" gate and already covers commits
        the agent made on the worktree branch. A previous version also ran
        ``rev-list --count HEAD..HEAD@{u}`` here, but the branch has no
        upstream before its first push, so that check always raised and
        was swallowed — dead logic, now removed.
        """
        porcelain = await self._run_git("-C", str(workspace), "status", "--porcelain")
        return bool(porcelain.strip())

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

    async def remove_worktree(self, workspace: Path, *, delete_branch: bool = False) -> None:
        if workspace.exists():
            try:
                await self._run_git("worktree", "remove", "--force", str(workspace))
            except WorktreeError:
                # Fall back to filesystem cleanup if git metadata is already
                # stale. Offloaded: rmtree of a full repo checkout can take
                # seconds and must not block the event loop.
                await asyncio.to_thread(shutil.rmtree, workspace, ignore_errors=True)
        if delete_branch:
            # The workspace directory name IS the task id (see
            # ensure_worktree), so the per-task branch is derivable even
            # after the directory itself is gone. Attempted regardless of
            # workspace existence so a manually-deleted workspace still gets
            # its local ref cleaned up.
            branch = f"codex/task-{workspace.name}"
            try:
                await self._run_git("branch", "-D", branch)
            except WorktreeError:
                # Branch may not exist, or may still be registered as checked
                # out if the rmtree fallback left a stale admin entry —
                # cleanup stays best-effort.
                pass

    async def prune_worktrees(self) -> None:
        """Remove orphaned worktree admin entries — but ONLY for worktrees under
        our own ``worktree_root``.

        ``git worktree prune`` is deliberately avoided: it is global and deletes
        the admin entry of EVERY registered worktree whose working tree is
        missing *from this process's filesystem view*. When the bot runs in
        Docker with the repo bind-mounted, host-side worktrees (e.g. Claude Code
        sessions under ``/Users/...``) are invisible inside the container and get
        silently pruned, dangling the host's ``.git`` pointer. We instead walk
        ``<git-common-dir>/worktrees`` ourselves and prune only entries whose
        working tree lives under ``worktree_root`` and no longer exists; anything
        we cannot positively classify as ours is left untouched.
        """
        try:
            common_raw = (await self._run_git("rev-parse", "--git-common-dir")).strip()
        except WorktreeError:
            return
        if not common_raw:
            return
        common_dir = Path(common_raw)
        if not common_dir.is_absolute():
            common_dir = (self._repo_root / common_dir).resolve()
        admin_root = common_dir / "worktrees"
        if not admin_root.is_dir():
            return
        try:
            own_root = self._worktree_root.resolve()
        except OSError:
            own_root = self._worktree_root
        for admin in admin_root.iterdir():
            # Respect `git worktree lock`: an admin with a ``locked`` marker file
            # is deliberately protected by the operator (or a future code path),
            # and `git worktree prune` itself honors it. Skip silently.
            if (admin / "locked").exists():
                continue
            gitdir_file = admin / "gitdir"
            try:
                # ``gitdir`` points at the worktree's ``.git`` file; its parent
                # is the working tree directory. Also catch ValueError /
                # UnicodeDecodeError so a single corrupt file (non-UTF-8 bytes,
                # partial write) doesn't abort the whole sweep.
                gitdir_text = gitdir_file.read_text(encoding="utf-8").strip()
            except (OSError, ValueError):
                continue
            if not gitdir_text:
                continue
            gitdir_path = Path(gitdir_text)
            # Git ≥2.40 with ``worktree.useRelativePaths=true`` writes ``gitdir``
            # as a path relative to the admin directory, not the process CWD.
            # Resolve it against ``admin`` to avoid mis-classifying foreign or own
            # worktrees when the bot happens to run from a different CWD.
            if not gitdir_path.is_absolute():
                gitdir_path = admin / gitdir_path
            worktree_dir = gitdir_path.parent
            try:
                is_ours = worktree_dir.resolve().is_relative_to(own_root)
            except (OSError, ValueError):
                is_ours = False
            if is_ours and not worktree_dir.exists():
                await asyncio.to_thread(shutil.rmtree, admin, ignore_errors=True)

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
