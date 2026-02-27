from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid

from oh_my_agent.automation import ScheduledJob, Scheduler
from oh_my_agent.gateway.base import BaseChannel, IncomingMessage
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.utils.chunker import chunk_message

logger = logging.getLogger(__name__)

THREAD_NAME_MAX = 90


class GatewayManager:
    """Manages multiple platform channels and routes messages to agent sessions."""

    def __init__(
        self,
        channels: list[tuple[BaseChannel, AgentRegistry]],
        compressor=None,
        scheduler: Scheduler | None = None,
        owner_user_ids: set[str] | None = None,
        skill_syncer=None,
        workspace_skills_dirs=None,
    ) -> None:
        self._channels = channels
        self._compressor = compressor
        self._scheduler = scheduler
        self._owner_user_ids = owner_user_ids or set()
        self._memory_store_ref = None  # set by set_memory_store()
        self._skill_syncer = skill_syncer
        self._workspace_skills_dirs = workspace_skills_dirs  # list[Path] | None
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
        self._memory_store_ref = store  # kept for session persistence
        for session in self._sessions.values():
            session.memory_store = store

    async def _sync_session(self, session, thread_id: str, agent) -> None:
        """Persist or delete an agent's CLI session ID in the memory store."""
        store = getattr(self, "_memory_store_ref", None)
        if not store or not hasattr(agent, "get_session_id"):
            return
        current = agent.get_session_id(thread_id)
        if current:
            await store.save_session(session.platform, session.channel_id, thread_id, agent.name, current)
        else:
            # Session was cleared (e.g. failed resume) — remove stale DB entry
            await store.delete_session(session.platform, session.channel_id, thread_id, agent.name)

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

            # Inject skill syncer for /reload-skills (Discord-specific)
            if hasattr(channel, "set_skill_syncer") and self._skill_syncer:
                channel.set_skill_syncer(self._skill_syncer, self._workspace_skills_dirs)

            async def make_handler(s: ChannelSession, r: AgentRegistry):
                async def handler(msg: IncomingMessage) -> None:
                    await self.handle_message(s, r, msg)
                return handler

            handler = await make_handler(session, registry)
            tasks.append(asyncio.create_task(channel.start(handler)))
            logger.info(
                "Started channel %s:%s", channel.platform, channel.channel_id
            )

        if self._scheduler:
            tasks.append(asyncio.create_task(self._run_scheduler()))
            logger.info("Scheduler started with %d job(s)", len(self._scheduler.jobs))

        await asyncio.gather(*tasks)

    async def _run_scheduler(self) -> None:
        if not self._scheduler:
            return
        await self._scheduler.run(self._dispatch_scheduled_job)

    async def _dispatch_scheduled_job(self, job: ScheduledJob) -> None:
        key = self._session_key(job.platform, job.channel_id)
        session = self._sessions.get(key)
        if session is None:
            logger.warning(
                "Scheduler job '%s' skipped: no active channel %s:%s",
                job.name,
                job.platform,
                job.channel_id,
            )
            return

        thread_id: str
        if job.delivery == "dm":
            if not job.target_user_id:
                logger.warning(
                    "Scheduler job '%s' skipped: delivery=dm requires target_user_id",
                    job.name,
                )
                return
            dm_resolver = getattr(session.channel, "ensure_dm_channel", None)
            if dm_resolver is None or not inspect.iscoroutinefunction(dm_resolver):
                logger.warning(
                    "Scheduler job '%s' skipped: channel %s does not support DM delivery",
                    job.name,
                    session.channel.platform,
                )
                return
            thread_id = await dm_resolver(job.target_user_id)
        else:
            # Scheduler jobs without explicit thread_id post to the parent
            # channel by using channel_id as the target "thread".
            thread_id = job.thread_id or job.channel_id

        msg = IncomingMessage(
            platform=job.platform,
            channel_id=job.channel_id,
            thread_id=thread_id,
            author=job.author,
            content=job.prompt,
            preferred_agent=job.agent,
            system=True,
        )
        await self.handle_message(session, session.registry, msg)

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

        if (not msg.system) and self._owner_user_ids:
            if msg.author_id is None or msg.author_id not in self._owner_user_ids:
                logger.warning(
                    "[%s] IGNORE unauthorized user author=%r author_id=%r",
                    req_id,
                    msg.author,
                    msg.author_id,
                )
                return

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

        # Pre-load persisted CLI session IDs so agents can resume after restart
        if self._memory_store_ref:
            for agent in registry.agents:
                if hasattr(agent, "set_session_id") and agent.get_session_id(thread_id) is None:
                    stored = await self._memory_store_ref.load_session(
                        session.platform, session.channel_id, thread_id, agent.name
                    )
                    if stored:
                        agent.set_session_id(thread_id, stored)
                        logger.debug("Restored session %s for %s thread %s", stored[:12], agent.name, thread_id)

        # Run agent (with fallback, or targeted if preferred_agent is set)
        t_agent = time.perf_counter()
        async with channel.typing(thread_id):
            agent_used, response = await registry.run(
                msg.content,
                prior_history,
                thread_id=thread_id,
                force_agent=msg.preferred_agent,
            )
        elapsed_agent = time.perf_counter() - t_agent

        if response.error:
            logger.error(
                "[%s] AGENT_ERROR agent=%s elapsed=%.2fs error=%r",
                req_id,
                agent_used.name,
                elapsed_agent,
                response.error,
            )
            # If the agent cleared its session (failed resume), remove stale DB entry
            await self._sync_session(session, thread_id, agent_used)
            await channel.send(thread_id, f"**Error** ({agent_used.name}): {response.error[:1800]}")
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
            len(response.text),
        )

        # Persist updated CLI session ID to DB (for resume after restart)
        await self._sync_session(session, thread_id, agent_used)

        # Record assistant response in history
        await session.append_assistant(thread_id, response.text, agent_used.name)

        # Send with attribution header + chunked content
        attribution = f"-# via **{agent_used.name}**"
        if response.usage:
            attribution += f" · {self._format_usage(response.usage)}"
        chunks = chunk_message(response.text)
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

        # Async: detect and hot-reload new skills created by agents
        if self._skill_syncer:
            asyncio.create_task(
                self._try_skill_sync(session, thread_id, req_id)
            )

    async def _try_skill_sync(
        self,
        session: ChannelSession,
        thread_id: str,
        req_id: str,
    ) -> None:
        """Detect new agent-created skills, sync them, validate, and notify via Discord."""
        try:
            new_skills = self._skill_syncer.find_new_skills(self._workspace_skills_dirs)
            if not new_skills:
                return

            logger.info("[%s] SKILL_SYNC new skills detected: %s", req_id, new_skills)
            forward, reverse = self._skill_syncer.full_sync(
                extra_source_dirs=self._workspace_skills_dirs
            )
            logger.info(
                "[%s] SKILL_SYNC complete forward=%d reverse=%d", req_id, forward, reverse
            )

            # Validate each new skill
            from oh_my_agent.skills.validator import SkillValidator
            validator = SkillValidator()
            skills_path = self._skill_syncer._skills_path

            validation_lines = []
            for skill_name in new_skills:
                skill_dir = skills_path / skill_name
                if not skill_dir.is_dir():
                    continue
                result = validator.validate(skill_dir)
                icon = "✅" if result.valid else "⚠️"
                line = f"{icon} **{skill_name}**"
                if result.errors:
                    line += f" — {len(result.errors)} error(s): {'; '.join(result.errors[:2])}"
                if result.warnings:
                    line += f" — {len(result.warnings)} warning(s)"
                validation_lines.append(line)

            # Copy validated skills into workspace CLI dirs
            if self._workspace_skills_dirs:
                import shutil
                for skill_name in new_skills:
                    skill_dir = skills_path / skill_name
                    if not skill_dir.is_dir():
                        continue
                    for ws_dir in self._workspace_skills_dirs:
                        dest = ws_dir / skill_name
                        if dest.exists():
                            shutil.rmtree(dest)
                        shutil.copytree(skill_dir, dest)
                        logger.debug(
                            "[%s] SKILL_SYNC copied '%s' to workspace %s",
                            req_id, skill_name, ws_dir,
                        )

            # Notify via the current thread
            lines = [f"🔧 **New skill(s) synced** ({len(new_skills)}):"]
            lines.extend(validation_lines)
            await session.channel.send(thread_id, "\n".join(lines)[:2000])

        except Exception as exc:
            logger.warning("[%s] SKILL_SYNC failed: %s", req_id, exc)

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

    @staticmethod
    def _format_usage(usage: dict) -> str:
        """Format token usage and cost into a compact string for Discord attribution.

        Example: "1,234 in / 567 out · $0.0042"
        """
        parts = []
        input_tok = usage.get("input_tokens", 0)
        output_tok = usage.get("output_tokens", 0)
        if input_tok or output_tok:
            parts.append(f"{input_tok:,} in / {output_tok:,} out")
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_write = usage.get("cache_creation_input_tokens", 0)
        if cache_read and cache_write:
            parts.append(f"cache {cache_read:,}r/{cache_write:,}w")
        elif cache_read:
            parts.append(f"cache {cache_read:,}r")
        elif cache_write:
            parts.append(f"cache {cache_write:,}w")
        cost = usage.get("cost_usd")
        if cost is not None:
            parts.append(f"${cost:.4f}")
        return " · ".join(parts)
