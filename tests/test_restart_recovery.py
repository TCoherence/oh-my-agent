"""End-to-end restart/recovery tests for v1.0 acceptance criteria #3 / #7 / #10.

Each test models an in-process "restart" by closing the live ``SQLiteMemoryStore``
and re-opening a fresh instance pointed at the same on-disk database, then
asserts that the durable surface (history, agent_sessions, runtime_tasks,
hitl_prompts, suspended_agent_runs, automation_runtime_state) survived the
round-trip and is usable by the next process. We intentionally do not exercise
real subprocess restarts: per-test ``< 2s`` keeps the suite fast and avoids
docker dependencies. SIGKILL-mid-write OS races and uncommitted-WAL scenarios
are explicitly out of scope (graceful shutdown is covered by
``test_main_shutdown.py``).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_my_agent.gateway.platforms.discord import DiscordChannel
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.runtime.types import (
    TASK_STATUS_DRAFT,
    TASK_STATUS_PENDING,
    TASK_STATUS_RUNNING,
    TASK_STATUS_WAITING_USER_INPUT,
)


async def _reopen(store: SQLiteMemoryStore) -> SQLiteMemoryStore:
    """Close the live store and re-open against the same path to model a restart."""
    db_path = store._db_path  # noqa: SLF001 — test helper, intentional access
    await store.close()
    fresh = SQLiteMemoryStore(db_path)
    await fresh.init()
    return fresh


# --- Surface 1: Chat thread (DB persistence layer only) -----------------------


@pytest.mark.asyncio
async def test_restart_chat_thread_history_and_session_row_persist(tmp_path: Path) -> None:
    """History rows and ``agent_sessions`` row survive close/reopen.

    Scope: store layer only. The CLI session preload bridge that re-binds
    ``agent_session_id`` into a live agent instance is exercised by
    ``test_restart_explicit_skill_invoke_session_resume_bridge`` below.
    """
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.init()

    await store.append("discord", "ch1", "t1", {"role": "user", "content": "hi"})
    await store.append("discord", "ch1", "t1", {"role": "assistant", "content": "yo", "agent": "claude"})
    await store.save_session("discord", "ch1", "t1", "claude", "sess-abc-123")

    store = await _reopen(store)
    try:
        history = await store.load_history("discord", "ch1", "t1")
        assert [h["role"] for h in history] == ["user", "assistant"]
        assert history[1]["agent"] == "claude"
        assert await store.load_session("discord", "ch1", "t1", "claude") == "sess-abc-123"
    finally:
        await store.close()


# --- Surface 2: Explicit-skill invoke (CLI session resume bridge) -------------


class _FakeAgent:
    """Minimal stand-in for ``BaseCLIAgent`` for the session-preload check."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._sessions: dict[str, str] = {}

    def get_session_id(self, thread_id: str) -> str | None:
        return self._sessions.get(thread_id)

    def set_session_id(self, thread_id: str, session_id: str) -> None:
        self._sessions[thread_id] = session_id


@pytest.mark.asyncio
async def test_restart_explicit_skill_invoke_session_resume_bridge(tmp_path: Path) -> None:
    """After restart, the persisted CLI ``session_id`` re-binds into a fresh agent.

    Models the [manager.py:1168] ``set_session_id`` preload that runs before each
    agent invocation. We do not run an actual skill — just verify that:
    (a) the row written by the prior process is still in ``agent_sessions``;
    (b) re-loading and calling ``set_session_id`` makes the next invocation
        able to ``--resume`` rather than start fresh.
    """
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.init()

    await store.save_session("discord", "ch1", "thread-explicit", "claude", "cli-session-xyz")

    store = await _reopen(store)
    try:
        agent = _FakeAgent("claude")
        assert agent.get_session_id("thread-explicit") is None

        stored = await store.load_session("discord", "ch1", "thread-explicit", "claude")
        assert stored == "cli-session-xyz"
        agent.set_session_id("thread-explicit", stored)

        assert agent.get_session_id("thread-explicit") == "cli-session-xyz"
    finally:
        await store.close()


# --- Surface 3: Runtime task --------------------------------------------------


