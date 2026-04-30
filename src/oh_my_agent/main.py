"""Thin CLI entry point.

Everything heavy (config normalization, service construction, gateway lifecycle)
lives in :mod:`oh_my_agent.boot`. This module just handles argparse and dispatch.

The private helpers are still re-exported here because existing tests import
them from ``oh_my_agent.main``; moving them with backwards-compatible aliases
avoids churning the test tree in the same change.
"""

from __future__ import annotations

import argparse
import asyncio

from oh_my_agent import __version__
from oh_my_agent.boot import (
    BootContext,
    _apply_agent_env_overrides,
    _apply_v052_defaults,
    _async_main,
    _build_agent,
    _build_channel,
    _maybe_move,
    _migrate_legacy_workspace,
    _parse_env_bool,
    _register_shutdown_signal_handlers,
    _resolve_project_path,
    _runtime_root,
    _setup_logging,
    _setup_workspace,
    _shutdown,
    _warn_if_legacy_memory_config,
    _warn_if_legacy_memory_layout,
    clear_slash_commands,
    ignite,
    verify_integrity,
)

__all__ = [
    "BootContext",
    "_apply_agent_env_overrides",
    "_apply_v052_defaults",
    "_async_main",
    "_build_agent",
    "_build_channel",
    "_maybe_move",
    "_migrate_legacy_workspace",
    "_parse_env_bool",
    "_register_shutdown_signal_handlers",
    "_resolve_project_path",
    "_runtime_root",
    "_setup_logging",
    "_setup_workspace",
    "_shutdown",
    "_warn_if_legacy_memory_config",
    "_warn_if_legacy_memory_layout",
    "clear_slash_commands",
    "ignite",
    "main",
    "verify_integrity",
]


def main() -> None:
    parser = argparse.ArgumentParser(prog="oh-my-agent")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="Path to config.yaml (overrides OMA_CONFIG_PATH env var)",
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        default=False,
        help="Validate config and exit (exit 0 = ok, exit 1 = errors)",
    )
    parser.add_argument(
        "--clear-commands",
        action="store_true",
        default=False,
        help=(
            "One-shot: clear all slash commands on the Discord application(s) "
            "the active config points at, then exit. Pair with --config to "
            "scope to a specific app (e.g. dev vs prod). Next normal start "
            "re-registers whatever commands the current code defines."
        ),
    )
    args = parser.parse_args()

    ctx = verify_integrity(args.config, validate_only=args.validate_config)
    if args.clear_commands:
        rc = asyncio.run(clear_slash_commands(ctx))
        raise SystemExit(rc)
    asyncio.run(ignite(ctx))


if __name__ == "__main__":
    main()
