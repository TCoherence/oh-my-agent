
import pytest

from oh_my_agent.main import _setup_workspace
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
    codex_link = project_root / ".agents" / "skills" / "test-skill"

    assert gemini_link.is_symlink()
    assert claude_link.is_symlink()
    assert codex_link.is_symlink()
    assert (gemini_link / "SKILL.md").exists()
    assert (claude_link / "SKILL.md").exists()
    assert (codex_link / "SKILL.md").exists()


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


def test_refresh_workspace_dirs_copies_skill_dirs(skill_dir, tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Repo Rules\n\n- keep things tidy\n", encoding="utf-8")
    syncer = SkillSync(skills_path=skill_dir, project_root=tmp_path)
    workspace_targets = [
        tmp_path / "agent-workspace" / ".claude" / "skills",
        tmp_path / "agent-workspace" / ".gemini" / "skills",
        tmp_path / "agent-workspace" / ".agents" / "skills",
    ]

    count = syncer.refresh_workspace_dirs(workspace_targets)

    assert count == 1
    for target in workspace_targets:
        copied = target / "test-skill"
        assert copied.is_dir()
        assert not copied.is_symlink()
        assert (copied / "SKILL.md").exists()

    agents_md = tmp_path / "agent-workspace" / "AGENTS.md"
    assert agents_md.exists()
    content = agents_md.read_text(encoding="utf-8")
    assert content.startswith("# Generated Workspace AGENTS")
    assert "Source repo AGENTS:" in content
    assert "# Repo Rules" in content
    assert "# Workspace Extensions" not in content
    assert ".agents/skills/test-skill/SKILL.md" not in content


def test_setup_workspace_uses_agents_md_not_agent_compat_files(skill_dir, tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "AGENTS.md").write_text("# Repo Rules\n", encoding="utf-8")
    (project_root / "AGENT.md").write_text("legacy agent file\n", encoding="utf-8")
    (project_root / "CLAUDE.md").write_text("legacy claude file\n", encoding="utf-8")
    (project_root / "GEMINI.md").write_text("legacy gemini file\n", encoding="utf-8")

    workspace = _setup_workspace(str(tmp_path / "workspace"), project_root, skill_dir)

    assert (workspace / "AGENTS.md").exists()
    assert not (workspace / "AGENT.md").exists()
    assert not (workspace / "CLAUDE.md").exists()
    assert not (workspace / "GEMINI.md").exists()
    assert (workspace / ".agents" / "skills" / "test-skill" / "SKILL.md").exists()
    assert (workspace / ".oh-my-agent-state.json").exists()


def test_workspace_needs_refresh_when_source_agents_changes(skill_dir, tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "AGENTS.md").write_text("# Repo Rules\n\n- first version\n", encoding="utf-8")

    syncer = SkillSync(skills_path=skill_dir, project_root=project_root)
    workspace = tmp_path / "workspace"
    syncer.refresh_workspace(workspace)

    assert syncer.workspace_needs_refresh(workspace) is False

    (project_root / "AGENTS.md").write_text("# Repo Rules\n\n- second version\n", encoding="utf-8")

    assert syncer.workspace_needs_refresh(workspace) is True

    syncer.refresh_workspace(workspace)
    content = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "- second version" in content
    assert syncer.workspace_needs_refresh(workspace) is False


def test_workspace_needs_refresh_when_skill_changes(skill_dir, tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Repo Rules\n", encoding="utf-8")
    syncer = SkillSync(skills_path=skill_dir, project_root=tmp_path)
    workspace = tmp_path / "workspace"
    syncer.refresh_workspace(workspace)

    assert syncer.workspace_needs_refresh(workspace) is False

    (skill_dir / "test-skill" / "scripts" / "run.sh").write_text("#!/bin/bash\necho changed\n", encoding="utf-8")

    assert syncer.workspace_needs_refresh(workspace) is True

    syncer.refresh_workspace(workspace)
    copied = workspace / ".agents" / "skills" / "test-skill" / "scripts" / "run.sh"
    assert "changed" in copied.read_text(encoding="utf-8")