def _new_task_kwargs(task_id: str, *, status: str = TASK_STATUS_DRAFT) -> dict:
    return dict(
        task_id=task_id,
        platform="discord",
        channel_id="ch1",
        thread_id="thread-rt",
        created_by="owner-1",
        goal="restart test goal",
        original_request="restart test request",
        preferred_agent=None,
        status=status,
        max_steps=8,
        max_minutes=20,
        agent_timeout_seconds=None,
        agent_max_turns=None,
        test_command="pytest -q",
        completion_mode="merge",
        output_summary=None,
        artifact_manifest=None,
        automation_name=None,
        task_type="repo_change",
        skill_name=None,
    )


@pytest.mark.parametrize(
    "status",
    [TASK_STATUS_DRAFT, TASK_STATUS_RUNNING, TASK_STATUS_WAITING_USER_INPUT],
)
@pytest.mark.asyncio
async def test_restart_runtime_task_state_and_event_log_persist(
    tmp_path: Path, status: str
) -> None:
    """Runtime tasks in any persisted state survive restart with their event_log intact."""
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.init()

    task = await store.create_runtime_task(**_new_task_kwargs("task-1", status=status))
    await store.add_runtime_event(task.id, "task.created", {"by": "test"})
    await store.add_runtime_event(task.id, "task.checkpoint", {"step": 1})

    store = await _reopen(store)
    try:
        rehydrated = await store.get_runtime_task("task-1")
        assert rehydrated is not None
        assert rehydrated.status == status
        assert rehydrated.goal == "restart test goal"
        assert rehydrated.task_type == "repo_change"

        events = await store.list_runtime_events("task-1")
        assert [e["event_type"] for e in events] == ["task.created", "task.checkpoint"]
        assert events[1]["payload"] == {"step": 1}
    finally:
        await store.close()


# --- Surface 4: HITL wait (DB layer + Discord view rehydration) ---------------


def _new_hitl_kwargs(prompt_id: str, *, prompt_message_id: str | None = "999000111") -> dict:
    return dict(
        prompt_id=prompt_id,
        target_kind="thread",
        platform="discord",
        channel_id="100",
        thread_id="thread-hitl",
        task_id=None,
        agent_name="codex",
        status="waiting",
        question="Pick a digest",
        details="Two daily flavors",
        choices_json=[
            {"id": "ai", "label": "AI daily", "description": "Frontier labs"},
            {"id": "fin", "label": "Finance daily", "description": None},
        ],
        selected_choice_id=None,
        selected_choice_label=None,
        selected_choice_description=None,
        control_envelope_json=json.dumps({"resume_token": "tok-abc"}),
        resume_context_json={"skill_name": "market-briefing"},
        session_id_snapshot="sess-hitl-1",
        prompt_message_id=prompt_message_id,
        created_by="owner-1",
    )


@pytest.mark.asyncio
async def test_restart_hitl_db_round_trip(tmp_path: Path) -> None:
    """HITL prompt + control envelope + resume_context survive restart.

    Asserts (a) ``get_hitl_prompt`` rehydrates the row,
    (b) ``list_active_hitl_prompts`` returns it (used by the Discord
    rehydration entry point), (c) ``update_hitl_prompt`` to mark it resolved
    works on the rehydrated row.
    """
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.init()

    await store.create_hitl_prompt(**_new_hitl_kwargs("hitl-1"))

    store = await _reopen(store)
    try:
        rehydrated = await store.get_hitl_prompt("hitl-1")
        assert rehydrated is not None
        assert rehydrated.status == "waiting"
        assert rehydrated.prompt_message_id == "999000111"
        assert rehydrated.resume_context == {"skill_name": "market-briefing"}
        assert rehydrated.control_envelope_json == json.dumps({"resume_token": "tok-abc"})
        assert {c["id"] for c in rehydrated.choices} == {"ai", "fin"}

        actives = await store.list_active_hitl_prompts(platform="discord", channel_id="100")
        assert any(p.id == "hitl-1" for p in actives)

        updated = await store.update_hitl_prompt(
            "hitl-1", status="resolving", selected_choice_id="ai"
        )
        assert updated is not None and updated.status == "resolving"
        assert updated.selected_choice_id == "ai"
    finally:
        await store.close()


