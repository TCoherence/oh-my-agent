"""Extract user memories from conversation history using an agent."""

from __future__ import annotations

import json
import logging
import re

from oh_my_agent.memory.adaptive import (
    MemoryEntry,
    VALID_DURABILITY,
    VALID_EXPLICITNESS,
    VALID_SCOPES,
)

logger = logging.getLogger(__name__)

_MAX_TURNS = 6
_MAX_ASSISTANT_TURN_CHARS = 800
_MAX_WINDOW_CHARS = 3600
_SIMPLIFIED_SCHEMA_KEYS = ("summary", "category", "confidence", "explicitness", "evidence")

_EXTRACTION_PROMPT = """\
You are a memory extraction system. Analyze the conversation below and extract only \
cross-session-worthy observations about the user: preferences, project knowledge, \
workflow patterns, and important facts.

Rules:
- Extract only things that are likely useful in future sessions.
- Only use content marked as [user] as evidence for memory extraction.
- Content marked as [assistant] is context only and must not be treated as evidence by itself.
- Each memory must be a single concise sentence.
- category must be one of: preference, project_knowledge, workflow, fact.
- explicitness must be one of: explicit, inferred.
- scope must be one of: global_user, workspace, skill, thread.
- durability must be one of: ephemeral, medium, long.
- evidence must be a short user-side evidence snippet (max 140 chars).
- confidence: 0.0-1.0.
- If the user explicitly states a stable preference or rule, confidence should usually be 0.85+.
- If something is only inferred from context, confidence should usually be 0.5-0.7.

Do NOT extract:
- one-off task details
- temporary plans in the current thread
- slash command or skill invocation habits
- file paths, commands, or implementation steps
- speculative future intent like "the user may request..."
- short-lived runtime facts about the current skill execution

If there is nothing worth extracting, output [].
Output ONLY a JSON array. No markdown, no explanation.

Execution context:
{execution_context}

{existing_context}

Conversation:
{conversation}

Output format:
[{{"summary":"...","category":"preference","confidence":0.9,"explicitness":"explicit","scope":"global_user","durability":"long","evidence":"..."}}]
"""

_SIMPLIFIED_EXTRACTION_PROMPT = """\
You are a memory extraction system. Extract only stable, cross-session user memories.

Rules:
- Use only [user] lines as evidence.
- Ignore one-off task details, temporary plans, command usage, and future speculation.
- Return ONLY JSON.
- If nothing is worth extracting, return [].

Conversation:
{conversation}

Output format:
[{{"summary":"...","category":"preference","confidence":0.9,"explicitness":"explicit","evidence":"..."}}]
"""


