from __future__ import annotations

from types import SimpleNamespace

import pytest

from oh_my_agent.gateway.services.task_service import TaskService
from oh_my_agent.runtime.types import RuntimeTask, TaskDecisionEvent


def _task(**overrides) -> RuntimeTask:
    base = dict(
        id="task-1",
        platform="discord",
        channel_id="100",
        thread_id="200",
        created_by="owner-1",
        goal="Fix the docs",
        original_request=None,
        preferred_agent="codex",
        status="DRAFT",
        step_no=0,
        max_steps=8,
        max_minutes=20,
        agent_timeout_seconds=None,
        agent_max_turns=None,
        test_command="pytest -q",
        workspace_path=None,
        decision_message_id=None,
        status_message_id=None,
        blocked_reason=None,
        error=None,
        summary=None,
        resume_instruction=None,
        merge_commit_hash=None,
        merge_error=None,
        completion_mode="merge",
        output_summary=None,
        artifact_manifest=None,
        automation_name=None,
        workspace_cleaned_at=None,
        created_at=None,
        started_at=None,
        updated_at=None,
        ended_at=None,
        task_type="repo_change",
        skill_name=None,
    )
    base.update(overrides)
    return RuntimeTask(**base)


class _RuntimeStub:
    def __init__(self):
        self.created = _task(status="PENDING")
        self.tasks = {"task-1": _task(), "task-merge": _task(id="task-merge", status="WAITING_MERGE")}
        self.last_event = None

    async def create_repo_change_task(self, **kwargs):
        self.created_kwargs = kwargs
        return self.created

    async def get_task(self, task_id: str):
        return self.tasks.get(task_id)

    async def list_tasks(self, *, platform: str, channel_id: str, status: str | None = None, limit: int = 20):
        del platform, channel_id, limit
        tasks = list(self.tasks.values())
        if status:
            tasks = [task for task in tasks if task.status == status]
        return tasks

    async def build_slash_decision_event(self, **kwargs):
        self.last_event = ("slash", kwargs)
        return TaskDecisionEvent(
            platform=kwargs["platform"],
            channel_id=kwargs["channel_id"],
            thread_id=kwargs["thread_id"],
            task_id=kwargs["task_id"],
            action=kwargs["action"],
            actor_id=kwargs["actor_id"],
            nonce="nonce-1",
            source="slash",
            suggestion=kwargs.get("suggestion"),
        )

    async def handle_decision_event(self, event: TaskDecisionEvent):
        self.last_event = event
        return f"Task `{event.task_id}` {event.action}d."

    async def get_task_changes(self, task_id: str):
        return f"Task `{task_id}` changes (1):\n- `README.md`"

    async def get_task_logs(self, task_id: str):
        return f"**Task Logs** `{task_id}`"

    async def cleanup_tasks(self, *, actor_id: str, task_id: str | None = None):
        return f"Cleanup completed by `{actor_id}` for `{task_id or 'all'}`."

    async def resume_task(self, task_id: str, instruction: str, *, actor_id: str):
        self.tasks[task_id] = _task(id=task_id, status="PENDING", resume_instruction=instruction)
        return f"Task `{task_id}` resumed and queued."

    async def stop_task(self, task_id: str, *, actor_id: str):
        self.tasks[task_id] = _task(id=task_id, status="STOPPED")
        return f"Task `{task_id}` stopped."

    async def replace_draft_task(self, task_id: str, *, actor_id: str):
        task = self.tasks.get(task_id)
        if task is None:
            return (f"Task `{task_id}` not found.", None)
        if task.status != "DRAFT":
            return (f"Task `{task.id}` is not a DRAFT (status: {task.status}).", None)
        name = task.automation_name
        if not name:
            return (f"Task `{task.id}` has no automation_name; cannot refire.", None)
        self.tasks[task_id] = _task(
            id=task_id,
            status="DISCARDED",
            automation_name=name,
            summary="Replaced by user (refired automation).",
        )
        return (
            f"Task `{task_id}` discarded; refiring automation `{name}`.",
            name,
        )

    async def consume_decision_nonce(self, *, task_id, nonce, action, actor_id, source):
        del task_id, action, actor_id, source
        return nonce == "valid-nonce"


@pytest.mark.asyncio
async def test_create_task_returns_action_result():
    runtime = _RuntimeStub()
    service = TaskService(runtime)

    result = await service.create_task(
        session=SimpleNamespace(),
        registry=SimpleNamespace(),
        thread_id="200",
        goal="Fix the docs",
        actor_id="owner-1",
        preferred_agent="codex",
    )

    assert result.success is True
    assert result.task_id == "task-1"
    assert result.task_status == "PENDING"
    assert runtime.created_kwargs["created_by"] == "owner-1"


@pytest.mark.asyncio
async def test_get_status_missing_task():
    runtime = _RuntimeStub()
    service = TaskService(runtime)

    result = await service.get_status("missing")

    assert result.success is False
    assert "not found" in result.message


