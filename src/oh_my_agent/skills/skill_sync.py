from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class SkillSync:
    """Sync skills from a unified directory to CLI-native skill directories.

    Both Gemini CLI and Claude CLI follow the Agent Skills standard:
    - Gemini: ``.gemini/skills/{name}/SKILL.md``
    - Claude: ``.claude/skills/{name}/SKILL.md``

    This module symlinks each skill folder from ``skills_path`` into both
    CLI directories so both agents can discover them natively.
    """

    def __init__(
        self,
        skills_path: str | Path = "skills",
        project_root: str | Path | None = None,
    ) -> None:
        self._skills_path = Path(skills_path).resolve()
        self._project_root = Path(project_root).resolve() if project_root else Path.cwd()

    def sync(self) -> int:
        """Sync all skills and return the number synced."""
        if not self._skills_path.is_dir():
            logger.info("Skills directory not found: %s — skipping", self._skills_path)
            return 0

        # Collect valid skill directories (must contain SKILL.md)
        skills = []
        for child in sorted(self._skills_path.iterdir()):
            if child.is_dir() and (child / "SKILL.md").exists():
                skills.append(child)

        if not skills:
            logger.info("No skills found in %s", self._skills_path)
            return 0

        targets = [
            self._project_root / ".gemini" / "skills",
            self._project_root / ".claude" / "skills",
        ]

        for target_dir in targets:
            target_dir.mkdir(parents=True, exist_ok=True)
            for skill_dir in skills:
                link = target_dir / skill_dir.name
                self._ensure_symlink(skill_dir, link)

        logger.info(
            "Synced %d skill(s) to .gemini/skills/ and .claude/skills/: %s",
            len(skills),
            [s.name for s in skills],
        )
        return len(skills)

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
