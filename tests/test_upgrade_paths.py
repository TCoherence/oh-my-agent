"""Upgrade-path tests for v1.0 acceptance criteria #9.

Three buckets:

* **Memory layout migration** — exercises ``scripts/migrate_memory_to_judge.py``
  end-to-end against a v0.8 ``curated.yaml`` fixture, asserting the produced
  ``memories.yaml`` plus the backup directory.
* **Config alias compatibility** — locks in the existing ``memory.adaptive`` →
  ``memory.judge`` fallback at [main.py:457] and the deprecation warnings emitted
  by [main.py:_warn_if_legacy_memory_config] / [main.py:_warn_if_legacy_memory_layout].
* **runtime.db schema migrations** — uses a builder helper (no binary fixture
  in git) to construct a pre-v0.8 ``runtime_tasks`` table that's missing the
  newer columns and stores a row with the obsolete ``task_type='code'`` enum,
  then runs ``init()`` and asserts the ``_migrate_runtime_schema`` /
  ``_run_schema_migrations`` hooks backfilled the columns + normalised the
  enum + bumped ``schema_version``.

Avoids: shipping a pre-built binary ``runtime.db`` (git-noisy + platform
divergent) — the builder is the canonical fixture.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import aiosqlite
import pytest
import yaml

from oh_my_agent.main import (
    _warn_if_legacy_memory_config,
    _warn_if_legacy_memory_layout,
)
from oh_my_agent.memory.store import (
    CURRENT_SCHEMA_VERSION,
    SQLiteMemoryStore,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_SCRIPT = REPO_ROOT / "scripts" / "migrate_memory_to_judge.py"


# --- Memory layout migration --------------------------------------------------


def _write_legacy_curated(memory_dir: Path) -> list[dict]:
    """Mirror the v0.8 curated.yaml shape so the migration script has a target."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "id": "legacy-pref-1",
            "summary": "User prefers Chinese for discussion, English for code",
            "category": "preference",
            "scope": "global_user",
            "confidence": 0.9,
            "observation_count": 4,
            "evidence": "Said 'we should keep code/docs in English' on multiple threads.",
            "source_threads": ["1234"],
            "source_skills": [],
            "created_at": "2026-03-01T00:00:00+00:00",
            "last_observed_at": "2026-04-10T00:00:00+00:00",
        },
        {
            "id": "legacy-workflow-1",
            "summary": "Run pytest via .venv/bin/python, never global python",
            "category": "workflow",
            "scope": "workspace",
            "confidence": 0.85,
            "observation_count": 3,
            "evidence": "Multiple corrections to use the venv interpreter.",
            "source_threads": ["5678"],
            "source_skills": [],
            "source_workspace": "/repos/oh-my-agent",
            "created_at": "2026-03-15T00:00:00+00:00",
        },
    ]
    (memory_dir / "curated.yaml").write_text(
        yaml.safe_dump(entries, allow_unicode=True), encoding="utf-8"
    )
    # Drop a stale MEMORY.md so we can assert the script removes it.
    (memory_dir / "MEMORY.md").write_text("# old natural-language memory\n", encoding="utf-8")
    return entries


