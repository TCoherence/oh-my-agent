#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    print("[ERROR] Missing dependency: PyYAML")
    print(
        "Run with project venv, e.g. "
        "`./.venv/bin/python skills/scheduler/scripts/validate_automations.py ~/.oh-my-agent/automations`"
    )
    raise SystemExit(1)

try:
    from oh_my_agent.automation.scheduler import _parse_cron_expression
except Exception:
    _parse_cron_expression = None


def _err(msg: str) -> None:
    print(f"[ERROR] {msg}")


def _warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def _ok(msg: str) -> None:
    print(f"[OK] {msg}")


def _validate_cron(raw: object) -> str | None:
    if raw is None:
        return None
    cron = str(raw).strip()
    if not cron:
        return "cron must be a non-empty string"
    if _parse_cron_expression is not None:
        try:
            _parse_cron_expression(cron)
        except Exception as exc:
            return str(exc)
        return None
    if len(cron.split()) != 5:
        return "cron must be a 5-field expression: minute hour day month weekday"
    return None


def _validate_file(path: Path) -> tuple[list[str], list[str], str | None]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"{path}: failed to read YAML: {exc}"], warnings, None
    if not isinstance(data, dict):
        return [f"{path}: file must contain a YAML mapping"], warnings, None

    name = str(data.get("name", "")).strip() or None
    if name is None:
        errors.append(f"{path}: missing required key: name")
    for key in ("platform", "channel_id", "prompt"):
        value = data.get(key)
        if value is None or str(value).strip() == "":
            errors.append(f"{path}: missing required key: {key}")

    delivery = str(data.get("delivery", "channel")).strip().lower()
    if delivery not in {"channel", "dm"}:
        errors.append(f"{path}: delivery must be 'channel' or 'dm'")
    if delivery == "dm":
        target = data.get("target_user_id")
        if target is None or str(target).strip() == "":
            warnings.append(
                f"{path}: delivery='dm' without target_user_id relies on runtime owner fallback"
            )

    cron = data.get("cron")
    interval = data.get("interval_seconds")
    if cron not in (None, "") and interval not in (None, ""):
        errors.append(f"{path}: cron and interval_seconds are mutually exclusive")
    elif cron in (None, "") and interval in (None, ""):
        errors.append(f"{path}: one of cron or interval_seconds is required")
    elif cron not in (None, ""):
        cron_error = _validate_cron(cron)
        if cron_error is not None:
            errors.append(f"{path}: {cron_error}")
        if data.get("initial_delay_seconds") not in (None, ""):
            errors.append(f"{path}: initial_delay_seconds is not supported with cron")
    else:
        try:
            interval_value = int(interval)
            if interval_value <= 0:
                errors.append(f"{path}: interval_seconds must be > 0")
        except Exception:
            errors.append(f"{path}: interval_seconds must be an integer")

    if data.get("initial_delay_seconds") not in (None, ""):
        try:
            delay = int(data["initial_delay_seconds"])
            if delay < 0:
                errors.append(f"{path}: initial_delay_seconds must be >= 0")
        except Exception:
            errors.append(f"{path}: initial_delay_seconds must be an integer")

    return errors, warnings, name


def _iter_automation_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        return []
    return sorted([*path.glob("*.yaml"), *path.glob("*.yml")], key=lambda item: str(item))


def main() -> int:
    raw_path = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / ".oh-my-agent" / "automations"
    target = raw_path.resolve()
    if raw_path.exists() and not raw_path.is_dir() and not raw_path.is_file():
        _err(f"unsupported path: {raw_path}")
        return 1

    files = _iter_automation_files(raw_path)
    if not files:
        _ok(f"validated 0 automation file(s) from {target}")
        return 0

    errors: list[str] = []
    warnings: list[str] = []
    paths_by_name: dict[str, list[Path]] = {}
    for path in files:
        file_errors, file_warnings, name = _validate_file(path)
        errors.extend(file_errors)
        warnings.extend(file_warnings)
        if name is not None:
            paths_by_name.setdefault(name, []).append(path)

    for name, paths in sorted(paths_by_name.items()):
        if len(paths) > 1:
            joined = ", ".join(str(path) for path in paths)
            errors.append(f"duplicate automation name {name!r}: {joined}")

    for warning in warnings:
        _warn(warning)
    if errors:
        for error in errors:
            _err(error)
        return 1

    _ok(f"validated {len(files)} automation file(s) from {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
