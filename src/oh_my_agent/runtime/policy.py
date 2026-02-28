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

_SKILL_HINTS = (
    "create a skill",
    "make a skill",
    "write a skill",
    "build a skill",
    "new skill",
    "skill for",
    "turn this into a skill",
    "package this workflow",
    "automate this as a skill",
    "创建skill",
    "新建skill",
    "生成skill",
)

_ARTIFACT_HINTS = (
    "report",
    "summary",
    "summarize",
    "research",
    "headlines",
    "news",
    "analyze",
    "analysis",
    "collect",
    "gather",
    "brief",
    "markdown report",
    "日报",
    "报告",
    "总结",
    "汇总",
    "调研",
    "分析",
    "新闻",
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


def is_skill_intent(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in _SKILL_HINTS)


def is_artifact_intent(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in _ARTIFACT_HINTS)


def extract_skill_name(text: str, existing_skills: set[str] | None = None) -> tuple[str, bool]:
    patterns = [
        r"`([a-zA-Z0-9][a-zA-Z0-9-_]{0,62})`",
        r'"([a-zA-Z0-9][a-zA-Z0-9-_]{0,62})"',
        r"'([a-zA-Z0-9][a-zA-Z0-9-_]{0,62})'",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            name = _normalize_skill_slug(match.group(1))
            return name, bool(existing_skills and name in existing_skills)

    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    if not tokens:
        name = "new-skill"
    else:
        name = "-".join(tokens[:4])[:64].strip("-") or "new-skill"
    return name, bool(existing_skills and name in existing_skills)


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
    original_request: str | None,
    step_no: int,
    max_steps: int,
    prior_failure: str | None,
    resume_instruction: str | None,
) -> str:
    lines = [
        "You are executing an autonomous coding task loop.",
        f"Normalized goal: {goal}",
        f"Current step: {step_no}/{max_steps}",
        "",
    ]
    if original_request:
        lines.extend([
            "Original user request:",
            original_request,
            "",
        ])
    lines.extend([
        "Rules:",
        "- Make concrete repository changes toward the goal.",
        "- Use the original user request as the source of truth for exact file content or constraints.",
        "- You may do quick local checks, but the runtime will run the authoritative test command after your turn.",
        "- Do not emit TASK_STATE: BLOCKED only because a local sandbox test or socket-bind attempt fails inside your agent environment.",
        "- User approval/merge happens outside your loop. If the workspace changes are ready and tests pass, emit TASK_STATE: DONE instead of waiting for more user input.",
        "- If blocked by missing dependency/permission/context, emit TASK_STATE: BLOCKED.",
        "- When the goal is complete and tests pass, emit TASK_STATE: DONE.",
        "- Otherwise emit TASK_STATE: CONTINUE.",
        "- Always include exactly one marker line at the end:",
        "  TASK_STATE: CONTINUE|DONE|BLOCKED",
        "- If blocked, also include: BLOCK_REASON: <reason>",
    ])
    if prior_failure:
        lines.extend(["", "Previous test failure summary:", prior_failure])
    if resume_instruction:
        lines.extend(["", "Resume instruction from user:", resume_instruction])
    return "\n".join(lines)


def build_skill_prompt(
    *,
    skill_name: str,
    goal: str,
    original_request: str | None,
    step_no: int,
    max_steps: int,
    prior_failure: str | None,
    resume_instruction: str | None,
) -> str:
    lines = [
        "You are executing an autonomous skill-creation task loop.",
        f"Skill name: {skill_name}",
        f"Normalized goal: {goal}",
        f"Current step: {step_no}/{max_steps}",
        "",
    ]
    if original_request:
        lines.extend([
            "Original user request:",
            original_request,
            "",
        ])
    lines.extend([
        "Rules:",
        f"- Create or update the skill under skills/{skill_name}/ inside the current worktree.",
        "- Ensure skills/<name>/SKILL.md exists and includes valid YAML frontmatter with at least name and description.",
        "- Follow the skill-creator workflow: understand the request, plan the skill, edit files, then validate.",
        f"- Validate with: python skills/skill-creator/scripts/quick_validate.py skills/{skill_name}",
        "- The runtime will run the authoritative validation command after your turn.",
        "- If the skill is ready and validation should pass, emit TASK_STATE: DONE.",
        "- If more edits are needed, emit TASK_STATE: CONTINUE.",
        "- If blocked by missing context or requirements, emit TASK_STATE: BLOCKED.",
        "- Always include exactly one marker line at the end:",
        "  TASK_STATE: CONTINUE|DONE|BLOCKED",
        "- If blocked, also include: BLOCK_REASON: <reason>",
    ])
    if prior_failure:
        lines.extend(["", "Previous validation failure summary:", prior_failure])
    if resume_instruction:
        lines.extend(["", "Resume instruction from user:", resume_instruction])
    return "\n".join(lines)


def _normalize_skill_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", value.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:64] or "new-skill"
