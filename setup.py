"""Custom build hook to run the React frontend build before packaging.

Co-exists with ``pyproject.toml`` (which holds all project metadata and
declares ``setuptools.build_meta`` as the build backend). This file
provides ONLY a cmdclass override so the frontend bundle lands in
``src/oh_my_agent/dashboard/web_dist/`` before ``build_py`` copies it
into the wheel.

Skip conditions (each independently disables the hook):

- ``OMA_SKIP_FRONTEND=1`` env var → operator explicitly opts out
  (used by CI when the frontend has already been built in a separate
  step to avoid double work, and for dev installs without Node)
- ``dashboard-web/`` source directory absent → e.g. running build from
  an sdist that didn't include the frontend sources

When skipped, the wheel still installs cleanly. ``dashboard/app.py``
checks for ``web_dist/index.html`` at runtime and falls back to the
legacy Jinja monitoring page at ``/`` when the SPA isn't present.

Outside those skip conditions, a missing npm or a failing frontend
build is FATAL: a release wheel must never silently ship without the
SPA. ``OMA_SKIP_FRONTEND=1`` is the single explicit opt-out.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

REPO_ROOT = Path(__file__).resolve().parent
WEB_SRC = REPO_ROOT / "dashboard-web"
WEB_DIST = REPO_ROOT / "src" / "oh_my_agent" / "dashboard" / "web_dist"


class BuildPyWithFrontend(build_py):
    """Runs ``npm ci`` + ``npm run build`` before build_py.

    Fatal on missing npm or a failed build unless ``OMA_SKIP_FRONTEND=1``.
    """

    def run(self):  # type: ignore[override]
        self._maybe_build_frontend()
        super().run()

    def _maybe_build_frontend(self) -> None:
        if os.environ.get("OMA_SKIP_FRONTEND") == "1":
            print("[setup] OMA_SKIP_FRONTEND=1 — skipping frontend build", file=sys.stderr)
            return
        if not WEB_SRC.exists():
            print(
                f"[setup] {WEB_SRC} not found — skipping frontend build "
                "(legacy Jinja page will serve at /)",
                file=sys.stderr,
            )
            return

        installer = self._pick_installer()
        if installer is None:
            # Fatal: a build that quietly skips the SPA produces a wheel
            # that silently serves the degraded legacy Jinja page. Dev
            # installs without Node keep working via OMA_SKIP_FRONTEND=1.
            raise RuntimeError(
                "[setup] npm not found on PATH — cannot build the dashboard "
                "SPA. Install Node.js (>=22), or set OMA_SKIP_FRONTEND=1 to "
                "deliberately install without the SPA (dashboard falls back "
                "to the legacy Jinja page)."
            )

        install_cmd, build_cmd = installer
        print(f"[setup] running {install_cmd[0]} install / build in {WEB_SRC}", file=sys.stderr)
        try:
            subprocess.run(install_cmd, cwd=WEB_SRC, check=True)
            subprocess.run(build_cmd, cwd=WEB_SRC, check=True)
        except subprocess.CalledProcessError as exc:
            # Fatal (used to be swallowed): a release wheel silently
            # shipping without the SPA is worse than a failed build.
            raise RuntimeError(
                f"[setup] frontend build failed: {exc}. Fix the npm build, "
                "or set OMA_SKIP_FRONTEND=1 to deliberately install "
                "without the SPA."
            ) from exc
        index_html = WEB_DIST / "index.html"
        if not index_html.exists():
            raise RuntimeError(
                f"[setup] frontend build succeeded but {index_html} is "
                "missing — the Vite outDir no longer matches what "
                "packaging ships. Fix dashboard-web/vite.config.ts, or "
                "set OMA_SKIP_FRONTEND=1 to deliberately install without "
                "the SPA."
            )

    @staticmethod
    def _pick_installer() -> tuple[list[str], list[str]] | None:
        # npm only — the project is standardized on npm (committed
        # package-lock.json, ``packageManager: npm@…``, the Docker image
        # ships npm). ``npm ci`` installs strictly from the lockfile and
        # never rewrites it, which is the whole point: ``npm install``
        # silently re-normalizes peer markers across npm versions and
        # churns package-lock.json on every dev's machine.
        if shutil.which("npm"):
            return (
                ["npm", "ci", "--no-audit", "--no-fund"],
                ["npm", "run", "build"],
            )
        return None


setup(cmdclass={"build_py": BuildPyWithFrontend})
