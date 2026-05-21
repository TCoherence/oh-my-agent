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
from dataclasses import dataclass
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


@dataclass
class _AutomationPostLookup:
    """Resolved automation_posts row (subset we care about)."""

    task_id: str
    automation_name: str | None
    posted_at: datetime
    skill_name: str | None
    source_workspace: str | None


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
        self._task_locks: dict[str, asyncio.Lock] = {}
        self._task_locks_guard = asyncio.Lock()

    def _lock_for_task(self, task_id: str) -> asyncio.Lock:
        """Return an asyncio.Lock unique to this task_id (lazy init)."""
        # Single guard so two callers don't both create new locks.
        # Acquiring _task_locks_guard is fast (microseconds in asyncio),
        # safe to hold for the dict access.
        if task_id not in self._task_locks:
            self._task_locks[task_id] = asyncio.Lock()
        return self._task_locks[task_id]

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
        async with self._lock_for_task(lookup.task_id):
            return await self._write_self_eval(
                lookup=lookup,
                quality=quality,
                confidence=confidence,
                reason=reason_phrase,
                feedback_source="implicit",
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
            async with self._lock_for_task(lookup.task_id):
                if await self._has_existing_self_eval_for_task(lookup.task_id):
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

    async def _has_existing_self_eval_for_task(self, task_id: str) -> bool:
        """Check whether any active self_eval entry already carries this
        task_id in its evidence trail. Cheap dedupe so the scan worker
        doesn't pile on duplicates.
        """
        active = self._judge_store.get_active()
        for entry in active:
            if entry.category != "self_eval":
                continue
            for evidence in entry.evidence_log:
                if str(evidence.thread_id or "").endswith(task_id):
                    return True
            if entry.source_automation and task_id in (entry.summary or ""):
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
        """Apply a self_eval add action via the shared JudgeStore path.

        Returns True if the action was persisted (write succeeded), False
        on validation reject or any exception.
        """
        actor_tag = f"actor={actor_id}" if actor_id else "actor=unknown"
        summary = f"reason={reason}; task={lookup.task_id}; {actor_tag}"
        action = {
            "op": "add",
            "summary": summary[:280],
            "category": "self_eval",
            "scope": "automation",
            "source_automation": lookup.automation_name,
            "feedback_source": feedback_source,
            "quality": quality,
            "confidence": confidence,
            "evidence": reason,
        }
        # Pass a synthetic thread_id so the evidence_log lookup later can
        # dedupe by task. Mirrors RuntimeService._spawn_post_completion_judge
        # convention: `automation:<name>` for shared automation memory +
        # ``task:<id>`` suffix appended below for per-task dedupe.
        synthetic_thread = (
            f"automation:{lookup.automation_name}::task:{lookup.task_id}"
        )
        try:
            stats = await self._judge_store.apply_actions(
                [action],
                thread_id=synthetic_thread,
                skill_name=lookup.skill_name,
                source_workspace=lookup.source_workspace,
            )
        except Exception as exc:
            logger.warning("FeedbackCollector apply_actions failed: %s", exc)
            return False
        if stats.get("add", 0) > 0:
            logger.info(
                "FeedbackCollector wrote self_eval automation=%s task=%s "
                "quality=%s source=%s",
                lookup.automation_name,
                lookup.task_id,
                quality,
                feedback_source,
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
