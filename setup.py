"""Custom build hook to run the React frontend build before packaging.

Co-exists with ``pyproject.toml`` (which holds all project metadata and
declares ``setuptools.build_meta`` as the build backend). This file
provides ONLY a cmdclass override so the frontend bundle lands in
``src/oh_my_agent/dashboard/web_dist/`` before ``build_py`` copies it
into the wheel.

Skip conditions (each independently disables the hook so dev installs
without node still work):

- ``OMA_SKIP_FRONTEND=1`` env var → operator explicitly opts out
  (used by CI when the frontend has already been built in a separate
  step to avoid double work)
- ``dashboard-web/`` source directory absent → e.g. running build from
  an sdist that didn't include the frontend sources

When skipped, the wheel still installs cleanly. ``dashboard/app.py``
checks for ``web_dist/index.html`` at runtime and falls back to the
legacy Jinja monitoring page at ``/`` when the SPA isn't present.
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
    """Runs ``pnpm install + pnpm build`` (or npm fallback) before build_py."""

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

        # Prefer pnpm (per plan) when available, fall back to npm. The
        # local install path requires nothing beyond Node.js, which any
        # contributor running ``pip install -e .`` likely already has.
        installer = self._pick_installer()
        if installer is None:
            print(
                "[setup] neither pnpm nor npm found on PATH — skipping "
                "frontend build. Install Node.js or set OMA_SKIP_FRONTEND=1 "
                "to silence this.",
                file=sys.stderr,
            )
            return

        install_cmd, build_cmd = installer
        print(f"[setup] running {install_cmd[0]} install / build in {WEB_SRC}", file=sys.stderr)
        try:
            subprocess.run(install_cmd, cwd=WEB_SRC, check=True)
            subprocess.run(build_cmd, cwd=WEB_SRC, check=True)
        except subprocess.CalledProcessError as exc:
            # Don't fail the whole install on a frontend build error —
            # the operator gets a wheel with the legacy Jinja fallback,
            # and ``npm run build`` from dashboard-web/ later fixes it.
            print(
                f"[setup] frontend build failed: {exc} — installing without SPA",
                file=sys.stderr,
            )

    @staticmethod
    def _pick_installer() -> tuple[list[str], list[str]] | None:
        # Returns (install_cmd, build_cmd) or None if no installer found.
        if shutil.which("pnpm"):
            return (
                ["pnpm", "install", "--frozen-lockfile"],
                ["pnpm", "run", "build"],
            )
        if shutil.which("npm"):
            # ``npm ci`` would be faster + reproducible, but it requires
            # a clean package-lock.json + node_modules absent. Use
            # ``npm install`` which is more forgiving for dev installs.
            return (
                ["npm", "install", "--no-audit", "--no-fund"],
                ["npm", "run", "build"],
            )
        return None


setup(cmdclass={"build_py": BuildPyWithFrontend})
