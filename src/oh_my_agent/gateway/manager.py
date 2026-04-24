from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
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
from oh_my_agent.control.protocol import (
    ProtocolError,
    extract_control_frame,
    parse_auth_challenge,
    parse_ask_user_challenge,
    parse_control_envelope,
    strip_control_frame_text,
)
from oh_my_agent.gateway.base import BaseChannel, IncomingMessage
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.gateway.stream_relay import StreamingRelay
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.skills.frontmatter import read_skill_frontmatter, resolve_skill_frontmatter, skill_execution_limits
from oh_my_agent.runtime.policy import is_artifact_intent, is_long_task_intent, is_skill_intent
from oh_my_agent.utils.chunker import chunk_message
from oh_my_agent.utils.errors import user_safe_agent_error, user_safe_message
from oh_my_agent.utils.usage import append_usage_audit, format_usage_audit, record_usage_from_response

logger = logging.getLogger(__name__)

THREAD_NAME_MAX = 90
_EXPLICIT_SKILL_CALL_RE = re.compile(r"^/([a-zA-Z0-9][a-zA-Z0-9-_]{0,62})(?:\s|$)")
AGENT_PROGRESS_LOG_INTERVAL_SECONDS = 20.0
_PARTIAL_EXCERPT_MAX_CHARS = 2000


