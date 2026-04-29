
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


def test_setup_workspace_writes_workspace_agents_hint_files(skill_dir, tmp_path):
    """When ``WORKSPACE_AGENTS.md`` exists at the repo root, ``_setup_workspace``
    copies its content into ``AGENTS.md`` / ``CLAUDE.md`` / ``GEMINI.md`` so
    every CLI agent reads the same workspace-specific guidance regardless of
    which one the registry picks (claude / codex / gemini)."""
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "AGENTS.md").write_text("# Repo Rules\n", encoding="utf-8")
    workspace_hint = "# Workspace agent guide\n\nUse `$OMA_AGENT_HOME` for skills.\n"
    (project_root / "WORKSPACE_AGENTS.md").write_text(workspace_hint, encoding="utf-8")

    workspace = _setup_workspace(str(tmp_path / "workspace"), project_root, skill_dir)

    for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        target = workspace / name
        assert target.is_file(), f"missing {name}"
        assert target.read_text(encoding="utf-8") == workspace_hint


def test_setup_workspace_skips_skillsync_agents_when_workspace_agents_present(skill_dir, tmp_path):
    """When ``WORKSPACE_AGENTS.md`` exists, ``_setup_workspace`` passes
    ``write_agents_md=False`` to ``SkillSync.refresh_workspace`` so SkillSync's
    own AGENTS.md generator no longer runs — avoiding a write-then-clobber on
    every boot. ``_refresh_workspace_hint_files`` is the sole writer of
    workspace AGENTS.md / CLAUDE.md / GEMINI.md in this path."""
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "AGENTS.md").write_text("# Repo Rules\n", encoding="utf-8")
    workspace_hint = "# WS guide\n"
    (project_root / "WORKSPACE_AGENTS.md").write_text(workspace_hint, encoding="utf-8")

    workspace = _setup_workspace(str(tmp_path / "workspace"), project_root, skill_dir)

    agents_md = workspace / "AGENTS.md"
    assert agents_md.read_text(encoding="utf-8") == workspace_hint
    # SkillSync's "# Generated Workspace AGENTS" header would have been the
    # only producer of AGENTS.md before _refresh_workspace_hint_files ran;
    # confirm SkillSync is now skipped (not just shadowed).
    assert "# Generated Workspace AGENTS" not in agents_md.read_text(encoding="utf-8")


def test_setup_workspace_idempotent_with_workspace_agents(skill_dir, tmp_path):
    """Re-running ``_setup_workspace`` with unchanged WORKSPACE_AGENTS.md is a
    no-op for the hint files (avoids needless writes that would bump mtimes
    and confuse SkillSync's hash-based change detection)."""
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "AGENTS.md").write_text("# Repo Rules\n", encoding="utf-8")
    (project_root / "WORKSPACE_AGENTS.md").write_text("# WS guide\n", encoding="utf-8")

    workspace = _setup_workspace(str(tmp_path / "workspace"), project_root, skill_dir)
    first_mtime = (workspace / "CLAUDE.md").stat().st_mtime

    _setup_workspace(str(tmp_path / "workspace"), project_root, skill_dir)
    assert (workspace / "CLAUDE.md").stat().st_mtime == first_mtime


def test_setup_workspace_skips_hint_files_when_source_missing(skill_dir, tmp_path):
    """No ``WORKSPACE_AGENTS.md`` in repo root → skip hint file generation
    entirely (legacy behavior). Avoids creating empty CLAUDE.md / GEMINI.md
    that would mislead the agent about what's available."""
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "AGENTS.md").write_text("# Repo Rules\n", encoding="utf-8")

    workspace = _setup_workspace(str(tmp_path / "workspace"), project_root, skill_dir)

    assert not (workspace / "CLAUDE.md").exists()
    assert not (workspace / "GEMINI.md").exists()
    # AGENTS.md still exists from SkillSync's generation, but it's the legacy
    # "# Generated Workspace AGENTS" content.
    assert (workspace / "AGENTS.md").exists()


def test_refresh_workspace_hint_files_skips_directory_target(skill_dir, tmp_path):
    """If a workspace path that should hold a hint file is unexpectedly a
    directory (corrupted state, manual mistake, prior failed run), refuse to
    rmtree it — log + skip and leave the other two hint files writable."""
    from oh_my_agent.boot import _refresh_workspace_hint_files

    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "WORKSPACE_AGENTS.md").write_text("# WS guide\n", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "CLAUDE.md").mkdir()  # degenerate: dir at hint path

    _refresh_workspace_hint_files(project_root, workspace)

    # Directory left in place (not rmtree'd).
    assert (workspace / "CLAUDE.md").is_dir()
    # AGENTS.md and GEMINI.md still get written.
    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == "# WS guide\n"
    assert (workspace / "GEMINI.md").read_text(encoding="utf-8") == "# WS guide\n"
