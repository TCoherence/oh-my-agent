"""Judge — single-stage memory decision agent.

Replaces the multi-stage extractor + dedup + promoter pipeline. Receives the
conversation, current active memories, and execution context; emits a list of
explicit actions (``add`` / ``strengthen`` / ``supersede`` / ``no_op``) that the
:class:`JudgeStore` applies.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from oh_my_agent.memory.judge_store import JudgeStore, parse_judge_actions

logger = logging.getLogger(__name__)

_MAX_TURNS = 20
_MAX_USER_TURN_CHARS = 1200
_MAX_ASSISTANT_TURN_CHARS = 600
_MAX_WINDOW_CHARS = 8000
# M0 PR3 — task-completion judge truncation budgets (synthetic input, not chat)
_TASK_PROMPT_MAX_CHARS = 6000
_TASK_OUTPUT_MAX_CHARS = 10000

# M1 PR1 — model pricing for budget gating (USD per million tokens).
# Updated when Anthropic changes price lists; defaults pinned to plan's
# 2026 sonnet-4-6 quote. Unknown models fall back to a conservative
# "treat as sonnet-4-6" so budget tracking errs on the side of caution.
_MODEL_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    # (input_price, output_price) per million tokens
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-4-7": (15.0, 75.0),
}
_DEFAULT_SELF_EVAL_MODEL = "claude-sonnet-4-6"
# The ``self_eval_model`` / per-skill ``model`` config knobs drive BOTH cost
# calculation AND actual model routing: the self_eval LLM call passes
# ``model_override`` to ``AgentRegistry.run``, which forwards it (by run()
# signature) to agents that accept the kwarg. ClaudeAgent honors it by
# computing a local effective_model — self._model is never mutated, so
# concurrent self_eval calls stay isolated. Agents whose run() lacks the
# param (gemini/codex) are silently skipped and use their configured model.
# Hard upper bound used by reserve step (worst-case 15k input + 800 output)
_SELF_EVAL_MAX_INPUT_TOKENS = 15000
_SELF_EVAL_MAX_OUTPUT_TOKENS = 800

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

# M0 PR3 — task-completion judge prompts.
# These are NOT the chat judge prompt (which talks about users). They feed
# Judge.run_for_task() with headless automation / runtime context where the
# "conversation" is synthetic (prompt + agent output, no human turns).

_PROMPT_TASK_COMPLETION = """\
You are a memory judge for an automated task that just finished. Decide what \
DURABLE KNOWLEDGE about the task domain (NOT about a user) should be \
remembered so the next run of the same automation / skill benefits.

Input:
1. The task prompt the agent received.
2. The agent's final output.
3. The current active memories already known for this automation / skill.

Emit a JSON list of ACTIONS. Allowed ops:
- "add": brand new memory derived from this run's output.
  Required: summary, category, scope, confidence, evidence.
- "strengthen": existing memory was reinforced by this run.
  Required: id, evidence. Optional: confidence_bump (0.0-0.20).
- "supersede": existing memory was contradicted by this run.
  Required: old_id, new_summary, category, scope, confidence, evidence.
- "no_op": nothing in this run deserves long-term memory.
  Required: reason.

Categories: preference | workflow | project_knowledge | fact
Scopes:     global_user | workspace | skill | thread

Strict rules for headless task memory:
- DO memorize: domain facts the output discovered, recurring patterns, gotchas,
  data shapes, repeatable preferences inferred from the run.
- DO NOT memorize: this specific run's transient output (article text, summary
  body, etc.); use the published artifact for that. The memory store is for
  cross-run signal, not the artifact itself.
- Confidence 0.85+ for clearly stated facts ("the API returns paginated…").
  0.5-0.7 for inferred patterns. Lower than 0.5 → emit no_op.

Execution context:
{execution_context}

Current active memories ({active_count} entries):
{active_memories}

Task prompt:
{task_prompt}

Agent output:
{task_output}

Output ONLY a JSON object: {{"actions": [...]}}
"""

_PROMPT_SELF_EVAL = """\
You are evaluating how well an automated task ran. Output a single JSON object \
with three fields: quality, reason, suggested_improvement.

