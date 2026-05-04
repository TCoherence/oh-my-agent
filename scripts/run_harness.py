#!/usr/bin/env python3
"""Run a single harness scenario standalone (outside pytest).

Useful for one-off debugging:

    python scripts/run_harness.py tests/harness/scenarios/bilibili_cached.yaml

Real-mode requires opt-in:

    OMA_HARNESS_ALLOW_REAL=1 python scripts/run_harness.py --mode real <yaml>

Real-mode is not implemented in v1 (raises NotImplementedError); the env
gate is checked first so accidentally-omitted env vars get a clear error.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Make tests/ importable when running from a checkout without pip install.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.harness.runner import run_scenario  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a single harness scenario.")
    parser.add_argument("scenario", type=Path, help="Path to scenario YAML file")
    parser.add_argument(
        "--mode",
        choices=["stub", "real"],
        default="stub",
        help="stub uses StubAgent + StubBilibiliAuthProvider (offline). real uses live CLI agents and the real bilibili API; requires OMA_HARNESS_ALLOW_REAL=1.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable INFO-level logs")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    if not args.scenario.exists():
        print(f"scenario not found: {args.scenario}", file=sys.stderr)
        return 2

    result = asyncio.run(run_scenario(args.scenario, mode=args.mode))
    if result.passed:
        print(f"PASS: {result.name} ({len(result.events)} events recorded)")
        return 0
    print(f"FAIL: {result.name}")
    for failure in result.failures:
        print(f"  - {failure.message}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