@dataclass
class ResponseDelivery:
    first_message_id: str | None
    chunk_count: int

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
        repo_root: str | Path | None = None,
        intent_router=None,
        router_context_turns: int = 6,
        router_require_user_confirm: bool = True,
        router_autonomy_threshold: float = 0.90,
        skill_evaluation_config: dict | None = None,
        judge_store=None,
        judge=None,
        idle_tracker=None,
        memory_inject_limit: int = 12,
        memory_keyword_patterns: list[str] | None = None,
        streaming_config: dict | None = None,
    ) -> None:
        self._channels = channels
        self._compressor = compressor
        self._scheduler = scheduler
        self._owner_user_ids = owner_user_ids or set()
        self._memory_store_ref = None  # set by set_memory_store()
        self._diary_writer = None  # set by set_diary_writer()
        self._diary_reflector = None  # set by set_diary_reflector()
        self._skill_syncer = skill_syncer
        self._workspace_skills_dirs = workspace_skills_dirs  # list[Path] | None
        self._runtime_service = runtime_service
        self._repo_root = Path(repo_root).expanduser().resolve() if repo_root else Path.cwd()
        self._intent_router = intent_router
        self._router_context_turns = max(1, int(router_context_turns))
        self._router_require_user_confirm = bool(router_require_user_confirm)
        self._router_autonomy_threshold = max(0.0, min(1.0, float(router_autonomy_threshold)))
        eval_cfg = skill_evaluation_config or {}
        auto_disable_cfg = eval_cfg.get("auto_disable", {})
        self._skill_eval_enabled = bool(eval_cfg.get("enabled", True))
        self._skill_feedback_emojis = [str(e) for e in eval_cfg.get("feedback_emojis", ["👍", "👎"])]
        self._skill_auto_disable_enabled = bool(auto_disable_cfg.get("enabled", True))
        self._skill_auto_disable_window = int(auto_disable_cfg.get("rolling_window", 20))
        self._skill_auto_disable_min_invocations = int(auto_disable_cfg.get("min_invocations", 5))
        self._skill_auto_disable_threshold = float(auto_disable_cfg.get("failure_rate_threshold", 0.60))
        self._skill_stats_recent_days = int(eval_cfg.get("stats_recent_days", 7))
        self._judge_store = judge_store
        self._judge = judge
        self._idle_tracker = idle_tracker
        self._memory_inject_limit = max(1, int(memory_inject_limit))
        default_keywords = ["记一下", "记下", "记住这个", "记下来", "remember this", "memorize this"]
        self._memory_keyword_patterns = [
            kw.lower() for kw in (memory_keyword_patterns or default_keywords) if kw
        ]
        if self._idle_tracker is not None:
            self._idle_tracker._on_fire = self._idle_judge_fire  # type: ignore[attr-defined]
        stream_cfg = streaming_config or {}
        self._streaming_enabled = bool(stream_cfg.get("enabled", False))
        try:
            interval_ms = int(stream_cfg.get("min_edit_interval_ms", 1000))
        except (TypeError, ValueError):
            interval_ms = 1000
        self._streaming_min_edit_interval = max(0.0, interval_ms / 1000.0)
        try:
            placeholder = str(stream_cfg.get("placeholder", "⏳ *thinking…*"))
        except Exception:
            placeholder = "⏳ *thinking…*"
        self._streaming_placeholder = placeholder or "⏳ *thinking…*"
        self._session_index: dict[str, tuple[ChannelSession, AgentRegistry]] = {}
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
        self._recent_thread_skills: dict[tuple[str, str, str], str] = {}
        self._auto_disabled_skills: set[str] = set()
        # key: "platform:channel_id" → ChannelSession
        self._sessions: dict[str, ChannelSession] = {}
        self._agent_progress_log_interval_seconds = AGENT_PROGRESS_LOG_INTERVAL_SECONDS
        self._accepting = True
        self._stopping = False
        self._background_tasks: list[asyncio.Task] = []
        self._inflight_messages: set[asyncio.Task] = set()

    def _session_key(self, platform: str, channel_id: str) -> str:
        return f"{platform}:{channel_id}"

    def _dump_channels_for(self, platform: str, source_channel_id: str) -> list[str]:
        """Return dump channel ids registered on the scheduler for this
        platform, excluding the source channel itself. Dump channels share
        the source channel's bot/gateway connection (single token = single
        listener) — they are send-only aliases."""
        sched = self._scheduler
        if sched is None:
            return []
        configured: dict[str, object] = getattr(sched, "_dump_channels", {}) or {}
        seen: set[str] = set()
        result: list[str] = []
        for dump in configured.values():
            dump_platform = getattr(dump, "platform", None)
            dump_channel_id = getattr(dump, "channel_id", None)
            if dump_platform != platform or not dump_channel_id:
                continue
            if dump_channel_id == source_channel_id:
                continue
            if dump_channel_id in seen:
                continue
            seen.add(dump_channel_id)
            result.append(dump_channel_id)
        return result

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
                diary_writer=self._diary_writer,
            )
        return self._sessions[key]

    def set_memory_store(self, store) -> None:
        """Inject a MemoryStore into all current and future sessions."""
        self._memory_store = store
        self._memory_store_ref = store  # kept for session persistence
        for session in self._sessions.values():
            session.memory_store = store

    def set_diary_writer(self, diary_writer) -> None:
        """Inject a SessionDiaryWriter into all current and future sessions."""
        self._diary_writer = diary_writer
        for session in self._sessions.values():
            session.diary_writer = diary_writer

    def set_diary_reflector(self, reflector) -> None:
        """Inject a DiaryReflector so platform channels can expose /reflect_*."""
        self._diary_reflector = reflector
        for channel, _registry in self._channels:
            if hasattr(channel, "set_diary_reflector"):
                channel.set_diary_reflector(reflector)

    async def _refresh_auto_disabled_skills(self) -> None:
        store = getattr(self, "_memory_store_ref", None)
        if not store or not hasattr(store, "list_auto_disabled_skills"):
            self._auto_disabled_skills = set()
            return
        self._auto_disabled_skills = await store.list_auto_disabled_skills()

    def _is_skill_auto_disabled(self, skill_name: str | None) -> bool:
        return bool(skill_name and skill_name in self._auto_disabled_skills)

    @staticmethod
    def _skill_invocation_outcome(response) -> str:
        if not response.error:
            return "success"
        if response.error_kind == "timeout":
            return "timeout"
        if response.error_kind == "cancelled":
            return "cancelled"
        return "error"

    async def _record_skill_invocation(
        self,
        *,
        skill_name: str,
        route_source: str,
        req_id: str,
        session: ChannelSession,
        thread_id: str,
        msg: IncomingMessage,
        agent_name: str,
        response,
        latency_ms: int,
    ) -> int | None:
        store = getattr(self, "_memory_store_ref", None)
        if not store or not self._skill_eval_enabled or not hasattr(store, "record_skill_invocation"):
            return None
        usage = response.usage or {}
        invocation_id = await store.record_skill_invocation(
            skill_name=skill_name,
            agent_name=agent_name,
            platform=session.platform,
            channel_id=session.channel_id,
            thread_id=thread_id,
            user_id=msg.author_id,
            route_source=route_source,
            request_id=req_id,
            outcome=self._skill_invocation_outcome(response),
            error_kind=response.error_kind,
            error_text=response.error[:500] if response.error else None,
            latency_ms=latency_ms,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cache_read_input_tokens=usage.get("cache_read_input_tokens"),
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens"),
        )
        await self._recompute_skill_health(skill_name)
        return invocation_id

    async def _bind_skill_invocation_message(
        self,
        invocation_id: int | None,
        message_id: str | None,
    ) -> None:
        if not invocation_id or not message_id:
            return
        store = getattr(self, "_memory_store_ref", None)
        if not store or not hasattr(store, "set_skill_invocation_response_message"):
            return
        await store.set_skill_invocation_response_message(invocation_id, message_id)

    async def _recompute_skill_health(self, skill_name: str) -> None:
        store = getattr(self, "_memory_store_ref", None)
        if (
            not store
            or not self._skill_eval_enabled
            or not self._skill_auto_disable_enabled
            or not hasattr(store, "list_recent_skill_invocations")
            or not hasattr(store, "set_skill_auto_disabled")
        ):
            return

        rows = await store.list_recent_skill_invocations(skill_name, limit=self._skill_auto_disable_window)
        if len(rows) < self._skill_auto_disable_min_invocations:
            await store.set_skill_auto_disabled(skill_name, disabled=False)
            self._auto_disabled_skills.discard(skill_name)
            return

        failures = sum(1 for row in rows if row.get("outcome") in {"error", "timeout", "cancelled"})
        rate = failures / max(len(rows), 1)
        if rate >= self._skill_auto_disable_threshold:
            reason = f"failure_rate={rate:.2f} over last {len(rows)} invocations"
            await store.set_skill_auto_disabled(skill_name, disabled=True, reason=reason)
            self._auto_disabled_skills.add(skill_name)
            return

        await store.set_skill_auto_disabled(skill_name, disabled=False)
        self._auto_disabled_skills.discard(skill_name)

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

    async def _sync_registry_sessions(self, session, thread_id: str, registry: AgentRegistry) -> None:
        """Synchronize persisted session state for every resume-capable agent."""
        for agent in registry.agents:
            await self._sync_session(session, thread_id, agent)

    def _chat_agent_log_base_path(
        self,
        *,
        thread_id: str,
        request_id: str,
        purpose: str,
    ) -> Path | None:
        runtime = self._runtime_service
        builder = getattr(runtime, "chat_agent_log_base_path", None) if runtime is not None else None
        if not callable(builder):
            return None
        candidate = builder(
            thread_id=thread_id,
            request_id=request_id,
            purpose=purpose,
        )
        if candidate is None or not isinstance(candidate, (str, Path)):
            return None
        return Path(candidate)

    async def _run_registry_with_progress_logging(
        self,
        *,
        req_id: str,
        purpose: str,
        registry: AgentRegistry,
        prompt: str,
        history: list[dict] | None,
        thread_id: str,
        force_agent: str | None,
        workspace_override,
        log_path: Path | None,
        image_paths: list[Path] | None,
        timeout_override_seconds: int | None,
        max_turns_override: int | None,
        on_agent_run=None,
        on_partial=None,
    ):
        run_task = asyncio.create_task(
            registry.run(
                prompt,
                history,
                thread_id=thread_id,
                force_agent=force_agent,
                workspace_override=workspace_override,
                log_path=log_path,
                image_paths=image_paths,
                timeout_override_seconds=timeout_override_seconds,
                max_turns_override=max_turns_override,
                on_agent_run=on_agent_run,
                on_partial=on_partial,
            )
        )
        started_at = time.perf_counter()
        interval = self._agent_progress_log_interval_seconds
        try:
            while True:
                try:
                    return await asyncio.wait_for(asyncio.shield(run_task), timeout=interval)
                except asyncio.TimeoutError:
                    elapsed = time.perf_counter() - started_at
                    logger.info(
                        "[%s] AGENT_RUNNING purpose=%s elapsed=%.2fs",
                        req_id,
                        purpose,
                        elapsed,
                    )
        finally:
            if not run_task.done():
                run_task.cancel()
                with suppress(asyncio.CancelledError):
                    await run_task

    async def _maybe_handle_control_challenge(
        self,
        *,
        req_id: str,
        session: ChannelSession,
        registry: AgentRegistry,
        thread_id: str,
        msg: IncomingMessage,
        agent_used,
        response,
        agent_prompt: str,
        explicit_skill: str | None,
        routed_skill: str | None,
    ) -> bool:
        if extract_control_frame(response.text) is None:
            return False
        try:
            envelope = parse_control_envelope(response.text)
            auth_challenge = parse_auth_challenge(envelope)
            ask_user_challenge = parse_ask_user_challenge(envelope)
        except ProtocolError as exc:
            logger.warning("[%s] CONTROL_FRAME_PARSE_FAILED thread=%s error=%s", req_id, thread_id, exc)
            return False
        if auth_challenge is None and ask_user_challenge is None:
            await session.channel.send(
                thread_id,
                "The agent requested an unsupported interactive step. This challenge type is not implemented yet.",
            )
            return True
        if ask_user_challenge is not None:
            if not self._runtime_service or not hasattr(self._runtime_service, "mark_thread_ask_user_required"):
                await session.channel.send(
                    thread_id,
                    "The agent requested an interactive choice, but runtime HITL handling is unavailable.",
                )
                return True
            visible_text = strip_control_frame_text(response.text)
            if visible_text:
                await session.append_assistant(thread_id, visible_text, agent_used.name)
                await self._send_agent_response(
                    session.channel,
                    thread_id,
                    agent_name=agent_used.name,
                    text=visible_text,
                    usage=response.usage,
                )
            session_snapshot = None
            if hasattr(agent_used, "get_session_id"):
                session_snapshot = agent_used.get_session_id(thread_id)
            result = await self._runtime_service.mark_thread_ask_user_required(
                platform=session.platform,
                channel_id=session.channel_id,
                thread_id=thread_id,
                actor_id=msg.author_id or msg.author,
                agent_name=agent_used.name,
                question=ask_user_challenge.question,
                details=ask_user_challenge.details,
                choices=ask_user_challenge.choices,
                control_envelope_json=envelope.raw_json,
                session_id_snapshot=session_snapshot,
                resume_context={
                    "agent_prompt": agent_prompt,
                    "original_user_content": msg.content,
                    "skill_name": explicit_skill or routed_skill,
                    "preferred_agent": msg.preferred_agent,
                },
            )
            logger.info(
                "[%s] CONTROL_FRAME_ASK_USER agent=%s thread=%s question=%r choices=%d",
                req_id,
                agent_used.name,
                thread_id,
                ask_user_challenge.question,
                len(ask_user_challenge.choices),
            )
            if result != f"Thread `{thread_id}` is waiting for input.":
                await session.channel.send(thread_id, result)
            return True
        if not self._runtime_service or not hasattr(self._runtime_service, "mark_thread_auth_required"):
            await session.channel.send(
                thread_id,
                f"Authentication is required for `{auth_challenge.provider}`, but runtime auth handling is unavailable.",
            )
            return True

        visible_text = self._build_auth_pause_message(
            raw_text=response.text,
            provider=auth_challenge.provider,
            skill_name=explicit_skill or routed_skill,
            original_user_content=msg.content,
        )
        if visible_text:
            await session.append_assistant(thread_id, visible_text, agent_used.name)
            await self._send_agent_response(
                session.channel,
                thread_id,
                agent_name=agent_used.name,
                text=visible_text,
                usage=response.usage,
            )

        session_snapshot = None
        if hasattr(agent_used, "get_session_id"):
            session_snapshot = agent_used.get_session_id(thread_id)
        result = await self._runtime_service.mark_thread_auth_required(
            platform=session.platform,
            channel_id=session.channel_id,
            thread_id=thread_id,
            provider=auth_challenge.provider,
            reason=auth_challenge.reason,
            actor_id=msg.author_id or msg.author,
            agent_name=agent_used.name,
            control_envelope_json=envelope.raw_json,
            session_id_snapshot=session_snapshot,
            resume_context={
                "agent_prompt": agent_prompt,
                "original_user_content": msg.content,
                "skill_name": explicit_skill or routed_skill,
                "preferred_agent": msg.preferred_agent,
            },
        )
        logger.info(
            "[%s] CONTROL_FRAME_AUTH_REQUIRED provider=%s agent=%s thread=%s",
            req_id,
            auth_challenge.provider,
            agent_used.name,
            thread_id,
        )
        try:
            await session.channel.send(thread_id, result)
        except Exception:
            logger.warning(
                "[%s] CONTROL_FRAME_RESULT_SEND_FAILED thread=%s provider=%s",
                req_id,
                thread_id,
                auth_challenge.provider,
                exc_info=True,
            )
        return True

    @staticmethod
    def _build_auth_pause_message(
        *,
        raw_text: str,
        provider: str,
        skill_name: str | None,
        original_user_content: str | None,
    ) -> str:
        visible_text = strip_control_frame_text(raw_text)
        if visible_text:
            return visible_text

        wants_zh = bool(original_user_content and re.search(r"[\u4e00-\u9fff]", original_user_content))
        if wants_zh:
            if skill_name:
                return (
                    f"继续按 `{skill_name}` 流程提取内容时，发现继续执行前需要先完成 `{provider}` 登录。"
                    "我先暂停在这里，等你完成认证后自动继续。"
                )
            return (
                f"继续处理这条请求时，发现继续执行前需要先完成 `{provider}` 登录。"
                "我先暂停在这里，等你完成认证后自动继续。"
            )
        if skill_name:
            return (
                f"I continued via `{skill_name}`, but `{provider}` login is required before I can keep going. "
                "I am pausing here and will resume automatically after authentication."
            )
        return (
            f"I hit a `{provider}` login requirement while continuing this request. "
            "I am pausing here and will resume automatically after authentication."
        )

    async def start(self) -> None:
        """Start all platform channels concurrently."""
        await self._refresh_auto_disabled_skills()
        self._accepting = True
        self._stopping = False
        self._background_tasks = []
        for channel, registry in self._channels:
            session = self._get_session(channel, registry)
            self._session_index[self._session_key(channel.platform, channel.channel_id)] = (session, registry)
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

            # Inject judge store + manager reference for /memories, /forget, /memorize (Discord)
            if hasattr(channel, "set_judge_store") and self._judge_store is not None:
                channel.set_judge_store(self._judge_store, self)

            # Inject runtime service for /task_* (Discord-specific)
            if hasattr(channel, "set_runtime_service") and self._runtime_service:
                channel.set_runtime_service(self._runtime_service)

            if hasattr(channel, "set_scheduler") and self._scheduler:
                channel.set_scheduler(self._scheduler)

            if hasattr(channel, "set_skill_evaluation_config"):
                channel.set_skill_evaluation_config(
                    {
                        "enabled": self._skill_eval_enabled,
                        "stats_recent_days": self._skill_stats_recent_days,
                        "feedback_emojis": self._skill_feedback_emojis,
                    }
                )

            if self._runtime_service:
                self._runtime_service.register_session(session, registry)

            # Register dump-channel aliases so completion messages bound to a
            # separate channel_id reuse the same BaseChannel + session.
            dump_channels = self._dump_channels_for(channel.platform, channel.channel_id)
            for dump_channel_id in dump_channels:
                self._sessions[self._session_key(channel.platform, dump_channel_id)] = session
                self._session_index[
                    self._session_key(channel.platform, dump_channel_id)
                ] = (session, registry)
                if self._runtime_service:
                    self._runtime_service.register_session_alias(
                        session=session,
                        registry=registry,
                        platform=channel.platform,
                        channel_id=dump_channel_id,
                    )
                if hasattr(channel, "register_dump_channel"):
                    channel.register_dump_channel(dump_channel_id)
                logger.info(
                    "Dump channel alias registered platform=%s source=%s dump=%s",
                    channel.platform,
                    channel.channel_id,
                    dump_channel_id,
                )

            async def make_handler(s: ChannelSession, r: AgentRegistry):
                async def handler(msg: IncomingMessage) -> None:
                    await self.handle_message(s, r, msg)
                return handler

            handler = await make_handler(session, registry)
            self._background_tasks.append(
                asyncio.create_task(
                    channel.start(handler),
                    name=f"channel:{channel.platform}:{channel.channel_id}",
                )
            )
            logger.info(
                "Started channel %s:%s", channel.platform, channel.channel_id
            )

        if self._scheduler:
            self._background_tasks.append(
                asyncio.create_task(self._run_scheduler(), name="scheduler")
            )
            self._background_tasks.append(
                asyncio.create_task(
                    self._run_scheduler_supervisor(), name="scheduler-supervisor"
                )
            )
            logger.info("Scheduler started with %d job(s)", len(self._scheduler.jobs))

        if self._runtime_service:
            await self._runtime_service.start()

        if self._short_workspace_enabled and self._short_workspace_root is not None:
            self._short_workspace_root.mkdir(parents=True, exist_ok=True)
            self._background_tasks.append(
                asyncio.create_task(
                    self._run_short_workspace_janitor(),
                    name="short-workspace-janitor",
                )
            )
            logger.info(
                "Short-workspace janitor enabled root=%s ttl=%sh interval=%sm",
                self._short_workspace_root,
                self._short_workspace_ttl_hours,
                self._short_workspace_cleanup_interval_minutes,
            )

        if self._idle_tracker is not None:
            await self._idle_tracker.start()
            logger.info(
                "Memory idle tracker started idle_seconds=%.0f",
                self._idle_tracker.idle_seconds,
            )

        try:
            await asyncio.gather(*self._background_tasks)
        finally:
            self._background_tasks = []

    async def stop(self, *, timeout: float = 10.0) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._accepting = False
        logger.info("Gateway shutdown requested; draining in-flight work.")

        if self._scheduler is not None and hasattr(self._scheduler, "stop"):
            try:
                self._scheduler.stop()
            except Exception:
                logger.warning("Failed to signal scheduler stop", exc_info=True)

        if self._idle_tracker is not None:
            try:
                await self._idle_tracker.stop()
            except Exception:
                logger.warning("Failed to stop memory idle tracker", exc_info=True)

        current_task = asyncio.current_task()
        inflight = [
            task
            for task in self._inflight_messages
            if task is not current_task and not task.done()
        ]
        if inflight:
            logger.info("Waiting for %d in-flight message(s) to finish.", len(inflight))
            done, pending = await asyncio.wait(inflight, timeout=timeout)
            if pending:
                logger.warning("Gateway shutdown timed out with %d in-flight message(s) pending.", len(pending))

        for channel, _ in self._channels:
            try:
                await channel.stop()
            except Exception:
                logger.warning(
                    "Channel stop failed for %s:%s",
                    channel.platform,
                    channel.channel_id,
                    exc_info=True,
                )

        background = [
            task
            for task in self._background_tasks
            if task is not current_task and not task.done()
        ]
        if background:
            done, pending = await asyncio.wait(background, timeout=timeout)
            for task in pending:
                task.cancel()
            if pending:
                pending_names = [task.get_name() for task in pending]
                await asyncio.gather(*pending, return_exceptions=True)
                logger.warning(
                    "Gateway shutdown cancelled %d lingering background task(s): %s",
                    len(pending_names),
                    ", ".join(pending_names),
                )

    async def _notify_user_error(
        self,
        *,
        channel: BaseChannel,
        thread_id: str,
        text: str,
    ) -> None:
        try:
            await channel.send(thread_id, text)
        except Exception:
            logger.warning(
                "Failed to deliver user-facing error message thread=%s",
                thread_id,
                exc_info=True,
            )

    async def _run_scheduler(self) -> None:
        if not self._scheduler:
            return
        self._scheduler._on_reload = self._sync_automation_runtime_state
        await self._sync_automation_runtime_state()
        await self._scheduler.run(self._dispatch_scheduled_job)

    async def _run_scheduler_supervisor(
        self, *, interval_seconds: float = 60.0
    ) -> None:
        """Periodic watchdog: evaluate scheduler liveness and self-heal."""
        if not self._scheduler:
            return
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                if not self._scheduler:
                    return
                findings = self._scheduler.evaluate_job_health()
                for finding in findings:
                    if finding.scope == "job" and finding.name:
                        restarted = await self._scheduler.restart_job(
                            finding.name, reason=finding.reason
                        )
                        if restarted:
                            logger.warning(
                                "Supervisor restarted job=%s reason=%s",
                                finding.name,
                                finding.reason,
                            )
                    elif finding.scope == "reload":
                        restarted = await self._scheduler.restart_reload_loop(
                            reason=finding.reason
                        )
                        if restarted:
                            logger.warning(
                                "Supervisor restarted reload-loop reason=%s",
                                finding.reason,
                            )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler supervisor iteration failed")

    async def _sync_automation_runtime_state(self) -> None:
        """Persist next_run_at and enabled for all known automations."""
        store = getattr(self, "_memory_store_ref", None)
        if not store or not self._scheduler:
            return
        next_runs = self._scheduler.compute_all_next_run_at()
        for record in self._scheduler.list_automations():
            next_dt = next_runs.get(record.name)
            next_run_iso = next_dt.isoformat() if next_dt else None
            await store.upsert_automation_state(
                record.name,
                platform=record.platform,
                channel_id=record.channel_id,
                enabled=record.enabled,
                next_run_at=next_run_iso,
            )

    async def _refresh_automation_next_run_at(self, name: str) -> None:
        """Refresh persisted next_run_at for a single automation. Never raises."""
        store = getattr(self, "_memory_store_ref", None)
        if not store or not self._scheduler:
            return
        try:
            next_dt = self._scheduler.compute_job_next_run_at(name)
            next_run_iso = next_dt.isoformat() if next_dt else None
            record = self._scheduler.get_automation(name)
            if record is None:
                return
            await store.upsert_automation_state(
                name,
                platform=record.platform,
                channel_id=record.channel_id,
                next_run_at=next_run_iso,
            )
        except Exception:
            logger.warning("refresh_next_run_at failed name=%s", name, exc_info=True)

    async def fire_automation(self, name: str) -> str:
        """Manually fire a named automation job.  Returns a status message."""
        if not self._scheduler:
            return "Scheduler not configured."
        fired = await self._scheduler.fire_job_now(name)
        if fired:
            return f"Automation `{name}` fired."
        return f"Automation `{name}` not found or scheduler not running."

    async def _dispatch_scheduled_job(self, job: ScheduledJob) -> None:
        try:
            await self._dispatch_scheduled_job_body(job)
        finally:
            try:
                await self._refresh_automation_next_run_at(job.name)
            except Exception:
                logger.warning(
                    "refresh_next_run_at failed name=%s", job.name, exc_info=True
                )

    async def _dispatch_scheduled_job_body(self, job: ScheduledJob) -> None:
        store = getattr(self, "_memory_store_ref", None)
        key = self._session_key(job.platform, job.channel_id)
        session = self._sessions.get(key)
        if session is None:
            logger.warning(
                "Scheduler job '%s' skipped: no active channel %s:%s",
                job.name,
                job.platform,
                job.channel_id,
            )
            if store:
                await store.upsert_automation_state(
                    job.name,
                    platform=job.platform,
                    channel_id=job.channel_id,
                    last_error=f"no active channel {job.platform}:{job.channel_id}",
                )
            return

        thread_id: str
        if job.delivery == "dm":
            if not job.target_user_id:
                logger.warning(
                    "Scheduler job '%s' skipped: delivery=dm requires target_user_id",
                    job.name,
                )
                if store:
                    await store.upsert_automation_state(
                        job.name,
                        platform=job.platform,
                        channel_id=job.channel_id,
                        last_error="delivery=dm requires target_user_id",
                    )
                return
            dm_resolver = getattr(session.channel, "ensure_dm_channel", None)
            if dm_resolver is None or not inspect.iscoroutinefunction(dm_resolver):
                logger.warning(
                    "Scheduler job '%s' skipped: channel %s does not support DM delivery",
                    job.name,
                    session.channel.platform,
                )
                if store:
                    await store.upsert_automation_state(
                        job.name,
                        platform=job.platform,
                        channel_id=job.channel_id,
                        last_error=f"channel {session.channel.platform} does not support DM delivery",
                    )
                return
            thread_id = await dm_resolver(job.target_user_id)
        else:
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
        try:
            if self._runtime_service and self._runtime_service.enabled:
                task = await self._runtime_service.enqueue_scheduler_task(
                    session=session,
                    registry=session.registry,
                    thread_id=thread_id,
                    automation_name=job.name,
                    prompt=job.prompt,
                    author=job.author,
                    preferred_agent=job.agent,
                    skill_name=job.skill_name,
                    timeout_seconds=job.timeout_seconds,
                    max_turns=job.max_turns,
                    auto_approve=job.auto_approve,
                    notify_channel_id=job.notify_channel_id,
                )
                if task and store:
                    await store.upsert_automation_state(
                        job.name,
                        platform=job.platform,
                        channel_id=job.channel_id,
                        last_run_at="__NOW__",
                        last_task_id=task.id,
                    )
                return
            await self.handle_message(session, session.registry, msg)
            if store:
                await store.upsert_automation_state(
                    job.name,
                    platform=job.platform,
                    channel_id=job.channel_id,
                    last_run_at="__NOW__",
                )
        except Exception as exc:
            logger.exception("Scheduler job '%s' fire failed: %s", job.name, exc)
            if store:
                await store.upsert_automation_state(
                    job.name,
                    platform=job.platform,
                    channel_id=job.channel_id,
                    last_error=str(exc)[:1000],
                )

    async def handle_message(
        self,
        session: ChannelSession,
        registry: AgentRegistry,
        msg: IncomingMessage,
    ) -> None:
        if not self._accepting:
            logger.info(
                "Ignoring message during shutdown platform=%s channel=%s thread=%s",
                msg.platform,
                msg.channel_id,
                msg.thread_id or "(new)",
            )
            return

        current_task = asyncio.current_task()
        if current_task is not None:
            self._inflight_messages.add(current_task)
        try:
            await self._handle_message_impl(session, registry, msg)
        except Exception as exc:
            logger.exception(
                "handle_message failed platform=%s channel=%s thread=%s author=%r",
                msg.platform,
                msg.channel_id,
                msg.thread_id or "(new)",
                msg.author,
            )
            await self._notify_user_error(
                channel=session.channel,
                thread_id=msg.thread_id or msg.channel_id,
                text=user_safe_message(exc),
            )
        finally:
            if current_task is not None:
                self._inflight_messages.discard(current_task)

    async def _handle_message_impl(
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

        # Reply-to-automation-post → spawn a follow-up thread anchored on the
        # original bot message, and seed it with a system turn referencing the
        # automation's archived artifacts. TTL is enforced by the runtime
        # janitor, so we just trust whatever get_automation_post returns.
        if (
            msg.thread_id is None
            and msg.reply_to_message_id
            and not msg.system
            and self._memory_store_ref is not None
        ):
            post = None
            try:
                post = await self._memory_store_ref.get_automation_post(
                    msg.platform, msg.channel_id, msg.reply_to_message_id,
                )
            except Exception:
                logger.debug(
                    "[%s] get_automation_post failed reply_to=%s",
                    req_id,
                    msg.reply_to_message_id,
                    exc_info=True,
                )
            if post is not None:
                thread_name = f"follow-up · {post.automation_name}"[:90]
                # When the anchor message lives in a dump channel (different
                # from the channel the BaseChannel was constructed with),
                # create_followup_thread needs the parent channel id passed
                # explicitly so it can fetch the anchor from there.
                new_thread_id = await channel.create_followup_thread(
                    msg.reply_to_message_id,
                    thread_name,
                    parent_channel_id=msg.channel_id,
                )
                if new_thread_id:
                    logger.info(
                        "[%s] AUTOMATION_FOLLOWUP thread=%s automation=%s anchor_msg=%s",
                        req_id,
                        new_thread_id,
                        post.automation_name,
                        msg.reply_to_message_id,
                    )
                    msg.thread_id = new_thread_id
                    seed_lines = [
                        f"[Follow-up on automation '{post.automation_name}'.]",
                    ]
                    # ``post.artifact_paths`` records the absolute **published**
                    # artifact paths from the original run (populated from
                    # ``delivery.archived_paths`` — legacy name, semantic is
                    # "single durable location per artifact under reports_dir").
                    # These are stable (reports_dir is not auto-pruned), which
                    # is why we prefer them over the workspace scratch path.
                    if post.artifact_paths:
                        seed_lines.append(
                            "Artifacts from that run (read them as needed):"
                        )
                        for path in post.artifact_paths[:8]:
                            seed_lines.append(f"- {path}")
                        if len(post.artifact_paths) > 8:
                            seed_lines.append(
                                f"- ... and {len(post.artifact_paths) - 8} more"
                            )
                    else:
                        seed_lines.append("(No published artifact paths were recorded.)")
                    await session.append_assistant(
                        new_thread_id, "\n".join(seed_lines), "system",
                    )
                    try:
                        await self._memory_store_ref.set_automation_post_follow_up_thread(
                            platform=msg.platform,
                            channel_id=msg.channel_id,
                            message_id=msg.reply_to_message_id,
                            follow_up_thread_id=new_thread_id,
                        )
                    except Exception:
                        logger.debug(
                            "[%s] set_automation_post_follow_up_thread failed",
                            req_id,
                            exc_info=True,
                        )

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

        history = await session.get_history(thread_id)
        user_turn_appended = False
        await self._refresh_auto_disabled_skills()

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
            router_context = self._build_router_context(
                history,
                platform=session.platform,
                channel_id=session.channel_id,
                thread_id=thread_id,
            )
            router_decision = await self._intent_router.route(msg.content, context=router_context)
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
                and router_decision.decision == "repair_skill"
                and router_decision.confidence >= threshold
            ):
                await self._append_user_turn_if_needed(session, thread_id, msg)
                user_turn_appended = True
                repair_skill = router_decision.skill_name or self._recent_invoked_skill(history) or "skill"
                repair_request = self._build_skill_repair_request(repair_skill, history, msg.content)
                goal = router_decision.goal or f"Update existing skill '{repair_skill}' based on recent user feedback."
                is_borderline = (
                    self._router_require_user_confirm
                    and router_decision.confidence < self._router_autonomy_threshold
                )
                await self._runtime_service.create_skill_task(
                    session=session,
                    registry=registry,
                    thread_id=thread_id,
                    goal=goal,
                    raw_request=repair_request,
                    created_by=msg.author_id or msg.author,
                    preferred_agent=msg.preferred_agent,
                    skill_name=repair_skill,
                    source="repair_skill",
                    force_draft=True if is_borderline else None,
                )
                if is_borderline:
                    confirm_msg = (
                        f"Router thinks this is feedback on skill `{repair_skill}` "
                        f"(confidence {router_decision.confidence:.2f}), but not confident enough. "
                        "Created a draft — use `/task_approve` to let the agent update the skill, "
                        "or `/task_reject` to cancel and rephrase."
                    )
                else:
                    confirm_msg = (
                        f"Router classified this as feedback on skill `{repair_skill}` "
                        f"(confidence {router_decision.confidence:.2f}) and started the update. "
                        "Use `/task_stop` to stop at any time."
                    )
                await channel.send(thread_id, confirm_msg)
                logger.info(
                    "[%s] ROUTER repair_skill confidence=%.2f goal=%r skill_name=%r borderline=%s",
                    req_id,
                    router_decision.confidence,
                    goal[:120],
                    repair_skill,
                    is_borderline,
                )
                return
            if (
                router_decision
                and router_decision.decision == "create_skill"
                and router_decision.confidence >= threshold
            ):
                await self._append_user_turn_if_needed(session, thread_id, msg)
                user_turn_appended = True
                goal = router_decision.goal or msg.content
                is_borderline = (
                    self._router_require_user_confirm
                    and router_decision.confidence < self._router_autonomy_threshold
                )
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
                    force_draft=True if is_borderline else None,
                )
                if is_borderline:
                    confirm_msg = (
                        f"Router thinks this is a skill-creation task "
                        f"(confidence {router_decision.confidence:.2f}), but not confident enough. "
                        "Created a draft — use `/task_approve` to start autonomous execution, "
                        "or `/task_reject` to cancel and rephrase."
                    )
                else:
                    confirm_msg = (
                        f"Router classified this as a skill-creation task "
                        f"(confidence {router_decision.confidence:.2f}) and started execution. "
                        "Use `/task_stop` to stop at any time."
                    )
                await channel.send(thread_id, confirm_msg)
                logger.info(
                    "[%s] ROUTER create_skill confidence=%.2f goal=%r skill_name=%r borderline=%s",
                    req_id,
                    router_decision.confidence,
                    goal[:120],
                    router_decision.skill_name,
                    is_borderline,
                )
                return
            if (
                router_decision
                and router_decision.decision == "propose_artifact_task"
                and router_decision.confidence >= threshold
            ):
                await self._append_user_turn_if_needed(session, thread_id, msg)
                user_turn_appended = True
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
                await self._append_user_turn_if_needed(session, thread_id, msg)
                user_turn_appended = True
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
                await self._append_user_turn_if_needed(session, thread_id, msg)
                user_turn_appended = True
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
            if is_artifact_intent(msg.content) or is_long_task_intent(msg.content):
                await self._append_user_turn_if_needed(session, thread_id, msg)
                user_turn_appended = True
            handled = await self._runtime_service.maybe_handle_incoming(
                session,
                registry,
                msg,
                thread_id=thread_id,
            )
            if handled:
                return

        # Extract image paths from attachments
        image_paths = [
            att.local_path for att in (msg.attachments or []) if att.is_image
        ] or None

        # Inject default prompt for image-only messages (no text)
        if not msg.content and image_paths:
            msg = IncomingMessage(
                platform=msg.platform,
                channel_id=msg.channel_id,
                thread_id=msg.thread_id,
                author=msg.author,
                author_id=msg.author_id,
                content="Please describe and analyze the attached image(s).",
                raw=msg.raw,
                preferred_agent=msg.preferred_agent,
                system=msg.system,
                attachments=msg.attachments,
            )

        # Append user turn to history
        if not user_turn_appended:
            await session.append_user(
                thread_id, msg.content, msg.author,
                attachments=msg.attachments or None,
            )
        prior_history = history[:-1] if len(history) > 1 else []
        routed_skill = self._routed_skill_name(
            router_decision=router_decision,
            router_threshold=(self._intent_router.confidence_threshold if self._intent_router else None),
            history=history,
        )
        tracked_skill = explicit_skill or routed_skill
        skill_timeout_override = self._skill_timeout_seconds_by_name(tracked_skill)
        agent_purpose = self._agent_run_purpose(
            explicit_skill=explicit_skill or routed_skill,
            router_decision=router_decision,
            router_threshold=(self._intent_router.confidence_threshold if self._intent_router else None),
        )
        log_mode = self._thread_log_mode_from_purpose(agent_purpose)

        logger.info(
            "[%s] AGENT starting purpose=%s preferred_agent=%r skill_timeout_override=%r registry=%s history_turns=%d",
            req_id,
            agent_purpose,
            msg.preferred_agent,
            skill_timeout_override,
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

        # Inject adaptive memory context if available
        agent_prompt = msg.content
        if routed_skill and not explicit_skill:
            agent_prompt = f"/{routed_skill}\n\n{agent_prompt}".strip()
        if self._judge_store is not None:
            try:
                relevant = self._judge_store.get_relevant(
                    skill_name=tracked_skill,
                    thread_id=thread_id,
                    workspace=str(self._repo_root),
                    limit=self._memory_inject_limit,
                )
                logger.info(
                    "[%s] memory_inject selected_count=%d store_active=%d",
                    req_id,
                    len(relevant),
                    len(self._judge_store.get_active()),
                )
                if relevant:
                    mem_lines = [f"- {m.summary}" for m in relevant]
                    agent_prompt = (
                        "[Remembered context]\n"
                        + "\n".join(mem_lines)
                        + "\n\n"
                        + msg.content
                    )
            except Exception as exc:
                logger.warning("[%s] Memory injection failed: %s", req_id, exc)

        # Run agent (with fallback, or targeted if preferred_agent is set)
        workspace_override = await self._resolve_short_workspace(session, thread_id)
        log_path = self._chat_agent_log_base_path(
            thread_id=thread_id,
            request_id=req_id,
            purpose=agent_purpose,
        )
        relay: StreamingRelay | None = None
        on_partial_hook = None
        if self._streaming_enabled and getattr(channel, "supports_streaming_edit", False):
            initial_agent_name = msg.preferred_agent or (
                registry.agents[0].name if registry.agents else "agent"
            )
            try:
                relay = StreamingRelay(
                    channel=channel,
                    thread_id=thread_id,
                    attribution_prefix=f"-# via **{initial_agent_name}**",
                    min_edit_interval=self._streaming_min_edit_interval,
                )
                await relay.start(self._streaming_placeholder)
                on_partial_hook = relay.update
            except Exception as exc:
                logger.warning("[%s] streaming_relay_start_failed err=%s", req_id, exc)
                relay = None
                on_partial_hook = None

        t_agent = time.perf_counter()
        async with channel.typing(thread_id):
            async def _record_agent_run(*, agent, response, log_path, duration_s):
                if self._runtime_service is None:
                    return
                recorder = getattr(self._runtime_service, "record_thread_agent_run", None)
                if not callable(recorder):
                    return
                result = recorder(
                    thread_id=thread_id,
                    mode=log_mode,
                    agent_name=agent.name,
                    live_log_path=log_path,
                    duration_s=duration_s,
                    skill_name=tracked_skill,
                    request_id=req_id,
                    error=response.error,
                )
                if inspect.isawaitable(result):
                    await result

            agent_used, response = await self._run_registry_with_progress_logging(
                req_id=req_id,
                purpose=agent_purpose,
                registry=registry,
                prompt=agent_prompt,
                history=prior_history,
                thread_id=thread_id,
                force_agent=msg.preferred_agent,
                workspace_override=workspace_override,
                log_path=log_path,
                image_paths=image_paths,
                timeout_override_seconds=skill_timeout_override,
                max_turns_override=self._skill_max_turns_by_name(tracked_skill),
                on_agent_run=_record_agent_run,
                on_partial=on_partial_hook,
            )
        elapsed_agent = time.perf_counter() - t_agent
        await self._sync_registry_sessions(session, thread_id, registry)
        await record_usage_from_response(
            getattr(self, "_memory_store_ref", None),
            agent=agent_used.name,
            source="chat",
            platform=session.platform,
            channel_id=session.channel_id,
            thread_id=thread_id,
            response=response,
        )
        route_source = "explicit" if explicit_skill else "router" if routed_skill else None
        invocation_id = None
        if tracked_skill and route_source:
            invocation_id = await self._record_skill_invocation(
                skill_name=tracked_skill,
                route_source=route_source,
                req_id=req_id,
                session=session,
                thread_id=thread_id,
                msg=msg,
                agent_name=agent_used.name,
                response=response,
                latency_ms=int(elapsed_agent * 1000),
            )

        if response.error:
            logger.error(
                "[%s] AGENT_ERROR purpose=%s agent=%s elapsed=%.2fs error=%r",
                req_id,
                agent_purpose,
                agent_used.name,
                elapsed_agent,
                response.error,
            )
            failure_text = self._format_agent_failure_text(response)
            if relay is not None:
                with suppress(Exception):
                    await relay.finalize(
                        failure_text,
                        attribution_override=f"-# via **{agent_used.name}** (error)",
                    )
            else:
                await channel.send(thread_id, failure_text)
            # Remove the failed user turn so history stays clean
            history = await session.get_history(thread_id)
            if history:
                history.pop()
            return

        logger.info(
            "[%s] AGENT_OK purpose=%s agent=%s elapsed=%.2fs response_len=%d",
            req_id,
            agent_purpose,
            agent_used.name,
            elapsed_agent,
            len(response.text),
        )

        if explicit_skill or routed_skill:
            self._remember_thread_skill(
                session.platform,
                session.channel_id,
                thread_id,
                explicit_skill or routed_skill,
            )

        if await self._maybe_handle_control_challenge(
            req_id=req_id,
            session=session,
            registry=registry,
            thread_id=thread_id,
            msg=msg,
            agent_used=agent_used,
            response=response,
            agent_prompt=agent_prompt,
            explicit_skill=explicit_skill,
            routed_skill=routed_skill,
        ):
            if relay is not None:
                with suppress(Exception):
                    await relay.finalize(
                        "_(awaiting confirmation)_",
                        attribution_override=f"-# via **{agent_used.name}**",
                    )
            return

        # Record assistant response in history
        await session.append_assistant(thread_id, response.text, agent_used.name)

        # Send with attribution header + chunked content.
        if relay is not None:
            try:
                delivered_ids = await relay.finalize(
                    response.text,
                    usage=response.usage,
                    attribution_override=f"-# via **{agent_used.name}**",
                )
            except Exception as exc:
                logger.warning("[%s] streaming_relay_finalize_failed err=%s", req_id, exc)
                delivered_ids = []
            if delivered_ids:
                delivery = ResponseDelivery(
                    first_message_id=delivered_ids[0],
                    chunk_count=len(delivered_ids),
                )
            else:
                delivery = await self._send_agent_response(
                    channel,
                    thread_id,
                    agent_name=agent_used.name,
                    text=response.text,
                    usage=response.usage,
                )
        else:
            delivery = await self._send_agent_response(
                channel,
                thread_id,
                agent_name=agent_used.name,
                text=response.text,
                usage=response.usage,
            )
        if invocation_id is not None:
            await self._bind_skill_invocation_message(invocation_id, delivery.first_message_id)

        elapsed_total = time.perf_counter() - t_start
        logger.info(
            "[%s] DONE thread=%s chunks=%d total_elapsed=%.2fs",
            req_id,
            thread_id,
            max(delivery.chunk_count, 1),
            elapsed_total,
        )

        # Idle tracker: reset timer for this thread; judge will fire after idle window.
        if self._idle_tracker is not None:
            await self._idle_tracker.touch(
                self._thread_key(session.platform, session.channel_id, thread_id),
                metadata={
                    "platform": session.platform,
                    "channel_id": session.channel_id,
                    "thread_id": thread_id,
                    "skill_name": tracked_skill,
                },
            )
            self._session_index[self._session_key(session.platform, session.channel_id)] = (session, registry)

        # Synchronous keyword-triggered judge (e.g. "记一下", "remember this")
        if self._judge is not None and self._user_message_has_memory_keyword(msg.content):
            asyncio.create_task(
                self._run_memory_judge(
                    session=session,
                    registry=registry,
                    thread_id=thread_id,
                    skill_name=tracked_skill,
                    source_workspace=str(self._repo_root),
                    req_id=req_id,
                    explicit_summary=None,
                    explicit_scope=None,
                )
            )

        # Async: history compression
        if self._compressor:
            asyncio.create_task(self._try_compress(session, registry, thread_id, req_id))

        # Async: detect and hot-reload new skills created by agents
        if self._skill_syncer:
            asyncio.create_task(
                self._try_skill_sync(session, thread_id, req_id)
            )

        # Clean up downloaded attachment temp files
        if image_paths:
            for p in image_paths:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

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
                session.platform, session.channel_id, thread_id, registry, req_id=req_id,
            )
            if did_compress:
                # Invalidate cache so next load picks up the summary
                session._cache.pop(thread_id, None)
                logger.info("[%s] COMPRESS thread=%s completed", req_id, thread_id)
        except Exception as exc:
            logger.warning("[%s] COMPRESS failed: %s", req_id, exc)

    @staticmethod
    def _thread_key(platform: str, channel_id: str, thread_id: str) -> str:
        return f"{platform}|{channel_id}|{thread_id}"

    @staticmethod
    def _split_thread_key(key: str) -> tuple[str, str, str]:
        parts = key.split("|", 2)
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        return "", "", key

    def _user_message_has_memory_keyword(self, text: str) -> bool:
        if not text:
            return False
        lowered = text.lower()
        return any(kw in lowered for kw in self._memory_keyword_patterns)

    async def _run_memory_judge(
        self,
        *,
        session: ChannelSession,
        registry: AgentRegistry,
        thread_id: str,
        skill_name: str | None,
        source_workspace: str | None,
        req_id: str | None,
        explicit_summary: str | None,
        explicit_scope: str | None,
    ) -> dict | None:
        if self._judge is None or self._judge_store is None:
            return None
        try:
            history = await session.get_history(thread_id)
        except Exception as exc:
            logger.warning("memory_judge: failed to load history thread=%s: %s", thread_id, exc)
            return None
        thread_topic = self._memory_thread_topic(history) if history else None
        try:
            result = await self._judge.run(
                conversation=history,
                registry=registry,
                thread_id=thread_id,
                skill_name=skill_name or self._recent_thread_skill(session.platform, session.channel_id, thread_id),
                source_workspace=source_workspace or str(self._repo_root),
                thread_topic=thread_topic,
                explicit_summary=explicit_summary,
                explicit_scope=explicit_scope,
                req_id=req_id,
            )
        except Exception as exc:
            logger.warning("memory_judge crashed thread=%s: %s", thread_id, exc)
            return None
        if self._idle_tracker is not None:
            await self._idle_tracker.mark_judged(
                self._thread_key(session.platform, session.channel_id, thread_id)
            )
        if self._judge_store.should_synthesize():
            asyncio.create_task(self._try_memory_md_synth(registry))
        return {"actions": result.actions, "stats": result.stats, "error": result.error}

    async def _try_memory_md_synth(self, registry: AgentRegistry) -> None:
        if self._judge_store is None:
            return
        try:
            await self._judge_store.synthesize_memory_md(registry)
        except Exception as exc:
            logger.warning("MEMORY.md synthesis failed: %s", exc)

    async def _idle_judge_fire(self, thread_key: str, metadata: dict) -> None:
        if self._judge is None or self._judge_store is None:
            return
        platform, channel_id, thread_id = self._split_thread_key(thread_key)
        if not (platform and channel_id and thread_id):
            return
        registry_pair = self._session_index.get(self._session_key(platform, channel_id))
        if registry_pair is None:
            logger.debug("idle judge fire: no session for %s", thread_key)
            return
        session, registry = registry_pair
        await self._run_memory_judge(
            session=session,
            registry=registry,
            thread_id=thread_id,
            skill_name=metadata.get("skill_name") if isinstance(metadata, dict) else None,
            source_workspace=str(self._repo_root),
            req_id=None,
            explicit_summary=None,
            explicit_scope=None,
        )

    async def request_memorize(
        self,
        *,
        platform: str,
        channel_id: str,
        thread_id: str,
        explicit_summary: str | None = None,
        explicit_scope: str | None = None,
    ) -> dict | None:
        """Public entry point used by Discord /memorize and router intent."""
        registry_pair = self._session_index.get(self._session_key(platform, channel_id))
        if registry_pair is None:
            return {"error": "no active session for this channel"}
        session, registry = registry_pair
        return await self._run_memory_judge(
            session=session,
            registry=registry,
            thread_id=thread_id,
            skill_name=self._recent_thread_skill(platform, channel_id, thread_id),
            source_workspace=str(self._repo_root),
            req_id=None,
            explicit_summary=explicit_summary,
            explicit_scope=explicit_scope,
        )

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
        self._refresh_base_workspace_if_needed()
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

    def _refresh_base_workspace_if_needed(self) -> None:
        if self._base_workspace is None:
            return
        syncer = self._workspace_syncer()
        if syncer is None:
            return
        if not syncer.workspace_needs_refresh(self._base_workspace):
            return
        logger.info("Refreshing base workspace from repo sources: %s", self._base_workspace)
        syncer.refresh_workspace(self._base_workspace)

    def _workspace_syncer(self):
        if self._skill_syncer is not None:
            return self._skill_syncer
        try:
            from oh_my_agent.skills.skill_sync import SkillSync
        except Exception:
            logger.warning("Failed to import SkillSync for base workspace refresh", exc_info=True)
            return None
        return SkillSync(self._repo_root / "skills", project_root=self._repo_root)

    def _prepare_workspace_compat_files(self, workspace: Path) -> None:
        if self._base_workspace is None:
            return
        for name in ("AGENTS.md", ".claude", ".gemini", ".agents"):
            src = self._base_workspace / name
            dst = workspace / name
            if not src.exists():
                continue
            if dst.exists() or dst.is_symlink():
                if dst.is_symlink():
                    try:
                        if dst.resolve() == src.resolve():
                            continue
                    except OSError:
                        pass
                    dst.unlink(missing_ok=True)
                elif dst.is_dir():
                    shutil.rmtree(dst, ignore_errors=True)
                else:
                    dst.unlink(missing_ok=True)
            try:
                os.symlink(src, dst, target_is_directory=src.is_dir())
            except OSError:
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
        legacy_codex = workspace / ".codex"
        if legacy_codex.exists() or legacy_codex.is_symlink():
            if legacy_codex.is_dir() and not legacy_codex.is_symlink():
                shutil.rmtree(legacy_codex, ignore_errors=True)
            else:
                legacy_codex.unlink(missing_ok=True)

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
    def _agent_run_purpose(
        *,
        explicit_skill: str | None,
        router_decision,
        router_threshold: float | None,
    ) -> str:
        if explicit_skill:
            return "explicit_skill"
        if router_decision is None or router_threshold is None:
            return "direct_reply"
        if router_decision.confidence < router_threshold:
            return "direct_reply"
        if router_decision.decision == "reply_once":
            return "router_reply_once"
        if router_decision.decision == "invoke_existing_skill":
            return "router_invoke_existing_skill"
        return "direct_reply"

    @staticmethod
    def _thread_log_mode_from_purpose(purpose: str) -> str:
        if purpose in {"explicit_skill", "router_invoke_existing_skill"}:
            return "invoke_existing_skill"
        if purpose in {"hitl_resume", "hitl_resume_fresh"}:
            return "hitl_resume"
        if purpose in {"resume", "resume_fresh"}:
            return "resume"
        return "chat"

    @staticmethod
    def _format_usage(usage: dict) -> str:
        return format_usage_audit(usage)

    async def _send_agent_response(
        self,
        channel,
        thread_id: str,
        *,
        agent_name: str,
        text: str,
        usage: dict | None = None,
    ) -> ResponseDelivery:
        attribution = append_usage_audit(f"-# via **{agent_name}**", usage)

        first_chunk_budget = max(1, 2000 - len(attribution) - 1)
        first_chunks = chunk_message(text, max_size=first_chunk_budget)
        if not first_chunks:
            message_id = await channel.send(thread_id, f"{attribution}\n*(empty response)*")
            return ResponseDelivery(first_message_id=message_id, chunk_count=1)

        first_message_id = await channel.send(thread_id, f"{attribution}\n{first_chunks[0]}")

        remainder = text[len(first_chunks[0]):].lstrip()
        remaining_chunks = chunk_message(remainder) if remainder else []
        for chunk in remaining_chunks:
            await channel.send(thread_id, chunk)
        return ResponseDelivery(
            first_message_id=first_message_id,
            chunk_count=1 + len(remaining_chunks),
        )

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

    def _known_skill_router_entries(self) -> list[tuple[str, str]]:
        if not self._skill_syncer:
            return []
        skills_path = getattr(self._skill_syncer, "_skills_path", None)
        if not isinstance(skills_path, Path) or not skills_path.is_dir():
            return []

        entries: list[tuple[str, str]] = []
        for child in sorted(skills_path.iterdir(), key=lambda p: p.name):
            skill_md = child / "SKILL.md"
            if not child.is_dir() or not skill_md.exists():
                continue
            if self._is_skill_auto_disabled(child.name):
                continue
            entries.append((child.name, self._read_skill_description(skill_md)))
        return entries

    @staticmethod
    def _read_skill_frontmatter(skill_md: Path) -> dict:
        return read_skill_frontmatter(skill_md)

    @classmethod
    def _read_skill_description(cls, skill_md: Path) -> str:
        meta = cls._read_skill_frontmatter(skill_md)
        description = meta.get("description", "")
        if not isinstance(description, str):
            return ""
        return description.strip().replace("\n", " ")

    def _thread_skill_key(self, platform: str, channel_id: str, thread_id: str) -> tuple[str, str, str]:
        return (platform, channel_id, thread_id)

    def _remember_thread_skill(
        self,
        platform: str,
        channel_id: str,
        thread_id: str,
        skill_name: str | None,
    ) -> None:
        if not skill_name:
            return
        self._recent_thread_skills[self._thread_skill_key(platform, channel_id, thread_id)] = skill_name

    def _recent_thread_skill(
        self,
        platform: str,
        channel_id: str,
        thread_id: str,
    ) -> str | None:
        return self._recent_thread_skills.get(self._thread_skill_key(platform, channel_id, thread_id))

    def _skill_frontmatter_by_name(self, skill_name: str | None) -> dict:
        skills_path = getattr(self._skill_syncer, "_skills_path", None) if self._skill_syncer else None
        return resolve_skill_frontmatter(
            skill_name,
            repo_root=self._repo_root,
            skills_path=skills_path if isinstance(skills_path, Path) and skills_path.is_dir() else None,
        )

    def _skill_description_by_name(self, skill_name: str | None) -> str:
        meta = self._skill_frontmatter_by_name(skill_name)
        description = meta.get("description", "")
        if not isinstance(description, str):
            return ""
        return description.strip().replace("\n", " ")

    def _skill_timeout_seconds_by_name(self, skill_name: str | None) -> int | None:
        return skill_execution_limits(self._skill_frontmatter_by_name(skill_name)).timeout_seconds

    def _skill_max_turns_by_name(self, skill_name: str | None) -> int | None:
        return skill_execution_limits(self._skill_frontmatter_by_name(skill_name)).max_turns

    @staticmethod
    def _format_agent_failure_text(response) -> str:
        base = user_safe_agent_error(response.error_kind)
        partial = str(getattr(response, "partial_text", "") or "").strip()
        if not partial:
            return base
        excerpt = partial[-_PARTIAL_EXCERPT_MAX_CHARS:]
        label = "**Partial result before stop**"
        if response.error_kind == "max_turns":
            label = "**Partial result before max turns**"
        elif response.error_kind == "timeout":
            label = "**Partial result before timeout**"
        text = f"{base}\n\n{label}\n```text\n{excerpt}\n```"
        return text[:1900]

    async def _append_user_turn_if_needed(
        self,
        session: ChannelSession,
        thread_id: str,
        msg: IncomingMessage,
    ) -> None:
        history = await session.get_history(thread_id)
        if history:
            last = history[-1]
            if (
                last.get("role") == "user"
                and last.get("content") == msg.content
                and last.get("author") == msg.author
            ):
                return
        await session.append_user(
            thread_id,
            msg.content,
            msg.author,
            attachments=msg.attachments or None,
        )

    def _build_router_context(
        self,
        history: list[dict],
        *,
        platform: str | None = None,
        channel_id: str | None = None,
        thread_id: str | None = None,
    ) -> str:
        lines: list[str] = []
        if history:
            recent = history[-self._router_context_turns:]
            lines.append("Recent thread context:")
            for turn in recent:
                role = turn.get("role", "unknown")
                content = str(turn.get("content", "")).strip().replace("\n", " ")
                if not content:
                    continue
                lines.append(f"- {role}: {content[:240]}")
        known_skills = self._known_skill_router_entries()
        if known_skills:
            lines.append("Known skills available in this workspace:")
            for name, description in known_skills[:12]:
                suffix = f": {description}" if description else ""
                lines.append(f"- {name}{suffix}")
            if len(known_skills) > 12:
                lines.append(f"- ... and {len(known_skills) - 12} more")
        recent_skill = None
        if platform and channel_id and thread_id:
            recent_skill = self._recent_thread_skill(platform, channel_id, thread_id)
        if not recent_skill:
            recent_skill = self._recent_invoked_skill(history) or self._recent_known_skill_reference(history)
        if recent_skill:
            desc = self._skill_description_by_name(recent_skill)
            suffix = f": {desc}" if desc else ""
            lines.append(f"Most recently invoked skill in this thread: {recent_skill}{suffix}")
        return "\n".join(lines)

    def _recent_known_skill_reference(self, history: list[dict]) -> str | None:
        known = self._known_skill_names()
        if not known:
            return None
        for turn in reversed(history):
            content = str(turn.get("content", ""))
            for token in re.findall(r"`([a-zA-Z0-9][a-zA-Z0-9-_]{0,62})`", content):
                if token in known:
                    return token
        return None

    def _routed_skill_name(
        self,
        *,
        router_decision,
        router_threshold: float | None,
        history: list[dict],
    ) -> str | None:
        if router_decision is None or router_threshold is None:
            return None
        if router_decision.confidence < router_threshold:
            return None
        if router_decision.decision != "invoke_existing_skill":
            return None
        known = self._known_skill_names()
        if router_decision.skill_name and router_decision.skill_name in known:
            if self._is_skill_auto_disabled(router_decision.skill_name):
                return None
            return router_decision.skill_name
        recent = self._recent_invoked_skill(history) or self._recent_known_skill_reference(history)
        if recent and recent in known and not self._is_skill_auto_disabled(recent):
            return recent
        return None

    def _recent_invoked_skill(self, history: list[dict]) -> str | None:
        known = self._known_skill_names()
        for turn in reversed(history):
            if turn.get("role") != "user":
                continue
            content = str(turn.get("content", ""))
            match = _EXPLICIT_SKILL_CALL_RE.match(content.strip())
            if not match:
                continue
            skill_name = match.group(1)
            if skill_name in known:
                return skill_name
        return None

    @staticmethod
    def _memory_thread_topic(history: list[dict]) -> str:
        lines: list[str] = []
        for turn in reversed(history):
            if turn.get("role") != "user":
                continue
            content = str(turn.get("content", "")).strip()
            if not content:
                continue
            lines.append(content[:200])
            if len(lines) >= 2:
                break
        return " | ".join(reversed(lines))

    @staticmethod
    def _build_skill_repair_request(skill_name: str, history: list[dict], latest_feedback: str) -> str:
        recent = history[-6:]
        lines = [
            f"Repair existing skill: {skill_name}",
            "",
            "Recent thread context:",
        ]
        for turn in recent:
            role = turn.get("role", "unknown")
            content = str(turn.get("content", "")).strip().replace("\n", " ")
            if len(content) > 280:
                content = content[:280] + "..."
            lines.append(f"- {role}: {content}")
        lines.extend(
            [
                "",
                "Latest user feedback:",
                latest_feedback.strip(),
            ]
        )
        return "\n".join(lines).strip()

    def resolve_session(self, platform: str, channel_id: str) -> ChannelSession | None:
        return self._sessions.get(self._session_key(platform, channel_id))

    def resolve_channel(self, platform: str, channel_id: str) -> BaseChannel | None:
        session = self.resolve_session(platform, channel_id)
        return session.channel if session else None
