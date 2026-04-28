"""End-to-end integration tests for push notification injection points.

Replaces the provider with an in-memory spy and verifies that:
- NotificationManager.emit() fans out task_draft / task_waiting_merge /
  ask_user to the dispatcher
- auth_required is NOT mapped to a push event (allow-list rejection)
- Same event with same dedupe_key fires only once (dedupe inheritance)
- task_draft body includes payload.reason_text (no _reason_label clobber)
- ask_user body keeps the original question
- RuntimeService._emit_automation_terminal_push fires for automation
  tasks, skips for manual tasks
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.memory.store import SQLiteMemoryStore
from oh_my_agent.push_notifications import (
    PushDispatcher,
    PushNotificationEvent,
    PushNotificationProvider,
    PushSettings,
)
from oh_my_agent.runtime.notifications import NotificationManager
from oh_my_agent.runtime.types import NotificationEvent


class _SpyProvider(PushNotificationProvider):
    def __init__(self) -> None:
        self.events: list[PushNotificationEvent] = []

    async def send(self, event: PushNotificationEvent) -> None:
        self.events.append(event)

    async def aclose(self) -> None:
        return None


def _make_dispatcher(*, enabled_events: dict[str, bool] | None = None) -> tuple[PushDispatcher, _SpyProvider]:
    spy = _SpyProvider()
    settings = PushSettings(
        enabled_events=enabled_events or {
            "mention_owner": True,
            "task_draft": True,
            "task_waiting_merge": True,
            "ask_user": True,
            "automation_complete": True,
            "automation_failed": True,
        },
        level_map={
            "task_draft": "timeSensitive",
            "task_waiting_merge": "timeSensitive",
            "ask_user": "timeSensitive",
            "automation_complete": "active",
            "automation_failed": "timeSensitive",
            "mention_owner": "timeSensitive",
        },
    )
    return PushDispatcher(spy, settings), spy


def _make_session() -> ChannelSession:
    channel = MagicMock()
    channel.platform = "discord"
    channel.channel_id = "100"
    channel.send = AsyncMock(return_value="thread-msg-1")
    channel.send_dm = AsyncMock(return_value="dm-msg-1")
    channel.render_user_mention = lambda uid: f"<@{uid}>"
    return ChannelSession(
        platform="discord",
        channel_id="100",
        channel=channel,
        registry=MagicMock(),
    )


@pytest.fixture
async def store(tmp_path):
    s = SQLiteMemoryStore(tmp_path / "notif.db")
    await s.init()
    yield s
    await s.close()


def _internal_event(kind: str, **over) -> NotificationEvent:
    defaults = dict(
        kind=kind,
        platform="discord",
        channel_id="100",
        thread_id="thread-1",
        title="Action required",
        body="Please approve",
        dedupe_key=f"task:abc:{kind}",
        task_id="abc",
        payload={"reason_text": "high-risk repo_change"},
    )
    defaults.update(over)
    return NotificationEvent(**defaults)


@pytest.mark.asyncio
async def test_emit_fans_out_task_draft_with_reason_text(store):
    dispatcher, spy = _make_dispatcher()
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: _make_session(),
        push_dispatcher=dispatcher,
    )
    await mgr.emit(_internal_event("task_draft"))

    # Wait for the fire-and-forget task to complete
    await asyncio.sleep(0.01)
    assert len(spy.events) == 1
    ev = spy.events[0]
    assert ev.kind == "task_draft"
    assert ev.group == "hitl"
    assert ev.level == "timeSensitive"
    # body should include reason_text prepended (the bug we fixed)
    assert "high-risk repo_change" in ev.body


@pytest.mark.asyncio
async def test_emit_fans_out_ask_user_with_question_intact(store):
    dispatcher, spy = _make_dispatcher()
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: _make_session(),
        push_dispatcher=dispatcher,
    )
    ask_event = _internal_event(
        "ask_user",
        body="Question: Should I delete the file?\nNext step: answer the prompt in this thread.",
        payload={"question": "Should I delete?"},
    )
    await mgr.emit(ask_event)
    await asyncio.sleep(0.01)

    assert len(spy.events) == 1
    ev = spy.events[0]
    assert ev.kind == "ask_user"
    # The question text must survive — not get replaced by _reason_label("ask_user") == "ask_user"
    assert "Should I delete" in ev.body
    assert ev.body.startswith("Question:")


@pytest.mark.asyncio
async def test_emit_fans_out_task_waiting_merge(store):
    dispatcher, spy = _make_dispatcher()
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: _make_session(),
        push_dispatcher=dispatcher,
    )
    await mgr.emit(_internal_event("task_waiting_merge"))
    await asyncio.sleep(0.01)

    assert len(spy.events) == 1
    assert spy.events[0].kind == "task_waiting_merge"


@pytest.mark.asyncio
async def test_emit_does_not_fan_out_auth_required(store):
    dispatcher, spy = _make_dispatcher()
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: _make_session(),
        push_dispatcher=dispatcher,
    )
    await mgr.emit(_internal_event("auth_required"))
    await asyncio.sleep(0.01)

    # auth_required has no mapping to a PushKind → push must NOT fire
    assert spy.events == []


@pytest.mark.asyncio
async def test_emit_dedupe_prevents_double_push(store):
    dispatcher, spy = _make_dispatcher()
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: _make_session(),
        push_dispatcher=dispatcher,
    )
    ev = _internal_event("task_draft")
    await mgr.emit(ev)
    await asyncio.sleep(0.01)
    assert len(spy.events) == 1

    # Re-emit with the same dedupe_key — should hit the dedupe early
    # return path; new push must NOT fire.
    await mgr.emit(ev)
    await asyncio.sleep(0.01)
    assert len(spy.events) == 1


@pytest.mark.asyncio
async def test_emit_skips_push_when_event_disabled_in_settings(store):
    dispatcher, spy = _make_dispatcher(
        enabled_events={"task_draft": False, "task_waiting_merge": True, "ask_user": True}
    )
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: _make_session(),
        push_dispatcher=dispatcher,
    )
    await mgr.emit(_internal_event("task_draft"))
    await asyncio.sleep(0.01)
    # task_draft disabled → no push, even though internal notification ran
    assert spy.events == []


@pytest.mark.asyncio
async def test_emit_without_dispatcher_is_safe(store):
    mgr = NotificationManager(
        store,
        owner_user_ids={"owner-1"},
        session_lookup=lambda p, c: _make_session(),
        push_dispatcher=None,  # no dispatcher → no fan-out
    )
    records = await mgr.emit(_internal_event("task_draft"))
    assert len(records) == 1  # internal flow still runs


# ──────────────────────────────────────────────────────────────────────
# RuntimeService._emit_automation_terminal_push helper
# ──────────────────────────────────────────────────────────────────────


def _make_runtime_helper_owner():
    """Construct a minimal stub mimicking the RuntimeService instance state
    that ``_emit_automation_terminal_push`` reads. Avoids spinning up the
    full RuntimeService just to test one method."""
    from oh_my_agent.runtime.service import RuntimeService

    dispatcher, spy = _make_dispatcher()

    # Bind the unbound method to a SimpleNamespace-like object holding only
    # the attributes the helper reads.
    helper = RuntimeService._emit_automation_terminal_push.__get__(
        type("Stub", (), {"_push_dispatcher": dispatcher})()
    )
    return helper, spy


def _task(automation_name: str | None) -> MagicMock:
    t = MagicMock()
    t.automation_name = automation_name
    return t


@pytest.mark.asyncio
async def test_terminal_push_fires_for_automation_complete():
    helper, spy = _make_runtime_helper_owner()
    helper(_task("market-briefing"), kind="automation_complete", body="done!")
    await asyncio.sleep(0.01)

    assert len(spy.events) == 1
    ev = spy.events[0]
    assert ev.kind == "automation_complete"
    assert "market-briefing" in ev.title
    assert ev.body == "done!"
    assert ev.group == "automation"


@pytest.mark.asyncio
async def test_terminal_push_fires_for_automation_failed():
    helper, spy = _make_runtime_helper_owner()
    helper(_task("housing-watch"), kind="automation_failed", body="oops")
    await asyncio.sleep(0.01)

    assert len(spy.events) == 1
    assert spy.events[0].kind == "automation_failed"
    assert "housing-watch" in spy.events[0].title


@pytest.mark.asyncio
async def test_terminal_push_skipped_when_not_automation():
    helper, spy = _make_runtime_helper_owner()
    helper(_task(None), kind="automation_complete", body="done!")
    await asyncio.sleep(0.01)

    # Manual /task_start runs must NEVER ring the owner's phone
    assert spy.events == []


@pytest.mark.asyncio
async def test_terminal_push_truncates_body():
    helper, spy = _make_runtime_helper_owner()
    helper(_task("auto"), kind="automation_failed", body="x" * 500)
    await asyncio.sleep(0.01)

    assert len(spy.events) == 1
    assert len(spy.events[0].body) == 200


@pytest.mark.asyncio
async def test_terminal_push_skipped_when_disabled_in_settings():
    from oh_my_agent.runtime.service import RuntimeService

    dispatcher, spy = _make_dispatcher(
        enabled_events={"automation_complete": True, "automation_failed": False}
    )
    helper = RuntimeService._emit_automation_terminal_push.__get__(
        type("Stub", (), {"_push_dispatcher": dispatcher})()
    )
    helper(_task("auto"), kind="automation_failed", body="kaboom")
    await asyncio.sleep(0.01)

    # automation_failed disabled → no push
    assert spy.events == []


# ──────────────────────────────────────────────────────────────────────
# Regression: notification_failure push body must not leak exception repr
# ──────────────────────────────────────────────────────────────────────


def test_notification_failure_call_site_does_not_pass_full_exc_repr():
    """The completion notification_failure branch in `_run_task` catches a
    `_notify` exception and triggers an external push. ``repr(exc)`` for
    Discord/HTTP exceptions can include URL query strings or request
    bodies that contain auth tokens — sending that raw to a lock-screen
    push is a leak. The body must use only ``type(exc).__name__``.

    This is a textual regression guard: if someone reverts to ``{exc!r}``
    or similar near ``_emit_automation_terminal_push`` for the
    notification_failure case, this test fires before the leak ships.
    """
    import inspect

    from oh_my_agent.runtime import service

    source = inspect.getsource(service)
    # Find the call sites and check the surrounding 250 chars.
    fragments = source.split("_emit_automation_terminal_push(")
    # First fragment is everything before the first call — skip
    for chunk in fragments[1:]:
        window = chunk[:250]
        # Allow ``str(exc)`` or ``type(exc).__name__`` etc., but ``{exc!r}``
        # and ``{exc}`` (which is ``__str__``, can also leak) inside the
        # body= kwarg are forbidden for notification_failure.
        if "notification failed" in window:
            assert "{exc!r}" not in window, (
                "notification_failure push body must not include {exc!r} — "
                "exception repr can leak auth tokens / URL query strings"
            )
            assert "{exc}" not in window, (
                "notification_failure push body must not include {exc} — "
                "exception str can also include sensitive payload data"
            )
            assert "type(exc).__name__" in window, (
                "Expected sanitized notification_failure push body using "
                "type(exc).__name__"
            )
