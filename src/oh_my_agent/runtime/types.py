from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

TASK_STATUS_DRAFT = "DRAFT"
TASK_STATUS_PENDING = "PENDING"
TASK_STATUS_RUNNING = "RUNNING"
TASK_STATUS_VALIDATING = "VALIDATING"
TASK_STATUS_APPLIED = "APPLIED"
TASK_STATUS_BLOCKED = "BLOCKED"
TASK_STATUS_FAILED = "FAILED"
TASK_STATUS_TIMEOUT = "TIMEOUT"
TASK_STATUS_STOPPED = "STOPPED"
TASK_STATUS_REJECTED = "REJECTED"

TaskStatus = Literal[
    "DRAFT",
    "PENDING",
    "RUNNING",
    "VALIDATING",
    "APPLIED",
    "BLOCKED",
    "FAILED",
    "TIMEOUT",
    "STOPPED",
    "REJECTED",
]

DecisionAction = Literal["approve", "reject", "suggest"]
DecisionSource = Literal["button", "slash"]


@dataclass(frozen=True)
class RuntimeTask:
    id: str
    platform: str
    channel_id: str
    thread_id: str
    created_by: str
    goal: str
    preferred_agent: str | None
    status: TaskStatus
    step_no: int
    max_steps: int
    max_minutes: int
    test_command: str
    workspace_path: str | None
    decision_message_id: str | None
    blocked_reason: str | None
    error: str | None
    summary: str | None
    resume_instruction: str | None
    created_at: str | None
    started_at: str | None
    updated_at: str | None
    ended_at: str | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "RuntimeTask":
        return cls(
            id=str(row["id"]),
            platform=str(row["platform"]),
            channel_id=str(row["channel_id"]),
            thread_id=str(row["thread_id"]),
            created_by=str(row.get("created_by", "unknown")),
            goal=str(row["goal"]),
            preferred_agent=row.get("preferred_agent"),
            status=row["status"],
            step_no=int(row.get("step_no", 0)),
            max_steps=int(row.get("max_steps", 8)),
            max_minutes=int(row.get("max_minutes", 20)),
            test_command=str(row.get("test_command", "pytest -q")),
            workspace_path=row.get("workspace_path"),
            decision_message_id=row.get("decision_message_id"),
            blocked_reason=row.get("blocked_reason"),
            error=row.get("error"),
            summary=row.get("summary"),
            resume_instruction=row.get("resume_instruction"),
            created_at=row.get("created_at"),
            started_at=row.get("started_at"),
            updated_at=row.get("updated_at"),
            ended_at=row.get("ended_at"),
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
