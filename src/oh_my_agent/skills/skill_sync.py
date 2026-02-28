from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class SkillSync:
    """Bidirectional skill sync between ``skills/`` and CLI-native directories.

    **Forward sync** (default): symlinks each skill from ``skills/`` into
    ``.gemini/skills/`` and ``.claude/skills/`` so both CLI agents discover
    them natively.

    **Reverse sync**: detects new skills created by CLI agents in their
    native directories (non-symlink folders containing ``SKILL.md``) and
    copies them back to ``skills/`` so they become the canonical source.
    """

    def __init__(
        self,
        skills_path: str | Path = "skills",
        project_root: str | Path | None = None,
    ) -> None:
        self._skills_path = Path(skills_path).resolve()
        self._project_root = Path(project_root).resolve() if project_root else Path.cwd()

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def sync(self) -> int:
        """Run forward sync and return the number of skills synced."""
        self._skills_path.mkdir(parents=True, exist_ok=True)

        # Collect valid skill directories (must contain SKILL.md)
        skills = self._collect_skills(self._skills_path)

        if not skills:
            logger.info("No skills found in %s", self._skills_path)

        targets = [
            self._project_root / ".gemini" / "skills",
            self._project_root / ".claude" / "skills",
        ]

        for target_dir in targets:
            target_dir.mkdir(parents=True, exist_ok=True)
            for skill_dir in skills:
                link = target_dir / skill_dir.name
                self._ensure_symlink(skill_dir, link)

        if skills:
            logger.info(
                "Synced %d skill(s) to .gemini/skills/ and .claude/skills/: %s",
                len(skills),
                [s.name for s in skills],
            )
        return len(skills)

    def find_new_skills(self, extra_source_dirs: list[Path] | None = None) -> list[str]:
        """Detect new skills in CLI dirs that are not yet in ``skills/``.

        Scans ``.claude/skills/``, ``.gemini/skills/``, and any *extra_source_dirs*
        for non-symlink directories containing ``SKILL.md`` that don't already exist
        in the canonical ``skills/`` path.

        **Does not copy or sync — detection only.**  Call :meth:`full_sync` to import.

        Args:
            extra_source_dirs: Additional directories to scan (e.g. workspace CLI skill dirs).

        Returns:
            Sorted list of new skill directory names.
        """
        existing_names = (
            {d.name for d in self._skills_path.iterdir() if d.is_dir()}
            if self._skills_path.is_dir()
            else set()
        )

        sources = [
            self._project_root / ".gemini" / "skills",
            self._project_root / ".claude" / "skills",
        ]
        if extra_source_dirs:
            sources.extend(extra_source_dirs)

        new_skills: list[str] = []
        seen: set[str] = set()
        for src_dir in sources:
            if not src_dir.is_dir():
                continue
            for child in sorted(src_dir.iterdir()):
                if not child.is_dir():
                    continue
                if child.is_symlink():
                    continue
                if not (child / "SKILL.md").exists():
                    continue
                if child.name in existing_names:
                    continue
                if child.name not in seen:
                    new_skills.append(child.name)
                    seen.add(child.name)

        return sorted(new_skills)

    def reverse_sync(self, extra_source_dirs: list[Path] | None = None) -> int:
        """Copy new skills from CLI directories back to ``skills/``.

        Only copies directories that:
        - Are **not** symlinks (i.e. created by a CLI agent, not by forward sync)
        - Contain a ``SKILL.md`` file
        - Do not already exist in ``skills/``

        Args:
            extra_source_dirs: Additional source directories to import from
                (e.g. workspace CLI skill dirs).

        Returns:
            The number of skills imported.
        """
        self._skills_path.mkdir(parents=True, exist_ok=True)
        existing_names = {
            d.name
            for d in self._skills_path.iterdir()
            if d.is_dir()
        }

        sources = [
            self._project_root / ".gemini" / "skills",
            self._project_root / ".claude" / "skills",
        ]
        if extra_source_dirs:
            sources.extend(extra_source_dirs)

        imported = 0
        for src_dir in sources:
            if not src_dir.is_dir():
                continue
            for child in sorted(src_dir.iterdir()):
                if not child.is_dir():
                    continue
                # Skip symlinks — those are our own forward-sync links
                if child.is_symlink():
                    continue
                if not (child / "SKILL.md").exists():
                    continue
                if child.name in existing_names:
                    continue

                dest = self._skills_path / child.name
                shutil.copytree(child, dest)
                existing_names.add(child.name)
                imported += 1
                logger.info(
                    "Reverse-synced skill '%s' from %s → %s",
                    child.name,
                    child,
                    dest,
                )

        if imported:
            logger.info("Reverse-synced %d new skill(s) into %s", imported, self._skills_path)
        return imported

    def full_sync(self, extra_source_dirs: list[Path] | None = None) -> tuple[int, int]:
        """Run reverse sync first, then forward sync. Returns (forward, reverse) counts."""
        reverse_count = self.reverse_sync(extra_source_dirs=extra_source_dirs)
        forward_count = self.sync()
        return forward_count, reverse_count

    def refresh_workspace_dirs(self, workspace_target_dirs: list[Path] | None = None) -> int:
        """Copy canonical skills into active workspace CLI directories."""
        if not workspace_target_dirs:
            return 0

        self._skills_path.mkdir(parents=True, exist_ok=True)
        skills = self._collect_skills(self._skills_path)

        for target_dir in workspace_target_dirs:
            target_dir.mkdir(parents=True, exist_ok=True)
            for skill_dir in skills:
                dest = target_dir / skill_dir.name
                if dest.is_symlink() or dest.is_file():
                    dest.unlink()
                elif dest.is_dir():
                    shutil.rmtree(dest)
                shutil.copytree(skill_dir, dest)

        workspace_roots = sorted(
            {
                target_dir.parent.parent
                for target_dir in workspace_target_dirs
                if target_dir.name == "skills" and target_dir.parent.name in {".claude", ".gemini", ".codex"}
            },
            key=lambda path: str(path),
        )
        for workspace_root in workspace_roots:
            self.write_workspace_agents_md(workspace_root)

        if skills:
            logger.info(
                "Refreshed %d skill(s) into workspace directories: %s",
                len(skills),
                [str(p) for p in workspace_target_dirs],
            )
        return len(skills)

    def write_workspace_agents_md(self, workspace_root: Path) -> Path:
        """Generate a workspace-local AGENTS.md that references Codex-visible skills."""
        workspace_root.mkdir(parents=True, exist_ok=True)
        codex_skills_dir = workspace_root / ".codex" / "skills"
        skills = self._collect_skills(codex_skills_dir)

        lines = [
            "# Workspace AGENTS.md",
            "",
            "This workspace is generated by oh-my-agent.",
            "Use the local workspace skill references below when they are relevant to the request.",
            "",
            "## Available workspace skills",
        ]
        if skills:
            for skill_dir in skills:
                desc = self._read_skill_description(skill_dir / "SKILL.md")
                line = f"- {skill_dir.name}: {desc} (file: {skill_dir / 'SKILL.md'})"
                lines.append(line)
        else:
            lines.append("- No workspace Codex skills are currently synced.")

        lines.extend(
            [
                "",
                "## Usage notes",
                "- Prefer the workspace-local skill paths under `.codex/skills/` when using Codex in this workspace.",
                "- Claude and Gemini continue to use their native `.claude/skills/` and `.gemini/skills/` directories.",
            ]
        )

        target = workspace_root / "AGENTS.md"
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _collect_skills(directory: Path) -> list[Path]:
        """Return sorted list of skill directories containing SKILL.md."""
        if not directory.is_dir():
            return []
        return sorted(
            child
            for child in directory.iterdir()
            if child.is_dir() and (child / "SKILL.md").exists()
        )

    @staticmethod
    def _ensure_symlink(source: Path, link: Path) -> None:
        """Create or update a symlink at *link* pointing to *source*."""
        if link.is_symlink():
            if link.resolve() == source.resolve():
                return  # Already correct
            link.unlink()
        elif link.exists():
            # Non-symlink exists at target — skip to avoid data loss
            logger.warning(
                "Skipping %s: non-symlink already exists", link,
            )
            return
        link.symlink_to(source)
        logger.debug("Symlinked %s → %s", link, source)

    @staticmethod
    def _read_skill_description(skill_md: Path) -> str:
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            return "No description available."

        in_frontmatter = False
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line == "---":
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter and line.lower().startswith("description:"):
                return line.partition(":")[2].strip() or "No description available."
        return "No description available."
