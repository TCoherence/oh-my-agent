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
from typing import Any, Awaitable, Callable

from oh_my_agent.memory.diary_reflector import ReflectionResult
from oh_my_agent.memory.judge_store import JudgeStore, parse_judge_actions

logger = logging.getLogger(__name__)


_MAX_DIARY_CHARS = 24_000
_PER_DAY_CHARS = 3_500
_WINDOW_DAYS = 7


_WEEKLY_REFLECT_PROMPT = """\
You are a 7-day-window memory judge. You will be given a week of conversation \
diary excerpts (one entry per day, possibly with missing days marked) between \
the user and the assistant(s) across all threads.

Your job: identify long-term USER patterns visible only at the weekly scale — \
recurring preferences, evolving workflows, sustained topics. The daily \
reflector already captures single-day signals; target what the daily judge \
CANNOT see in a single day.

Allowed ops:
- "add": brand new memory not yet in the store.
  HARD REQUIREMENT: evidence MUST quote at least 2 distinct dated diary \
sections. Single-day evidence → emit no_op or strengthen instead.
  Required: summary, category, scope, confidence, evidence (a short user-side \
snippet, each citation marked with its [YYYY-MM-DD]).
- "strengthen": an existing memory was reinforced by this week's evidence.
  Required: id, evidence. Optional: confidence_bump (0.0-0.20).
- "supersede": an existing memory was replaced by a contradictory statement \
this week.
  Required: old_id, new_summary, category, scope, confidence, evidence \
(≥ 2 dated sections).
- "no_op": nothing this week deserves a new long-term memory.
  Required: reason.

You MUST always output something — at minimum a single no_op action.

Categories: preference | workflow | project_knowledge | fact
Scopes:     global_user | workspace | skill | thread

Strict rules (stricter than daily — weekly mistakes propagate further):
- Use ONLY evidence from user turns (quoted ``> ...`` lines or ``user:`` headers).
- Each memory must be ONE concise sentence about the user.
- Cite every evidence snippet with its date in [YYYY-MM-DD] form.
- Days marked ``(no diary)`` provide no evidence — count only days with content.
- Do NOT memorize: one-off task details, this week's plans, slash command \
usage, file paths, implementation choices, debugging steps, speculation, \
specific PR / issue / commit numbers, library versions tried this week.
- If a new observation paraphrases an existing memory → emit "strengthen", \
do NOT "add" a duplicate.
- If a new observation contradicts an existing memory → emit "supersede".
- Prefer strengthen / supersede / no_op over add when uncertain.
- For "add": confidence ≥ 0.80 AND ≥ 2 distinct dates required. Otherwise no_op.
- If the week's signal is single-day or weak → emit a single no_op.

Current active memories ({active_count} entries):
{active_memories}

Diary (week ending {week_end_date}, window {week_start_date} → {week_end_date}):
{diary_text}

Output ONLY a JSON object with this exact shape (no markdown, no preamble):
{{"actions": [
  {{"op": "add", "summary": "...", "category": "preference", "scope": "global_user", "confidence": 0.85, "evidence": "[YYYY-MM-DD] ... ; [YYYY-MM-DD] ..."}},
  {{"op": "strengthen", "id": "abc123", "evidence": "[YYYY-MM-DD] ..."}},
  {{"op": "supersede", "old_id": "def456", "new_summary": "...", "category": "...", "scope": "...", "confidence": 0.85, "evidence": "[YYYY-MM-DD] ... ; [YYYY-MM-DD] ..."}},
  {{"op": "no_op", "reason": "..."}}
]}}
"""


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
        text, present = self._collect_week_text(week_end_date)
        if present == 0:
            return ReflectionResult(
                diary_date=week_end_date,
                diary_path=self._path_for(week_end_date),
                actions=[],
                stats={"add": 0, "strengthen": 0, "supersede": 0, "no_op": 0, "rejected": 0},
                skipped_reason="diary_missing",
            )
        active_context = self._store.to_judge_context()
        import json as _json
        active_text = (
            _json.dumps(active_context, ensure_ascii=False, indent=2)
            if active_context else "[]"
        )
        prompt = _WEEKLY_REFLECT_PROMPT.format(
            active_count=len(active_context),
            active_memories=active_text,
            week_end_date=week_end_date.isoformat(),
            week_start_date=week_start.isoformat(),
            diary_text=text,
        )

        try:
            _agent, response = await registry.run(prompt, run_label=run_label)
        except Exception as exc:
            logger.warning(
                "weekly_reflect agent_exception week_end=%s err=%s",
                week_end_date,
                exc,
            )
            return ReflectionResult(
                diary_date=week_end_date,
                diary_path=self._path_for(week_end_date),
                actions=[],
                stats={"add": 0, "strengthen": 0, "supersede": 0, "no_op": 0, "rejected": 0},
                error=f"agent_exception: {exc}",
            )
        if getattr(response, "error", None):
            return ReflectionResult(
                diary_date=week_end_date,
                diary_path=self._path_for(week_end_date),
                actions=[],
                stats={"add": 0, "strengthen": 0, "supersede": 0, "no_op": 0, "rejected": 0},
                raw_response=response.text or "",
                error=response.error,
            )
        actions = parse_judge_actions(response.text or "")
        stats = await self._store.apply_actions(
            actions,
            thread_id=None,
            skill_name=None,
            source_workspace=None,
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
            diary_path=self._path_for(week_end_date),
            actions=actions,
            stats=stats,
            raw_response=response.text or "",
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
        sleeper: Callable[[float], Awaitable[None]] | None = None,
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
        self._sleeper = sleeper or asyncio.sleep
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
