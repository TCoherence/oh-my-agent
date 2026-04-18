"""Platform-agnostic memory (/memories /forget /memorize) business logic."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from oh_my_agent.gateway.services.types import (
    MemoryActionResult,
    MemoryEntrySummary,
    MemoryListResult,
)

if TYPE_CHECKING:
    from oh_my_agent.agents.registry import AgentRegistry
    from oh_my_agent.gateway.manager import GatewayManager
    from oh_my_agent.memory.judge_store import JudgeStore

logger = logging.getLogger(__name__)


class MemoryService:
    """List/forget/memorize operations for the judge memory subsystem.

    Platform adapters call these methods and render the returned results;
    they should not touch ``JudgeStore`` or ``GatewayManager`` directly.

    The optional ``registry`` is used only to trigger a best-effort
    ``MEMORY.md`` resynthesis after a write. Synthesis failures are logged
    and never raised.
    """

    def __init__(
        self,
        judge_store: JudgeStore | None,
        gateway_manager: GatewayManager | None = None,
        registry: AgentRegistry | None = None,
    ) -> None:
        self._store = judge_store
        self._gateway = gateway_manager
        self._registry = registry

    def list_entries(self, *, category: str | None = None) -> MemoryListResult:
        if self._store is None:
            return MemoryListResult(
                success=False, message="Memory subsystem is not enabled."
            )
        entries = self._store.get_active()
        total_active = len(entries)
        if category:
            entries = [m for m in entries if m.category == category]
        entries.sort(key=lambda m: (m.scope, -m.confidence, -m.observation_count))
        summaries = [
            MemoryEntrySummary(
                memory_id=m.id,
                summary=m.summary,
                category=m.category,
                scope=m.scope,
                confidence=m.confidence,
                observation_count=m.observation_count,
                last_observed_at=str(m.last_observed_at),
            )
            for m in entries
        ]
        if not summaries:
            msg = (
                f"No memories in category **{category}**."
                if category
                else "No memories stored."
            )
            return MemoryListResult(
                success=True,
                message=msg,
                entries=[],
                total_active=total_active,
                category_filter=category,
            )
        return MemoryListResult(
            success=True,
            message=f"{len(summaries)} memory entries.",
            entries=summaries,
            total_active=total_active,
            category_filter=category,
        )

    async def forget(self, memory_id: str) -> MemoryActionResult:
        if self._store is None:
            return MemoryActionResult(
                success=False, message="Memory subsystem is not enabled."
            )
        deleted = await self._store.manual_supersede(memory_id)
        if not deleted:
            return MemoryActionResult(
                success=False,
                message=f"Memory `{memory_id}` not found or already inactive.",
                memory_id=memory_id,
            )
        await self._try_synth()
        return MemoryActionResult(
            success=True,
            message=f"Memory `{memory_id}` forgotten.",
            memory_id=memory_id,
        )

    async def memorize(
        self,
        *,
        platform: str,
        channel_id: str,
        thread_id: str,
        explicit_summary: str | None = None,
        explicit_scope: str | None = None,
    ) -> MemoryActionResult:
        if self._gateway is None or self._store is None:
            return MemoryActionResult(
                success=False, message="Memory subsystem is not enabled."
            )
        try:
            result = await self._gateway.request_memorize(
                platform=platform,
                channel_id=channel_id,
                thread_id=thread_id,
                explicit_summary=explicit_summary,
                explicit_scope=explicit_scope,
            )
        except Exception as exc:
            logger.warning("memorize failed: %s", exc)
            return MemoryActionResult(
                success=False, message=f"Memorize failed: {exc}"
            )
        if result is None:
            return MemoryActionResult(
                success=False, message="Judge not available."
            )
        if result.get("error"):
            return MemoryActionResult(
                success=False, message=str(result["error"])
            )
        stats = result.get("stats") or {}
        actions = result.get("actions") or []
        return MemoryActionResult(
            success=True,
            message=(
                f"Judge ran — actions={len(actions)} "
                f"add={stats.get('add', 0)} strengthen={stats.get('strengthen', 0)} "
                f"supersede={stats.get('supersede', 0)} no_op={stats.get('no_op', 0)}"
            ),
            judge_stats=dict(stats),
            judge_action_count=len(actions),
        )

    async def _try_synth(self) -> None:
        """Best-effort MEMORY.md resynthesis; failures are logged, not raised."""
        if self._gateway is None or self._registry is None:
            return
        try:
            await self._gateway._try_memory_md_synth(self._registry)
        except Exception:
            logger.debug("MEMORY.md synth after memory action failed", exc_info=True)
