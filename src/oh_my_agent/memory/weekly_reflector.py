"""Weekly cross-day memory reflection — a 7-day window on top of daily.

Where :class:`~oh_my_agent.memory.diary_reflector.DiaryReflector` looks at a
single day, ``WeeklyReflector`` looks at the trailing 7 complete days
(yesterday and the 6 days before). It targets patterns visible only at the
weekly scale — recurring preferences, evolving workflows, "this is the third
time the user has asked about X" signals a daily-only judge can never catch.

Uses the same :class:`~oh_my_agent.memory.judge_store.JudgeStore.apply_actions`
pipeline as daily, with stricter prompt rules (≥ 2 distinct dates required for
``add``) to avoid creating duplicates of memory entries the daily reflector
may already have added.

Window semantics — anchored on yesterday so the latest complete day is
included. ``reflect_last_week()`` computes::

    end_date   = today - 1                             # yesterday
    window     = [end_date - 6, end_date]  inclusive    # 7 complete days
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable

from oh_my_agent.memory.diary_reflector import ReflectionResult
from oh_my_agent.memory.judge import (
    JUDGE_RULE_ONE_SENTENCE,
    JUDGE_RULE_USER_EVIDENCE_DIARY,
    JUDGE_RULES_DEDUP,
    build_judge_blocklist_rule,
    build_judge_ops_contract,
    build_judge_output_shape,
)
from oh_my_agent.memory.judge_store import (
    JudgeStore,
    dump_judge_context,
    run_judge_actions,
    zero_stats,
)
from oh_my_agent.memory.session_diary import strip_system_blocks

logger = logging.getLogger(__name__)


_MAX_DIARY_CHARS = 24_000
_PER_DAY_CHARS = 3_500
_WINDOW_DAYS = 7


# Shared rule blocks (allowed ops, categories/scopes, blocklist, dedup rules,
# output shape) come from oh_my_agent.memory.judge — the single source of
# truth for the judge prompt contract. Only weekly-specific wording lives here.
_WEEKLY_REFLECT_PROMPT = (
    """\
You are a 7-day-window memory judge. You will be given a week of conversation \
diary excerpts (one entry per day, possibly with missing days marked) between \
the user and the assistant(s) across all threads.

Your job: identify long-term USER patterns visible only at the weekly scale — \
recurring preferences, evolving workflows, sustained topics. The daily \
reflector already captures single-day signals; target what the daily judge \
CANNOT see in a single day.

Allowed ops:
"""
    + build_judge_ops_contract(
        add_qualifier=(
            "  HARD REQUIREMENT: evidence MUST quote at least 2 distinct dated diary "
            "sections. Single-day evidence → emit no_op or strengthen instead.\n"
        ),
        add_evidence="a short user-side snippet, each citation marked with its [YYYY-MM-DD]",
        strengthen_source="this week's evidence",
        supersede_event="was replaced by a contradictory statement this week",
        supersede_suffix=" (≥ 2 dated sections)",
        no_op_subject="this week",
        no_op_target="a new long-term memory",
    )
    + """

Strict rules (stricter than daily — weekly mistakes propagate further):
"""
    + JUDGE_RULE_USER_EVIDENCE_DIARY
    + "\n"
    + JUDGE_RULE_ONE_SENTENCE
    + """
- Cite every evidence snippet with its date in [YYYY-MM-DD] form.
- Days marked ``(no diary)`` provide no evidence — count only days with content.
"""
    + build_judge_blocklist_rule(
        plans="this week's",
        tail=(
            "speculation, specific PR / issue / commit numbers, "
            "library versions tried this week"
        ),
    )
    + "\n"
    + JUDGE_RULES_DEDUP
    + """
- Prefer strengthen / supersede / no_op over add when uncertain.
- For "add": confidence ≥ 0.80 AND ≥ 2 distinct dates required. Otherwise no_op.
- If the week's signal is single-day or weak → emit a single no_op.

Current active memories ({active_count} entries):
{active_memories}

Diary (week ending {week_end_date}, window {week_start_date} → {week_end_date}):
{diary_text}

