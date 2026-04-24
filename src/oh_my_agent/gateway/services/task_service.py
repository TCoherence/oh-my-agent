from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Awaitable, Callable, Literal

from oh_my_agent.gateway.services.types import ServiceResult, TaskActionResult, TaskListResult, TaskSummary
from oh_my_agent.runtime.types import RuntimeTask, TaskDecisionEvent

if TYPE_CHECKING:
    from oh_my_agent.agents.registry import AgentRegistry
    from oh_my_agent.gateway.session import ChannelSession
    from oh_my_agent.memory.store import MemoryStore
    from oh_my_agent.runtime.service import RuntimeService


FireJobResult = Literal["ok", "not_found", "scheduler_down", "already_firing"]
FireAutomation = Callable[[str], Awaitable[FireJobResult]]


_ERROR_PREFIXES = (
    "Only configured owners",
    "Task `",
    "No active approval token",
    "Unsupported decision action",
    "Decision token is invalid or expired",
)


class TaskService:
    def __init__(
        self,
        runtime_service: RuntimeService | None,
        memory_store: MemoryStore | None = None,
        fire_automation: FireAutomation | None = None,
    ):
        self._runtime = runtime_service
        self._store = memory_store
        self._fire_automation = fire_automation

    async def create_task(
        self,
        *,
        session: ChannelSession,
        registry: AgentRegistry,
        thread_id: str,
        goal: str,
        actor_id: str,
        task_type: str = "repo_change",
        preferred_agent: str | None = None,
        test_command: str | None = None,
        max_steps: int | None = None,
        max_minutes: int | None = None,
    ) -> TaskActionResult:
        del task_type
        if self._runtime is None:
            return TaskActionResult(success=False, message="Runtime service is not enabled.")
        task = await self._runtime.create_repo_change_task(
            session=session,
            registry=registry,
            thread_id=thread_id,
            goal=goal,
            created_by=actor_id,
            preferred_agent=preferred_agent,
            test_command=test_command,
            max_steps=max_steps,
            max_minutes=max_minutes,
            source="slash",
        )
        return TaskActionResult(
            success=True,
            message=f"Created task `{task.id}` with status `{task.status}`.",
            task_id=task.id,
            task_status=task.status,
            task=task,
        )

    async def get_status(self, task_id: str) -> TaskActionResult:
        if self._runtime is None:
            return TaskActionResult(success=False, message="Runtime service is not enabled.")
        task = await self._runtime.get_task(task_id)
        if task is None:
            return TaskActionResult(success=False, message=f"Task `{task_id}` not found.", task_id=task_id)
        return TaskActionResult(
            success=True,
            message=f"Task `{task.id}` is `{task.status}`.",
            task_id=task.id,
            task_status=task.status,
            task=task,
        )

    async def list_tasks(
        self,
        *,
        platform: str,
        channel_id: str,
        status: str | None = None,
        limit: int = 20,
    ) -> TaskListResult:
        if self._runtime is None:
            return TaskListResult(success=False, message="Runtime service is not enabled.")
        tasks = await self._runtime.list_tasks(
            platform=platform,
            channel_id=channel_id,
            status=status,
            limit=max(1, min(limit, 30)),
        )
        summaries = [
            TaskSummary(
                task_id=task.id,
                status=task.status,
                task_type=task.task_type,
                goal=task.goal,
                step_info=f"step {task.step_no}/{task.max_steps}",
            )
            for task in tasks
        ]
        return TaskListResult(
            success=True,
            message=f"Found {len(summaries)} task(s).",
            tasks=summaries,
        )

    async def decide(
        self,
        *,
        platform: str,
        channel_id: str,
        thread_id: str,
        task_id: str,
        action: str,
        actor_id: str,
        suggestion: str | None = None,
        source: str = "slash",
        nonce: str | None = None,
        max_turns: int | None = None,
        timeout_seconds: int | None = None,
    ) -> TaskActionResult:
        if self._runtime is None:
            return TaskActionResult(success=False, message="Runtime service is not enabled.")
        if action == "replace":
            return await self._decide_replace(
                task_id=task_id,
                actor_id=actor_id,
                source=source,
                nonce=nonce,
            )
        resolved_action = action
        if action == "suggest":
            task = await self._runtime.get_task(task_id)
            if task and task.status in {"WAITING_MERGE", "APPLIED"}:
                resolved_action = "request_changes"
        if source == "button":
            if not nonce:
                return TaskActionResult(success=False, message="Decision token is missing.", task_id=task_id)
            event = TaskDecisionEvent(
                platform=platform,
                channel_id=channel_id,
                thread_id=thread_id,
                task_id=task_id,
                action=resolved_action,  # type: ignore[arg-type]
                actor_id=actor_id,
                nonce=nonce,
                source="button",
                suggestion=suggestion,
                max_turns=max_turns,
                timeout_seconds=timeout_seconds,
            )
        else:
            event = await self._runtime.build_slash_decision_event(
                platform=platform,
                channel_id=channel_id,
                thread_id=thread_id,
                task_id=task_id,
                action=resolved_action,
                actor_id=actor_id,
                suggestion=suggestion,
                max_turns=max_turns,
                timeout_seconds=timeout_seconds,
            )
            if not event:
                return TaskActionResult(
                    success=False,
                    message="No active approval token found for this task.",
                    task_id=task_id,
                )
        message = await self._runtime.handle_decision_event(event)
        task = await self._runtime.get_task(task_id)
        return TaskActionResult(
            success=not self._looks_like_error(message, task_id),
            message=message,
            task_id=task_id,
            task_status=task.status if task else None,
            task=task,
        )

    async def _decide_replace(
        self,
        *,
        task_id: str,
        actor_id: str,
        source: str,
        nonce: str | None,
    ) -> TaskActionResult:
        if self._runtime is None:
            return TaskActionResult(success=False, message="Runtime service is not enabled.")
        if source == "button":
            if not nonce:
                return TaskActionResult(
                    success=False,
                    message="Decision token is missing.",
                    task_id=task_id,
                )
            consumed = await self._runtime.consume_decision_nonce(
                task_id=task_id,
                nonce=nonce,
                action="replace",
                actor_id=actor_id,
                source="button",
            )
            if not consumed:
                return TaskActionResult(
                    success=False,
                    message="Decision token is invalid or expired.",
                    task_id=task_id,
                )
        message, automation_name = await self._runtime.replace_draft_task(
            task_id, actor_id=actor_id
        )
        if automation_name is None:
            task = await self._runtime.get_task(task_id)
            return TaskActionResult(
                success=False,
                message=message,
                task_id=task_id,
                task_status=task.status if task else None,
                task=task,
            )
        if self._fire_automation is None:
            task = await self._runtime.get_task(task_id)
            return TaskActionResult(
                success=False,
                message=f"{message} But automation scheduler is not enabled.",
                task_id=task_id,
                task_status=task.status if task else None,
                task=task,
            )
        try:
            result = await self._fire_automation(automation_name)
        except Exception as exc:
            task = await self._runtime.get_task(task_id)
            return TaskActionResult(
                success=False,
                message=f"{message} Refire failed: {exc}",
                task_id=task_id,
                task_status=task.status if task else None,
                task=task,
            )
        task = await self._runtime.get_task(task_id)
        if result == "ok":
            suffix = "refired"
        elif result == "already_firing":
            suffix = "refire skipped (already firing)"
        elif result == "not_found":
            suffix = "refire failed (automation not found)"
        else:  # scheduler_down
            suffix = "refire failed (scheduler not running)"
        return TaskActionResult(
            success=result == "ok",
            message=f"{message} ({suffix})",
            task_id=task_id,
            task_status=task.status if task else None,
            task=task,
        )

    async def get_changes(self, task_id: str) -> ServiceResult:
        if self._runtime is None:
            return ServiceResult(success=False, message="Runtime service is not enabled.")
        text = await self._runtime.get_task_changes(task_id)
        return ServiceResult(success=not text.startswith("Task `") or "changes (" in text, message=text)

    async def get_logs(self, task_id: str) -> ServiceResult:
        if self._runtime is None:
            return ServiceResult(success=False, message="Runtime service is not enabled.")
        text = await self._runtime.get_task_logs(task_id)
        return ServiceResult(success=not text.startswith("Task `") or "Task Logs" in text, message=text)

    async def cleanup(self, *, actor_id: str, task_id: str | None = None) -> ServiceResult:
        if self._runtime is None:
            return ServiceResult(success=False, message="Runtime service is not enabled.")
        text = await self._runtime.cleanup_tasks(actor_id=actor_id, task_id=task_id)
        return ServiceResult(success=not self._looks_like_error(text, task_id), message=text)

    async def resume(self, task_id: str, instruction: str, *, actor_id: str) -> TaskActionResult:
        if self._runtime is None:
            return TaskActionResult(success=False, message="Runtime service is not enabled.")
        text = await self._runtime.resume_task(task_id, instruction, actor_id=actor_id)
        task = await self._runtime.get_task(task_id)
        return TaskActionResult(
            success=not self._looks_like_error(text, task_id),
            message=text,
            task_id=task_id,
            task_status=task.status if task else None,
            task=task,
        )

    async def stop(self, task_id: str, *, actor_id: str) -> TaskActionResult:
        if self._runtime is None:
            return TaskActionResult(success=False, message="Runtime service is not enabled.")
        text = await self._runtime.stop_task(task_id, actor_id=actor_id)
        task = await self._runtime.get_task(task_id)
        return TaskActionResult(
            success=not self._looks_like_error(text, task_id),
            message=text,
            task_id=task_id,
            task_status=task.status if task else None,
            task=task,
        )

    @staticmethod
    def disable_actions(task: RuntimeTask | None, *, suggestion_only: bool = False) -> list[str]:
        if task is None:
            return []
        if suggestion_only:
            return ["approve", "reject"]
        if task.status in {"DRAFT", "BLOCKED"}:
            return ["approve", "reject", "suggest", "discard", "replace"]
        if task.status in {"WAITING_MERGE", "APPLIED", "MERGE_FAILED"}:
            return ["merge", "discard", "request_changes"]
        if task.status == "FAILED":
            return ["rerun_bump_turns"]
        return []

    @staticmethod
    def build_task_draft_text(
        *,
        original_text: str,
        task: RuntimeTask | None,
        result_message: str,
    ) -> str:
        status = task.status if task else "UNKNOWN"
        summary_bits = [f"Status: `{status}`"]
        if task and task.merge_commit_hash:
            summary_bits.append(f"Commit: `{task.merge_commit_hash}`")
        return (
            f"{original_text}\n\n---\n"
            + "\n".join(summary_bits)
            + f"\nResult: {result_message}"
        )[:1900]

    @staticmethod
    def build_processing_text(
        *,
        original_text: str,
        task: RuntimeTask | None,
        action: str,
    ) -> str:
        status = task.status if task else "PENDING"
        return (
            f"{original_text}\n\n---\n"
            f"Status: `{status}`\n"
            f"Result: Processing `{action}`..."
        )[:1900]

    @staticmethod
    def _looks_like_error(message: str, task_id: str | None = None) -> bool:
        if any(message.startswith(prefix) for prefix in _ERROR_PREFIXES):
            if task_id and message.startswith(f"Task `{task_id}`") and "approved" in message:
                return False
            return not any(
                good in message
                for good in ("approved", "rejected", "discarded", "stopped", "resumed", "cleaned", "changes (", "Task Logs")
            )
        return False
