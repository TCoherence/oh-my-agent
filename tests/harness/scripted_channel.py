"""Scripted/headless BaseChannel implementation for end-to-end harness tests.

Replaces the Discord transport so the GatewayManager + RuntimeService stack
can be driven by yaml scenarios. Records every outgoing channel call as a
typed ``ChannelEvent`` so assertions can run against structured fields
instead of substring-matching log output.

Cross-platform contract: this file MUST NOT import or depend on
``DiscordChannel``. Anything GatewayManager / RuntimeService call on the
channel must work via the ``BaseChannel`` ABC alone — that's how the
harness validates the abstraction itself, not just Discord.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Any

from oh_my_agent.gateway.base import (
    BaseChannel,
    IncomingMessage,
    InteractivePrompt,
    MessageHandler,
    OutgoingAttachment,
)
from oh_my_agent.gateway.services.automation_service import AutomationService
from oh_my_agent.gateway.services.doctor_service import DoctorService
from oh_my_agent.gateway.services.memory_service import MemoryService
from oh_my_agent.gateway.services.skill_eval_service import SkillEvalService
from oh_my_agent.gateway.services.task_service import TaskService
from oh_my_agent.gateway.services.types import TaskActionResult, TaskListResult

logger = logging.getLogger(__name__)


@dataclass
class ChannelEvent:
    """Typed record of one outgoing channel side-effect."""

    seq: int
    type: str          # "send" | "send_attachment" | "send_dm" | "edit_message" | "upsert_status_message" | "send_task_draft" | "signal_task_status" | "send_hitl_prompt" | "send_interactive" | "create_thread" | "create_followup_thread" | "typing_open" | "typing_close" | "stop"
    thread_id: str | None
    payload: dict[str, Any]
    timestamp: float


class HarnessChannel(BaseChannel):
    """Driver-controlled BaseChannel impl for scripted scenarios.

    Lifecycle (see plan §Architecture > HarnessChannel):
      - ``start(handler)`` stores the handler, sets ``_ready_event``, then
        blocks on ``_stop_event``. GatewayManager spawns this as a background
        task and gathers on it; we MUST block, otherwise gateway treats the
        channel as exited and tears the stack down.
      - ``inject_user_message(msg)`` is the driver's API for pumping user
        input. Resolves any ``@alias`` thread/message refs.
      - ``stop()`` sets ``_stop_event`` so ``start()`` returns and the
        gateway's gather completes.
      - ``wait_ready(timeout)`` lets the driver block until the gateway has
        installed our handler.

    Streaming-edit collapse: every ``edit_message(thread_id, message_id,
    text)`` updates the latest text for that ``(thread_id, message_id)``
    pair so the final consolidated text is what assertions see, while the
    raw event log keeps the per-edit history.

    Discord platform extension hooks (``set_session_context`` etc.) are
    intentionally NOT implemented — GatewayManager guards those with
    ``hasattr`` so they're skipped. The harness's job is to test what
    BaseChannel guarantees, not what individual platforms layer on top.
    """

    # Capability flag honored by GatewayManager.
    supports_streaming_edit: bool = True

    def __init__(
        self,
        *,
        platform: str = "harness",
        channel_id: str = "100",
    ) -> None:
        self._platform = platform
        self._channel_id = channel_id
        self._handler: MessageHandler | None = None
        self._stop_event = asyncio.Event()
        # set inside start() after the handler is installed; the driver
        # awaits this via wait_ready() so it doesn't race ahead and call
        # inject_user_message() before the gateway has wired us in.
        self._ready_event = asyncio.Event()
        self.events: list[ChannelEvent] = []
        self._aliases: dict[str, str] = {}   # "@thread1" / "@msg1" → resolved id
        self._next_seq = count(1)
        self._next_msg_id = count(1)
        self._next_thread_id = count(1)
        # Latest rendered text per (thread_id, message_id) — captures the
        # streaming-edit final state without losing the intermediate
        # edits in self.events.
        self._final_message_text: dict[tuple[str, str], str] = {}

        # Slash command services context
        self._session = None
        self._registry = None
        self._memory_store = None
        self._skill_syncer = None
        self._workspace_skills_dirs = None
        self._runtime_service = None
        self._judge_store = None
        self._gateway_manager = None
        self._scheduler = None
        self._diary_reflector = None
        self._skill_eval_enabled = True
        self._skill_stats_recent_days = 7
        self._skill_feedback_emojis = {"👍", "👎"}

        self._task_service: TaskService
        self._doctor_service: DoctorService
        self._automation_service: AutomationService
        self._memory_service: MemoryService
        self._skill_eval_service: SkillEvalService
        self._refresh_services()

    # -- Service Setters and Refresher ------------------------------------

    def set_session_context(self, session, registry, memory_store=None) -> None:
        """Inject session objects needed by slash commands."""
        self._session = session
        self._registry = registry
        self._memory_store = memory_store
        self._refresh_services()

    def set_skill_syncer(self, syncer, workspace_skills_dirs=None) -> None:
        """Inject skill syncer."""
        self._skill_syncer = syncer
        self._workspace_skills_dirs = workspace_skills_dirs

    def set_runtime_service(self, runtime_service) -> None:
        """Inject runtime service."""
        self._runtime_service = runtime_service
        self._refresh_services()

    def set_diary_reflector(self, reflector) -> None:
        """Inject diary reflector."""
        self._diary_reflector = reflector

    def set_scheduler(self, scheduler) -> None:
        """Inject scheduler."""
        self._scheduler = scheduler
        self._refresh_services()

    def set_judge_store(self, store, gateway_manager=None) -> None:
        """Inject judge store."""
        self._judge_store = store
        if gateway_manager is not None:
            self._gateway_manager = gateway_manager
        self._refresh_services()

    def set_skill_evaluation_config(self, cfg: dict | None) -> None:
        """Inject skill evaluation config."""
        cfg = cfg or {}
        self._skill_eval_enabled = bool(cfg.get("enabled", True))
        self._skill_stats_recent_days = int(cfg.get("stats_recent_days", 7))
        emojis = cfg.get("feedback_emojis", ["👍", "👎"])
        self._skill_feedback_emojis = {str(e) for e in emojis if str(e)}
        self._refresh_services()

    def _refresh_services(self) -> None:
        """Re-instantiate business services with current injected components."""
        fire_automation = self._scheduler.fire_job_now if self._scheduler is not None else None
        self._task_service = TaskService(
            self._runtime_service,
            self._memory_store,
            fire_automation=fire_automation,
        )
        self._doctor_service = DoctorService(self._runtime_service)
        self._automation_service = AutomationService(self._scheduler, self._memory_store)
        self._memory_service = MemoryService(
            self._judge_store,
            gateway_manager=self._gateway_manager,
            registry=self._registry,
        )
        self._skill_eval_service = SkillEvalService(
            self._memory_store,
            recent_days=self._skill_stats_recent_days,
            feedback_emojis=self._skill_feedback_emojis,
        )

    def _render_task_action_result(self, result: TaskActionResult) -> str:
        task = result.task
        if task is None:
            return result.message[:1900]
        lines = [
            f"**Task** `{task.id}`",
            f"- Status: `{task.status}`",
            f"- Type: `{task.task_type}`",
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
        return "\n".join(lines)[:1900]

    def _render_task_list_result(self, result: TaskListResult) -> str:
        if not result.tasks:
            return "No runtime tasks found."
        lines = [f"**Runtime tasks** ({len(result.tasks)})"]
        for task in result.tasks:
            lines.append(
                f"- `{task.task_id}` [{task.status}] `{task.task_type}` {task.step_info or ''} · {task.goal[:80]}".rstrip()
            )
        return "\n".join(lines)[:1900]

    # -- BaseChannel mandatory surface ------------------------------------

    @property
    def platform(self) -> str:
        return self._platform

    @property
    def channel_id(self) -> str:
        return self._channel_id

    async def start(self, on_message: MessageHandler) -> None:
        # GatewayManager.start() spawns this as a task and gathers on
        # _background_tasks (manager.py:695-700, :742). We block until
        # stop() is called so the gateway lifecycle stays alive.
        self._handler = on_message
        self._ready_event.set()
        await self._stop_event.wait()

    async def create_thread(self, msg: IncomingMessage, name: str) -> str:
        thread_id = f"t-{next(self._next_thread_id)}"
        self._record(
            "create_thread",
            thread_id=thread_id,
            payload={"name": name, "anchor_message_author": msg.author},
        )
        return thread_id

    async def send(self, thread_id: str, text: str) -> str | None:
        msg_id = f"m-{next(self._next_msg_id)}"
        self._record(
            "send",
            thread_id=thread_id,
            payload={"message_id": msg_id, "text": text},
        )
        self._final_message_text[(thread_id, msg_id)] = text
        return msg_id

    # -- BaseChannel optional surface (recorded, not no-op) ---------------

    async def stop(self) -> None:
        self._record("stop", thread_id=None, payload={})
        self._stop_event.set()

    async def edit_message(
        self,
        thread_id: str,
        message_id: str,
        text: str,
    ) -> None:
        self._record(
            "edit_message",
            thread_id=thread_id,
            payload={"message_id": message_id, "text": text},
        )
        self._final_message_text[(thread_id, message_id)] = text

    async def send_interactive(
        self,
        thread_id: str,
        prompt: InteractivePrompt,
    ) -> str | None:
        msg_id = f"i-{next(self._next_msg_id)}"
        self._record(
            "send_interactive",
            thread_id=thread_id,
            payload={
                "message_id": msg_id,
                "text": prompt.text,
                "actions": [
                    {"id": a.id, "label": a.label, "style": a.style, "disabled": a.disabled}
                    for a in prompt.actions
                ],
                "idempotency_key": prompt.idempotency_key,
                "entity_kind": prompt.entity_kind,
                "entity_id": prompt.entity_id,
            },
        )
        return msg_id

    async def update_interactive(
        self,
        thread_id: str,
        message_id: str,
        prompt: InteractivePrompt,
    ) -> None:
        self._record(
            "update_interactive",
            thread_id=thread_id,
            payload={
                "message_id": message_id,
                "text": prompt.text,
                "actions": [
                    {"id": a.id, "label": a.label, "style": a.style, "disabled": a.disabled}
                    for a in prompt.actions
                ],
            },
        )

    def render_user_mention(self, user_id: str) -> str:
        return f"<@{user_id}>"

    async def send_dm(self, user_id: str, text: str) -> str | None:
        msg_id = f"dm-{next(self._next_msg_id)}"
        self._record(
            "send_dm",
            thread_id=None,
            payload={"message_id": msg_id, "user_id": user_id, "text": text},
        )
        return msg_id

    async def upsert_status_message(
        self,
        thread_id: str,
        text: str,
        *,
        message_id: str | None = None,
    ) -> str | None:
        if message_id:
            self._record(
                "upsert_status_message",
                thread_id=thread_id,
                payload={"message_id": message_id, "text": text, "operation": "edit"},
            )
            self._final_message_text[(thread_id, message_id)] = text
            return message_id
        new_id = f"s-{next(self._next_msg_id)}"
        self._record(
            "upsert_status_message",
            thread_id=thread_id,
            payload={"message_id": new_id, "text": text, "operation": "create"},
        )
        self._final_message_text[(thread_id, new_id)] = text
        return new_id

    async def send_attachment(
        self,
        thread_id: str,
        attachment: OutgoingAttachment,
    ) -> str | None:
        msg_id = f"a-{next(self._next_msg_id)}"
        self._record(
            "send_attachment",
            thread_id=thread_id,
            payload={
                "message_id": msg_id,
                "filename": attachment.filename,
                "content_type": attachment.content_type,
                "local_path": str(attachment.local_path),
                "caption": attachment.caption,
            },
        )
        return msg_id

    async def send_attachments(
        self,
        thread_id: str,
        attachments: list[OutgoingAttachment],
        *,
        text: str | None = None,
    ) -> list[str]:
        message_ids: list[str] = []
        if text:
            msg_id = await self.send(thread_id, text)
            if msg_id:
                message_ids.append(msg_id)
        for attachment in attachments:
            msg_id = await self.send_attachment(thread_id, attachment)
            if msg_id:
                message_ids.append(msg_id)
        return message_ids

    @asynccontextmanager
    async def typing(self, thread_id: str) -> AsyncIterator[None]:
        self._record("typing_open", thread_id=thread_id, payload={})
        try:
            yield
        finally:
            self._record("typing_close", thread_id=thread_id, payload={})

    def supports_buttons(self) -> bool:
        return True

    async def send_task_draft(
        self,
        *,
        thread_id: str,
        draft_text: str,
        task_id: str,
        nonce: str,
        actions: list[str],
    ) -> str | None:
        msg_id = f"d-{next(self._next_msg_id)}"
        self._record(
            "send_task_draft",
            thread_id=thread_id,
            payload={
                "message_id": msg_id,
                "draft_text": draft_text,
                "task_id": task_id,
                "nonce": nonce,
                "actions": list(actions),
            },
        )
        return msg_id

    def parse_decision_event(self, raw: Any) -> Any | None:
        # Harness doesn't surface raw decision events; scenarios that need
        # to simulate a button click should call inject_user_message with
        # the equivalent text command. (Future: add inject_button_click.)
        del raw
        return None

    async def signal_task_status(
        self,
        thread_id: str,
        message_id: str | None,
        emoji: str,
    ) -> None:
        self._record(
            "signal_task_status",
            thread_id=thread_id,
            payload={"message_id": message_id, "emoji": emoji},
        )

    async def send_hitl_prompt(
        self,
        *,
        thread_id: str,
        prompt: Any,
    ) -> str | None:
        msg_id = f"h-{next(self._next_msg_id)}"
        choices = getattr(prompt, "choices", ()) or ()
        normalized_choices = []
        for choice in choices:
            if isinstance(choice, dict):
                normalized_choices.append(dict(choice))
            else:
                normalized_choices.append(
                    {
                        "id": getattr(choice, "id", None),
                        "label": getattr(choice, "label", None),
                        "description": getattr(choice, "description", None),
                    }
                )
        self._record(
            "send_hitl_prompt",
            thread_id=thread_id,
            payload={
                "message_id": msg_id,
                "prompt_id": getattr(prompt, "id", None),
                "question": getattr(prompt, "question", None),
                "details": getattr(prompt, "details", None),
                "choices": normalized_choices,
            },
        )
        return msg_id

    # -- driver-only API (NOT in BaseChannel) -----------------------------

    async def inject_slash_command(
        self,
        name: str,
        args: dict[str, Any],
        *,
        author_id: str = "owner-1",
        thread_id: str | None = None,
    ) -> str:
        """Simulate a slash command invocation by routing to the appropriate service."""
        if not self._task_service:
            raise RuntimeError("TaskService not ready in HarnessChannel.")

        # Resolve aliases in args
        resolved_args = {}
        for k, v in args.items():
            if isinstance(v, str):
                resolved_args[k] = self.resolve_alias(v) or v
            else:
                resolved_args[k] = v

        resolved_thread = self.resolve_alias(thread_id) or thread_id or "1"

        if name == "task_start":
            result = await self._task_service.create_task(
                session=self._session,
                registry=self._registry,
                thread_id=resolved_thread,
                goal=resolved_args["goal"],
                actor_id=author_id,
                preferred_agent=resolved_args.get("agent"),
                test_command=resolved_args.get("test_command"),
                max_steps=resolved_args.get("max_steps"),
                max_minutes=resolved_args.get("max_minutes"),
            )
            reply = self._render_task_action_result(result)
        elif name == "task_stop":
            task_id = resolved_args.get("task_id")
            result = await self._task_service.stop(task_id, actor_id=author_id)
            reply = self._render_task_action_result(result)
        elif name == "task_status":
            task_id = resolved_args.get("task_id")
            result = await self._task_service.get_status(task_id)
            reply = self._render_task_action_result(result)
        elif name == "task_list":
            result = await self._task_service.list_tasks(
                platform=self.platform,
                channel_id=self.channel_id,
                status=resolved_args.get("status"),
                limit=resolved_args.get("limit", 20),
            )
            reply = self._render_task_list_result(result)
        elif name in {"task_approve", "task_reject", "task_suggest", "task_merge", "task_discard"}:
            task_id = resolved_args.get("task_id")
            action = name.replace("task_", "")
            result = await self._task_service.decide(
                platform=self.platform,
                channel_id=self.channel_id,
                thread_id=resolved_thread,
                task_id=task_id,
                action=action,
                actor_id=author_id,
                suggestion=resolved_args.get("suggestion"),
            )
            reply = self._render_task_action_result(result)
        else:
            raise NotImplementedError(f"Slash command {name!r} not implemented in HarnessChannel.")

        # Record this command result as a send event so assertions can check it!
        self._record(
            "send",
            thread_id=resolved_thread,
            payload={"message_id": f"sc-{next(self._next_msg_id)}", "text": reply},
        )
        return reply

    async def wait_ready(self, *, timeout: float = 5.0) -> None:
        """Block until ``start()`` has installed the handler.

        Driver calls this after spawning ``gateway.start()`` as a task and
        before injecting the first user message; otherwise we'd race the
        gateway's setup work and lose messages.
        """
        await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)

    async def inject_user_message(self, msg: IncomingMessage) -> None:
        """Pump a user message through the gateway as if it came from the platform.

        Resolves ``@alias`` references in ``thread_id`` and ``reply_to_message_id``
        first so scenarios can chain steps that reference earlier captures.
        """
        if self._handler is None:
            raise RuntimeError(
                "HarnessChannel.start() has not been awaited yet — "
                "call wait_ready() first"
            )
        msg = self._resolve_aliases(msg)
        await self._handler(msg)

    def bind_alias(self, alias: str, value: str) -> None:
        """Record ``@alias → value`` so subsequent steps can reference it."""
        if not alias.startswith("@"):
            raise ValueError(f"Aliases must start with '@'; got {alias!r}")
        self._aliases[alias] = value

    def resolve_alias(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith("@"):
            return value
        try:
            return self._aliases[value]
        except KeyError as exc:
            raise KeyError(
                f"Alias {value!r} is not bound. Available: {sorted(self._aliases)}"
            ) from exc

    # -- introspection helpers --------------------------------------------

    def find_events(
        self,
        *,
        type: str | None = None,
        thread_id: str | None = None,
        payload_contains: str | None = None,
    ) -> list[ChannelEvent]:
        """Return events matching all provided filters (AND semantics)."""
        resolved_thread = self.resolve_alias(thread_id)
        results: list[ChannelEvent] = []
        for event in self.events:
            if type is not None and event.type != type:
                continue
            if resolved_thread is not None and event.thread_id != resolved_thread:
                continue
            if payload_contains is not None:
                if not _payload_text_contains(event.payload, payload_contains):
                    continue
            results.append(event)
        return results

    def latest_text_for(self, thread_id: str, message_id: str) -> str | None:
        return self._final_message_text.get(
            (self.resolve_alias(thread_id) or thread_id, message_id)
        )

    # -- private ----------------------------------------------------------

    def _record(
        self,
        event_type: str,
        *,
        thread_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        event = ChannelEvent(
            seq=next(self._next_seq),
            type=event_type,
            thread_id=thread_id,
            payload=payload,
            timestamp=time.monotonic(),
        )
        self.events.append(event)

    def _resolve_aliases(self, msg: IncomingMessage) -> IncomingMessage:
        if not (msg.thread_id and msg.thread_id.startswith("@")) and not (
            msg.reply_to_message_id and msg.reply_to_message_id.startswith("@")
        ):
            return msg
        return IncomingMessage(
            platform=msg.platform,
            channel_id=msg.channel_id,
            thread_id=self.resolve_alias(msg.thread_id),
            author=msg.author,
            content=msg.content,
            author_id=msg.author_id,
            raw=msg.raw,
            preferred_agent=msg.preferred_agent,
            system=msg.system,
            attachments=msg.attachments,
            reply_to_message_id=self.resolve_alias(msg.reply_to_message_id),
        )


def _payload_text_contains(payload: dict[str, Any], needle: str) -> bool:
    """Search payload values for *needle* (case-sensitive substring).

    Used by ``find_events(payload_contains=...)`` and the scenario assertions.
    Limited to string-leaf values across one level deep — sufficient for
    matching message text, attachment filenames, and reaction emoji without
    overreaching into nested action lists where match semantics get fuzzy.
    """
    needle_lower = needle.lower()
    for value in payload.values():
        if isinstance(value, str) and needle_lower in value.lower():
            return True
        if isinstance(value, Path) and needle_lower in str(value).lower():
            return True
    return False


def make_incoming(
    *,
    content: str,
    author: str = "harness-user",
    author_id: str = "owner-1",
    thread_id: str | None = None,
    reply_to_message_id: str | None = None,
    platform: str = "harness",
    channel_id: str = "100",
    preferred_agent: str | None = None,
    system: bool = False,
) -> IncomingMessage:
    """Convenience builder for IncomingMessage in scenarios / tests."""
    return IncomingMessage(
        platform=platform,
        channel_id=channel_id,
        thread_id=thread_id,
        author=author,
        content=content,
        author_id=author_id,
        preferred_agent=preferred_agent,
        system=system,
        reply_to_message_id=reply_to_message_id,
    )
