from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskDecision:
    require_approval: bool
    reasons: list[str]


_LONG_TASK_HINTS = (
    "fix",
    "implement",
    "refactor",
    "write code",
    "run test",
    "run tests",
    "repair",
    "bug",
    "回归",
    "修复",
    "实现",
    "重构",
    "测试",
)

_HIGH_RISK_HINTS = (
    "pip install",
    "npm install",
    "apt ",
    "brew ",
    "network",
    "internet",
    ".env",
    "config.yaml",
    "deploy",
    "migration",
    "database",
    "production",
)

_DONE_RE = re.compile(r"^\s*TASK_STATE:\s*DONE\s*$", re.MULTILINE)
_BLOCKED_RE = re.compile(r"^\s*TASK_STATE:\s*BLOCKED\s*$", re.MULTILINE)
_CONTINUE_RE = re.compile(r"^\s*TASK_STATE:\s*CONTINUE\s*$", re.MULTILINE)
_BLOCK_REASON_RE = re.compile(r"^\s*BLOCK_REASON:\s*(.+?)\s*$", re.MULTILINE)


def is_long_task_intent(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in _LONG_TASK_HINTS)


def evaluate_strict_risk(
    text: str,
    *,
    max_steps: int,
    max_minutes: int,
) -> RiskDecision:
    reasons: list[str] = []
    lowered = text.lower()

    if max_steps > 8:
        reasons.append("steps_over_8")
    if max_minutes > 20:
        reasons.append("minutes_over_20")
    if any(hint in lowered for hint in _HIGH_RISK_HINTS):
        reasons.append("contains_sensitive_keywords")
    if "across the repo" in lowered or "all files" in lowered or "large refactor" in lowered:
        reasons.append("possible_large_change")

    return RiskDecision(require_approval=bool(reasons), reasons=reasons)


def parse_task_state(text: str) -> tuple[str, str | None]:
    """Parse TASK_STATE/BLOCK_REASON markers from agent output.

    Defaults to CONTINUE when marker is missing.
    """
    if _DONE_RE.search(text):
        return "DONE", None
    if _BLOCKED_RE.search(text):
        m = _BLOCK_REASON_RE.search(text)
        return "BLOCKED", m.group(1).strip() if m else "Agent marked task blocked."
    if _CONTINUE_RE.search(text):
        return "CONTINUE", None
    return "CONTINUE", None


def build_runtime_prompt(
    *,
    goal: str,
    step_no: int,
    max_steps: int,
    prior_failure: str | None,
    resume_instruction: str | None,
) -> str:
    lines = [
        "You are executing an autonomous coding task loop.",
        f"Goal: {goal}",
        f"Current step: {step_no}/{max_steps}",
        "",
        "Rules:",
        "- Make concrete repository changes toward the goal.",
        "- Run or update tests as needed.",
        "- If blocked by missing dependency/permission/context, emit TASK_STATE: BLOCKED.",
        "- When the goal is complete and tests pass, emit TASK_STATE: DONE.",
        "- Otherwise emit TASK_STATE: CONTINUE.",
        "- Always include exactly one marker line at the end:",
        "  TASK_STATE: CONTINUE|DONE|BLOCKED",
        "- If blocked, also include: BLOCK_REASON: <reason>",
    ]
    if prior_failure:
        lines.extend(["", "Previous test failure summary:", prior_failure])
    if resume_instruction:
        lines.extend(["", "Resume instruction from user:", resume_instruction])
    return "\n".join(lines)
