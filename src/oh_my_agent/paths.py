"""Centralized runtime / memory / log path resolution.

All helpers accept a **top-level config dict** (i.e. the dict returned by
``oh_my_agent.config.load_config``) and return absolute, expanded ``Path``
values matching the defaults baked into ``boot.py``'s setdefault chain.

Why this module exists
----------------------

Path derivation was previously scattered across ``boot.py`` (private
``_runtime_root``), ``runtime/service.py`` (self-derives logs dir from
``cfg["worktree_root"]``), ``memory/store.py`` (db path), and
``memory/judge_store.py`` (memory dir). Dashboard (and any future tool that
needs to read these paths from outside ``boot.py``) would have re-derived
the same logic and silently drifted.

This module is the single source of truth for **boot.py + dashboard**. It
intentionally does **not** retrofit ``runtime/service.py`` to import it —
``RuntimeService.__init__`` is constructed in 20+ test fixtures, and rewiring
its signature would balloon the change. Instead, ``tests/test_paths.py``
contains a drift sentinel that constructs a ``RuntimeService`` instance and
asserts its self-derived path attributes equal the helper outputs.

Import direction
----------------

This module imports only stdlib (``pathlib``, ``typing``). It must not import
any ``oh_my_agent.*`` modules to avoid circular imports.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults — kept in lockstep with boot.py:_apply_defaults setdefault chain.
# When changing a default here, change the matching setdefault in boot.py too.
# ---------------------------------------------------------------------------

_DEFAULT_WORKTREE_ROOT = "~/.oh-my-agent/runtime/tasks"
_DEFAULT_STATE_PATH = "~/.oh-my-agent/runtime/runtime.db"
_DEFAULT_REPORTS_DIR = "~/.oh-my-agent/reports"
_DEFAULT_MEMORY_DB = "~/.oh-my-agent/runtime/memory.db"
_DEFAULT_SKILLS_TELEMETRY = "~/.oh-my-agent/runtime/skills.db"
_DEFAULT_JUDGE_MEMORY_DIR = "~/.oh-my-agent/memory"


def _abs(path: str | Path) -> Path:
    """Expand ``~`` and resolve to an absolute path."""

    return Path(path).expanduser().resolve()


# ---------------------------------------------------------------------------
# Runtime paths
# ---------------------------------------------------------------------------


def runtime_worktree_root(config: dict) -> Path:
    """``runtime.worktree_root`` (default ``~/.oh-my-agent/runtime/tasks``)."""

    runtime_cfg = config.get("runtime", {}) or {}
    return _abs(runtime_cfg.get("worktree_root", _DEFAULT_WORKTREE_ROOT))


def runtime_root(config: dict) -> Path:
    """Parent of ``worktree_root``.

    Mirrors ``boot.py:_runtime_root`` and ``service.py:280`` — both derive
    runtime root from ``Path(worktree_root).parent``. There is no explicit
    ``runtime.runtime_root`` config field.
    """

    return runtime_worktree_root(config).parent


def runtime_state_path(config: dict) -> Path:
    """``runtime.state_path`` (default ``~/.oh-my-agent/runtime/runtime.db``)."""

    runtime_cfg = config.get("runtime", {}) or {}
    return _abs(runtime_cfg.get("state_path", _DEFAULT_STATE_PATH))


def runtime_logs_root(config: dict) -> Path:
    """``runtime_root / "logs"``.

    Matches ``service.py:283``: ``self._logs_root = self._runtime_workspace_root.parent / "logs"``.
    """

    return runtime_root(config) / "logs"


def runtime_service_log_path(config: dict) -> Path:
    """``runtime_logs_root / "service.log"`` — the root-logger structured log
    written by ``logging_setup.py`` (gateway, scheduler, runtime, etc.)."""

    return runtime_logs_root(config) / "service.log"


def runtime_oma_log_path(config: dict) -> Path:
    """``runtime_logs_root / "oh-my-agent.log"`` — the ``RuntimeService``-owned
    secondary log file (see ``service.py:289``).

    Naming is unfortunate (``service_log_path`` attribute points here), but the
    reality is two log files coexist and the dashboard system layer reads both
    for ERROR/WARNING aggregation."""

    return runtime_logs_root(config) / "oh-my-agent.log"


def runtime_reports_dir(config: dict) -> Path | None:
    """``runtime.reports_dir`` (default ``~/.oh-my-agent/reports``).

    Mirrors ``service.py:298-302`` exactly: explicit ``None`` / ``False`` / ``""``
    means "publishing disabled, no path"; missing key means default."""

    runtime_cfg = config.get("runtime", {}) or {}
    if "reports_dir" in runtime_cfg:
        reports_cfg = runtime_cfg["reports_dir"]
    else:
        reports_cfg = _DEFAULT_REPORTS_DIR
    if reports_cfg in (None, False, ""):
        return None
    return _abs(str(reports_cfg))


# ---------------------------------------------------------------------------
# Memory / skills paths
# ---------------------------------------------------------------------------


def memory_db_path(config: dict) -> Path:
    """``memory.path`` (default ``~/.oh-my-agent/runtime/memory.db``)."""

    memory_cfg = config.get("memory", {}) or {}
    return _abs(memory_cfg.get("path", _DEFAULT_MEMORY_DB))


def skills_telemetry_path(config: dict) -> Path:
    """``skills.telemetry_path`` (default ``~/.oh-my-agent/runtime/skills.db``)."""

    skills_cfg = config.get("skills", {}) or {}
    return _abs(skills_cfg.get("telemetry_path", _DEFAULT_SKILLS_TELEMETRY))


def judge_memory_dir(config: dict) -> Path:
    """Directory holding ``memories.yaml`` + ``MEMORY.md``.

    Mirrors ``boot.py:803`` exactly:

    .. code:: python

        memory_cfg_block = memory_cfg.get("judge", memory_cfg.get("adaptive", {}))
        memory_dir = memory_cfg_block.get("memory_dir", "~/.oh-my-agent/memory")

    The "block" is picked **once**: ``memory.judge`` if present, else
    ``memory.adaptive``, else ``{}``. Then ``memory_dir`` comes from that block.
    Crucially, if ``memory.judge`` exists **without** a ``memory_dir`` key,
    we do NOT fall through to ``memory.adaptive`` — we fall through to the
    default. This matches boot.py's semantics; an earlier helper version
    leaked adaptive's value into the judge case (caught by Codex review).
    """

    memory_cfg = config.get("memory", {}) or {}
    block = memory_cfg.get("judge", memory_cfg.get("adaptive", {})) or {}
    if not isinstance(block, dict):
        block = {}
    return _abs(block.get("memory_dir", _DEFAULT_JUDGE_MEMORY_DIR))


def judge_memories_yaml_path(config: dict) -> Path:
    """``judge_memory_dir / "memories.yaml"``."""

    return judge_memory_dir(config) / "memories.yaml"
