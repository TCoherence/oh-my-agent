from __future__ import annotations

from dataclasses import replace
import logging
import tempfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

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
from oh_my_agent.gateway.services import AskService, AutomationService, DoctorService, TaskService
from oh_my_agent.gateway.services.types import (
    AutomationStatusResult,
    DoctorResult,
    InteractiveDecision,
    TaskActionResult,
    TaskListResult,
)
from oh_my_agent.runtime.types import HitlPrompt
from oh_my_agent.utils.errors import user_safe_message
from oh_my_agent.utils.rate_limiter import TokenBucketLimiter

logger = logging.getLogger(__name__)

THREAD_ARCHIVE_MINUTES = 60
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
            button = discord.ui.Button(
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

            button.callback = _callback
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


class DiscordChannel(BaseChannel):
    """Discord platform adapter implementing BaseChannel.

    Supports both regular messages and slash commands
    (``/ask``, ``/reset``, ``/agent``, ``/search``).
    """

    def __init__(self, token: str, channel_id: str, owner_user_ids: set[str] | None = None) -> None:
        self._token = token
        self._channel_id = channel_id
        self._owner_user_ids = owner_user_ids or set()
        self._client: discord.Client | None = None
        # Injected by GatewayManager after construction
        self._session = None  # ChannelSession
        self._registry = None  # AgentRegistry
        self._memory_store = None  # MemoryStore
        self._skill_syncer = None  # SkillSync
        self._workspace_skills_dirs = None  # list[Path] | None
        self._runtime_service = None  # RuntimeService
        self._adaptive_memory_store = None  # AdaptiveMemoryStore
        self._scheduler = None  # Scheduler
        self._ask_service = AskService()
        self._task_service: TaskService | None = None
        self._doctor_service: DoctorService | None = None
        self._automation_service: AutomationService | None = None
        self._skill_eval_enabled = True
        self._skill_stats_recent_days = 7
        self._skill_feedback_emojis = {"👍", "👎"}
        self._rate_limiter = TokenBucketLimiter(rate=5.0, burst=10)

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
                lines.append(
                    f"- `{record.name}` · {self._format_automation_schedule(record)} · {self._format_automation_target(record)}{suffix}"
                )
        if disabled_records:
            lines.append("**Disabled**")
            for record in disabled_records[:12]:
                lines.append(
                    f"- `{record.name}` · {self._format_automation_schedule(record)} · {self._format_automation_target(record)}"
                )
        if len(result.automations) > 24:
            lines.append(f"_…and {len(result.automations) - 24} more_")
        lines.append("_Invalid or conflicting automation files remain log-visible only._")
        return "\n".join(lines)[:1900]

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
        await interaction.response.defer(ephemeral=True)
        task = None
        if self._runtime_service is not None:
            task = await self._runtime_service.get_task(decision.entity_id)
        try:
            await self.update_interactive(
                str(interaction.channel_id),
                str(interaction.message.id) if interaction.message is not None else str(decision.message_id or ""),
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
        )
        try:
            await self.update_interactive(
                str(interaction.channel_id),
                str(interaction.message.id) if interaction.message is not None else str(decision.message_id or ""),
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

    def set_adaptive_memory_store(self, store) -> None:
        """Inject adaptive memory store for /memories and /forget commands."""
        self._adaptive_memory_store = store

    def _refresh_services(self) -> None:
        self._task_service = TaskService(self._runtime_service, self._memory_store)
        self._doctor_service = DoctorService(self._runtime_service)
        self._automation_service = AutomationService(self._scheduler, self._memory_store)

    def set_skill_evaluation_config(self, cfg: dict | None) -> None:
        cfg = cfg or {}
        self._skill_eval_enabled = bool(cfg.get("enabled", True))
        self._skill_stats_recent_days = int(cfg.get("stats_recent_days", 7))
        emojis = cfg.get("feedback_emojis", ["👍", "👎"])
        self._skill_feedback_emojis = {str(e) for e in emojis if str(e)}

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

        def _format_skill_health_row(row: dict) -> str:
            recent = int(row.get("recent_invocations") or 0)
            successes = int(row.get("recent_successes") or 0)
            rate = (successes / recent) if recent else 0.0
            avg_ms = float(row.get("recent_avg_latency_ms") or 0.0)
            badge = "disabled" if int(row.get("auto_disabled") or 0) else "enabled"
            return (
                f"- `{row['skill_name']}` [{badge}] "
                f"success {rate:.0%} · recent {recent} · avg {avg_ms/1000:.2f}s · feedback {int(row.get('net_feedback') or 0):+d}"
            )

        def _format_skill_eval_lines(evals: list[dict]) -> list[str]:
            lines: list[str] = []
            for item in evals:
                lines.append(
                    f"- `{item['evaluation_type']}` [{item['status']}] {item['summary']}"
                )
            return lines

        async def _sync_skill_feedback_from_payload(payload: discord.RawReactionActionEvent) -> None:
            if not self._skill_eval_enabled or not self._memory_store:
                return
            emoji = str(payload.emoji)
            if emoji not in self._skill_feedback_emojis:
                return
            if self._owner_user_ids and str(payload.user_id) not in self._owner_user_ids:
                return
            if hasattr(client, "user") and client.user and payload.user_id == client.user.id:
                return
            if not hasattr(self._memory_store, "get_skill_invocation_by_message"):
                return

            invocation = await self._memory_store.get_skill_invocation_by_message(str(payload.message_id))
            if not invocation:
                return

            channel_obj = client.get_channel(payload.channel_id)
            if channel_obj is None:
                channel_obj = await client.fetch_channel(payload.channel_id)
            message = await channel_obj.fetch_message(payload.message_id)

            active_score = None
            for reaction in message.reactions:
                reaction_emoji = str(reaction.emoji)
                if reaction_emoji not in self._skill_feedback_emojis:
                    continue
                users = [u async for u in reaction.users()]
                if any(u.id == payload.user_id for u in users):
                    active_score = 1 if reaction_emoji == "👍" else -1

            if active_score is None:
                await self._memory_store.delete_skill_feedback(
                    invocation_id=int(invocation["id"]),
                    actor_id=str(payload.user_id),
                )
                logger.info(
                    "[discord] SKILL_FEEDBACK_CLEAR skill=%s invocation=%s actor=%s message=%s",
                    invocation.get("skill_name"),
                    invocation.get("id"),
                    payload.user_id,
                    payload.message_id,
                )
                return

            await self._memory_store.upsert_skill_feedback(
                invocation_id=int(invocation["id"]),
                actor_id=str(payload.user_id),
                platform=self.platform,
                channel_id=self._channel_id,
                thread_id=str(payload.channel_id),
                score=active_score,
                source="reaction",
            )
            logger.info(
                "[discord] SKILL_FEEDBACK_RECORDED skill=%s invocation=%s actor=%s score=%+d emoji=%s message=%s",
                invocation.get("skill_name"),
                invocation.get("id"),
                payload.user_id,
                active_score,
                emoji,
                payload.message_id,
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

        @tree.command(name="ask", description="Ask the AI agent a question (creates a new thread)")
        @app_commands.describe(
            question="Your question for the AI agent",
            agent="Agent to use (e.g. claude, gemini, codex). Defaults to fallback order.",
        )
        async def slash_ask(
            interaction: discord.Interaction,
            question: str,
            agent: str | None = None,
        ):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This bot is currently restricted to the configured owner.",
                    ephemeral=True,
                )
                return

            if interaction.channel_id != target_id:
                await interaction.response.send_message(
                    "This command only works in the configured channel.",
                    ephemeral=True,
                )
                return

            validation_error = await self._ask_service.validate_ask_params(self._registry, agent)
            if validation_error:
                await interaction.response.send_message(validation_error, ephemeral=True)
                return

            await interaction.response.send_message(question)
            response_msg = await interaction.original_response()

            msg = IncomingMessage(
                platform="discord",
                channel_id=self._channel_id,
                thread_id=None,
                author=str(interaction.user.display_name),
                author_id=str(interaction.user.id),
                content=question,
                raw=response_msg,
                preferred_agent=agent,
            )
            await _handler(msg)

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
            if not self._memory_store or not hasattr(self._memory_store, "get_skill_stats"):
                await interaction.response.send_message(
                    "Skill evaluation store is not configured.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            rows = await self._memory_store.get_skill_stats(
                skill,
                recent_days=self._skill_stats_recent_days,
            )
            if not rows:
                label = f" `{skill}`" if skill else ""
                await interaction.followup.send(f"No skill stats found for{label}.", ephemeral=True)
                return

            if skill:
                row = rows[0]
                lines = [
                    f"**Skill** `{row['skill_name']}`",
                    f"- State: `{'auto-disabled' if int(row.get('auto_disabled') or 0) else 'enabled'}`",
                    f"- Total invocations: {int(row.get('total_invocations') or 0)}",
                    f"- Recent invocations ({self._skill_stats_recent_days}d): {int(row.get('recent_invocations') or 0)}",
                    f"- Recent success/error/timeout/cancelled: {int(row.get('recent_successes') or 0)}/{int(row.get('recent_errors') or 0)}/{int(row.get('recent_timeouts') or 0)}/{int(row.get('recent_cancelled') or 0)}",
                    f"- Avg latency: {float(row.get('recent_avg_latency_ms') or 0.0)/1000:.2f}s",
                    f"- Feedback: 👍 {int(row.get('thumbs_up') or 0)} / 👎 {int(row.get('thumbs_down') or 0)} / net {int(row.get('net_feedback') or 0):+d}",
                ]
                if row.get("last_invoked_at"):
                    lines.append(f"- Last invoked: {row['last_invoked_at']}")
                if row.get("merged_commit_hash"):
                    lines.append(f"- Last merged commit: `{row['merged_commit_hash']}`")
                if row.get("auto_disabled_reason"):
                    lines.append(f"- Auto-disabled reason: {row['auto_disabled_reason']}")
                if hasattr(self._memory_store, "get_latest_skill_evaluations"):
                    evals = await self._memory_store.get_latest_skill_evaluations(row["skill_name"])
                    if evals:
                        lines.append("**Latest evaluations**")
                        lines.extend(_format_skill_eval_lines(evals))
                await interaction.followup.send("\n".join(lines)[:2000], ephemeral=True)
                return

            lines = [f"**Skill stats** — last {self._skill_stats_recent_days} day(s)"]
            lines.extend(_format_skill_health_row(row) for row in rows[:15])
            if len(rows) > 15:
                lines.append(f"_…and {len(rows) - 15} more_")
            await interaction.followup.send("\n".join(lines)[:2000], ephemeral=True)

        @tree.command(name="skill_enable", description="Re-enable an auto-disabled skill")
        @app_commands.describe(skill="Skill name")
        async def slash_skill_enable(interaction: discord.Interaction, skill: str):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if not self._memory_store or not hasattr(self._memory_store, "get_skill_provenance"):
                await interaction.response.send_message(
                    "Skill evaluation store is not configured.",
                    ephemeral=True,
                )
                return
            row = await self._memory_store.get_skill_provenance(skill)
            if not row:
                await interaction.response.send_message(
                    f"Skill `{skill}` not found.",
                    ephemeral=True,
                )
                return
            if hasattr(self._memory_store, "set_skill_auto_disabled"):
                await self._memory_store.set_skill_auto_disabled(skill, disabled=False)
            await interaction.response.send_message(
                f"Skill `{skill}` re-enabled for automatic routing.",
                ephemeral=True,
            )

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
        @app_commands.describe(task_id="Task ID", suggestion="Suggested change")
        async def slash_task_suggest(
            interaction: discord.Interaction,
            task_id: str,
            suggestion: str,
        ):
            await _slash_decide(
                interaction,
                action="suggest",
                task_id=task_id,
                suggestion=suggestion,
            )

        @tree.command(name="task_merge", description="Merge a completed runtime task into current branch")
        @app_commands.describe(task_id="Task ID")
        async def slash_task_merge(interaction: discord.Interaction, task_id: str):
            await _slash_decide(interaction, action="merge", task_id=task_id)

        @tree.command(name="task_discard", description="Discard a completed runtime task result")
        @app_commands.describe(task_id="Task ID")
        async def slash_task_discard(interaction: discord.Interaction, task_id: str):
            await _slash_decide(interaction, action="discard", task_id=task_id)

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

        # ---- Adaptive Memory commands --------------------------------------

        @tree.command(name="memories", description="Show learned user memories")
        @app_commands.describe(category="Filter by category (preference, project_knowledge, workflow, fact)")
        async def slash_memories(interaction: discord.Interaction, category: str | None = None):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if not self._adaptive_memory_store:
                await interaction.response.send_message(
                    "Adaptive memory is not enabled.", ephemeral=True,
                )
                return

            await interaction.response.defer()
            all_memories = await self._adaptive_memory_store.list_all()
            if category:
                all_memories = [m for m in all_memories if m.category == category]

            if not all_memories:
                msg = "No memories stored."
                if category:
                    msg = f"No memories in category **{category}**."
                await interaction.followup.send(msg)
                return

            lines = [f"**Memories** — {len(all_memories)} total"]
            for m in all_memories[:20]:
                conf_bar = "█" * int(m.confidence * 5) + "░" * (5 - int(m.confidence * 5))
                tier_tag = "[C]" if getattr(m, "tier", "daily") == "curated" else "[D]"
                explicitness = getattr(m, "explicitness", "inferred")
                status = getattr(m, "status", "active")
                scope = getattr(m, "scope", "global_user")
                durability = getattr(m, "durability", "medium")
                observed_at = str(getattr(m, "last_observed_at", getattr(m, "created_at", "-")))[:10]
                lines.append(
                    f"`{m.id}` {tier_tag} [{conf_bar}] **[{m.category}]** [{explicitness}/{status}]"
                    f" [{scope}/{durability}]"
                    f" obs={m.observation_count} seen={observed_at} {m.summary}"
                )
            if len(all_memories) > 20:
                lines.append(f"_…and {len(all_memories) - 20} more_")

            await interaction.followup.send("\n".join(lines)[:2000])

        @tree.command(name="forget", description="Delete a specific memory by ID")
        @app_commands.describe(memory_id="The memory ID to delete (shown in /memories)")
        async def slash_forget(interaction: discord.Interaction, memory_id: str):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if not self._adaptive_memory_store:
                await interaction.response.send_message(
                    "Adaptive memory is not enabled.", ephemeral=True,
                )
                return

            deleted = await self._adaptive_memory_store.delete_memory(memory_id)
            if deleted:
                await interaction.response.send_message(
                    f"Memory `{memory_id}` deleted.", ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"Memory `{memory_id}` not found.", ephemeral=True,
                )

        @tree.command(name="promote", description="Promote a daily memory to curated (long-term)")
        @app_commands.describe(memory_id="The memory ID to promote (shown in /memories)")
        async def slash_promote(interaction: discord.Interaction, memory_id: str):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if not self._adaptive_memory_store:
                await interaction.response.send_message(
                    "Adaptive memory is not enabled.", ephemeral=True,
                )
                return
            if not hasattr(self._adaptive_memory_store, "promote_memory"):
                await interaction.response.send_message(
                    "Memory store does not support promotion.", ephemeral=True,
                )
                return

            promoted = await self._adaptive_memory_store.promote_memory(memory_id)
            if promoted:
                if (
                    getattr(self._adaptive_memory_store, "needs_synthesis", False)
                    and hasattr(self._adaptive_memory_store, "synthesize_memory_md")
                    and self._registry is not None
                ):
                    try:
                        await self._adaptive_memory_store.synthesize_memory_md(self._registry)
                        if hasattr(self._adaptive_memory_store, "clear_synthesis_flag"):
                            self._adaptive_memory_store.clear_synthesis_flag()
                    except Exception:
                        logger.warning("MEMORY.md synthesis after /promote failed", exc_info=True)
                await interaction.response.send_message(
                    f"Memory `{memory_id}` promoted to curated.", ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"Memory `{memory_id}` not found in daily memories.", ephemeral=True,
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
            if self._owner_user_ids and str(message.author.id) not in self._owner_user_ids:
                return

            ch = message.channel
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

            # Message in a thread whose parent is our target channel
            if isinstance(ch, discord.Thread) and ch.parent_id == target_id:
                msg = IncomingMessage(
                    platform="discord",
                    channel_id=self._channel_id,
                    thread_id=str(ch.id),
                    author=str(message.author.display_name),
                    author_id=str(message.author.id),
                    content=content,
                    raw=message,
                    preferred_agent=preferred_agent,
                    attachments=downloaded,
                )
            # Message directly in our target channel → needs new thread
            elif ch.id == target_id:
                msg = IncomingMessage(
                    platform="discord",
                    channel_id=self._channel_id,
                    thread_id=None,
                    author=str(message.author.display_name),
                    author_id=str(message.author.id),
                    content=content,
                    raw=message,
                    preferred_agent=preferred_agent,
                    attachments=downloaded,
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

        if message_id:
            try:
                existing = await thread.fetch_message(int(message_id))
                if existing.author == self._client.user:
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
                and latest.author == self._client.user
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

    async def _resolve_channel(self, thread_id: str):
        thread = self._client.get_channel(int(thread_id))
        if thread is None:
            thread = await self._client.fetch_channel(int(thread_id))
        return thread

    async def ensure_dm_channel(self, user_id: str) -> str:
        """Return a DM channel id for the target user, creating it if needed."""
        uid = int(user_id)
        user = self._client.get_user(uid)
        if user is None:
            user = await self._client.fetch_user(uid)
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
        channel = self._client.get_channel(target_id)
        if channel is None:
            try:
                channel = await self._client.fetch_channel(target_id)
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
