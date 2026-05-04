"""Pytest entry that discovers + runs every yaml scenario in stub mode.

CI runs this; failure here means a code change broke a regression that the
harness was specifically watching for. Each scenario is its own test case
so failures point at the exact scenario that broke.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.harness.runner import run_scenario

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
SCENARIO_FILES = sorted(SCENARIOS_DIR.glob("*.yaml"))


@pytest.mark.parametrize("scenario_path", SCENARIO_FILES, ids=lambda p: p.stem)
@pytest.mark.asyncio
async def test_scenario(scenario_path: Path) -> None:
    result = await run_scenario(scenario_path, mode="stub")
    if not result.passed:
        formatted = "\n".join(f"  - {f.message}" for f in result.failures)
        pytest.fail(f"scenario {scenario_path.name} failed:\n{formatted}")


@pytest.mark.asyncio
async def test_l_level_regression_caught_when_resolver_disabled() -> None:
    """Smoke check: confirm the L-level scenario actually fails when the
    workspace resolver is disabled (i.e. PR #41's fix is regressed). Without
    this guard, a passing harness could mean either (a) the fix is in place
    or (b) the scenario doesn't actually exercise the bug — same green result.
    Patching the resolver to None lets us distinguish."""
    from tests.harness.runner import (
        assert_expectations,
        bootstrap_harness_env,
        dispatch_step,
        load_yaml,
        reset_event_cursor,
        teardown_harness_env,
    )

    spec_path = SCENARIOS_DIR / "bilibili_chat_reply_resume.yaml"
    spec = load_yaml(spec_path)
    env = await bootstrap_harness_env(spec)
    env.runtime.set_workspace_resolver(None)
    reset_event_cursor(env)
    failures = []
    try:
        for step in spec.steps:
            try:
                await dispatch_step(env, step)
            except AssertionError:
                # An await-step timing out under the regression is itself
                # a signal that the regression broke the flow — caught.
                break
        failures = await assert_expectations(env, spec.expect)
    finally:
        await teardown_harness_env(env)
    assert failures, (
        "L-level regression scenario should have failed when the workspace "
        "resolver is disabled — passing means the scenario doesn't actually "
        "exercise the cwd-mismatch path"
    )
