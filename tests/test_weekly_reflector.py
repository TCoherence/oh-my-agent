from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from oh_my_agent.memory.judge_store import JudgeStore
from oh_my_agent.memory.weekly_reflector import (
    _MAX_DIARY_CHARS,
    _PER_DAY_CHARS,
    _WINDOW_DAYS,
    WeeklyReflectionLoop,
    WeeklyReflector,
)


@dataclass
class FakeResponse:
    text: str
    error: str | None = None


@dataclass
class FakeAgent:
    name: str = "fake"


class StubRegistry:
    def __init__(self, responses: list[str | tuple[str, str | None]] | None = None):
        self._responses = list(responses or [])
        self.calls: list[tuple[str, str | None]] = []

    async def run(self, prompt: str, run_label: str | None = None):
        self.calls.append((prompt, run_label))
        if not self._responses:
            return FakeAgent(), FakeResponse(text="", error="no_response")
        nxt = self._responses.pop(0)
        if isinstance(nxt, tuple):
            text, err = nxt
        else:
            text, err = nxt, None
        return FakeAgent(), FakeResponse(text=text, error=err)


class ExplodingRegistry:
    async def run(self, prompt: str, run_label: str | None = None):
        raise RuntimeError("boom")


def _write_diary(diary_dir: Path, day: date, body: str) -> Path:
    path = diary_dir / f"{day.isoformat()}.md"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Window boundary — the single most important behaviour to lock down
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflect_last_week_window_is_yesterday_minus_six_through_yesterday(
    tmp_path: Path,
):
    """Boundary: with now=2026-04-29 03:00, weekly reads files for
    2026-04-22 ... 2026-04-28 (7 days ending yesterday)."""
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    fixed_now = datetime(2026, 4, 29, 3, 0, 0)
    expected_dates = [date(2026, 4, 22) + timedelta(days=i) for i in range(7)]
    for d in expected_dates:
        _write_diary(diary_dir, d, f"user: hi on {d}\n")
    # Add a file for "today" (2026-04-29) that must NOT be read.
    _write_diary(diary_dir, date(2026, 4, 29), "user: today should be excluded\n")
    store = JudgeStore(memory_dir=tmp_path / "memory")
    await store.load()
    response = json.dumps({"actions": [{"op": "no_op", "reason": "n/a"}]})
    registry = StubRegistry([response])
    reflector = WeeklyReflector(diary_dir=diary_dir, store=store)

    result = await reflector.reflect_last_week(registry=registry, now=fixed_now)

    assert result.diary_date == date(2026, 4, 28)
    assert result.skipped_reason is None
    prompt = registry.calls[0][0]
    for d in expected_dates:
        assert d.isoformat() in prompt, f"missing {d} in prompt"
    assert "2026-04-29" not in prompt, "today must be excluded from window"
    assert "2026-04-21" not in prompt, "day before window must be excluded"


# --------------------------------------------------------------------------
# _collect_week_text composition
# --------------------------------------------------------------------------