"""
    + build_judge_output_shape(
        confidence="0.85",
        add_evidence="[YYYY-MM-DD] ... ; [YYYY-MM-DD] ...",
        strengthen_evidence="[YYYY-MM-DD] ...",
        supersede_evidence="[YYYY-MM-DD] ... ; [YYYY-MM-DD] ...",
    )
    + "\n"
)


class WeeklyReflector:
    """Reads the trailing 7 days of diaries and feeds Judge-style actions to the store."""

    def __init__(
        self,
        *,
        diary_dir: str | Path,
        store: JudgeStore,
        max_diary_chars: int = _MAX_DIARY_CHARS,
        per_day_chars: int = _PER_DAY_CHARS,
    ) -> None:
        self._diary_dir = Path(diary_dir).expanduser().resolve()
        self._store = store
        self._max_diary_chars = max_diary_chars
        self._per_day_chars = per_day_chars

    @property
    def diary_dir(self) -> Path:
        return self._diary_dir

    def _path_for(self, day: date) -> Path:
        return self._diary_dir / f"{day.isoformat()}.md"

    def _collect_week_text(self, end_date: date) -> tuple[str, int]:
        """Build the week's diary text for the prompt.

        Returns ``(text, present_day_count)``. Missing days are rendered with
        an explicit ``(no diary)`` placeholder so the prompt knows how
        continuous the evidence is. Present days are truncated to
        ``per_day_chars``; total length is capped by ``max_diary_chars``.
        Days are emitted oldest → newest.
        """
        sections: list[str] = []
        present = 0
        for offset in range(_WINDOW_DAYS - 1, -1, -1):
            day = end_date - timedelta(days=offset)
            path = self._path_for(day)
            header = f"## --- {day.isoformat()} ---"
            if not path.exists():
                sections.append(f"{header} (no diary)")
                continue
            try:
                body = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning("weekly_reflect read_failed date=%s err=%s", day, exc)
                sections.append(f"{header} (read error)")
                continue
            if not body:
                sections.append(f"{header} (empty)")
                continue
            body = strip_system_blocks(body).strip()
            if not body:
                sections.append(f"{header} (no user content)")
                continue
            if len(body) > self._per_day_chars:
                body = body[: self._per_day_chars] + "\n...[day truncated]"
            sections.append(f"{header}\n{body}")
            present += 1
        text = "\n\n".join(sections)
        if len(text) > self._max_diary_chars:
            text = text[: self._max_diary_chars] + "\n...[week truncated]"
        return text, present

    async def reflect(
        self,
        *,
        week_end_date: date,
        registry: Any,
        run_label: str = "weekly_reflect",
    ) -> ReflectionResult:
        """Reflect over the 7 days ending on ``week_end_date`` (inclusive)."""
        week_start = week_end_date - timedelta(days=_WINDOW_DAYS - 1)
        path = self._path_for(week_end_date)
        text, present = self._collect_week_text(week_end_date)
        if present == 0:
            return ReflectionResult(
                diary_date=week_end_date,
                diary_path=path,
                actions=[],
                stats=zero_stats(),
                skipped_reason="diary_missing",
            )
        active_context = self._store.to_judge_context()
        prompt = _WEEKLY_REFLECT_PROMPT.format(
            active_count=len(active_context),
            active_memories=dump_judge_context(active_context),
            week_end_date=week_end_date.isoformat(),
            week_start_date=week_start.isoformat(),
            diary_text=text,
        )

        actions, stats, raw_text, error = await run_judge_actions(
            prompt, registry, self._store, run_label=run_label
        )
        if error is not None:
            logger.warning(
                "weekly_reflect error week_end=%s err=%s", week_end_date, error
            )
            return ReflectionResult(
                diary_date=week_end_date,
                diary_path=path,
                actions=[],
                stats=stats,
                raw_response=raw_text,
                error=error,
            )
        logger.info(
            "weekly_reflect applied week_end=%s present_days=%d actions=%d stats=%s",
            week_end_date,
            present,
            len(actions),
            stats,
        )
        return ReflectionResult(
            diary_date=week_end_date,
            diary_path=path,
            actions=actions,
            stats=stats,
            raw_response=raw_text,
        )

    async def reflect_last_week(
        self,
        *,
        registry: Any,
        now: datetime | date | None = None,
    ) -> ReflectionResult:
        """Reflect over the 7 complete days ending **yesterday** (inclusive).

        Concretely, ``end_date = today - 1`` and the window is
        ``[end_date - 6, end_date]`` inclusive — the same 'yesterday' anchor
        as :meth:`DiaryReflector.reflect_yesterday`. Boundary expectation:
        called at any time on 2026-04-29 → reads files for 2026-04-22 …
        2026-04-28 (7 days).

        Accepts ``datetime`` or plain ``date`` for ``now`` so callers can
        anchor on either; the loop passes datetime, manual / test callers
        often have a date.
        """
        if now is None:
            today = datetime.now().date()
        elif isinstance(now, datetime):
            today = now.date()
        else:
            today = now
        return await self.reflect(
            week_end_date=today - timedelta(days=1),
            registry=registry,
        )


class WeeklyReflectionLoop:
    """Fires ``reflector.reflect_last_week`` once per week at a configured local hour.

    Mirrors :class:`DiaryReflectionLoop` but with weekly cadence. Uses naive
    local-time arithmetic — same DST gotcha as daily; revisit when daily moves
    to timezone-aware scheduling.
    """

    def __init__(
        self,
        *,
        reflector: WeeklyReflector,
        registry: Any,
        fire_dow_local: int = 1,
        fire_hour_local: int = 3,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 0 <= fire_dow_local <= 6:
            raise ValueError(
                f"fire_dow_local must be 0-6 (Mon=0...Sun=6), got {fire_dow_local}"
            )
        if not 0 <= fire_hour_local <= 23:
            raise ValueError(f"fire_hour_local must be 0-23, got {fire_hour_local}")
        self._reflector = reflector
        self._registry = registry
        self._fire_dow_local = int(fire_dow_local)
        self._fire_hour_local = int(fire_hour_local)
        self._clock = clock or datetime.now
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="weekly-reflector:weekly")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None

    def _seconds_until_next_fire(self) -> float:
        now = self._clock()
        days_until = (self._fire_dow_local - now.weekday()) % 7
        target = datetime.combine(
            now.date() + timedelta(days=days_until),
            time(hour=self._fire_hour_local),
        )
        if target <= now:
            target = target + timedelta(days=7)
        return max(1.0, (target - now).total_seconds())

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            wait_seconds = self._seconds_until_next_fire()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
                return
            except asyncio.TimeoutError:
                pass
            try:
                result = await self._reflector.reflect_last_week(registry=self._registry)
                logger.info(
                    "weekly_reflect_loop fired week_end=%s applied=%s skipped=%s error=%s",
                    result.diary_date,
                    result.stats,
                    result.skipped_reason,
                    result.error,
                )
            except Exception:
                logger.warning("weekly_reflect_loop unexpected failure", exc_info=True)
