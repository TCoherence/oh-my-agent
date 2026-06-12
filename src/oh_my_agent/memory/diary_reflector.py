"""Cross-day memory reflection driven by the session diary.

The :class:`~oh_my_agent.memory.judge.Judge` only sees a single thread at a
time — by design, it fires on idle / keyword / ``/memorize``. That means
patterns that only emerge when you look at an entire day's activity across
threads (e.g. "the user spent the afternoon wiring up usage telemetry — the
next day's suggestions should stay on that topic") never make it into
persistent memory.

``DiaryReflector`` closes that gap. Once per day (default) it reads
yesterday's human-readable diary file, hands the text to an agent with a
"long-horizon review" prompt, and pipes the resulting actions through the
same :class:`~oh_my_agent.memory.judge_store.JudgeStore.apply_actions`
pipeline the real-time Judge uses — so the dedup / supersede / confidence
machinery is shared.

The reflector is deliberately read-only over the diary file: diaries are
operator-visible artefacts and stay append-only.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable

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


_MAX_DIARY_CHARS = 24_000  # stay comfortably within typical context budgets


# Shared rule blocks (allowed ops, categories/scopes, blocklist, dedup rules,
# output shape) come from oh_my_agent.memory.judge — the single source of
# truth for the judge prompt contract. Only daily-specific wording lives here.
_DIARY_REFLECT_PROMPT = (
    """\
You are a long-horizon memory judge. You will be given a full day's worth of \
conversation diary between the user and the assistant(s) across all threads.

Your job: decide what (if anything) about the USER deserves to be remembered \
LONG-TERM based on patterns visible across the whole day. Prefer cross-thread \
signals and repeated behaviours over one-off task detail.

Allowed ops:
"""
    + build_judge_ops_contract(
        strengthen_source="today's evidence",
        supersede_event="was replaced by a contradictory statement today",
        no_op_subject="in today's diary",
    )
    + """

Strict rules:
"""
    + JUDGE_RULE_USER_EVIDENCE_DIARY
    + "\n"
    + JUDGE_RULE_ONE_SENTENCE
    + "\n"
    + build_judge_blocklist_rule(plans="today's")
    + """
- Confidence: 0.85+ for explicit stable preferences observed more than once today. \
0.5-0.7 for single-day inferences.
"""
    + JUDGE_RULES_DEDUP
    + """
- Prefer strengthen/supersede over add when uncertain.
- If the day shows no real cross-thread signal → emit a single no_op.

Current active memories ({active_count} entries):
{active_memories}

Diary (date: {diary_date}):
{diary_text}

