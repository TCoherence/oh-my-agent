"""``oma-dashboard`` entry point.

Loads ``config.yaml`` via the same ``oh_my_agent.config.load_config`` path
the bot uses, builds the FastAPI app, runs ``uvicorn``.

Loopback-only by deployment convention. The default bind is ``127.0.0.1``;
inside Docker the user is expected to bind ``0.0.0.0`` and publish only on
host ``127.0.0.1`` via compose port mapping. There is no auth — see
``docs/EN/monitoring.md`` for the security boundary discussion.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Sequence

from oh_my_agent.config import load_config

logger = logging.getLogger("oh_my_agent.dashboard.cli")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oma-dashboard",
        description="Read-only monitoring dashboard for oh-my-agent.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to oh-my-agent config.yaml. "
        "Resolution order: --config flag → OMA_CONFIG_PATH env var → ./config.yaml.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1). Use 0.0.0.0 inside Docker; "
        "publish only on host loopback via compose port mapping.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Bind port (default: 8080). Pass 0 to let the OS assign a free port.",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=("debug", "info", "warning", "error"),
        help="uvicorn log level (default: info).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the exit code (0 on clean shutdown)."""

    parser = _build_parser()
    args = parser.parse_args(argv)

    # Resolution order: --config > OMA_CONFIG_PATH > ./config.yaml.
    # argparse's default kwarg can't express that — keep default=None and
    # resolve here so the env var actually kicks in.
    config_path_str = args.config or os.environ.get("OMA_CONFIG_PATH") or "config.yaml"
    config_path = Path(config_path_str).expanduser()
    if not config_path.is_absolute():
        config_path = config_path.resolve()

    if not config_path.exists():
        print(f"oma-dashboard: config not found at {config_path}", file=sys.stderr)
        return 2

    try:
        config = load_config(config_path)
    except Exception as exc:
        print(f"oma-dashboard: failed to load config {config_path}: {exc}", file=sys.stderr)
        return 2

    # Lazy import — fastapi/uvicorn are optional deps, surface a clear error
    # before launching if they're not installed.
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print(
            "oma-dashboard: fastapi/uvicorn not installed.\n"
            "  pip install -e '.[dashboard]'  # for editable installs\n"
            "  or pip install fastapi uvicorn jinja2",
            file=sys.stderr,
        )
        return 2

    from .app import create_app

    app = create_app(config)
    print(
        f"oma-dashboard: serving on http://{args.host}:{args.port} "
        f"(config={config_path}, log_level={args.log_level})"
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


if __name__ == "__main__":
    sys.exit(main())
