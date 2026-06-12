"""M1 PR2 — Implicit feedback collection for self-evaluation memory.

Wires Discord reaction events into the judge store so a user's quick
👍/👎/✅/❌/⚠️ on an automation post becomes a self_eval ``MemoryEntry``
with ``feedback_source=implicit``.

The chat path's `Judge.run()` handles user-fact extraction; this module
handles the orthogonal "did the user like the output" signal that the
LLM self-eval (M1 PR1) and the explicit /feedback command (M1 PR3) also
write into. PR4 consolidates the three sources into a unified signal
merge step.

Also exposes a daily scan worker that converts "no reply + no reaction
within 24h" into a weak negative signal — the heuristic for silent
indifference.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)


# Emoji vocabulary for implicit reactions. Keep small + explicit so the
# user's intent maps clearly to a signal. Anything outside this set is
# ignored (e.g. casual 🚀 / 😊 / etc.).
_POSITIVE_EMOJI = {"👍", "✅"}
_NEGATIVE_EMOJI = {"👎", "❌", "⚠️"}

# Confidence band for implicit signals. Plan says 0.4–0.6. We pick:
# - reaction add: 0.55 (user took an active action)
# - reaction remove: 0.50 (user undid a signal — weaker)
# - no-reply 24h: 0.40 (passive indifference, weakest)
_CONFIDENCE_REACTION_ADD = 0.55
_CONFIDENCE_REACTION_REMOVE = 0.50
_CONFIDENCE_NO_REPLY = 0.40
# M1 PR3: explicit user feedback is the highest-trust signal (user
# deliberately rated this task) → plan says confidence 0.9.
_CONFIDENCE_EXPLICIT = 0.9


@dataclass
class _AutomationPostLookup:
    """Resolved automation_posts row (subset we care about)."""

    task_id: str
    automation_name: str | None
    posted_at: datetime
    skill_name: str | None
    source_workspace: str | None


@dataclass
class _TaskLockEntry:
    """Per-task lock with a refcount so idle entries can be evicted."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refs: int = 0


