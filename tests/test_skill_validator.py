"""Tests for SkillValidator and ValidationResult."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
import yaml

from oh_my_agent.skills.validator import SkillValidator, ValidationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_skill(
    tmp_path: Path,
    name: str = "test-skill",
    *,
    with_skill_md: bool = True,
    frontmatter: dict | None = None,
    body: str = "# Test Skill\n\nContent.\n",
    scripts: list[tuple[str, str, bool]] | None = None,
) -> Path:
    """Create a minimal skill directory under *tmp_path*.

    Args:
        name: Directory name.
        with_skill_md: Whether to create SKILL.md at all.
        frontmatter: Dict for YAML frontmatter. Defaults to name+description.
        body: Markdown body after the frontmatter.
        scripts: List of (filename, content, executable) tuples.

    Returns:
        Path to the skill directory.
    """
    skill_dir = tmp_path / name
    skill_dir.mkdir()

    if with_skill_md:
        if frontmatter is None:
            frontmatter = {"name": name, "description": f"Test skill: {name}"}

        fm_dump = yaml.dump(frontmatter, default_flow_style=False) if frontmatter else "{}\n"
        content = f"---\n{fm_dump}---\n\n{body}"
        (skill_dir / "SKILL.md").write_text(content)

    if scripts:
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        for filename, content, executable in scripts:
            f = scripts_dir / filename
            f.write_text(content)
            if executable:
                f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return skill_dir


@pytest.fixture
def validator() -> SkillValidator:
    return SkillValidator()


# ---------------------------------------------------------------------------
# ValidationResult unit tests
# ---------------------------------------------------------------------------

class TestValidationResult:
    def test_valid_with_no_errors(self):
        r = ValidationResult(skill_name="x", errors=[], warnings=["something"])
        assert r.valid is True

    def test_invalid_when_errors_present(self):
        r = ValidationResult(skill_name="x", errors=["oops"])
        assert r.valid is False

    def test_valid_no_errors_no_warnings(self):
        r = ValidationResult(skill_name="x")
        assert r.valid is True
        assert r.errors == []
        assert r.warnings == []


# ---------------------------------------------------------------------------
# SkillValidator: SKILL.md checks
# ---------------------------------------------------------------------------

class TestSkillMdChecks:
    def test_valid_skill(self, validator, tmp_path):
        skill_dir = _make_skill(tmp_path)
        result = validator.validate(skill_dir)
        assert result.valid
        assert result.errors == []
        assert result.warnings == []

    def test_missing_skill_md_is_error(self, validator, tmp_path):
        skill_dir = tmp_path / "no-md"
        skill_dir.mkdir()
        result = validator.validate(skill_dir)
        assert not result.valid
        assert any("SKILL.md" in e for e in result.errors)

    def test_missing_name_field_is_error(self, validator, tmp_path):
        skill_dir = _make_skill(tmp_path, frontmatter={"description": "No name here"})
        result = validator.validate(skill_dir)
        assert not result.valid
        assert any("name" in e for e in result.errors)

    def test_missing_description_field_is_error(self, validator, tmp_path):
        skill_dir = _make_skill(tmp_path, frontmatter={"name": "test"})
        result = validator.validate(skill_dir)
        assert not result.valid
        assert any("description" in e for e in result.errors)

    def test_no_frontmatter_is_error(self, validator, tmp_path):
        skill_dir = tmp_path / "no-fm"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# No Frontmatter\n\nJust content.\n")
        result = validator.validate(skill_dir)
        assert not result.valid
        assert any("frontmatter" in e for e in result.errors)

    def test_non_dict_frontmatter_is_error(self, validator, tmp_path):
        skill_dir = tmp_path / "bad-fm"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\n- list item\n---\n\nContent.\n")
        result = validator.validate(skill_dir)
        assert not result.valid
        assert any("mapping" in e for e in result.errors)

    def test_both_name_and_description_required(self, validator, tmp_path):
        skill_dir = _make_skill(tmp_path, frontmatter={})
        result = validator.validate(skill_dir)
        assert not result.valid
        error_text = " ".join(result.errors)
        assert "name" in error_text
        assert "description" in error_text

    def test_skill_name_from_dir(self, validator, tmp_path):
        skill_dir = _make_skill(tmp_path, name="my-awesome-skill")
        result = validator.validate(skill_dir)
        assert result.skill_name == "my-awesome-skill"


# ---------------------------------------------------------------------------
# SkillValidator: script checks
# ---------------------------------------------------------------------------

class TestScriptChecks:
    def test_valid_bash_script(self, validator, tmp_path):
        skill_dir = _make_skill(
            tmp_path,
            scripts=[("run.sh", "#!/bin/bash\necho hello\n", True)],
        )
        result = validator.validate(skill_dir)
        assert result.valid
        assert result.warnings == []

    def test_invalid_bash_syntax_is_warning(self, validator, tmp_path):
        skill_dir = _make_skill(
            tmp_path,
            scripts=[("run.sh", "#!/bin/bash\nif [ broken syntax\n", True)],
        )
        result = validator.validate(skill_dir)
        assert result.valid  # warning only, not blocking
        assert any("run.sh" in w for w in result.warnings)

    def test_valid_python_script(self, validator, tmp_path):
        skill_dir = _make_skill(
            tmp_path,
            scripts=[("run.py", "#!/usr/bin/env python3\nprint('hello')\n", True)],
        )
        result = validator.validate(skill_dir)
        assert result.valid
        assert result.warnings == []

    def test_invalid_python_syntax_is_warning(self, validator, tmp_path):
        skill_dir = _make_skill(
            tmp_path,
            scripts=[("run.py", "def broken syntax(\n    pass\n", True)],
        )
        result = validator.validate(skill_dir)
        assert result.valid  # warning only
        assert any("run.py" in w for w in result.warnings)

    def test_non_executable_script_is_warning(self, validator, tmp_path):
        skill_dir = _make_skill(
            tmp_path,
            scripts=[("run.sh", "#!/bin/bash\necho hi\n", False)],
        )
        result = validator.validate(skill_dir)
        assert result.valid
        assert any("executable" in w for w in result.warnings)

    def test_no_scripts_dir_is_fine(self, validator, tmp_path):
        skill_dir = _make_skill(tmp_path)
        result = validator.validate(skill_dir)
        assert result.valid
        assert result.warnings == []

    def test_multiple_scripts_checked(self, validator, tmp_path):
        skill_dir = _make_skill(
            tmp_path,
            scripts=[
                ("good.sh", "#!/bin/bash\necho ok\n", True),
                ("bad.sh", "#!/bin/bash\nif broken\n", True),
                ("helper.py", "print('ok')\n", True),
            ],
        )
        result = validator.validate(skill_dir)
        assert result.valid  # only warnings
        # bad.sh should produce a warning; good.sh and helper.py should not
        assert any("bad.sh" in w for w in result.warnings)
        assert not any("good.sh" in w for w in result.warnings)

    def test_non_script_files_in_scripts_dir_are_skipped(self, validator, tmp_path):
        skill_dir = _make_skill(tmp_path)
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "README.md").write_text("# docs\n")
        result = validator.validate(skill_dir)
        assert result.valid
        assert result.warnings == []
