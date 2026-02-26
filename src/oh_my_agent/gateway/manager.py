from __future__ import annotations

import asyncio
import logging

from oh_my_agent.gateway.base import BaseChannel, IncomingMessage
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.utils.chunker import chunk_message

logger = logging.getLogger(__name__)

THREAD_NAME_MAX = 90


class GatewayManager:
    """Manages multiple platform channels and routes messages to agent sessions."""

    def __init__(self, channels: list[tuple[BaseChannel, AgentRegistry]]) -> None:
        self._channels = channels
        # key: "platform:channel_id" → ChannelSession
        self._sessions: dict[str, ChannelSession] = {}

    def _session_key(self, platform: str, channel_id: str) -> str:
        return f"{platform}:{channel_id}"

    def _get_session(
        self, channel: BaseChannel, registry: AgentRegistry
    ) -> ChannelSession:
        key = self._session_key(channel.platform, channel.channel_id)
        if key not in self._sessions:
            self._sessions[key] = ChannelSession(
                platform=channel.platform,
                channel_id=channel.channel_id,
                channel=channel,
                registry=registry,
            )
        return self._sessions[key]

    async def start(self) -> None:
        """Start all platform channels concurrently."""
        tasks = []
        for channel, registry in self._channels:
            session = self._get_session(channel, registry)

            async def make_handler(s: ChannelSession, r: AgentRegistry):
                async def handler(msg: IncomingMessage) -> None:
                    await self.handle_message(s, r, msg)
                return handler

            handler = await make_handler(session, registry)
            tasks.append(asyncio.create_task(channel.start(handler)))
            logger.info(
                "Started channel %s:%s", channel.platform, channel.channel_id
            )

        await asyncio.gather(*tasks)

    async def handle_message(
        self,
        session: ChannelSession,
        registry: AgentRegistry,
        msg: IncomingMessage,
    ) -> None:
        channel = session.channel

        # Determine thread: use existing or create a new one
        thread_id = msg.thread_id
        if thread_id is None:
            name = self._thread_name(msg.content)
            thread_id = await channel.create_thread(msg, name)

        # Append user turn to history
        session.append_user(thread_id, msg.content, msg.author)
        history = session.get_history(thread_id)
        # Pass history minus the last user turn (the current message is the prompt)
        prior_history = history[:-1] if len(history) > 1 else []

        # Run agent (with fallback)
        async with channel.typing(thread_id):
            agent_used, response = await registry.run(msg.content, prior_history)

        if response.error:
            await channel.send(thread_id, f"**Error** ({agent_used.name}): {response.error[:1800]}")
            # Remove the failed user turn so history stays clean
            session.get_history(thread_id).pop()
            return

        # Record assistant response in history
        session.append_assistant(thread_id, response.text, agent_used.name)

        # Send with attribution header + chunked content
        attribution = f"-# via **{agent_used.name}**"
        chunks = chunk_message(response.text)
        if chunks:
            await channel.send(thread_id, f"{attribution}\n{chunks[0]}")
            for chunk in chunks[1:]:
                await channel.send(thread_id, chunk)
        else:
            await channel.send(thread_id, f"{attribution}\n*(empty response)*")

    @staticmethod
    def _thread_name(content: str) -> str:
        name = content[:THREAD_NAME_MAX].split("\n")[0]
        if len(content) > THREAD_NAME_MAX:
            name += "..."
        return name
