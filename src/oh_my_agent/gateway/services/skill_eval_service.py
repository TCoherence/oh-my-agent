"""Platform-agnostic skill evaluation (/skill_stats /skill_enable + reactions)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from oh_my_agent.gateway.services.types import (
    ServiceResult,
    SkillStatRow,
    SkillStatsResult,
    SkillToggleResult,
)

if TYPE_CHECKING:
    from oh_my_agent.memory.store import MemoryStore

logger = logging.getLogger(__name__)


class SkillEvalService:
    """Skill stats + auto-disable toggle + thumbs reaction handling.

    Platform adapters call these methods; they should not poke
    ``MemoryStore.get_skill_stats`` / ``upsert_skill_feedback`` directly.
    """

    def __init__(
        self,
        memory_store: MemoryStore | None,
        *,
        recent_days: int = 7,
        feedback_emojis: set[str] | None = None,
    ) -> None:
        self._store = memory_store
        self._recent_days = max(1, int(recent_days))
        self._feedback_emojis = feedback_emojis or {"👍", "👎"}

    @property
    def recent_days(self) -> int:
        return self._recent_days

    @property
    def feedback_emojis(self) -> set[str]:
        return set(self._feedback_emojis)

    def is_feedback_emoji(self, emoji: str) -> bool:
        return str(emoji) in self._feedback_emojis

    async def get_stats(self, skill: str | None = None) -> SkillStatsResult:
        if self._store is None or not hasattr(self._store, "get_skill_stats"):
            return SkillStatsResult(
                success=False, message="Skill evaluation store is not configured."
            )
        rows = await self._store.get_skill_stats(skill, recent_days=self._recent_days)
        if not rows:
            label = f" `{skill}`" if skill else ""
            return SkillStatsResult(
                success=True,
                message=f"No skill stats found for{label}.",
                stats=[],
                recent_days=self._recent_days,
                skill_filter=skill,
            )
        stat_rows: list[SkillStatRow] = []
        for row in rows:
            evals: list[dict] = []
            if skill and hasattr(self._store, "get_latest_skill_evaluations"):
                evals = await self._store.get_latest_skill_evaluations(row["skill_name"])
            stat_rows.append(_row_to_stat(row, latest_evaluations=evals))
        return SkillStatsResult(
            success=True,
            message=f"{len(stat_rows)} skill row(s).",
            stats=stat_rows,
            recent_days=self._recent_days,
            skill_filter=skill,
        )

    async def enable(self, skill: str) -> SkillToggleResult:
        if self._store is None or not hasattr(self._store, "get_skill_provenance"):
            return SkillToggleResult(
                success=False,
                message="Skill evaluation store is not configured.",
                skill_name=skill,
            )
        row = await self._store.get_skill_provenance(skill)
        if not row:
            return SkillToggleResult(
                success=False,
                message=f"Skill `{skill}` not found.",
                skill_name=skill,
            )
        if hasattr(self._store, "set_skill_auto_disabled"):
            await self._store.set_skill_auto_disabled(skill, disabled=False)
        return SkillToggleResult(
            success=True,
            message=f"Skill `{skill}` re-enabled for automatic routing.",
            skill_name=skill,
            now_enabled=True,
        )

    async def record_reaction(
        self,
        *,
        message_id: str,
        actor_id: str,
        platform: str,
        channel_id: str,
        thread_id: str,
        active_score: int | None,
    ) -> ServiceResult:
        """Upsert (score in {-1, +1}) or clear (score=None) a skill feedback row.

        Returns success=False with a non-error message when the message has no
        recorded skill invocation (the caller should silently ignore).
        """
        if self._store is None or not hasattr(self._store, "get_skill_invocation_by_message"):
            return ServiceResult(
                success=False, message="Skill evaluation store is not configured."
            )
        invocation = await self._store.get_skill_invocation_by_message(message_id)
        if not invocation:
            return ServiceResult(
                success=False, message="No skill invocation for this message."
            )
        if active_score is None:
            await self._store.delete_skill_feedback(
                invocation_id=int(invocation["id"]),
                actor_id=actor_id,
            )
            logger.info(
                "SKILL_FEEDBACK_CLEAR skill=%s invocation=%s actor=%s message=%s",
                invocation.get("skill_name"),
                invocation.get("id"),
                actor_id,
                message_id,
            )
            return ServiceResult(
                success=True,
                message=f"Cleared feedback for invocation `{invocation.get('id')}`.",
            )
        if active_score not in (-1, 1):
            return ServiceResult(
                success=False, message=f"Invalid score {active_score}; expected -1, 1, or None.",
            )
        await self._store.upsert_skill_feedback(
            invocation_id=int(invocation["id"]),
            actor_id=actor_id,
            platform=platform,
            channel_id=channel_id,
            thread_id=thread_id,
            score=active_score,
            source="reaction",
        )
        logger.info(
            "SKILL_FEEDBACK_RECORDED skill=%s invocation=%s actor=%s score=%+d message=%s",
            invocation.get("skill_name"),
            invocation.get("id"),
            actor_id,
            active_score,
            message_id,
        )
        return ServiceResult(
            success=True,
            message=f"Recorded feedback {active_score:+d} for invocation `{invocation.get('id')}`.",
        )


def _row_to_stat(row: dict[str, Any], *, latest_evaluations: list[dict]) -> SkillStatRow:
    return SkillStatRow(
        skill_name=str(row["skill_name"]),
        auto_disabled=bool(int(row.get("auto_disabled") or 0)),
        total_invocations=int(row.get("total_invocations") or 0),
        recent_invocations=int(row.get("recent_invocations") or 0),
        recent_successes=int(row.get("recent_successes") or 0),
        recent_errors=int(row.get("recent_errors") or 0),
        recent_timeouts=int(row.get("recent_timeouts") or 0),
        recent_cancelled=int(row.get("recent_cancelled") or 0),
        recent_avg_latency_ms=float(row.get("recent_avg_latency_ms") or 0.0),
        thumbs_up=int(row.get("thumbs_up") or 0),
        thumbs_down=int(row.get("thumbs_down") or 0),
        net_feedback=int(row.get("net_feedback") or 0),
        last_invoked_at=row.get("last_invoked_at"),
        merged_commit_hash=row.get("merged_commit_hash"),
        auto_disabled_reason=row.get("auto_disabled_reason"),
        latest_evaluations=list(latest_evaluations),
    )
