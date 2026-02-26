from __future__ import annotations

import asyncio
import logging
import time
import uuid

from oh_my_agent.gateway.base import BaseChannel, IncomingMessage
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.utils.chunker import chunk_message

logger = logging.getLogger(__name__)

THREAD_NAME_MAX = 90
# Minimum interval (seconds) between Discord message edits to respect rate limits
STREAM_EDIT_INTERVAL = 1.5
# Discord message size limit (leaving room for attribution header)
STREAM_MAX_LEN = 1900


class GatewayManager:
    """Manages multiple platform channels and routes messages to agent sessions."""

    def __init__(
        self,
        channels: list[tuple[BaseChannel, AgentRegistry]],
        compressor=None,
    ) -> None:
        self._channels = channels
        self._compressor = compressor
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

    def set_memory_store(self, store) -> None:
        """Inject a MemoryStore into all current and future sessions."""
        self._memory_store = store
        for session in self._sessions.values():
            session.memory_store = store

    async def start(self) -> None:
        """Start all platform channels concurrently."""
        tasks = []
        for channel, registry in self._channels:
            session = self._get_session(channel, registry)
            # Inject memory store if available
            if hasattr(self, "_memory_store"):
                session.memory_store = self._memory_store

            # Inject session context for slash commands (Discord-specific)
            if hasattr(channel, "set_session_context"):
                channel.set_session_context(
                    session,
                    registry,
                    getattr(self, "_memory_store", None),
                )

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
        req_id = uuid.uuid4().hex[:8]
        t_start = time.perf_counter()
        channel = session.channel

        logger.info(
            "[%s] MSG platform=%s channel=%s thread=%s author=%r content=%r",
            req_id,
            msg.platform,
            msg.channel_id,
            msg.thread_id or "(new)",
            msg.author,
            msg.content[:120],
        )

        # Determine thread: use existing or create a new one
        thread_id = msg.thread_id
        if thread_id is None:
            name = self._thread_name(msg.content)
            thread_id = await channel.create_thread(msg, name)
            logger.info("[%s] THREAD created thread_id=%s name=%r", req_id, thread_id, name)

        # Append user turn to history
        await session.append_user(thread_id, msg.content, msg.author)
        history = await session.get_history(thread_id)
        prior_history = history[:-1] if len(history) > 1 else []

        logger.info(
            "[%s] AGENT starting registry=%s history_turns=%d",
            req_id,
            [a.name for a in registry.agents],
            len(prior_history),
        )

        # Pick first agent that works; try streaming for the first agent
        first_agent = registry.agents[0]
        t_agent = time.perf_counter()

        if first_agent.supports_streaming:
            result_text, agent_used, error = await self._run_streaming(
                first_agent, channel, thread_id, msg.content, prior_history, req_id,
            )
            elapsed_agent = time.perf_counter() - t_agent

            if error:
                # Streaming failed — fall back to non-streaming registry
                logger.warning("[%s] Streaming failed for %s, falling back", req_id, first_agent.name)
                async with channel.typing(thread_id):
                    agent_used, response = await registry.run(msg.content, prior_history, thread_id=thread_id)
                elapsed_agent = time.perf_counter() - t_agent
                result_text = response.text
                error = response.error
            else:
                error = None
        else:
            async with channel.typing(thread_id):
                agent_used, response = await registry.run(msg.content, prior_history)
            elapsed_agent = time.perf_counter() - t_agent
            result_text = response.text
            error = response.error

        if error:
            logger.error(
                "[%s] AGENT_ERROR agent=%s elapsed=%.2fs error=%r",
                req_id,
                agent_used.name,
                elapsed_agent,
                error,
            )
            await channel.send(thread_id, f"**Error** ({agent_used.name}): {error[:1800]}")
            # Remove the failed user turn so history stays clean
            history = await session.get_history(thread_id)
            if history:
                history.pop()
            return

        logger.info(
            "[%s] AGENT_OK agent=%s elapsed=%.2fs response_len=%d",
            req_id,
            agent_used.name,
            elapsed_agent,
            len(result_text),
        )

        # Record assistant response in history
        await session.append_assistant(thread_id, result_text, agent_used.name)

        # If we already streamed the response, no need to send again
        if not first_agent.supports_streaming or error:
            attribution = f"-# via **{agent_used.name}**"
            chunks = chunk_message(result_text)
            if chunks:
                await channel.send(thread_id, f"{attribution}\n{chunks[0]}")
                for chunk in chunks[1:]:
                    await channel.send(thread_id, chunk)
            else:
                await channel.send(thread_id, f"{attribution}\n*(empty response)*")

        elapsed_total = time.perf_counter() - t_start
        logger.info(
            "[%s] DONE thread=%s chunks=%d total_elapsed=%.2fs",
            req_id,
            thread_id,
            max(len(chunks), 1),
            elapsed_total,
        )

        # Async: check compression (don't block the response)
        if self._compressor:
            asyncio.create_task(
                self._try_compress(session, registry, thread_id, req_id)
            )

    async def _run_streaming(
        self,
        agent,
        channel: BaseChannel,
        thread_id: str,
        prompt: str,
        history: list[dict] | None,
        req_id: str,
    ) -> tuple[str, object, str | None]:
        """Run an agent in streaming mode, editing a Discord message in-place.

        Returns ``(full_text, agent, error_or_None)``.
        """
        attribution = f"-# via **{agent.name}**\n"
        msg_id = await channel.send_message(thread_id, f"{attribution}*thinking...*")
        accumulated = ""
        last_edit = 0.0

        try:
            async for chunk in agent.run_stream(prompt, history):
                accumulated += chunk
                now = time.perf_counter()
                # Throttle edits to respect Discord rate limits
                if now - last_edit >= STREAM_EDIT_INTERVAL:
                    display = accumulated[:STREAM_MAX_LEN]
                    if len(accumulated) > STREAM_MAX_LEN:
                        display += "\n..."
                    await channel.edit_message(thread_id, msg_id, f"{attribution}{display}")
                    last_edit = now
        except Exception as exc:
            logger.error("[%s] Streaming error: %s", req_id, exc)
            return "", agent, str(exc)

        # Final edit with complete text
        if accumulated:
            final_chunks = chunk_message(accumulated)
            await channel.edit_message(
                thread_id, msg_id, f"{attribution}{final_chunks[0][:STREAM_MAX_LEN]}"
            )
            # Send remaining chunks as separate messages
            for extra in final_chunks[1:]:
                await channel.send(thread_id, extra)
        else:
            await channel.edit_message(thread_id, msg_id, f"{attribution}*(empty response)*")

        return accumulated, agent, None

    async def _try_compress(
        self,
        session: ChannelSession,
        registry: AgentRegistry,
        thread_id: str,
        req_id: str,
    ) -> None:
        try:
            did_compress = await self._compressor.maybe_compress(
                session.platform, session.channel_id, thread_id, registry,
            )
            if did_compress:
                # Invalidate cache so next load picks up the summary
                session._cache.pop(thread_id, None)
                logger.info("[%s] COMPRESS thread=%s completed", req_id, thread_id)
        except Exception as exc:
            logger.warning("[%s] COMPRESS failed: %s", req_id, exc)

    @staticmethod
    def _thread_name(content: str) -> str:
        name = content[:THREAD_NAME_MAX].split("\n")[0]
        if len(content) > THREAD_NAME_MAX:
            name += "..."
        return name