"""
    + build_judge_output_shape(confidence="0.9", add_evidence="...")
    + "\n"
)


@dataclass
class ReflectionResult:
    """Outcome of one reflection pass."""

    diary_date: date
    diary_path: Path
    actions: list[dict[str, Any]]
    stats: dict[str, int]
    raw_response: str = ""
    error: str | None = None
    skipped_reason: str | None = None


class DiaryReflector:
    """Reads a day's diary and feeds a Judge-style action list to ``JudgeStore``."""

    def __init__(
        self,
        *,
        diary_dir: str | Path,
        store: JudgeStore,
        max_diary_chars: int = _MAX_DIARY_CHARS,
    ) -> None:
        self._diary_dir = Path(diary_dir).expanduser().resolve()
        self._store = store
        self._max_diary_chars = max_diary_chars

    @property
    def diary_dir(self) -> Path:
        return self._diary_dir

    def _path_for(self, day: date) -> Path:
        return self._diary_dir / f"{day.isoformat()}.md"

    @staticmethod
    def _skipped(diary_date: date, path: Path, reason: str) -> ReflectionResult:
        return ReflectionResult(
            diary_date=diary_date,
            diary_path=path,
            actions=[],
            stats=zero_stats(),
            skipped_reason=reason,
        )

    async def reflect(
        self,
        *,
        diary_date: date,
        registry: Any,
        run_label: str = "diary_reflect",
    ) -> ReflectionResult:
        """Run a reflection pass over the diary for ``diary_date``.

        Returns a :class:`ReflectionResult` describing what was applied. If
        the diary file is missing or empty, ``skipped_reason`` is set and
        no store mutation happens.
        """
        path = self._path_for(diary_date)
        if not path.exists():
            return self._skipped(diary_date, path, "diary_missing")
        diary_text = path.read_text(encoding="utf-8").strip()
        if not diary_text:
            return self._skipped(diary_date, path, "diary_empty")
        # Drop ``system:`` blocks (automation status pings) before reflection
        # so task metadata cannot leak back into long-term memory via the
        # apply_actions path.
        diary_text = strip_system_blocks(diary_text).strip()
        if not diary_text:
            return self._skipped(diary_date, path, "diary_empty")
        # Truncation policy: keep the head of the day (first N chars). Intra-day
        # patterns usually form in the morning/early-afternoon block; the tail
        # tends to be follow-ups.
        if len(diary_text) > self._max_diary_chars:
            diary_text = diary_text[: self._max_diary_chars] + "\n...[diary truncated]"

        active_context = self._store.to_judge_context()
        prompt = _DIARY_REFLECT_PROMPT.format(
            active_count=len(active_context),
            active_memories=dump_judge_context(active_context),
            diary_date=diary_date.isoformat(),
            diary_text=diary_text,
        )

        actions, stats, raw_text, error = await run_judge_actions(
            prompt, registry, self._store, run_label=run_label
        )
        if error is not None:
            logger.warning("diary_reflect error date=%s err=%s", diary_date, error)
            return ReflectionResult(
                diary_date=diary_date,
                diary_path=path,
                actions=[],
                stats=stats,
                raw_response=raw_text,
                error=error,
            )
        logger.info(
            "diary_reflect applied date=%s actions=%d stats=%s",
            diary_date,
            len(actions),
            stats,
        )
        return ReflectionResult(
            diary_date=diary_date,
            diary_path=path,
            actions=actions,
            stats=stats,
            raw_response=raw_text,
        )

    async def reflect_yesterday(
        self,
        *,
        registry: Any,
        now: datetime | None = None,
    ) -> ReflectionResult:
        today = (now or datetime.now()).date()
        return await self.reflect(
            diary_date=today - timedelta(days=1),
            registry=registry,
        )


class DiaryReflectionLoop:
    """Fires ``reflector.reflect_yesterday`` once per day at a configured local hour.

    Simpler than wiring into the YAML automation scheduler: this only has one
    firing time and never resumes mid-day, so a dedicated asyncio task is
    cheaper than coupling through cron parsing + dispatch.
    """

    def __init__(
        self,
        *,
        reflector: DiaryReflector,
        registry: Any,
        fire_hour_local: int = 2,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 0 <= fire_hour_local <= 23:
            raise ValueError(f"fire_hour_local must be 0-23, got {fire_hour_local}")
        self._reflector = reflector
        self._registry = registry
        self._fire_hour_local = int(fire_hour_local)
        self._clock = clock or datetime.now
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="diary-reflector:daily")

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
        target = datetime.combine(now.date(), time(hour=self._fire_hour_local))
        if target <= now:
            target = target + timedelta(days=1)
        return max(1.0, (target - now).total_seconds())

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            wait_seconds = self._seconds_until_next_fire()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
                return  # stop() was called
            except asyncio.TimeoutError:
                pass
            try:
                result = await self._reflector.reflect_yesterday(registry=self._registry)
                logger.info(
                    "diary_reflect_loop fired date=%s applied=%s skipped=%s error=%s",
                    result.diary_date,
                    result.stats,
                    result.skipped_reason,
                    result.error,
                )
            except Exception:
                logger.warning("diary_reflect_loop unexpected failure", exc_info=True)
