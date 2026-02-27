from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import discord
from discord import app_commands

from oh_my_agent.gateway.base import BaseChannel, IncomingMessage, MessageHandler

logger = logging.getLogger(__name__)

THREAD_ARCHIVE_MINUTES = 60


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
            results = await self._memory_store.search(query, limit=min(limit, 20))
            if not results:
                await interaction.followup.send(f"No results for **{query}**.")
                return

            lines = [f"**Search results for** \"{query}\" ({len(results)} found):"]
            for r in results:
                role = r.get("role", "?")
                content = r.get("content", "")[:200]
                thread = r.get("thread_id", "?")
                lines.append(f"- [{role}] {content}... (thread: `{thread}`)")

            await interaction.followup.send("\n".join(lines)[:2000])

        # ---- Events --------------------------------------------------------

        @client.event
        async def on_ready() -> None:
            await tree.sync()
            logger.info(
                "[discord] Online as %s, listening on channel %s, slash commands synced",
                client.user,
                self._channel_id,
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

    async def create_thread(self, msg: IncomingMessage, name: str) -> str:
        original: discord.Message = msg.raw
        thread = await original.create_thread(
            name=name[:100],
            auto_archive_duration=THREAD_ARCHIVE_MINUTES,
        )
        return str(thread.id)

    async def send(self, thread_id: str, text: str) -> None:
        thread = await self._resolve_channel(thread_id)
        await thread.send(text)

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
