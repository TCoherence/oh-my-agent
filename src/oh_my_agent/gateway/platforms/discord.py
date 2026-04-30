from __future__ import annotations

import logging
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import discord
from discord import app_commands

from oh_my_agent.gateway.base import (
    ActionDescriptor,
    Attachment,
    BaseChannel,
    IncomingMessage,
    InteractivePrompt,
    MessageHandler,
    OutgoingAttachment,
)
from oh_my_agent.gateway.services import (
    AskService,
    AutomationService,
    DoctorService,
    MemoryService,
    SkillEvalService,
    TaskService,
)
from oh_my_agent.gateway.services.types import (
    AutomationStatusResult,
    DoctorResult,
    InteractiveDecision,
    MemoryListResult,
    SkillStatsResult,
    TaskActionResult,
    TaskListResult,
)
from oh_my_agent.push_notifications import (
    PushCoolDown,
    PushNotificationEvent,
)
from oh_my_agent.runtime.types import HitlPrompt
from oh_my_agent.utils.errors import user_safe_message
from oh_my_agent.utils.rate_limiter import TokenBucketLimiter

logger = logging.getLogger(__name__)

THREAD_ARCHIVE_MINUTES: Literal[60, 1440, 4320, 10080] = 60
STATUS_MESSAGE_PREFIX = "**Task Status**"
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
_ATTACHMENT_DIR = Path(tempfile.gettempdir()) / "oh-my-agent" / "attachments"


class _InteractiveView(discord.ui.View):
    def __init__(self, channel_adapter: "DiscordChannel", prompt: InteractivePrompt, *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self._channel_adapter = channel_adapter
        self._prompt = prompt

        style_map = {
            "primary": discord.ButtonStyle.primary,
            "secondary": discord.ButtonStyle.secondary,
            "danger": discord.ButtonStyle.danger,
            "success": discord.ButtonStyle.success,
        }
        for row, action in enumerate(prompt.actions):
            button: discord.ui.Button = discord.ui.Button(
                label=action.label[:80] or action.id[:80] or "Action",
                style=style_map.get(action.style, discord.ButtonStyle.secondary),
                custom_id=self._custom_id(prompt, action.id),
                disabled=disabled or action.disabled,
                row=min(row, 4),
            )

            async def _callback(
                interaction: discord.Interaction,
                *,
                action_id: str = action.id,
            ) -> None:
                await self._channel_adapter._handle_interactive_action(
                    interaction,
                    prompt=self._prompt,
                    decision=InteractiveDecision(
                        entity_id=prompt.entity_id or "",
                        entity_kind=prompt.entity_kind,
                        action_id=action_id,
                        actor_id=str(interaction.user.id),
                        message_id=str(interaction.message.id) if interaction.message is not None else None,
                    ),
                )

            # discord.py's button-handler pattern reassigns the bound method
            button.callback = _callback  # type: ignore[method-assign]
            self.add_item(button)

    @staticmethod
    def _custom_id(prompt: InteractivePrompt, action_id: str) -> str:
        parts = ["interactive", prompt.entity_kind or "generic", prompt.entity_id or "unknown", action_id]
        return ":".join(parts)


async def _download_discord_attachments(
    attachments: list[discord.Attachment],
) -> list[Attachment]:
    """Download image attachments to a local temp directory.

    Only ``image/*`` MIME types are accepted; files over 10 MB are skipped.
    """
    results: list[Attachment] = []
    _ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)

    for att in attachments:
        ct = att.content_type or ""
        if not ct.startswith("image/"):
            continue
        if att.size > _MAX_IMAGE_BYTES:
            logger.warning(
                "Skipping oversized attachment %s (%d bytes)", att.filename, att.size
            )
            continue
        # Prefix filename with uuid to avoid collisions
        safe_name = f"{uuid.uuid4().hex[:8]}_{att.filename}"
        dest = _ATTACHMENT_DIR / safe_name
        try:
            await att.save(dest)
            results.append(
                Attachment(
                    filename=att.filename,
                    content_type=ct,
                    local_path=dest,
                    original_url=att.url,
                    size_bytes=att.size,
                )
            )
        except Exception:
            logger.warning("Failed to download attachment %s", att.filename, exc_info=True)
    return results


def _start_of_today_utc() -> str:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.strftime("%Y-%m-%d %H:%M:%S")


def _hours_ago_utc(hours: int) -> str:
    ts = datetime.now(timezone.utc) - timedelta(hours=hours)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _render_reflection_result(result: Any) -> str:
    """Render a DiaryReflector.ReflectionResult for Discord ephemeral replies."""
    lines = [f"**Diary reflection — {result.diary_date.isoformat()}**"]
    if result.skipped_reason:
        lines.append(f"_skipped: {result.skipped_reason}_")
        return "\n".join(lines)
    if result.error:
        lines.append(f"❌ error: `{result.error[:200]}`")
        return "\n".join(lines)
    stats = result.stats or {}
    added = int(stats.get("add") or 0)
    strengthened = int(stats.get("strengthen") or 0)
    superseded = int(stats.get("supersede") or 0)
    no_op = int(stats.get("no_op") or 0)
    rejected = int(stats.get("rejected") or 0)
    lines.append(
        f"✅ actions: `{len(result.actions)}` · "
        f"+{added} ~{strengthened} ↪{superseded} ∅{no_op} rej{rejected}"
    )
    if added + strengthened + superseded > 0:
        for action in (result.actions or [])[:6]:
            op = str(action.get("op") or "")
            if op in {"add", "supersede"}:
                summary = str(action.get("summary") or action.get("new_summary") or "")[:140]
                lines.append(f"• `{op}` — {summary}")
    return "\n".join(lines)


def _render_usage_summary(summary: dict[str, Any], *, title: str) -> str:
    total = summary.get("total") or {}
    by_agent = summary.get("by_agent") or []
    by_source = summary.get("by_source") or []

    def _fmt_cost(value: Any) -> str:
        try:
            return f"${float(value or 0):.4f}"
        except (TypeError, ValueError):
            return "$0.0000"

    lines = [f"**{title}**"]
    events = int(total.get("events") or 0)
    if events == 0:
        lines.append("_no usage recorded in this window._")
        return "\n".join(lines)
    in_tok = int(total.get("input_tokens") or 0)
    out_tok = int(total.get("output_tokens") or 0)
    cache_r = int(total.get("cache_read_input_tokens") or 0)
    cache_w = int(total.get("cache_creation_input_tokens") or 0)
    lines.append(
        f"Total: `{events}` events · `{in_tok:,}` in / `{out_tok:,}` out · "
        f"cache `{cache_r:,}r/{cache_w:,}w` · {_fmt_cost(total.get('cost_usd'))}"
    )
    if by_agent:
        lines.append("**By agent:**")
        for row in by_agent[:8]:
            lines.append(
                f"• `{row.get('agent')}` — `{int(row.get('events') or 0)}` events · "
                f"`{int(row.get('input_tokens') or 0):,}` in / `{int(row.get('output_tokens') or 0):,}` out · "
                f"{_fmt_cost(row.get('cost_usd'))}"
            )
    if by_source:
        lines.append("**By source:**")
        for row in by_source[:8]:
            lines.append(
                f"• `{row.get('source')}` — `{int(row.get('events') or 0)}` events · "
                f"{_fmt_cost(row.get('cost_usd'))}"
            )
    return "\n".join(lines)


def _parse_optional_positive_int(raw: str, field: str) -> tuple[int | None, str | None]:
    """Parse an optional positive integer from a modal/textinput string.

    Returns ``(value, error)``. Empty / whitespace input yields ``(None, None)``.
    Non-integer or non-positive input yields ``(None, <human-readable error>)``.
    Strict: never silently coerces.
    """
    stripped = (raw or "").strip()
    if not stripped:
        return None, None
    try:
        value = int(stripped)
    except ValueError:
        return None, f"`{field}` must be an integer; got {stripped!r}."
    if value <= 0:
        return None, f"`{field}` must be positive; got {value}."
    return value, None


