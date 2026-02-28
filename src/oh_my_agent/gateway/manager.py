from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from oh_my_agent.automation import ScheduledJob, Scheduler
from oh_my_agent.gateway.base import BaseChannel, IncomingMessage
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.runtime.policy import is_skill_intent
from oh_my_agent.utils.chunker import chunk_message

logger = logging.getLogger(__name__)

THREAD_NAME_MAX = 90
_EXPLICIT_SKILL_CALL_RE = re.compile(r"^/([a-zA-Z0-9][a-zA-Z0-9-_]{0,62})(?:\s|$)")


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
        runtime_service=None,
        short_workspace: dict | None = None,
        intent_router=None,
    ) -> None:
        self._channels = channels
        self._compressor = compressor
        self._scheduler = scheduler
        self._owner_user_ids = owner_user_ids or set()
        self._memory_store_ref = None  # set by set_memory_store()
        self._skill_syncer = skill_syncer
        self._workspace_skills_dirs = workspace_skills_dirs  # list[Path] | None
        self._runtime_service = runtime_service
        self._intent_router = intent_router
        short_cfg = short_workspace or {}
        self._short_workspace_enabled = bool(short_cfg.get("enabled", True))
        self._short_workspace_ttl_hours = int(short_cfg.get("ttl_hours", 24))
        self._short_workspace_cleanup_interval_minutes = int(
            short_cfg.get("cleanup_interval_minutes", 1440)
        )
        root_cfg = short_cfg.get("root")
        self._short_workspace_root = (
            Path(root_cfg).expanduser().resolve() if root_cfg else None
        )
        self._base_workspace = (
            Path(short_cfg["base_workspace"]).expanduser().resolve()
            if short_cfg.get("base_workspace")
            else None
        )
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

            # Inject runtime service for /task_* (Discord-specific)
            if hasattr(channel, "set_runtime_service") and self._runtime_service:
                channel.set_runtime_service(self._runtime_service)

            if self._runtime_service:
                self._runtime_service.register_session(session, registry)

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

        if self._runtime_service:
            await self._runtime_service.start()

        if self._short_workspace_enabled and self._short_workspace_root is not None:
            self._short_workspace_root.mkdir(parents=True, exist_ok=True)
            tasks.append(asyncio.create_task(self._run_short_workspace_janitor()))
            logger.info(
                "Short-workspace janitor enabled root=%s ttl=%sh interval=%sm",
                self._short_workspace_root,
                self._short_workspace_ttl_hours,
                self._short_workspace_cleanup_interval_minutes,
            )

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
        if self._runtime_service and self._runtime_service.enabled:
            await self._runtime_service.enqueue_scheduler_task(
                session=session,
                registry=session.registry,
                thread_id=thread_id,
                prompt=job.prompt,
                author=job.author,
                preferred_agent=job.agent,
            )
            return
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

        if self._runtime_service:
            handled = await self._runtime_service.maybe_handle_thread_context(
                session,
                msg,
                thread_id=thread_id,
            )
            if handled:
                logger.info("[%s] THREAD_CONTEXT handled thread=%s", req_id, thread_id)
                return

        explicit_skill = self._detect_explicit_skill_invocation(msg.content)
        if explicit_skill:
            logger.info(
                "[%s] SKILL_INVOKE explicit skill=%s preferred_agent=%r thread=%s",
                req_id,
                explicit_skill,
                msg.preferred_agent,
                thread_id,
            )

        # Runtime interception for long-running autonomous tasks.
        router_decision = None
        if (
            (not msg.system)
            and not explicit_skill
            and self._intent_router
            and self._runtime_service
        ):
            router_decision = await self._intent_router.route(msg.content)
            threshold = self._intent_router.confidence_threshold
            if router_decision is None:
                logger.info("[%s] ROUTER unavailable; fallback to heuristic/normal flow", req_id)
            else:
                logger.info(
                    "[%s] ROUTER decision=%s confidence=%.2f threshold=%.2f",
                    req_id,
                    router_decision.decision,
                    router_decision.confidence,
                    threshold,
                )
            if (
                router_decision
                and router_decision.decision == "create_skill"
                and router_decision.confidence >= threshold
            ):
                goal = router_decision.goal or msg.content
                await self._runtime_service.create_skill_task(
                    session=session,
                    registry=registry,
                    thread_id=thread_id,
                    goal=goal,
                    raw_request=msg.content,
                    created_by=msg.author_id or msg.author,
                    preferred_agent=msg.preferred_agent,
                    skill_name=router_decision.skill_name or goal,
                    source="router",
                )
                await channel.send(
                    thread_id,
                    (
                        "Router classified this as a skill-creation task and created a draft. "
                        "Approve to start autonomous execution, or reject/suggest to keep it in chat flow."
                    ),
                )
                logger.info(
                    "[%s] ROUTER create_skill confidence=%.2f goal=%r skill_name=%r",
                    req_id,
                    router_decision.confidence,
                    goal[:120],
                    router_decision.skill_name,
                )
                return
            if (
                router_decision
                and router_decision.decision == "propose_artifact_task"
                and router_decision.confidence >= threshold
            ):
                goal = router_decision.goal or msg.content
                await self._runtime_service.create_artifact_task(
                    session=session,
                    registry=registry,
                    thread_id=thread_id,
                    goal=goal,
                    raw_request=msg.content,
                    created_by=msg.author_id or msg.author,
                    preferred_agent=msg.preferred_agent,
                    source="router",
                    force_draft=True,
                )
                await channel.send(
                    thread_id,
                    (
                        "Router suggested this as an artifact task and created a draft. "
                        "Approve to start autonomous execution, or reject/suggest to keep it in chat flow."
                    ),
                )
                logger.info(
                    "[%s] ROUTER propose_artifact_task confidence=%.2f goal=%r",
                    req_id,
                    router_decision.confidence,
                    goal[:120],
                )
                return
            if (
                router_decision
                and router_decision.decision == "propose_repo_task"
                and router_decision.confidence >= threshold
            ):
                goal = router_decision.goal or msg.content
                await self._runtime_service.create_repo_change_task(
                    session=session,
                    registry=registry,
                    thread_id=thread_id,
                    goal=goal,
                    raw_request=msg.content,
                    created_by=msg.author_id or msg.author,
                    preferred_agent=msg.preferred_agent,
                    source="router",
                    force_draft=True,
                )
                await channel.send(
                    thread_id,
                    (
                        "Router suggested this as a repository-change task and created a draft. "
                        "Approve to start autonomous execution, or reject/suggest to keep it in chat flow."
                    ),
                )
                logger.info(
                    "[%s] ROUTER propose_repo_task confidence=%.2f goal=%r",
                    req_id,
                    router_decision.confidence,
                    goal[:120],
                )
                return
            if (
                router_decision
                and router_decision.decision == "invoke_existing_skill"
                and router_decision.confidence >= threshold
            ):
                logger.info(
                    "[%s] ROUTER invoke_existing_skill confidence=%.2f skill_name=%r",
                    req_id,
                    router_decision.confidence,
                    router_decision.skill_name,
                )

            should_try_heuristic = (
                router_decision is None
                or router_decision.confidence < threshold
            )
        else:
            should_try_heuristic = bool(self._runtime_service)

        if self._runtime_service and should_try_heuristic and not explicit_skill:
            if is_skill_intent(msg.content):
                await self._runtime_service.create_skill_task(
                    session=session,
                    registry=registry,
                    thread_id=thread_id,
                    goal=msg.content,
                    raw_request=msg.content,
                    created_by=msg.author_id or msg.author,
                    preferred_agent=msg.preferred_agent,
                    skill_name=msg.content,
                    source="heuristic",
                )
                await channel.send(
                    thread_id,
                    "Heuristic skill-intent detection created a draft. Approve to start autonomous execution.",
                )
                return
            handled = await self._runtime_service.maybe_handle_incoming(
                session,
                registry,
                msg,
                thread_id=thread_id,
            )
            if handled:
                return

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
        workspace_override = await self._resolve_short_workspace(session, thread_id)
        t_agent = time.perf_counter()
        async with channel.typing(thread_id):
            agent_used, response = await registry.run(
                msg.content,
                prior_history,
                thread_id=thread_id,
                force_agent=msg.preferred_agent,
                workspace_override=workspace_override,
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

            self._skill_syncer.refresh_workspace_dirs(self._workspace_skills_dirs)

            # Notify via the current thread
            lines = [f"🔧 **New skill(s) synced** ({len(new_skills)}):"]
            lines.extend(validation_lines)
            await session.channel.send(thread_id, "\n".join(lines)[:2000])

            if self._memory_store_ref and hasattr(self._memory_store_ref, "upsert_skill_provenance"):
                for skill_name in new_skills:
                    await self._memory_store_ref.upsert_skill_provenance(
                        skill_name,
                        source_task_id=None,
                        created_by="agent-side-effect",
                        agent_name="agent-side-effect",
                        platform=session.platform,
                        channel_id=session.channel_id,
                        thread_id=thread_id,
                        validation_mode="reverse_sync",
                        validated=0,
                    )

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

    async def _run_short_workspace_janitor(self) -> None:
        while True:
            try:
                cleaned = await self._cleanup_expired_short_workspaces()
                if cleaned:
                    logger.info("Short-workspace janitor removed %d expired workspace(s)", cleaned)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Short-workspace janitor failed: %s", exc)
            await asyncio.sleep(max(1, self._short_workspace_cleanup_interval_minutes) * 60)

    async def _resolve_short_workspace(
        self,
        session: ChannelSession,
        thread_id: str,
    ) -> Path | None:
        if not self._short_workspace_enabled or self._short_workspace_root is None:
            return None
        ws_key = self._short_workspace_key(session.platform, session.channel_id, thread_id)
        workspace = self._short_workspace_root / self._workspace_dirname(thread_id, ws_key)
        workspace.mkdir(parents=True, exist_ok=True)
        self._prepare_workspace_compat_files(workspace)
        store = getattr(self, "_memory_store_ref", None)
        if store and hasattr(store, "upsert_ephemeral_workspace"):
            await store.upsert_ephemeral_workspace(ws_key, str(workspace))
        return workspace

    async def _cleanup_expired_short_workspaces(self) -> int:
        if not self._short_workspace_enabled or self._short_workspace_root is None:
            return 0

        cleaned = 0
        store = getattr(self, "_memory_store_ref", None)
        if store and hasattr(store, "list_expired_ephemeral_workspaces"):
            rows = await store.list_expired_ephemeral_workspaces(
                ttl_hours=self._short_workspace_ttl_hours,
                limit=500,
            )
            for row in rows:
                path = Path(row.get("workspace_path", ""))
                if path.exists():
                    shutil.rmtree(path, ignore_errors=True)
                if hasattr(store, "mark_ephemeral_workspace_cleaned"):
                    await store.mark_ephemeral_workspace_cleaned(row["workspace_key"])
                cleaned += 1
            return cleaned

        # Fallback cleanup mode (no DB): scan by mtime.
        now = time.time()
        ttl_seconds = max(1, self._short_workspace_ttl_hours) * 3600
        for child in self._short_workspace_root.iterdir():
            if not child.is_dir():
                continue
            age = now - child.stat().st_mtime
            if age < ttl_seconds:
                continue
            shutil.rmtree(child, ignore_errors=True)
            cleaned += 1
        return cleaned

    def _prepare_workspace_compat_files(self, workspace: Path) -> None:
        if self._base_workspace is None:
            return
        for name in ("AGENTS.md", "AGENT.md", "CLAUDE.md", "GEMINI.md", ".claude", ".gemini", ".codex"):
            src = self._base_workspace / name
            dst = workspace / name
            if not src.exists() or dst.exists():
                continue
            try:
                os.symlink(src, dst, target_is_directory=src.is_dir())
            except OSError:
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

    @staticmethod
    def _short_workspace_key(platform: str, channel_id: str, thread_id: str) -> str:
        return f"{platform}:{channel_id}:{thread_id}"

    @staticmethod
    def _workspace_dirname(thread_id: str, workspace_key: str) -> str:
        digest = hashlib.sha1(workspace_key.encode("utf-8")).hexdigest()[:10]
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in thread_id)
        return f"{safe[:32]}-{digest}"

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

    def _detect_explicit_skill_invocation(self, content: str) -> str | None:
        match = _EXPLICIT_SKILL_CALL_RE.match(content.strip())
        if not match:
            return None

        skill_name = match.group(1)
        known = self._known_skill_names()
        if skill_name not in known:
            return None
        return skill_name

    def _known_skill_names(self) -> set[str]:
        if not self._skill_syncer:
            return set()
        skills_path = getattr(self._skill_syncer, "_skills_path", None)
        if not isinstance(skills_path, Path) or not skills_path.is_dir():
            return set()
        return {
            child.name
            for child in skills_path.iterdir()
            if child.is_dir() and (child / "SKILL.md").exists()
        }

    def resolve_session(self, platform: str, channel_id: str) -> ChannelSession | None:
        return self._sessions.get(self._session_key(platform, channel_id))

    def resolve_channel(self, platform: str, channel_id: str) -> BaseChannel | None:
        session = self.resolve_session(platform, channel_id)
        return session.channel if session else None
