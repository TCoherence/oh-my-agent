from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import discord
from discord import app_commands

from oh_my_agent.gateway.base import BaseChannel, IncomingMessage, MessageHandler
from oh_my_agent.runtime.types import TaskDecisionEvent

logger = logging.getLogger(__name__)

THREAD_ARCHIVE_MINUTES = 60
STATUS_MESSAGE_PREFIX = "**Task Status**"


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
                    "Active Claude/Gemini workspace skills refreshed.",
                ]
                if validation_lines:
                    summary.append("**Skills:**")
                    summary.extend(validation_lines)
                else:
                    summary.append("No skills found.")

                await interaction.followup.send("\n".join(summary)[:2000])
            except Exception as exc:
                await interaction.followup.send(f"Skill reload failed: {exc}")

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
            result = await self._runtime_service.handle_decision_event(event)
            await interaction.response.send_message(result, ephemeral=True)

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
            text = await self._runtime_service.get_task_changes(task_id)
            await interaction.response.send_message(text[:1900], ephemeral=True)

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
            text = await self._runtime_service.get_task_logs(task_id)
            await interaction.response.send_message(text[:1900], ephemeral=True)

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
            result = await self._runtime_service.cleanup_tasks(
                actor_id=str(interaction.user.id),
                task_id=task_id,
            )
            await interaction.response.send_message(result, ephemeral=True)

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
            result = await self._runtime_service.resume_task(
                task_id,
                instruction,
                actor_id=str(interaction.user.id),
            )
            await interaction.response.send_message(result, ephemeral=True)

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
            result = await self._runtime_service.stop_task(task_id, actor_id=str(interaction.user.id))
            await interaction.response.send_message(result, ephemeral=True)

        # ---- Events --------------------------------------------------------

        @client.event
        async def on_ready() -> None:
            scope = await self._sync_command_tree(tree, target_id)
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
                )
            else:
                return

            if not msg.content:
                return

            await _handler(msg)

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
                await interaction.response.defer(ephemeral=True)
                result = await self._runtime_service.handle_decision_event(event)

                task_after = await self._runtime_service.get_task(action_task_id)
                for child in view.children:
                    child.disabled = True
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
                    logger.debug("Failed to update decision message for task %s", action_task_id, exc_info=True)

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
