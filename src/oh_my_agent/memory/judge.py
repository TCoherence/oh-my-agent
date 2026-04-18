"""Judge — single-stage memory decision agent.

Replaces the multi-stage extractor + dedup + promoter pipeline. Receives the
conversation, current active memories, and execution context; emits a list of
explicit actions (``add`` / ``strengthen`` / ``supersede`` / ``no_op``) that the
:class:`JudgeStore` applies.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from oh_my_agent.memory.judge_store import JudgeStore, parse_judge_actions

logger = logging.getLogger(__name__)

_MAX_TURNS = 20
_MAX_USER_TURN_CHARS = 1200
_MAX_ASSISTANT_TURN_CHARS = 600
_MAX_WINDOW_CHARS = 8000

_JUDGE_PROMPT = """\
You are a long-term memory judge. Decide what (if anything) about the USER should be \
remembered for future sessions.

You will be given:
1. The full conversation between the user and the assistant in this thread.
2. The current list of active memories about this user (id + summary + category + scope).
3. The execution context (current skill / workspace / thread).

Emit a list of ACTIONS describing how the memory store should change. Allowed ops:

- "add": brand new memory not yet in the store.
  Required: summary, category, scope, confidence, evidence (a short user-side snippet).
- "strengthen": an existing memory was reinforced by new user evidence.
  Required: id, evidence. Optional: confidence_bump (0.0-0.20).
- "supersede": an existing memory has been replaced by a new, contradictory user statement.
  Required: old_id, new_summary, category, scope, confidence, evidence.
- "no_op": nothing in this conversation deserves long-term memory.
  Required: reason.

You MUST always output something — at minimum a single no_op action.

Categories: preference | workflow | project_knowledge | fact
Scopes:     global_user | workspace | skill | thread

Strict rules:
- Use ONLY [user] turns as evidence. The assistant's text is context, not signal.
- Each memory must be ONE concise sentence about the user.
- Do NOT memorize: one-off task details, temporary plans, slash command usage, file paths, \
implementation choices, debugging steps, or speculation.
- Confidence: 0.85+ for explicit stable preferences ("我喜欢…", "I always…"). 0.5-0.7 for inferred patterns.
- If a new observation paraphrases an existing memory → emit "strengthen", do NOT "add" a duplicate.
- If a new observation contradicts an existing memory → emit "supersede".
- If the conversation contains no real signal → emit a single no_op.

Execution context:
{execution_context}

Current active memories ({active_count} entries):
{active_memories}

Conversation:
{conversation}

Output ONLY a JSON object with this exact shape (no markdown, no preamble):
{{"actions": [
  {{"op": "add", "summary": "...", "category": "preference", "scope": "global_user", "confidence": 0.9, "evidence": "用户原话片段"}},
  {{"op": "strengthen", "id": "abc123", "evidence": "..."}},
  {{"op": "supersede", "old_id": "def456", "new_summary": "...", "category": "...", "scope": "...", "confidence": 0.9, "evidence": "..."}},
  {{"op": "no_op", "reason": "..."}}
]}}
"""

_SIMPLIFIED_JUDGE_PROMPT = """\
You are a memory judge. Output a JSON list of actions describing memory changes.

Allowed ops: add | strengthen | supersede | no_op.
Use only user evidence. One sentence per memory. If nothing worth saving, output a single no_op.

Current active memories:
{active_memories}

Conversation:
{conversation}

