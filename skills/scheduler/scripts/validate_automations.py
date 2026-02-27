#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    print("[ERROR] Missing dependency: PyYAML")
    print("Run with project venv, e.g. `./.venv/bin/python skills/scheduler/scripts/validate_automations.py config.yaml`")
    raise SystemExit(1)


def _err(msg: str) -> None:
    print(f"[ERROR] {msg}")


def _ok(msg: str) -> None:
    print(f"[OK] {msg}")


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config.yaml")
    if not path.exists():
        _err(f"config file not found: {path}")
        return 1

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    automations = data.get("automations", {})
    enabled = bool(automations.get("enabled", False))
    jobs = automations.get("jobs", [])

    if not enabled:
        _ok("automations.enabled=false (nothing to validate)")
        return 0
    if not isinstance(jobs, list):
        _err("automations.jobs must be a list")
        return 1

    required_keys = ("name", "platform", "channel_id", "prompt", "interval_seconds")
    errors: list[str] = []

    for idx, job in enumerate(jobs):
        if not isinstance(job, dict):
            errors.append(f"jobs[{idx}] must be a mapping")
            continue
        for key in required_keys:
            if key not in job:
                errors.append(f"jobs[{idx}] missing required key: {key}")
        if "interval_seconds" in job:
            try:
                interval = int(job["interval_seconds"])
                if interval <= 0:
                    errors.append(f"jobs[{idx}].interval_seconds must be > 0")
            except Exception:
                errors.append(f"jobs[{idx}].interval_seconds must be an integer")
        if "initial_delay_seconds" in job:
            try:
                delay = int(job["initial_delay_seconds"])
                if delay < 0:
                    errors.append(f"jobs[{idx}].initial_delay_seconds must be >= 0")
            except Exception:
                errors.append(f"jobs[{idx}].initial_delay_seconds must be an integer")

    if errors:
        for e in errors:
            _err(e)
        return 1

    _ok(f"validated {len(jobs)} automation job(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
