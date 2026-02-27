from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ValidationResult:
    skill_name: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return len(self.errors) == 0


class SkillValidator:
    """Validate a skill directory for correctness.

    Checks (in order):
    1. ``SKILL.md`` exists — **error** if missing.
    2. ``SKILL.md`` has valid YAML frontmatter with ``name`` and ``description`` — **error**.
    3. ``.sh`` files under ``scripts/`` pass ``bash -n`` — **warning**.
    4. ``.py`` files under ``scripts/`` pass ``python -m py_compile`` — **warning**.
    5. Script files have executable permission — **warning**.

    Strategy: warn-but-import — errors are recorded but skills are still imported.
    """

    def validate(self, skill_dir: Path) -> ValidationResult:
        """Validate *skill_dir* and return a :class:`ValidationResult`."""
        result = ValidationResult(skill_name=skill_dir.name)

        # --- Check 1: SKILL.md exists ---
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            result.errors.append("SKILL.md not found")
            return result  # Can't do further checks without SKILL.md

        # --- Check 2: Valid YAML frontmatter with name + description ---
        try:
            content = skill_md.read_text(encoding="utf-8")
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    front = parts[1]
                    meta = yaml.safe_load(front)
                    if not isinstance(meta, dict):
                        result.errors.append("SKILL.md frontmatter is not a valid YAML mapping")
                    else:
                        if "name" not in meta:
                            result.errors.append("SKILL.md frontmatter missing required 'name' field")
                        if "description" not in meta:
                            result.errors.append(
                                "SKILL.md frontmatter missing required 'description' field"
                            )
                else:
                    result.errors.append("SKILL.md has malformed frontmatter (expected closing ---)")
            else:
                result.errors.append(
                    "SKILL.md has no YAML frontmatter (content must start with ---)"
                )
        except Exception as exc:
            result.errors.append(f"SKILL.md parse error: {exc}")

        # --- Checks 3–5: scripts/ ---
        _SCRIPT_SUFFIXES = {".sh", ".py"}
        scripts_dir = skill_dir / "scripts"
        if scripts_dir.is_dir():
            for script in sorted(scripts_dir.iterdir()):
                if not script.is_file():
                    continue
                # Only check known script types; skip docs, configs, etc.
                if script.suffix not in _SCRIPT_SUFFIXES:
                    continue

                # Check 5: executable permission
                if not os.access(script, os.X_OK):
                    result.warnings.append(f"scripts/{script.name}: not executable")

                # Check 3: bash syntax
                if script.suffix == ".sh":
                    try:
                        proc = subprocess.run(
                            ["bash", "-n", str(script)],
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        if proc.returncode != 0:
                            stderr = proc.stderr.strip()
                            result.warnings.append(
                                f"scripts/{script.name}: bash syntax error: {stderr}"
                            )
                    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                        result.warnings.append(f"scripts/{script.name}: bash check failed: {exc}")

                # Check 4: python syntax
                elif script.suffix == ".py":
                    try:
                        proc = subprocess.run(
                            ["python", "-m", "py_compile", str(script)],
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        if proc.returncode != 0:
                            stderr = proc.stderr.strip()
                            result.warnings.append(
                                f"scripts/{script.name}: python syntax error: {stderr}"
                            )
                    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                        result.warnings.append(
                            f"scripts/{script.name}: python check failed: {exc}"
                        )

        return result
