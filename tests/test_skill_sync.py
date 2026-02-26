import pytest
from pathlib import Path
from oh_my_agent.skills.skill_sync import SkillSync


@pytest.fixture
def skill_dir(tmp_path):
    """Create a temporary skill directory with a sample skill."""
    skill = tmp_path / "skills" / "test-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: A test skill\n---\n\n# Test Skill\n"
    )
    (skill / "scripts").mkdir()
    (skill / "scripts" / "run.sh").write_text("#!/bin/bash\necho hello\n")
    return tmp_path / "skills"


def test_sync_creates_symlinks(skill_dir, tmp_path):
    project_root = tmp_path
    syncer = SkillSync(skills_path=skill_dir, project_root=project_root)
    count = syncer.sync()

    assert count == 1

    gemini_link = project_root / ".gemini" / "skills" / "test-skill"
    claude_link = project_root / ".claude" / "skills" / "test-skill"

    assert gemini_link.is_symlink()
    assert claude_link.is_symlink()
    assert (gemini_link / "SKILL.md").exists()
    assert (claude_link / "SKILL.md").exists()


def test_sync_skips_dirs_without_skill_md(tmp_path):
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    (skill_dir / "no-skill-md").mkdir()

    syncer = SkillSync(skills_path=skill_dir, project_root=tmp_path)
    count = syncer.sync()
    assert count == 0


def test_sync_idempotent(skill_dir, tmp_path):
    syncer = SkillSync(skills_path=skill_dir, project_root=tmp_path)
    syncer.sync()
    # Run again — should not raise
    count = syncer.sync()
    assert count == 1


def test_sync_no_skills_dir(tmp_path):
    syncer = SkillSync(skills_path=tmp_path / "nonexistent", project_root=tmp_path)
    count = syncer.sync()
    assert count == 0
