from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from oh_my_agent.agents.base import BaseAgent
from oh_my_agent.utils.chunker import chunk_message

if TYPE_CHECKING:
    from oh_my_agent.config import Config

logger = logging.getLogger(__name__)

THREAD_ARCHIVE_MINUTES = 60

# Sentinel: indicates a new thread should be created from the message.
_CREATE_THREAD = object()


class AgentBot(discord.Client):
    """Discord client that routes channel messages to a CLI agent."""

    def __init__(self, config: Config, agent: BaseAgent) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

        self._agent = agent
        self._channel_id = config.discord_channel_id

    async def on_ready(self) -> None:
        logger.info("Bot is online as %s (id=%s)", self.user, self.user.id)
        logger.info("Listening on channel %s", self._channel_id)

    async def on_message(self, message: discord.Message) -> None:
        # Ignore own messages and other bots.
        if message.author == self.user or message.author.bot:
            return

        target = self._resolve_thread(message)
        if target is None:
            return

        prompt = message.content.strip()
        if not prompt:
            return

        logger.info("Message from %s: %s", message.author, prompt[:100])

        # Create a new thread if needed.
        if target is _CREATE_THREAD:
            target = await message.create_thread(
                name=self._thread_name(prompt),
                auto_archive_duration=THREAD_ARCHIVE_MINUTES,
            )

        # Show typing while the agent works.
        async with target.typing():
            response = await self._agent.run(prompt)

        if response.error:
            await target.send(f"**Error:** {response.error[:1900]}")
            return

        if not response.text.strip():
            await target.send("*(Agent returned an empty response.)*")
            return

        for chunk in chunk_message(response.text):
            await target.send(chunk)

    def _resolve_thread(
        self, message: discord.Message
    ) -> discord.Thread | object | None:
        """Return the thread to respond in, _CREATE_THREAD, or None to ignore."""
        channel = message.channel

        # Message is already in a thread whose parent is our target channel.
        if isinstance(channel, discord.Thread):
            if channel.parent_id == self._channel_id:
                return channel
            return None

        # Message is in the target channel itself — create a new thread.
        if channel.id == self._channel_id:
            return _CREATE_THREAD

        return None

    @staticmethod
    def _thread_name(prompt: str) -> str:
        name = prompt[:90].split("\n")[0]
        if len(prompt) > 90:
            name += "..."
        return name
