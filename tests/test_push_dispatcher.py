"""Tests for PushDispatcher + PushCoolDown.

Validates the dispatcher's three guarantees:
1. Allow-list filtering (default-False; only declared kinds fire).
2. Fire-and-forget — `schedule()` returns synchronously even when the
   provider's `send()` is slow.
3. Exception isolation — done callback catches anything the provider
   missed, logs at WARNING, never re-raises.

Plus the cool-down helper used to coalesce mention-peek bursts.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from oh_my_agent.push_notifications import (
    NoopPushProvider,
    PushCoolDown,
    PushDispatcher,
    PushNotificationEvent,
    PushNotificationProvider,
    PushSettings,
)


def _event(kind: str = "mention_owner") -> PushNotificationEvent:
    return PushNotificationEvent(
        kind=kind,  # type: ignore[arg-type]
        title="t",
        body="b",
        group="g",
        level="active",
        deep_link=None,
    )


class _SpyProvider(PushNotificationProvider):
    def __init__(self, *, sleep: float = 0.0, raises: BaseException | None = None) -> None:
        self.events: list[PushNotificationEvent] = []
        self._sleep = sleep
        self._raises = raises
        self.closed = False

    async def send(self, event: PushNotificationEvent) -> None:
        self.events.append(event)
        if self._sleep:
            await asyncio.sleep(self._sleep)
        if self._raises is not None:
            raise self._raises

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_default_settings_block_all_kinds():
    spy = _SpyProvider()
    d = PushDispatcher(spy, PushSettings())  # empty settings → nothing enabled

    d.schedule(_event("mention_owner"))
    await asyncio.sleep(0)  # let any pending task drain
    assert spy.events == []


@pytest.mark.asyncio
async def test_disabled_kind_does_not_fire():
    spy = _SpyProvider()
    d = PushDispatcher(spy, PushSettings(enabled_events={"mention_owner": False}))

    d.schedule(_event("mention_owner"))
    await asyncio.sleep(0)
    assert spy.events == []


@pytest.mark.asyncio
async def test_enabled_kind_dispatches_to_provider():
    spy = _SpyProvider()
    d = PushDispatcher(spy, PushSettings(enabled_events={"mention_owner": True}))

    d.schedule(_event("mention_owner"))
    # Yield once so the create_task callback runs.
    await asyncio.sleep(0.01)
    assert len(spy.events) == 1
    assert spy.events[0].kind == "mention_owner"


@pytest.mark.asyncio
async def test_schedule_returns_synchronously_even_when_provider_is_slow():
    spy = _SpyProvider(sleep=2.0)  # mimic a slow Bark POST
    d = PushDispatcher(spy, PushSettings(enabled_events={"task_draft": True}))

    started = asyncio.get_event_loop().time()
    d.schedule(_event("task_draft"))
    elapsed = asyncio.get_event_loop().time() - started
    # schedule should not have awaited the provider — should be ~0s
    assert elapsed < 0.05, f"schedule blocked for {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_done_callback_logs_unhandled_exceptions(caplog):
    spy = _SpyProvider(raises=RuntimeError("provider screwed up"))
    d = PushDispatcher(
        spy, PushSettings(enabled_events={"automation_failed": True})
    )

    with caplog.at_level(logging.WARNING, logger="oh_my_agent.push_notifications.base"):
        d.schedule(_event("automation_failed"))
        # Wait for the task to complete and the done callback to run.
        await asyncio.sleep(0.01)

    # Find the warning emitted by _on_done
    matches = [r for r in caplog.records if "Push fan-out raised" in r.getMessage()]
    assert matches, f"expected warning, got {[r.getMessage() for r in caplog.records]}"


@pytest.mark.asyncio
async def test_level_for_returns_configured_level():
    d = PushDispatcher(
        NoopPushProvider(),
        PushSettings(level_map={"task_draft": "critical", "mention_owner": "passive"}),
    )
    assert d.level_for("task_draft") == "critical"
    assert d.level_for("mention_owner") == "passive"


@pytest.mark.asyncio
async def test_level_for_falls_back_to_active():
    d = PushDispatcher(NoopPushProvider(), PushSettings())
    assert d.level_for("automation_complete") == "active"


@pytest.mark.asyncio
async def test_aclose_delegates_to_provider():
    spy = _SpyProvider()
    d = PushDispatcher(spy, PushSettings())
    await d.aclose()
    assert spy.closed


# ──────────────────────────────────────────────────────────────────────
# PushCoolDown
# ──────────────────────────────────────────────────────────────────────


def _fake_clock():
    """Returns a (now_callable, advance_callable) pair for deterministic
    time control without monkey-patching globals."""
    state = {"t": 1000.0}

    def now() -> float:
        return state["t"]

    def advance(seconds: float) -> None:
        state["t"] += seconds

    return now, advance


def test_cooldown_first_call_fires():
    now, _ = _fake_clock()
    cd = PushCoolDown(60.0, now=now)
    assert cd.should_fire("channel-A:author-1") is True


def test_cooldown_immediate_repeat_suppressed():
    now, advance = _fake_clock()
    cd = PushCoolDown(60.0, now=now)
    assert cd.should_fire("channel-A:author-1") is True
    assert cd.should_fire("channel-A:author-1") is False
    advance(30.0)
    assert cd.should_fire("channel-A:author-1") is False


def test_cooldown_just_inside_window_suppressed():
    now, advance = _fake_clock()
    cd = PushCoolDown(60.0, now=now)
    assert cd.should_fire("channel-A:author-1") is True
    # 1ms before the cool-down expires — still suppressed
    advance(59.999)
    assert cd.should_fire("channel-A:author-1") is False


def test_cooldown_at_boundary_fires():
    """At exactly the cool-down window (delta == cool_down), the
    suppression check ``delta < cool_down`` is False, so a fire is
    allowed. Documents the boundary semantics."""
    now, advance = _fake_clock()
    cd = PushCoolDown(60.0, now=now)
    assert cd.should_fire("channel-A:author-1") is True
    advance(60.0)
    assert cd.should_fire("channel-A:author-1") is True


def test_cooldown_expires_after_window():
    now, advance = _fake_clock()
    cd = PushCoolDown(60.0, now=now)
    assert cd.should_fire("channel-A:author-1") is True
    advance(60.001)
    assert cd.should_fire("channel-A:author-1") is True


def test_cooldown_independent_keys():
    now, _ = _fake_clock()
    cd = PushCoolDown(60.0, now=now)
    assert cd.should_fire("channel-A:author-1") is True
    # Same channel, different author — independent
    assert cd.should_fire("channel-A:author-2") is True
    # Same author, different channel — independent
    assert cd.should_fire("channel-B:author-1") is True
    # All three keys are now in cool-down
    assert cd.should_fire("channel-A:author-1") is False
    assert cd.should_fire("channel-A:author-2") is False
    assert cd.should_fire("channel-B:author-1") is False


def test_cooldown_lazy_cleanup_after_threshold():
    now, advance = _fake_clock()
    cd = PushCoolDown(60.0, now=now)
    # Fill past the cleanup threshold with stale entries
    for i in range(120):
        cd.should_fire(f"key-{i}")
    advance(120.0)  # all entries now stale
    # Triggering a new entry past the threshold should prune stale ones
    cd.should_fire("fresh-key")
    # The internal map should have shrunk dramatically
    # (allowing some headroom — exact size depends on prune timing)
    assert len(cd._last_fire) < 20


def test_cooldown_rejects_invalid_seconds():
    with pytest.raises(ValueError):
        PushCoolDown(0)
    with pytest.raises(ValueError):
        PushCoolDown(-1.0)
