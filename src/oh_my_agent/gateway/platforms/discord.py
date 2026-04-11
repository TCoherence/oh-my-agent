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
from oh_my_agent.runtime.types import HitlPrompt, TaskDecisionEvent

logger = logging.getLogger(__name__)

THREAD_ARCHIVE_MINUTES = 60
STATUS_MESSAGE_PREFIX = "**Task Status**"
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
_ATTACHMENT_DIR = Path(tempfile.gettempdir()) / "oh-my-agent" / "attachments"


class _HitlPromptView(discord.ui.View):
    def __init__(self, channel_adapter: "DiscordChannel", prompt: HitlPrompt, *, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self._channel_adapter = channel_adapter
        self._prompt = prompt
        self._disabled = disabled

        for choice in prompt.choices:
            button = discord.ui.Button(
                label=str(choice.get("label") or "")[:80] or str(choice.get("id") or "")[:80] or "Choice",
                style=discord.ButtonStyle.primary,
                custom_id=f"hitl:{prompt.id}:choose:{choice.get('id')}",
                disabled=disabled,
                row=0,
            )

            async def _callback(
                interaction: discord.Interaction,
                *,
                action_prompt_id: str = prompt.id,
                action_choice_id: str = str(choice.get("id") or ""),
            ) -> None:
                await self._channel_adapter._handle_hitl_interaction(
                    interaction,
                    prompt_id=action_prompt_id,
                    choice_id=action_choice_id,
                    cancel=False,
                )

            button.callback = _callback
            self.add_item(button)

        cancel_button = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id=f"hitl:{prompt.id}:cancel",
            disabled=disabled,
            row=1,
        )

        async def _cancel_callback(
            interaction: discord.Interaction,
            *,
            action_prompt_id: str = prompt.id,
        ) -> None:
            await self._channel_adapter._handle_hitl_interaction(
                interaction,
                prompt_id=action_prompt_id,
                choice_id=None,
                cancel=True,
            )

        cancel_button.callback = _cancel_callback
        self.add_item(cancel_button)


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
        self._skill_eval_enabled = True
        self._skill_stats_recent_days = 7
        self._skill_feedback_emojis = {"👍", "👎"}

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

    def set_skill_syncer(self, syncer, workspace_skills_dirs=None) -> None:
        """Inject skill syncer for the ``/reload-skills`` slash command."""
        self._skill_syncer = syncer
        self._workspace_skills_dirs = workspace_skills_dirs

    def set_runtime_service(self, runtime_service) -> None:
        """Inject runtime service for /task_* commands and decision buttons."""
        self._runtime_service = runtime_service

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

    async def send_hitl_prompt(self, *, thread_id: str, prompt: HitlPrompt) -> str | None:
        thread = await self._resolve_channel(thread_id)
        msg = await thread.send(
            self._render_hitl_prompt_message(prompt),
            view=_HitlPromptView(self, prompt),
        )
        return str(msg.id)

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
            client.add_view(_HitlPromptView(self, prompt), message_id=message_id)
            restored += 1
        if restored:
            logger.info("[discord] Restored %d active HITL prompt view(s)", restored)

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
                await interaction.message.edit(
                    content=self._render_hitl_prompt_message(resolving_prompt),
                    view=_HitlPromptView(self, resolving_prompt, disabled=True),
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
                await interaction.message.edit(
                    content=self._render_hitl_prompt_message(prompt),
                    view=_HitlPromptView(self, prompt, disabled=disabled),
                )
            except Exception:
                logger.debug("Failed to update HITL prompt message %s", prompt_id, exc_info=True)

        if cancel or (prompt is not None and prompt.status == "failed"):
            await interaction.followup.send(result[:1900], ephemeral=True)

    def set_scheduler(self, scheduler) -> None:
        """Inject scheduler for /automation_* commands."""
        self._scheduler = scheduler

    def set_adaptive_memory_store(self, store) -> None:
        """Inject adaptive memory store for /memories and /forget commands."""
        self._adaptive_memory_store = store

    def set_skill_evaluation_config(self, cfg: dict | None) -> None:
        cfg = cfg or {}
        self._skill_eval_enabled = bool(cfg.get("enabled", True))
        self._skill_stats_recent_days = int(cfg.get("stats_recent_days", 7))
        emojis = cfg.get("feedback_emojis", ["👍", "👎"])
        self._skill_feedback_emojis = {str(e) for e in emojis if str(e)}

    def supports_buttons(self) -> bool:
        return True

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

        def _format_automation_schedule(record) -> str:
            if record.cron:
                return f"cron `{record.cron}`"
            return f"interval `{record.interval_seconds}s`"

        def _format_automation_target(record) -> str:
            if record.delivery == "dm":
                return f"dm user `{record.target_user_id or '?'}` via channel `{record.channel_id}`"
            if record.thread_id:
                return f"channel `{record.channel_id}` thread `{record.thread_id}`"
            return f"channel `{record.channel_id}`"

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

            # Validate agent name early so the user gets immediate feedback
            if agent and self._registry:
                if self._registry.get_agent(agent) is None:
                    names = [a.name for a in self._registry.agents]
                    await interaction.response.send_message(
                        f"Unknown agent `{agent}`. Available: {', '.join(f'`{n}`' for n in names)}",
                        ephemeral=True,
                    )
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

            if self._session:
                await self._session.clear_history(str(ch.id))

            await interaction.response.send_message("History cleared for this thread.")

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

            if not self._session:
                await interaction.response.send_message("No session available.", ephemeral=True)
                return

            history = await self._session.get_history(str(ch.id))
            if not history:
                await interaction.response.send_message(
                    "No history for this thread yet.", ephemeral=True
                )
                return

            lines = [f"**Thread history** — {len(history)} turns:"]
            for i, turn in enumerate(history, 1):
                role = turn.get("role", "?")
                label = turn.get("author") or turn.get("agent") or role
                content = turn.get("content", "")
                preview = content[:120] + ("…" if len(content) > 120 else "")
                lines.append(f"`{i}` **{label}** [{role}]: {preview}")

            await interaction.response.send_message(
                "\n".join(lines)[:2000], ephemeral=True
            )

        @tree.command(name="agent", description="Show available agents and their status")
        async def slash_agent(interaction: discord.Interaction):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This bot is currently restricted to the configured owner.",
                    ephemeral=True,
                )
                return

            if not self._registry:
                await interaction.response.send_message("No agents configured.", ephemeral=True)
                return

            lines = ["**Available agents** (in fallback order):"]
            for i, agent in enumerate(self._registry.agents, 1):
                lines.append(f"{i}. `{agent.name}`")

            await interaction.response.send_message("\n".join(lines))

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
                await interaction.followup.send(f"Skill reload failed: {exc}")

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
            if not self._scheduler:
                await interaction.response.send_message(
                    "Automation scheduler is not enabled.",
                    ephemeral=True,
                )
                return

            runtime_states: dict = {}
            if self._memory_store:
                try:
                    for s in await self._memory_store.list_automation_states():
                        runtime_states[s.name] = s
                except Exception:
                    pass

            if name:
                record = self._scheduler.get_automation(name.strip())
                if not record:
                    await interaction.response.send_message(
                        f"Automation `{name}` not found.",
                        ephemeral=True,
                    )
                    return
                state_label = "enabled" if record.enabled else "disabled"
                rs = runtime_states.get(record.name)
                lines = [
                    f"**Automation** `{record.name}`",
                    f"- State: `{state_label}`",
                    f"- Schedule: {_format_automation_schedule(record)}",
                    f"- Delivery: `{record.delivery}`",
                    f"- Target: {_format_automation_target(record)}",
                    f"- Agent: `{record.agent or 'fallback'}`",
                    f"- Author: `{record.author}`",
                    f"- Source: `{record.source_path}`",
                ]
                if rs:
                    lines.append("")
                    lines.append("**Runtime state**")
                    lines.append(f"- Last run: `{rs.last_run_at or '—'}`")
                    lines.append(f"- Last success: `{rs.last_success_at or '—'}`")
                    lines.append(f"- Next run: `{rs.next_run_at or '—'}`")
                    lines.append(f"- Last task: `{rs.last_task_id or '—'}`")
                    if rs.last_error:
                        lines.append(f"- Last error: {rs.last_error[:200]}")
                await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)
                return

            records = self._scheduler.list_automations()
            if not records:
                await interaction.response.send_message(
                    "No visible automations found. Invalid or conflicting files are log-only for now.",
                    ephemeral=True,
                )
                return

            enabled_records = [record for record in records if record.enabled]
            disabled_records = [record for record in records if not record.enabled]
            failed_names = [s.name for s in runtime_states.values() if s.last_error]
            lines = [
                f"**Automations** — {len(enabled_records)} enabled, {len(disabled_records)} disabled",
            ]
            if failed_names:
                lines.append(f"- Recent failures: `{len(failed_names)}` ({', '.join(f'`{n}`' for n in failed_names[:5])})")
            if enabled_records:
                lines.append("**Enabled**")
                for record in enabled_records[:12]:
                    rs = runtime_states.get(record.name)
                    suffix = ""
                    if rs and rs.last_error:
                        suffix = " ⚠️"
                    elif rs and rs.last_success_at:
                        suffix = " ✓"
                    lines.append(
                        f"- `{record.name}` · {_format_automation_schedule(record)} · {_format_automation_target(record)}{suffix}"
                    )
            if disabled_records:
                lines.append("**Disabled**")
                for record in disabled_records[:12]:
                    lines.append(
                        f"- `{record.name}` · {_format_automation_schedule(record)} · {_format_automation_target(record)}"
                    )
            if len(records) > 24:
                lines.append(f"_…and {len(records) - 24} more_")
            lines.append("_Invalid or conflicting automation files remain log-visible only._")
            await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

        @tree.command(name="automation_reload", description="Force an automation directory reload")
        async def slash_automation_reload(interaction: discord.Interaction):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if not self._scheduler:
                await interaction.response.send_message(
                    "Automation scheduler is not enabled.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            summary = await self._scheduler.reload_now()
            await interaction.followup.send(
                (
                    "**Automation reload complete**\n"
                    f"- Visible: {summary['visible']}\n"
                    f"- Active: {summary['active']}\n"
                    f"- Added: {summary['added']}\n"
                    f"- Updated: {summary['updated']}\n"
                    f"- Removed: {summary['removed']}\n"
                    "_Invalid or conflicting automation files remain log-visible only._"
                )[:1900],
                ephemeral=True,
            )

        async def _set_automation_enabled(interaction: discord.Interaction, *, name: str, enabled: bool):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            if not self._scheduler:
                await interaction.response.send_message(
                    "Automation scheduler is not enabled.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            try:
                record = await self._scheduler.set_automation_enabled(name.strip(), enabled=enabled)
            except ValueError as exc:
                await interaction.followup.send(str(exc)[:1900], ephemeral=True)
                return

            action = "enabled" if enabled else "disabled"
            await interaction.followup.send(
                (
                    f"Automation `{record.name}` {action}.\n"
                    f"- Schedule: {_format_automation_schedule(record)}\n"
                    f"- Target: {_format_automation_target(record)}\n"
                    f"- Source: `{record.source_path}`"
                )[:1900],
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
            if not self._runtime_service:
                await interaction.response.send_message(
                    "Runtime service is not enabled.",
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
            task = await self._runtime_service.create_repo_change_task(
                session=self._session,
                registry=self._registry,
                thread_id=thread_id,
                goal=goal,
                created_by=str(interaction.user.id),
                preferred_agent=agent,
                test_command=test_command,
                max_steps=max_steps,
                max_minutes=max_minutes,
                source="slash",
            )
            await interaction.followup.send(
                f"Created task `{task.id}` with status `{task.status}`.",
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
            if not self._runtime_service:
                await interaction.response.send_message(
                    "Runtime service is not enabled.",
                    ephemeral=True,
                )
                return
            task = await self._runtime_service.get_task(task_id)
            if not task:
                await interaction.response.send_message(f"Task `{task_id}` not found.", ephemeral=True)
                return
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
            await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

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
            if not self._runtime_service:
                await interaction.response.send_message(
                    "Runtime service is not enabled.",
                    ephemeral=True,
                )
                return
            tasks = await self._runtime_service.list_tasks(
                platform=self.platform,
                channel_id=self._channel_id,
                status=status,
                limit=max(1, min(limit, 30)),
            )
            if not tasks:
                await interaction.response.send_message("No runtime tasks found.", ephemeral=True)
                return
            lines = [f"**Runtime tasks** ({len(tasks)})"]
            for t in tasks:
                lines.append(
                    f"- `{t.id}` [{t.status}] `{t.task_type}`/{t.completion_mode} "
                    f"step {t.step_no}/{t.max_steps} · {t.goal[:80]}"
                )
            await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

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
            if not self._runtime_service:
                await interaction.response.send_message(
                    "Runtime service is not enabled.",
                    ephemeral=True,
                )
                return
            event = await self._runtime_service.build_slash_decision_event(
                platform=self.platform,
                channel_id=self._channel_id,
                thread_id=str(interaction.channel_id),
                task_id=task_id,
                action=action,
                actor_id=str(interaction.user.id),
                suggestion=suggestion,
            )
            if not event:
                await interaction.response.send_message(
                    "No active approval token found for this task.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._runtime_service.handle_decision_event(event)
            await interaction.followup.send(result[:1900], ephemeral=True)

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
            resolved_action = "suggest"
            if self._runtime_service:
                task = await self._runtime_service.get_task(task_id)
                if task and task.status in {"WAITING_MERGE", "APPLIED"}:
                    resolved_action = "request_changes"
            await _slash_decide(
                interaction,
                action=resolved_action,
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
            if not self._runtime_service:
                await interaction.response.send_message(
                    "Runtime service is not enabled.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            text = await self._runtime_service.get_task_changes(task_id)
            await interaction.followup.send(text[:1900], ephemeral=True)

        @tree.command(name="task_logs", description="Show recent logs/events for a runtime task")
        @app_commands.describe(task_id="Task ID")
        async def slash_task_logs(interaction: discord.Interaction, task_id: str):
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
            text = await self._runtime_service.get_task_logs(task_id)
            await interaction.followup.send(text[:1900], ephemeral=True)

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
            if not self._runtime_service:
                await interaction.response.send_message(
                    "Runtime service is not enabled.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._runtime_service.cleanup_tasks(
                actor_id=str(interaction.user.id),
                task_id=task_id,
            )
            await interaction.followup.send(result[:1900], ephemeral=True)

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
            if not self._runtime_service:
                await interaction.response.send_message(
                    "Runtime service is not enabled.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            result = await self._runtime_service.resume_task(
                task_id,
                instruction,
                actor_id=str(interaction.user.id),
            )
            await interaction.followup.send(result[:1900], ephemeral=True)

        @tree.command(name="task_stop", description="Stop a runtime task")
        @app_commands.describe(task_id="Task ID")
        async def slash_task_stop(interaction: discord.Interaction, task_id: str):
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
            result = await self._runtime_service.stop_task(task_id, actor_id=str(interaction.user.id))
            await interaction.followup.send(result[:1900], ephemeral=True)

        @tree.command(name="doctor", description="Show a runtime/operator health snapshot")
        async def slash_doctor(interaction: discord.Interaction):
            if self._owner_user_ids and str(interaction.user.id) not in self._owner_user_ids:
                await interaction.response.send_message(
                    "This command is restricted to the configured owner.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer(ephemeral=True)
            lines = [
                "**Gateway health**",
                f"- Bot online: `{self._client.user is not None if self._client else False}`",
                f"- Channel bound: `{self._channel_id}`",
            ]
            if self._runtime_service is None:
                lines.extend(["", "**Runtime health**", "- Enabled: `False`"])
            else:
                report = await self._runtime_service.build_doctor_report(
                    platform=self.platform,
                    channel_id=self._channel_id,
                    scheduler=self._scheduler,
                )
                lines.extend(["", report])
            await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)

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
                lines.append(
                    f"`{m.id}` {tier_tag} [{conf_bar}] **[{m.category}]** {m.summary}"
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

        target = await self._resolve_channel(thread_id)

        view = discord.ui.View(timeout=3600)
        action_meta = {
            "approve": ("Approve", discord.ButtonStyle.success),
            "reject": ("Reject", discord.ButtonStyle.danger),
            "suggest": ("Suggest", discord.ButtonStyle.secondary),
            "merge": ("Merge", discord.ButtonStyle.success),
            "discard": ("Discard", discord.ButtonStyle.danger),
            "request_changes": ("Request Changes", discord.ButtonStyle.secondary),
        }

        for action in actions:
            if action not in action_meta:
                continue
            label, style = action_meta[action]
            button = discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"tdec:{task_id}:{action}:{nonce}",
            )

            async def _callback(
                interaction: discord.Interaction,
                *,
                action_name: str = action,
                action_nonce: str = nonce,
                action_task_id: str = task_id,
                original_text: str = draft_text,
            ) -> None:
                if not self._runtime_service:
                    await interaction.response.send_message(
                        "Runtime service not configured.",
                        ephemeral=True,
                    )
                    return

                event = TaskDecisionEvent(
                    platform=self.platform,
                    channel_id=self._channel_id,
                    thread_id=str(interaction.channel_id),
                    task_id=action_task_id,
                    action=action_name,  # type: ignore[arg-type]
                    actor_id=str(interaction.user.id),
                    nonce=action_nonce,
                    source="button",
                )
                task_after = await self._runtime_service.get_task(action_task_id)
                for child in view.children:
                    child.disabled = True
                status = task_after.status if task_after else "PENDING"
                processing_content = (
                    f"{original_text}\n\n---\n"
                    f"Status: `{status}`\n"
                    f"Result: Processing `{action_name}`..."
                )[:1900]
                try:
                    await interaction.response.edit_message(content=processing_content, view=view)
                except Exception:
                    logger.debug(
                        "Failed to acknowledge decision message update for task %s",
                        action_task_id,
                        exc_info=True,
                    )
                    return

                result = await self._runtime_service.handle_decision_event(event)

                task_after = await self._runtime_service.get_task(action_task_id)
                status = task_after.status if task_after else "UNKNOWN"
                summary_bits = [f"Status: `{status}`"]
                if task_after and task_after.merge_commit_hash:
                    summary_bits.append(f"Commit: `{task_after.merge_commit_hash}`")
                updated_content = (
                    f"{original_text}\n\n---\n"
                    + "\n".join(summary_bits)
                    + f"\nResult: {result}"
                )[:1900]
                try:
                    await interaction.message.edit(content=updated_content, view=view)
                except Exception:
                    logger.debug("Failed to finalize decision message for task %s", action_task_id, exc_info=True)

                await interaction.followup.send(result, ephemeral=True)

            button.callback = _callback
            view.add_item(button)

        message = await target.send(draft_text, view=view)
        return str(message.id)

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
            await msg.edit(content=prompt.text, view=view)
        except Exception:
            logger.debug("update_interactive failed thread=%s msg=%s", thread_id, message_id, exc_info=True)

    @staticmethod
    def _build_interactive_view(prompt: InteractivePrompt) -> discord.ui.View:
        """Convert a platform-neutral ``InteractivePrompt`` into a Discord View."""
        _STYLE_MAP = {
            "primary": discord.ButtonStyle.primary,
            "secondary": discord.ButtonStyle.secondary,
            "danger": discord.ButtonStyle.danger,
            "success": discord.ButtonStyle.success,
        }
        view = discord.ui.View(timeout=None)
        for action in prompt.actions:
            custom_id_parts = ["interactive"]
            if prompt.entity_id:
                custom_id_parts.append(prompt.entity_id)
            custom_id_parts.append(action.id)
            button = discord.ui.Button(
                label=action.label[:80],
                style=_STYLE_MAP.get(action.style, discord.ButtonStyle.secondary),
                custom_id=":".join(custom_id_parts),
                disabled=action.disabled,
            )
            view.add_item(button)
        return view

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
                await latest.edit(content=text)
                return str(latest.id)
        except Exception:
            logger.debug("Failed to inspect latest thread message for status upsert", exc_info=True)

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
