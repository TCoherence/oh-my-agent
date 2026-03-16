from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal

TASK_TYPE_ARTIFACT = "artifact"
TASK_TYPE_REPO_CHANGE = "repo_change"
TASK_TYPE_SKILL_CHANGE = "skill_change"

# Legacy aliases kept for internal/backward compatibility while rows migrate.
TASK_TYPE_CODE = TASK_TYPE_REPO_CHANGE
TASK_TYPE_SKILL = TASK_TYPE_SKILL_CHANGE

TASK_COMPLETION_REPLY = "reply"
TASK_COMPLETION_ARTIFACT = "artifact"
TASK_COMPLETION_MERGE = "merge"

TASK_STATUS_DRAFT = "DRAFT"
TASK_STATUS_PENDING = "PENDING"
TASK_STATUS_RUNNING = "RUNNING"
TASK_STATUS_VALIDATING = "VALIDATING"
TASK_STATUS_APPLIED = "APPLIED"
TASK_STATUS_COMPLETED = "COMPLETED"
TASK_STATUS_WAITING_MERGE = "WAITING_MERGE"
TASK_STATUS_MERGED = "MERGED"
TASK_STATUS_MERGE_FAILED = "MERGE_FAILED"
TASK_STATUS_DISCARDED = "DISCARDED"
TASK_STATUS_BLOCKED = "BLOCKED"
TASK_STATUS_FAILED = "FAILED"
TASK_STATUS_TIMEOUT = "TIMEOUT"
TASK_STATUS_STOPPED = "STOPPED"
TASK_STATUS_REJECTED = "REJECTED"
TASK_STATUS_PAUSED = "PAUSED"
TASK_STATUS_WAITING_USER_INPUT = "WAITING_USER_INPUT"

TaskStatus = Literal[
    "DRAFT",
    "PENDING",
    "RUNNING",
    "VALIDATING",
    "APPLIED",
    "COMPLETED",
    "WAITING_MERGE",
    "MERGED",
    "MERGE_FAILED",
    "DISCARDED",
    "BLOCKED",
    "FAILED",
    "TIMEOUT",
    "STOPPED",
    "REJECTED",
    "PAUSED",
    "WAITING_USER_INPUT",
]
TaskType = Literal["artifact", "repo_change", "skill_change"]
TaskCompletionMode = Literal["reply", "artifact", "merge"]
SuspendedAgentRunStatus = Literal["waiting_auth", "resuming", "completed", "cancelled", "failed"]
HitlPromptStatus = Literal["waiting", "resolving", "completed", "cancelled", "failed"]
HitlPromptTargetKind = Literal["thread", "task"]

DecisionAction = Literal[
    "approve",
    "reject",
    "suggest",
    "merge",
    "discard",
    "request_changes",
]
DecisionSource = Literal["button", "slash"]


@dataclass(frozen=True)
class RuntimeTask:
    id: str
    platform: str
    channel_id: str
    thread_id: str
    created_by: str
    goal: str
    original_request: str | None
    preferred_agent: str | None
    status: TaskStatus
    step_no: int
    max_steps: int
    max_minutes: int
    test_command: str
    workspace_path: str | None
    decision_message_id: str | None
    status_message_id: str | None
    blocked_reason: str | None
    error: str | None
    summary: str | None
    resume_instruction: str | None
    merge_commit_hash: str | None
    merge_error: str | None
    completion_mode: str
    output_summary: str | None
    artifact_manifest: list[str] | None
    automation_name: str | None
    workspace_cleaned_at: str | None
    created_at: str | None
    started_at: str | None
    updated_at: str | None
    ended_at: str | None
    task_type: str = TASK_TYPE_REPO_CHANGE
    skill_name: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "RuntimeTask":
        raw_task_type = str(row.get("task_type", TASK_TYPE_REPO_CHANGE))
        if raw_task_type == "code":
            raw_task_type = TASK_TYPE_REPO_CHANGE
        elif raw_task_type == "skill":
            raw_task_type = TASK_TYPE_SKILL_CHANGE
        return cls(
            id=str(row["id"]),
            platform=str(row["platform"]),
            channel_id=str(row["channel_id"]),
            thread_id=str(row["thread_id"]),
            created_by=str(row.get("created_by", "unknown")),
            goal=str(row["goal"]),
            original_request=row.get("original_request"),
            preferred_agent=row.get("preferred_agent"),
            status=row["status"],
            step_no=int(row.get("step_no", 0)),
            max_steps=int(row.get("max_steps", 8)),
            max_minutes=int(row.get("max_minutes", 20)),
            test_command=str(row.get("test_command", "pytest -q")),
            workspace_path=row.get("workspace_path"),
            decision_message_id=row.get("decision_message_id"),
            status_message_id=row.get("status_message_id"),
            blocked_reason=row.get("blocked_reason"),
            error=row.get("error"),
            summary=row.get("summary"),
            resume_instruction=row.get("resume_instruction"),
            merge_commit_hash=row.get("merge_commit_hash"),
            merge_error=row.get("merge_error"),
            completion_mode=str(row.get("completion_mode", TASK_COMPLETION_MERGE)),
            output_summary=row.get("output_summary"),
            artifact_manifest=row.get("artifact_manifest"),
            automation_name=row.get("automation_name"),
            workspace_cleaned_at=row.get("workspace_cleaned_at"),
            created_at=row.get("created_at"),
            started_at=row.get("started_at"),
            updated_at=row.get("updated_at"),
            ended_at=row.get("ended_at"),
            task_type=raw_task_type,
            skill_name=row.get("skill_name"),
        )


