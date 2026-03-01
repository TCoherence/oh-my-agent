"""Extract user memories from conversation history using an agent."""

from __future__ import annotations

import json
import logging
import re

from oh_my_agent.memory.adaptive import AdaptiveMemoryStore, MemoryEntry

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
You are a memory extraction system. Analyze the conversation below and extract \
cross-session-worthy observations about the user: preferences, project knowledge, \
workflow patterns, and important facts.

Rules:
- Only extract things useful across future sessions (not task-specific details).
- Each memory should be a single, concise sentence.
- category must be one of: preference, project_knowledge, workflow, fact.
- confidence: 0.0-1.0 (how confident you are this is a stable, reusable observation).
- If the user explicitly states a preference, confidence should be 0.85+.
- If you infer something from context, confidence should be 0.5-0.7.
- Output ONLY a JSON array. No markdown, no preamble, no explanation.

{existing_context}

Conversation:
{conversation}

Output format (JSON array):
[{{"summary": "...", "category": "...", "confidence": 0.8}}]

If there is nothing worth extracting, output: []
"""


class MemoryExtractor:
    """Uses an agent to extract memories from conversation turns."""

    def __init__(self, store: AdaptiveMemoryStore) -> None:
        self._store = store

    async def extract(
        self,
        conversation_turns: list[dict],
        registry,
        thread_id: str | None = None,
    ) -> list[MemoryEntry]:
        """Extract memories from conversation, add to store, return new entries."""
        if not conversation_turns:
            return []

        # Build conversation text
        conv_lines = []
        for turn in conversation_turns:
            role = turn.get("role", "?")
            content = turn.get("content", "")
            conv_lines.append(f"{role}: {content}")
        conversation_text = "\n".join(conv_lines)

        # Include existing memories for dedup guidance
        existing = await self._store.list_all()
        if existing:
            existing_lines = [f"- {m.summary}" for m in existing[:20]]
            existing_context = (
                "Existing memories (match wording if updating the same observation):\n"
                + "\n".join(existing_lines)
            )
        else:
            existing_context = ""

        prompt = _EXTRACTION_PROMPT.format(
            conversation=conversation_text[:3000],
            existing_context=existing_context,
        )

        try:
            _agent, response = await registry.run(prompt)
        except Exception as exc:
            logger.warning("Memory extraction agent call failed: %s", exc)
            return []

        if response.error:
            logger.warning("Memory extraction agent returned error: %s", response.error)
            return []

        # Parse JSON from response
        entries = self._parse_response(response.text, thread_id)
        if not entries:
            return entries

        added = await self._store.add_memories(entries)
        logger.info("Memory extraction: parsed=%d, added=%d", len(entries), added)
        return entries

    @staticmethod
    def _parse_response(text: str, thread_id: str | None = None) -> list[MemoryEntry]:
        """Parse agent response into MemoryEntry list. Handles markdown fences."""
        # Strip markdown code fences
        cleaned = re.sub(r"```(?:json)?\s*", "", text).strip()
        cleaned = cleaned.rstrip("`").strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to find JSON array in the text
            match = re.search(r"\[.*\]", cleaned, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    logger.warning("Failed to parse memory extraction response")
                    return []
            else:
                logger.warning("No JSON array found in memory extraction response")
                return []

        if not isinstance(data, list):
            return []

        entries = []
        for item in data:
            if not isinstance(item, dict) or not item.get("summary"):
                continue
            entry = MemoryEntry(
                summary=str(item["summary"]),
                category=str(item.get("category", "fact")),
                confidence=float(item.get("confidence", 0.6)),
                source_threads=[thread_id] if thread_id else [],
            )
            entries.append(entry)

        return entries