class FeedbackCollector:
    """Collects implicit user feedback into self_eval ``MemoryEntry`` rows.

    Two entry points:

    - :meth:`record_reaction` is called by the Discord platform on each
      reaction add/remove for messages in automation_posts.
    - :meth:`scan_no_reply_negative` is run on a background timer by the
      runtime service; emits weak-negative entries for automation posts
      that received no engagement within ``window_hours``.

    All writes go through ``JudgeStore.apply_actions`` so the model
    validator (``self_eval`` ↔ ``automation`` binding) still enforces
    schema invariants.
    """

    def __init__(
        self,
        *,
        memory_store: Any,
        judge_store: Any,
        no_reply_window_hours: int = 24,
        no_reply_max_age_hours: int = 24 * 7,
    ) -> None:
        self._memory_store = memory_store
        self._judge_store = judge_store
        self._no_reply_window_hours = int(no_reply_window_hours)
        # Codex round-1 catch: prevent backfilling historical posts as
        # silent negatives on startup. Anything older than this is too
        # stale to be meaningfully a "user ignored it" signal.
        self._no_reply_max_age_hours = int(no_reply_max_age_hours)
        # Codex round-1 catch: per-task lock prevents the scan worker and a
        # near-simultaneous reaction handler from both passing the dedupe
        # check and writing contradictory self_eval entries.
        self._task_locks: dict[str, _TaskLockEntry] = {}

    @asynccontextmanager
    async def _locked_task(self, task_id: str) -> AsyncIterator[None]:
        """Serialize feedback writes for one task_id.

        Entries are refcounted and dropped when the last holder releases, so
        the dict only contains locks for in-flight operations (it previously
        grew by one Lock per reacted/scanned task_id forever).
        """
        entry = self._task_locks.get(task_id)
        if entry is None:
            entry = _TaskLockEntry()
            self._task_locks[task_id] = entry
        # Incremented before any await: a concurrent waiter keeps refs > 0,
        # which keeps the entry pinned in the dict until everyone releases.
        entry.refs += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.refs -= 1
            if entry.refs == 0:
                self._task_locks.pop(task_id, None)

    @staticmethod
    def classify_emoji(emoji: str) -> Literal["positive", "negative", "unknown"]:
        """Map a raw reaction emoji into a signal direction."""
        if emoji in _POSITIVE_EMOJI:
            return "positive"
        if emoji in _NEGATIVE_EMOJI:
            return "negative"
        return "unknown"

    async def record_reaction(
        self,
        *,
        message_id: str,
        emoji: str,
        action: Literal["add", "remove"],
        actor_id: str | None = None,
    ) -> bool:
        """Record one reaction add/remove against an automation post.

        Returns True on a real write, False on skip (unknown emoji, no
        matching automation post, etc.). Failures are caught + logged
        but never raised so Discord's event loop stays alive.
        """
        signal = self.classify_emoji(emoji)
        if signal == "unknown":
            return False
        lookup = await self._lookup_automation_post(message_id)
        if lookup is None:
            return False

        # M1 PR2 simplification: only the add path writes a new self_eval
        # entry. A 'remove' is recorded as a counter-signal (i.e. removing
        # 👎 = the user no longer disagrees). PR4 will consolidate the
        # signal merge across LLM + explicit + implicit sources; for MVP
        # we just write the latest implicit observation.
        quality: Literal["pass", "fail"]
        if action == "remove":
            # Flip the inferred direction: removing a positive = mild
            # negative ("revoked endorsement"), removing a negative = mild
            # positive ("retracted complaint").
            quality = "pass" if signal == "negative" else "fail"
            confidence = _CONFIDENCE_REACTION_REMOVE
            reason_phrase = f"user removed {emoji} reaction"
        else:
            quality = "pass" if signal == "positive" else "fail"
            confidence = _CONFIDENCE_REACTION_ADD
            reason_phrase = f"user reacted {emoji}"

        # Codex round-1 catch: serialize record + scan against this task so
        # they don't both pass the dedupe pre-check.
        async with self._locked_task(lookup.task_id):
            return await self._write_self_eval(
                lookup=lookup,
                quality=quality,
                confidence=confidence,
                reason=reason_phrase,
                feedback_source="implicit",
                actor_id=actor_id,
            )

    async def record_explicit_feedback(
        self,
        *,
        task_id: str,
        verdict: Literal["good", "bad"],
        note: str | None = None,
        actor_id: str | None = None,
    ) -> bool:
        """M1 PR3: record an explicit /feedback rating.

        Unlike reaction-based input, the user has typed task_id directly,
        so we resolve the automation context via the task_id → recent
        automation_post lookup (rather than message_id). On automation
        tasks: writes a self_eval entry with feedback_source="explicit"
        + confidence 0.9. On manual tasks (no automation): returns False
        (the strict self_eval ↔ automation binding blocks the write).
        """
        lookup = await self._lookup_automation_post_by_task(task_id)
        if lookup is None:
            return False
        quality: Literal["pass", "fail"] = "pass" if verdict == "good" else "fail"
        reason_phrase = "user explicit rating"
        if note:
            # Cap note inline so summary stays under MemoryEntry width.
            reason_phrase = f"user explicit rating: {note[:160]}"
        async with self._locked_task(lookup.task_id):
            return await self._write_self_eval(
                lookup=lookup,
                quality=quality,
                confidence=_CONFIDENCE_EXPLICIT,
                reason=reason_phrase,
                feedback_source="explicit",
                actor_id=actor_id,
            )

    async def scan_no_reply_negative(self) -> int:
        """Daily worker: find automation posts older than ``window_hours``
        with NO user engagement (no reactions, no follow-up thread, no
        prior self_eval) and write weak-negative self_eval entries.

        Codex round-1 hardening:
        - Skip posts whose ``follow_up_thread_id`` is set (user replied).
        - Skip posts older than ``no_reply_max_age_hours`` (default 7 days)
          to prevent startup backfill of historical posts as silent
          negatives.
        - Acquire per-task lock around the dedupe + write so concurrent
          ``record_reaction`` calls can't double-write.

        Returns the number of entries written.
        """
        try:
            posts = await self._memory_store.list_automation_posts_older_than(
                hours=self._no_reply_window_hours
            )
        except AttributeError:
            logger.debug(
                "FeedbackCollector.scan_no_reply_negative: memory store lacks "
                "list_automation_posts_older_than(); skipping"
            )
            return 0
        now = datetime.now(timezone.utc)
        max_age = timedelta(hours=self._no_reply_max_age_hours)
        written = 0
        for post in posts:
            task_id = post.get("task_id")
            automation_name = post.get("automation_name")
            posted_at_raw = post.get("posted_at")
            if not task_id or not automation_name:
                continue
            # Skip user-engaged posts. The follow-up-thread bit is set when
            # a reply spawns a thread (see GatewayManager / DiscordChannel
            # automation reply path).
            if post.get("follow_up_thread_id"):
                continue
            # Cap age so startup doesn't backfill ancient posts.
            posted_at = _parse_ts(posted_at_raw) or now
            if (now - posted_at) > max_age:
                continue
            lookup = _AutomationPostLookup(
                task_id=str(task_id),
                automation_name=str(automation_name),
                posted_at=posted_at,
                skill_name=post.get("skill_name"),
                source_workspace=post.get("source_workspace"),
            )
            # Per-task lock: covers dedupe check + write, race-safe with
            # record_reaction() running concurrently on the same task.
            async with self._locked_task(lookup.task_id):
                # Codex M1 PR4 fix: dedupe on existing IMPLICIT signal, not
                # any self_eval. The LLM self_eval now writes a per-task
                # entry on completion, so checking "any self_eval" would
                # suppress the no-reply signal forever. We only skip if the
                # user already engaged (reaction → implicit signal present)
                # or a prior scan already added a no-reply implicit signal.
                if self._has_implicit_signal_for_task(lookup.task_id):
                    continue
                ok = await self._write_self_eval(
                    lookup=lookup,
                    quality="fail",  # weakest signal still maps to fail
                    confidence=_CONFIDENCE_NO_REPLY,
                    reason=f"no reply or reaction within {self._no_reply_window_hours}h",
                    feedback_source="implicit",
                    actor_id=None,
                )
                if ok:
                    written += 1
        if written:
            logger.info(
                "FeedbackCollector.scan_no_reply_negative wrote %d weak-negative entries",
                written,
            )
        return written

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _lookup_automation_post_by_task(
        self, task_id: str
    ) -> _AutomationPostLookup | None:
        """Resolve task_id → automation post via memory_store.

        Used by ``record_explicit_feedback`` since /feedback typers carry
        a task_id, not a message_id. Returns None if there's no
        automation_post for the task (e.g. manual /task_start runs).
        """
        try:
            row = await self._memory_store.get_automation_post_by_task(
                task_id=str(task_id)
            )
        except AttributeError:
            logger.debug(
                "Memory store lacks get_automation_post_by_task(); skipping"
            )
            return None
        except Exception as exc:
            logger.warning("automation_posts task lookup failed: %s", exc)
            return None
        if not row:
            return None
        automation_name = row.get("automation_name")
        if not automation_name:
            return None
        return _AutomationPostLookup(
            task_id=str(task_id),
            automation_name=str(automation_name),
            posted_at=_parse_ts(row.get("posted_at")) or datetime.now(timezone.utc),
            skill_name=row.get("skill_name"),
            source_workspace=row.get("source_workspace"),
        )

    async def _lookup_automation_post(self, message_id: str) -> _AutomationPostLookup | None:
        """Resolve message_id → (task_id, automation_name) via the
        automation_posts table.

        Returns ``None`` if the message isn't tracked as an automation post
        OR if the post has no associated task_id / automation_name.
        """
        try:
            row = await self._memory_store.get_automation_post_by_message(
                message_id=str(message_id)
            )
        except AttributeError:
            logger.debug(
                "Memory store lacks get_automation_post_by_message(); skipping"
            )
            return None
        except Exception as exc:
            logger.warning("automation_posts lookup failed: %s", exc)
            return None
        if not row:
            return None
        task_id = row.get("task_id")
        automation_name = row.get("automation_name")
        if not task_id or not automation_name:
            return None
        posted_at_raw = row.get("posted_at")
        return _AutomationPostLookup(
            task_id=str(task_id),
            automation_name=str(automation_name),
            posted_at=_parse_ts(posted_at_raw) or datetime.now(timezone.utc),
            skill_name=row.get("skill_name"),
            source_workspace=row.get("source_workspace"),
        )

    def _has_implicit_signal_for_task(self, task_id: str) -> bool:
        """True if an active self_eval entry for this task already carries an
        ``implicit`` signal.

        Codex M1 PR4: the no-reply scan dedupes on this (not "any self_eval")
        because the LLM self_eval writes a per-task entry on completion. We
        want the no-reply weak-negative to still merge into an LLM-only entry,
        but never pile on top of an existing implicit signal (user reacted,
        or a prior scan already fired).
        """
        active = self._judge_store.get_active()
        for entry in active:
            if entry.category != "self_eval":
                continue
            # Match by evidence thread_id (the per-task key) ending with task.
            matches_task = any(
                str(ev.thread_id or "").endswith(f"task:{task_id}")
                for ev in entry.evidence_log
            )
            if not matches_task:
                continue
            for sig in entry.signals:
                if str(sig.get("source")) == "implicit":
                    return True
        return False

    async def _write_self_eval(
        self,
        *,
        lookup: _AutomationPostLookup,
        quality: Literal["pass", "fail"],
        confidence: float,
        reason: str,
        feedback_source: Literal["implicit", "explicit"],
        actor_id: str | None,
    ) -> bool:
        """Merge a feedback signal into the per-task self_eval entry.

        M1 PR4: routes through ``JudgeStore.upsert_self_eval_signal`` so the
        three feedback sources (llm_judge / implicit / explicit) consolidate
        into ONE entry per task, with a ``signals`` list and confidence =
        max across sources. Returns True on a real write.
        """
        actor_tag = f"actor={actor_id}" if actor_id else "actor=unknown"
        reason_full = f"{reason}; {actor_tag}"
        try:
            entry_id = await self._judge_store.upsert_self_eval_signal(
                automation_name=lookup.automation_name,
                task_id=lookup.task_id,
                source=feedback_source,
                quality=quality,
                confidence=confidence,
                reason=reason_full,
                skill_name=lookup.skill_name,
                source_workspace=lookup.source_workspace,
            )
        except Exception as exc:
            logger.warning("FeedbackCollector upsert_self_eval_signal failed: %s", exc)
            return False
        if entry_id is not None:
            logger.info(
                "FeedbackCollector merged self_eval automation=%s task=%s "
                "quality=%s source=%s entry=%s",
                lookup.automation_name,
                lookup.task_id,
                quality,
                feedback_source,
                entry_id,
            )
            return True
        return False


