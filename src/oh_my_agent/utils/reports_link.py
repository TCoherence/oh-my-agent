"""Read-intended ``reports_archive/`` symlink for cross-session report discovery.

Runtime tasks publish reports to a durable ``reports_dir`` (default
``~/.oh-my-agent/reports``) that is otherwise invisible to ordinary chat/task
sessions because each runs in an isolated workspace. This helper drops a
``reports_archive/`` symlink into a session workspace pointing at that tree so an
agent can browse / read prior runs. It is **read-intended** by convention (the
AGENTS hint says "treat as read-only") — a symlink is not an OS read-only mount.

Safety: the base agent workspace is durable, not disposable scratch, so we never
clobber a real path — only an existing *symlink* is repointed.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_LINK_NAME = "reports_archive"


def ensure_reports_archive_link(workspace: Path, reports_dir: Path | None) -> None:
    """Idempotently link ``workspace/reports_archive`` → ``reports_dir``.

    No-op when ``reports_dir`` is ``None`` (publishing disabled). Pre-creates
    ``reports_dir`` so the symlink never dangles on first boot. Three cases for
    an existing ``workspace/reports_archive``:

    1. correct symlink (already → ``reports_dir``) — keep, no-op.
    2. wrong symlink (→ elsewhere) — unlink and recreate.
    3. real file or directory — **refuse to overwrite** (log + skip); never
       ``rmtree``/``unlink`` a real path.
    """
    if reports_dir is None:
        return
    try:
        reports_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("reports_archive: cannot create reports_dir %s: %s", reports_dir, exc)
        return

    target = reports_dir.resolve()
    link = workspace / _LINK_NAME

    if link.is_symlink():
        try:
            if link.resolve() == target:
                return  # case 1: already correct
        except OSError:
            pass  # broken symlink → fall through to repoint
        try:
            link.unlink()  # case 2: wrong/broken symlink → recreate
        except OSError as exc:
            logger.warning("reports_archive: cannot repoint %s: %s", link, exc)
            return
    elif link.exists():
        # case 3: a real file/dir lives here — do not destroy it.
        logger.warning(
            "reports_archive: %s is a real path; refusing to overwrite", link
        )
        return

    try:
        workspace.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        logger.warning(
            "reports_archive: cannot link %s -> %s: %s", link, target, exc
        )
