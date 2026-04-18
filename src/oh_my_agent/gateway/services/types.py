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
    timeout_seconds: int | None = None
    max_turns: int | None = None
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
    scheduler_timezone: str | None = None


# ── Interactive results ─────────────────────────────────────────────── #

@dataclass
class InteractiveDecision:
    """Platform-neutral action emitted from an interactive message."""

    entity_id: str
    action_id: str
    actor_id: str
    entity_kind: str | None = None
    message_id: str | None = None


# ── Memory results ──────────────────────────────────────────────────── #

@dataclass
class MemoryEntrySummary:
    """Lightweight memory descriptor for /memories listings."""

    memory_id: str
    summary: str
    category: str
    scope: str
    confidence: float
    observation_count: int
    last_observed_at: str


@dataclass
class MemoryListResult(ServiceResult):
    """Result of listing user memories."""

    entries: list[MemoryEntrySummary] = field(default_factory=list)
    total_active: int = 0
    category_filter: str | None = None


@dataclass
class MemoryActionResult(ServiceResult):
    """Result of a single-memory action (forget, memorize)."""

    memory_id: str | None = None
    judge_stats: dict[str, int] | None = None
    judge_action_count: int = 0


# ── Skill evaluation results ────────────────────────────────────────── #

@dataclass
class SkillStatRow:
    """Per-skill evaluation snapshot used by /skill_stats."""

    skill_name: str
    auto_disabled: bool
    total_invocations: int
    recent_invocations: int
    recent_successes: int
    recent_errors: int
    recent_timeouts: int
    recent_cancelled: int
    recent_avg_latency_ms: float
    thumbs_up: int
    thumbs_down: int
    net_feedback: int
    last_invoked_at: str | None = None
    merged_commit_hash: str | None = None
    auto_disabled_reason: str | None = None
    latest_evaluations: list[dict] = field(default_factory=list)


@dataclass
class SkillStatsResult(ServiceResult):
    """Result of querying skill evaluation stats."""

    stats: list[SkillStatRow] = field(default_factory=list)
    recent_days: int = 7
    skill_filter: str | None = None


@dataclass
class SkillToggleResult(ServiceResult):
    """Result of enabling/disabling a skill."""

    skill_name: str = ""
    now_enabled: bool = False
