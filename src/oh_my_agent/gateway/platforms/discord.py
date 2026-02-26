from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import discord

from oh_my_agent.gateway.base import BaseChannel, IncomingMessage, MessageHandler

logger = logging.getLogger(__name__)

THREAD_ARCHIVE_MINUTES = 60


class DiscordChannel(BaseChannel):
    """Discord platform adapter implementing BaseChannel."""

    def __init__(self, token: str, channel_id: str) -> None:
        self._token = token
        self._channel_id = channel_id
        self._client: discord.Client | None = None

    @property
    def platform(self) -> str:
        return "discord"

    @property
    def channel_id(self) -> str:
        return self._channel_id

    async def start(self, handler: MessageHandler) -> None:
        # Capture as a local alias BEFORE @client.event defines an inner
        # function also named `on_message` which would shadow this parameter.
        _handler = handler

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_ready() -> None:
            logger.info(
                "[discord] Online as %s, listening on channel %s",
                client.user,
                self._channel_id,
            )

        @client.event
        async def on_message(message: discord.Message) -> None:
            if message.author == client.user or message.author.bot:
                return

            ch = message.channel
            target_id = int(self._channel_id)

            # Message in a thread whose parent is our target channel
            if isinstance(ch, discord.Thread) and ch.parent_id == target_id:
                msg = IncomingMessage(
                    platform="discord",
                    channel_id=self._channel_id,
                    thread_id=str(ch.id),
                    author=str(message.author.display_name),
                    content=message.content.strip(),
                    raw=message,
                )
            # Message directly in our target channel → needs new thread
            elif ch.id == target_id:
                msg = IncomingMessage(
                    platform="discord",
                    channel_id=self._channel_id,
                    thread_id=None,
                    author=str(message.author.display_name),
                    content=message.content.strip(),
                    raw=message,
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
        thread = self._client.get_channel(int(thread_id))
        if thread is None:
            thread = await self._client.fetch_channel(int(thread_id))
        await thread.send(text)

    @asynccontextmanager
    async def typing(self, thread_id: str) -> AsyncIterator[None]:
        thread = self._client.get_channel(int(thread_id))
        if thread is None:
            thread = await self._client.fetch_channel(int(thread_id))
        async with thread.typing():
            yield