- quality: one of "pass" | "borderline" | "fail".
- reason: 1-2 sentences explaining the verdict.
- suggested_improvement: 1 sentence, may be empty for "pass".

Judge the agent OUTPUT against the task PROMPT. Things that count against \
quality: unhelpful or off-topic output, partial completion, ignoring \
instructions, runtime errors visible in output, hallucinated facts.

Task prompt:
{task_prompt}

Agent output:
{task_output}

Output ONLY a JSON object like:
{{"quality": "pass", "reason": "...", "suggested_improvement": ""}}
"""


def _current_year_month() -> str:
    """``YYYY-MM`` bucket for monthly budget reset (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _model_price(model: str) -> tuple[float, float]:
    """Return (input_price, output_price) USD/MTok for a given model.

    Unknown models fall back to the default self-eval model price so the
    budget always tracks something rather than silently free-running.
    """
    if model in _MODEL_PRICING_USD_PER_MTOK:
        return _MODEL_PRICING_USD_PER_MTOK[model]
    logger.warning(
        "judge: unknown model %r for cost calc — falling back to %s pricing",
        model,
        _DEFAULT_SELF_EVAL_MODEL,
    )
    return _MODEL_PRICING_USD_PER_MTOK[_DEFAULT_SELF_EVAL_MODEL]


def _calculate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Convert token counts → USD using the model's price list."""
    in_price, out_price = _model_price(model)
    return (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price


class SelfEvalBudget:
    """M1 PR1 — opt-in budget gate for LLM self-eval calls.

    Reads/writes the ``self_eval_budget`` SQLite table (year_month PK) so
    budgets survive bot restarts. Uses an in-process ``asyncio.Lock`` to
    serialize ``read → reserve → call → reconcile`` across concurrent
    task completions; reservations cap the optimistic-spend window so a
    burst of concurrent completions can't all sneak past the gate.

    Disable by passing ``monthly_budget_usd <= 0`` — the tracker then
    short-circuits ``check_and_reserve`` to always allow.
    """

    def __init__(
        self,
        store,
        *,
        monthly_budget_usd: float,
        default_model: str = _DEFAULT_SELF_EVAL_MODEL,
    ) -> None:
        self._store = store
        self._budget = float(monthly_budget_usd)
        self._default_model = default_model
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._budget > 0

    async def check_and_reserve(self, model: str | None = None) -> tuple[bool, float]:
        """Atomically check budget + reserve worst-case spend for one call.

        Returns ``(allowed, reserved_amount)``. On allow, the caller must
        invoke :meth:`reconcile` with the actual spend after the LLM call
        returns. Reserved amount is computed from the model's price + worst-
        case 15k+800 tokens. If reserved + current spend exceeds budget,
        returns ``(False, 0.0)`` without touching the ledger.
        """
        if not self.enabled:
            return True, 0.0
        async with self._lock:
            current = await self._store.get_self_eval_budget(_current_year_month())
            reserve = _calculate_cost_usd(
                model or self._default_model,
                _SELF_EVAL_MAX_INPUT_TOKENS,
                _SELF_EVAL_MAX_OUTPUT_TOKENS,
            )
            if current + reserve > self._budget:
                logger.warning(
                    "self_eval_budget gate REJECT: spent=%.4f reserve=%.4f budget=%.4f month=%s",
                    current,
                    reserve,
                    self._budget,
                    _current_year_month(),
                )
                return False, 0.0
            new_total = await self._store.add_self_eval_spend(
                _current_year_month(), reserve
            )
            logger.debug(
                "self_eval_budget gate ALLOW: reserved=%.4f new_total=%.4f budget=%.4f",
                reserve,
                new_total,
                self._budget,
            )
            return True, reserve

    async def reconcile(self, reserved: float, actual: float) -> None:
        """Refund the over-reserved difference, or charge any shortfall.

        ``reserved`` is what was added in ``check_and_reserve``. ``actual``
        is the real cost computed from the LLM response's token counts.
        Result: row contains the real cumulative spend, not the worst-case.
        """
        if not self.enabled:
            return
        delta = actual - reserved
        if abs(delta) < 1e-9:
            return
        await self._store.add_self_eval_spend(_current_year_month(), delta)
        logger.debug(
            "self_eval_budget reconcile: reserved=%.4f actual=%.4f delta=%.4f",
            reserved,
            actual,
            delta,
        )


def _extract_response_tokens(response: Any) -> tuple[int, int]:
    """Pull (input_tokens, output_tokens) off an AgentResponse-like object.

    Returns ``(0, 0)`` if usage is absent / malformed. Stubs in tests may
    not carry usage fields; we treat that as "zero cost" so the budget
    tracker simply doesn't move.
    """
    usage = getattr(response, "usage", None)
    if not isinstance(usage, dict):
        return 0, 0
    try:
        return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
    except (TypeError, ValueError):
        return 0, 0


def _truncate(text: str, max_chars: int) -> str:
    """Trim text to ``max_chars`` with a marker so the judge knows it was cut."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n... [truncated]"


def _try_parse_self_eval(raw_text: str) -> dict[str, Any] | None:
    """Parse a Judge self_eval LLM response into ``{quality, reason, suggested_improvement}``.

    Tolerant of fenced code blocks and surrounding prose, mirroring
    :func:`parse_judge_actions`. Returns ``None`` on parse failure or when the
    required ``quality`` field is missing.
    """
    if not raw_text:
        return None
    text = raw_text.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl > 0:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    candidates: list[str] = [text]
    start = text.find("{")
    if start > 0:
        candidates.append(text[start:])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "quality" in data:
            return data
    return None


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

    def __init__(
        self,
        store: JudgeStore,
        *,
        memory_store: Any | None = None,
        self_eval_budget_usd: float = 0.0,
        self_eval_model: str = _DEFAULT_SELF_EVAL_MODEL,
    ) -> None:
        self._store = store
        # M1 PR1: budget tracker. Only created when monthly_budget_usd > 0
        # AND a memory_store with the self_eval_budget table is wired.
        self._budget: SelfEvalBudget | None = None
        if memory_store is not None and self_eval_budget_usd > 0:
            self._budget = SelfEvalBudget(
                memory_store,
                monthly_budget_usd=self_eval_budget_usd,
                default_model=self_eval_model,
            )
        self._self_eval_model = self_eval_model

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

    async def run_for_task(
        self,
        *,
        mode: Literal["completion", "self_eval"],
        registry,
        task_prompt: str,
        task_output: str,
        automation_name: str | None = None,
        skill_name: str | None = None,
        source_workspace: str | None = None,
        thread_id: str | None = None,
        task_id: str | None = None,
        req_id: str | None = None,
        model: str | None = None,
    ) -> JudgeResult:
        """M0 PR3: judge entry for headless task completion + self-evaluation.

        ``mode`` toggles the prompt template only. Both modes share the parse
        helper (``parse_judge_actions``) and the persist helper
        (``JudgeStore.apply_actions``). ``self_eval`` mode's response shape is
        translated into a same-shape add-action via
        :meth:`_coerce_self_eval_to_action` so persist plumbing stays unified.

        The ``model`` kwarg (per-call override, falls back to the configured
        ``self_eval_model``) is used BOTH for budget pricing AND to route the
        actual LLM call: it flows to ``registry.run(model_override=...)``,
        which forwards it to the running agent's ``run()`` (non-mutating —
        ClaudeAgent derives a local effective_model per call).

        Returns a :class:`JudgeResult`. On agent / parse failure the result
        carries ``error`` non-None and persist is skipped.
        """
        prompt_trunc = _truncate(task_prompt, _TASK_PROMPT_MAX_CHARS)
        output_trunc = _truncate(task_output, _TASK_OUTPUT_MAX_CHARS)

        if mode == "completion":
            active_context = self._store.to_judge_context()
            active_text = (
                json.dumps(active_context, ensure_ascii=False, indent=2)
                if active_context
                else "[]"
            )
            execution_context = self._format_execution_context(
                skill_name=skill_name,
                source_workspace=source_workspace,
                thread_topic=automation_name or "headless task",
            )
            prompt = _PROMPT_TASK_COMPLETION.format(
                execution_context=execution_context,
                active_count=len(active_context),
                active_memories=active_text,
                task_prompt=prompt_trunc,
                task_output=output_trunc,
            )
            actions, raw_text, err = await self._invoke(
                prompt, registry, req_id=req_id, label="memory_judge_task_completion"
            )
        elif mode == "self_eval":
            # M1 PR1: budget gate. If a SelfEvalBudget is wired and over
            # budget, skip the LLM call entirely (no action, no error).
            effective_model = model or self._self_eval_model
            reserved = 0.0
            if self._budget is not None:
                allowed, reserved = await self._budget.check_and_reserve(effective_model)
                if not allowed:
                    stats = {"add": 0, "strengthen": 0, "supersede": 0, "no_op": 1, "rejected": 0}
                    logger.info(
                        "memory_judge_self_eval SKIPPED automation=%s reason=budget_exceeded",
                        automation_name,
                    )
                    return JudgeResult(
                        actions=[{"op": "no_op", "reason": "self_eval_budget_exceeded"}],
                        stats=stats,
                        raw_response="",
                        error=None,
                    )

            prompt = _PROMPT_SELF_EVAL.format(
                task_prompt=prompt_trunc,
                task_output=output_trunc,
            )
            # self_eval prompt returns {"quality", "reason",
            # "suggested_improvement"} (not {"actions": [...]}), so the action
            # list ``parse_judge_actions`` returns is always empty here — we
            # discard it and re-parse the raw text for self_eval shape below.
            _discard, raw_text, err, response = await self._invoke_with_response(
                prompt,
                registry,
                req_id=req_id,
                label="memory_judge_self_eval",
                model_override=effective_model,
            )
            # Reconcile budget: refund the worst-case reservation against the
            # actual usage from the LLM response (if available). Codex round-1
            # catch: when usage is absent/malformed (e.g. provider doesn't
            # surface token counts), DO NOT refund — fail closed and keep the
            # worst-case reserve so the budget can't be silently bypassed.
            if self._budget is not None and reserved > 0:
                input_tokens, output_tokens = _extract_response_tokens(response)
                if input_tokens == 0 and output_tokens == 0:
                    # No usage signal — keep the reserve. Log so operators
                    # can debug a budget that drains faster than expected.
                    logger.info(
                        "memory_judge_self_eval: response carried no usage; "
                        "keeping reserved=%.4f (fail-closed budget gate)",
                        reserved,
                    )
                else:
                    actual_cost = _calculate_cost_usd(
                        effective_model, input_tokens, output_tokens
                    )
                    try:
                        await self._budget.reconcile(reserved, actual_cost)
                    except Exception as exc:
                        logger.warning("self_eval_budget reconcile failed: %s", exc)
            # M1 PR4: self_eval persists via upsert_self_eval_signal so the
            # LLM verdict merges with implicit/explicit signals into ONE
            # entry per task. Requires automation_name + task_id.
            if err is None:
                parsed = _try_parse_self_eval(raw_text)
                if parsed is None:
                    err = "self_eval_parse_failed"
                elif automation_name and task_id:
                    quality = str(parsed.get("quality", "")).strip().lower()
                    if quality not in {"pass", "borderline", "fail"}:
                        err = "self_eval_invalid_quality"
                    else:
                        reason = str(parsed.get("reason", "")).strip() or "(unspecified)"
                        suggested = str(parsed.get("suggested_improvement", "")).strip()
                        # upsert prefixes "reason=" itself; pass clean text.
                        reason_full = reason
                        if suggested:
                            reason_full = f"{reason}; suggested={suggested}"
                        confidence = {"pass": 0.5, "borderline": 0.4, "fail": 0.5}[quality]
                        entry_id = await self._store.upsert_self_eval_signal(
                            automation_name=automation_name,
                            task_id=task_id,
                            source="llm_judge",
                            quality=quality,
                            confidence=confidence,
                            reason=reason_full,
                            skill_name=skill_name,
                            source_workspace=source_workspace,
                        )
                        stats = {
                            "add": 1 if entry_id else 0,
                            "strengthen": 0,
                            "supersede": 0,
                            "no_op": 0,
                            "rejected": 0 if entry_id else 1,
                        }
                        logger.info(
                            "memory_judge_self_eval merged automation=%s task=%s "
                            "quality=%s entry=%s",
                            automation_name,
                            task_id,
                            quality,
                            entry_id,
                        )
                        return JudgeResult(
                            actions=[{"op": "add", "category": "self_eval", "quality": quality}]
                            if entry_id
                            else [],
                            stats=stats,
                            raw_response=raw_text,
                            error=None,
                        )
                # No automation_name/task_id → can't write self_eval (manual run)
                actions = []
            else:
                actions = []
        else:
            raise ValueError(f"unknown Judge.run_for_task mode: {mode!r}")

        if err:
            stats = {"add": 0, "strengthen": 0, "supersede": 0, "no_op": 0, "rejected": 0}
            logger.warning(
                "memory_judge_run_for_task mode=%s automation=%s err=%s",
                mode,
                automation_name,
                err,
            )
            return JudgeResult(actions=[], stats=stats, raw_response=raw_text, error=err)

        # Persist via the shared apply_actions path. For self_eval, the coerced
        # action's source_automation will be passed through judge metadata so
        # _apply_add picks it up.
        stats = await self._store.apply_actions(
            actions,
            thread_id=thread_id,
            skill_name=skill_name,
            source_workspace=source_workspace,
        )
        logger.info(
            "memory_judge_run_for_task mode=%s automation=%s actions=%d stats=%s",
            mode,
            automation_name,
            len(actions),
            stats,
        )
        return JudgeResult(actions=actions, stats=stats, raw_response=raw_text, error=None)

    @staticmethod
    def _coerce_self_eval_to_action(
        parsed: dict[str, Any], *, automation_name: str
    ) -> dict[str, Any] | None:
        """Translate self_eval LLM output → same-shape add action.

        self_eval response: ``{quality, reason, suggested_improvement}``
        ⇒ ``{op:"add", category:"self_eval", scope:"automation", ...}``

        The ``quality`` value is preserved as a structured field on the
        MemoryEntry (queryable for skill-health rejection-rate metrics);
        ``reason`` and ``suggested_improvement`` go into the summary.
        """
        quality = str(parsed.get("quality", "")).strip().lower()
        if quality not in {"pass", "borderline", "fail"}:
            return None
        reason = str(parsed.get("reason", "")).strip()
        suggested = str(parsed.get("suggested_improvement", "")).strip()
        summary = f"reason={reason}" if reason else "reason=(unspecified)"
        if suggested:
            summary = f"{summary}; suggested={suggested}"
        # confidence: pass=high, borderline=mid, fail=mid (a failure verdict is
        # itself a signal worth keeping, but verdict-quality is not high).
        confidence = {"pass": 0.7, "borderline": 0.55, "fail": 0.6}[quality]
        return {
            "op": "add",
            "summary": summary[:280],
            "category": "self_eval",
            "scope": "automation",
            "source_automation": automation_name,
            "feedback_source": "llm_judge",
            "quality": quality,
            "confidence": confidence,
        }

    async def _invoke(
        self,
        prompt: str,
        registry,
        *,
        req_id: str | None,
        label: str,
    ) -> tuple[list[dict[str, Any]], str, str | None]:
        actions, raw, err, _ = await self._invoke_with_response(
            prompt, registry, req_id=req_id, label=label
        )
        return actions, raw, err

    async def _invoke_with_response(
        self,
        prompt: str,
        registry,
        *,
        req_id: str | None,
        label: str,
        model_override: str | None = None,
    ) -> tuple[list[dict[str, Any]], str, str | None, Any]:
        """Variant of :meth:`_invoke` that also returns the raw AgentResponse.

        Used by self_eval to extract ``response.usage`` for budget reconcile.
        ``model_override`` routes this one call to a specific model (e.g. a
        cheaper self-eval model) via AgentRegistry's per-call model swap.
        """
        run_kwargs: dict[str, Any] = {"run_label": label}
        if model_override:
            run_kwargs["model_override"] = model_override
        try:
            _agent, response = await registry.run(prompt, **run_kwargs)
        except Exception as exc:
            return [], "", f"agent_exception: {exc}", None
        if response.error:
            return [], response.text or "", response.error, response
        actions = parse_judge_actions(response.text or "")
        return actions, response.text or "", None, response

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
