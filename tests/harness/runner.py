"""Scenario runner: yaml → bootstrap → dispatch steps → assert.

Lifecycle (see plan §Driver — explicit wiring):

- ``bootstrap_harness_env(spec)`` builds a minimal runtime graph following
  the ``runtime_env`` fixture pattern from ``tests/test_runtime_service.py``.
  We do NOT reuse ``boot.ignite()`` — it owns signal handlers and daemon
  supervisors that are pure noise in a scripted test.

- ``run_scenario(spec)`` dispatches steps sequentially against the env,
  then evaluates ``expect:`` assertions against the channel's structured
  event log + the runtime store.

- Teardown order matches production (boot.py:540-545):
    1. ``gateway.stop()`` — drains in-flight handlers and stops channels
       (lets ``gateway.start()``'s ``gather`` return).
    2. ``runtime.stop()`` — stops worker loops + auth poller (NOT done by
       ``gateway.stop()``; see manager.py:746-832).
    3. ``await gateway_task`` — propagate exceptions from ``gateway.start()``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from oh_my_agent.agents.base import BaseAgent
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.auth.service import AuthService
from oh_my_agent.gateway.manager import GatewayManager
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.runtime import RuntimeService
from tests.harness.scripted_channel import ChannelEvent, HarnessChannel, make_incoming
from tests.harness.stubs import StubAgent, StubBilibiliAuthProvider, seed_credential

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Spec dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ScenarioSpec:
    name: str
    seed: dict[str, Any]
    steps: list[dict[str, Any]]
    expect: dict[str, Any]
    raw: dict[str, Any]


def load_yaml(path: Path) -> ScenarioSpec:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    name = str(raw.get("scenario") or path.stem)
    seed = dict(raw.get("seed") or {})
    steps = list(raw.get("steps") or [])
    expect = dict(raw.get("expect") or {})
    return ScenarioSpec(name=name, seed=seed, steps=steps, expect=expect, raw=raw)


# ---------------------------------------------------------------------------
# HarnessEnv
# ---------------------------------------------------------------------------


@dataclass
class HarnessEnv:
    tmp_root: Path
    store: SQLiteMemoryStore
    runtime: RuntimeService
    channel: HarnessChannel
    gateway: GatewayManager
    gateway_task: asyncio.Task
    session: ChannelSession
    registry: AgentRegistry
    auth_service: AuthService
    auth_provider: StubBilibiliAuthProvider
    cleanup_paths: list[Path] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


async def bootstrap_harness_env(spec: ScenarioSpec, *, mode: str = "stub") -> HarnessEnv:
    if mode != "stub":
        if os.environ.get("OMA_HARNESS_ALLOW_REAL") != "1":
            raise RuntimeError(
                "real mode requires OMA_HARNESS_ALLOW_REAL=1 — refusing to "
                "spend API quota / make network calls without explicit opt-in"
            )
        # Real-mode plumbing intentionally out of v1 — would replace
        # StubAgent / StubBilibiliAuthProvider with real CLI agents and
        # real BilibiliAuthProvider. Stubbed for now to keep v1 small.
        raise NotImplementedError(
            "real mode (real CLI subprocess + real bilibili API) is a v2 deliverable"
        )

    tmp_root = Path(tempfile.mkdtemp(prefix="oma-harness-"))
    cleanup_paths: list[Path] = [tmp_root]

    repo_root = tmp_root / "repo"
    _init_test_git_repo(repo_root)

    store = SQLiteMemoryStore(tmp_root / "runtime.db")
    await store.init()

    auth_storage = tmp_root / "runtime" / "auth"
    auth_storage.mkdir(parents=True, exist_ok=True)

    # Seed credentials BEFORE constructing AuthService so the eventual
    # get_valid_credential lookup finds the row + file already in place.
    seed_credentials = list(spec.seed.get("credentials") or [])
    for cred in seed_credentials:
        provider = str(cred.get("provider") or "bilibili")
        owner_id = str(cred.get("owner_user_id") or "owner-1")
        cookie_value = str(cred.get("cookie_value") or "stub-sessdata-value")
        await seed_credential(
            store=store,
            storage_root=auth_storage,
            provider=provider,
            owner_user_id=owner_id,
            cookie_value=cookie_value,
        )

    # Pick auth provider mode. Default = "valid" (cached cookies); scenarios
    # can override via seed.auth_provider for fresh-login / failure tests.
    auth_provider_mode = str(spec.seed.get("auth_provider") or "valid")
    if auth_provider_mode not in {"valid", "approving", "failing", "expiring"}:
        raise ValueError(
            f"Unknown auth_provider mode: {auth_provider_mode!r} "
            f"(allowed: valid / approving / failing / expiring)"
        )
    auth_provider = StubBilibiliAuthProvider(
        mode=auth_provider_mode,
        persist_root=auth_storage,
    )

    auth_service = AuthService(
        store,
        config={
            "enabled": True,
            "storage_root": str(auth_storage),
            "qr_poll_interval_seconds": 0.05,
            "qr_default_timeout_seconds": 30,
        },
        providers=[auth_provider],
    )

    # Build agent registry from spec.seed.agents.
    agents_cfg = dict(spec.seed.get("agents") or {})
    agents_cfg.pop("mode", None)  # drop the mode marker; it's processed above
    registry_agents: list[BaseAgent] = []
    if not agents_cfg:
        # A minimal default keeps the runtime happy if a scenario forgets
        # to declare agents; tests should always be explicit though.
        registry_agents.append(StubAgent("claude", responses=[{"when": {"default": True}, "text": "(stub) ok\nTASK_STATE: DONE"}]))
    else:
        for agent_name, raw_agent_cfg in agents_cfg.items():
            agent_cfg = dict(raw_agent_cfg or {})
            registry_agents.append(
                StubAgent(
                    str(agent_name),
                    responses=agent_cfg.get("responses"),
                    cwd_keyed_sessions=bool(agent_cfg.get("cwd_keyed_sessions", False)),
                )
            )
    registry = AgentRegistry(registry_agents)

    runtime = RuntimeService(
        store,
        config={
            "enabled": True,
            "worker_concurrency": 1,
            "worktree_root": str(tmp_root / "runtime" / "tasks"),
            "reports_dir": str(tmp_root / "reports"),
            "default_agent": registry_agents[0].name,
            "default_test_command": "true",
            "default_max_steps": 4,
            "default_max_minutes": 1,
            "agent_heartbeat_seconds": 0.1,
            "test_heartbeat_seconds": 0.1,
            "test_timeout_seconds": 5,
            "progress_notice_seconds": 0.1,
            "progress_persist_seconds": 0.1,
            "cleanup": {"enabled": False, "interval_minutes": 60, "retention_hours": 168},
            "merge_gate": {"enabled": False},
            "skill_auto_approve": True,
        },
        owner_user_ids={"owner-1"},
        repo_root=repo_root,
        auth_service=auth_service,
    )

    channel = HarnessChannel()
    session = ChannelSession(
        platform=channel.platform,
        channel_id=channel.channel_id,
        channel=channel,
        registry=registry,
        memory_store=store,
    )
    runtime.register_session(session, registry)

    gateway = GatewayManager(
        channels=[(channel, registry)],
        runtime_service=runtime,
        owner_user_ids={"owner-1"},
        short_workspace={
            "enabled": True,
            "root": str(tmp_root / "agent-workspace" / "sessions"),
            "ttl_hours": 24,
            "cleanup_interval_minutes": 1440,
        },
        repo_root=repo_root,
    )

    # See plan §Driver: GatewayManager.start() blocks at
    # ``await asyncio.gather(*background_tasks)`` (manager.py:742) — it
    # returns only when every background task exits. We spawn it as a
    # task and wait for the channel to signal handler readiness.
    gateway_task = asyncio.create_task(gateway.start(), name="harness-gateway")
    try:
        await channel.wait_ready(timeout=10.0)
    except Exception:
        # Surface gateway's startup exception (if any) instead of letting
        # the timeout swallow it.
        if gateway_task.done():
            await gateway_task
        raise

    return HarnessEnv(
        tmp_root=tmp_root,
        store=store,
        runtime=runtime,
        channel=channel,
        gateway=gateway,
        gateway_task=gateway_task,
        session=session,
        registry=registry,
        auth_service=auth_service,
        auth_provider=auth_provider,
        cleanup_paths=cleanup_paths,
    )


async def teardown_harness_env(env: HarnessEnv) -> None:
    try:
        await env.gateway.stop()
    except Exception:
        logger.warning("gateway.stop() raised", exc_info=True)
    try:
        await env.runtime.stop()
    except Exception:
        logger.warning("runtime.stop() raised", exc_info=True)
    try:
        await env.gateway_task
    except Exception:
        logger.warning("gateway_task raised on shutdown", exc_info=True)
    try:
        await env.store.close()
    except Exception:
        logger.warning("store.close() raised", exc_info=True)
    for path in env.cleanup_paths:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


def _init_test_git_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "harness@example.invalid"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Harness"], cwd=repo_root, check=True)
    (repo_root / "README.md").write_text("# harness test repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_root, check=True)


# ---------------------------------------------------------------------------
# Step dispatch
# ---------------------------------------------------------------------------


async def dispatch_step(env: HarnessEnv, step: dict[str, Any]) -> None:
    if "inject_user_message" in step:
        await _step_inject_user_message(env, step["inject_user_message"])
    elif "await" in step:
        await _step_await(env, step["await"])
    elif "sleep" in step:
        # Escape hatch — strongly discouraged. Surfaces explicit timing
        # assumptions when polling-based awaits don't fit.
        await asyncio.sleep(float(step["sleep"]))
    else:
        raise ValueError(f"Unknown step shape: {sorted(step.keys())}")


async def _step_inject_user_message(env: HarnessEnv, payload: dict[str, Any]) -> None:
    capture = payload.get("capture") or {}
    msg = make_incoming(
        content=str(payload.get("content") or ""),
        author=str(payload.get("author") or "harness-user"),
        author_id=str(payload.get("author_id") or "owner-1"),
        thread_id=payload.get("thread_id"),
        reply_to_message_id=payload.get("reply_to_message_id"),
        platform=env.channel.platform,
        channel_id=env.channel.channel_id,
        preferred_agent=payload.get("preferred_agent"),
        system=bool(payload.get("system", False)),
    )
    pre_event_count = len(env.channel.events)
    await env.channel.inject_user_message(msg)

    # If the gateway created a new thread for this message (msg.thread_id is
    # None), find the create_thread event so we can bind any thread alias
    # the scenario requested.
    new_thread_event: ChannelEvent | None = None
    new_send_event: ChannelEvent | None = None
    for event in env.channel.events[pre_event_count:]:
        if event.type == "create_thread" and new_thread_event is None:
            new_thread_event = event
        if event.type == "send" and new_send_event is None and msg.thread_id in (None, event.thread_id):
            new_send_event = event

    thread_alias = capture.get("thread_id_as")
    if thread_alias:
        target_thread_id = msg.thread_id
        if target_thread_id is None and new_thread_event is not None:
            target_thread_id = new_thread_event.thread_id
        if target_thread_id is not None:
            env.channel.bind_alias(thread_alias, target_thread_id)
    msg_alias = capture.get("message_id_as")
    if msg_alias and new_send_event is not None:
        env.channel.bind_alias(msg_alias, new_send_event.payload.get("message_id"))


async def _step_await(env: HarnessEnv, payload: dict[str, Any]) -> None:
    condition = str(payload.get("condition") or "")
    timeout = float(payload.get("timeout_seconds") or 30.0)
    interval = float(payload.get("poll_interval_seconds") or 0.1)
    deadline = asyncio.get_running_loop().time() + timeout

    if condition == "task_status_eq":
        target_status = str(payload.get("status") or "COMPLETED")
        index = int(payload.get("task_index") or 0)
        while asyncio.get_running_loop().time() < deadline:
            tasks = await env.store.list_runtime_tasks(
                platform=env.channel.platform,
                channel_id=env.channel.channel_id,
                limit=50,
            )
            tasks_sorted = sorted(tasks, key=lambda t: t.created_at or "")
            if index < len(tasks_sorted) and tasks_sorted[index].status == target_status:
                return
            await asyncio.sleep(interval)
        statuses = [(t.id[:8], t.status) for t in tasks_sorted] if tasks_sorted else []
        raise AssertionError(
            f"await task_status_eq timeout after {timeout}s — "
            f"expected task[{index}] status={target_status}; observed: {statuses}"
        )

    if condition == "event_seen":
        event_type = payload.get("type")
        payload_contains = payload.get("payload_contains")
        thread_id = payload.get("thread_id")
        while asyncio.get_running_loop().time() < deadline:
            matches = env.channel.find_events(
                type=event_type,
                thread_id=thread_id,
                payload_contains=payload_contains,
            )
            if matches:
                return
            await asyncio.sleep(interval)
        raise AssertionError(
            f"await event_seen timeout — type={event_type!r} "
            f"payload_contains={payload_contains!r} thread={thread_id!r}"
        )

    if condition == "auth_flow_event":
        # Wait until at least N auth flows have been seen by the stub.
        target_count = int(payload.get("min_count") or 1)
        while asyncio.get_running_loop().time() < deadline:
            seen = sum(env.auth_provider.poll_count.values())
            if seen >= target_count:
                return
            await asyncio.sleep(interval)
        raise AssertionError(
            f"await auth_flow_event timeout — wanted at least {target_count} "
            f"polls; seen {sum(env.auth_provider.poll_count.values())}"
        )

    raise ValueError(f"Unknown await condition: {condition!r}")


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


@dataclass
class AssertionFailure:
    message: str


async def assert_expectations(env: HarnessEnv, expect: dict[str, Any]) -> list[AssertionFailure]:
    failures: list[AssertionFailure] = []

    for predicate in expect.get("events_in_order") or []:
        result = _check_event_in_order(env, predicate, failures)
        if result is not None:
            failures.append(result)

    for predicate in expect.get("events_not_present") or []:
        matches = env.channel.find_events(
            type=predicate.get("type"),
            thread_id=predicate.get("thread_id"),
            payload_contains=predicate.get("payload_contains"),
        )
        if matches:
            failures.append(
                AssertionFailure(
                    f"events_not_present matched {len(matches)} event(s): "
                    f"{[(e.seq, e.type, _short_payload(e.payload)) for e in matches[:3]]}"
                )
            )

    task_expect = expect.get("task")
    if task_expect:
        await _check_task_expectations(env, task_expect, failures)

    metrics_expect = expect.get("metrics") or {}
    if "auth_flows_started" in metrics_expect:
        target = int(metrics_expect["auth_flows_started"])
        actual = len(env.auth_provider.poll_count)
        if actual != target:
            failures.append(
                AssertionFailure(
                    f"metrics.auth_flows_started: want {target}, got {actual}"
                )
            )

    return failures


def _check_event_in_order(
    env: HarnessEnv,
    predicate: dict[str, Any],
    failures: list[AssertionFailure],
) -> AssertionFailure | None:
    # ``events_in_order`` semantics: predicates must appear in order in the
    # event log, with arbitrary other events allowed between. We track a
    # cursor that advances past matched events.
    cursor: int = getattr(_check_event_in_order, "_cursor_per_env", {}).get(id(env), 0)
    matches = env.channel.find_events(
        type=predicate.get("type"),
        thread_id=predicate.get("thread_id"),
        payload_contains=predicate.get("payload_contains"),
    )
    matches_after_cursor = [m for m in matches if m.seq > cursor]
    if not matches_after_cursor:
        return AssertionFailure(
            f"events_in_order: no event after seq={cursor} matched "
            f"type={predicate.get('type')!r} payload_contains={predicate.get('payload_contains')!r}"
        )
    new_cursor = matches_after_cursor[0].seq
    cursor_map = getattr(_check_event_in_order, "_cursor_per_env", None)
    if cursor_map is None:
        cursor_map = {}
        _check_event_in_order._cursor_per_env = cursor_map  # type: ignore[attr-defined]
    cursor_map[id(env)] = new_cursor
    return None


def reset_event_cursor(env: HarnessEnv) -> None:
    """Tests that re-run scenarios on the same env should reset the cursor.

    Standalone scenarios get fresh envs so this matters mostly for unit
    tests that assert multiple times against the same channel.
    """
    cursor_map = getattr(_check_event_in_order, "_cursor_per_env", None)
    if cursor_map is not None:
        cursor_map.pop(id(env), None)


async def _check_task_expectations(
    env: HarnessEnv,
    task_expect: dict[str, Any],
    failures: list[AssertionFailure],
) -> None:
    index = int(task_expect.get("index") or 0)
    tasks = sorted(
        await env.store.list_runtime_tasks(
            platform=env.channel.platform,
            channel_id=env.channel.channel_id,
            limit=50,
        ),
        key=lambda t: t.created_at or "",
    )
    if index >= len(tasks):
        failures.append(
            AssertionFailure(
                f"task expectations: requested index {index} but only {len(tasks)} task(s) recorded"
            )
        )
        return
    task = tasks[index]
    if (target_status := task_expect.get("status")) is not None:
        if task.status != str(target_status):
            failures.append(
                AssertionFailure(
                    f"task[{index}].status: want {target_status}, got {task.status}"
                )
            )
    if (agent_used := task_expect.get("agent_used")) is not None:
        observed_agent = _infer_agent_used_for_task(env, task.thread_id)
        if observed_agent != str(agent_used):
            failures.append(
                AssertionFailure(
                    f"task[{index}].agent_used: want {agent_used!r}, got {observed_agent!r} "
                    f"(based on '-# via **<agent>**' attribution in channel sends)"
                )
            )
    if task_expect.get("output_summary_non_empty") is True:
        body = (task.output_summary or "").strip()
        if not body:
            failures.append(
                AssertionFailure(
                    f"task[{index}].output_summary_non_empty: output_summary was empty"
                )
            )


_VIA_ATTRIBUTION_RE = re.compile(r"-#\s*via\s+\*\*([^*]+)\*\*", re.IGNORECASE)


def _infer_agent_used_for_task(env: HarnessEnv, thread_id: str) -> str | None:
    """Return the agent name from the latest '-# via **<name>**' attribution
    in *thread_id*'s send events, or None if no attribution found."""
    last: str | None = None
    for event in env.channel.events:
        if event.type != "send":
            continue
        if event.thread_id != thread_id:
            continue
        text = str(event.payload.get("text") or "")
        match = _VIA_ATTRIBUTION_RE.search(text)
        if match:
            last = match.group(1).strip()
    return last


def _short_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Truncate string values for legible failure messages."""
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, str) and len(v) > 60:
            out[k] = v[:57] + "..."
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    failures: list[AssertionFailure]
    events: list[ChannelEvent]


async def run_scenario(yaml_path: Path, *, mode: str = "stub") -> ScenarioResult:
    spec = load_yaml(yaml_path)
    env = await bootstrap_harness_env(spec, mode=mode)
    reset_event_cursor(env)
    try:
        for step in spec.steps:
            await dispatch_step(env, step)
        failures = await assert_expectations(env, spec.expect)
    finally:
        await teardown_harness_env(env)
    return ScenarioResult(
        name=spec.name,
        passed=not failures,
        failures=failures,
        events=list(env.channel.events),
    )