def test_collect_week_text_all_seven_days_present(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    end = date(2026, 4, 28)
    for offset in range(_WINDOW_DAYS):
        d = end - timedelta(days=offset)
        _write_diary(diary_dir, d, f"user: day {d.isoformat()}\n")
    store = JudgeStore(memory_dir=tmp_path / "memory")
    reflector = WeeklyReflector(diary_dir=diary_dir, store=store)

    text, present = reflector._collect_week_text(end)

    assert present == 7
    # Days emitted oldest -> newest.
    expected_order = [
        (end - timedelta(days=offset)).isoformat()
        for offset in range(_WINDOW_DAYS - 1, -1, -1)
    ]
    positions = [text.find(f"## --- {d} ---") for d in expected_order]
    assert all(p >= 0 for p in positions), positions
    assert positions == sorted(positions), "days must appear oldest -> newest"
    assert "(no diary)" not in text


def test_collect_week_text_missing_day_renders_placeholder(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    end = date(2026, 4, 28)
    # 2026-04-25 is missing on purpose.
    for d in [date(2026, 4, 22), date(2026, 4, 23), date(2026, 4, 24),
              date(2026, 4, 26), date(2026, 4, 27), date(2026, 4, 28)]:
        _write_diary(diary_dir, d, f"user: hi {d}\n")
    store = JudgeStore(memory_dir=tmp_path / "memory")
    reflector = WeeklyReflector(diary_dir=diary_dir, store=store)

    text, present = reflector._collect_week_text(end)

    assert present == 6
    assert "## --- 2026-04-25 --- (no diary)" in text


def test_collect_week_text_truncates_oversized_day(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    end = date(2026, 4, 28)
    big = "y" * (_PER_DAY_CHARS + 1_000)
    _write_diary(diary_dir, end, big)
    store = JudgeStore(memory_dir=tmp_path / "memory")
    reflector = WeeklyReflector(diary_dir=diary_dir, store=store)

    text, present = reflector._collect_week_text(end)

    assert present == 1
    assert "[day truncated]" in text
    # Other 6 days are missing → placeholders.
    assert text.count("(no diary)") == 6


def test_collect_week_text_caps_total_length(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    end = date(2026, 4, 28)
    big = "z" * (_PER_DAY_CHARS - 100)
    for offset in range(_WINDOW_DAYS):
        _write_diary(diary_dir, end - timedelta(days=offset), big)
    store = JudgeStore(memory_dir=tmp_path / "memory")
    reflector = WeeklyReflector(
        diary_dir=diary_dir,
        store=store,
        max_diary_chars=10_000,  # well below 7 * per_day_chars
    )

    text, present = reflector._collect_week_text(end)

    assert present == 7
    assert "[week truncated]" in text
    assert len(text) <= 10_000 + len("\n...[week truncated]")


def test_collect_week_text_empty_day_treated_as_no_content(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    end = date(2026, 4, 28)
    _write_diary(diary_dir, end, "   \n\t")
    _write_diary(diary_dir, end - timedelta(days=1), "user: real content\n")
    store = JudgeStore(memory_dir=tmp_path / "memory")
    reflector = WeeklyReflector(diary_dir=diary_dir, store=store)

    text, present = reflector._collect_week_text(end)

    assert present == 1
    assert "## --- 2026-04-28 --- (empty)" in text


# --------------------------------------------------------------------------
# reflect() integration
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflect_skipped_when_all_days_missing(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    store = JudgeStore(memory_dir=tmp_path / "memory")
    await store.load()
    registry = StubRegistry(["should not be used"])
    reflector = WeeklyReflector(diary_dir=diary_dir, store=store)

    result = await reflector.reflect(
        week_end_date=date(2026, 4, 28), registry=registry
    )

    assert result.skipped_reason == "diary_missing"
    assert result.actions == []
    assert registry.calls == []


@pytest.mark.asyncio
async def test_reflect_applies_add_action(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    end = date(2026, 4, 28)
    for d in [date(2026, 4, 23), date(2026, 4, 26), end]:
        _write_diary(diary_dir, d, f"user: prefer terse commits on {d}\n")
    store = JudgeStore(memory_dir=tmp_path / "memory")
    await store.load()
    response = json.dumps(
        {
            "actions": [
                {
                    "op": "add",
                    "summary": "user prefers terse commits",
                    "category": "preference",
                    "scope": "global_user",
                    "confidence": 0.85,
                    "evidence": "[2026-04-23] prefer terse; [2026-04-26] prefer terse",
                }
            ]
        }
    )
    registry = StubRegistry([response])
    reflector = WeeklyReflector(diary_dir=diary_dir, store=store)

    result = await reflector.reflect(week_end_date=end, registry=registry)

    assert result.error is None
    assert result.stats["add"] == 1
    assert registry.calls[0][1] == "weekly_reflect"
    active = store.get_active()
    assert len(active) == 1
    assert "terse" in active[0].summary


@pytest.mark.asyncio
async def test_reflect_returns_error_when_agent_errors(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    end = date(2026, 4, 28)
    _write_diary(diary_dir, end, "user: hi\n")
    store = JudgeStore(memory_dir=tmp_path / "memory")
    await store.load()
    registry = StubRegistry([("", "rate_limit")])
    reflector = WeeklyReflector(diary_dir=diary_dir, store=store)

    result = await reflector.reflect(week_end_date=end, registry=registry)

    assert result.error == "rate_limit"
    assert result.actions == []
    assert store.get_active() == []


@pytest.mark.asyncio
async def test_reflect_returns_error_when_agent_raises(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    end = date(2026, 4, 28)
    _write_diary(diary_dir, end, "user: hi\n")
    store = JudgeStore(memory_dir=tmp_path / "memory")
    await store.load()
    reflector = WeeklyReflector(diary_dir=diary_dir, store=store)

    result = await reflector.reflect(
        week_end_date=end, registry=ExplodingRegistry()
    )

    assert result.error is not None
    assert "agent_exception" in result.error


# --------------------------------------------------------------------------
# WeeklyReflectionLoop construction and timing math
# --------------------------------------------------------------------------


def _make_loop(
    *,
    fire_dow: int = 1,
    fire_hour: int = 3,
    now: datetime = datetime(2026, 4, 27, 1, 0, 0),  # Mon 2026-04-27 01:00
) -> WeeklyReflectionLoop:
    reflector = WeeklyReflector.__new__(WeeklyReflector)
    return WeeklyReflectionLoop(
        reflector=reflector,  # type: ignore[arg-type]
        registry=object(),
        fire_dow_local=fire_dow,
        fire_hour_local=fire_hour,
        clock=lambda: now,
    )


def test_invalid_fire_dow_raises():
    with pytest.raises(ValueError):
        WeeklyReflectionLoop(
            reflector=None,  # type: ignore[arg-type]
            registry=object(),
            fire_dow_local=7,
        )
    with pytest.raises(ValueError):
        WeeklyReflectionLoop(
            reflector=None,  # type: ignore[arg-type]
            registry=object(),
            fire_dow_local=-1,
        )


def test_invalid_fire_hour_raises():
    with pytest.raises(ValueError):
        WeeklyReflectionLoop(
            reflector=None,  # type: ignore[arg-type]
            registry=object(),
            fire_dow_local=1,
            fire_hour_local=24,
        )


def test_seconds_until_next_fire_dow_in_future_same_week():
    # Mon 01:00, fire Tue 03:00 → 26 hours.
    loop = _make_loop(
        fire_dow=1, fire_hour=3, now=datetime(2026, 4, 27, 1, 0, 0)
    )
    seconds = loop._seconds_until_next_fire()
    assert seconds == pytest.approx(26 * 3600.0, rel=1e-3)


def test_seconds_until_next_fire_same_dow_before_target_hour():
    # Tue 01:00, fire Tue 03:00 → 2 hours.
    loop = _make_loop(
        fire_dow=1, fire_hour=3, now=datetime(2026, 4, 28, 1, 0, 0)
    )
    seconds = loop._seconds_until_next_fire()
    assert seconds == pytest.approx(2 * 3600.0, rel=1e-3)


def test_seconds_until_next_fire_same_dow_after_target_hour_rolls_to_next_week():
    # Tue 04:00, fire Tue 03:00 → next week's Tue 03:00 = 7*24 - 1 = 167 hours.
    loop = _make_loop(
        fire_dow=1, fire_hour=3, now=datetime(2026, 4, 28, 4, 0, 0)
    )
    seconds = loop._seconds_until_next_fire()
    assert seconds == pytest.approx(167 * 3600.0, rel=1e-3)


def test_seconds_until_next_fire_dow_already_passed_this_week():
    # Wed 01:00, fire Tue → next Tue.
    # Wed 2026-04-29 01:00 → next Tue is 2026-05-05 03:00. That's 6 days + 2 h = 146 h.
    loop = _make_loop(
        fire_dow=1, fire_hour=3, now=datetime(2026, 4, 29, 1, 0, 0)
    )
    seconds = loop._seconds_until_next_fire()
    assert seconds == pytest.approx(146 * 3600.0, rel=1e-3)


def test_seconds_until_next_fire_at_exact_target_rolls_one_week():
    # Tue 03:00 exactly (== target) → next Tue 03:00 = 7*24 = 168 h.
    loop = _make_loop(
        fire_dow=1, fire_hour=3, now=datetime(2026, 4, 28, 3, 0, 0)
    )
    seconds = loop._seconds_until_next_fire()
    assert seconds == pytest.approx(168 * 3600.0, rel=1e-3)


# --------------------------------------------------------------------------
# Loop lifecycle
# --------------------------------------------------------------------------


class _FakeReflector:
    def __init__(self):
        self.calls: list[datetime] = []

    async def reflect_last_week(self, *, registry):  # noqa: ARG002
        self.calls.append(datetime.now())
        return type(
            "R",
            (),
            {
                "diary_date": date(2026, 4, 28),
                "stats": {"add": 0, "strengthen": 0, "supersede": 0, "no_op": 1, "rejected": 0},
                "skipped_reason": None,
                "error": None,
            },
        )()


@pytest.mark.asyncio
async def test_loop_stop_before_first_fire():
    fake = _FakeReflector()
    loop = WeeklyReflectionLoop(
        reflector=fake,  # type: ignore[arg-type]
        registry=object(),
        fire_dow_local=1,
        fire_hour_local=3,
        clock=lambda: datetime(2026, 4, 27, 1, 0, 0),
    )
    loop.start()
    await loop.stop()
    assert fake.calls == []


@pytest.mark.asyncio
async def test_loop_start_is_idempotent():
    fake = _FakeReflector()
    loop = WeeklyReflectionLoop(
        reflector=fake,  # type: ignore[arg-type]
        registry=object(),
        fire_dow_local=1,
        fire_hour_local=3,
        clock=lambda: datetime(2026, 4, 27, 1, 0, 0),
    )
    loop.start()
    first_task = loop._task
    loop.start()
    assert loop._task is first_task
    await loop.stop()


@pytest.mark.asyncio
async def test_loop_fires_when_wait_completes(monkeypatch):
    """Simulate wait expiring so reflect_last_week fires once."""
    fake = _FakeReflector()
    fire_count = {"n": 0}

    async def fake_wait_for(awaitable, timeout):  # noqa: ARG001
        fire_count["n"] += 1
        if fire_count["n"] == 1:
            try:
                awaitable.close()
            except Exception:
                pass
            raise asyncio.TimeoutError
        try:
            awaitable.close()
        except Exception:
            pass
        return None

    monkeypatch.setattr(
        "oh_my_agent.memory.weekly_reflector.asyncio.wait_for",
        fake_wait_for,
    )
    loop = WeeklyReflectionLoop(
        reflector=fake,  # type: ignore[arg-type]
        registry=object(),
        fire_dow_local=1,
        fire_hour_local=3,
        clock=lambda: datetime(2026, 4, 27, 1, 0, 0),
    )
    loop.start()
    await asyncio.sleep(0.05)
    await loop.stop()
    assert len(fake.calls) >= 1


# Ensure module-level constants are sensible; guards regression of magic numbers.
def test_constants_are_reasonable():
    assert _WINDOW_DAYS == 7
    assert _MAX_DIARY_CHARS >= _PER_DAY_CHARS
