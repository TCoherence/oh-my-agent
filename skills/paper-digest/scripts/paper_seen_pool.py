#!/usr/bin/env python3
"""Rolling 14-day paper-seen pool + tracked labs runtime state.

用途：
- HF Daily 上的 trending 是多日滚动的，没有 seen-pool 同一篇论文会天天出现。
- 记录过去 14 天已在报告里出现过的论文，paper_fetch.py 会把它们标记为
  seen_before=true；生成报告时 agent 可据此把老论文放进「延伸阅读」
  而不是「Top picks」。
- 同时维护 tracked_labs_runtime：从报告的 tracked_labs_seen 字段累计
  观察到的机构频次，便于后续 sync-seed 回写种子文件。

State 文件：~/.oh-my-agent/reports/paper-digest/state/paper_seen_pool.json
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from report_store import current_local_date, resolve_report_timezone_label, resolve_reports_root


RUNTIME_STATE_FILENAME = "paper_seen_pool.json"
SEED_FILENAME = "paper_groups_seed.yaml"
DEFAULT_PRUNE_DAYS = 14


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def resolve_seed_path(seed_file: str | Path | None = None) -> Path:
    if seed_file is not None:
        return Path(seed_file).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent / "references" / SEED_FILENAME).resolve()


def resolve_state_path(root: str | Path | None = None, state_file: str | Path | None = None) -> Path:
    if state_file is not None:
        return Path(state_file).expanduser().resolve()
    return resolve_reports_root(root) / "state" / RUNTIME_STATE_FILENAME


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "groups": {}}
    if yaml is None:
        return {"version": 1, "groups": {}, "_note": "PyYAML missing; seed not parsed"}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"seed file must contain an object: {path}")
    data.setdefault("version", 1)
    data.setdefault("groups", {})
    return data


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "updated_at": "",
            "report_timezone": resolve_report_timezone_label(),
            "seen": {},
            "tracked_labs_runtime": {},
            "synced_seed": {},
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"state file must contain an object: {path}")
    data.setdefault("version", 1)
    data.setdefault("updated_at", "")
    data.setdefault("report_timezone", resolve_report_timezone_label())
    data.setdefault("seen", {})
    data.setdefault("tracked_labs_runtime", {})
    data.setdefault("synced_seed", {})
    return data


def _paper_key(entry: dict[str, Any]) -> str | None:
    """Stable dedup key. Priority: arxiv_id > doi > s2_paper_id > normalized title."""
    arxiv_id = str(entry.get("arxiv_id") or "").strip()
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    doi = str(entry.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    s2 = str(entry.get("s2_paper_id") or "").strip()
    if s2:
        return f"s2:{s2}"
    title = str(entry.get("title") or "").strip().lower()
    if title:
        return f"title:{title[:120]}"
    return None


def _iter_report_papers(payload: dict[str, Any]):
    """Walk all paper-containing fields of a paper-digest report payload."""
    for bucket in ("top_picks", "extended_reading"):
        for entry in payload.get(bucket) or []:
            if isinstance(entry, dict):
                yield entry, bucket
    category_hits = payload.get("category_hits") or {}
    if isinstance(category_hits, dict):
        for category, entries in category_hits.items():
            for entry in entries or []:
                if isinstance(entry, dict):
                    yield entry, f"category_hits:{category}"


def build_context(
    *,
    root: str | Path | None = None,
    state_file: str | Path | None = None,
    seed_file: str | Path | None = None,
) -> dict[str, Any]:
    seed_path = resolve_seed_path(seed_file)
    state_path = resolve_state_path(root, state_file)
    seed = _load_yaml(seed_path)
    state = _load_state(state_path)
    seen = state.get("seen") or {}
    tracked_runtime = state.get("tracked_labs_runtime") or {}
    synced_seed = state.get("synced_seed") or {}
    return {
        "seed_path": str(seed_path),
        "state_path": str(state_path),
        "seen_count": len(seen),
        "seen_keys": sorted(seen.keys()),
        "seen_sample": dict(list(seen.items())[:10]),
        "tracked_labs_runtime": list(tracked_runtime.values()),
        "synced_seed_labs": list(synced_seed.values()),
        "seed_groups": seed.get("groups") or {},
    }


def _within_last_days(last_seen: str, *, today: date, days: int) -> bool:
    try:
        seen_day = date.fromisoformat(last_seen)
    except ValueError:
        return False
    return (today.toordinal() - seen_day.toordinal()) < days


def _prune_seen(seen: dict[str, dict[str, Any]], *, today: date, days: int) -> int:
    dropped = 0
    for key in list(seen.keys()):
        entry = seen.get(key) or {}
        last_seen = str(entry.get("last_seen_date") or "")
        if not last_seen or not _within_last_days(last_seen, today=today, days=days):
            seen.pop(key, None)
            dropped += 1
    return dropped


def record_report(
    *,
    report_json: str | Path,
    root: str | Path | None = None,
    state_file: str | Path | None = None,
    seed_file: str | Path | None = None,
    as_of: date | None = None,
    prune_days: int = DEFAULT_PRUNE_DAYS,
) -> dict[str, Any]:
    today = as_of or current_local_date()
    report_path = Path(report_json).expanduser().resolve()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if payload.get("domain") != "paper-digest":
        raise ValueError("record only supports paper-digest reports")

    state_path = resolve_state_path(root, state_file)
    state = _load_state(state_path)
    seen: dict[str, dict[str, Any]] = dict(state.get("seen") or {})
    tracked_runtime: dict[str, dict[str, Any]] = dict(state.get("tracked_labs_runtime") or {})

    added_keys: list[str] = []
    updated_keys: list[str] = []

    for entry, bucket in _iter_report_papers(payload):
        key = _paper_key(entry)
        if key is None:
            continue
        existing = seen.get(key)
        ranking_score = float(entry.get("ranking_score") or 0.0)
        record = {
            "key": key,
            "arxiv_id": str(entry.get("arxiv_id") or "").strip(),
            "doi": str(entry.get("doi") or "").strip(),
            "s2_paper_id": str(entry.get("s2_paper_id") or "").strip(),
            "title": str(entry.get("title") or "").strip(),
            "arxiv_url": str(entry.get("arxiv_url") or "").strip(),
            "first_seen_date": today.isoformat(),
            "last_seen_date": today.isoformat(),
            "appearances": 1,
            "best_rank_bucket": bucket,
            "best_ranking_score": ranking_score,
        }
        if existing:
            record["first_seen_date"] = existing.get("first_seen_date") or today.isoformat()
            record["appearances"] = int(existing.get("appearances", 0) or 0) + 1
            prev_score = float(existing.get("best_ranking_score", 0) or 0)
            if prev_score >= ranking_score:
                record["best_ranking_score"] = prev_score
                record["best_rank_bucket"] = existing.get("best_rank_bucket", bucket)
            updated_keys.append(key)
        else:
            added_keys.append(key)
        seen[key] = record

    for lab in payload.get("tracked_labs_seen") or []:
        if not isinstance(lab, dict):
            continue
        lab_id = str(lab.get("lab_id") or lab.get("name") or "").strip().lower().replace(" ", "-")
        if not lab_id:
            continue
        existing = tracked_runtime.get(lab_id) or {}
        tracked_runtime[lab_id] = {
            "lab_id": lab_id,
            "name": str(lab.get("name") or existing.get("name", "")).strip(),
            "s2_affiliation_match": list(lab.get("s2_affiliation_match") or existing.get("s2_affiliation_match") or []),
            "aliases": list(lab.get("aliases") or existing.get("aliases") or []),
            "first_seen_date": existing.get("first_seen_date") or today.isoformat(),
            "last_seen_date": today.isoformat(),
            "observation_count": int(existing.get("observation_count", 0) or 0) + 1,
            "group_hint": str(lab.get("group") or existing.get("group_hint", "")).strip(),
        }

    dropped = _prune_seen(seen, today=today, days=prune_days)

    state.update(
        {
            "updated_at": datetime.now().astimezone().isoformat(),
            "report_timezone": resolve_report_timezone_label(),
            "seen": seen,
            "tracked_labs_runtime": tracked_runtime,
        }
    )
    atomic_write_text(state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    return {
        "state_path": str(state_path),
        "added_keys": added_keys,
        "updated_keys": updated_keys,
        "seen_count": len(seen),
        "dropped_stale": dropped,
        "tracked_labs_count": len(tracked_runtime),
    }


def prune_state(
    *,
    root: str | Path | None = None,
    state_file: str | Path | None = None,
    days: int = DEFAULT_PRUNE_DAYS,
    as_of: date | None = None,
) -> dict[str, Any]:
    today = as_of or current_local_date()
    state_path = resolve_state_path(root, state_file)
    state = _load_state(state_path)
    seen = dict(state.get("seen") or {})
    dropped = _prune_seen(seen, today=today, days=days)
    state.update(
        {
            "updated_at": datetime.now().astimezone().isoformat(),
            "seen": seen,
        }
    )
    atomic_write_text(state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    return {"state_path": str(state_path), "seen_count": len(seen), "dropped": dropped}


def sync_repo_seed(
    *,
    root: str | Path | None = None,
    state_file: str | Path | None = None,
    seed_file: str | Path | None = None,
    min_observations: int = 2,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Promote tracked_labs_runtime entries with enough observations into the repo seed YAML."""
    if yaml is None:
        raise RuntimeError("PyYAML is required for sync-seed; install with `pip install pyyaml`.")
    seed_path = resolve_seed_path(seed_file)
    state_path = resolve_state_path(root, state_file)
    seed = _load_yaml(seed_path)
    state = _load_state(state_path)
    tracked_runtime: dict[str, dict[str, Any]] = dict(state.get("tracked_labs_runtime") or {})
    synced_seed: dict[str, dict[str, Any]] = dict(state.get("synced_seed") or {})
    groups = seed.setdefault("groups", {})

    synced_ids: list[str] = []
    for lab_id, item in list(tracked_runtime.items()):
        if int(item.get("observation_count", 0) or 0) < min_observations:
            continue
        group_slug = item.get("group_hint") or "systems-labs"
        group_data = groups.setdefault(
            group_slug,
            {"label": group_slug, "description": "auto-promoted runtime labs", "labs": []},
        )
        labs_list = group_data.setdefault("labs", [])
        if not any(existing.get("lab_id") == lab_id for existing in labs_list):
            labs_list.append(
                {
                    "lab_id": lab_id,
                    "name": item.get("name", ""),
                    "s2_affiliation_match": item.get("s2_affiliation_match", []),
                    "aliases": item.get("aliases", []),
                }
            )
        synced_entry = dict(item)
        synced_entry["status"] = "synced_seed"
        synced_seed[lab_id] = synced_entry
        tracked_runtime.pop(lab_id, None)
        synced_ids.append(lab_id)

    result = {
        "seed_path": str(seed_path),
        "state_path": str(state_path),
        "synced_ids": sorted(synced_ids),
        "dry_run": dry_run,
    }
    if dry_run:
        return result

    seed["version"] = 1
    atomic_write_text(seed_path, yaml.safe_dump(seed, allow_unicode=True, sort_keys=False))
    state["updated_at"] = datetime.now().astimezone().isoformat()
    state["report_timezone"] = resolve_report_timezone_label()
    state["tracked_labs_runtime"] = tracked_runtime
    state["synced_seed"] = synced_seed
    atomic_write_text(state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the paper-digest seen-pool + tracked labs runtime state")
    subparsers = parser.add_subparsers(dest="command", required=True)

    context_cmd = subparsers.add_parser("context", help="load current seen-pool context")
    context_cmd.add_argument("--root")
    context_cmd.add_argument("--state-file")
    context_cmd.add_argument("--seed-file")

    record_cmd = subparsers.add_parser("record", help="record papers + tracked labs from a report JSON")
    record_cmd.add_argument("--report-json", required=True)
    record_cmd.add_argument("--root")
    record_cmd.add_argument("--state-file")
    record_cmd.add_argument("--seed-file")
    record_cmd.add_argument("--as-of")
    record_cmd.add_argument("--prune-days", type=int, default=DEFAULT_PRUNE_DAYS)

    prune_cmd = subparsers.add_parser("prune", help="drop seen entries older than N days")
    prune_cmd.add_argument("--root")
    prune_cmd.add_argument("--state-file")
    prune_cmd.add_argument("--days", type=int, default=DEFAULT_PRUNE_DAYS)
    prune_cmd.add_argument("--as-of")

    sync_cmd = subparsers.add_parser("sync-seed", help="promote tracked runtime labs back into the seed YAML")
    sync_cmd.add_argument("--root")
    sync_cmd.add_argument("--state-file")
    sync_cmd.add_argument("--seed-file")
    sync_cmd.add_argument("--min-observations", type=int, default=2)
    sync_cmd.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "context":
        print(
            json.dumps(
                build_context(root=args.root, state_file=args.state_file, seed_file=args.seed_file),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "record":
        as_of = date.fromisoformat(args.as_of) if args.as_of else None
        print(
            json.dumps(
                record_report(
                    report_json=args.report_json,
                    root=args.root,
                    state_file=args.state_file,
                    seed_file=args.seed_file,
                    as_of=as_of,
                    prune_days=args.prune_days,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "prune":
        as_of = date.fromisoformat(args.as_of) if args.as_of else None
        print(
            json.dumps(
                prune_state(
                    root=args.root,
                    state_file=args.state_file,
                    days=args.days,
                    as_of=as_of,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "sync-seed":
        print(
            json.dumps(
                sync_repo_seed(
                    root=args.root,
                    state_file=args.state_file,
                    seed_file=args.seed_file,
                    min_observations=args.min_observations,
                    dry_run=args.dry_run,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