class _RecordingDiscordClient:
    """Records every ``add_view`` call so the test can assert rehydration."""

    def __init__(self) -> None:
        self.views: list[tuple[object, int | None]] = []

    def add_view(self, view, *, message_id=None) -> None:
        self.views.append((view, message_id))


@pytest.mark.asyncio
async def test_restart_hitl_discord_view_rehydration(tmp_path: Path) -> None:
    """After restart, Discord re-registers a persistent view for the live HITL row.

    Without this, the row sits in the DB but the button callback no longer
    fires when the operator clicks. The test wires a real ``SQLiteMemoryStore``
    through the same ``list_active_hitl_prompts`` runtime-service entry point
    that the production Discord adapter uses.
    """
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.init()
    await store.create_hitl_prompt(**_new_hitl_kwargs("hitl-2"))
    store = await _reopen(store)

    try:
        # Minimal RuntimeService stand-in: only the one method
        # ``_rehydrate_hitl_prompt_views`` needs.
        runtime_service = SimpleNamespace(
            list_active_hitl_prompts=store.list_active_hitl_prompts,
        )
        channel = DiscordChannel(token="x", channel_id="100", owner_user_ids={"owner-1"})
        channel.set_runtime_service(runtime_service)
        client = _RecordingDiscordClient()

        await channel._rehydrate_hitl_prompt_views(client)  # type: ignore[arg-type]

        assert len(client.views) == 1
        _view, message_id = client.views[0]
        assert message_id == 999000111
    finally:
        await store.close()


# --- Surface 5: Auth wait (runtime task path) ---------------------------------


@pytest.mark.asyncio
async def test_restart_runtime_task_auth_wait_then_resume_to_pending(tmp_path: Path) -> None:
    """Runtime task in WAITING_USER_INPUT (auth blocked) restarts and can be requeued.

    Models the [runtime/service.py:5355] auth-approved branch which flips a
    waiting task back to PENDING + clears blocked_reason. After restart the
    rehydrated task must support that exact transition so the auth flow handler
    can pick up where it left off.
    """
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.init()

    task = await store.create_runtime_task(
        **_new_task_kwargs("task-auth", status=TASK_STATUS_WAITING_USER_INPUT)
    )
    await store.update_runtime_task(
        task.id, blocked_reason="Awaiting bilibili login (login_required)."
    )
    await store.add_runtime_event(
        task.id,
        "task.auth_required",
        {"provider": "bilibili", "reason": "login_required", "flow_id": "flow-1"},
    )

    store = await _reopen(store)
    try:
        rehydrated = await store.get_runtime_task("task-auth")
        assert rehydrated is not None
        assert rehydrated.status == TASK_STATUS_WAITING_USER_INPUT
        assert rehydrated.blocked_reason and "bilibili" in rehydrated.blocked_reason

        # Simulate the auth-approved branch: flip status back to PENDING.
        await store.update_runtime_task(
            "task-auth",
            status=TASK_STATUS_PENDING,
            blocked_reason=None,
            resume_instruction="Auth flow completed.",
            error=None,
        )
        requeued = await store.get_runtime_task("task-auth")
        assert requeued is not None and requeued.status == TASK_STATUS_PENDING
        assert requeued.blocked_reason is None

        claimed = await store.claim_pending_runtime_task()
        assert claimed is not None and claimed.id == "task-auth"
    finally:
        await store.close()


# --- Surface 6: Auth wait (direct-chat path) ----------------------------------