class _TaskSuggestModal(discord.ui.Modal):
    """Modal launched from the Suggest button to collect suggestion + optional budget.

    One-shot / transient: Discord does not persist modals across bot restarts,
    so no rehydration is needed (unlike interactive button views). Budget
    overrides are validated with ``_parse_optional_positive_int``; any bad
    input short-circuits with an ephemeral error and ``decide()`` is NOT called.
    """

    def __init__(
        self,
        *,
        prompt: InteractivePrompt,
        decision: InteractiveDecision,
        original_message_id: str,
        channel: "DiscordChannel",
    ) -> None:
        # Title is capped at 45 chars by Discord; truncate task id safely.
        task_id_hint = (decision.entity_id or "")[:12]
        super().__init__(title=f"Suggest changes — task {task_id_hint}"[:45])
        self._prompt = prompt
        self._decision = decision
        self._original_message_id = original_message_id
        self._channel = channel

        self._suggestion_input: discord.ui.TextInput = discord.ui.TextInput(
            label="Suggestion",
            style=discord.TextStyle.paragraph,
            placeholder="What should the agent change on the next run?",
            required=True,
            max_length=2000,
        )
        # NOTE: these labels intentionally say "per call" to disambiguate the
        # per-invocation agent turn/timeout budget (Claude's ``--max-turns`` /
        # subprocess timeout) from the OUTER runtime loop budget
        # (``max_steps`` / ``max_minutes``), which this modal does NOT affect.
        # ``step=N/M`` in operator logs is the outer counter; the override here
        # feeds into each inner agent call.
        self._max_turns_input: discord.ui.TextInput = discord.ui.TextInput(
            label="Agent max-turns per call (optional)",
            style=discord.TextStyle.short,
            placeholder="claude --max-turns; e.g. 45",
            required=False,
            max_length=4,
        )
        self._timeout_input: discord.ui.TextInput = discord.ui.TextInput(
            label="Agent timeout sec per call (optional)",
            style=discord.TextStyle.short,
            placeholder="per-call timeout; e.g. 900",
            required=False,
            max_length=6,
        )
        self.add_item(self._suggestion_input)
        self.add_item(self._max_turns_input)
        self.add_item(self._timeout_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        max_turns, err_turns = _parse_optional_positive_int(
            str(self._max_turns_input.value), "max_turns"
        )
        if err_turns:
            await interaction.response.send_message(err_turns, ephemeral=True)
            return
        timeout_seconds, err_timeout = _parse_optional_positive_int(
            str(self._timeout_input.value), "timeout_seconds"
        )
        if err_timeout:
            await interaction.response.send_message(err_timeout, ephemeral=True)
            return
        suggestion = str(self._suggestion_input.value).strip()
        await self._channel._finalize_task_decision(
            interaction,
            prompt=self._prompt,
            decision=self._decision,
            message_id=self._original_message_id,
            suggestion=suggestion,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
        )


class DiscordChannel(BaseChannel):
    """Discord platform adapter implementing BaseChannel.

    Supports both regular messages and slash commands
    (``/reset``, ``/history``, ``/agent``, ``/search``, and the runtime /
    skills / memory / automation command families).
    """

    supports_streaming_edit: bool = True

    def __init__(
        self,
        token: str,
        channel_id: str,
        owner_user_ids: set[str] | None = None,
        *,
        push_dispatcher=None,
        mention_cool_down_seconds: float = 60.0,
    ) -> None:
        self._token = token
        self._channel_id = channel_id
        self._owner_user_ids = owner_user_ids or set()
        # External push (Bark, etc.) — fires when a non-owner mentions an owner
        # in an accepted channel. Optional; ``None`` means push is fully off.
        self._push_dispatcher = push_dispatcher
        # Anti-spam: coalesce bursts of @-mentions from the same author in
        # the same channel into one push. ``@everyone`` style flooding or
        # bot-loops mentioning the owner repeatedly should not trigger N
        # lock-screen alerts.
        self._mention_cooldown = PushCoolDown(mention_cool_down_seconds)
        # Dump channels are send/reply-only aliases registered by the gateway
        # manager after construction. The bot must still accept replies to
        # messages posted there so follow-up threads can be spawned on them.
        self._dump_channel_ids: set[str] = set()
        self._client: discord.Client | None = None
        # Injected by GatewayManager after construction
        self._session = None  # ChannelSession
        self._registry = None  # AgentRegistry
        self._memory_store = None  # MemoryStore
        self._skill_syncer = None  # SkillSync
        self._workspace_skills_dirs = None  # list[Path] | None
        self._runtime_service = None  # RuntimeService
        self._judge_store = None  # JudgeStore
        self._gateway_manager = None  # GatewayManager (for /memorize)
        self._scheduler = None  # Scheduler
        self._diary_reflector = None  # DiaryReflector
        self._ask_service = AskService()
        self._skill_eval_enabled = True
        self._skill_stats_recent_days = 7
        self._skill_feedback_emojis = {"👍", "👎"}
        self._rate_limiter = TokenBucketLimiter(rate=5.0, burst=10)
        # Populate services up-front so type-checkers know they're always set.
        # Each service tolerates None dependencies; setters below reset the
        # attributes once real dependencies are injected.
        self._task_service: TaskService
        self._doctor_service: DoctorService
        self._automation_service: AutomationService
        self._memory_service: MemoryService
        self._skill_eval_service: SkillEvalService
        self._refresh_services()

    @property
    def platform(self) -> str:
        return "discord"

    @property
    def channel_id(self) -> str:
        return self._channel_id

    def set_session_context(self, session, registry, memory_store=None) -> None:
        """Inject session objects needed by slash commands."""
        self._session = session
        self._registry = registry
        self._memory_store = memory_store
        self._refresh_services()

    def set_skill_syncer(self, syncer, workspace_skills_dirs=None) -> None:
        """Inject skill syncer for the ``/reload-skills`` slash command."""
        self._skill_syncer = syncer
        self._workspace_skills_dirs = workspace_skills_dirs

    def set_runtime_service(self, runtime_service) -> None:
        """Inject runtime service for /task_* commands and decision buttons."""
        self._runtime_service = runtime_service
        self._refresh_services()

    def set_diary_reflector(self, reflector) -> None:
        """Inject a DiaryReflector so ``/reflect_yesterday`` can run on demand."""
        self._diary_reflector = reflector

    def register_dump_channel(self, channel_id: str) -> None:
        """Record a dump channel id so ``on_message`` will route replies
        that arrive there (e.g. users replying to an automation terminal
        message posted to the dump channel) back into the gateway."""
        if channel_id and channel_id != self._channel_id:
            self._dump_channel_ids.add(str(channel_id))

    def _render_hitl_prompt_message(self, prompt: HitlPrompt) -> str:
        if prompt.status == "completed":
            selected = prompt.selected_choice_label or prompt.selected_choice_id or "unknown"
            lines = [
                "**Input resolved**",
                f"Prompt: `{prompt.id}`",
                f"Question: {prompt.question}",
                f"Selected: **{selected}** (`{prompt.selected_choice_id or ''}`)",
            ]
            if prompt.selected_choice_description:
                lines.append(f"Details: {prompt.selected_choice_description}")
            return "\n".join(lines)[:1900]
        if prompt.status == "resolving":
            selected = prompt.selected_choice_label or prompt.selected_choice_id or "unknown"
            lines = [
                "**Input recorded**",
                f"Prompt: `{prompt.id}`",
                f"Question: {prompt.question}",
                f"Selected: **{selected}** (`{prompt.selected_choice_id or ''}`)",
                "Status: resuming the agent with your choice.",
            ]
            if prompt.selected_choice_description:
                lines.append(f"Details: {prompt.selected_choice_description}")
            return "\n".join(lines)[:1900]
        if prompt.status == "cancelled":
            return (
                f"**Input cancelled**\n"
                f"Prompt: `{prompt.id}`\n"
                f"Question: {prompt.question}"
            )[:1900]
        if prompt.status == "failed":
            return (
                f"**Input unavailable**\n"
                f"Prompt: `{prompt.id}`\n"
                f"Question: {prompt.question}"
            )[:1900]

        lines = [
            "**Input required**",
            f"Prompt: `{prompt.id}`",
            f"Question: {prompt.question}",
        ]
        if prompt.details:
            lines.append(f"Details: {prompt.details}")
        lines.append("")
        lines.append("Choices:")
        for idx, choice in enumerate(prompt.choices, start=1):
            label = str(choice.get("label") or choice.get("id") or "")
            description = choice.get("description")
            if description:
                lines.append(f"{idx}. **{label}** — {description}")
            else:
                lines.append(f"{idx}. **{label}**")
        lines.extend(
            [
                "",
                "Only the configured owner can answer this prompt.",
            ]
        )
        return "\n".join(lines)[:1900]

    @staticmethod
    def _format_automation_schedule(record) -> str:
        if record.schedule:
            return record.schedule
        if getattr(record, "cron", None):
            return f"cron `{record.cron}`"
        return f"interval `{record.interval_seconds}s`"

    @staticmethod
    def _format_automation_target(record) -> str:
        if record.target:
            return record.target
        if getattr(record, "delivery", None) == "dm":
            return f"dm user `{record.target_user_id or '?'}` via channel `{record.channel_id}`"
        if getattr(record, "thread_id", None):
            return f"channel `{record.channel_id}` thread `{record.thread_id}`"
        return f"channel `{record.channel_id}`"

    def _render_task_action_result(self, result: TaskActionResult) -> str:
        task = result.task
        if task is None:
            return result.message[:1900]
        lines = [
            f"**Task** `{task.id}`",
            f"- Status: `{task.status}`",
            f"- Type: `{task.task_type}`",
            f"- Completion: `{task.completion_mode}`",
            f"- Goal: {task.goal[:200]}",
            f"- Step: {task.step_no}/{task.max_steps}",
            f"- Budget: {task.max_minutes} min",
            f"- Agent: `{task.preferred_agent or 'fallback'}`",
        ]
        if task.blocked_reason:
            lines.append(f"- Blocked: {task.blocked_reason[:300]}")
        if task.error:
            lines.append(f"- Error: {task.error[:300]}")
        if task.output_summary:
            lines.append(f"- Output: {task.output_summary[:300]}")
        if task.artifact_manifest:
            lines.append(f"- Artifacts: {', '.join(task.artifact_manifest[:8])[:300]}")
        if task.merge_commit_hash:
            lines.append(f"- Commit: `{task.merge_commit_hash}`")
        if task.merge_error:
            lines.append(f"- Merge error: {task.merge_error[:300]}")
        if task.workspace_path:
            lines.append(f"- Workspace: `{task.workspace_path}`")
        return "\n".join(lines)[:1900]

    def _render_task_list_result(self, result: TaskListResult) -> str:
        if not result.tasks:
            return "No runtime tasks found."
        lines = [f"**Runtime tasks** ({len(result.tasks)})"]
        for task in result.tasks:
            lines.append(
                f"- `{task.task_id}` [{task.status}] `{task.task_type}` {task.step_info or ''} · {task.goal[:80]}".rstrip()
            )
        return "\n".join(lines)[:1900]

    def _render_doctor_result(self, result: DoctorResult) -> str:
        parts: list[str] = []
        for section in result.sections:
            if parts:
                parts.append("")
            parts.append(f"**{section.title}**")
            parts.extend(section.lines)
        return "\n".join(parts)[:1900]

    def _render_automation_status_result(
        self,
        result: AutomationStatusResult,
        *,
        name: str | None = None,
    ) -> str:
        if not result.success:
            return result.message[:1900]
        if name:
            record = result.automations[0]
            state_label = "enabled" if record.enabled else "disabled"
            lines = [
                f"**Automation** `{record.name}`",
                f"- State: `{state_label}`",
                f"- Schedule: {self._format_automation_schedule(record)}",
                f"- Scheduler timezone: `{result.scheduler_timezone or '—'}`",
                f"- Delivery: `{record.delivery}`",
                f"- Target: {self._format_automation_target(record)}",
                f"- Agent: `{record.agent or 'fallback'}`",
            ]
            if record.timeout_seconds is not None:
                lines.append(f"- Timeout override: `{record.timeout_seconds}s`")
            if record.max_turns is not None:
                lines.append(f"- Max turns override: `{record.max_turns}`")
            if record.author:
                lines.append(f"- Author: `{record.author}`")
            if record.source_path:
                lines.append(f"- Source: `{record.source_path}`")
            if any(
                value is not None
                for value in (record.last_run_at, record.last_success_at, record.next_run_at, record.last_task_id, record.last_error)
            ):
                lines.extend(
                    [
                        "",
                        "**Runtime state**",
                        f"- Last run: `{record.last_run_at or '—'}`",
                        f"- Last success: `{record.last_success_at or '—'}`",
                        f"- Next run: `{record.next_run_at or '—'}`",
                        f"- Last task: `{record.last_task_id or '—'}`",
                    ]
                )
                if record.last_error:
                    lines.append(f"- Last error: {record.last_error[:200]}")
            if record.active_tasks:
                lines.append("")
                lines.append(f"**Active tasks** ({len(record.active_tasks)})")
                for task in record.active_tasks[:5]:
                    label = "started" if task.started_at else "created"
                    timestamp = task.started_at or task.created_at or "—"
                    lines.append(
                        f"- `{task.id[:12]}` [{task.status}] step {task.step_no} · {label} `{timestamp}`"
                    )
                if len(record.active_tasks) > 5:
                    lines.append(f"_…and {len(record.active_tasks) - 5} more_")
            return "\n".join(lines)[:1900]

        enabled_records = [record for record in result.automations if record.enabled]
        disabled_records = [record for record in result.automations if not record.enabled]
        failed_names = [record.name for record in result.automations if record.last_error]
        lines = [f"**Automations** — {len(enabled_records)} enabled, {len(disabled_records)} disabled"]
        if result.scheduler_timezone:
            lines.append(f"- Scheduler timezone: `{result.scheduler_timezone}`")
        if failed_names:
            lines.append(
                f"- Recent failures: `{len(failed_names)}` ({', '.join(f'`{name}`' for name in failed_names[:5])})"
            )
        if enabled_records:
            lines.append("**Enabled**")
            for record in enabled_records[:12]:
                suffix = " ⚠️" if record.last_error else (" ✓" if record.last_success_at else "")
                active_marker = f" · {len(record.active_tasks)} active" if record.active_tasks else ""
                lines.append(
                    f"- `{record.name}` · {self._format_automation_schedule(record)} · {self._format_automation_target(record)}{active_marker}{suffix}"
                )
        if disabled_records:
            lines.append("**Disabled**")
            for record in disabled_records[:12]:
                active_marker = f" · {len(record.active_tasks)} active" if record.active_tasks else ""
                lines.append(
                    f"- `{record.name}` · {self._format_automation_schedule(record)} · {self._format_automation_target(record)}{active_marker}"
                )
        if len(result.automations) > 24:
            lines.append(f"_…and {len(result.automations) - 24} more_")
        lines.append("_Invalid or conflicting automation files remain log-visible only._")
        return "\n".join(lines)[:1900]

    @staticmethod
    def _render_memory_list_result(result: MemoryListResult) -> str:
        if not result.success or not result.entries:
            return result.message[:1900]
        lines = [f"**Memories** — {result.total_active} active"]
        for entry in result.entries[:25]:
            conf_bar = "█" * int(entry.confidence * 5) + "░" * (5 - int(entry.confidence * 5))
            observed_at = entry.last_observed_at[:10]
            lines.append(
                f"`{entry.memory_id}` [{conf_bar}] **[{entry.category}/{entry.scope}]**"
                f" obs={entry.observation_count} seen={observed_at} {entry.summary}"
            )
        if len(result.entries) > 25:
            lines.append(f"_…and {len(result.entries) - 25} more_")
        return "\n".join(lines)[:2000]

    @staticmethod
    def _render_skill_stats_result(result: SkillStatsResult) -> str:
        if not result.success or not result.stats:
            return result.message[:1900]
        if result.skill_filter:
            row = result.stats[0]
            lines = [
                f"**Skill** `{row.skill_name}`",
                f"- State: `{'auto-disabled' if row.auto_disabled else 'enabled'}`",
                f"- Total invocations: {row.total_invocations}",
                f"- Recent invocations ({result.recent_days}d): {row.recent_invocations}",
                f"- Recent success/error/timeout/cancelled: {row.recent_successes}/{row.recent_errors}/{row.recent_timeouts}/{row.recent_cancelled}",
                f"- Avg latency: {row.recent_avg_latency_ms / 1000:.2f}s",
                f"- Feedback: 👍 {row.thumbs_up} / 👎 {row.thumbs_down} / net {row.net_feedback:+d}",
            ]
            if row.last_invoked_at:
                lines.append(f"- Last invoked: {row.last_invoked_at}")
            if row.merged_commit_hash:
                lines.append(f"- Last merged commit: `{row.merged_commit_hash}`")
            if row.auto_disabled_reason:
                lines.append(f"- Auto-disabled reason: {row.auto_disabled_reason}")
            if row.latest_evaluations:
                lines.append("**Latest evaluations**")
                for item in row.latest_evaluations:
                    lines.append(
                        f"- `{item['evaluation_type']}` [{item['status']}] {item['summary']}"
                    )
            return "\n".join(lines)[:2000]
        lines = [f"**Skill stats** — last {result.recent_days} day(s)"]
        for row in result.stats[:15]:
            recent = row.recent_invocations
            rate = (row.recent_successes / recent) if recent else 0.0
            badge = "disabled" if row.auto_disabled else "enabled"
            lines.append(
                f"- `{row.skill_name}` [{badge}] "
                f"success {rate:.0%} · recent {recent} · avg {row.recent_avg_latency_ms / 1000:.2f}s · feedback {row.net_feedback:+d}"
            )
        if len(result.stats) > 15:
            lines.append(f"_…and {len(result.stats) - 15} more_")
        return "\n".join(lines)[:2000]

    @staticmethod
    def _with_selected_choice(prompt: HitlPrompt, choice_id: str | None) -> HitlPrompt:
        if not choice_id:
            return prompt
        selected = next((choice for choice in prompt.choices if str(choice.get("id") or "") == choice_id), None)
        if selected is None:
            return prompt
        return replace(
            prompt,
            status="resolving",
            selected_choice_id=str(selected.get("id") or ""),
            selected_choice_label=str(selected.get("label") or "") or str(selected.get("id") or ""),
            selected_choice_description=(
                str(selected.get("description")).strip()
                if selected.get("description") is not None
                else None
            ),
        )

    def _build_hitl_interactive_prompt(self, prompt: HitlPrompt, *, disabled: bool = False) -> InteractivePrompt:
        actions = [
            ActionDescriptor(
                id=str(choice.get("id") or ""),
                label=str(choice.get("label") or "")[:80] or str(choice.get("id") or "")[:80] or "Choice",
                style="primary",
                disabled=disabled,
            )
            for choice in prompt.choices
        ]
        actions.append(ActionDescriptor(id="cancel", label="Cancel", style="secondary", disabled=disabled))
        return InteractivePrompt(
            text=self._render_hitl_prompt_message(prompt),
            actions=actions,
            entity_kind="hitl",
            entity_id=prompt.id,
        )

    def _build_task_interactive_prompt(
        self,
        *,
        draft_text: str,
        task_id: str,
        nonce: str,
        actions: list[str],
        disabled: bool = False,
    ) -> InteractivePrompt:
        action_meta = {
            "approve": ("Approve", "success"),
            "reject": ("Reject", "danger"),
            "suggest": ("Suggest", "secondary"),
            "merge": ("Merge", "success"),
            "discard": ("Discard", "danger"),
            "request_changes": ("Request Changes", "secondary"),
            "replace": ("Replace", "secondary"),
            "rerun_bump_turns": ("Re-run +30 turns", "primary"),
        }
        descriptors = [
            ActionDescriptor(
                id=action,
                label=action_meta[action][0],
                style=action_meta[action][1],
                disabled=disabled,
            )
            for action in actions
            if action in action_meta
        ]
        return InteractivePrompt(
            text=draft_text,
            actions=descriptors,
            idempotency_key=nonce,
            entity_kind="task",
            entity_id=task_id,
        )

    async def send_hitl_prompt(self, *, thread_id: str, prompt: HitlPrompt) -> str | None:
        return await self.send_interactive(thread_id, self._build_hitl_interactive_prompt(prompt))

    async def _rehydrate_hitl_prompt_views(self, client: discord.Client) -> None:
        if not self._runtime_service:
            return
        prompts = await self._runtime_service.list_active_hitl_prompts(
            platform=self.platform,
            channel_id=self._channel_id,
            limit=200,
        )
        restored = 0
        for prompt in prompts:
            if not prompt.prompt_message_id:
                continue
            try:
                message_id = int(prompt.prompt_message_id)
            except (TypeError, ValueError):
                logger.warning("Skipping HITL prompt view restore for non-numeric message id %r", prompt.prompt_message_id)
                continue
            client.add_view(
                self._build_interactive_view(self._build_hitl_interactive_prompt(prompt)),
                message_id=message_id,
            )
            restored += 1
        if restored:
            logger.info("[discord] Restored %d active HITL prompt view(s)", restored)

    async def _handle_interactive_action(
        self,
        interaction: discord.Interaction,
        *,
        prompt: InteractivePrompt,
        decision: InteractiveDecision,
    ) -> None:
        if prompt.entity_kind == "hitl":
            await self._handle_hitl_interaction(
                interaction,
                prompt_id=decision.entity_id,
                choice_id=None if decision.action_id == "cancel" else decision.action_id,
                cancel=decision.action_id == "cancel",
            )
            return
        if prompt.entity_kind == "task":
            await self._handle_task_interaction(interaction, prompt=prompt, decision=decision)
            return
        await interaction.response.send_message("Unsupported interactive action.", ephemeral=True)

    async def _handle_hitl_interaction(
        self,
        interaction: discord.Interaction,
        *,
        prompt_id: str,
        choice_id: str | None,
        cancel: bool,
    ) -> None:
        if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
            await interaction.response.send_message(
                "This interactive prompt is restricted to the configured owner.",
                ephemeral=True,
            )
            return
        if not self._runtime_service:
            await interaction.response.send_message(
                "Runtime service is not enabled.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        prompt = await self._runtime_service.get_hitl_prompt(prompt_id)
        if not cancel and prompt is not None and interaction.message is not None:
            resolving_prompt = self._with_selected_choice(prompt, choice_id)
            try:
                await self.update_interactive(
                    str(interaction.channel_id),
                    str(interaction.message.id),
                    self._build_hitl_interactive_prompt(resolving_prompt, disabled=True),
                )
            except Exception:
                logger.debug("Failed to update HITL prompt message %s to resolving", prompt_id, exc_info=True)
            await interaction.followup.send("Input recorded. Resuming now...", ephemeral=True)
        if cancel:
            result = await self._runtime_service.cancel_hitl_prompt(
                prompt_id,
                actor_id=str(interaction.user.id),
            )
        else:
            result = await self._runtime_service.answer_hitl_prompt(
                prompt_id,
                choice_id=str(choice_id or ""),
                actor_id=str(interaction.user.id),
            )

        prompt = await self._runtime_service.get_hitl_prompt(prompt_id)
        if prompt is not None and interaction.message is not None:
            disabled = prompt.status in {"completed", "cancelled", "failed"}
            try:
                await self.update_interactive(
                    str(interaction.channel_id),
                    str(interaction.message.id),
                    self._build_hitl_interactive_prompt(prompt, disabled=disabled),
                )
            except Exception:
                logger.debug("Failed to update HITL prompt message %s", prompt_id, exc_info=True)

        if cancel or (prompt is not None and prompt.status == "failed"):
            await interaction.followup.send(result[:1900], ephemeral=True)

    async def _handle_task_interaction(
        self,
        interaction: discord.Interaction,
        *,
        prompt: InteractivePrompt,
        decision: InteractiveDecision,
    ) -> None:
        if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
            await interaction.response.send_message(
                "This interactive prompt is restricted to the configured owner.",
                ephemeral=True,
            )
            return
        if self._task_service is None:
            await interaction.response.send_message(
                "Runtime service is not enabled.",
                ephemeral=True,
            )
            return
        # Capture the original prompt message id up-front: after a modal submit,
        # ``interaction.message`` is None, so we must fall back to the decision
        # (which recorded it at click time).
        original_message_id = (
            str(interaction.message.id)
            if interaction.message is not None
            else str(decision.message_id or "")
        )
        if decision.action_id == "suggest":
            # Modals REQUIRE an unacknowledged interaction — must NOT defer first.
            await interaction.response.send_modal(
                _TaskSuggestModal(
                    prompt=prompt,
                    decision=decision,
                    original_message_id=original_message_id,
                    channel=self,
                )
            )
            return
        await self._finalize_task_decision(
            interaction,
            prompt=prompt,
            decision=decision,
            message_id=original_message_id,
        )

    async def _finalize_task_decision(
        self,
        interaction: discord.Interaction,
        *,
        prompt: InteractivePrompt,
        decision: InteractiveDecision,
        message_id: str,
        suggestion: str | None = None,
        max_turns: int | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        """Shared finalize flow for both direct button clicks and modal submits.

        - Direct click: ``interaction.response`` is pristine → we defer first.
        - Modal submit: ``interaction.response`` was already consumed by
          ``send_modal`` on the parent interaction; the submit interaction's
          response is still fresh, so we defer it too before updating the
          original prompt message.

        We always finish with ``interaction.followup.send`` (never
        ``response.send_message``) to stay safe regardless of entry point.
        """
        assert self._task_service is not None
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        task = None
        if self._runtime_service is not None:
            task = await self._runtime_service.get_task(decision.entity_id)
        try:
            await self.update_interactive(
                str(interaction.channel_id),
                message_id,
                self._build_task_interactive_prompt(
                    draft_text=self._task_service.build_processing_text(
                        original_text=prompt.text,
                        task=task,
                        action=decision.action_id,
                    ),
                    task_id=decision.entity_id,
                    nonce=prompt.idempotency_key or "",
                    actions=self._task_service.disable_actions(task),
                    disabled=True,
                ),
            )
        except Exception:
            logger.debug("Failed to update task prompt %s to processing", decision.entity_id, exc_info=True)
        result = await self._task_service.decide(
            platform=self.platform,
            channel_id=self._channel_id,
            thread_id=str(interaction.channel_id),
            task_id=decision.entity_id,
            action=decision.action_id,
            actor_id=decision.actor_id,
            source="button",
            nonce=prompt.idempotency_key,
            suggestion=suggestion,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
        )
        try:
            await self.update_interactive(
                str(interaction.channel_id),
                message_id,
                self._build_task_interactive_prompt(
                    draft_text=self._task_service.build_task_draft_text(
                        original_text=prompt.text,
                        task=result.task,
                        result_message=result.message,
                    ),
                    task_id=decision.entity_id,
                    nonce=prompt.idempotency_key or "",
                    actions=self._task_service.disable_actions(
                        result.task,
                        suggestion_only=decision.action_id == "suggest",
                    ),
                    disabled=True,
                ),
            )
        except Exception:
            logger.debug("Failed to finalize task prompt %s", decision.entity_id, exc_info=True)
        await interaction.followup.send(result.message[:1900], ephemeral=True)

    def set_scheduler(self, scheduler) -> None:
        """Inject scheduler for /automation_* commands."""
        self._scheduler = scheduler
        self._refresh_services()

    def set_judge_store(self, store, gateway_manager=None) -> None:
        """Inject the judge memory store + gateway for /memories /forget /memorize commands."""
        self._judge_store = store
        if gateway_manager is not None:
            self._gateway_manager = gateway_manager
        self._refresh_services()

    def _refresh_services(self) -> None:
        fire_automation = self._scheduler.fire_job_now if self._scheduler is not None else None
        self._task_service = TaskService(
            self._runtime_service,
            self._memory_store,
            fire_automation=fire_automation,
        )
        self._doctor_service = DoctorService(self._runtime_service)
        self._automation_service = AutomationService(self._scheduler, self._memory_store)
        self._memory_service = MemoryService(
            self._judge_store,
            gateway_manager=self._gateway_manager,
            registry=self._registry,
        )
        self._skill_eval_service = SkillEvalService(
            self._memory_store,
            recent_days=self._skill_stats_recent_days,
            feedback_emojis=self._skill_feedback_emojis,
        )

    def set_skill_evaluation_config(self, cfg: dict | None) -> None:
        cfg = cfg or {}
        self._skill_eval_enabled = bool(cfg.get("enabled", True))
        self._skill_stats_recent_days = int(cfg.get("stats_recent_days", 7))
        emojis = cfg.get("feedback_emojis", ["👍", "👎"])
        self._skill_feedback_emojis = {str(e) for e in emojis if str(e)}
        self._refresh_services()

    def supports_buttons(self) -> bool:
        return True

    async def _acquire_outbound_slot(self) -> None:
        await self._rate_limiter.acquire()

    async def _send_interaction_error(self, interaction: discord.Interaction, text: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text[:1900], ephemeral=True)
            else:
                await interaction.response.send_message(text[:1900], ephemeral=True)
        except Exception:
            logger.warning("Failed to deliver app-command error response", exc_info=True)

    async def start(self, handler: MessageHandler) -> None:
        _handler = handler

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        tree = app_commands.CommandTree(client)
        self._client = client

        target_id = int(self._channel_id)

        async def _sync_skill_feedback_from_payload(payload: discord.RawReactionActionEvent) -> None:
            if not self._skill_eval_enabled or self._skill_eval_service is None:
                return
            if not self._skill_eval_service.is_feedback_emoji(str(payload.emoji)):
                return
            if self._owner_user_ids and str(payload.user_id) not in self._owner_user_ids:
                return
            if hasattr(client, "user") and client.user and payload.user_id == client.user.id:
                return

            channel_obj = client.get_channel(payload.channel_id)
            if channel_obj is None:
                channel_obj = await client.fetch_channel(payload.channel_id)
            if not isinstance(channel_obj, (discord.TextChannel, discord.Thread, discord.DMChannel)):
                return
            message = await channel_obj.fetch_message(payload.message_id)

            active_score = None
            for reaction in message.reactions:
                reaction_emoji = str(reaction.emoji)
                if not self._skill_eval_service.is_feedback_emoji(reaction_emoji):
                    continue
                users = [u async for u in reaction.users()]
                if any(u.id == payload.user_id for u in users):
                    active_score = 1 if reaction_emoji == "👍" else -1

            await self._skill_eval_service.record_reaction(
                message_id=str(payload.message_id),
                actor_id=str(payload.user_id),
                platform=self.platform,
                channel_id=self._channel_id,
                thread_id=str(payload.channel_id),
                active_score=active_score,
            )

        def _interaction_thread_id(interaction: discord.Interaction) -> str | None:
            ch = interaction.channel
            if isinstance(ch, discord.Thread):
                if ch.parent_id != target_id:
                    return None
                return str(ch.id)
            if interaction.channel_id == target_id:
                return str(interaction.channel_id)
            return None

        # ---- Slash commands ------------------------------------------------

        @tree.command(name="reset", description="Clear conversation history for this thread")
        async def slash_reset(interaction: discord.Interaction):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This bot is currently restricted to the configured owner.",
                    ephemeral=True,
                )
                return

            ch = interaction.channel
            if not isinstance(ch, discord.Thread) or ch.parent_id != target_id:
                await interaction.response.send_message(
                    "Use this command inside a conversation thread.",
                    ephemeral=True,
                )
                return

            result = await self._ask_service.reset_history(self._session, str(ch.id))
            await interaction.response.send_message(result.message, ephemeral=not result.success)

        @tree.command(name="history", description="Show conversation history for this thread (for debugging)")
        async def slash_history(interaction: discord.Interaction):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This bot is currently restricted to the configured owner.",
                    ephemeral=True,
                )
                return

            ch = interaction.channel
            if not isinstance(ch, discord.Thread) or ch.parent_id != target_id:
                await interaction.response.send_message(
                    "Use this command inside a conversation thread.",
                    ephemeral=True,
                )
                return

            result = await self._ask_service.get_history(self._session, str(ch.id))
            await interaction.response.send_message(result.message[:2000], ephemeral=True)

        @tree.command(name="agent", description="Show available agents and their status")
        async def slash_agent(interaction: discord.Interaction):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This bot is currently restricted to the configured owner.",
                    ephemeral=True,
                )
                return

            result = await self._ask_service.list_agents(self._registry)
            await interaction.response.send_message(result.message[:2000], ephemeral=not result.success)

        @tree.command(name="search", description="Search across all conversation history")
        @app_commands.describe(
            query="Search query",
            limit="Max results (default 5)",
        )
        async def slash_search(
            interaction: discord.Interaction,
            query: str,
            limit: int = 5,
        ):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This bot is currently restricted to the configured owner.",
                    ephemeral=True,
                )
                return

            if not self._memory_store:
                await interaction.response.send_message(
                    "Memory store not configured.", ephemeral=True,
                )
                return

            await interaction.response.defer()
            cap = min(limit, 20)
            results = await self._memory_store.search(query, limit=cap)
            if not results:
                await interaction.followup.send(f"No results for **{query}**.")
                return

            display = results[:10]
            header = f'**Search:** "{query}"'
            if len(results) > len(display):
                header += f" — showing first {len(display)} of {len(results)}"
            else:
                header += f" — {len(results)} result(s)"

            lines = [header]
            for r in display:
                role = r.get("role", "?")
                agent = r.get("agent") or ""
                thread = r.get("thread_id", "?")
                raw_date = r.get("created_at", "")
                date_str = raw_date[:10] if raw_date else "?"
                by = f"{role}/{agent}" if agent else role
                content = r.get("content", "")[:160].replace("\n", " ")
                if len(r.get("content", "")) > 160:
                    content += "…"
                lines.append(f"`{date_str}` **[{by}]** {content}\n> thread `{thread}`")

            await interaction.followup.send("\n".join(lines)[:2000])

        @tree.command(name="reload-skills", description="Manually trigger skill sync and validation")
        async def slash_reload_skills(interaction: discord.Interaction):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return

            if not self._skill_syncer:
                await interaction.response.send_message(
                    "Skill syncer not configured (enable skills in config.yaml).",
                    ephemeral=True,
                )
                return

            await interaction.response.defer()
            try:
                forward, reverse = self._skill_syncer.full_sync(
                    extra_source_dirs=self._workspace_skills_dirs
                )
                self._skill_syncer.refresh_workspace_dirs(self._workspace_skills_dirs)

                from oh_my_agent.skills.validator import SkillValidator
                validator = SkillValidator()
                skills_path = self._skill_syncer._skills_path

                validation_lines = []
                if skills_path.is_dir():
                    for skill_dir in sorted(skills_path.iterdir()):
                        if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").exists():
                            continue
                        result = validator.validate(skill_dir)
                        icon = "✅" if result.valid else "⚠️"
                        line = f"{icon} **{skill_dir.name}**"
                        if result.errors:
                            line += f" — {len(result.errors)} error(s)"
                        if result.warnings:
                            line += f" — {len(result.warnings)} warning(s)"
                        validation_lines.append(line)

                summary = [
                    f"**Skill reload complete** — {forward} synced, {reverse} reverse-imported",
                    "Active Claude/Gemini/Codex workspace skill directories refreshed.",
                ]
                if validation_lines:
                    summary.append("**Skills:**")
                    summary.extend(validation_lines)
                else:
                    summary.append("No skills found.")

                await interaction.followup.send("\n".join(summary)[:2000])
            except Exception as exc:
                logger.exception("Skill reload failed")
                await self._send_interaction_error(interaction, user_safe_message(exc))

        @tree.command(name="automation_status", description="Show automation status")
        @app_commands.describe(name="Optional automation name")
        async def slash_automation_status(
            interaction: discord.Interaction,
            name: str | None = None,
        ):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            result = await self._automation_service.get_status(name=name)
            await interaction.response.send_message(
                self._render_automation_status_result(result, name=name)[:1900],
                ephemeral=True,
            )

        @tree.command(name="automation_reload", description="Force an automation directory reload")
        async def slash_automation_reload(interaction: discord.Interaction):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._automation_service.reload()
            await interaction.followup.send(result.message[:1900], ephemeral=True)

        async def _set_automation_enabled(interaction: discord.Interaction, *, name: str, enabled: bool):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._automation_service.set_enabled(name.strip(), enabled=enabled)
            if not result.success:
                await interaction.followup.send(result.message[:1900], ephemeral=True)
                return
            await interaction.followup.send(
                self._render_automation_status_result(result, name=name)[:1900],
                ephemeral=True,
            )

        @tree.command(name="automation_enable", description="Enable an automation by name")
        @app_commands.describe(name="Automation name")
        async def slash_automation_enable(interaction: discord.Interaction, name: str):
            await _set_automation_enabled(interaction, name=name, enabled=True)

        @tree.command(name="automation_disable", description="Disable an automation by name")
        @app_commands.describe(name="Automation name")
        async def slash_automation_disable(interaction: discord.Interaction, name: str):
            await _set_automation_enabled(interaction, name=name, enabled=False)

        @tree.command(name="automation_run", description="Manually fire an automation job now")
        @app_commands.describe(name="Automation name to fire")
        async def slash_automation_run(interaction: discord.Interaction, name: str):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer()
            result = await self._automation_service.fire(name)
            await interaction.followup.send(result.message[:1900])

        @tree.command(name="skill_stats", description="Show skill health and evaluation stats")
        @app_commands.describe(skill="Optional skill name")
        async def slash_skill_stats(
            interaction: discord.Interaction,
            skill: str | None = None,
        ):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if self._skill_eval_service is None:
                await interaction.response.send_message(
                    "Skill evaluation store is not configured.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._skill_eval_service.get_stats(skill)
            await interaction.followup.send(
                self._render_skill_stats_result(result), ephemeral=True
            )

        @tree.command(name="skill_enable", description="Re-enable an auto-disabled skill")
        @app_commands.describe(skill="Skill name")
        async def slash_skill_enable(interaction: discord.Interaction, skill: str):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if self._skill_eval_service is None:
                await interaction.response.send_message(
                    "Skill evaluation store is not configured.",
                    ephemeral=True,
                )
                return
            result = await self._skill_eval_service.enable(skill)
            await interaction.response.send_message(result.message[:1900], ephemeral=True)

        @tree.command(name="task_start", description="Create an autonomous runtime task")
        @app_commands.describe(
            goal="Task goal",
            agent="Preferred agent name (optional)",
            test_command="Test command to run each step (optional)",
            max_steps="Max task loop steps (optional)",
            max_minutes="Max task runtime in minutes (optional)",
        )
        async def slash_task_start(
            interaction: discord.Interaction,
            goal: str,
            agent: str | None = None,
            test_command: str | None = None,
            max_steps: int | None = None,
            max_minutes: int | None = None,
        ):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if not self._session or not self._registry:
                await interaction.response.send_message(
                    "Session/registry not ready.",
                    ephemeral=True,
                )
                return

            ch = interaction.channel
            if isinstance(ch, discord.Thread):
                if ch.parent_id != target_id:
                    await interaction.response.send_message(
                        "Use this command in the configured channel or its threads.",
                        ephemeral=True,
                    )
                    return
                thread_id = str(ch.id)
            elif interaction.channel_id == target_id:
                thread_id = str(interaction.channel_id)
            else:
                await interaction.response.send_message(
                    "Use this command in the configured channel.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            result = await self._task_service.create_task(
                session=self._session,
                registry=self._registry,
                thread_id=thread_id,
                goal=goal,
                actor_id=str(interaction.user.id),
                preferred_agent=agent,
                test_command=test_command,
                max_steps=max_steps,
                max_minutes=max_minutes,
            )
            await interaction.followup.send(
                result.message[:1900],
                ephemeral=True,
            )

        @tree.command(name="task_status", description="Show runtime task status")
        @app_commands.describe(task_id="Task ID")
        async def slash_task_status(interaction: discord.Interaction, task_id: str):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            result = await self._task_service.get_status(task_id)
            await interaction.response.send_message(
                self._render_task_action_result(result)[:1900],
                ephemeral=True,
            )

        @tree.command(name="auth_login", description="Start a QR login flow for a provider")
        @app_commands.describe(provider="Auth provider name")
        async def slash_auth_login(interaction: discord.Interaction, provider: str = "bilibili"):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if not self._runtime_service:
                await interaction.response.send_message(
                    "Runtime service is not enabled.",
                    ephemeral=True,
                )
                return
            thread_id = _interaction_thread_id(interaction)
            if thread_id is None:
                await interaction.response.send_message(
                    "Use this command in the configured channel or one of its threads.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._runtime_service.start_auth_login(
                platform=self.platform,
                channel_id=self._channel_id,
                thread_id=thread_id,
                provider=provider.strip().lower() or "bilibili",
                actor_id=str(interaction.user.id),
            )
            await interaction.followup.send(result[:1900], ephemeral=True)

        @tree.command(name="auth_status", description="Show auth credential and flow state")
        @app_commands.describe(provider="Auth provider name")
        async def slash_auth_status(interaction: discord.Interaction, provider: str | None = None):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if not self._runtime_service:
                await interaction.response.send_message(
                    "Runtime service is not enabled.",
                    ephemeral=True,
                )
                return
            text = await self._runtime_service.get_auth_status(
                provider=(provider or "bilibili").strip().lower(),
                actor_id=str(interaction.user.id),
            )
            await interaction.response.send_message(text[:1900], ephemeral=True)

        @tree.command(name="auth_clear", description="Clear auth credential and cancel active login flow")
        @app_commands.describe(provider="Auth provider name")
        async def slash_auth_clear(interaction: discord.Interaction, provider: str = "bilibili"):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if not self._runtime_service:
                await interaction.response.send_message(
                    "Runtime service is not enabled.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            text = await self._runtime_service.clear_auth(
                provider=provider.strip().lower() or "bilibili",
                actor_id=str(interaction.user.id),
            )
            await interaction.followup.send(text[:1900], ephemeral=True)

        @tree.command(name="task_list", description="List runtime tasks for this channel")
        @app_commands.describe(status="Optional status filter", limit="Max rows (default 10)")
        async def slash_task_list(
            interaction: discord.Interaction,
            status: str | None = None,
            limit: int = 10,
        ):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            result = await self._task_service.list_tasks(
                platform=self.platform,
                channel_id=self._channel_id,
                status=status,
                limit=limit,
            )
            await interaction.response.send_message(
                self._render_task_list_result(result)[:1900],
                ephemeral=True,
            )

        async def _slash_decide(
            interaction: discord.Interaction,
            *,
            action: str,
            task_id: str,
            suggestion: str | None = None,
            max_turns: int | None = None,
            timeout_seconds: int | None = None,
        ):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._task_service.decide(
                platform=self.platform,
                channel_id=self._channel_id,
                thread_id=str(interaction.channel_id),
                task_id=task_id,
                action=action,
                actor_id=str(interaction.user.id),
                suggestion=suggestion,
                max_turns=max_turns,
                timeout_seconds=timeout_seconds,
            )
            await interaction.followup.send(result.message[:1900], ephemeral=True)

        @tree.command(name="task_approve", description="Approve a runtime task draft")
        @app_commands.describe(task_id="Task ID")
        async def slash_task_approve(interaction: discord.Interaction, task_id: str):
            await _slash_decide(interaction, action="approve", task_id=task_id)

        @tree.command(name="task_reject", description="Reject a runtime task draft")
        @app_commands.describe(task_id="Task ID")
        async def slash_task_reject(interaction: discord.Interaction, task_id: str):
            await _slash_decide(interaction, action="reject", task_id=task_id)

        @tree.command(name="task_suggest", description="Suggest changes for a runtime task draft")
        @app_commands.describe(
            task_id="Task ID",
            suggestion="Suggested change",
            max_turns="Override --max-turns per agent call (outer max_steps unchanged; optional)",
            timeout_seconds="Override per-call agent timeout seconds (outer max_minutes unchanged; optional)",
        )
        async def slash_task_suggest(
            interaction: discord.Interaction,
            task_id: str,
            suggestion: str,
            max_turns: app_commands.Range[int, 1, 500] | None = None,
            timeout_seconds: app_commands.Range[int, 1, 86400] | None = None,
        ):
            await _slash_decide(
                interaction,
                action="suggest",
                task_id=task_id,
                suggestion=suggestion,
                max_turns=max_turns,
                timeout_seconds=timeout_seconds,
            )

        @tree.command(name="task_merge", description="Merge a completed runtime task into current branch")
        @app_commands.describe(task_id="Task ID")
        async def slash_task_merge(interaction: discord.Interaction, task_id: str):
            await _slash_decide(interaction, action="merge", task_id=task_id)

        @tree.command(name="task_discard", description="Discard a completed runtime task result")
        @app_commands.describe(task_id="Task ID")
        async def slash_task_discard(interaction: discord.Interaction, task_id: str):
            await _slash_decide(interaction, action="discard", task_id=task_id)

        @tree.command(name="task_replace", description="Discard a DRAFT and refire its automation cron")
        @app_commands.describe(task_id="Task ID")
        async def slash_task_replace(interaction: discord.Interaction, task_id: str):
            await _slash_decide(interaction, action="replace", task_id=task_id)

        @tree.command(name="task_changes", description="Show file changes for a runtime task")
        @app_commands.describe(task_id="Task ID")
        async def slash_task_changes(interaction: discord.Interaction, task_id: str):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._task_service.get_changes(task_id)
            await interaction.followup.send(result.message[:1900], ephemeral=True)

        @tree.command(name="task_logs", description="Show recent logs/events for a runtime task")
        @app_commands.describe(task_id="Task ID")
        async def slash_task_logs(interaction: discord.Interaction, task_id: str):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._task_service.get_logs(task_id)
            await interaction.followup.send(result.message[:1900], ephemeral=True)

        @tree.command(name="task_cleanup", description="Cleanup runtime task workspace(s)")
        @app_commands.describe(task_id="Optional task ID for immediate cleanup")
        async def slash_task_cleanup(
            interaction: discord.Interaction,
            task_id: str | None = None,
        ):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._task_service.cleanup(
                actor_id=str(interaction.user.id),
                task_id=task_id,
            )
            await interaction.followup.send(result.message[:1900], ephemeral=True)

        @tree.command(name="task_resume", description="Resume a blocked runtime task")
        @app_commands.describe(task_id="Task ID", instruction="Instruction to unblock and continue")
        async def slash_task_resume(
            interaction: discord.Interaction,
            task_id: str,
            instruction: str,
        ):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._task_service.resume(task_id, instruction, actor_id=str(interaction.user.id))
            await interaction.followup.send(result.message[:1900], ephemeral=True)

        @tree.command(name="task_stop", description="Stop a runtime task")
        @app_commands.describe(task_id="Task ID")
        async def slash_task_stop(interaction: discord.Interaction, task_id: str):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._task_service.stop(task_id, actor_id=str(interaction.user.id))
            await interaction.followup.send(result.message[:1900], ephemeral=True)

        @tree.command(name="doctor", description="Show a runtime/operator health snapshot")
        async def slash_doctor(interaction: discord.Interaction):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._doctor_service.build_report(
                platform=self.platform,
                channel_id=self._channel_id,
                scheduler=self._scheduler,
                gateway_info={
                    "bot_online": self._client.user is not None if self._client else False,
                    "channel_bound": self._channel_id,
                },
            )
            await interaction.followup.send(self._render_doctor_result(result)[:1900], ephemeral=True)

        # ---- Memory commands -----------------------------------------------

        @tree.command(name="memories", description="Show learned user memories")
        @app_commands.describe(category="Filter by category (preference, project_knowledge, workflow, fact)")
        async def slash_memories(interaction: discord.Interaction, category: str | None = None):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if self._memory_service is None:
                await interaction.response.send_message(
                    "Memory subsystem is not enabled.", ephemeral=True,
                )
                return
            await interaction.response.defer()
            result = self._memory_service.list_entries(category=category)
            await interaction.followup.send(self._render_memory_list_result(result))

        @tree.command(name="forget", description="Delete a specific memory by ID (marks superseded)")
        @app_commands.describe(memory_id="The memory ID to forget (shown in /memories)")
        async def slash_forget(interaction: discord.Interaction, memory_id: str):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if self._memory_service is None:
                await interaction.response.send_message(
                    "Memory subsystem is not enabled.", ephemeral=True,
                )
                return
            result = await self._memory_service.forget(memory_id)
            await interaction.response.send_message(result.message[:1900], ephemeral=True)

        @tree.command(name="memorize", description="Trigger the memory judge for the current thread")
        @app_commands.describe(
            summary="Optional explicit memory text (skips LLM judgment)",
            scope="Optional scope: global_user | workspace | skill | thread",
        )
        async def slash_memorize(
            interaction: discord.Interaction,
            summary: str | None = None,
            scope: str | None = None,
        ):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if self._memory_service is None:
                await interaction.response.send_message(
                    "Memory subsystem is not enabled.", ephemeral=True,
                )
                return
            channel = interaction.channel
            if channel is None:
                await interaction.response.send_message(
                    "Cannot resolve current thread.", ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._memory_service.memorize(
                platform="discord",
                channel_id=str(self._channel_id),
                thread_id=str(channel.id),
                explicit_summary=summary,
                explicit_scope=scope,
            )
            prefix = "✅ " if result.success else "❌ "
            await interaction.followup.send(prefix + result.message[:1900], ephemeral=True)

        @tree.command(name="reflect_yesterday", description="Run a memory reflection pass over yesterday's diary")
        async def slash_reflect_yesterday(interaction: discord.Interaction):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if self._diary_reflector is None or self._registry is None:
                await interaction.response.send_message(
                    "Diary reflector is not enabled.", ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._diary_reflector.reflect_yesterday(registry=self._registry)
            await interaction.followup.send(_render_reflection_result(result)[:1900], ephemeral=True)

        # ---- Usage ledger --------------------------------------------------

        @tree.command(name="usage_today", description="Show today's token usage totals for this channel")
        async def slash_usage_today(interaction: discord.Interaction):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if not self._memory_store or not hasattr(self._memory_store, "get_usage_summary"):
                await interaction.response.send_message(
                    "Usage ledger is not available.", ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            since = _start_of_today_utc()
            summary = await self._memory_store.get_usage_summary(
                since_ts=since,
                platform=self.platform,
                channel_id=str(self._channel_id),
            )
            await interaction.followup.send(
                _render_usage_summary(summary, title=f"Usage since {since[:10]} UTC (channel)")[:1900],
                ephemeral=True,
            )

        @tree.command(name="usage_thread", description="Show token usage for this thread (last 24h)")
        async def slash_usage_thread(interaction: discord.Interaction, hours: int = 24):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if not self._memory_store or not hasattr(self._memory_store, "get_usage_summary"):
                await interaction.response.send_message(
                    "Usage ledger is not available.", ephemeral=True,
                )
                return
            channel = interaction.channel
            if channel is None:
                await interaction.response.send_message(
                    "Cannot resolve current thread.", ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            window = max(1, min(int(hours), 24 * 30))
            since = _hours_ago_utc(window)
            summary = await self._memory_store.get_usage_summary(
                since_ts=since,
                platform=self.platform,
                channel_id=str(self._channel_id),
                thread_id=str(channel.id),
            )
            await interaction.followup.send(
                _render_usage_summary(summary, title=f"Usage last {window}h (thread)")[:1900],
                ephemeral=True,
            )

        # ---- Events --------------------------------------------------------

        @client.event
        async def on_ready() -> None:
            scope = await self._sync_command_tree(tree, target_id)
            await self._rehydrate_hitl_prompt_views(client)
            logger.info(
                "[discord] Online as %s, listening on channel %s, slash commands synced (%s)",
                client.user,
                self._channel_id,
                scope,
            )

        @client.event
        async def on_message(message: discord.Message) -> None:
            if message.author == client.user or message.author.bot:
                return

            # Build accepted channel set first — needed for both mention peek
            # (below) and the regular message routing further down. Pulled
            # ahead of the owner gate so a third-party @-mention of the owner
            # can still trigger an external push notification even though the
            # message itself is dropped before agent dispatch.
            ch = message.channel
            dump_ids = {int(cid) for cid in self._dump_channel_ids if cid.isdigit()}
            accepted_parent_ids = {target_id, *dump_ids}
            in_accepted_channel = (
                (isinstance(ch, discord.Thread) and ch.parent_id in accepted_parent_ids)
                or (ch.id in accepted_parent_ids)
            )
            if not in_accepted_channel:
                return

            # Mention peek: third-party @owner → external push notification.
            # Read-only — does NOT lift the owner gate; non-owner messages
            # still get dropped from the agent dispatch path below.
            if (
                self._push_dispatcher is not None
                and self._push_dispatcher.is_enabled("mention_owner")
                and self._owner_user_ids
                and str(message.author.id) not in self._owner_user_ids
            ):
                mentioned_owners = [
                    str(u.id)
                    for u in getattr(message, "mentions", []) or []
                    if str(u.id) in self._owner_user_ids
                ]
                if mentioned_owners:
                    cooldown_key = f"{ch.id}:{message.author.id}"
                    if self._mention_cooldown.should_fire(cooldown_key):
                        # ``clean_content`` renders raw ``<@123>`` mention
                        # syntax as @username — push body shows on the lock
                        # screen, so readability beats fidelity.
                        body_text = (
                            getattr(message, "clean_content", None)
                            or message.content
                            or "(no text)"
                        )
                        self._push_dispatcher.schedule(PushNotificationEvent(
                            kind="mention_owner",
                            title=f"{message.author.display_name} mentioned you",
                            body=body_text[:200],
                            group="mentions",
                            level=self._push_dispatcher.level_for("mention_owner"),
                            deep_link=getattr(message, "jump_url", None),
                        ))

            if self._owner_user_ids and str(message.author.id) not in self._owner_user_ids:
                return

            content = message.content.strip()

            # Download image attachments (non-image and oversized are skipped)
            downloaded: list[Attachment] = []
            if message.attachments:
                downloaded = await _download_discord_attachments(message.attachments)

            # Detect "@agentname" prefix for per-message agent selection.
            # e.g. "@gemini does this look right?" routes only to gemini.
            preferred_agent: str | None = None
            if content.startswith("@") and self._registry:
                first, _, rest = content[1:].partition(" ")
                if first and self._registry.get_agent(first):
                    preferred_agent = first
                    content = rest.strip()

            # Message in a thread whose parent is our target channel (or a dump channel)
            if isinstance(ch, discord.Thread) and ch.parent_id in accepted_parent_ids:
                source_channel_id = (
                    self._channel_id if ch.parent_id == target_id else str(ch.parent_id)
                )
                msg = IncomingMessage(
                    platform="discord",
                    channel_id=source_channel_id,
                    thread_id=str(ch.id),
                    author=str(message.author.display_name),
                    author_id=str(message.author.id),
                    content=content,
                    raw=message,
                    preferred_agent=preferred_agent,
                    attachments=downloaded,
                )
            # Message directly in our target channel (or a dump channel) → may spawn new thread
            elif ch.id in accepted_parent_ids:
                source_channel_id = (
                    self._channel_id if ch.id == target_id else str(ch.id)
                )
                reply_to_id: str | None = None
                ref = getattr(message, "reference", None)
                if ref is not None:
                    ref_msg_id = getattr(ref, "message_id", None)
                    if ref_msg_id is not None:
                        reply_to_id = str(ref_msg_id)
                msg = IncomingMessage(
                    platform="discord",
                    channel_id=source_channel_id,
                    thread_id=None,
                    author=str(message.author.display_name),
                    author_id=str(message.author.id),
                    content=content,
                    raw=message,
                    preferred_agent=preferred_agent,
                    attachments=downloaded,
                    reply_to_message_id=reply_to_id,
                )
            else:
                return

            # Allow image-only messages (no text content required)
            if not msg.content and not msg.attachments:
                return

            await _handler(msg)

        @client.event
        async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
            try:
                await _sync_skill_feedback_from_payload(payload)
            except Exception:
                logger.debug("Failed to sync skill feedback from reaction add", exc_info=True)

        @client.event
        async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent) -> None:
            try:
                await _sync_skill_feedback_from_payload(payload)
            except Exception:
                logger.debug("Failed to sync skill feedback from reaction remove", exc_info=True)

        @tree.error
        async def on_app_command_error(
            interaction: discord.Interaction,
            error: app_commands.AppCommandError,
        ) -> None:
            exc = getattr(error, "original", error)
            command_name = getattr(getattr(interaction, "command", None), "qualified_name", "unknown")
            logger.exception(
                "Discord app command failed command=%s user_id=%s channel_id=%s",
                command_name,
                getattr(getattr(interaction, "user", None), "id", None),
                getattr(interaction, "channel_id", None),
            )
            await self._send_interaction_error(interaction, user_safe_message(exc))

        await client.start(self._token)

    async def send_task_draft(
        self,
        *,
        thread_id: str,
        draft_text: str,
        task_id: str,
        nonce: str,
        actions: list[str],
    ) -> str | None:
        if not self._runtime_service:
            await self.send(thread_id, draft_text)
            return None
        prompt = self._build_task_interactive_prompt(
            draft_text=draft_text,
            task_id=task_id,
            nonce=nonce,
            actions=actions,
        )
        return await self.send_interactive(thread_id, prompt)

    def parse_decision_event(self, raw):
        if not isinstance(raw, str):
            return None
        if not raw.startswith("tdec:"):
            return None
        parts = raw.split(":")
        if len(parts) != 4:
            return None
        _, task_id, action, nonce = parts
        return {"task_id": task_id, "action": action, "nonce": nonce}

    async def signal_task_status(self, thread_id: str, message_id: str | None, emoji: str) -> None:
        if not message_id:
            return
        try:
            target = await self._resolve_channel(thread_id)
            msg = await target.fetch_message(int(message_id))
            await msg.add_reaction(emoji)
        except Exception:
            logger.debug("Failed to add reaction %s to %s", emoji, message_id, exc_info=True)

    async def create_thread(self, msg: IncomingMessage, name: str) -> str:
        original: discord.Message = msg.raw
        thread = await original.create_thread(
            name=name[:100],
            auto_archive_duration=THREAD_ARCHIVE_MINUTES,
        )
        return str(thread.id)

    async def create_followup_thread(
        self,
        anchor_message_id: str,
        name: str,
        *,
        parent_channel_id: str | None = None,
    ) -> str | None:
        client = self._client
        if client is None:
            return None
        effective_parent = parent_channel_id or self._channel_id
        try:
            parent = client.get_channel(int(effective_parent))
            if parent is None:
                parent = await client.fetch_channel(int(effective_parent))
            if not isinstance(parent, (discord.TextChannel, discord.Thread, discord.DMChannel)):
                return None
            anchor = await parent.fetch_message(int(anchor_message_id))
            thread = await anchor.create_thread(
                name=name[:100],
                auto_archive_duration=THREAD_ARCHIVE_MINUTES,
            )
            return str(thread.id)
        except Exception:
            logger.warning(
                "create_followup_thread failed anchor=%s channel=%s",
                anchor_message_id,
                effective_parent,
                exc_info=True,
            )
            return None

    async def send(self, thread_id: str, text: str) -> str | None:
        thread = await self._resolve_channel(thread_id)
        await self._acquire_outbound_slot()
        msg = await thread.send(text)
        return str(msg.id)

    def render_user_mention(self, user_id: str) -> str:
        return f"<@{user_id}>"

    async def send_dm(self, user_id: str, text: str) -> str | None:
        dm_channel_id = await self.ensure_dm_channel(user_id)
        return await self.send(dm_channel_id, text)

    async def send_attachment(
        self,
        thread_id: str,
        attachment: OutgoingAttachment,
    ) -> str | None:
        thread = await self._resolve_channel(thread_id)
        await self._acquire_outbound_slot()
        msg = await thread.send(
            content=attachment.caption,
            file=discord.File(attachment.local_path, filename=attachment.filename),
        )
        return str(msg.id)

    async def send_attachments(
        self,
        thread_id: str,
        attachments: list[OutgoingAttachment],
        *,
        text: str | None = None,
    ) -> list[str]:
        thread = await self._resolve_channel(thread_id)
        files = [
            discord.File(attachment.local_path, filename=attachment.filename)
            for attachment in attachments
        ]
        await self._acquire_outbound_slot()
        msg = await thread.send(content=text, files=files)
        return [str(msg.id)]

    async def stop(self) -> None:
        if self._client and not self._client.is_closed():
            await self._client.close()

    async def edit_message(
        self,
        thread_id: str,
        message_id: str,
        text: str,
    ) -> None:
        try:
            thread = await self._resolve_channel(thread_id)
            msg = await thread.fetch_message(int(message_id))
            await self._acquire_outbound_slot()
            await msg.edit(content=text)
        except Exception:
            logger.debug("edit_message failed thread=%s msg=%s", thread_id, message_id, exc_info=True)

    async def send_interactive(
        self,
        thread_id: str,
        prompt: InteractivePrompt,
    ) -> str | None:
        thread = await self._resolve_channel(thread_id)
        view = self._build_interactive_view(prompt)
        await self._acquire_outbound_slot()
        msg = await thread.send(content=prompt.text, view=view)
        return str(msg.id)

    async def update_interactive(
        self,
        thread_id: str,
        message_id: str,
        prompt: InteractivePrompt,
    ) -> None:
        try:
            thread = await self._resolve_channel(thread_id)
            msg = await thread.fetch_message(int(message_id))
            view = self._build_interactive_view(prompt)
            await self._acquire_outbound_slot()
            await msg.edit(content=prompt.text, view=view)
        except Exception:
            logger.debug("update_interactive failed thread=%s msg=%s", thread_id, message_id, exc_info=True)

    def _build_interactive_view(self, prompt: InteractivePrompt) -> discord.ui.View:
        """Convert a platform-neutral ``InteractivePrompt`` into a Discord View."""
        return _InteractiveView(self, prompt)

    async def upsert_status_message(
        self,
        thread_id: str,
        text: str,
        *,
        message_id: str | None = None,
    ) -> str | None:
        thread = await self._resolve_channel(thread_id)
        bot_user = self._require_client().user

        if message_id:
            try:
                existing = await thread.fetch_message(int(message_id))
                if existing.author == bot_user:
                    await self._acquire_outbound_slot()
                    await existing.edit(content=text)
                    return str(existing.id)
            except Exception:
                logger.debug("Failed to edit status message %s", message_id, exc_info=True)

        try:
            latest = None
            async for item in thread.history(limit=1):
                latest = item
            if (
                latest is not None
                and latest.author == bot_user
                and (latest.content or "").startswith(STATUS_MESSAGE_PREFIX)
            ):
                await self._acquire_outbound_slot()
                await latest.edit(content=text)
                return str(latest.id)
        except Exception:
            logger.debug("Failed to inspect latest thread message for status upsert", exc_info=True)

        await self._acquire_outbound_slot()
        msg = await thread.send(text)
        return str(msg.id)

    @asynccontextmanager
    async def typing(self, thread_id: str) -> AsyncIterator[None]:
        thread = await self._resolve_channel(thread_id)
        async with thread.typing():
            yield

    def _require_client(self) -> discord.Client:
        """Return the Discord client, asserting it has been started.

        All callers of this method run inside request/event handlers, which
        only fire after ``start()`` populated ``self._client``.
        """
        assert self._client is not None, "Discord client not initialized; start() must run first"
        return self._client

    async def _resolve_channel(self, thread_id: str):
        client = self._require_client()
        thread = client.get_channel(int(thread_id))
        if thread is None:
            thread = await client.fetch_channel(int(thread_id))
        return thread

    async def ensure_dm_channel(self, user_id: str) -> str:
        """Return a DM channel id for the target user, creating it if needed."""
        client = self._require_client()
        uid = int(user_id)
        user = client.get_user(uid)
        if user is None:
            user = await client.fetch_user(uid)
        dm = user.dm_channel
        if dm is None:
            dm = await user.create_dm()
        return str(dm.id)

    async def _sync_command_tree(
        self,
        tree: app_commands.CommandTree,
        target_id: int,
    ) -> str:
        guild_id = await self._resolve_target_guild_id(target_id)
        if guild_id is not None:
            guild = discord.Object(id=guild_id)
            tree.copy_global_to(guild=guild)
            tree.clear_commands(guild=None)
            await tree.sync()
            await tree.sync(guild=guild)
            return f"guild:{guild_id}"

        await tree.sync()
        return "global"

    async def _resolve_target_guild_id(self, target_id: int) -> int | None:
        client = self._require_client()
        channel = client.get_channel(target_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(target_id)
            except Exception:
                logger.debug("Failed to fetch target channel %s for guild sync", target_id, exc_info=True)
                return None
        return self._extract_guild_id(channel)

    @staticmethod
    def _extract_guild_id(channel) -> int | None:
        guild = getattr(channel, "guild", None)
        if guild is not None and getattr(guild, "id", None):
            return int(guild.id)
        guild_id = getattr(channel, "guild_id", None)
        if guild_id:
            return int(guild_id)
        return None


async def clear_application_commands(token: str, channel_id: str) -> str:
    """Connect once, clear all slash commands for the application, then exit.

    Operates on whichever Discord application owns ``token``. Pair with the
    matching config (`--config dev-config.yaml --clear-commands`) to scope to
    dev vs prod. Resolves the configured channel to a guild and clears both
    the guild-scoped and global command sets so leftover stale registrations
    disappear regardless of where they were originally synced.

    Returns a short scope string (`"guild:<id>+global"` or `"global"`) for log
    visibility.
    """
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)
    scope_holder: dict[str, str] = {"scope": "global"}
    error_holder: dict[str, BaseException] = {}

    @client.event
    async def on_ready() -> None:
        try:
            target_id = int(channel_id)
            channel = client.get_channel(target_id)
            if channel is None:
                try:
                    channel = await client.fetch_channel(target_id)
                except Exception:
                    logger.debug(
                        "Could not fetch channel %s while clearing commands; "
                        "falling back to global-only clear",
                        target_id,
                        exc_info=True,
                    )
                    channel = None
            guild_id = DiscordChannel._extract_guild_id(channel) if channel is not None else None

            if guild_id is not None:
                guild = discord.Object(id=guild_id)
                tree.clear_commands(guild=guild)
                await tree.sync(guild=guild)
                logger.info("[discord] Cleared guild-scoped commands for guild %s", guild_id)
                scope_holder["scope"] = f"guild:{guild_id}+global"

            tree.clear_commands(guild=None)
            await tree.sync()
            logger.info("[discord] Cleared global application commands")
        except BaseException as exc:
            error_holder["error"] = exc
        finally:
            await client.close()

    try:
        await client.start(token)
    except discord.LoginFailure:
        raise
    finally:
        if not client.is_closed():
            await client.close()

    if "error" in error_holder:
        raise error_holder["error"]
    return scope_holder["scope"]
