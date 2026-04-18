from __future__ import annotations

import asyncio

import pytest

from oh_my_agent.memory.idle_trigger import IdleTracker


@pytest.mark.asyncio
async def test_invalid_init_raises():
    with pytest.raises(ValueError):
        IdleTracker(on_fire=lambda *_a, **_kw: asyncio.sleep(0), idle_seconds=0)
    with pytest.raises(ValueError):
        IdleTracker(on_fire=lambda *_a, **_kw: asyncio.sleep(0), idle_seconds=10, poll_interval_seconds=0)


@pytest.mark.asyncio
async def test_touch_then_tick_fires_after_idle(monkeypatch):
    fired: list[tuple[str, dict]] = []

    async def cb(key, meta):
        fired.append((key, meta))

    tracker = IdleTracker(on_fire=cb, idle_seconds=10, poll_interval_seconds=60)

    fake_now = {"value": 1_000_000.0}

    def now():
        return fake_now["value"]

    monkeypatch.setattr("oh_my_agent.memory.idle_trigger.time.time", now)

    await tracker.touch("d|c|t1", metadata={"skill_name": "x"})
    # Not yet idle
    await tracker._tick()
    assert fired == []
    # Advance past idle threshold
    fake_now["value"] += 11
    await tracker._tick()
    assert len(fired) == 1
    assert fired[0][0] == "d|c|t1"
    assert fired[0][1]["skill_name"] == "x"

    # Second tick should not fire again (already judged)
    await tracker._tick()
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_new_message_resets_idle(monkeypatch):
    fired: list[str] = []

    async def cb(key, meta):
        fired.append(key)

    tracker = IdleTracker(on_fire=cb, idle_seconds=10, poll_interval_seconds=60)
    fake_now = {"value": 1_000.0}
    monkeypatch.setattr("oh_my_agent.memory.idle_trigger.time.time", lambda: fake_now["value"])

    await tracker.touch("d|c|t1")
    fake_now["value"] += 5
    await tracker.touch("d|c|t1")  # reset
    fake_now["value"] += 6  # only 6 since last touch
    await tracker._tick()
    assert fired == []
    fake_now["value"] += 5  # now 11 since last touch
    await tracker._tick()
    assert fired == ["d|c|t1"]


@pytest.mark.asyncio
async def test_mark_judged_prevents_refire_until_new_touch(monkeypatch):
    fired: list[str] = []

    async def cb(key, meta):
        fired.append(key)

    tracker = IdleTracker(on_fire=cb, idle_seconds=10, poll_interval_seconds=60)
    fake_now = {"value": 1_000.0}
    monkeypatch.setattr("oh_my_agent.memory.idle_trigger.time.time", lambda: fake_now["value"])

    await tracker.touch("d|c|t1")
    fake_now["value"] += 11
    await tracker._tick()
    assert fired == ["d|c|t1"]

    # Even far in the future, no re-fire without a new touch.
    fake_now["value"] += 1_000
    await tracker._tick()
    assert fired == ["d|c|t1"]

    # New touch then idle window → fires again.
    await tracker.touch("d|c|t1")
    fake_now["value"] += 11
    await tracker._tick()
    assert fired == ["d|c|t1", "d|c|t1"]


@pytest.mark.asyncio
async def test_forget_removes_state(monkeypatch):
    fired: list[str] = []

    async def cb(key, meta):
        fired.append(key)

    tracker = IdleTracker(on_fire=cb, idle_seconds=5, poll_interval_seconds=60)
    fake_now = {"value": 1_000.0}
    monkeypatch.setattr("oh_my_agent.memory.idle_trigger.time.time", lambda: fake_now["value"])

    await tracker.touch("d|c|t1")
    await tracker.forget("d|c|t1")
    fake_now["value"] += 100
    await tracker._tick()
    assert fired == []
