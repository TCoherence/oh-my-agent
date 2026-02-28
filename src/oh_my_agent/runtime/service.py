from __future__ import annotations

import asyncio
import fnmatch
import inspect
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.gateway.base import IncomingMessage
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.runtime.policy import (
    build_skill_prompt,
    build_runtime_prompt,
    extract_skill_name,
    evaluate_strict_risk,
    is_long_task_intent,
    parse_task_state,
)
from oh_my_agent.runtime.types import (
    TASK_TYPE_CODE,
    TASK_TYPE_SKILL,
    TASK_STATUS_APPLIED,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_DISCARDED,
    TASK_STATUS_DRAFT,
    TASK_STATUS_FAILED,
    TASK_STATUS_MERGED,
    TASK_STATUS_MERGE_FAILED,
    TASK_STATUS_PAUSED,
    TASK_STATUS_PENDING,
    TASK_STATUS_REJECTED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_STOPPED,
    TASK_STATUS_TIMEOUT,
    TASK_STATUS_VALIDATING,
    TASK_STATUS_WAITING_MERGE,
    RuntimeTask,
    TaskDecisionEvent,
)
from oh_my_agent.runtime.worktree import WorktreeError, WorktreeManager

logger = logging.getLogger(__name__)

_STATUS_MESSAGE_PREFIX = "**Task Status**"

_TERMINAL_CLEANUP_STATUSES = {
    TASK_STATUS_APPLIED,  # legacy
    TASK_STATUS_MERGED,
    TASK_STATUS_DISCARDED,
    TASK_STATUS_MERGE_FAILED,
    TASK_STATUS_FAILED,
    TASK_STATUS_TIMEOUT,
    TASK_STATUS_STOPPED,
    TASK_STATUS_REJECTED,
}


