#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from report_store import current_local_date, resolve_report_timezone_label, resolve_reports_root


RUNTIME_STATE_FILENAME = "ai_people_pool.json"
SEED_FILENAME = "ai_people_seed.yaml"
GROUPS = {
    "claude-code-builders",
    "openai-builders",
    "oss-ai-builders",
    "ai-generalists",
}


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
            "candidates": {},
            "tracked_runtime": {},
            "synced_seed": {},
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"state file must contain an object: {path}")
    data.setdefault("version", 1)
    data.setdefault("updated_at", "")
    data.setdefault("report_timezone", resolve_report_timezone_label())
    data.setdefault("candidates", {})
    data.setdefault("tracked_runtime", {})
    data.setdefault("synced_seed", {})
    return data


def _normalize_group(raw: str | None) -> str:
    if raw in GROUPS:
        return raw
    return "ai-generalists"


def _normalize_person(raw: dict[str, Any], *, today: date) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("person entry must be an object")
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError("person entry requires name")
    person_id = str(raw.get("person_id") or name.lower().replace(" ", "-")).strip()
    x_handle = str(raw.get("x_handle", "") or "").strip()
    group = _normalize_group(raw.get("group"))
    evidence_urls = [str(url).strip() for url in raw.get("evidence_urls", []) if str(url).strip()]
    search_terms = [str(term).strip() for term in raw.get("search_terms", []) if str(term).strip()]
    aliases = [str(alias).strip() for alias in raw.get("aliases", []) if str(alias).strip()]
    return {
        "person_id": person_id,
        "name": name,
        "x_handle": x_handle,
        "group": group,
        "role": str(raw.get("role", "")).strip(),
        "why_track": str(raw.get("why_track", "")).strip(),
        "reason": str(raw.get("reason", "")).strip(),
        "search_terms": search_terms,
        "aliases": aliases,
        "evidence_urls": evidence_urls,
        "cross_checked": bool(raw.get("cross_checked", False)),
        "promote_recommended": bool(raw.get("promote_recommended", False)),
        "mention_count": int(raw.get("mention_count", 0) or 0),
        "first_seen_date": str(raw.get("first_seen_date") or today.isoformat()),
        "last_seen_date": str(raw.get("last_seen_date") or today.isoformat()),
        "status": str(raw.get("status") or ""),
        "promotion_reason": str(raw.get("promotion_reason", "")).strip(),
        "promoted_at": str(raw.get("promoted_at", "")).strip(),
    }