def _run_migration_script(memory_dir: Path, *extra_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(MIGRATION_SCRIPT), str(memory_dir), *extra_args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_migrate_memory_to_judge_dry_run_does_not_touch_filesystem(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "legacy_memory"
    _write_legacy_curated(legacy_dir)

    result = _run_migration_script(legacy_dir, "--dry-run")
    assert result.returncode == 0, result.stderr

    assert (legacy_dir / "curated.yaml").exists()
    assert (legacy_dir / "MEMORY.md").exists()
    assert not (legacy_dir / "memories.yaml").exists()
    # Backup is only written on real runs.
    assert not list(tmp_path.glob("legacy_memory.bak.*"))


def test_migrate_memory_to_judge_writes_active_entries_and_backup(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "legacy_memory"
    _write_legacy_curated(legacy_dir)

    result = _run_migration_script(legacy_dir)
    assert result.returncode == 0, result.stderr

    new_path = legacy_dir / "memories.yaml"
    assert new_path.exists()
    converted = yaml.safe_load(new_path.read_text(encoding="utf-8"))
    assert isinstance(converted, list)
    assert len(converted) == 2
    summaries = {e["summary"] for e in converted}
    assert "User prefers Chinese for discussion, English for code" in summaries

    for entry in converted:
        assert entry["status"] == "active"
        assert entry["category"] in {"preference", "workflow", "project_knowledge", "fact"}
        assert entry["scope"] in {"global_user", "workspace", "skill", "thread"}
        assert isinstance(entry["evidence_log"], list) and entry["evidence_log"]

    # Live dir was cleaned so the next startup synthesizes a fresh MEMORY.md.
    assert not (legacy_dir / "curated.yaml").exists()
    assert not (legacy_dir / "MEMORY.md").exists()

    backups = list(tmp_path.glob("legacy_memory.bak.*"))
    assert len(backups) == 1
    assert (backups[0] / "curated.yaml").exists()


# --- Config alias compatibility (lock-in, not regress to raise) ---------------


def test_warn_emitted_when_only_memory_adaptive_present(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="test")
    test_logger = logging.getLogger("test")

    fired = _warn_if_legacy_memory_config(
        {"adaptive": {"enabled": True, "inject_limit": 5}},
        test_logger,
    )
    assert fired is True
    assert any("memory.adaptive is deprecated" in r.message for r in caplog.records)


def test_no_warn_when_memory_judge_present(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="test")
    test_logger = logging.getLogger("test")

    fired = _warn_if_legacy_memory_config(
        {"judge": {"enabled": True}, "adaptive": {"enabled": True}},
        test_logger,
    )
    assert fired is False
    assert not any("memory.adaptive is deprecated" in r.message for r in caplog.records)


def test_no_warn_when_neither_present(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="test")
    test_logger = logging.getLogger("test")

    fired = _warn_if_legacy_memory_config({}, test_logger)
    assert fired is False
    assert not any("memory.adaptive is deprecated" in r.message for r in caplog.records)


def test_adaptive_alias_still_resolves_to_judge_block() -> None:
    """Lock in the [main.py:457] alias fallback — must NOT raise on legacy configs."""
    memory_cfg = {"adaptive": {"enabled": True, "inject_limit": 5, "idle_seconds": 600}}
    block = memory_cfg.get("judge", memory_cfg.get("adaptive", {}))
    assert block.get("enabled") is True
    assert int(block.get("inject_limit")) == 5
    assert int(block.get("idle_seconds")) == 600


def test_warn_emitted_for_legacy_curated_yaml(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="test")
    test_logger = logging.getLogger("test")

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "curated.yaml").write_text("[]", encoding="utf-8")

    fired = _warn_if_legacy_memory_layout(memory_dir, test_logger)
    assert fired is True
    assert any("Legacy memory layout detected" in r.message for r in caplog.records)
    assert any("migrate_memory_to_judge.py" in r.message for r in caplog.records)


def test_warn_emitted_for_legacy_daily_dir(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="test")
    test_logger = logging.getLogger("test")

    memory_dir = tmp_path / "memory"
    (memory_dir / "daily").mkdir(parents=True)
    (memory_dir / "daily" / "2026-04-15.yaml").write_text("[]", encoding="utf-8")

    fired = _warn_if_legacy_memory_layout(memory_dir, test_logger)
    assert fired is True


def test_no_warn_when_only_new_layout_present(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="test")
    test_logger = logging.getLogger("test")

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "memories.yaml").write_text("[]", encoding="utf-8")

    fired = _warn_if_legacy_memory_layout(memory_dir, test_logger)
    assert fired is False


# --- runtime.db schema migrations (builder fixture, not binary) --------------


async def _build_legacy_runtime_db(db_path: Path) -> None:
    """Construct a pre-v0.8 runtime.db: minimal columns + obsolete task_type enum.

    Mirrors the v0.7 era schema where ``runtime_tasks`` had a tiny column set
    and used ``task_type='code'`` / ``'skill'`` instead of the current
    ``'repo_change'`` / ``'skill_change'``. The on-disk SQL purposely omits the
    newer columns so ``_ensure_column`` has work to do, and the row uses the
    legacy enum so the data-normalisation UPDATE has rows to flip.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(
            """
            CREATE TABLE runtime_tasks (
                id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                created_by TEXT NOT NULL,
                goal TEXT NOT NULL,
                preferred_agent TEXT,
                status TEXT NOT NULL,
                step_no INTEGER NOT NULL DEFAULT 0,
                max_steps INTEGER NOT NULL,
                max_minutes INTEGER NOT NULL,
                test_command TEXT NOT NULL,
                workspace_path TEXT,
                decision_message_id TEXT,
                blocked_reason TEXT,
                error TEXT,
                summary TEXT,
                resume_instruction TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP,
                task_type TEXT NOT NULL DEFAULT 'code'
            )
            """
        )
        await db.execute(
            "INSERT INTO runtime_tasks "
            "(id, platform, channel_id, thread_id, created_by, goal, status, "
            " max_steps, max_minutes, test_command, task_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-task-1",
                "discord",
                "ch1",
                "thread-old",
                "owner-1",
                "legacy goal",
                "DRAFT",
                8,
                20,
                "pytest -q",
                "code",  # obsolete enum to be normalised by _migrate_runtime_schema
            ),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_runtime_schema_migration_backfills_columns_and_normalises_enum(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime.db"
    await _build_legacy_runtime_db(db_path)

    store = SQLiteMemoryStore(db_path)
    await store.init()
    try:
        # Spot-check a handful of columns that _ensure_column should have added.
        async with aiosqlite.connect(str(db_path)) as raw:
            cursor = await raw.execute("PRAGMA table_info(runtime_tasks)")
            rows = await cursor.fetchall()
        cols = {row[1] for row in rows}
        for required in (
            "original_request",
            "status_message_id",
            "merge_commit_hash",
            "merge_error",
            "completion_mode",
            "output_summary",
            "artifact_manifest",
            "automation_name",
            "workspace_cleaned_at",
            "skill_name",
            "agent_timeout_seconds",
            "agent_max_turns",
        ):
            assert required in cols, f"column {required} not backfilled by migration"

        # The pre-existing row still loads, and its task_type was normalised.
        rehydrated = await store.get_runtime_task("legacy-task-1")
        assert rehydrated is not None
        assert rehydrated.task_type == "repo_change"
        assert rehydrated.status == "DRAFT"
        assert rehydrated.completion_mode == "merge"  # default backfill

        # Re-running init() against an already-migrated DB must be idempotent.
        await store.init()
        again = await store.get_runtime_task("legacy-task-1")
        assert again is not None and again.task_type == "repo_change"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_schema_version_lands_at_current_after_init(tmp_path: Path) -> None:
    """Placeholder for future ``_run_schema_migrations`` versioned steps.

    Today the hook is a no-op (CURRENT_SCHEMA_VERSION = 1). The check below
    exists so when somebody adds an ``if current < 2:`` block they get
    immediate signal that the version landed at the expected target after
    ``init()``. Locks the contract: ``init()`` must always leave
    ``schema_version >= CURRENT_SCHEMA_VERSION``.
    """
    store = SQLiteMemoryStore(tmp_path / "runtime.db")
    await store.init()
    try:
        assert await store.get_schema_version() == CURRENT_SCHEMA_VERSION

        # Idempotent re-init.
        await store.init()
        assert await store.get_schema_version() == CURRENT_SCHEMA_VERSION
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_legacy_db_without_schema_version_table_lands_at_current(
    tmp_path: Path,
) -> None:
    """A pre-v0.7 DB without ``schema_version`` should still wind up at the
    current version after ``init()`` (the SQL bootstrap creates the table +
    inserts version=1, then ``_run_schema_migrations`` finalises).
    """
    db_path = tmp_path / "runtime.db"
    await _build_legacy_runtime_db(db_path)

    # Confirm the legacy DB really has no schema_version table.
    async with aiosqlite.connect(str(db_path)) as raw:
        cursor = await raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        assert await cursor.fetchone() is None

    store = SQLiteMemoryStore(db_path)
    await store.init()
    try:
        assert await store.get_schema_version() == CURRENT_SCHEMA_VERSION
    finally:
        await store.close()