Output ONLY JSON like: {{"actions": [{{"op":"no_op","reason":"..."}}]}}
"""


@dataclass
class JudgeResult:
    actions: list[dict[str, Any]]
    stats: dict[str, int]
    raw_response: str = ""
    error: str | None = None


class Judge:
    """Event-driven memory judge.

    Unlike the legacy extractor (which ran every N turns from a fixed window), the
    Judge is invoked at explicit trigger points: thread idle, ``/memorize`` slash,
    or natural-language keyword. Each invocation considers the entire conversation
    plus the current memory state.
    """

    def __init__(self, store: JudgeStore) -> None:
        self._store = store

    async def run(
        self,
        *,
        conversation: list[dict],
        registry,
        thread_id: str | None = None,
        skill_name: str | None = None,
        source_workspace: str | None = None,
        thread_topic: str | None = None,
        explicit_summary: str | None = None,
        explicit_scope: str | None = None,
        req_id: str | None = None,
    ) -> JudgeResult:
        """Run the judge against the given conversation.

        ``explicit_summary`` short-circuits LLM judgment when the user invoked
        ``/memorize`` with a literal sentence — we add that directly as a
        high-confidence ``add`` action.
        """
        if explicit_summary:
            scope = explicit_scope if explicit_scope in {"global_user", "workspace", "skill", "thread"} else "global_user"
            evidence = ""
            for turn in reversed(conversation or []):
                if turn.get("role") == "user":
                    evidence = str(turn.get("content", ""))[:280]
                    break
            actions: list[dict[str, Any]] = [
                {
                    "op": "add",
                    "summary": explicit_summary.strip(),
                    "category": "preference",
                    "scope": scope,
                    "confidence": 0.95,
                    "evidence": evidence,
                }
            ]
            stats = await self._store.apply_actions(
                actions,
                thread_id=thread_id,
                skill_name=skill_name,
                source_workspace=source_workspace,
            )
            logger.info(
                "memory_judge_explicit thread_id=%s actions=%s stats=%s",
                thread_id,
                actions,
                stats,
            )
            return JudgeResult(actions=actions, stats=stats, raw_response="", error=None)

        rendered_convo = self._render_conversation(conversation or [])
        if not rendered_convo:
            stats = {"add": 0, "strengthen": 0, "supersede": 0, "no_op": 1, "rejected": 0}
            return JudgeResult(actions=[{"op": "no_op", "reason": "empty conversation"}], stats=stats)

        active_context = self._store.to_judge_context()
        active_text = json.dumps(active_context, ensure_ascii=False, indent=2) if active_context else "[]"
        execution_context = self._format_execution_context(
            skill_name=skill_name,
            source_workspace=source_workspace,
            thread_topic=thread_topic,
        )

        prompt = _JUDGE_PROMPT.format(
            execution_context=execution_context,
            active_count=len(active_context),
            active_memories=active_text,
            conversation=rendered_convo,
        )

        actions, raw_text, err = await self._invoke(prompt, registry, req_id=req_id, label="memory_judge")
        if err is None and not actions:
            simplified = _SIMPLIFIED_JUDGE_PROMPT.format(
                active_memories=active_text,
                conversation=rendered_convo,
            )
            actions, raw_text2, err2 = await self._invoke(
                simplified, registry, req_id=req_id, label="memory_judge_simplified"
            )
            raw_text = raw_text2 or raw_text
            err = err2 or err

        if err:
            stats = {"add": 0, "strengthen": 0, "supersede": 0, "no_op": 0, "rejected": 0}
            logger.warning("memory_judge error thread_id=%s err=%s", thread_id, err)
            return JudgeResult(actions=[], stats=stats, raw_response=raw_text, error=err)

        stats = await self._store.apply_actions(
            actions,
            thread_id=thread_id,
            skill_name=skill_name,
            source_workspace=source_workspace,
        )
        logger.info(
            "memory_judge_run thread_id=%s actions=%d stats=%s",
            thread_id,
            len(actions),
            stats,
        )
        return JudgeResult(actions=actions, stats=stats, raw_response=raw_text, error=None)

    async def _invoke(
        self,
        prompt: str,
        registry,
        *,
        req_id: str | None,
        label: str,
    ) -> tuple[list[dict[str, Any]], str, str | None]:
        try:
            _agent, response = await registry.run(prompt, run_label=label)
        except Exception as exc:
            return [], "", f"agent_exception: {exc}"
        if response.error:
            return [], response.text or "", response.error
        actions = parse_judge_actions(response.text or "")
        return actions, response.text or "", None

    @staticmethod
    def _format_execution_context(
        *,
        skill_name: str | None,
        source_workspace: str | None,
        thread_topic: str | None,
    ) -> str:
        return "\n".join(
            [
                f"- current_skill: {skill_name or 'none'}",
                f"- source_workspace: {source_workspace or 'none'}",
                f"- thread_topic_hint: {(thread_topic or 'none')[:240]}",
                "- scope guidance: global_user for broad preferences, workspace for repo-bound knowledge, skill for skill-bound rules, thread only for thread-local detail.",
            ]
        )

    @staticmethod
    def _render_conversation(turns: list[dict]) -> str:
        cleaned: list[dict] = []
        for turn in turns:
            role = str(turn.get("role", "")).strip()
            content = str(turn.get("content", "") or "").strip()
            if not role or not content:
                continue
            cleaned.append({"role": role, "content": content})
        if not cleaned:
            return ""
        selected = cleaned[-_MAX_TURNS:]
        normalized: list[str] = []
        for turn in selected:
            content = turn["content"]
            if turn["role"] == "user" and len(content) > _MAX_USER_TURN_CHARS:
                content = content[:_MAX_USER_TURN_CHARS].rstrip() + "..."
            elif turn["role"] == "assistant" and len(content) > _MAX_ASSISTANT_TURN_CHARS:
                content = content[:_MAX_ASSISTANT_TURN_CHARS].rstrip() + "..."
            normalized.append(f"[{turn['role']}] {content}")
        rendered = "\n".join(normalized)
        if len(rendered) <= _MAX_WINDOW_CHARS:
            return rendered
        # Drop oldest turns until under budget; always keep last user turn.
        while len(normalized) > 2 and len("\n".join(normalized)) > _MAX_WINDOW_CHARS:
            normalized.pop(0)
        return "\n".join(normalized)