def _active_seed_people(seed: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for group_slug, group_data in (seed.get("groups") or {}).items():
        if not isinstance(group_data, dict):
            continue
        for person in group_data.get("people", []) or []:
            if not isinstance(person, dict):
                continue
            item = dict(person)
            item.setdefault("group", group_slug)
            item.setdefault("status", "seed")
            items.append(item)
    return items


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
    seed_people = _active_seed_people(seed)
    tracked_runtime = list((state.get("tracked_runtime") or {}).values())
    synced_seed = list((state.get("synced_seed") or {}).values())
    candidates = list((state.get("candidates") or {}).values())
    active_tracked = seed_people + tracked_runtime + synced_seed
    tracked_by_group = Counter(item.get("group", "ai-generalists") for item in active_tracked)
    return {
        "seed_path": str(seed_path),
        "state_path": str(state_path),
        "tracked_people_groups": sorted(tracked_by_group),
        "tracked_people": active_tracked,
        "candidate_people": candidates,
        "pending_seed_sync_people": tracked_runtime,
        "candidate_queue_summary": {
            "candidate_count": len(candidates),
            "tracked_runtime_count": len(tracked_runtime),
            "synced_seed_count": len(synced_seed),
        },
    }


def _within_last_days(first_seen: str, last_seen: str, *, today: date, days: int) -> bool:
    try:
        start = date.fromisoformat(first_seen)
        end = date.fromisoformat(last_seen)
    except ValueError:
        return False
    threshold = today.toordinal() - (days - 1)
    return start.toordinal() >= threshold or end.toordinal() >= threshold


def record_report(
    *,
    report_json: str | Path,
    root: str | Path | None = None,
    state_file: str | Path | None = None,
    seed_file: str | Path | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    today = as_of or current_local_date()
    report_path = Path(report_json).expanduser().resolve()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if payload.get("domain") != "ai":
        raise ValueError("record only supports ai reports")

    seed = _load_yaml(resolve_seed_path(seed_file))
    state_path = resolve_state_path(root, state_file)
    state = _load_state(state_path)
    seed_ids = {item.get("person_id") for item in _active_seed_people(seed)}
    candidates = dict(state.get("candidates") or {})
    tracked_runtime = dict(state.get("tracked_runtime") or {})
    synced_seed = dict(state.get("synced_seed") or {})

    promoted_ids: list[str] = []
    new_candidate_ids: list[str] = []

    for raw in payload.get("new_candidate_people", []) or []:
        item = _normalize_person(raw, today=today)
        person_id = item["person_id"]
        if person_id in seed_ids or person_id in tracked_runtime or person_id in synced_seed:
            continue
        existing = candidates.get(person_id)
        if existing:
            item["mention_count"] = int(existing.get("mention_count", 0)) + 1
            item["first_seen_date"] = existing.get("first_seen_date", item["first_seen_date"])
        else:
            item["mention_count"] = max(item["mention_count"], 1)
            new_candidate_ids.append(person_id)
        item["last_seen_date"] = today.isoformat()
        item["status"] = "candidate"
        candidates[person_id] = item

    for raw in payload.get("promoted_people", []) or []:
        item = _normalize_person(raw, today=today)
        person_id = item["person_id"]
        existing = candidates.pop(person_id, None) or tracked_runtime.get(person_id, {})
        item["mention_count"] = max(int(item["mention_count"]), int(existing.get("mention_count", 0)), 1)
        item["first_seen_date"] = existing.get("first_seen_date", item["first_seen_date"])
        item["last_seen_date"] = today.isoformat()
        item["status"] = "tracked_runtime"
        item["promoted_at"] = today.isoformat()
        item["promotion_reason"] = item["reason"] or existing.get("promotion_reason") or "explicit promotion from report"
        tracked_runtime[person_id] = item
        promoted_ids.append(person_id)

    for person_id, item in list(candidates.items()):
        if not _within_last_days(item.get("first_seen_date", today.isoformat()), item.get("last_seen_date", today.isoformat()), today=today, days=14):
            continue
        if int(item.get("mention_count", 0)) >= 2 or (item.get("cross_checked") and item.get("promote_recommended")):
            promoted = dict(item)
            promoted["status"] = "tracked_runtime"
            promoted["promoted_at"] = today.isoformat()
            promoted["promotion_reason"] = promoted.get("reason") or "candidate met promotion threshold"
            tracked_runtime[person_id] = promoted
            candidates.pop(person_id, None)
            if person_id not in promoted_ids:
                promoted_ids.append(person_id)

    state.update(
        {
            "updated_at": datetime.now().astimezone().isoformat(),
            "report_timezone": resolve_report_timezone_label(),
            "candidates": candidates,
            "tracked_runtime": tracked_runtime,
            "synced_seed": synced_seed,
        }
    )
    atomic_write_text(state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    return {
        "state_path": str(state_path),
        "new_candidate_ids": sorted(new_candidate_ids),
        "promoted_ids": sorted(promoted_ids),
        "candidate_count": len(candidates),
        "tracked_runtime_count": len(tracked_runtime),
    }


def sync_repo_seed(
    *,
    root: str | Path | None = None,
    state_file: str | Path | None = None,
    seed_file: str | Path | None = None,
) -> dict[str, Any]:
    seed_path = resolve_seed_path(seed_file)
    state_path = resolve_state_path(root, state_file)
    seed = _load_yaml(seed_path)
    state = _load_state(state_path)
    tracked_runtime = dict(state.get("tracked_runtime") or {})
    synced_seed = dict(state.get("synced_seed") or {})
    groups = seed.setdefault("groups", {})

    synced_ids: list[str] = []
    for person_id, item in list(tracked_runtime.items()):
        group_slug = _normalize_group(item.get("group"))
        group_data = groups.setdefault(group_slug, {"label": group_slug, "description": "", "people": []})
        people = group_data.setdefault("people", [])
        if not any(existing.get("person_id") == person_id for existing in people):
            people.append(
                {
                    "person_id": person_id,
                    "name": item.get("name", ""),
                    "x_handle": item.get("x_handle", ""),
                    "role": item.get("role", ""),
                    "why_track": item.get("why_track") or item.get("reason", ""),
                    "search_terms": item.get("search_terms", []),
                    "aliases": item.get("aliases", []),
                }
            )
        synced = dict(item)
        synced["status"] = "synced_seed"
        synced_seed[person_id] = synced
        tracked_runtime.pop(person_id, None)
        synced_ids.append(person_id)

    seed["version"] = 1
    atomic_write_text(seed_path, yaml.safe_dump(seed, allow_unicode=True, sort_keys=False))
    state["updated_at"] = datetime.now().astimezone().isoformat()
    state["report_timezone"] = resolve_report_timezone_label()
    state["tracked_runtime"] = tracked_runtime
    state["synced_seed"] = synced_seed
    atomic_write_text(state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    return {
        "seed_path": str(seed_path),
        "state_path": str(state_path),
        "synced_ids": sorted(synced_ids),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the market-briefing AI people pool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    context_cmd = subparsers.add_parser("context", help="load the current AI people pool context")
    context_cmd.add_argument("--root")
    context_cmd.add_argument("--state-file")
    context_cmd.add_argument("--seed-file")

    record_cmd = subparsers.add_parser("record", help="record candidates and promotions from an AI report JSON")
    record_cmd.add_argument("--report-json", required=True)
    record_cmd.add_argument("--root")
    record_cmd.add_argument("--state-file")
    record_cmd.add_argument("--seed-file")
    record_cmd.add_argument("--as-of")

    sync_cmd = subparsers.add_parser("sync-repo", help="sync promoted runtime-tracked people back into the repo seed file")
    sync_cmd.add_argument("--root")
    sync_cmd.add_argument("--state-file")
    sync_cmd.add_argument("--seed-file")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.command == "context":
        print(
            json.dumps(
                build_context(
                    root=args.root,
                    state_file=args.state_file,
                    seed_file=args.seed_file,
                ),
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
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "sync-repo":
        print(
            json.dumps(
                sync_repo_seed(
                    root=args.root,
                    state_file=args.state_file,
                    seed_file=args.seed_file,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
