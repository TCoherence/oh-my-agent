from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from oh_my_agent.memory.diary_reflector import (
    _MAX_DIARY_CHARS,
    DiaryReflectionLoop,
    DiaryReflector,
)
from oh_my_agent.memory.judge_store import JudgeStore


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


# --------------------------------------------------------------------------
# DiaryReflector.reflect
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reflect_with_populated_diary_applies_actions(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    target_date = date(2026, 4, 20)
    diary_path = diary_dir / f"{target_date.isoformat()}.md"
    diary_path.write_text(
        "# 2026-04-20\n\n"
        "user: let's keep commits terse\n\n"
        "assistant: ok\n",
        encoding="utf-8",
    )
    memory_dir = tmp_path / "memory"
    store = JudgeStore(memory_dir=memory_dir)
    await store.load()

    response_json = json.dumps(
        {
            "actions": [
                {
                    "op": "add",
                    "summary": "user prefers terse commit messages",
                    "category": "preference",
                    "scope": "global_user",
                    "confidence": 0.9,
                    "evidence": "let's keep commits terse",
                }
            ]
        }
    )
    registry = StubRegistry([response_json])
    reflector = DiaryReflector(diary_dir=diary_dir, store=store)

    result = await reflector.reflect(diary_date=target_date, registry=registry)

    assert result.error is None
    assert result.skipped_reason is None
    assert result.stats["add"] == 1
    assert len(registry.calls) == 1
    assert registry.calls[0][1] == "diary_reflect"
    active = store.get_active()
    assert len(active) == 1
    assert "terse" in active[0].summary


@pytest.mark.asyncio
async def test_reflect_with_missing_diary_is_skipped(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    store = JudgeStore(memory_dir=tmp_path / "memory")
    await store.load()
    registry = StubRegistry(["should not be used"])
    reflector = DiaryReflector(diary_dir=diary_dir, store=store)

    result = await reflector.reflect(
        diary_date=date(2026, 1, 1), registry=registry
    )

    assert result.skipped_reason == "diary_missing"
    assert result.actions == []
    assert registry.calls == []
    assert store.get_active() == []


@pytest.mark.asyncio
async def test_reflect_with_empty_diary_is_skipped(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    target_date = date(2026, 4, 20)
    (diary_dir / f"{target_date.isoformat()}.md").write_text("   \n\n\t", encoding="utf-8")
    store = JudgeStore(memory_dir=tmp_path / "memory")
    await store.load()
    registry = StubRegistry(["should not be used"])
    reflector = DiaryReflector(diary_dir=diary_dir, store=store)

    result = await reflector.reflect(diary_date=target_date, registry=registry)

    assert result.skipped_reason == "diary_empty"
    assert result.actions == []
    assert registry.calls == []


@pytest.mark.asyncio
async def test_reflect_truncates_large_diary(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    target_date = date(2026, 4, 20)
    # Fabricate a diary larger than the truncation threshold.
    oversized = "x" * (_MAX_DIARY_CHARS + 5_000)
    (diary_dir / f"{target_date.isoformat()}.md").write_text(oversized, encoding="utf-8")
    store = JudgeStore(memory_dir=tmp_path / "memory")
    await store.load()
    response_json = json.dumps({"actions": [{"op": "no_op", "reason": "n/a"}]})
    registry = StubRegistry([response_json])
    reflector = DiaryReflector(diary_dir=diary_dir, store=store)

    result = await reflector.reflect(diary_date=target_date, registry=registry)

    assert result.skipped_reason is None
    assert result.error is None
    prompt = registry.calls[0][0]
    assert "[diary truncated]" in prompt
    # The prompt must contain the truncation marker but not the full oversized text.
    assert prompt.count("x") <= _MAX_DIARY_CHARS + 100


@pytest.mark.asyncio
async def test_reflect_returns_error_when_agent_errors(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    target_date = date(2026, 4, 20)
    (diary_dir / f"{target_date.isoformat()}.md").write_text("user: hi\n", encoding="utf-8")
    store = JudgeStore(memory_dir=tmp_path / "memory")
    await store.load()
    registry = StubRegistry([("", "rate_limit")])
    reflector = DiaryReflector(diary_dir=diary_dir, store=store)

    result = await reflector.reflect(diary_date=target_date, registry=registry)

    assert result.error == "rate_limit"
    assert result.actions == []
    assert store.get_active() == []


@pytest.mark.asyncio
async def test_reflect_returns_error_when_agent_raises(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    target_date = date(2026, 4, 20)
    (diary_dir / f"{target_date.isoformat()}.md").write_text("user: hi\n", encoding="utf-8")
    store = JudgeStore(memory_dir=tmp_path / "memory")
    await store.load()
    reflector = DiaryReflector(diary_dir=diary_dir, store=store)

    result = await reflector.reflect(
        diary_date=target_date, registry=ExplodingRegistry()
    )

    assert result.error is not None
    assert "agent_exception" in result.error
    assert result.actions == []


@pytest.mark.asyncio
async def test_reflect_yesterday_uses_today_minus_one(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    fixed_now = datetime(2026, 4, 20, 10, 0, 0)
    yesterday = (fixed_now - timedelta(days=1)).date()
    (diary_dir / f"{yesterday.isoformat()}.md").write_text("user: hi\n", encoding="utf-8")
    store = JudgeStore(memory_dir=tmp_path / "memory")
    await store.load()
    response_json = json.dumps({"actions": [{"op": "no_op", "reason": "n/a"}]})
    registry = StubRegistry([response_json])
    reflector = DiaryReflector(diary_dir=diary_dir, store=store)

    result = await reflector.reflect_yesterday(registry=registry, now=fixed_now)

    assert result.diary_date == yesterday
    assert result.skipped_reason is None


# --------------------------------------------------------------------------
# DiaryReflectionLoop._seconds_until_next_fire
# --------------------------------------------------------------------------


def _make_loop(*, fire_hour: int, now: datetime) -> DiaryReflectionLoop:
    # Reflector/registry aren't used for these pure-timing assertions.
    reflector = DiaryReflector.__new__(DiaryReflector)
    return DiaryReflectionLoop(
        reflector=reflector,  # type: ignore[arg-type]
        registry=object(),
        fire_hour_local=fire_hour,
        clock=lambda: now,
    )


def test_seconds_until_next_fire_future_same_day():
    now = datetime(2026, 4, 20, 1, 0, 0)  # 01:00, fire at 02:00 → 1 h from now
    loop = _make_loop(fire_hour=2, now=now)
    seconds = loop._seconds_until_next_fire()
    assert seconds == pytest.approx(3600.0, rel=1e-3)


def test_seconds_until_next_fire_rolls_to_next_day():
    # 03:00 on 04-20, fire at 02:00 → next fire is 02:00 on 04-21 = 23 h.
    now = datetime(2026, 4, 20, 3, 0, 0)
    loop = _make_loop(fire_hour=2, now=now)
    seconds = loop._seconds_until_next_fire()
    assert seconds == pytest.approx(23 * 3600.0, rel=1e-3)


def test_seconds_until_next_fire_at_exact_hour_rolls_forward():
    # Sitting exactly at fire hour → target <= now, so we roll forward a day.
    now = datetime(2026, 4, 20, 2, 0, 0)
    loop = _make_loop(fire_hour=2, now=now)
    seconds = loop._seconds_until_next_fire()
    assert seconds == pytest.approx(24 * 3600.0, rel=1e-3)


def test_invalid_fire_hour_raises():
    with pytest.raises(ValueError):
        DiaryReflectionLoop(
            reflector=None,  # type: ignore[arg-type]
            registry=object(),
            fire_hour_local=24,
        )
    with pytest.raises(ValueError):
        DiaryReflectionLoop(
            reflector=None,  # type: ignore[arg-type]
            registry=object(),
            fire_hour_local=-1,
        )


# --------------------------------------------------------------------------
# DiaryReflectionLoop lifecycle
# --------------------------------------------------------------------------


class _FakeReflector:
    def __init__(self):
        self.calls: list[datetime] = []

    async def reflect_yesterday(self, *, registry):  # noqa: ARG002
        self.calls.append(datetime.now())
        return type(
            "R",
            (),
            {
                "diary_date": date(2026, 4, 19),
                "stats": {"add": 0, "strengthen": 0, "supersede": 0, "no_op": 1, "rejected": 0},
                "skipped_reason": None,
                "error": None,
            },
        )()


@pytest.mark.asyncio
async def test_loop_stop_before_first_fire(tmp_path: Path):
    fake_reflector = _FakeReflector()
    now = datetime(2026, 4, 20, 1, 0, 0)  # well before fire hour
    loop = DiaryReflectionLoop(
        reflector=fake_reflector,  # type: ignore[arg-type]
        registry=object(),
        fire_hour_local=2,
        clock=lambda: now,
    )
    loop.start()
    # Immediately stop — should never fire.
    await loop.stop()
    assert fake_reflector.calls == []


@pytest.mark.asyncio
async def test_loop_fires_when_wait_completes(monkeypatch):
    """Simulate wait expiring: sleeper raises TimeoutError so reflect fires."""
    fake_reflector = _FakeReflector()

    fire_count = {"n": 0}

    # Patch asyncio.wait_for so the first "wait for stop event" returns via
    # TimeoutError — triggering a fire — and the second returns normally so
    # the loop exits.

    async def fake_wait_for(awaitable, timeout):  # noqa: ARG001
        fire_count["n"] += 1
        if fire_count["n"] == 1:
            # close coroutine to avoid "never awaited" warning
            try:
                awaitable.close()
            except Exception:
                pass
            raise asyncio.TimeoutError
        # Second call: act as if stop_event was set.
        try:
            awaitable.close()
        except Exception:
            pass
        return None

    monkeypatch.setattr(
        "oh_my_agent.memory.diary_reflector.asyncio.wait_for",
        fake_wait_for,
    )
    loop = DiaryReflectionLoop(
        reflector=fake_reflector,  # type: ignore[arg-type]
        registry=object(),
        fire_hour_local=2,
        clock=lambda: datetime(2026, 4, 20, 1, 0, 0),
    )
    loop.start()
    await asyncio.sleep(0.05)
    await loop.stop()
    # Shim also intercepts the stop() wait_for, but by then the loop has
    # already fired once.
    assert len(fake_reflector.calls) >= 1


@pytest.mark.asyncio
async def test_loop_start_is_idempotent():
    fake_reflector = _FakeReflector()
    loop = DiaryReflectionLoop(
        reflector=fake_reflector,  # type: ignore[arg-type]
        registry=object(),
        fire_hour_local=2,
        clock=lambda: datetime(2026, 4, 20, 1, 0, 0),
    )
    loop.start()
    first_task = loop._task
    loop.start()
    assert loop._task is first_task
    await loop.stop()


# --------------------------------------------------------------------------
# System-block stripping (Plan B.5.1)
# --------------------------------------------------------------------------


def _build_diary(*blocks: str) -> str:
    return "\n\n".join(blocks)


SYSTEM_BLOCK = (
    "## 09:00:00 · discord#100 · thread:t · system:runtime\n"
    "Task `abc` queued.\n\n**Output detail**\nrich automation result"
)
USER_BLOCK = (
    "## 10:00:00 · discord#100 · thread:t · user:alice\n"
    "> we should ship sooner"
)
ASSIST_BLOCK = (
    "## 10:01:00 · discord#100 · thread:t · assistant:claude\n"
    "ok"
)


@pytest.mark.asyncio
async def test_reflect_strips_system_blocks_before_prompt(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    target_date = date(2026, 4, 21)
    (diary_dir / f"{target_date.isoformat()}.md").write_text(
        _build_diary(SYSTEM_BLOCK, USER_BLOCK, ASSIST_BLOCK), encoding="utf-8"
    )
    store = JudgeStore(memory_dir=tmp_path / "memory")
    await store.load()
    registry = StubRegistry(['{"actions":[{"op":"no_op","reason":"x"}]}'])
    reflector = DiaryReflector(diary_dir=diary_dir, store=store)

    result = await reflector.reflect(diary_date=target_date, registry=registry)

    assert result.skipped_reason is None
    prompt = registry.calls[0][0]
    assert "system:runtime" not in prompt
    assert "rich automation result" not in prompt
    # User and assistant content survives the strip pass.
    assert "we should ship sooner" in prompt
    assert "assistant:claude" in prompt


@pytest.mark.asyncio
async def test_reflect_skips_when_only_system_blocks(tmp_path: Path):
    diary_dir = tmp_path / "diary"
    diary_dir.mkdir()
    target_date = date(2026, 4, 22)
    sys_only = _build_diary(SYSTEM_BLOCK, SYSTEM_BLOCK.replace("09:00:00", "10:00:00"))
    (diary_dir / f"{target_date.isoformat()}.md").write_text(sys_only, encoding="utf-8")
    store = JudgeStore(memory_dir=tmp_path / "memory")
    await store.load()
    registry = StubRegistry(["should not be called"])
    reflector = DiaryReflector(diary_dir=diary_dir, store=store)

    result = await reflector.reflect(diary_date=target_date, registry=registry)

    assert result.skipped_reason == "diary_empty"
    assert registry.calls == []