class MemoryExtractor:
    """Uses an agent to extract memories from conversation turns."""

    def __init__(self, store) -> None:  # accepts any store with add_memories/list_all
        self._store = store

    async def extract(
        self,
        conversation_turns: list[dict],
        registry,
        thread_id: str | None = None,
        req_id: str | None = None,
        *,
        skill_name: str | None = None,
        source_workspace: str | None = None,
        thread_topic: str | None = None,
    ) -> list[MemoryEntry]:
        """Extract memories from conversation, add to store, return new entries."""
        if not conversation_turns:
            return []

        conversation_text = self._build_recent_window(conversation_turns)
        if not conversation_text.strip():
            logger.info(
                "%smemory_extract thread_id=%s turn_count=%d extracted_count=0 rejected_count=0 retry_used=false skip_reason=no_recent_window parse_failure=false",
                f"[{req_id}] " if req_id else "",
                thread_id or "-",
                len(conversation_turns),
            )
            return []

        # Include existing memories for dedup guidance
        existing = [
            m for m in await self._store.list_all()
            if getattr(m, "status", "active") == "active"
        ]
        if existing:
            existing_lines = [f"- {m.summary}" for m in existing[:20]]
            existing_context = (
                "Existing memories (match wording if updating the same observation):\n"
                + "\n".join(existing_lines)
            )
        else:
            existing_context = ""

        prompt = _EXTRACTION_PROMPT.format(
            conversation=conversation_text,
            existing_context=existing_context,
            execution_context=self._format_execution_context(
                skill_name=skill_name,
                source_workspace=source_workspace,
                thread_topic=thread_topic,
            ),
        )
        req_prefix = f"[{req_id}] " if req_id else ""
        run_label = f"memory_extract req={req_id or '-'} thread={thread_id or '-'}"

        try:
            logger.info(
                "%sMemory extraction started thread=%s turns=%d",
                req_prefix,
                thread_id or "-",
                len(conversation_turns),
            )
            _agent, response = await registry.run(prompt, run_label=run_label)
        except Exception as exc:
            logger.warning("%sMemory extraction agent call failed: %s", req_prefix, exc)
            return []

        if response.error:
            logger.warning("%sMemory extraction agent returned error: %s", req_prefix, response.error)
            return []

        # Parse JSON from response
        entries, rejected_count, parse_failure = self._parse_response(response.text, thread_id)
        self._apply_context_defaults(
            entries,
            skill_name=skill_name,
            source_workspace=source_workspace,
        )
        retry_used = False
        if parse_failure:
            retry_used = True
            try:
                _agent, retry_response = await registry.run(
                    _SIMPLIFIED_EXTRACTION_PROMPT.format(conversation=conversation_text),
                    run_label=f"{run_label} retry=1",
                )
            except Exception as exc:
                logger.warning("%sMemory extraction retry failed: %s", req_prefix, exc)
                logger.info(
                    "%smemory_extract thread_id=%s turn_count=%d extracted_count=0 rejected_count=%d retry_used=true skip_reason=parse_failure parse_failure=true",
                    req_prefix,
                    thread_id or "-",
                    len(conversation_turns),
                    rejected_count,
                )
                return []
            if retry_response.error:
                logger.warning("%sMemory extraction retry returned error: %s", req_prefix, retry_response.error)
                logger.info(
                    "%smemory_extract thread_id=%s turn_count=%d extracted_count=0 rejected_count=%d retry_used=true skip_reason=parse_failure parse_failure=true",
                    req_prefix,
                    thread_id or "-",
                    len(conversation_turns),
                    rejected_count,
                )
                return []
            entries, rejected_count, parse_failure = self._parse_response(
                retry_response.text,
                thread_id,
                simplified=True,
            )
            self._apply_context_defaults(
                entries,
                skill_name=skill_name,
                source_workspace=source_workspace,
            )
            if parse_failure:
                logger.warning("%sMemory extraction retry parse failed", req_prefix)
                logger.info(
                    "%smemory_extract thread_id=%s turn_count=%d extracted_count=0 rejected_count=%d retry_used=true skip_reason=parse_failure parse_failure=true",
                    req_prefix,
                    thread_id or "-",
                    len(conversation_turns),
                    rejected_count,
                )
                return []

        if not entries:
            logger.info(
                "%smemory_extract thread_id=%s turn_count=%d extracted_count=0 rejected_count=%d retry_used=%s skip_reason=empty_result parse_failure=false",
                req_prefix,
                thread_id or "-",
                len(conversation_turns),
                rejected_count,
                str(retry_used).lower(),
            )
            return entries

        added = await self._store.add_memories(entries, registry=registry, req_id=req_id)
        if getattr(self._store, "needs_synthesis", False) and hasattr(self._store, "synthesize_memory_md"):
            try:
                await self._store.synthesize_memory_md(registry)
                if hasattr(self._store, "clear_synthesis_flag"):
                    self._store.clear_synthesis_flag()
            except Exception as exc:
                logger.warning("%sMemory synthesis failed: %s", req_prefix, exc)
        logger.info(
            "%smemory_extract thread_id=%s turn_count=%d extracted_count=%d rejected_count=%d retry_used=%s skip_reason=- parse_failure=false added=%d",
            req_prefix,
            thread_id or "-",
            len(conversation_turns),
            len(entries),
            rejected_count,
            str(retry_used).lower(),
            added,
        )
        return entries

    @staticmethod
    def _parse_response(
        text: str,
        thread_id: str | None = None,
        *,
        simplified: bool = False,
    ) -> tuple[list[MemoryEntry], int, bool]:
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
                    return [], 0, True
            else:
                logger.warning("No JSON array found in memory extraction response")
                return [], 0, True

        if not isinstance(data, list):
            return [], 0, True

        entries = []
        rejected = 0
        for item in data:
            if not isinstance(item, dict) or not item.get("summary"):
                rejected += 1
                continue
            category = str(item.get("category", "fact"))
            explicitness = str(item.get("explicitness", "inferred"))
            scope = str(item.get("scope", ""))
            durability = str(item.get("durability", ""))
            evidence = str(item.get("evidence", ""))[:140]
            if simplified:
                item = {k: item.get(k) for k in _SIMPLIFIED_SCHEMA_KEYS}
            entry = MemoryEntry(
                summary=str(item["summary"]),
                category=category,
                confidence=float(item.get("confidence", 0.6)),
                source_threads=[thread_id] if thread_id else [],
                explicitness=explicitness,
                evidence=evidence,
                scope=scope if scope in VALID_SCOPES else "",
                durability=durability if durability in VALID_DURABILITY else "",
            )
            entries.append(entry)

        return entries, rejected, False

    @staticmethod
    def _format_execution_context(
        *,
        skill_name: str | None = None,
        source_workspace: str | None = None,
        thread_topic: str | None = None,
    ) -> str:
        lines = [
            f"- current_skill: {skill_name or 'none'}",
            f"- source_workspace: {source_workspace or 'none'}",
            f"- thread_topic_hint: {(thread_topic or 'none')[:240]}",
            "- scope guidance: use global_user for broad preferences/interests, workspace for repo/project constraints, skill for rules tied to the current skill, thread only for thread-local memory.",
            "- durability guidance: use long for stable durable preferences/constraints, medium for reusable but revisable patterns, ephemeral for near-term thread context.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _apply_context_defaults(
        entries: list[MemoryEntry],
        *,
        skill_name: str | None = None,
        source_workspace: str | None = None,
    ) -> None:
        for entry in entries:
            if entry.explicitness not in VALID_EXPLICITNESS:
                entry.explicitness = "inferred"
            if entry.scope not in VALID_SCOPES:
                if entry.category == "project_knowledge" and source_workspace:
                    entry.scope = "workspace"
                elif entry.category == "workflow" and skill_name:
                    entry.scope = "skill"
                else:
                    entry.scope = "global_user"
            if entry.durability not in VALID_DURABILITY:
                if entry.scope == "thread":
                    entry.durability = "ephemeral"
                elif entry.category in {"preference", "project_knowledge"}:
                    entry.durability = "long"
                else:
                    entry.durability = "medium"
            if skill_name:
                entry.source_skills = [skill_name]
            if source_workspace:
                entry.source_workspace = source_workspace

    @staticmethod
    def _build_recent_window(conversation_turns: list[dict]) -> str:
        raw_turns = [
            {"role": str(turn.get("role", "?")), "content": str(turn.get("content", "") or "")}
            for turn in conversation_turns
            if str(turn.get("content", "") or "").strip()
        ]
        if not raw_turns:
            return ""

        selected = raw_turns[-_MAX_TURNS:]
        selected_user_count = sum(1 for turn in selected if turn["role"] == "user")
        if selected_user_count < 2:
            older_user_turns = [
                turn for turn in raw_turns[:-len(selected)] if turn["role"] == "user"
            ]
            while selected_user_count < 2 and older_user_turns:
                user_turn = older_user_turns.pop()
                replaced = False
                for idx, turn in enumerate(selected):
                    if turn["role"] != "user":
                        selected[idx] = user_turn
                        replaced = True
                        selected_user_count += 1
                        break
                if not replaced:
                    break

        normalized = []
        for turn in selected:
            content = turn["content"]
            if turn["role"] == "assistant" and len(content) > _MAX_ASSISTANT_TURN_CHARS:
                content = content[:_MAX_ASSISTANT_TURN_CHARS].rstrip() + "..."
            normalized.append({"role": turn["role"], "content": content})

        def _render(turns: list[dict]) -> str:
            return "\n".join(f"[{turn['role']}] {turn['content']}" for turn in turns if turn["content"].strip())

        rendered = _render(normalized)
        if len(rendered) <= _MAX_WINDOW_CHARS:
            return rendered

        assistant_indexes = [idx for idx, turn in enumerate(normalized) if turn["role"] == "assistant"]
        for idx in assistant_indexes:
            if len(_render(normalized)) <= _MAX_WINDOW_CHARS:
                break
            content = normalized[idx]["content"]
            if len(content) > 160:
                normalized[idx]["content"] = content[:160].rstrip() + "..."

        rendered = _render(normalized)
        if len(rendered) <= _MAX_WINDOW_CHARS:
            return rendered

        user_indexes = [idx for idx, turn in enumerate(normalized) if turn["role"] == "user"]
        protected_user_indexes = set(user_indexes[-2:])
        for idx in user_indexes:
            if len(_render(normalized)) <= _MAX_WINDOW_CHARS:
                break
            if idx in protected_user_indexes:
                continue
            content = normalized[idx]["content"]
            if len(content) > 200:
                normalized[idx]["content"] = content[:200].rstrip() + "..."

        return _render(normalized)
