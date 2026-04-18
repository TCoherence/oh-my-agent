#!/usr/bin/env python3
"""One-shot migration from the legacy daily/curated tier store to the new
single-layer JudgeStore.

Usage::

    python scripts/migrate_memory_to_judge.py /path/to/memory_dir

Behavior:
- Backs up the existing directory to ``<memory_dir>.bak.<timestamp>``
- Reads ``curated.yaml`` (and optionally daily/*.yaml when --include-daily is passed)
- Writes ``memories.yaml`` in the new schema with status=active
- Removes ``daily/``, ``curated.yaml``, and ``MEMORY.md`` from the live dir so the
  next process startup synthesizes a fresh MEMORY.md from active entries.

Run with ``--dry-run`` to preview the diff without writing.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


VALID_CATEGORIES = {"preference", "project_knowledge", "workflow", "fact"}
VALID_SCOPES = {"global_user", "workspace", "skill", "thread"}


def _load_yaml_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    return [d for d in raw if isinstance(d, dict)]


def _convert_entry(legacy: dict, *, source_file: str) -> dict | None:
    summary = str(legacy.get("summary", "")).strip()
    if not summary:
        return None
    category = str(legacy.get("category", "fact"))
    if category not in VALID_CATEGORIES:
        category = "fact"
    scope = str(legacy.get("scope", "global_user"))
    if scope not in VALID_SCOPES:
        scope = "global_user"
    try:
        confidence = float(legacy.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.7
    confidence = max(0.0, min(1.0, confidence))
    try:
        observation_count = int(legacy.get("observation_count", 1))
    except (TypeError, ValueError):
        observation_count = 1
    observation_count = max(1, observation_count)

    evidence_log = []
    legacy_evidence = str(legacy.get("evidence", "")).strip()
    if legacy_evidence:
        ts = str(legacy.get("last_observed_at") or legacy.get("created_at") or _now_iso())
        thread_id = ""
        threads = legacy.get("source_threads") or []
        if isinstance(threads, list) and threads:
            thread_id = str(threads[0])
        evidence_log.append(
            {"thread_id": thread_id, "ts": ts, "snippet": legacy_evidence[:280]}
        )

    source_skills = legacy.get("source_skills") or []
    if not isinstance(source_skills, list):
        source_skills = []
    source_skills = [str(s) for s in source_skills if s]

    return {
        "id": str(legacy.get("id") or _new_id()),
        "summary": summary,
        "category": category,
        "scope": scope,
        "confidence": confidence,
        "observation_count": observation_count,
        "evidence_log": evidence_log,
        "source_skills": source_skills,
        "source_workspace": str(legacy.get("source_workspace", "")),
        "status": "active",
        "superseded_by": None,
        "created_at": str(legacy.get("created_at") or _now_iso()),
        "last_observed_at": str(legacy.get("last_observed_at") or legacy.get("created_at") or _now_iso()),
        "_migrated_from": source_file,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("memory_dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--include-daily",
        action="store_true",
        help="Also import daily/*.yaml entries (default: curated-only).",
    )
    args = parser.parse_args()

    memory_dir: Path = args.memory_dir.expanduser().resolve()
    if not memory_dir.is_dir():
        print(f"❌ {memory_dir} is not a directory", file=sys.stderr)
        return 2

    curated_path = memory_dir / "curated.yaml"
    daily_dir = memory_dir / "daily"
    new_path = memory_dir / "memories.yaml"

    legacy_curated = _load_yaml_list(curated_path)
    legacy_daily: list[tuple[str, dict]] = []
    if args.include_daily and daily_dir.is_dir():
        for yaml_file in sorted(daily_dir.glob("*.yaml")):
            for item in _load_yaml_list(yaml_file):
                legacy_daily.append((yaml_file.name, item))

    converted: list[dict] = []
    for item in legacy_curated:
        entry = _convert_entry(item, source_file="curated.yaml")
        if entry is not None:
            converted.append(entry)
    for source_file, item in legacy_daily:
        entry = _convert_entry(item, source_file=f"daily/{source_file}")
        if entry is not None:
            converted.append(entry)

    print(
        f"Source: curated={len(legacy_curated)} daily={len(legacy_daily)} → "
        f"converted={len(converted)} active entries"
    )
    print(f"Will write: {new_path}")
    if args.dry_run:
        print("--dry-run: skipping writes")
        return 0

    backup = memory_dir.with_name(memory_dir.name + ".bak." + datetime.now().strftime("%Y%m%dT%H%M%S"))
    shutil.copytree(memory_dir, backup)
    print(f"Backup: {backup}")

    new_path.write_text(
        yaml.dump(converted, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    if curated_path.exists():
        curated_path.unlink()
    if daily_dir.is_dir():
        shutil.rmtree(daily_dir)
    md_path = memory_dir / "MEMORY.md"
    if md_path.exists():
        md_path.unlink()

    print("✅ Migration complete. Next oh-my-agent startup will synthesize MEMORY.md from active entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