@dataclass(frozen=True)
class TaskDecisionEvent:
    platform: str
    channel_id: str
    thread_id: str
    task_id: str
    action: DecisionAction
    actor_id: str
    nonce: str
    source: DecisionSource
    suggestion: str | None = None


@dataclass(frozen=True)
class SuspendedAgentRun:
    id: str
    platform: str
    channel_id: str
    thread_id: str
    agent_name: str
    status: SuspendedAgentRunStatus
    provider: str
    control_envelope_json: str
    session_id_snapshot: str | None
    resume_context: dict[str, Any]
    created_by: str
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "SuspendedAgentRun":
        raw_context = row.get("resume_context_json")
        context: dict[str, Any] = {}
        if isinstance(raw_context, str) and raw_context:
            try:
                parsed = json.loads(raw_context)
                if isinstance(parsed, dict):
                    context = parsed
            except Exception:
                context = {}
        elif isinstance(raw_context, dict):
            context = raw_context
        return cls(
            id=str(row["id"]),
            platform=str(row["platform"]),
            channel_id=str(row["channel_id"]),
            thread_id=str(row["thread_id"]),
            agent_name=str(row["agent_name"]),
            status=str(row["status"]),
            provider=str(row["provider"]),
            control_envelope_json=str(row["control_envelope_json"]),
            session_id_snapshot=row.get("session_id_snapshot"),
            resume_context=context,
            created_by=str(row["created_by"]),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            completed_at=row.get("completed_at"),
        )


@dataclass(frozen=True)
class HitlPrompt:
    id: str
    target_kind: HitlPromptTargetKind
    platform: str
    channel_id: str
    thread_id: str
    task_id: str | None
    agent_name: str
    status: HitlPromptStatus
    question: str
    details: str | None
    choices: tuple[dict[str, Any], ...]
    selected_choice_id: str | None
    selected_choice_label: str | None
    selected_choice_description: str | None
    control_envelope_json: str
    resume_context: dict[str, Any]
    session_id_snapshot: str | None
    prompt_message_id: str | None
    created_by: str
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "HitlPrompt":
        raw_choices = row.get("choices_json")
        choices: tuple[dict[str, Any], ...] = ()
        if isinstance(raw_choices, str) and raw_choices:
            try:
                parsed = json.loads(raw_choices)
                if isinstance(parsed, list):
                    normalized: list[dict[str, Any]] = []
                    for item in parsed:
                        if isinstance(item, dict):
                            normalized.append(
                                {
                                    "id": str(item.get("id") or ""),
                                    "label": str(item.get("label") or ""),
                                    "description": (
                                        str(item.get("description")).strip()
                                        if item.get("description") is not None
                                        else None
                                    ),
                                }
                            )
                    choices = tuple(normalized)
            except Exception:
                choices = ()

        raw_context = row.get("resume_context_json")
        context: dict[str, Any] = {}
        if isinstance(raw_context, str) and raw_context:
            try:
                parsed_context = json.loads(raw_context)
                if isinstance(parsed_context, dict):
                    context = parsed_context
            except Exception:
                context = {}
        elif isinstance(raw_context, dict):
            context = raw_context

        return cls(
            id=str(row["id"]),
            target_kind=str(row["target_kind"]),
            platform=str(row["platform"]),
            channel_id=str(row["channel_id"]),
            thread_id=str(row["thread_id"]),
            task_id=row.get("task_id"),
            agent_name=str(row["agent_name"]),
            status=str(row["status"]),
            question=str(row["question"]),
            details=row.get("details"),
            choices=choices,
            selected_choice_id=row.get("selected_choice_id"),
            selected_choice_label=row.get("selected_choice_label"),
            selected_choice_description=row.get("selected_choice_description"),
            control_envelope_json=str(row["control_envelope_json"]),
            resume_context=context,
            session_id_snapshot=row.get("session_id_snapshot"),
            prompt_message_id=row.get("prompt_message_id"),
            created_by=str(row["created_by"]),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            completed_at=row.get("completed_at"),
        )
