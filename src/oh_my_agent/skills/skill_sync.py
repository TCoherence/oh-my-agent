from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_CODEX_SKILLS_DIR = Path(".agents") / "skills"
_LEGACY_CODEX_SKILLS_DIR = Path(".codex") / "skills"


class SkillSync:
    """Bidirectional skill sync between ``skills/`` and CLI-native directories.

    **Forward sync** (default): symlinks each skill from ``skills/`` into
    ``.gemini/skills/``, ``.claude/skills/``, and ``.agents/skills/`` so
    each CLI agent discovers them through its native repo/workspace path.

    **Reverse sync**: detects new skills created by CLI agents in their
    native directories (non-symlink folders containing ``SKILL.md``) and
    copies them back to ``skills/`` so they become the canonical source.
    """

    def __init__(
        self,
        skills_path: str | Path = "skills",
        project_root: str | Path | None = None,
    ) -> None:
        self._project_root = Path(project_root).resolve() if project_root else Path.cwd()
        raw_skills_path = Path(skills_path).expanduser()
        if raw_skills_path.is_absolute():
            self._skills_path = raw_skills_path.resolve()
        else:
            self._skills_path = (self._project_root / raw_skills_path).resolve()
        self._state_filename = ".oh-my-agent-state.json"
        # Content-hash state memo keyed by a cheap stat-only fingerprint.
        # See _workspace_source_state() for the staleness argument.
        self._source_state_cache: tuple[str, dict[str, str]] | None = None

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    @property
    def skills_path(self) -> Path:
        """Resolved canonical ``skills/`` directory (read-only)."""
        return self._skills_path

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
            self._project_root / _CODEX_SKILLS_DIR,
        ]

        for target_dir in targets:
            target_dir.mkdir(parents=True, exist_ok=True)
            for skill_dir in skills:
                link = target_dir / skill_dir.name
                self._ensure_symlink(skill_dir, link)

        if skills:
            logger.info(
                "Synced %d skill(s) to .gemini/skills/, .claude/skills/, and .agents/skills/: %s",
                len(skills),
                [s.name for s in skills],
            )
        return len(skills)

    def find_new_skills(self, extra_source_dirs: list[Path] | None = None) -> list[str]:
        """Detect new skills in CLI dirs that are not yet in ``skills/``.

        Scans ``.claude/skills/``, ``.gemini/skills/``, ``.agents/skills/``,
        and any *extra_source_dirs*
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
            self._project_root / _CODEX_SKILLS_DIR,
            self._project_root / _LEGACY_CODEX_SKILLS_DIR,
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
            self._project_root / _CODEX_SKILLS_DIR,
            self._project_root / _LEGACY_CODEX_SKILLS_DIR,
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

        workspace_roots = sorted(
            {
                target_dir.parent.parent
                for target_dir in workspace_target_dirs
                if target_dir.name == "skills" and target_dir.parent.name in {".claude", ".gemini", ".agents", ".codex"}
            },
            key=lambda path: str(path),
        )
        for workspace_root in workspace_roots:
            self.refresh_workspace(workspace_root)

        if skills:
            logger.info(
                "Refreshed %d skill(s) into workspace directories: %s",
                len(skills),
                [str(p) for p in workspace_target_dirs],
            )
        return len(skills)

    def refresh_workspace(self, workspace_root: Path, *, write_agents_md: bool = True) -> Path:
        """Refresh workspace skills and regenerate AGENTS.md as one unit.

        Pass ``write_agents_md=False`` when the caller will write its own
        ``AGENTS.md`` (e.g. ``boot._refresh_workspace_hint_files`` when
        ``WORKSPACE_AGENTS.md`` is present). Avoids a double-write on every
        boot — the SkillSync-generated content would only get clobbered.
        """
        workspace_root.mkdir(parents=True, exist_ok=True)
        for relative in (
            Path(".claude/skills"),
            Path(".gemini/skills"),
            _CODEX_SKILLS_DIR,
        ):
            self._replace_workspace_skills_dir(workspace_root / relative)
        self._remove_path(workspace_root / ".codex")
        if write_agents_md:
            self.write_workspace_agents_md(workspace_root)
        self._write_workspace_state(workspace_root)
        return workspace_root

    def workspace_needs_refresh(self, workspace_root: Path) -> bool:
        """Return True when the workspace no longer matches current repo sources."""
        workspace_root = workspace_root.resolve()
        target_agents = workspace_root / "AGENTS.md"
        if not target_agents.exists():
            return True
        for relative in (
            Path(".claude/skills"),
            Path(".gemini/skills"),
            _CODEX_SKILLS_DIR,
        ):
            if not (workspace_root / relative).is_dir():
                return True

        state_path = workspace_root / self._state_filename
        try:
            stored = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return True
        return stored != self._workspace_source_state()

    def write_workspace_agents_md(self, workspace_root: Path) -> Path:
        """Generate a workspace-local AGENTS.md derived from the repo source file."""
        workspace_root.mkdir(parents=True, exist_ok=True)
        source_agents = self._project_root / "AGENTS.md"
        state = self._workspace_source_state()
        generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        agents_hash = state["source_agents_hash"]

        lines: list[str] = [
            "# Generated Workspace AGENTS",
            "",
            "This file is generated by oh-my-agent from the source repository AGENTS.md plus workspace-local skill metadata.",
            "Do not edit it directly. Edit the source repo AGENTS.md or workspace skills instead.",
            "",
            f"Source repo AGENTS: {source_agents}",
            f"Source repo AGENTS hash: {agents_hash[:12] if agents_hash else 'missing'}",
            f"Generated at: {generated_at}",
            "",
            "---",
            "",
        ]
        if source_agents.exists():
            try:
                lines.extend(source_agents.read_text(encoding="utf-8").rstrip().splitlines())
            except OSError:
                logger.warning("Failed to read source AGENTS.md from %s", source_agents)

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

    def _workspace_source_state(self) -> dict[str, str]:
        """Content-hash state of the sync sources, memoized per instance.

        Hashing every byte under ``skills/`` is too expensive for the hot
        per-message path, so the content hashes are cached behind a cheap
        stat-only fingerprint (relative path + size + mtime_ns of every
        source file). Any real edit changes mtime_ns (nanosecond resolution)
        or size, and adding/removing/renaming files changes the path set —
        the cache can only serve a stale state if a file is rewritten while
        deliberately preserving both size and mtime_ns, which no editor or
        git operation does. The persisted workspace state stays content-hash
        based, so existing ``.oh-my-agent-state.json`` files remain valid.
        """
        fingerprint = self._source_fingerprint()
        cached = self._source_state_cache
        if cached is not None and cached[0] == fingerprint:
            return dict(cached[1])
        state = {
            "source_agents_hash": self._hash_file(self._project_root / "AGENTS.md"),
            "canonical_skills_hash": self._hash_skills_tree(self._skills_path),
        }
        self._source_state_cache = (fingerprint, dict(state))
        return state

    def _source_fingerprint(self) -> str:
        """Stat-only digest over exactly the files _workspace_source_state hashes."""
        digest = hashlib.sha256()
        agents_md = self._project_root / "AGENTS.md"
        try:
            st = agents_md.stat()
            digest.update(f"agents:{st.st_size}:{st.st_mtime_ns}\n".encode("utf-8"))
        except OSError:
            digest.update(b"agents:missing\n")
        for skill_dir in self._collect_skills(self._skills_path):
            digest.update(f"dir:{skill_dir.name}\n".encode("utf-8"))
            for child in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
                try:
                    st = child.stat()
                except OSError:
                    continue
                rel = child.relative_to(self._skills_path).as_posix()
                digest.update(f"{rel}:{st.st_size}:{st.st_mtime_ns}\n".encode("utf-8"))
        return digest.hexdigest()

    def _write_workspace_state(self, workspace_root: Path) -> None:
        state_path = workspace_root / self._state_filename
        state_path.write_text(
            json.dumps(self._workspace_source_state(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _replace_workspace_skills_dir(self, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        for child in list(target_dir.iterdir()):
            self._remove_path(child)

        for skill_dir in self._collect_skills(self._skills_path):
            dest = target_dir / skill_dir.name
            shutil.copytree(skill_dir, dest)

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

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
    def _hash_file(path: Path) -> str:
        if not path.exists() or not path.is_file():
            return ""
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _hash_skills_tree(cls, root: Path) -> str:
        digest = hashlib.sha256()
        if not root.exists() or not root.is_dir():
            return ""
        for skill_dir in cls._collect_skills(root):
            digest.update(f"dir:{skill_dir.name}\n".encode("utf-8"))
            for child in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
                digest.update(f"path:{child.relative_to(root).as_posix()}\n".encode("utf-8"))
                with child.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        digest.update(chunk)
        return digest.hexdigest()
