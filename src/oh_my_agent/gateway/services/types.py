"""Shared result types for the gateway service layer.

These are platform-neutral dataclasses returned by service methods.
Platform adapters consume them to render platform-specific output
(Discord markdown, Slack Block Kit, etc.).

Fields are intentionally minimal.  Add more as needed during service
extraction — do not pre-define fields that are not yet used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oh_my_agent.runtime.types import RuntimeTask


# ── Base ────────────────────────────────────────────────────────────── #

@dataclass
class ServiceResult:
    """Base result returned by every service method."""

    success: bool
    message: str


# ── Task results ────────────────────────────────────────────────────── #

@dataclass
class TaskSummary:
    """Lightweight task descriptor for list views."""

    task_id: str
    status: str
    task_type: str
    goal: str
    step_info: str | None = None


@dataclass
class TaskActionResult(ServiceResult):
    """Result of a single-task action (create, decide, stop, ...)."""

    task_id: str | None = None
    task_status: str | None = None
    detail: str | None = None
    task: RuntimeTask | None = None


@dataclass
class TaskListResult(ServiceResult):
    """Result of listing tasks."""

    tasks: list[TaskSummary] = field(default_factory=list)


# ── Doctor results ──────────────────────────────────────────────────── #

@dataclass
class DoctorSection:
    """One logical section in a doctor health report."""

    title: str
    lines: list[str] = field(default_factory=list)


@dataclass
class DoctorResult(ServiceResult):
    """Operator health snapshot."""

    sections: list[DoctorSection] = field(default_factory=list)


# ── Automation results ──────────────────────────────────────────────── #

@dataclass
class AutomationInfo:
    """Descriptor for a single automation entry."""

    name: str
    enabled: bool
    schedule: str | None = None
    delivery: str | None = None
    target: str | None = None
    agent: str | None = None
    skill_name: str | None = None
    last_run_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    last_task_id: str | None = None
    next_run_at: str | None = None
    author: str | None = None
    source_path: str | None = None


@dataclass
class AutomationStatusResult(ServiceResult):
    """Result of querying automation status."""

    automations: list[AutomationInfo] = field(default_factory=list)


# ── Interactive results ─────────────────────────────────────────────── #

@dataclass
class InteractiveDecision:
    """Platform-neutral action emitted from an interactive message."""

    entity_id: str
    action_id: str
    actor_id: str
    entity_kind: str | None = None
    message_id: str | None = None
