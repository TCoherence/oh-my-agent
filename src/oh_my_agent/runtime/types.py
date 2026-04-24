from __future__ import annotations

import json
from dataclasses import dataclass
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
NotificationKind = Literal["auth_required", "ask_user", "task_draft", "task_waiting_merge"]
NotificationSeverity = Literal["action_required"]
NotificationStatus = Literal["active", "resolved", "failed", "cancelled"]

# Standard HITL choice families — convenience constants, not enforced at type level.
HITL_CHOICES_APPROVAL = (
    {"id": "approve", "label": "Approve", "description": "Proceed as proposed"},
    {"id": "request_changes", "label": "Request changes", "description": "Ask for modifications"},
    {"id": "cancel", "label": "Cancel", "description": "Abort this action"},
)
HITL_CHOICES_CONTINUE = (
    {"id": "continue", "label": "Continue", "description": "Proceed"},
    {"id": "stop", "label": "Stop", "description": "Stop and discard"},
)

DecisionAction = Literal[
    "approve",
    "reject",
    "suggest",
    "merge",
    "discard",
    "request_changes",
    "rerun_bump_turns",
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
    agent_timeout_seconds: int | None
    agent_max_turns: int | None
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
    notify_channel_id: str | None = None

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
            agent_timeout_seconds=(
                int(row["agent_timeout_seconds"]) if row.get("agent_timeout_seconds") is not None else None
            ),
            agent_max_turns=(
                int(row["agent_max_turns"]) if row.get("agent_max_turns") is not None else None
            ),
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
            notify_channel_id=row.get("notify_channel_id"),
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
    # Optional budget overrides carried by `suggest` / `request_changes` actions.
    # When non-None, the runtime updates the task row's agent_max_turns /
    # agent_timeout_seconds before the next invocation, so the agent loop
    # re-reads them at the next temporary_max_turns / temporary_timeout bake.
    max_turns: int | None = None
    timeout_seconds: int | None = None


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


@dataclass(frozen=True)
class NotificationEvent:
    kind: NotificationKind
    platform: str
    channel_id: str
    thread_id: str
    title: str
    body: str
    dedupe_key: str
    severity: NotificationSeverity = "action_required"
    task_id: str | None = None
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class NotificationRecord:
    id: str
    kind: NotificationKind
    status: NotificationStatus
    platform: str
    channel_id: str
    thread_id: str
    task_id: str | None
    owner_user_id: str
    dedupe_key: str
    title: str
    body: str
    payload: dict[str, Any]
    thread_message_id: str | None
    dm_message_id: str | None
    created_at: str | None = None
    updated_at: str | None = None
    resolved_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "NotificationRecord":
        raw_payload = row.get("payload_json")
        payload: dict[str, Any] = {}
        if isinstance(raw_payload, str) and raw_payload:
            try:
                parsed = json.loads(raw_payload)
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = {}
        elif isinstance(raw_payload, dict):
            payload = raw_payload
        return cls(
            id=str(row["id"]),
            kind=str(row["kind"]),
            status=str(row["status"]),
            platform=str(row["platform"]),
            channel_id=str(row["channel_id"]),
            thread_id=str(row["thread_id"]),
            task_id=row.get("task_id"),
            owner_user_id=str(row["owner_user_id"]),
            dedupe_key=str(row["dedupe_key"]),
            title=str(row["title"]),
            body=str(row["body"]),
            payload=payload,
            thread_message_id=row.get("thread_message_id"),
            dm_message_id=row.get("dm_message_id"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            resolved_at=row.get("resolved_at"),
        )


@dataclass(frozen=True)
class AutomationRuntimeState:
    name: str
    platform: str
    channel_id: str
    enabled: bool
    last_run_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    last_task_id: str | None = None
    next_run_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "AutomationRuntimeState":
        return cls(
            name=str(row["name"]),
            platform=str(row["platform"]),
            channel_id=str(row["channel_id"]),
            enabled=bool(row.get("enabled", True)),
            last_run_at=row.get("last_run_at"),
            last_success_at=row.get("last_success_at"),
            last_error=row.get("last_error"),
            last_task_id=row.get("last_task_id"),
            next_run_at=row.get("next_run_at"),
            updated_at=row.get("updated_at"),
        )


@dataclass(frozen=True)
class AutomationPost:
    """Record of a message an automation posted into a channel.

    Used to wire Discord-style "reply to this message" into a follow-up thread
    that inherits the automation's artifacts as context.
    """

    platform: str
    channel_id: str
    message_id: str
    automation_name: str
    fired_at: str
    artifact_paths: list[str]
    agent_name: str | None = None
    skill_name: str | None = None
    task_id: str | None = None
    follow_up_thread_id: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "AutomationPost":
        raw_paths = row.get("artifact_paths")
        paths: list[str] = []
        if raw_paths:
            try:
                loaded = json.loads(raw_paths)
                if isinstance(loaded, list):
                    paths = [str(p) for p in loaded]
            except (TypeError, ValueError):
                paths = []
        return cls(
            platform=str(row["platform"]),
            channel_id=str(row["channel_id"]),
            message_id=str(row["message_id"]),
            automation_name=str(row["automation_name"]),
            fired_at=str(row["fired_at"]),
            artifact_paths=paths,
            agent_name=row.get("agent_name"),
            skill_name=row.get("skill_name"),
            task_id=row.get("task_id"),
            follow_up_thread_id=row.get("follow_up_thread_id"),
        )