def _parse_ts(value: Any) -> datetime | None:
    """Best-effort ISO/SQL timestamp → datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class FeedbackScanWorker:
    """Daily timer that drives :meth:`FeedbackCollector.scan_no_reply_negative`.

    Runs as an asyncio.Task with a `stop_event` for clean shutdown. Default
    interval is 1 hour — the scan is cheap and missing a window once is
    fine (no_reply weak negatives are by definition not time-critical).
    """

    def __init__(
        self,
        collector: FeedbackCollector,
        *,
        interval_seconds: int = 3600,
    ) -> None:
        self._collector = collector
        self._interval = int(interval_seconds)
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def run(self) -> None:
        """Background loop. Exits when ``stop_event`` is set."""
        logger.info(
            "FeedbackScanWorker started (interval=%ds)",
            self._interval,
        )
        while not self._stop.is_set():
            try:
                await self._collector.scan_no_reply_negative()
            except Exception as exc:
                logger.warning("FeedbackScanWorker scan failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue
        logger.info("FeedbackScanWorker stopped")

    def start(self) -> asyncio.Task:
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run(), name="feedback-scan-worker")
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass

    def schedule_now(self) -> timedelta:
        """Hint to callers: time until next scan would fire. Useful for tests."""
        return timedelta(seconds=self._interval)
