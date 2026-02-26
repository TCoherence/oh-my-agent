from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oh_my_agent.agents.registry import AgentRegistry
    from oh_my_agent.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# Default prompt used to ask the agent to produce a summary.
_SUMMARY_PROMPT = (
    "Below is a conversation history. Produce a concise summary (max {max_chars} chars) "
    "that captures the key facts, decisions, and context needed to continue the "
    "conversation.  Respond ONLY with the summary text, no preamble.\n\n"
    "{conversation}"
)


class HistoryCompressor:
    """Compress old conversation turns into a summary.

    Strategy:
        1. When ``turn_count > max_turns``, select the oldest turns to summarise.
        2. Ask the first available agent (via *registry*) to produce a summary.
        3. Store the summary in the memory store and delete the raw turns.
        4. Fallback: if all agents fail, truncate (delete oldest turns silently).
    """

    def __init__(
        self,
        store: MemoryStore,
        max_turns: int = 20,
        summary_max_chars: int = 500,
    ) -> None:
        self._store = store
        self._max_turns = max_turns
        self._summary_max_chars = summary_max_chars

    async def maybe_compress(
        self,
        platform: str,
        channel_id: str,
        thread_id: str,
        registry: AgentRegistry,
    ) -> bool:
        """Compress if the thread exceeds *max_turns*.

        Returns ``True`` if compression was performed.
        """
        count = await self._store.count_turns(platform, channel_id, thread_id)
        if count <= self._max_turns:
            return False

        # How many turns to summarise (keep the last max_turns intact)
        n_to_compress = count - self._max_turns

        history = await self._store.load_history(
            platform, channel_id, thread_id,
        )

        # Separate raw turns (those with _id) from system/summary turns
        raw_turns = [t for t in history if "_id" in t]
        if len(raw_turns) <= self._max_turns:
            return False

        old_turns = raw_turns[:n_to_compress]
        first_id = old_turns[0]["_id"]
        last_id = old_turns[-1]["_id"]

        # Build conversation text for the summariser
        conv_lines = []
        for t in old_turns:
            label = t.get("author") or t.get("agent") or t["role"]
            conv_lines.append(f"[{label}] {t['content']}")
        conversation_text = "\n".join(conv_lines)

        prompt = _SUMMARY_PROMPT.format(
            max_chars=self._summary_max_chars,
            conversation=conversation_text,
        )

        # Try to get a summary from the agent
        summary_text: str | None = None
        try:
            agent_used, response = await registry.run(prompt)
            if not response.error and response.text.strip():
                summary_text = response.text.strip()[:self._summary_max_chars]
                logger.info(
                    "Generated summary for thread %s via %s (%d chars)",
                    thread_id,
                    agent_used.name,
                    len(summary_text),
                )
        except Exception as exc:
            logger.warning("Summary generation failed: %s", exc)

        if summary_text:
            await self._store.save_summary(
                platform, channel_id, thread_id,
                summary_text, first_id, last_id,
            )
        else:
            # Fallback: just delete old turns (pure truncation)
            logger.warning(
                "Falling back to truncation for thread %s (%d turns removed)",
                thread_id,
                n_to_compress,
            )
            await self._store.save_summary(
                platform, channel_id, thread_id,
                "(Earlier conversation history was truncated.)",
                first_id, last_id,
            )

        return True
