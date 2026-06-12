"""Custom build hook to run the React frontend build before packaging.

Co-exists with ``pyproject.toml`` (which holds all project metadata and
declares ``setuptools.build_meta`` as the build backend). This file
provides ONLY a cmdclass override so the frontend bundle lands in
``src/oh_my_agent/dashboard/web_dist/`` before ``build_py`` copies it
into the wheel.

Behavior matrix (per skip/strict env vars):

- ``OMA_SKIP_FRONTEND=1`` → hook disabled entirely (CI test jobs that
  built the frontend separately; dev installs without Node; the Docker
  entrypoint's editable re-install at container start)
- ``OMA_REQUIRE_FRONTEND=1`` → any reason the SPA can't be built and
  verified is FATAL: missing ``dashboard-web/`` sources, missing npm, a
  failing build, or a missing ``web_dist/index.html`` after the build.
  Set by the CI ``package`` job — a release wheel must never silently
  ship without the SPA.
- neither set (default, e.g. a plain ``pip install -e .`` per README) →
  best-effort: build when possible, otherwise print a LOUD warning and
  continue, so a dev checkout without Node still installs.

When the SPA is absent, the wheel still installs cleanly —
``dashboard/app.py`` checks for ``web_dist/index.html`` at runtime and
falls back to the legacy Jinja monitoring page at ``/``.
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


def _frontend_unavailable(reason: str) -> None:
    """Fatal under OMA_REQUIRE_FRONTEND=1, loud warning otherwise."""
    if os.environ.get("OMA_REQUIRE_FRONTEND") == "1":
        raise RuntimeError(
            f"[setup] {reason} OMA_REQUIRE_FRONTEND=1 is set, so a wheel "
            "without the SPA must not be produced."
        )
    print(
        "\n[setup] " + "=" * 66 + f"\n[setup] WARNING: {reason}\n"
        "[setup] The package installs WITHOUT the dashboard SPA — the\n"
        "[setup] dashboard falls back to the legacy Jinja page at /.\n"
        "[setup] Set OMA_REQUIRE_FRONTEND=1 to make this fatal, or\n"
        "[setup] OMA_SKIP_FRONTEND=1 to silence this warning.\n"
        "[setup] " + "=" * 66 + "\n",
        file=sys.stderr,
    )


class BuildPyWithFrontend(build_py):
    """Runs ``npm ci`` + ``npm run build`` before build_py.

    Strictness is env-controlled — see the module docstring.
    """

    def run(self):  # type: ignore[override]
        self._maybe_build_frontend()
        super().run()

    def _maybe_build_frontend(self) -> None:
        if os.environ.get("OMA_SKIP_FRONTEND") == "1":
            print("[setup] OMA_SKIP_FRONTEND=1 — skipping frontend build", file=sys.stderr)
            return
        if not WEB_SRC.exists():
            # e.g. building from an sdist that doesn't include dashboard-web/.
            _frontend_unavailable(f"{WEB_SRC} not found — cannot build the SPA.")
            return

        installer = self._pick_installer()
        if installer is None:
            _frontend_unavailable(
                "npm not found on PATH — cannot build the dashboard SPA. "
                "Install Node.js (>=22) to include it."
            )
            return

        install_cmd, build_cmd = installer
        print(f"[setup] running {install_cmd[0]} install / build in {WEB_SRC}", file=sys.stderr)
        try:
            subprocess.run(install_cmd, cwd=WEB_SRC, check=True)
            subprocess.run(build_cmd, cwd=WEB_SRC, check=True)
        except subprocess.CalledProcessError as exc:
            # A *failing* build (as opposed to an unavailable toolchain) is
            # always fatal: the operator clearly intended to build the SPA.
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