@pytest.mark.asyncio
async def test_list_tasks_builds_summaries():
    runtime = _RuntimeStub()
    service = TaskService(runtime)

    result = await service.list_tasks(platform="discord", channel_id="100", status=None, limit=10)

    assert result.success is True
    assert len(result.tasks) == 2
    assert result.tasks[0].step_info.startswith("step")


@pytest.mark.asyncio
async def test_decide_slash_promotes_suggest_to_request_changes_for_waiting_merge():
    runtime = _RuntimeStub()
    service = TaskService(runtime)

    result = await service.decide(
        platform="discord",
        channel_id="100",
        thread_id="200",
        task_id="task-merge",
        action="suggest",
        actor_id="owner-1",
        suggestion="Please fix conflict handling",
    )

    assert result.task_id == "task-merge"
    assert runtime.last_event.action == "request_changes"


@pytest.mark.asyncio
async def test_decide_button_uses_provided_nonce():
    runtime = _RuntimeStub()
    service = TaskService(runtime)

    result = await service.decide(
        platform="discord",
        channel_id="100",
        thread_id="200",
        task_id="task-1",
        action="approve",
        actor_id="owner-1",
        source="button",
        nonce="nonce-xyz",
    )

    assert result.success is True
    assert isinstance(runtime.last_event, TaskDecisionEvent)
    assert runtime.last_event.nonce == "nonce-xyz"
    assert runtime.last_event.source == "button"


@pytest.mark.asyncio
async def test_get_changes_and_logs_passthrough():
    runtime = _RuntimeStub()
    service = TaskService(runtime)

    changes = await service.get_changes("task-1")
    logs = await service.get_logs("task-1")

    assert "README.md" in changes.message
    assert "Task Logs" in logs.message


@pytest.mark.asyncio
async def test_decide_replace_discards_draft_and_refires_automation():
    runtime = _RuntimeStub()
    runtime.tasks["task-1"] = _task(id="task-1", status="DRAFT", automation_name="daily-news")
    fired: list[str] = []

    async def _fire(name: str) -> bool:
        fired.append(name)
        return True

    service = TaskService(runtime, fire_automation=_fire)

    result = await service.decide(
        platform="discord",
        channel_id="100",
        thread_id="200",
        task_id="task-1",
        action="replace",
        actor_id="owner-1",
    )

    assert result.success is True
    assert fired == ["daily-news"]
    assert "refired" in result.message
    assert runtime.tasks["task-1"].status == "DISCARDED"


@pytest.mark.asyncio
async def test_decide_replace_without_fire_callback_returns_error():
    runtime = _RuntimeStub()
    runtime.tasks["task-1"] = _task(id="task-1", status="DRAFT", automation_name="daily-news")
    service = TaskService(runtime)  # no fire_automation

    result = await service.decide(
        platform="discord",
        channel_id="100",
        thread_id="200",
        task_id="task-1",
        action="replace",
        actor_id="owner-1",
    )

    assert result.success is False
    assert "automation scheduler is not enabled" in result.message


@pytest.mark.asyncio
async def test_decide_replace_fails_when_task_not_draft():
    runtime = _RuntimeStub()
    runtime.tasks["task-1"] = _task(id="task-1", status="RUNNING", automation_name="daily-news")
    fired: list[str] = []

    async def _fire(name: str) -> bool:
        fired.append(name)
        return True

    service = TaskService(runtime, fire_automation=_fire)

    result = await service.decide(
        platform="discord",
        channel_id="100",
        thread_id="200",
        task_id="task-1",
        action="replace",
        actor_id="owner-1",
    )

    assert result.success is False
    assert "not a DRAFT" in result.message
    assert fired == []


@pytest.mark.asyncio
async def test_decide_replace_button_requires_valid_nonce():
    runtime = _RuntimeStub()
    runtime.tasks["task-1"] = _task(id="task-1", status="DRAFT", automation_name="daily-news")
    fired: list[str] = []

    async def _fire(name: str) -> bool:
        fired.append(name)
        return True

    service = TaskService(runtime, fire_automation=_fire)

    rejected = await service.decide(
        platform="discord",
        channel_id="100",
        thread_id="200",
        task_id="task-1",
        action="replace",
        actor_id="owner-1",
        source="button",
        nonce="expired",
    )
    assert rejected.success is False
    assert "invalid or expired" in rejected.message
    assert fired == []

    accepted = await service.decide(
        platform="discord",
        channel_id="100",
        thread_id="200",
        task_id="task-1",
        action="replace",
        actor_id="owner-1",
        source="button",
        nonce="valid-nonce",
    )
    assert accepted.success is True
    assert fired == ["daily-news"]


@pytest.mark.asyncio
async def test_cleanup_resume_and_stop_return_results():
    runtime = _RuntimeStub()
    service = TaskService(runtime)

    cleanup = await service.cleanup(actor_id="owner-1", task_id=None)
    resume = await service.resume("task-1", "continue", actor_id="owner-1")
    stop = await service.stop("task-1", actor_id="owner-1")

    assert cleanup.success is True
    assert resume.task_status == "PENDING"
    assert stop.task_status == "STOPPED"