@pytest.mark.asyncio
async def test_restart_direct_chat_auth_wait_then_resume_via_runtime_service(
    tmp_path: Path,
) -> None:
    """Direct-chat suspended_agent_run survives restart and is resumable.

    Models the [runtime/service.py:5302] direct-chat branch + the
    [runtime/service.py:1819] ``resume_suspended_agent_run`` entry. After
    restart the row must:
    (a) come back via ``get_active_suspended_agent_run`` so the auth event
        handler can find it,
    (b) accept ``update_suspended_agent_run(status="resuming")`` (the first
        store mutation ``resume_suspended_agent_run`` makes),
    (c) carry its ``resume_context`` (which holds the original request +
        skill_name that get spliced into the resume prompt).
    """
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.init()

    resume_context = {
        "skill_name": "youtube-podcast-digest",
        "original_request": "Summarize this week's lex fridman episode",
    }
    await store.create_suspended_agent_run(
        run_id="run-direct-chat",
        platform="discord",
        channel_id="ch1",
        thread_id="thread-suspend",
        agent_name="claude",
        status="waiting_auth",
        provider="bilibili",
        control_envelope_json=json.dumps({"control": True}),
        session_id_snapshot="sess-snap-1",
        resume_context_json=resume_context,
        created_by="owner-1",
    )

    store = await _reopen(store)
    try:
        rehydrated = await store.get_suspended_agent_run("run-direct-chat")
        assert rehydrated is not None
        assert rehydrated.status == "waiting_auth"
        assert rehydrated.resume_context == resume_context

        active = await store.get_active_suspended_agent_run(
            platform="discord",
            channel_id="ch1",
            thread_id="thread-suspend",
            provider="bilibili",
        )
        assert active is not None and active.id == "run-direct-chat"

        # Mirror what resume_suspended_agent_run() does first: flip to "resuming".
        updated = await store.update_suspended_agent_run(
            "run-direct-chat", status="resuming"
        )
        assert updated is not None and updated.status == "resuming"

        # original_request stayed in the resume_context so the resume prompt
        # builder ([runtime/service.py:1864] _build_suspended_run_resume_prompt)
        # can still splice it in.
        assert updated.resume_context["original_request"].startswith("Summarize")
    finally:
        await store.close()


# --- Surface 7: Automation ----------------------------------------------------


@pytest.mark.asyncio
async def test_restart_automation_state_persists_with_no_replay(tmp_path: Path) -> None:
    """Scheduler ``automation_runtime_state`` rehydrates with last/next timestamps.

    Confirms missed-while-down jobs are not replayed: ``last_run_at`` and
    ``next_run_at`` are durable and the scheduler reads them to decide skip vs
    replay on next tick (skip-on-overdue is the existing scheduler contract,
    not asserted here — we only assert the durable inputs survive).
    """
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.init()

    await store.upsert_automation_state(
        "weekly-podcast-digest",
        platform="discord",
        channel_id="ch1",
        enabled=True,
        last_run_at="2026-04-15T08:00:00+00:00",
        last_success_at="2026-04-15T08:00:00+00:00",
        next_run_at="2026-04-22T08:00:00+00:00",
        last_task_id="task-prev",
    )

    store = await _reopen(store)
    try:
        rehydrated = await store.get_automation_state("weekly-podcast-digest")
        assert rehydrated is not None
        assert rehydrated.enabled is True
        assert rehydrated.last_run_at == "2026-04-15T08:00:00+00:00"
        assert rehydrated.next_run_at == "2026-04-22T08:00:00+00:00"
        assert rehydrated.last_task_id == "task-prev"

        states = await store.list_automation_states()
        assert {s.name for s in states} == {"weekly-podcast-digest"}
    finally:
        await store.close()


# --- Cross-cutting: schema_version is preserved & migrations idempotent -------


@pytest.mark.asyncio
async def test_restart_schema_version_preserved_and_init_idempotent(tmp_path: Path) -> None:
    """Re-running ``init()`` against an already-migrated DB is a no-op.

    Belt-and-suspenders for the upgrade-path test suite: a clean restart must
    not bump or reset ``schema_version``, and the column-backfill +
    version-driven hooks must be safe to call repeatedly.
    """
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.init()
    initial_version = await store.get_schema_version()
    assert initial_version >= 1

    # Simulate a runtime task being created so _migrate_runtime_schema's
    # data-normalisation branches (UPDATE runtime_tasks SET task_type=...) have
    # rows to operate on.
    await store.create_runtime_task(**_new_task_kwargs("task-versioned"))

    store = await _reopen(store)
    try:
        assert await store.get_schema_version() == initial_version
        rehydrated = await store.get_runtime_task("task-versioned")
        assert rehydrated is not None
        assert rehydrated.task_type == "repo_change"
    finally:
        await store.close()