class RuntimeService:
    """Autonomous task runtime for multi-step coding loops."""

    def __init__(
        self,
        store,
        *,
        config: dict[str, Any] | None = None,
        owner_user_ids: set[str] | None = None,
        repo_root: Path | None = None,
        skill_syncer=None,
        skills_path: Path | None = None,
    ) -> None:
        cfg = config or {}
        self._enabled = bool(cfg.get("enabled", True))
        self._worker_concurrency = int(cfg.get("worker_concurrency", 3))
        self._default_agent = str(cfg.get("default_agent", "codex"))
        self._default_test_command = str(cfg.get("default_test_command", "pytest -q"))
        self._default_max_steps = int(cfg.get("default_max_steps", 8))
        self._default_max_minutes = int(cfg.get("default_max_minutes", 20))
        self._risk_profile = str(cfg.get("risk_profile", "strict"))
        self._path_policy_mode = str(cfg.get("path_policy_mode", "allow_all_with_denylist"))
        self._allowed_paths = list(
            cfg.get("allowed_paths", ["src/**", "tests/**", "docs/**", "skills/**", "pyproject.toml"])
        )
        self._denied_paths = list(cfg.get("denied_paths", [".env", "config.yaml", ".workspace/**", ".git/**"]))
        self._decision_ttl_minutes = int(cfg.get("decision_ttl_minutes", 1440))
        self._agent_heartbeat_seconds = float(cfg.get("agent_heartbeat_seconds", 20))
        self._test_heartbeat_seconds = float(cfg.get("test_heartbeat_seconds", 15))
        self._test_timeout_seconds = float(cfg.get("test_timeout_seconds", 600))
        self._progress_notice_seconds = float(cfg.get("progress_notice_seconds", 30))
        self._progress_persist_seconds = float(cfg.get("progress_persist_seconds", 60))
        self._log_event_limit = int(cfg.get("log_event_limit", 12))
        self._log_tail_chars = int(cfg.get("log_tail_chars", 1200))

        cleanup_cfg = cfg.get("cleanup", {})
        self._cleanup_enabled = bool(cleanup_cfg.get("enabled", True))
        self._cleanup_interval_minutes = int(cleanup_cfg.get("interval_minutes", 60))
        self._cleanup_retention_hours = int(cleanup_cfg.get("retention_hours", 72))
        self._cleanup_prune_worktrees = bool(cleanup_cfg.get("prune_git_worktrees", True))
        self._cleanup_merged_immediately = bool(cleanup_cfg.get("merged_immediate", True))

        merge_cfg = cfg.get("merge_gate", {})
        self._merge_gate_enabled = bool(merge_cfg.get("enabled", True))
        self._merge_auto_commit = bool(merge_cfg.get("auto_commit", True))
        self._merge_require_clean_repo = bool(merge_cfg.get("require_clean_repo", True))
        self._merge_preflight_check = bool(merge_cfg.get("preflight_check", True))
        self._merge_target_branch_mode = str(merge_cfg.get("target_branch_mode", "current"))
        self._merge_commit_template = str(
            merge_cfg.get("commit_message_template", "runtime(task:{task_id}): {goal_short}")
        )

        self._store = store
        self._owner_user_ids = owner_user_ids or set()
        self._repo_root = (repo_root or Path.cwd()).resolve()
        self._skill_syncer = skill_syncer
        self._skills_path = skills_path
        worktree_root = Path(cfg.get("worktree_root", "~/.oh-my-agent/runtime/tasks")).expanduser().resolve()
        self._worktree = WorktreeManager(self._repo_root, worktree_root)

        self._sessions: dict[str, ChannelSession] = {}
        self._registries: dict[str, AgentRegistry] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._workers: list[asyncio.Task] = []
        self._janitor_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def register_session(self, session: ChannelSession, registry: AgentRegistry) -> None:
        key = self._key(session.platform, session.channel_id)
        self._sessions[key] = session
        self._registries[key] = registry

    async def start(self) -> None:
        if not self._enabled:
            return
        requeued = await self._store.requeue_inflight_runtime_tasks()
        cleaned_on_start = 0
        if self._cleanup_enabled:
            cleaned_on_start = await self._cleanup_expired_tasks()
        for idx in range(self._worker_concurrency):
            self._workers.append(
                asyncio.create_task(self._worker_loop(idx), name=f"runtime-worker-{idx}")
            )
        if self._cleanup_enabled:
            self._janitor_task = asyncio.create_task(self._janitor_loop(), name="runtime-janitor")
        logger.info(
            "Runtime started with %d worker(s)%s%s%s",
            len(self._workers),
            " + janitor" if self._janitor_task else "",
            f"; requeued {requeued} inflight task(s)" if requeued else "",
            f"; cleaned {cleaned_on_start} stale workspace(s) on start" if cleaned_on_start else "",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._janitor_task:
            self._janitor_task.cancel()
        for task in self._workers:
            task.cancel()
        waiters = [*self._workers]
        if self._janitor_task:
            waiters.append(self._janitor_task)
        if waiters:
            await asyncio.gather(*waiters, return_exceptions=True)

    async def maybe_handle_incoming(
        self,
        session: ChannelSession,
        registry: AgentRegistry,
        msg: IncomingMessage,
        *,
        thread_id: str,
    ) -> bool:
        if not self._enabled:
            return False
        if msg.system:
            return False

        actor = msg.author_id or msg.author

        # 1. Try control command first (stop/pause/resume)
        control = self._parse_control_intent(msg.content)
        if control is not None:
            action, instruction = control
            active = await self._active_task_for_thread(session.platform, session.channel_id, thread_id)
            if active is not None:
                if action == "stop":
                    result = await self.stop_task(active.id, actor_id=actor)
                elif action == "pause":
                    result = await self.pause_task(active.id, actor_id=actor)
                elif action == "resume":
                    result = await self.resume_task(active.id, instruction, actor_id=actor)
                else:
                    result = "Unknown control action."
                await session.channel.send(thread_id, result)
                return True

        # 2. Auto-resume: if thread has a BLOCKED/PAUSED task, resume it regardless of intent.
        #    This takes priority over new task creation — the user is replying to the blocked task.
        active = await self._active_task_for_thread(session.platform, session.channel_id, thread_id)
        if active is not None and active.status in {TASK_STATUS_BLOCKED, TASK_STATUS_PAUSED}:
            result = await self.resume_task(active.id, msg.content, actor_id=actor)
            await session.channel.send(thread_id, result)
            return True

        # 3. Long task intent → create task
        if not is_long_task_intent(msg.content):
            return False

        await self.create_task(
            session=session,
            registry=registry,
            thread_id=thread_id,
            goal=msg.content,
            raw_request=msg.content,
            created_by=actor,
            preferred_agent=msg.preferred_agent,
            source="message",
        )
        return True

    async def create_task(
        self,
        *,
        session: ChannelSession,
        registry: AgentRegistry,
        thread_id: str,
        goal: str,
        raw_request: str | None = None,
        created_by: str,
        preferred_agent: str | None = None,
        test_command: str | None = None,
        max_steps: int | None = None,
        max_minutes: int | None = None,
        source: str,
        force_draft: bool = False,
        task_type: str = TASK_TYPE_CODE,
        skill_name: str | None = None,
    ) -> RuntimeTask:
        self.register_session(session, registry)

        steps = int(max_steps or self._default_max_steps)
        minutes = int(max_minutes or self._default_max_minutes)
        command = test_command or self._default_test_command
        chosen_agent = preferred_agent or self._default_agent

        require_approval = False
        reasons: list[str] = []
        if self._risk_profile == "strict":
            risk = evaluate_strict_risk(goal, max_steps=steps, max_minutes=minutes)
            require_approval = risk.require_approval
            reasons = risk.reasons

        task_id = uuid.uuid4().hex[:12]
        status = TASK_STATUS_DRAFT if (force_draft or require_approval) else TASK_STATUS_PENDING

        task = await self._store.create_runtime_task(
            task_id=task_id,
            platform=session.platform,
            channel_id=session.channel_id,
            thread_id=thread_id,
            created_by=created_by,
            goal=goal,
            original_request=raw_request or goal,
            preferred_agent=chosen_agent,
            status=status,
            max_steps=steps,
            max_minutes=minutes,
            test_command=command,
            task_type=task_type,
            skill_name=skill_name,
        )
        await self._store.add_runtime_event(
            task.id,
            "task.created",
            {"source": source, "status": status, "risk_reasons": reasons, "force_draft": force_draft},
        )
        logger.info(
            "Runtime task created id=%s status=%s source=%s agent=%s budget=%d/%d",
            task.id,
            status,
            source,
            chosen_agent,
            steps,
            minutes,
        )

        if status == TASK_STATUS_DRAFT:
            nonce = await self._store.create_runtime_decision_nonce(
                task.id,
                ttl_minutes=self._decision_ttl_minutes,
            )
            draft_text = self._draft_text(task, reasons=reasons)
            msg_id = await self._send_decision_surface(
                session,
                thread_id,
                draft_text,
                task.id,
                nonce,
                ["approve", "reject", "suggest"],
            )
            if msg_id:
                await self._store.update_runtime_task(task.id, decision_message_id=msg_id)
            await self._notify(
                task,
                f"Task `{task.id}` is waiting for approval. Use buttons or `/task_approve {task.id}`.",
            )
            await self._signal_status_by_id(task, TASK_STATUS_DRAFT)
        else:
            await self._notify(
                task,
                f"Task `{task.id}` queued (`{chosen_agent}`), max {steps} steps / {minutes} min.",
            )
            await self._signal_status_by_id(task, TASK_STATUS_PENDING)

        return task

    async def create_skill_task(
        self,
        *,
        session: ChannelSession,
        registry: AgentRegistry,
        thread_id: str,
        goal: str,
        raw_request: str | None = None,
        created_by: str,
        preferred_agent: str | None = None,
        skill_name: str,
        source: str,
    ) -> RuntimeTask:
        existing = (
            {d.name for d in self._skills_path.iterdir() if d.is_dir()}
            if self._skills_path and self._skills_path.is_dir()
            else None
        )
        resolved_name, is_update = extract_skill_name(skill_name or goal, existing)
        effective_goal = f"Update existing skill '{resolved_name}': {goal}" if is_update else goal
        return await self.create_task(
            session=session,
            registry=registry,
            thread_id=thread_id,
            goal=effective_goal,
            raw_request=raw_request,
            created_by=created_by,
            preferred_agent=preferred_agent or self._default_agent,
            test_command=f"python skills/skill-creator/scripts/quick_validate.py skills/{resolved_name}",
            max_steps=6,
            max_minutes=15,
            source=source,
            force_draft=True,
            task_type=TASK_TYPE_SKILL,
            skill_name=resolved_name,
        )

    async def enqueue_scheduler_task(
        self,
        *,
        session: ChannelSession,
        registry: AgentRegistry,
        thread_id: str,
        prompt: str,
        author: str,
        preferred_agent: str | None,
    ) -> RuntimeTask:
        return await self.create_task(
            session=session,
            registry=registry,
            thread_id=thread_id,
            goal=prompt,
            raw_request=prompt,
            created_by=author,
            preferred_agent=preferred_agent,
            source="scheduler",
        )

    async def get_task(self, task_id: str) -> RuntimeTask | None:
        return await self._store.get_runtime_task(task_id)

    async def list_tasks(
        self,
        *,
        platform: str,
        channel_id: str,
        status: str | None = None,
        limit: int = 20,
    ) -> list[RuntimeTask]:
        return await self._store.list_runtime_tasks(
            platform=platform,
            channel_id=channel_id,
            status=status,
            limit=limit,
        )

    async def stop_task(self, task_id: str, *, actor_id: str) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can stop tasks."
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."
        await self._store.update_runtime_task(
            task_id,
            status=TASK_STATUS_STOPPED,
            summary="Stopped by user.",
            ended_at_now=True,
        )
        await self._store.add_runtime_event(task_id, "task.stopped", {"actor_id": actor_id})
        # The heartbeat loop in _invoke_agent will detect STOPPED status and cancel the agent.
        await self._notify(task, f"Task `{task.id}` stopped.")
        await self._signal_status_by_id(task, TASK_STATUS_STOPPED)
        return f"Task `{task.id}` stopped."

    async def pause_task(self, task_id: str, *, actor_id: str) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can pause tasks."
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."
        if task.status not in {TASK_STATUS_RUNNING, TASK_STATUS_VALIDATING, TASK_STATUS_PENDING}:
            return f"Task `{task.id}` cannot be paused (current status: {task.status})."
        await self._store.update_runtime_task(
            task_id,
            status=TASK_STATUS_PAUSED,
            summary="Paused by user.",
            ended_at=None,
        )
        await self._store.add_runtime_event(task_id, "task.paused", {"actor_id": actor_id})
        # The heartbeat loop in _invoke_agent will detect PAUSED status and cancel the agent.
        await self._notify(task, f"Task `{task.id}` paused. Reply with instructions to resume.")
        await self._signal_status_by_id(task, TASK_STATUS_PAUSED)
        return f"Task `{task.id}` paused."

    async def resume_task(self, task_id: str, instruction: str, *, actor_id: str) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can resume tasks."
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."
        if task.status not in {TASK_STATUS_BLOCKED, TASK_STATUS_PAUSED}:
            return f"Task `{task.id}` is not blocked or paused (current status: {task.status})."
        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_PENDING,
            blocked_reason=None,
            resume_instruction=instruction.strip() or None,
            ended_at=None,
        )
        await self._store.add_runtime_event(
            task.id,
            "task.resumed",
            {"actor_id": actor_id, "instruction": instruction},
        )
        await self._notify(task, f"Task `{task.id}` resumed and queued.")
        await self._signal_status_by_id(task, TASK_STATUS_PENDING)
        return f"Task `{task.id}` resumed and queued."

    async def merge_task(self, task_id: str, *, actor_id: str) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can merge tasks."
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."
        return await self._execute_merge(task, actor_id=actor_id, source="slash")

    async def discard_task(self, task_id: str, *, actor_id: str) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can discard tasks."
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."
        if task.status not in {TASK_STATUS_WAITING_MERGE, TASK_STATUS_APPLIED}:
            return f"Task `{task.id}` is not waiting merge (status: {task.status})."
        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_DISCARDED,
            summary="Discarded by user.",
            ended_at_now=True,
        )
        await self._store.add_runtime_event(task.id, "task.discarded", {"actor_id": actor_id})
        await self._notify(task, f"Task `{task.id}` discarded.")
        await self._signal_status_by_id(task, TASK_STATUS_DISCARDED)
        return f"Task `{task.id}` discarded."

    async def get_task_changes(self, task_id: str) -> str:
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."

        changes = await self._collect_task_changes(task, limit=200)

        if not changes:
            return f"Task `{task.id}` has no detectable file changes."

        lines = [f"Task `{task.id}` changes ({len(changes)}):"]
        lines.extend(f"- `{line}`" for line in changes[:80])
        if len(changes) > 80:
            lines.append(f"- ... and {len(changes) - 80} more")
        return "\n".join(lines)[:1900]

    async def get_task_logs(self, task_id: str) -> str:
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."

        lines = [
            f"**Task Logs** `{task.id}`",
            f"- Status: `{task.status}`",
            f"- Step: {task.step_no}/{task.max_steps}",
        ]
        if task.summary:
            lines.append(f"- Summary: {task.summary[:240]}")
        if task.error:
            lines.append(f"- Error: {task.error[:240]}")

        events = await self._store.list_runtime_events(task.id, limit=self._log_event_limit)
        if events:
            lines.append("")
            lines.append("**Recent events**")
            for event in events[-8:]:
                payload = event.get("payload", {})
                summary = self._summarize_event_payload(payload)
                lines.append(
                    f"- `{event['event_type']}`"
                    + (f": {summary}" if summary else "")
                )

        ckpt = await self._store.get_last_runtime_checkpoint(task.id)
        if ckpt:
            agent_tail = self._tail_text(str(ckpt.get("agent_result", "")))
            test_tail = self._format_test_output(str(ckpt.get("test_result", "")))
            if agent_tail:
                lines.append("")
                lines.append("**Last agent output tail**")
                lines.append(f"```text\n{agent_tail}\n```")
            if test_tail:
                lines.append("")
                lines.append("**Last test result**")
                lines.append(f"```text\n{test_tail}\n```")
        return "\n".join(lines)[:3800]

    async def cleanup_tasks(self, *, actor_id: str, task_id: str | None = None) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can clean runtime tasks."

        if task_id:
            task = await self._store.get_runtime_task(task_id)
            if task is None:
                return f"Task `{task_id}` not found."
            if task.status not in _TERMINAL_CLEANUP_STATUSES:
                return f"Task `{task.id}` is not in cleanable terminal state (status: {task.status})."
            cleaned = await self._cleanup_single_task(task)
            if cleaned:
                return f"Task `{task.id}` workspace cleaned."
            return f"Task `{task.id}` had no workspace to clean."

        cleaned = await self._cleanup_expired_tasks()
        return f"Cleanup completed. {cleaned} task workspace(s) removed."

    async def handle_decision_event(self, event: TaskDecisionEvent) -> str:
        if not self._is_authorized(event.actor_id):
            return "Only configured owners can perform task decisions."

        task = await self._store.get_runtime_task(event.task_id)
        if task is None:
            return f"Task `{event.task_id}` not found."

        if event.action in {"approve", "reject", "suggest"}:
            valid = {TASK_STATUS_DRAFT, TASK_STATUS_BLOCKED}
            if task.status not in valid:
                return f"Task `{task.id}` is not waiting approval (status: {task.status})."
        elif event.action in {"merge", "discard", "request_changes"}:
            valid = {TASK_STATUS_WAITING_MERGE, TASK_STATUS_APPLIED}
            if task.status not in valid:
                return f"Task `{task.id}` is not waiting merge (status: {task.status})."
        else:
            return f"Unsupported decision action: {event.action}"

        if not await self._store.consume_runtime_decision_nonce(
            task_id=task.id,
            nonce=event.nonce,
            action=event.action,
            actor_id=event.actor_id,
            source=event.source,
            result="accepted",
        ):
            return "Decision token is invalid or expired."

        if event.action == "approve":
            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_PENDING,
                blocked_reason=None,
            )
            await self._store.add_runtime_event(
                task.id,
                "task.approved",
                {"actor_id": event.actor_id, "source": event.source},
            )
            await self._notify(task, f"Task `{task.id}` approved and queued.")
            await self._signal_status_by_id(task, TASK_STATUS_PENDING)
            return f"Task `{task.id}` approved."

        if event.action == "reject":
            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_REJECTED,
                ended_at_now=True,
                summary="Rejected by user.",
            )
            await self._store.add_runtime_event(
                task.id,
                "task.rejected",
                {"actor_id": event.actor_id, "source": event.source},
            )
            await self._notify(task, f"Task `{task.id}` rejected.")
            await self._signal_status_by_id(task, TASK_STATUS_REJECTED)
            return f"Task `{task.id}` rejected."

        if event.action == "suggest":
            suggestion = (event.suggestion or "").strip()
            new_nonce = await self._store.create_runtime_decision_nonce(
                task.id,
                ttl_minutes=self._decision_ttl_minutes,
            )
            await self._store.update_runtime_task(
                task.id,
                resume_instruction=suggestion or task.resume_instruction,
            )
            await self._store.add_runtime_event(
                task.id,
                "task.suggested",
                {"actor_id": event.actor_id, "source": event.source, "suggestion": suggestion},
            )
            session = self._session_for(task)
            if session is not None:
                suggestion_preview = suggestion or task.resume_instruction or "(none)"
                suggest_text = (
                    f"### Runtime Task `{task.id}` — Suggestion Recorded\n"
                    f"> {suggestion_preview}\n\n"
                    "Approve to run with this guidance, or reject to discard."
                )
                await self._send_decision_surface(
                    session,
                    event.thread_id,
                    suggest_text,
                    task.id,
                    new_nonce,
                    ["approve", "reject"],
                )
            return f"Task `{task.id}` suggestion recorded."

        if event.action == "merge":
            return await self._execute_merge(task, actor_id=event.actor_id, source=event.source)

        if event.action == "discard":
            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_DISCARDED,
                summary="Discarded by user.",
                ended_at_now=True,
            )
            await self._store.add_runtime_event(
                task.id,
                "task.discarded",
                {"actor_id": event.actor_id, "source": event.source},
            )
            await self._notify(task, f"Task `{task.id}` discarded.")
            await self._signal_status_by_id(task, TASK_STATUS_DISCARDED)
            return f"Task `{task.id}` discarded."

        # request_changes: move back to BLOCKED and keep suggestion as resume hint.
        suggestion = (event.suggestion or "").strip()
        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_BLOCKED,
            blocked_reason="Requested changes before merge.",
            resume_instruction=suggestion or task.resume_instruction,
            ended_at=None,
        )
        await self._store.add_runtime_event(
            task.id,
            "task.request_changes",
            {"actor_id": event.actor_id, "source": event.source, "suggestion": suggestion},
        )
        await self._notify(
            task,
            (
                f"Task `{task.id}` marked as BLOCKED for additional changes. "
                f"Use `/task_resume {task.id} <instruction>` to continue."
            ),
        )
        await self._signal_status_by_id(task, TASK_STATUS_BLOCKED)
        return f"Task `{task.id}` moved to BLOCKED."

    async def build_slash_decision_event(
        self,
        *,
        platform: str,
        channel_id: str,
        thread_id: str,
        task_id: str,
        action: str,
        actor_id: str,
        suggestion: str | None = None,
    ) -> TaskDecisionEvent | None:
        nonce = await self._store.get_active_runtime_decision_nonce(task_id)
        if not nonce:
            nonce = await self._store.create_runtime_decision_nonce(
                task_id,
                ttl_minutes=self._decision_ttl_minutes,
            )
        return TaskDecisionEvent(
            platform=platform,
            channel_id=channel_id,
            thread_id=thread_id,
            task_id=task_id,
            action=action,  # type: ignore[arg-type]
            actor_id=actor_id,
            nonce=nonce,
            source="slash",
            suggestion=suggestion,
        )

    async def _worker_loop(self, idx: int) -> None:
        while not self._stop_event.is_set():
            try:
                task = await self._store.claim_pending_runtime_task()
                if task is None:
                    await asyncio.sleep(0.8)
                    continue
                logger.info("Runtime worker=%d claimed task=%s", idx, task.id)
                await self._run_task(task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Runtime worker %s crashed: %s", idx, exc)
                await asyncio.sleep(1.5)

    async def _janitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                cleaned = await self._cleanup_expired_tasks()
                if cleaned:
                    logger.info("Runtime janitor cleaned %d expired task workspace(s)", cleaned)
                await asyncio.sleep(max(1, self._cleanup_interval_minutes) * 60)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Runtime janitor failed: %s", exc)

    async def _run_task(self, task: RuntimeTask) -> None:
        session = self._session_for(task)
        registry = self._registry_for(task)
        if session is None or registry is None:
            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_BLOCKED,
                blocked_reason="No active session/registry for platform+channel.",
            )
            return

        try:
            workspace = await self._worktree.ensure_worktree(task.id)
        except WorktreeError as exc:
            await self._fail(task, f"Failed to prepare worktree: {exc}")
            return

        await self._store.update_runtime_task(task.id, workspace_path=str(workspace))
        logger.info(
            "Runtime task=%s start workspace=%s goal=%r",
            task.id,
            workspace,
            task.goal[:140],
        )
        await self._store.add_runtime_event(
            task.id,
            "task.started",
            {"workspace": str(workspace), "goal": task.goal[:200]},
        )
        await self._notify(task, f"Task `{task.id}` started. Workspace is ready; entering autonomous loop.")
        await self._signal_status_by_id(task, TASK_STATUS_RUNNING)

        start = time.monotonic()
        step = task.step_no
        prior_failure: str | None = None
        total_agent_s = 0.0
        total_test_s = 0.0
        last_agent_name: str = task.preferred_agent or self._default_agent or ""
        latest = await self._store.get_last_runtime_checkpoint(task.id)
        if latest:
            prior_failure = latest.get("test_result")

        while step < task.max_steps:
            current = await self._store.get_runtime_task(task.id)
            if current is None:
                return
            if current.status in {TASK_STATUS_STOPPED, TASK_STATUS_PAUSED}:
                return
            if (time.monotonic() - start) > (task.max_minutes * 60):
                await self._store.update_runtime_task(
                    task.id,
                    status=TASK_STATUS_TIMEOUT,
                    ended_at_now=True,
                    summary="Task exceeded runtime budget.",
                )
                await self._notify(task, f"Task `{task.id}` timed out.")
                await self._signal_status_by_id(task, TASK_STATUS_TIMEOUT)
                return

            step += 1
            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_RUNNING,
                step_no=step,
            )
            logger.info(
                "Runtime task=%s step=%d/%d status=RUNNING",
                task.id,
                step,
                task.max_steps,
            )
            await self._store.add_runtime_event(
                task.id,
                "task.phase",
                {"step": step, "phase": "agent_running"},
            )
            await self._notify(
                task,
                f"Task `{task.id}` step {step}/{task.max_steps}: running agent `{task.preferred_agent or self._default_agent}`.",
            )
            if task.task_type == TASK_TYPE_SKILL and task.skill_name:
                prompt = build_skill_prompt(
                    skill_name=task.skill_name,
                    goal=task.goal,
                    original_request=current.original_request,
                    step_no=step,
                    max_steps=task.max_steps,
                    prior_failure=prior_failure,
                    resume_instruction=current.resume_instruction,
                )
            else:
                prompt = build_runtime_prompt(
                    goal=task.goal,
                    original_request=current.original_request,
                    step_no=step,
                    max_steps=task.max_steps,
                    prior_failure=prior_failure,
                    resume_instruction=current.resume_instruction,
                )

            t_agent = time.perf_counter()
            agent_name, response = await self._run_agent(
                registry=registry,
                task=task,
                prompt=prompt,
                workspace=workspace,
                step=step,
            )
            last_agent_name = agent_name
            elapsed_agent = time.perf_counter() - t_agent
            total_agent_s += elapsed_agent
            if response.error:
                # If the task was stopped or paused externally, don't overwrite its status.
                current_after = await self._store.get_runtime_task(task.id)
                if current_after and current_after.status in {TASK_STATUS_STOPPED, TASK_STATUS_PAUSED}:
                    return
                await self._fail(task, f"{agent_name}: {response.error}")
                return

            state, block_reason = parse_task_state(response.text)
            logger.info(
                "Runtime task=%s step=%d AGENT_OK agent=%s elapsed=%.2fs response_len=%d state=%s",
                task.id,
                step,
                agent_name,
                elapsed_agent,
                len(response.text),
                state,
            )
            changed_files = await self._worktree.changed_files(workspace)
            guard_error = self._validate_changed_paths(changed_files)
            if guard_error:
                await self._fail(task, guard_error)
                return

            await self._store.update_runtime_task(task.id, status=TASK_STATUS_VALIDATING)
            logger.info(
                "Runtime task=%s step=%d status=VALIDATING test=%r changed=%d",
                task.id,
                step,
                task.test_command,
                len(changed_files),
            )
            await self._store.add_runtime_event(
                task.id,
                "task.phase",
                {"step": step, "phase": "test_running", "command": task.test_command},
            )
            await self._notify(
                task,
                f"Task `{task.id}` step {step}: agent finished. Running tests: `{task.test_command}`",
            )
            await self._signal_status_by_id(task, TASK_STATUS_VALIDATING)
            test_notice_state = {"last_notice": 0.0, "last_persist": 0.0}

            async def _on_test_heartbeat(elapsed: float) -> None:
                logger.info(
                    "Runtime task=%s step=%d TEST_RUNNING elapsed=%.2fs command=%r",
                    task.id,
                    step,
                    elapsed,
                    task.test_command,
                )
                if elapsed - test_notice_state["last_persist"] >= self._progress_persist_seconds:
                    test_notice_state["last_persist"] = elapsed
                    await self._store.add_runtime_event(
                        task.id,
                        "task.test_progress",
                        {"step": step, "elapsed_seconds": round(elapsed, 2), "command": task.test_command},
                    )
                if elapsed - test_notice_state["last_notice"] >= self._progress_notice_seconds:
                    test_notice_state["last_notice"] = elapsed
                    await self._notify(
                        task,
                        f"Task `{task.id}` step {step}: tests still running ({int(elapsed)}s elapsed).",
                    )

            t_test = time.perf_counter()
            rc, out, err, test_timed_out = await self._worktree.run_shell(
                workspace,
                task.test_command,
                timeout_seconds=self._test_timeout_seconds,
                heartbeat_seconds=self._test_heartbeat_seconds,
                on_heartbeat=_on_test_heartbeat,
            )
            total_test_s += time.perf_counter() - t_test
            test_ok = rc == 0
            test_summary = (out + ("\n" + err if err else "")).strip()
            if not test_summary:
                test_summary = f"exit={rc}"
            test_display = self._format_test_output(test_summary)
            logger.info(
                "Runtime task=%s step=%d TEST_DONE rc=%d",
                task.id,
                step,
                rc,
            )
            if test_timed_out:
                timeout_msg = (
                    f"Test command exceeded timeout ({int(self._test_timeout_seconds)}s). "
                    f"Recent output:\n{test_display}"
                )
                await self._store.add_runtime_event(
                    task.id,
                    "task.test_timeout",
                    {"step": step, "timeout_seconds": self._test_timeout_seconds},
                )
                await self._store.update_runtime_task(
                    task.id,
                    status=TASK_STATUS_TIMEOUT,
                    ended_at_now=True,
                    summary="Test command timed out.",
                    error=timeout_msg[:2000],
                )
                await self._notify(task, f"Task `{task.id}` timed out during tests.\n```text\n{test_display}\n```")
                await self._signal_status_by_id(task, TASK_STATUS_TIMEOUT)
                return

            await self._store.add_runtime_checkpoint(
                task_id=task.id,
                step_no=step,
                status=TASK_STATUS_VALIDATING,
                prompt_digest=prompt[:500],
                agent_result=response.text[:4000],
                test_result=test_summary[:2000],
                files_changed=changed_files,
            )
            await self._store.add_runtime_event(
                task.id,
                "task.step",
                {
                    "step": step,
                    "agent": agent_name,
                    "test_exit_code": rc,
                    "changed_files": changed_files,
                    "test_output_tail": test_display,
                },
            )

            if (
                test_ok
                and state == "BLOCKED"
                and self._should_ignore_agent_block(response.text, block_reason)
            ):
                override_state = "DONE" if changed_files else "CONTINUE"
                await self._store.add_runtime_event(
                    task.id,
                    "task.block_override",
                    {
                        "step": step,
                        "from_state": "BLOCKED",
                        "to_state": override_state,
                        "reason": "runtime_test_authoritative",
                    },
                )
                logger.info(
                    "Runtime task=%s step=%d overriding agent BLOCKED -> %s because runtime tests passed",
                    task.id,
                    step,
                    override_state,
                )
                state = override_state
                block_reason = None

            if state == "BLOCKED":
                await self._store.update_runtime_task(
                    task.id,
                    status=TASK_STATUS_BLOCKED,
                    blocked_reason=block_reason or "Agent reported blocked.",
                )
                logger.info(
                    "Runtime task=%s BLOCKED reason=%r",
                    task.id,
                    block_reason or "unknown reason",
                )
                await self._notify(
                    task,
                    (
                        f"Task `{task.id}` blocked: {block_reason or 'unknown reason'}\n"
                        f"Provide missing context and resume with `/task_resume {task.id} <instruction>`."
                    ),
                )
                await self._signal_status_by_id(task, TASK_STATUS_BLOCKED)
                return

            if test_ok and state == "DONE":
                total_elapsed_s = time.monotonic() - start
                summary = self._build_completion_summary(
                    task=task,
                    step=step,
                    changed_files=changed_files,
                    test_summary=test_summary,
                    total_agent_s=total_agent_s,
                    total_test_s=total_test_s,
                    total_elapsed_s=total_elapsed_s,
                    waiting_merge=self._merge_gate_enabled,
                )
                if self._merge_gate_enabled:
                    new_state = TASK_STATUS_WAITING_MERGE
                else:
                    new_state = TASK_STATUS_APPLIED

                await self._store.update_runtime_task(
                    task.id,
                    status=new_state,
                    ended_at_now=True,
                    summary=summary,
                    blocked_reason=None,
                    merge_error=None,
                )
                await self._store.add_runtime_event(
                    task.id,
                    "task.completed",
                    {
                        "status": new_state,
                        "step": step,
                        "total_agent_s": round(total_agent_s, 2),
                        "total_test_s": round(total_test_s, 2),
                        "total_elapsed_s": round(total_elapsed_s, 2),
                    },
                )

                if self._merge_gate_enabled:
                    merge_nonce = await self._store.create_runtime_decision_nonce(
                        task.id,
                        ttl_minutes=self._decision_ttl_minutes,
                    )
                    refreshed = await self._store.get_runtime_task(task.id)
                    merge_task = refreshed or task
                    text = await self._merge_gate_text(merge_task)
                    msg_id = await self._send_decision_surface(
                        session,
                        task.thread_id,
                        text,
                        task.id,
                        merge_nonce,
                        ["merge", "discard", "request_changes"],
                    )
                    if msg_id:
                        await self._store.update_runtime_task(task.id, decision_message_id=msg_id)
                    await self._notify(
                        task,
                        f"Task `{task.id}` completed in workspace and is waiting merge decision.",
                    )
                    await self._signal_status_by_id(task, TASK_STATUS_WAITING_MERGE)
                    logger.info("Runtime task=%s WAITING_MERGE step=%d", task.id, step)
                    return

                logger.info("Runtime task=%s APPLIED step=%d", task.id, step)
                await self._notify(task, f"Task `{task.id}` completed successfully.")
                await self._signal_status_by_id(task, TASK_STATUS_APPLIED)
                return

            prior_failure = test_summary if not test_ok else None

        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_TIMEOUT,
            ended_at_now=True,
            summary="Task exceeded step budget.",
        )
        logger.info("Runtime task=%s TIMEOUT max_steps=%d", task.id, task.max_steps)
        await self._notify(task, f"Task `{task.id}` reached max steps and stopped.")
        await self._signal_status_by_id(task, TASK_STATUS_TIMEOUT)

    async def _run_agent(
        self,
        *,
        registry: AgentRegistry,
        task: RuntimeTask,
        prompt: str,
        workspace: Path,
        step: int,
    ) -> tuple[str, AgentResponse]:
        if task.preferred_agent:
            forced = registry.get_agent(task.preferred_agent)
            if forced is not None:
                response = await self._invoke_agent(forced, prompt, workspace, task.id, task, step)
                return forced.name, response

        last_name = registry.agents[-1].name
        last_response = AgentResponse(text="", error="No agents available.")
        for agent in registry.agents:
            response = await self._invoke_agent(agent, prompt, workspace, task.id, task, step)
            if not response.error:
                return agent.name, response
            last_name = agent.name
            last_response = response
        return last_name, last_response

    async def _invoke_agent(
        self,
        agent,
        prompt: str,
        workspace: Path,
        runtime_thread_id: str,
        task: RuntimeTask,
        step: int,
    ) -> AgentResponse:
        sig = inspect.signature(agent.run)
        kwargs = {}
        if "thread_id" in sig.parameters:
            kwargs["thread_id"] = runtime_thread_id
        if "workspace_override" in sig.parameters:
            kwargs["workspace_override"] = workspace
        run_task = asyncio.create_task(agent.run(prompt, [], **kwargs))
        self._running_tasks[task.id] = run_task
        started = asyncio.get_running_loop().time()
        last_notice = 0.0
        last_persist = 0.0
        try:
            while True:
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(run_task),
                        timeout=self._agent_heartbeat_seconds,
                    )
                except asyncio.TimeoutError:
                    elapsed = asyncio.get_running_loop().time() - started
                    # Check if user stopped or paused mid-run
                    current = await self._store.get_runtime_task(task.id)
                    if current and current.status in {TASK_STATUS_STOPPED, TASK_STATUS_PAUSED}:
                        run_task.cancel()
                        reason = "paused" if current.status == TASK_STATUS_PAUSED else "stopped"
                        return AgentResponse(text="", error=f"Task {reason} by user.")
                    logger.info(
                        "Runtime task=%s step=%d AGENT_RUNNING agent=%s elapsed=%.2fs",
                        task.id,
                        step,
                        agent.name,
                        elapsed,
                    )
                    if elapsed - last_persist >= self._progress_persist_seconds:
                        last_persist = elapsed
                        await self._store.add_runtime_event(
                            task.id,
                            "task.agent_progress",
                            {"step": step, "agent": agent.name, "elapsed_seconds": round(elapsed, 2)},
                        )
                    if elapsed - last_notice >= self._progress_notice_seconds:
                        last_notice = elapsed
                        await self._notify(
                            task,
                            f"Task `{task.id}` step {step}: agent `{agent.name}` still running ({int(elapsed)}s elapsed).",
                        )
        finally:
            self._running_tasks.pop(task.id, None)

    async def _execute_merge(self, task: RuntimeTask, *, actor_id: str, source: str) -> str:
        if task.status not in {TASK_STATUS_WAITING_MERGE, TASK_STATUS_APPLIED}:
            return f"Task `{task.id}` is not waiting merge (status: {task.status})."
        if not self._merge_gate_enabled:
            return "Merge gate is disabled."
        if self._merge_target_branch_mode != "current":
            return "Only target_branch_mode=current is supported in v0.5.2."

        workspace = Path(task.workspace_path) if task.workspace_path else None
        if workspace is None or not workspace.exists():
            return await self._mark_merge_failed(task, "Workspace path is missing; cannot build patch.")

        try:
            if self._merge_require_clean_repo and not await self._worktree.repo_is_clean():
                return await self._mark_merge_failed(
                    task,
                    "Main repository is not clean. Commit/stash changes before merging runtime task.",
                )

            patch = await self._worktree.create_patch(workspace)
            if not patch.strip():
                return await self._mark_merge_failed(task, "No patch produced from task workspace.")

            if self._merge_preflight_check:
                await self._worktree.apply_patch_check(patch)
            await self._worktree.apply_patch(patch)

            commit_hash: str | None = None
            if self._merge_auto_commit:
                msg = self._merge_commit_template.format(
                    task_id=task.id,
                    goal_short=self._goal_short(task.goal),
                )
                commit_hash = await self._worktree.commit_repo_changes(msg)

            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_MERGED,
                merge_commit_hash=commit_hash,
                merge_error=None,
                summary="Merged into current branch.",
                ended_at_now=True,
            )
            await self._store.add_runtime_event(
                task.id,
                "task.merged",
                {
                    "actor_id": actor_id,
                    "source": source,
                    "commit_hash": commit_hash,
                    "auto_commit": self._merge_auto_commit,
                },
            )
            if task.task_type == TASK_TYPE_SKILL:
                await self._on_skill_task_merged(
                    task,
                    await self._resolve_last_agent_name(task),
                    commit_hash or "",
                )
            extra = f" commit `{commit_hash}`" if commit_hash else ""
            logger.info("Runtime task=%s MERGED commit=%s", task.id, commit_hash or "none")
            if self._cleanup_merged_immediately:
                cleaned = await self._cleanup_single_task(task)
                if cleaned:
                    logger.info("Runtime task=%s workspace cleaned immediately after merge", task.id)
                    if self._cleanup_prune_worktrees:
                        try:
                            await self._worktree.prune_worktrees()
                        except Exception:
                            logger.debug("git worktree prune failed after immediate merge cleanup", exc_info=True)
            merged_note = f"Task `{task.id}` merged successfully.{extra}"
            if task.task_type == TASK_TYPE_SKILL and task.skill_name:
                merged_note += f" Skill `{task.skill_name}` merged. Run `/reload-skills` to activate."
            await self._notify(task, merged_note)
            await self._signal_status_by_id(task, TASK_STATUS_MERGED)
            return merged_note
        except WorktreeError as exc:
            return await self._mark_merge_failed(task, str(exc))
        except Exception as exc:
            return await self._mark_merge_failed(task, f"Unexpected merge error: {exc}")

    async def _mark_merge_failed(self, task: RuntimeTask, error: str) -> str:
        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_MERGE_FAILED,
            merge_error=error[:2000],
            summary="Merge failed.",
            ended_at_now=True,
        )
        await self._store.add_runtime_event(task.id, "task.merge_failed", {"error": error[:1000]})
        logger.error("Runtime task=%s MERGE_FAILED error=%s", task.id, error[:600])
        await self._notify(task, f"Task `{task.id}` merge failed: {error[:400]}")
        await self._signal_status_by_id(task, TASK_STATUS_MERGE_FAILED)
        return f"Task `{task.id}` merge failed: {error[:200]}"

    async def _cleanup_expired_tasks(self) -> int:
        candidates: list[RuntimeTask] = []
        delayed_statuses = sorted(
            status
            for status in _TERMINAL_CLEANUP_STATUSES
            if not (self._cleanup_merged_immediately and status == TASK_STATUS_MERGED)
        )
        if delayed_statuses:
            candidates.extend(
                await self._store.list_runtime_cleanup_candidates(
                    statuses=delayed_statuses,
                    older_than_hours=self._cleanup_retention_hours,
                    limit=200,
                )
            )
        if self._cleanup_merged_immediately:
            candidates.extend(
                await self._store.list_runtime_cleanup_candidates(
                    statuses=[TASK_STATUS_MERGED],
                    older_than_hours=0,
                    limit=200,
                )
            )
        cleaned = 0
        seen: set[str] = set()
        for task in candidates:
            if task.id in seen:
                continue
            seen.add(task.id)
            if await self._cleanup_single_task(task):
                cleaned += 1
        if cleaned and self._cleanup_prune_worktrees:
            try:
                await self._worktree.prune_worktrees()
            except Exception:
                logger.debug("git worktree prune failed", exc_info=True)
        return cleaned

    async def _cleanup_single_task(self, task: RuntimeTask) -> bool:
        if not task.workspace_path:
            return False
        workspace = Path(task.workspace_path)
        if workspace.exists():
            try:
                await self._worktree.remove_worktree(workspace)
            except Exception as exc:
                logger.warning("Failed to remove worktree for task=%s: %s", task.id, exc)
                return False
        await self._store.update_runtime_task(
            task.id,
            workspace_path=None,
            workspace_cleaned_at="__NOW__",
        )
        await self._store.add_runtime_event(task.id, "task.workspace_cleaned", {"workspace": str(workspace)})
        return True

    async def _fail(self, task: RuntimeTask, error: str) -> None:
        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_FAILED,
            error=error[:2000],
            ended_at_now=True,
        )
        await self._store.add_runtime_event(task.id, "task.failed", {"error": error[:1000]})
        logger.error("Runtime task=%s FAILED error=%s", task.id, error[:600])
        await self._notify(task, f"Task `{task.id}` failed: {error[:400]}")
        await self._signal_status_by_id(task, TASK_STATUS_FAILED)

    async def _on_skill_task_merged(
        self,
        task: RuntimeTask,
        agent_name: str,
        merge_commit_hash: str,
    ) -> None:
        if self._skill_syncer is not None:
            try:
                self._skill_syncer.sync()
            except Exception as exc:
                logger.warning("Post-merge skill sync failed for task %s: %s", task.id, exc)

        warnings: list[str] = []
        if self._skills_path and task.skill_name:
            try:
                from oh_my_agent.skills.validator import SkillValidator

                result = SkillValidator().validate(self._skills_path / task.skill_name)
                warnings = result.warnings
            except Exception:
                logger.debug("SkillValidator failed after merge for task %s", task.id, exc_info=True)

        if task.skill_name:
            await self._store.upsert_skill_provenance(
                task.skill_name,
                source_task_id=task.id,
                created_by=task.created_by,
                agent_name=agent_name,
                platform=task.platform,
                channel_id=task.channel_id,
                thread_id=task.thread_id,
                validation_mode="quick_validate",
                validated=1,
                validation_warnings=warnings,
                merged_commit_hash=merge_commit_hash or None,
            )

    async def _resolve_last_agent_name(self, task: RuntimeTask) -> str:
        events = await self._store.list_runtime_events(task.id, limit=20)
        for event in reversed(events):
            payload = event.get("payload", {})
            agent = payload.get("agent")
            if isinstance(agent, str) and agent:
                return agent
        return task.preferred_agent or self._default_agent or ""

    def _validate_changed_paths(self, paths: list[str]) -> str | None:
        for raw in paths:
            path = raw.replace("\\", "/")
            if any(fnmatch.fnmatch(path, pat) for pat in self._denied_paths):
                return f"Changed forbidden path: {path}"
            if self._path_policy_mode == "allow_all_with_denylist":
                continue
            if self._allowed_paths and not any(fnmatch.fnmatch(path, pat) for pat in self._allowed_paths):
                return f"Changed path outside allow-list: {path}"
        return None

    async def _send_decision_surface(
        self,
        session: ChannelSession,
        thread_id: str,
        text: str,
        task_id: str,
        nonce: str,
        actions: list[str],
    ) -> str | None:
        sender = getattr(session.channel, "send_task_draft", None)
        if sender and callable(sender):
            try:
                return await sender(
                    thread_id=thread_id,
                    draft_text=text,
                    task_id=task_id,
                    nonce=nonce,
                    actions=actions,
                )
            except Exception as exc:
                logger.warning("send_task_draft failed, falling back to plain text: %s", exc)
        await session.channel.send(thread_id, text)
        return None

    async def _notify(self, task: RuntimeTask, text: str) -> None:
        session = self._session_for(task)
        if session is None:
            return
        current = await self._store.get_runtime_task(task.id)
        status_message_id = current.status_message_id if current else task.status_message_id
        body = self._format_status_message(text)
        upsert = getattr(session.channel, "upsert_status_message", None)
        if upsert and callable(upsert):
            msg_id = await upsert(task.thread_id, body[:1900], message_id=status_message_id)
        else:
            msg_id = await session.channel.send(task.thread_id, body[:1900])
        if msg_id:
            if not current or current.status_message_id != msg_id:
                await self._store.update_runtime_task(task.id, status_message_id=msg_id)

    async def _signal_status_by_id(self, task: RuntimeTask, status: str) -> None:
        emoji = self._emoji_for_status(status)
        if not emoji:
            return
        session = self._session_for(task)
        if session is None:
            return
        current = await self._store.get_runtime_task(task.id)
        message_id = None
        if current:
            message_id = current.status_message_id or current.decision_message_id
        else:
            message_id = task.status_message_id or task.decision_message_id
        if not message_id:
            return
        signaler = getattr(session.channel, "signal_task_status", None)
        if signaler and callable(signaler):
            try:
                await signaler(task.thread_id, message_id, emoji)
            except Exception:
                logger.debug("signal_task_status failed for task %s", task.id, exc_info=True)

    def _session_for(self, task: RuntimeTask) -> ChannelSession | None:
        return self._sessions.get(self._key(task.platform, task.channel_id))

    def _registry_for(self, task: RuntimeTask) -> AgentRegistry | None:
        return self._registries.get(self._key(task.platform, task.channel_id))

    @staticmethod
    def _key(platform: str, channel_id: str) -> str:
        return f"{platform}:{channel_id}"

    def _is_authorized(self, actor_id: str) -> bool:
        if not self._owner_user_ids:
            return True
        return actor_id in self._owner_user_ids

    def _tail_text(self, text: str) -> str:
        if not text:
            return ""
        text = text.strip()
        if len(text) <= self._log_tail_chars:
            return text
        return text[-self._log_tail_chars :]

    def _format_test_output(self, text: str) -> str:
        if not text:
            return ""
        summary = self._summarize_pytest_output(text)
        if summary:
            return summary[: self._log_tail_chars]
        return self._tail_text(text)

    def _summarize_pytest_output(self, text: str) -> str:
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""

        summary_line = ""
        summary_re = re.compile(r"\b\d+\s+passed\b|\b\d+\s+failed\b|\b\d+\s+error\b|\b\d+\s+errors\b|\b\d+\s+skipped\b")
        for line in reversed(lines):
            cleaned = line.strip().strip("=").strip()
            if summary_re.search(cleaned) and " in " in cleaned:
                summary_line = cleaned
                break

        failure_lines: list[str] = []
        seen: set[str] = set()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("FAILED ", "ERROR ")):
                if stripped not in seen:
                    seen.add(stripped)
                    failure_lines.append(stripped)
            elif re.match(r"^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Failure):", stripped):
                if stripped not in seen:
                    seen.add(stripped)
                    failure_lines.append(stripped)

        if summary_line and not failure_lines:
            return summary_line

        parts: list[str] = []
        if summary_line:
            parts.append(f"Summary: {summary_line}")
        parts.extend(failure_lines[:4])
        if parts:
            return "\n".join(parts)
        return ""

    @staticmethod
    def _should_ignore_agent_block(agent_text: str, block_reason: str | None) -> bool:
        reason = (block_reason or "").strip()
        hay = reason.lower() if reason else (agent_text or "").lower()
        if not hay:
            return False

        positive_hints = (
            "sandbox",
            "socket-bind",
            "127.0.0.1",
            "permissionerror",
            "operation not permitted",
            "environment-specific",
        )
        negative_hints = (
            "missing content",
            "missing context",
            "missing dependency",
            "missing file",
            "missing api key",
            "missing credential",
            "need user input",
        )
        return any(hint in hay for hint in positive_hints) and not any(hint in hay for hint in negative_hints)

    @staticmethod
    def _format_status_message(text: str) -> str:
        return f"{_STATUS_MESSAGE_PREFIX}\n{text}"

    @staticmethod
    def _summarize_event_payload(payload: dict[str, Any]) -> str:
        if not payload:
            return ""
        interesting = []
        for key in (
            "phase",
            "step",
            "agent",
            "elapsed_seconds",
            "test_exit_code",
            "timeout_seconds",
            "command",
            "status",
            "error",
        ):
            if key in payload and payload[key] not in (None, ""):
                interesting.append(f"{key}={payload[key]}")
        return ", ".join(interesting)[:220]

    def _draft_text(self, task: RuntimeTask, *, reasons: list[str]) -> str:
        reason_text = ", ".join(reasons) if reasons else "requires explicit approval"
        return (
            f"### Runtime Task Draft `{task.id}`\n"
            f"Goal: {task.goal}\n"
            f"Agent: `{task.preferred_agent or self._default_agent}`\n"
            f"Budget: {task.max_steps} steps / {task.max_minutes} min\n"
            f"Test command: `{task.test_command}`\n"
            f"Reason: {reason_text}\n"
            "Use Approve / Reject / Suggest."
        )

    async def _merge_gate_text(self, task: RuntimeTask) -> str:
        lines = [
            f"### Runtime Task `{task.id}` Ready to Merge",
            f"Goal: {task.goal[:220]}",
            f"Agent: `{task.preferred_agent or self._default_agent}`",
            f"Completed step: {task.step_no}/{task.max_steps}",
            f"Test command: `{task.test_command}`",
        ]

        changes = await self._collect_task_changes(task, limit=10)
        if changes:
            lines.append("")
            lines.append("Changed files:")
            lines.extend(f"- `{line}`" for line in changes[:8])
            if len(changes) > 8:
                lines.append(f"- ... and {len(changes) - 8} more")

        ckpt = await self._store.get_last_runtime_checkpoint(task.id)
        if ckpt:
            test_tail = self._format_test_output(str(ckpt.get("test_result", "")))
            if test_tail:
                lines.append("")
                lines.append("Latest test result:")
                lines.append(f"```text\n{test_tail[:500]}\n```")

        lines.extend(
            [
                "",
                "Choose one action:",
                "- Merge: apply patch to current branch and auto commit",
                "- Discard: keep audit metadata, drop this task result",
                "- Request Changes: send task back to BLOCKED for another iteration",
                "",
                "Use `/task_changes` or `/task_logs` for full details, if available.",
            ]
        )
        return "\n".join(lines)[:1900]

    async def _collect_task_changes(self, task: RuntimeTask, *, limit: int = 80) -> list[str]:
        changes: list[str] = []
        if task.workspace_path:
            workspace = Path(task.workspace_path)
            if workspace.exists():
                try:
                    changes = await self._worktree.list_workspace_changes(workspace, limit=limit)
                except Exception as exc:
                    logger.warning("Failed to list workspace changes for %s: %s", task.id, exc)

        if changes:
            return changes[:limit]

        ckpt = await self._store.get_last_runtime_checkpoint(task.id)
        raw = ckpt.get("files_changed_json") if ckpt else None
        if not raw:
            return []
        try:
            files = json.loads(raw)
        except Exception:
            return []
        return [f"M\t{p}" for p in files][:limit]

    @staticmethod
    def _goal_short(goal: str) -> str:
        one_line = " ".join(goal.strip().split())
        return one_line[:72] if one_line else "task"

    @staticmethod
    def _emoji_for_status(status: str) -> str | None:
        if status == TASK_STATUS_RUNNING:
            return "👀"
        if status == TASK_STATUS_VALIDATING:
            return "🧪"
        if status in {TASK_STATUS_DRAFT, TASK_STATUS_PENDING, TASK_STATUS_WAITING_MERGE}:
            return "⏳"
        if status in {TASK_STATUS_MERGED, TASK_STATUS_APPLIED}:
            return "✅"
        if status == TASK_STATUS_DISCARDED:
            return "🗑️"
        if status == TASK_STATUS_PAUSED:
            return "⏸️"
        if status in {
            TASK_STATUS_BLOCKED,
            TASK_STATUS_FAILED,
            TASK_STATUS_TIMEOUT,
            TASK_STATUS_STOPPED,
            TASK_STATUS_REJECTED,
            TASK_STATUS_MERGE_FAILED,
        }:
            return "⚠️"
        return None

    @staticmethod
    def _parse_control_intent(text: str) -> tuple[str, str] | None:
        """Return (action, instruction) if text is a runtime control command, else None."""
        stripped = text.strip()
        lower = stripped.lower()
        if lower in {"stop", "stop the task", "cancel"}:
            return ("stop", "")
        if lower in {"pause", "pause the task"}:
            return ("pause", "")
        for prefix in ("resume ", "continue "):
            if lower.startswith(prefix):
                return ("resume", stripped[len(prefix):].strip())
        return None

    async def _active_task_for_thread(
        self, platform: str, channel_id: str, thread_id: str
    ) -> "RuntimeTask | None":
        """Return the most recent active task in the given thread, or None."""
        active_statuses = {
            TASK_STATUS_RUNNING,
            TASK_STATUS_VALIDATING,
            TASK_STATUS_BLOCKED,
            TASK_STATUS_PAUSED,
            TASK_STATUS_PENDING,
        }
        tasks = await self._store.list_runtime_tasks(
            platform=platform,
            channel_id=channel_id,
            limit=20,
        )
        for task in tasks:
            if task.thread_id == thread_id and task.status in active_statuses:
                return task
        return None

    @staticmethod
    def _build_completion_summary(
        task: RuntimeTask,
        step: int,
        changed_files: list[str],
        test_summary: str,
        total_agent_s: float,
        total_test_s: float,
        total_elapsed_s: float,
        waiting_merge: bool,
    ) -> str:
        parts: list[str] = []
        goal_short = " ".join(task.goal.strip().split())[:120]
        parts.append(f"Goal: {goal_short}")
        parts.append(f"Completed in {step} step(s)")

        # Changed files
        if changed_files:
            shown = changed_files[:10]
            parts.append(f"Changed files ({len(changed_files)}): " + ", ".join(f"`{f}`" for f in shown))
            if len(changed_files) > 10:
                parts[-1] += f" and {len(changed_files) - 10} more"

        # Test result excerpt
        if test_summary:
            summary_re = re.compile(
                r"\b\d+\s+passed\b|\b\d+\s+failed\b|\b\d+\s+error\b|\b\d+\s+errors\b|\b\d+\s+skipped\b"
            )
            for line in reversed(test_summary.splitlines()):
                cleaned = line.strip().strip("=").strip()
                if summary_re.search(cleaned) and " in " in cleaned:
                    parts.append(f"Tests: {cleaned}")
                    break

        # Latency metrics
        parts.append(
            f"Timing: agent {total_agent_s:.1f}s | tests {total_test_s:.1f}s | total {total_elapsed_s:.1f}s"
        )

        if waiting_merge:
            parts.append("Waiting merge confirmation.")
        return " | ".join(parts)
