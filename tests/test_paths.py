"""Tests for ``oh_my_agent.paths`` plus a drift sentinel against ``RuntimeService``.

The sentinel is the load-bearing piece: ``runtime/service.py:280-302`` derives
its own logs/workspace/reports paths internally rather than importing
``paths.py`` (so we don't have to rewire 20+ test fixtures). If the helpers in
``paths.py`` ever drift from the in-class derivation, this test fails loudly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_my_agent import paths

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_runtime_root_default() -> None:
    assert paths.runtime_root({}) == Path("~/.oh-my-agent/runtime").expanduser().resolve()


def test_runtime_worktree_root_default() -> None:
    assert (
        paths.runtime_worktree_root({})
        == Path("~/.oh-my-agent/runtime/tasks").expanduser().resolve()
    )


def test_runtime_state_path_default() -> None:
    assert (
        paths.runtime_state_path({})
        == Path("~/.oh-my-agent/runtime/runtime.db").expanduser().resolve()
    )


def test_runtime_logs_root_default() -> None:
    assert paths.runtime_logs_root({}) == Path("~/.oh-my-agent/runtime/logs").expanduser().resolve()


def test_runtime_service_log_path_default() -> None:
    assert (
        paths.runtime_service_log_path({})
        == Path("~/.oh-my-agent/runtime/logs/service.log").expanduser().resolve()
    )


def test_runtime_oma_log_path_default() -> None:
    assert (
        paths.runtime_oma_log_path({})
        == Path("~/.oh-my-agent/runtime/logs/oh-my-agent.log").expanduser().resolve()
    )


def test_memory_db_path_default() -> None:
    assert (
        paths.memory_db_path({})
        == Path("~/.oh-my-agent/runtime/memory.db").expanduser().resolve()
    )


def test_skills_telemetry_path_default() -> None:
    assert (
        paths.skills_telemetry_path({})
        == Path("~/.oh-my-agent/runtime/skills.db").expanduser().resolve()
    )


def test_judge_memory_dir_default() -> None:
    assert paths.judge_memory_dir({}) == Path("~/.oh-my-agent/memory").expanduser().resolve()


def test_judge_memories_yaml_path_default() -> None:
    assert (
        paths.judge_memories_yaml_path({})
        == Path("~/.oh-my-agent/memory/memories.yaml").expanduser().resolve()
    )


# ---------------------------------------------------------------------------
# Config overrides
# ---------------------------------------------------------------------------


def test_runtime_paths_with_override(tmp_path: Path) -> None:
    cfg = {"runtime": {"worktree_root": str(tmp_path / "wt"), "state_path": str(tmp_path / "x.db")}}
    assert paths.runtime_worktree_root(cfg) == tmp_path / "wt"
    assert paths.runtime_root(cfg) == tmp_path  # parent of wt
    assert paths.runtime_state_path(cfg) == tmp_path / "x.db"
    assert paths.runtime_logs_root(cfg) == tmp_path / "logs"
    assert paths.runtime_service_log_path(cfg) == tmp_path / "logs" / "service.log"
    assert paths.runtime_oma_log_path(cfg) == tmp_path / "logs" / "oh-my-agent.log"


def test_memory_db_path_with_override(tmp_path: Path) -> None:
    cfg = {"memory": {"path": str(tmp_path / "m.db")}}
    assert paths.memory_db_path(cfg) == tmp_path / "m.db"


def test_skills_telemetry_path_with_override(tmp_path: Path) -> None:
    cfg = {"skills": {"telemetry_path": str(tmp_path / "s.db")}}
    assert paths.skills_telemetry_path(cfg) == tmp_path / "s.db"


# ---------------------------------------------------------------------------
# runtime_reports_dir 4-case (Codex round 6)
# ---------------------------------------------------------------------------


def test_runtime_reports_dir_default() -> None:
    """Missing key → default ``~/.oh-my-agent/reports``."""
    assert (
        paths.runtime_reports_dir({})
        == Path("~/.oh-my-agent/reports").expanduser().resolve()
    )


def test_runtime_reports_dir_explicit_path(tmp_path: Path) -> None:
    cfg = {"runtime": {"reports_dir": str(tmp_path / "r")}}
    assert paths.runtime_reports_dir(cfg) == tmp_path / "r"


@pytest.mark.parametrize("disabled_value", [None, False, ""])
def test_runtime_reports_dir_disabled_returns_none(disabled_value) -> None:
    """Explicit None / False / "" → publishing disabled, helper returns None.

    Mirrors ``service.py:298-302`` semantics exactly."""
    cfg = {"runtime": {"reports_dir": disabled_value}}
    assert paths.runtime_reports_dir(cfg) is None


# ---------------------------------------------------------------------------
# judge_memory_dir three-level fallback (boot.py:809-812)
# ---------------------------------------------------------------------------


def test_judge_memory_dir_judge_block_wins(tmp_path: Path) -> None:
    cfg = {
        "memory": {
            "judge": {"memory_dir": str(tmp_path / "judge")},
            "adaptive": {"memory_dir": str(tmp_path / "adaptive")},
        }
    }
    assert paths.judge_memory_dir(cfg) == tmp_path / "judge"


def test_judge_memory_dir_adaptive_fallback(tmp_path: Path) -> None:
    cfg = {"memory": {"adaptive": {"memory_dir": str(tmp_path / "adaptive")}}}
    assert paths.judge_memory_dir(cfg) == tmp_path / "adaptive"


def test_judge_memory_dir_legacy_default() -> None:
    assert paths.judge_memory_dir({}) == Path("~/.oh-my-agent/memory").expanduser().resolve()


def test_judge_memory_dir_judge_without_memory_dir_falls_to_default() -> None:
    """Judge block exists but no memory_dir key → default (not adaptive)."""
    cfg = {"memory": {"judge": {"enabled": True}}}
    assert paths.judge_memory_dir(cfg) == Path("~/.oh-my-agent/memory").expanduser().resolve()


# ---------------------------------------------------------------------------
# Absolute-path invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn",
    [
        paths.runtime_root,
        paths.runtime_worktree_root,
        paths.runtime_state_path,
        paths.runtime_logs_root,
        paths.runtime_service_log_path,
        paths.runtime_oma_log_path,
        paths.memory_db_path,
        paths.skills_telemetry_path,
        paths.judge_memory_dir,
        paths.judge_memories_yaml_path,
    ],
)
def test_helpers_return_absolute_paths(fn) -> None:
    result = fn({})
    assert result.is_absolute(), f"{fn.__name__} returned non-absolute path: {result}"


def test_runtime_reports_dir_returns_absolute_when_set() -> None:
    result = paths.runtime_reports_dir({})
    assert result is not None
    assert result.is_absolute()


# ---------------------------------------------------------------------------
# Drift sentinel — Codex round 4+5+6
#
# RuntimeService self-derives 6 path-related attributes from `cfg["worktree_root"]`
# and `cfg["reports_dir"]`. paths.py is a parallel re-implementation that boot.py
# and the new dashboard module use. The two implementations must produce
# identical results — this sentinel pins them together so any future drift
# fails CI.
# ---------------------------------------------------------------------------


@pytest.fixture
async def _runtime_for_sentinel(tmp_path: Path):
    """Construct a minimal RuntimeService for path-attribute inspection.

    We don't start the service — we only need __init__ to populate the path
    attributes on self. Stop is called for cleanliness in case any background
    state was scheduled."""

    from oh_my_agent.memory.store import SQLiteMemoryStore
    from oh_my_agent.runtime.service import RuntimeService

    db_path = tmp_path / "runtime.db"
    store = SQLiteMemoryStore(db_path)
    await store.init()

    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)

    def _make(reports_dir_value):
        cfg_runtime = {
            "enabled": True,
            "worker_concurrency": 1,
            "worktree_root": str(tmp_path / "worktrees"),
            "reports_dir": reports_dir_value,
            "default_agent": "x",
            "default_test_command": "true",
            "default_max_steps": 1,
            "default_max_minutes": 1,
            "cleanup": {"enabled": False},
            "merge_gate": {"enabled": False},
        }
        rt = RuntimeService(
            store, config=cfg_runtime, owner_user_ids={"owner"}, repo_root=repo
        )
        # paths.py helpers take TOP-LEVEL config, not the runtime sub-dict.
        top_cfg = {"runtime": cfg_runtime}
        return rt, top_cfg

    yield _make

    await store.close()


async def test_drift_sentinel_six_path_attributes(_runtime_for_sentinel, tmp_path: Path) -> None:
    """Pin all 6 RuntimeService path attributes to their paths.py counterparts.

    Six fields per Codex round 5+6:
      1. _logs_root
      2. _service_log_path
      3. _runtime_workspace_root (= worktree_root)
      4. _reports_dir
      5. _thread_logs_root  (structural derivative: _logs_root / "threads")
      6. _agent_logs_root   (structural derivative: _logs_root / "agents")
    """

    rt, top_cfg = _runtime_for_sentinel(str(tmp_path / "reports-explicit"))

    # paths.py-backed helpers (1, 2, 3, 4)
    assert paths.runtime_logs_root(top_cfg) == rt._logs_root
    assert paths.runtime_oma_log_path(top_cfg) == rt._service_log_path
    assert paths.runtime_worktree_root(top_cfg) == rt._runtime_workspace_root
    assert paths.runtime_reports_dir(top_cfg) == rt._reports_dir

    # Structural invariants (5, 6) — no helper needed but pinned anyway
    assert rt._thread_logs_root == rt._logs_root / "threads"
    assert rt._agent_logs_root == rt._logs_root / "agents"


@pytest.mark.parametrize("reports_value", [None, False, "", "DEFAULT_OMITTED"])
async def test_drift_sentinel_reports_dir_all_four_cases(
    _runtime_for_sentinel, reports_value
) -> None:
    """Verify paths.runtime_reports_dir matches RuntimeService._reports_dir
    across all 4 cases: default (key omitted), None, False, "".

    Codex round 6 explicitly required this multi-case coverage."""

    if reports_value == "DEFAULT_OMITTED":
        # We can't easily delete the key after construction in our factory;
        # fake "key absent" by passing the literal default value the helper
        # would also return. RuntimeService.__init__ uses
        # `cfg.get("reports_dir", "~/.oh-my-agent/reports")` so the absent
        # case maps to that exact string — we mirror it.
        rt, _ = _runtime_for_sentinel("~/.oh-my-agent/reports")
        # Compare against the helper's default-key-absent behavior:
        assert paths.runtime_reports_dir({}) == rt._reports_dir
    else:
        rt, top_cfg = _runtime_for_sentinel(reports_value)
        assert paths.runtime_reports_dir(top_cfg) == rt._reports_dir
        # Disabled values must produce None on both sides.
        assert rt._reports_dir is None
        assert paths.runtime_reports_dir(top_cfg) is None
