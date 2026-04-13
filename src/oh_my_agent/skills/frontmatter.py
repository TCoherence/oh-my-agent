from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SkillExecutionLimits:
    timeout_seconds: int | None = None
    max_turns: int | None = None


def read_skill_frontmatter(skill_md: Path) -> dict[str, Any]:
    try:
        content = skill_md.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    meta = yaml.safe_load(parts[1]) or {}
    return meta if isinstance(meta, dict) else {}


def resolve_skill_frontmatter(
    skill_name: str | None,
    *,
    repo_root: Path,
    skills_path: Path | None = None,
) -> dict[str, Any]:
    if not skill_name:
        return {}
    candidates: list[Path] = []
    if isinstance(skills_path, Path):
        candidates.append(skills_path / skill_name / "SKILL.md")
    candidates.append(repo_root / "skills" / skill_name / "SKILL.md")
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists():
            return read_skill_frontmatter(candidate)
    return {}


def skill_execution_limits(frontmatter: dict[str, Any]) -> SkillExecutionLimits:
    metadata = frontmatter.get("metadata")
    timeout_value = metadata.get("timeout_seconds") if isinstance(metadata, dict) else None
    if timeout_value is None:
        timeout_value = frontmatter.get("timeout_seconds")

    max_turns_value = metadata.get("max_turns") if isinstance(metadata, dict) else None
    if max_turns_value is None:
        max_turns_value = frontmatter.get("max_turns")

    return SkillExecutionLimits(
        timeout_seconds=_positive_int(timeout_value),
        max_turns=_positive_int(max_turns_value),
    )


def _positive_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
