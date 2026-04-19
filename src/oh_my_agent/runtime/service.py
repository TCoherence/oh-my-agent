from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import fnmatch
import inspect
import json
import logging
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from oh_my_agent.auth.types import AuthFlow, CredentialHandle
from oh_my_agent.agents.base import AgentResponse
from oh_my_agent.agents.cli.base import _bounded_log_excerpt
from oh_my_agent.agents.registry import AgentRegistry
from oh_my_agent.control.protocol import (
    AskUserChoice,
    ProtocolError,
    extract_control_frame,
    parse_auth_challenge,
    parse_ask_user_challenge,
    parse_control_envelope,
    strip_control_frame_text,
)
from oh_my_agent.gateway.base import BaseChannel, IncomingMessage, OutgoingAttachment
from oh_my_agent.gateway.session import ChannelSession
from oh_my_agent.skills.frontmatter import read_skill_frontmatter, resolve_skill_frontmatter, skill_execution_limits
from oh_my_agent.runtime.notifications import NotificationManager
from oh_my_agent.runtime.policy import (
    is_artifact_intent,
    build_skill_prompt,
    build_runtime_prompt,
    extract_skill_name,
    evaluate_strict_risk,
    is_long_task_intent,
    parse_task_state,
)
from oh_my_agent.runtime.types import (
    TASK_COMPLETION_ARTIFACT,
    TASK_COMPLETION_MERGE,
    TASK_COMPLETION_REPLY,
    TASK_TYPE_ARTIFACT,
    TASK_TYPE_CODE,
    TASK_TYPE_REPO_CHANGE,
    TASK_TYPE_SKILL,
    TASK_TYPE_SKILL_CHANGE,
    TASK_STATUS_APPLIED,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_DISCARDED,
    TASK_STATUS_DRAFT,
    TASK_STATUS_FAILED,
    TASK_STATUS_MERGED,
    TASK_STATUS_MERGE_FAILED,
    TASK_STATUS_PAUSED,
    TASK_STATUS_PENDING,
    TASK_STATUS_REJECTED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_STOPPED,
    TASK_STATUS_TIMEOUT,
    TASK_STATUS_VALIDATING,
    TASK_STATUS_WAITING_USER_INPUT,
    TASK_STATUS_WAITING_MERGE,
    HitlPrompt,
    NotificationEvent,
    RuntimeTask,
    SuspendedAgentRun,
    TaskDecisionEvent,
)
from oh_my_agent.runtime.worktree import WorktreeError, WorktreeManager
from oh_my_agent.utils.chunker import chunk_message
from oh_my_agent.utils.errors import user_safe_agent_error
from oh_my_agent.utils.usage import append_usage_audit

logger = logging.getLogger(__name__)

_STATUS_MESSAGE_PREFIX = "**Task Status**"
_TERMINAL_MESSAGE_PREFIX = "**Task Update**"
_TASK_STATE_LINE_RE = re.compile(r"^\s*TASK_STATE:\s*\w+\s*$", re.MULTILINE)
_BLOCK_REASON_LINE_RE = re.compile(r"^\s*BLOCK_REASON:\s*.+\s*$", re.MULTILINE)

_TERMINAL_CLEANUP_STATUSES = {
    TASK_STATUS_APPLIED,  # legacy
    TASK_STATUS_COMPLETED,
    TASK_STATUS_MERGED,
    TASK_STATUS_DISCARDED,
    TASK_STATUS_MERGE_FAILED,
    TASK_STATUS_FAILED,
    TASK_STATUS_TIMEOUT,
    TASK_STATUS_STOPPED,
    TASK_STATUS_REJECTED,
}

_ACTIVE_AUTOMATION_TASK_STATUSES = {
    TASK_STATUS_DRAFT,
    TASK_STATUS_PENDING,
    TASK_STATUS_RUNNING,
    TASK_STATUS_VALIDATING,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_PAUSED,
    TASK_STATUS_WAITING_USER_INPUT,
    TASK_STATUS_WAITING_MERGE,
}

_TASK_LIVE_STATUSES = {
    TASK_STATUS_PENDING,
    TASK_STATUS_RUNNING,
    TASK_STATUS_VALIDATING,
    TASK_STATUS_WAITING_USER_INPUT,
    TASK_STATUS_BLOCKED,
    TASK_STATUS_PAUSED,
}

_PARTIAL_EXCERPT_MAX_CHARS = 2000


@dataclass(frozen=True)
class ArtifactDeliveryResult:
    mode: str
    delivered_paths: list[str]
    message_ids: list[str]
    summary_text: str
    attachment_names: list[str]
    archived_paths: list[str] = field(default_factory=list)


def _fmt_dt(dt) -> str:
    if dt is None:
        return "n/a"
    try:
        return dt.strftime("%H:%M:%S")
    except Exception:
        return str(dt)[:19]


class RuntimeService:
    """Autonomous task runtime for multi-step coding loops."""

    def __init__(
        self,
        store,
        *,
        config: dict[str, Any] | None = None,
        owner_user_ids: set[str] | None = None,
        repo_root: Path | None = None,
        skill_syncer=None,
        skills_path: Path | None = None,
        workspace_skills_dirs: list[Path] | None = None,
        auth_service=None,
    ) -> None:
        cfg = config or {}
        self._enabled = bool(cfg.get("enabled", True))
        self._worker_concurrency = int(cfg.get("worker_concurrency", 3))
        self._default_agent = str(cfg.get("default_agent", "codex"))
        self._default_test_command = str(cfg.get("default_test_command", "pytest -q"))
        self._default_max_steps = int(cfg.get("default_max_steps", 8))
        self._default_max_minutes = int(cfg.get("default_max_minutes", 20))
        self._risk_profile = str(cfg.get("risk_profile", "strict"))
        self._path_policy_mode = str(cfg.get("path_policy_mode", "allow_all_with_denylist"))
        self._allowed_paths = list(
            cfg.get("allowed_paths", ["src/**", "tests/**", "docs/**", "skills/**", "pyproject.toml"])
        )
        self._denied_paths = list(cfg.get("denied_paths", [".env", "config.yaml", ".workspace/**", ".git/**"]))
        self._decision_ttl_minutes = int(cfg.get("decision_ttl_minutes", 1440))
        self._agent_heartbeat_seconds = float(cfg.get("agent_heartbeat_seconds", 20))
        self._test_heartbeat_seconds = float(cfg.get("test_heartbeat_seconds", 15))
        self._test_timeout_seconds = float(cfg.get("test_timeout_seconds", 600))
        self._progress_notice_seconds = float(cfg.get("progress_notice_seconds", 30))
        self._progress_persist_seconds = float(cfg.get("progress_persist_seconds", 60))
        self._log_event_limit = int(cfg.get("log_event_limit", 12))
        self._log_tail_chars = int(cfg.get("log_tail_chars", 1200))

        cleanup_cfg = cfg.get("cleanup", {})
        self._cleanup_enabled = bool(cleanup_cfg.get("enabled", True))
        self._cleanup_interval_minutes = int(cleanup_cfg.get("interval_minutes", 60))
        self._cleanup_retention_hours = int(cleanup_cfg.get("retention_hours", 168))
        self._cleanup_prune_worktrees = bool(cleanup_cfg.get("prune_git_worktrees", True))
        self._cleanup_merged_immediately = bool(cleanup_cfg.get("merged_immediate", True))

        merge_cfg = cfg.get("merge_gate", {})
        self._merge_gate_enabled = bool(merge_cfg.get("enabled", True))
        self._merge_auto_commit = bool(merge_cfg.get("auto_commit", True))
        self._merge_require_clean_repo = bool(merge_cfg.get("require_clean_repo", True))
        self._merge_preflight_check = bool(merge_cfg.get("preflight_check", True))
        self._merge_target_branch_mode = str(merge_cfg.get("target_branch_mode", "current"))
        self._merge_commit_template = str(
            merge_cfg.get("commit_message_template", "runtime(task:{task_id}): {goal_short}")
        )

        self._skill_auto_approve = bool(cfg.get("skill_auto_approve", True))
        skill_eval_cfg = cfg.get("skill_evaluation", {})
        overlap_cfg = skill_eval_cfg.get("overlap_guard", {})
        source_cfg = skill_eval_cfg.get("source_grounded", {})
        self._skill_eval_enabled = bool(skill_eval_cfg.get("enabled", True))
        self._skill_overlap_guard_enabled = bool(overlap_cfg.get("enabled", True))
        self._skill_overlap_threshold = float(overlap_cfg.get("review_similarity_threshold", 0.45))
        self._skill_source_grounded_enabled = bool(source_cfg.get("enabled", True))
        self._skill_source_grounded_block_auto_merge = bool(source_cfg.get("block_auto_merge", True))

        self._store = store
        self._owner_user_ids = owner_user_ids or set()
        self._repo_root = (repo_root or Path.cwd()).resolve()
        self._skill_syncer = skill_syncer
        self._skills_path = skills_path
        self._workspace_skills_dirs = workspace_skills_dirs
        worktree_root = Path(cfg.get("worktree_root", "~/.oh-my-agent/runtime/tasks")).expanduser().resolve()
        self._runtime_workspace_root = worktree_root
        self._worktree = WorktreeManager(self._repo_root, worktree_root)
        self._logs_root = self._runtime_workspace_root.parent / "logs"
        self._thread_logs_root = self._logs_root / "threads"
        self._agent_logs_root = self._logs_root / "agents"  # Internal live spool files.
        self._logs_root.mkdir(parents=True, exist_ok=True)
        self._thread_logs_root.mkdir(parents=True, exist_ok=True)
        self._agent_logs_root.mkdir(parents=True, exist_ok=True)
        self._service_log_path = self._logs_root / "oh-my-agent.log"
        self._artifact_attachment_max_count = int(cfg.get("artifact_attachment_max_count", 5))
        self._artifact_attachment_max_bytes = int(cfg.get("artifact_attachment_max_bytes", 8 * 1024 * 1024))
        self._artifact_attachment_max_total_bytes = int(
            cfg.get("artifact_attachment_max_total_bytes", 20 * 1024 * 1024)
        )
        reports_cfg = cfg.get("reports_dir", "~/.oh-my-agent/reports")
        if reports_cfg in (None, False, ""):
            self._reports_dir: Path | None = None
        else:
            self._reports_dir = Path(str(reports_cfg)).expanduser().resolve()
        self._auth_service = auth_service
        if self._auth_service is not None:
            self._auth_service.add_listener(self._on_auth_flow_event)

        self._sessions: dict[str, ChannelSession] = {}
        self._registries: dict[str, AgentRegistry] = {}
        self._notifications = NotificationManager(
            store=self._store,
            owner_user_ids=self._owner_user_ids,
            session_lookup=lambda platform, channel_id: self._sessions.get(self._key(platform, channel_id)),
        )
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._live_agent_logs: dict[str, Path] = {}
        self._task_sources: dict[str, str] = {}
        self._workers: list[asyncio.Task] = []
        self._janitor_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    @staticmethod
    def _extract_skill_frontmatter(skill_md: Path) -> dict[str, Any]:
        return read_skill_frontmatter(skill_md)

    @staticmethod
    def _skill_description_from_dir(skill_dir: Path) -> str:
        meta = RuntimeService._extract_skill_frontmatter(skill_dir / "SKILL.md")
        return str(meta.get("description") or "").strip()

    def _skill_frontmatter_by_name(self, skill_name: str | None) -> dict[str, Any]:
        return resolve_skill_frontmatter(
            skill_name,
            repo_root=self._repo_root,
            skills_path=self._skills_path if isinstance(self._skills_path, Path) else None,
        )

    def _skill_timeout_seconds_by_name(self, skill_name: str | None) -> int | None:
        return skill_execution_limits(self._skill_frontmatter_by_name(skill_name)).timeout_seconds

    def _skill_max_turns_by_name(self, skill_name: str | None) -> int | None:
        return skill_execution_limits(self._skill_frontmatter_by_name(skill_name)).max_turns

    @staticmethod
    def _format_agent_failure_text(response: AgentResponse, *, prefix: str) -> str:
        lines = [prefix, user_safe_agent_error(response.error_kind)]
        partial = str(getattr(response, "partial_text", "") or "").strip()
        if partial:
            if response.error_kind == "max_turns":
                label = "Partial result before max turns"
            elif response.error_kind == "timeout":
                label = "Partial result before timeout"
            else:
                label = "Partial result before stop"
            lines.extend(
                [
                    "",
                    f"**{label}**",
                    f"```text\n{partial[-_PARTIAL_EXCERPT_MAX_CHARS:]}\n```",
                ]
            )
        return "\n".join(lines)[:1900]

    @staticmethod
    def _normalize_similarity_tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", text.lower()))

    @classmethod
    def _jaccard_similarity(cls, left: str, right: str) -> float:
        left_tokens = cls._normalize_similarity_tokens(left)
        right_tokens = cls._normalize_similarity_tokens(right)
        if not left_tokens or not right_tokens:
            return 0.0
        overlap = left_tokens & right_tokens
        union = left_tokens | right_tokens
        return len(overlap) / max(len(union), 1)

    @staticmethod
    def _extract_urls(text: str | None) -> list[str]:
        if not text:
            return []
        return re.findall(r"https?://[^\s)>]+", text)

    @classmethod
    def _has_external_source_signals(cls, text: str | None) -> bool:
        hay = (text or "").lower()
        if not hay:
            return False
        if cls._extract_urls(text):
            return True
        hints = (
            "adapt",
            "internalize",
            "based on",
            "reference",
            "github",
            "repo",
            "tool",
            "project",
            "参考",
            "内化",
            "基于",
            "改造成",
        )
        return any(hint in hay for hint in hints)

    def _skill_tree_summary(self, skill_dir: Path) -> str:
        if not skill_dir.exists():
            return "(skill directory missing)"
        lines: list[str] = []
        for path in sorted(skill_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(skill_dir).as_posix()
            lines.append(f"- {rel}")
            if rel == "SKILL.md":
                snippet = path.read_text(encoding="utf-8", errors="ignore")[:2000].strip()
                if snippet:
                    lines.append("```md")
                    lines.append(snippet[:1200])
                    lines.append("```")
            if len(lines) >= 24:
                break
        return "\n".join(lines) if lines else "(no files)"

    async def _record_skill_evaluation(
        self,
        *,
        skill_name: str,
        source_task_id: str,
        evaluation_type: str,
        status: str,
        summary: str,
        details_json: dict[str, Any] | None = None,
    ) -> None:
        if not hasattr(self._store, "add_skill_evaluation"):
            return
        await self._store.add_skill_evaluation(
            skill_name=skill_name,
            source_task_id=source_task_id,
            evaluation_type=evaluation_type,
            status=status,
            summary=summary,
            details_json=details_json,
        )

    async def _evaluate_skill_overlap(
        self,
        task: RuntimeTask,
        skill_dir: Path,
    ) -> dict[str, Any] | None:
        if (
            not self._skill_eval_enabled
            or not self._skill_overlap_guard_enabled
            or not self._skills_path
            or not task.skill_name
        ):
            return None
        candidate_description = self._skill_description_from_dir(skill_dir)
        candidate_text = " ".join(
            part for part in (task.skill_name, candidate_description, task.original_request or task.goal) if part
        )
        best: dict[str, Any] | None = None
        for child in sorted(self._skills_path.iterdir()):
            if not child.is_dir() or child.name == task.skill_name:
                continue
            existing_description = self._skill_description_from_dir(child)
            score = self._jaccard_similarity(
                candidate_text,
                " ".join(part for part in (child.name, existing_description) if part),
            )
            if best is None or score > float(best["score"]):
                best = {
                    "skill_name": child.name,
                    "description": existing_description,
                    "score": score,
                }
        if not best or float(best["score"]) < self._skill_overlap_threshold:
            if task.skill_name:
                await self._record_skill_evaluation(
                    skill_name=task.skill_name,
                    source_task_id=task.id,
                    evaluation_type="overlap",
                    status="pass",
                    summary="No strong overlap with existing skills detected.",
                    details_json={"top_match": best},
                )
            return None

        summary = (
            f"Candidate skill `{task.skill_name}` overlaps with existing skill "
            f"`{best['skill_name']}` (similarity={float(best['score']):.2f})."
        )
        result = {
            "evaluation_type": "overlap",
            "status": "review_required",
            "summary": summary,
            "details_json": {
                "candidate_skill": task.skill_name,
                "matched_skill": best["skill_name"],
                "matched_description": best["description"],
                "similarity": round(float(best["score"]), 3),
            },
        }
        await self._record_skill_evaluation(
            skill_name=task.skill_name,
            source_task_id=task.id,
            evaluation_type="overlap",
            status="review_required",
            summary=summary,
            details_json=result["details_json"],
        )
        return result

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any] | None:
        raw = text.strip()
        candidates = [raw]
        fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
        candidates.extend(fenced)
        brace_match = re.search(r"(\{.*\})", raw, flags=re.DOTALL)
        if brace_match:
            candidates.append(brace_match.group(1))
        for candidate in candidates:
            try:
                data = json.loads(candidate)
            except Exception:
                continue
            if isinstance(data, dict):
                return data
        return None

    async def _evaluate_skill_source_grounding(
        self,
        task: RuntimeTask,
        registry: AgentRegistry,
        skill_dir: Path,
    ) -> dict[str, Any] | None:
        request_text = task.original_request or task.goal
        if (
            not self._skill_eval_enabled
            or not self._skill_source_grounded_enabled
            or not task.skill_name
            or not self._has_external_source_signals(request_text)
        ):
            return None

        meta = self._extract_skill_frontmatter(skill_dir / "SKILL.md")
        metadata = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
        source_urls = metadata.get("source_urls")
        adapted_from = metadata.get("adapted_from")
        adaptation_notes = metadata.get("adaptation_notes")
        missing_fields = [
            field
            for field, value in (
                ("metadata.source_urls", source_urls),
                ("metadata.adapted_from", adapted_from),
                ("metadata.adaptation_notes", adaptation_notes),
            )
            if not value
        ]
        extracted_urls = self._extract_urls(request_text)
        if missing_fields:
            summary = (
                "External-source skill adaptation is missing required metadata: "
                + ", ".join(missing_fields)
            )
            details = {
                "missing_fields": missing_fields,
                "request_urls": extracted_urls,
            }
            await self._record_skill_evaluation(
                skill_name=task.skill_name,
                source_task_id=task.id,
                evaluation_type="source_grounded",
                status="review_required",
                summary=summary,
                details_json=details,
            )
            return {
                "evaluation_type": "source_grounded",
                "status": "review_required",
                "summary": summary,
                "details_json": details,
            }

        prompt = "\n".join(
            [
                "You are reviewing whether a generated skill is genuinely adapted from the referenced external source.",
                "Return strict JSON only with keys: status, summary, evidence.",
                "Allowed status values: pass, review_required.",
                "",
                f"Original request:\n{request_text}",
                "",
                f"Extracted source URLs: {json.dumps(extracted_urls, ensure_ascii=False)}",
                f"Skill metadata: {json.dumps(metadata, ensure_ascii=False)}",
                "",
                "Skill tree summary:",
                self._skill_tree_summary(skill_dir),
                "",
                "Mark review_required if the skill looks generic, thin, or not clearly grounded in the referenced source.",
            ]
        )
        agent_name, response = await registry.run(
            prompt,
            [],
            thread_id=f"{task.id}:source_grounded",
            force_agent=self._default_agent,
            workspace_override=skill_dir.parent.parent,
            run_label=f"source_grounded_eval task={task.id}",
        )
        del agent_name
        if response.error:
            summary = f"Source-grounded evaluation could not complete: {response.error[:200]}"
            details = {"error_kind": response.error_kind, "error": response.error[:400]}
            await self._record_skill_evaluation(
                skill_name=task.skill_name,
                source_task_id=task.id,
                evaluation_type="source_grounded",
                status="review_required",
                summary=summary,
                details_json=details,
            )
            return {
                "evaluation_type": "source_grounded",
                "status": "review_required",
                "summary": summary,
                "details_json": details,
            }

        payload = self._extract_json_object(response.text)
        status = str((payload or {}).get("status") or "review_required")
        if status not in {"pass", "review_required"}:
            status = "review_required"
        summary = str((payload or {}).get("summary") or "Source-grounded evaluation returned an invalid payload.")
        details = {"evidence": (payload or {}).get("evidence"), "raw": response.text[:1200]}
        await self._record_skill_evaluation(
            skill_name=task.skill_name,
            source_task_id=task.id,
            evaluation_type="source_grounded",
            status=status,
            summary=summary,
            details_json=details,
        )
        if status == "pass":
            return None
        return {
            "evaluation_type": "source_grounded",
            "status": status,
            "summary": summary,
            "details_json": details,
        }

    async def _evaluate_skill_task(
        self,
        task: RuntimeTask,
        *,
        registry: AgentRegistry,
        workspace: Path,
    ) -> list[dict[str, Any]]:
        if not task.skill_name or not self._skills_path:
            return []

        skill_dir = workspace / "skills" / task.skill_name
        findings: list[dict[str, Any]] = []
        await self._record_skill_evaluation(
            skill_name=task.skill_name,
            source_task_id=task.id,
            evaluation_type="structure",
            status="pass",
            summary="Skill passed quick_validate structural checks.",
            details_json={"validation_mode": "quick_validate"},
        )
        overlap = await self._evaluate_skill_overlap(task, skill_dir)
        if overlap:
            findings.append(overlap)
        source_grounded = await self._evaluate_skill_source_grounding(task, registry, skill_dir)
        if source_grounded:
            findings.append(source_grounded)
        return findings

    @staticmethod
    def _format_evaluation_findings(findings: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for item in findings:
            if item.get("status") != "review_required":
                continue
            lines.append(f"- `{item['evaluation_type']}`: {item['summary']}")
        return lines

    @property
    def enabled(self) -> bool:
        return self._enabled

    def register_session(self, session: ChannelSession, registry: AgentRegistry) -> None:
        key = self._key(session.platform, session.channel_id)
        self._sessions[key] = session
        self._registries[key] = registry

    async def start(self) -> None:
        if not self._enabled:
            return
        if self._auth_service is not None:
            await self._auth_service.start()
        requeued = await self._store.requeue_inflight_runtime_tasks()
        cleaned_on_start = 0
        cleaned_logs_on_start = 0
        if self._cleanup_enabled:
            cleaned_on_start = await self._cleanup_expired_tasks()
            cleaned_logs_on_start = await self._cleanup_expired_agent_logs()
        for idx in range(self._worker_concurrency):
            self._workers.append(
                asyncio.create_task(self._worker_loop(idx), name=f"runtime-worker-{idx}")
            )
        if self._cleanup_enabled:
            self._janitor_task = asyncio.create_task(self._janitor_loop(), name="runtime-janitor")
        logger.info(
            "Runtime started with %d worker(s)%s%s%s%s",
            len(self._workers),
            " + janitor" if self._janitor_task else "",
            f"; requeued {requeued} inflight task(s)" if requeued else "",
            f"; cleaned {cleaned_on_start} stale workspace(s) on start" if cleaned_on_start else "",
            f"; pruned {cleaned_logs_on_start} stale agent log(s) on start" if cleaned_logs_on_start else "",
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._auth_service is not None:
            await self._auth_service.stop()
        if self._janitor_task:
            self._janitor_task.cancel()
        for task in self._workers:
            task.cancel()
        waiters = [*self._workers]
        if self._janitor_task:
            waiters.append(self._janitor_task)
        if waiters:
            await asyncio.gather(*waiters, return_exceptions=True)

    async def maybe_handle_incoming(
        self,
        session: ChannelSession,
        registry: AgentRegistry,
        msg: IncomingMessage,
        *,
        thread_id: str,
    ) -> bool:
        if not self._enabled:
            return False
        if msg.system:
            return False

        if await self.maybe_handle_thread_context(session, msg, thread_id=thread_id):
            return True

        actor = msg.author_id or msg.author
        if is_artifact_intent(msg.content):
            await self.create_artifact_task(
                session=session,
                registry=registry,
                thread_id=thread_id,
                goal=msg.content,
                raw_request=msg.content,
                created_by=actor,
                preferred_agent=msg.preferred_agent,
                source="message",
            )
            return True

        # 2. Long task intent → create repo-change task
        if not is_long_task_intent(msg.content):
            return False

        await self.create_repo_change_task(
            session=session,
            registry=registry,
            thread_id=thread_id,
            goal=msg.content,
            raw_request=msg.content,
            created_by=actor,
            preferred_agent=msg.preferred_agent,
            source="message",
        )
        return True

    async def maybe_handle_thread_context(
        self,
        session: ChannelSession,
        msg: IncomingMessage,
        *,
        thread_id: str,
    ) -> bool:
        if not self._enabled:
            return False
        if msg.system:
            return False

        actor = msg.author_id or msg.author
        active = await self._active_task_for_thread(session.platform, session.channel_id, thread_id)
        if active is None:
            suspended = await self._store.get_active_suspended_agent_run(
                platform=session.platform,
                channel_id=session.channel_id,
                thread_id=thread_id,
            )
            if suspended and suspended.status == "waiting_auth" and self._is_auth_retry_intent(msg.content):
                result = await self.start_auth_login(
                    platform=session.platform,
                    channel_id=session.channel_id,
                    thread_id=thread_id,
                    provider=suspended.provider,
                    actor_id=actor,
                    force_new=True,
                )
                await session.channel.send(thread_id, result)
                return True
            active_prompt = await self._store.get_active_hitl_prompt_for_thread(
                platform=session.platform,
                channel_id=session.channel_id,
                thread_id=thread_id,
            )
            if active_prompt is not None:
                await session.channel.send(
                    thread_id,
                    "This thread is waiting for an owner selection. Use the buttons on the active input prompt to answer or cancel it.",
                )
                return True
            return False

        # 1. Try control command first (stop/pause/resume)
        control = self._parse_control_intent(msg.content, active)
        if control is not None:
            action, instruction = control
            if action == "stop":
                result = await self.stop_task(active.id, actor_id=actor)
            elif action == "pause":
                result = await self.pause_task(active.id, actor_id=actor)
            elif action == "resume":
                result = await self.resume_task(active.id, instruction, actor_id=actor)
            elif action == "retry_merge":
                result = await self.merge_task(active.id, actor_id=actor)
            elif action == "wait":
                result = await self.wait_task(active.id, actor_id=actor)
            elif action == "discard":
                result = await self.discard_task(active.id, actor_id=actor)
            else:
                result = "Unknown control action."
            await session.channel.send(thread_id, result)
            return True

        if active.status == TASK_STATUS_WAITING_USER_INPUT:
            active_prompt = await self._store.get_active_hitl_prompt_for_task(active.id)
            if active_prompt is not None:
                await session.channel.send(
                    thread_id,
                    "This task is waiting for an owner selection. Use the buttons on the active input prompt to answer or cancel it.",
                )
                return True

        if active.status == TASK_STATUS_WAITING_USER_INPUT and self._is_auth_retry_intent(msg.content):
            result = await self.start_auth_login(
                platform=session.platform,
                channel_id=session.channel_id,
                thread_id=thread_id,
                provider="bilibili",
                actor_id=actor,
                linked_task_id=active.id,
                force_new=True,
            )
            await session.channel.send(thread_id, result)
            return True

        # 2. Auto-resume: if thread has a BLOCKED/PAUSED task, resume it regardless of intent.
        #    This takes priority over new task creation — the user is replying to the blocked task.
        if active.status in {TASK_STATUS_BLOCKED, TASK_STATUS_PAUSED}:
            result = await self.resume_task(active.id, msg.content, actor_id=actor)
            await session.channel.send(thread_id, result)
            return True

        return False

    async def create_task(
        self,
        *,
        session: ChannelSession,
        registry: AgentRegistry,
        thread_id: str,
        goal: str,
        raw_request: str | None = None,
        created_by: str,
        preferred_agent: str | None = None,
        test_command: str | None = None,
        max_steps: int | None = None,
        max_minutes: int | None = None,
        source: str,
        force_draft: bool = False,
        auto_approve: bool = False,
        task_type: str = TASK_TYPE_REPO_CHANGE,
        completion_mode: str = TASK_COMPLETION_MERGE,
        output_summary: str | None = None,
        artifact_manifest: list[str] | None = None,
        skill_name: str | None = None,
        automation_name: str | None = None,
        agent_timeout_seconds: int | None = None,
        agent_max_turns: int | None = None,
    ) -> RuntimeTask:
        self.register_session(session, registry)

        steps = int(max_steps or self._default_max_steps)
        minutes = int(max_minutes or self._default_max_minutes)
        command = test_command or self._default_test_command
        chosen_agent = preferred_agent or self._default_agent

        require_approval = False
        reasons: list[str] = []
        if not auto_approve and self._risk_profile == "strict":
            risk = evaluate_strict_risk(goal, max_steps=steps, max_minutes=minutes)
            require_approval = risk.require_approval
            reasons = risk.reasons

        task_id = uuid.uuid4().hex[:12]
        status = TASK_STATUS_DRAFT if (force_draft or require_approval) else TASK_STATUS_PENDING

        task = await self._store.create_runtime_task(
            task_id=task_id,
            platform=session.platform,
            channel_id=session.channel_id,
            thread_id=thread_id,
            created_by=created_by,
            goal=goal,
            original_request=raw_request or goal,
            preferred_agent=chosen_agent,
            status=status,
            max_steps=steps,
            max_minutes=minutes,
            test_command=command,
            completion_mode=completion_mode,
            output_summary=output_summary,
            artifact_manifest=artifact_manifest,
            automation_name=automation_name,
            task_type=task_type,
            skill_name=skill_name,
            agent_timeout_seconds=agent_timeout_seconds,
            agent_max_turns=agent_max_turns,
        )
        await self._store.add_runtime_event(
            task.id,
            "task.created",
            {"source": source, "status": status, "risk_reasons": reasons, "force_draft": force_draft, "auto_approve": auto_approve},
        )
        self._task_sources[task.id] = source
        logger.info(
            "Runtime task created id=%s status=%s source=%s agent=%s budget=%d/%d",
            task.id,
            status,
            source,
            chosen_agent,
            steps,
            minutes,
        )

        if status == TASK_STATUS_DRAFT:
            nonce = await self._store.create_runtime_decision_nonce(
                task.id,
                ttl_minutes=self._decision_ttl_minutes,
            )
            draft_text = self._draft_text(task, reasons=reasons)
            msg_id = await self._send_decision_surface(
                session,
                thread_id,
                draft_text,
                task.id,
                nonce,
                ["approve", "reject", "suggest"],
            )
            if msg_id:
                await self._store.update_runtime_task(task.id, decision_message_id=msg_id)
            await self._notify(
                task,
                f"Task `{task.id}` is waiting for approval. Use buttons or `/task_approve {task.id}`.",
                record_history=True,
            )
            await self._signal_status_by_id(task, TASK_STATUS_DRAFT)
            await self._notify_task_draft_required(task, reasons=reasons)
        else:
            await self._notify(
                task,
                f"Task `{task.id}` queued (`{chosen_agent}`), max {steps} steps / {minutes} min.",
                record_history=True,
            )
            await self._signal_status_by_id(task, TASK_STATUS_PENDING)

        return task

    async def create_repo_change_task(
        self,
        *,
        session: ChannelSession,
        registry: AgentRegistry,
        thread_id: str,
        goal: str,
        raw_request: str | None = None,
        created_by: str,
        preferred_agent: str | None = None,
        test_command: str | None = None,
        max_steps: int | None = None,
        max_minutes: int | None = None,
        source: str,
        force_draft: bool = False,
    ) -> RuntimeTask:
        return await self.create_task(
            session=session,
            registry=registry,
            thread_id=thread_id,
            goal=goal,
            raw_request=raw_request,
            created_by=created_by,
            preferred_agent=preferred_agent,
            test_command=test_command,
            max_steps=max_steps,
            max_minutes=max_minutes,
            source=source,
            force_draft=force_draft,
            task_type=TASK_TYPE_REPO_CHANGE,
            completion_mode=TASK_COMPLETION_MERGE,
        )

    async def create_artifact_task(
        self,
        *,
        session: ChannelSession,
        registry: AgentRegistry,
        thread_id: str,
        goal: str,
        raw_request: str | None = None,
        created_by: str,
        preferred_agent: str | None = None,
        test_command: str | None = None,
        max_steps: int | None = None,
        max_minutes: int | None = None,
        source: str,
        force_draft: bool = False,
        auto_approve: bool = False,
        automation_name: str | None = None,
        skill_name: str | None = None,
        agent_timeout_seconds: int | None = None,
        agent_max_turns: int | None = None,
    ) -> RuntimeTask:
        return await self.create_task(
            session=session,
            registry=registry,
            thread_id=thread_id,
            goal=goal,
            raw_request=raw_request,
            created_by=created_by,
            preferred_agent=preferred_agent,
            test_command=test_command or "true",
            max_steps=max_steps,
            max_minutes=max_minutes,
            source=source,
            force_draft=force_draft,
            auto_approve=auto_approve,
            task_type=TASK_TYPE_ARTIFACT,
            completion_mode=TASK_COMPLETION_REPLY,
            automation_name=automation_name,
            skill_name=skill_name,
            agent_timeout_seconds=agent_timeout_seconds,
            agent_max_turns=agent_max_turns,
        )

    async def create_skill_task(
        self,
        *,
        session: ChannelSession,
        registry: AgentRegistry,
        thread_id: str,
        goal: str,
        raw_request: str | None = None,
        created_by: str,
        preferred_agent: str | None = None,
        skill_name: str,
        source: str,
    ) -> RuntimeTask:
        existing = (
            {d.name for d in self._skills_path.iterdir() if d.is_dir()}
            if self._skills_path and self._skills_path.is_dir()
            else None
        )
        resolved_name, is_update = extract_skill_name(skill_name or goal, existing)
        effective_goal = f"Update existing skill '{resolved_name}': {goal}" if is_update else goal
        skill_timeout = self._skill_timeout_seconds_by_name(resolved_name)
        skill_max_turns = self._skill_max_turns_by_name(resolved_name)
        return await self.create_task(
            session=session,
            registry=registry,
            thread_id=thread_id,
            goal=effective_goal,
            raw_request=raw_request,
            created_by=created_by,
            preferred_agent=preferred_agent or self._default_agent,
            test_command=f"python skills/skill-creator/scripts/quick_validate.py skills/{resolved_name}",
            max_steps=6,
            max_minutes=15,
            source=source,
            force_draft=not self._skill_auto_approve,
            task_type=TASK_TYPE_SKILL_CHANGE,
            completion_mode=TASK_COMPLETION_MERGE,
            skill_name=resolved_name,
            agent_timeout_seconds=skill_timeout,
            agent_max_turns=skill_max_turns,
        )

    async def enqueue_scheduler_task(
        self,
        *,
        session: ChannelSession,
        registry: AgentRegistry,
        thread_id: str,
        automation_name: str,
        prompt: str,
        author: str,
        preferred_agent: str | None,
        skill_name: str | None = None,
        timeout_seconds: int | None = None,
        max_turns: int | None = None,
        auto_approve: bool = False,
    ) -> RuntimeTask | None:
        tasks = await self._store.list_runtime_tasks(
            platform=session.platform,
            channel_id=session.channel_id,
            limit=200,
        )
        for task in tasks:
            if task.automation_name != automation_name:
                continue
            if task.status in _ACTIVE_AUTOMATION_TASK_STATUSES:
                logger.info(
                    "Scheduler job '%s' skipped: existing task %s is still %s",
                    automation_name,
                    task.id,
                    task.status,
                )
                if task.status == TASK_STATUS_DRAFT:
                    await self._remind_blocking_draft(
                        session=session,
                        thread_id=thread_id,
                        task=task,
                        automation_name=automation_name,
                    )
                return None

        effective_timeout_seconds = timeout_seconds or self._skill_timeout_seconds_by_name(skill_name)
        effective_max_turns = max_turns or self._skill_max_turns_by_name(skill_name)

        max_minutes = 10
        if effective_timeout_seconds is not None:
            max_minutes = max(1, (effective_timeout_seconds + 59) // 60)

        return await self.create_artifact_task(
            session=session,
            registry=registry,
            thread_id=thread_id,
            goal=prompt,
            raw_request=prompt,
            created_by=author,
            preferred_agent=preferred_agent,
            test_command="true",
            max_steps=1,
            max_minutes=max_minutes,
            source="scheduler",
            auto_approve=auto_approve,
            automation_name=automation_name,
            skill_name=skill_name,
            agent_timeout_seconds=effective_timeout_seconds,
            agent_max_turns=effective_max_turns,
        )

    async def get_task(self, task_id: str) -> RuntimeTask | None:
        return await self._store.get_runtime_task(task_id)

    async def list_tasks(
        self,
        *,
        platform: str,
        channel_id: str,
        status: str | None = None,
        limit: int = 20,
    ) -> list[RuntimeTask]:
        return await self._store.list_runtime_tasks(
            platform=platform,
            channel_id=channel_id,
            status=status,
            limit=limit,
        )

    async def stop_task(self, task_id: str, *, actor_id: str) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can stop tasks."
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."
        await self._store.update_runtime_task(
            task_id,
            status=TASK_STATUS_STOPPED,
            summary="Stopped by user.",
            ended_at_now=True,
        )
        await self._store.add_runtime_event(task_id, "task.stopped", {"actor_id": actor_id})
        # The heartbeat loop in _invoke_agent will detect STOPPED status and cancel the agent.
        await self._notify(task, f"Task `{task.id}` stopped.")
        await self._signal_status_by_id(task, TASK_STATUS_STOPPED)
        return f"Task `{task.id}` stopped."

    async def pause_task(self, task_id: str, *, actor_id: str) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can pause tasks."
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."
        if task.status not in {TASK_STATUS_RUNNING, TASK_STATUS_VALIDATING, TASK_STATUS_PENDING}:
            return f"Task `{task.id}` cannot be paused (current status: {task.status})."
        await self._store.update_runtime_task(
            task_id,
            status=TASK_STATUS_PAUSED,
            summary="Paused by user.",
            ended_at=None,
        )
        await self._store.add_runtime_event(task_id, "task.paused", {"actor_id": actor_id})
        # The heartbeat loop in _invoke_agent will detect PAUSED status and cancel the agent.
        await self._notify(task, f"Task `{task.id}` paused. Reply with instructions to resume.")
        await self._signal_status_by_id(task, TASK_STATUS_PAUSED)
        return f"Task `{task.id}` paused."

    async def resume_task(self, task_id: str, instruction: str, *, actor_id: str) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can resume tasks."
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."
        if task.status not in {TASK_STATUS_BLOCKED, TASK_STATUS_PAUSED}:
            return f"Task `{task.id}` is not blocked or paused (current status: {task.status})."
        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_PENDING,
            blocked_reason=None,
            resume_instruction=instruction.strip() or None,
            ended_at=None,
        )
        await self._store.add_runtime_event(
            task.id,
            "task.resumed",
            {"actor_id": actor_id, "instruction": instruction},
        )
        self._task_sources[task.id] = "resume"
        await self._notify(task, f"Task `{task.id}` resumed and queued.")
        await self._signal_status_by_id(task, TASK_STATUS_PENDING)
        return f"Task `{task.id}` resumed and queued."

    async def start_auth_login(
        self,
        *,
        platform: str,
        channel_id: str,
        thread_id: str,
        provider: str,
        actor_id: str,
        linked_task_id: str | None = None,
        force_new: bool = False,
    ) -> str:
        if not self._auth_service or not self._auth_service.enabled:
            return "Auth service is not enabled."
        if not self._owner_user_ids:
            return "Auth commands are disabled because no owner_user_ids are configured."
        if not self._is_authorized(actor_id):
            return "This command is restricted to the configured owner."
        try:
            flow = await self._auth_service.start_qr_flow(
                provider,
                owner_user_id=actor_id,
                platform=platform,
                channel_id=channel_id,
                thread_id=thread_id,
                linked_task_id=linked_task_id,
                force_new=force_new,
            )
            session = self._sessions.get(self._key(platform, channel_id))
            if session is None:
                return f"Started `{provider}` auth flow `{flow.id}`, but there is no live session to deliver the QR code."
            try:
                await self._send_auth_prompt(session.channel, flow)
            except Exception as exc:
                logger.warning(
                    "Auth flow created but delivery failed flow=%s provider=%s thread=%s",
                    flow.id,
                    provider,
                    thread_id,
                    exc_info=True,
                )
                return (
                    f"Auth flow `{flow.id}` for `{provider}` is ready, "
                    f"but failed to deliver the QR code in thread `{thread_id}`: {exc}"
                )
            return f"Auth flow `{flow.id}` for `{provider}` is ready. QR code sent to the current thread."
        except Exception as exc:
            logger.warning("Failed to start auth flow provider=%s actor=%s", provider, actor_id, exc_info=True)
            return f"Failed to start `{provider}` auth flow: {exc}"

    async def get_auth_status(self, *, provider: str, actor_id: str) -> str:
        if not self._auth_service or not self._auth_service.enabled:
            return "Auth service is not enabled."
        if not self._owner_user_ids:
            return "Auth commands are disabled because no owner_user_ids are configured."
        if not self._is_authorized(actor_id):
            return "This command is restricted to the configured owner."

        try:
            status = await self._auth_service.get_status(provider, actor_id)
        except Exception as exc:
            logger.warning("Failed to read auth status provider=%s actor=%s", provider, actor_id, exc_info=True)
            return f"Failed to read `{provider}` auth status: {exc}"
        credential = status["credential"]
        flow = status["active_flow"]
        lines = [f"**Auth status** `{provider}`"]
        if credential is None:
            lines.append("- Credential: none")
        else:
            lines.append(f"- Credential: `{credential.status}`")
            lines.append(f"- Path: `{credential.storage_path}`")
            if credential.last_verified_at:
                lines.append(f"- Last verified: `{credential.last_verified_at}`")
            if credential.expires_at:
                lines.append(f"- Expires: `{credential.expires_at}`")
        if flow is None:
            lines.append("- Active flow: none")
        else:
            lines.append(f"- Active flow: `{flow.id}` [{flow.status}]")
            lines.append(f"- Thread: `{flow.thread_id}`")
            if flow.linked_task_id:
                lines.append(f"- Linked task: `{flow.linked_task_id}`")
            if flow.expires_at:
                lines.append(f"- Flow expires: `{flow.expires_at}`")
        return "\n".join(lines)

    async def clear_auth(self, *, provider: str, actor_id: str) -> str:
        if not self._auth_service or not self._auth_service.enabled:
            return "Auth service is not enabled."
        if not self._owner_user_ids:
            return "Auth commands are disabled because no owner_user_ids are configured."
        if not self._is_authorized(actor_id):
            return "This command is restricted to the configured owner."
        try:
            await self._auth_service.clear_credential(provider, actor_id)
        except Exception as exc:
            logger.warning("Failed to clear auth provider=%s actor=%s", provider, actor_id, exc_info=True)
            return f"Failed to clear `{provider}` auth state: {exc}"
        return f"Cleared `{provider}` credential and cancelled any active auth flow."

    async def mark_task_auth_required(
        self,
        task_id: str,
        *,
        provider: str,
        reason: str,
    ) -> str:
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."
        if not self._auth_service or not self._auth_service.enabled:
            return "Auth service is not enabled."
        if not self._owner_user_ids:
            return "Auth commands are disabled because no owner_user_ids are configured."
        if not self._is_authorized(task.created_by):
            return f"Task `{task.id}` was not created by an authorized owner."
        session = self._session_for(task)
        if session is None:
            return f"Task `{task.id}` has no live session bound to its channel."

        try:
            flow = await self._auth_service.start_qr_flow(
                provider,
                owner_user_id=task.created_by,
                platform=task.platform,
                channel_id=task.channel_id,
                thread_id=task.thread_id,
                linked_task_id=task.id,
                force_new=False,
            )
        except Exception as exc:
            logger.warning("Failed to trigger auth-required flow for task=%s provider=%s", task.id, provider, exc_info=True)
            return f"Failed to start `{provider}` auth flow for task `{task.id}`: {exc}"
        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_WAITING_USER_INPUT,
            blocked_reason=f"Awaiting {provider} login ({reason}).",
            resume_instruction=None,
            ended_at=None,
        )
        await self._store.add_runtime_event(
            task.id,
            "task.auth_required",
            {"provider": provider, "reason": reason, "flow_id": flow.id},
        )
        updated = await self._store.get_runtime_task(task.id)
        try:
            await self._send_auth_prompt(session.channel, flow)
        except Exception as exc:
            logger.warning(
                "Task auth flow delivery failed task=%s flow=%s provider=%s",
                task.id,
                flow.id,
                provider,
                exc_info=True,
            )
            return (
                f"Task `{task.id}` is waiting for `{provider}` login, "
                f"but QR delivery failed in thread `{task.thread_id}`: {exc}"
            )
        await self._notify_task_auth_required(task, provider=provider)
        if updated is not None:
            await self._signal_status_by_id(updated, TASK_STATUS_WAITING_USER_INPUT)
        return f"Task `{task.id}` is waiting for `{provider}` login."

    async def mark_thread_auth_required(
        self,
        *,
        platform: str,
        channel_id: str,
        thread_id: str,
        provider: str,
        reason: str,
        actor_id: str,
        agent_name: str,
        control_envelope_json: str,
        resume_context: dict[str, Any] | None = None,
        session_id_snapshot: str | None = None,
    ) -> str:
        if not self._auth_service or not self._auth_service.enabled:
            return "Auth service is not enabled."
        if not self._owner_user_ids:
            return "Auth commands are disabled because no owner_user_ids are configured."
        if not self._is_authorized(actor_id):
            return "This action is restricted to the configured owner."
        session = self._sessions.get(self._key(platform, channel_id))
        if session is None:
            return f"Thread `{thread_id}` has no live session bound to its channel."

        existing = await self._store.get_active_suspended_agent_run(
            platform=platform,
            channel_id=channel_id,
            thread_id=thread_id,
        )
        if existing is None:
            run = await self._store.create_suspended_agent_run(
                run_id=uuid.uuid4().hex[:12],
                platform=platform,
                channel_id=channel_id,
                thread_id=thread_id,
                agent_name=agent_name,
                status="waiting_auth",
                provider=provider,
                control_envelope_json=control_envelope_json,
                session_id_snapshot=session_id_snapshot,
                resume_context_json=resume_context or {},
                created_by=actor_id,
            )
        else:
            run = await self._store.update_suspended_agent_run(
                existing.id,
                agent_name=agent_name,
                status="waiting_auth",
                provider=provider,
                control_envelope_json=control_envelope_json,
                session_id_snapshot=session_id_snapshot,
                resume_context_json=resume_context or existing.resume_context,
                created_by=actor_id,
                completed_at=None,
            ) or existing
        try:
            flow = await self._auth_service.start_qr_flow(
                provider,
                owner_user_id=actor_id,
                platform=platform,
                channel_id=channel_id,
                thread_id=thread_id,
                linked_task_id=None,
                force_new=False,
            )
        except Exception as exc:
            logger.warning(
                "Failed to trigger thread auth-required flow thread=%s provider=%s",
                thread_id,
                provider,
                exc_info=True,
            )
            await self._store.update_suspended_agent_run(
                run.id,
                status="failed",
                resume_context_json={**(resume_context or {}), "auth_error": str(exc)},
                completed_at_now=True,
            )
            return f"Failed to start `{provider}` auth flow: {exc}"
        await self._store.update_suspended_agent_run(
            run.id,
            status="waiting_auth",
            resume_context_json={**(resume_context or {}), "auth_reason": reason, "flow_id": flow.id},
        )
        try:
            await self._send_auth_prompt(session.channel, flow)
        except Exception as exc:
            logger.warning(
                "Thread auth flow delivery failed thread=%s flow=%s provider=%s",
                thread_id,
                flow.id,
                provider,
                exc_info=True,
            )
            await self._store.update_suspended_agent_run(
                run.id,
                resume_context_json={
                    **(resume_context or {}),
                    "auth_reason": reason,
                    "flow_id": flow.id,
                    "auth_delivery_error": str(exc),
                },
            )
            return (
                f"Thread `{thread_id}` is waiting for `{provider}` login, "
                f"but QR delivery failed: {exc}"
            )
        await self._notify_thread_auth_required(
            platform=platform,
            channel_id=channel_id,
            thread_id=thread_id,
            provider=provider,
        )
        return f"Thread `{thread_id}` is waiting for `{provider}` login."

    async def get_hitl_prompt(self, prompt_id: str) -> HitlPrompt | None:
        return await self._store.get_hitl_prompt(prompt_id)

    async def list_active_hitl_prompts(
        self,
        *,
        platform: str | None = None,
        channel_id: str | None = None,
        limit: int = 100,
    ) -> list[HitlPrompt]:
        return await self._store.list_active_hitl_prompts(
            platform=platform,
            channel_id=channel_id,
            limit=limit,
        )

    async def build_doctor_report(
        self,
        *,
        platform: str,
        channel_id: str,
        scheduler=None,
    ) -> str:
        tasks = await self._store.list_runtime_tasks(
            platform=platform,
            channel_id=channel_id,
            limit=500,
        )
        status_counts: dict[str, int] = {}
        for task in tasks:
            status_counts[task.status] = status_counts.get(task.status, 0) + 1
        important_statuses = [
            TASK_STATUS_DRAFT,
            TASK_STATUS_RUNNING,
            TASK_STATUS_WAITING_MERGE,
            TASK_STATUS_WAITING_USER_INPUT,
            TASK_STATUS_BLOCKED,
        ]
        active_task_count = sum(status_counts.get(status, 0) for status in important_statuses)
        prompts = await self.list_active_hitl_prompts(platform=platform, channel_id=channel_id, limit=200)
        active_auth_flows = await self._store.list_active_auth_flows(limit=100)
        active_auth_waits = len(
            [flow for flow in active_auth_flows if flow.platform == platform and flow.channel_id == channel_id]
        )
        lines = [
            "**Runtime health**",
            f"- Enabled: `{self._enabled}`",
            f"- Workers: `{self._worker_concurrency}`",
            f"- Default agent: `{self._default_agent}`",
            f"- Active tasks: `{active_task_count}`",
            f"- Recent tasks: `{len(tasks)}`",
        ]
        if any(status_counts.get(status, 0) for status in important_statuses):
            lines.append("- Task counts:")
            for status in important_statuses:
                lines.append(f"  - `{status}`: {status_counts.get(status, 0)}")
        prompt_waiting = sum(1 for p in prompts if p.status == "waiting")
        prompt_resolving = sum(1 for p in prompts if p.status == "resolving")
        lines.extend(
            [
                "",
                "**HITL health**",
                f"- Active prompts: `{len(prompts)}`",
            ]
        )
        if prompts:
            lines.append(f"  - waiting: `{prompt_waiting}`")
            lines.append(f"  - resolving: `{prompt_resolving}`")
        lines.extend(
            [
                "",
                "**Scheduler health**",
                f"- Enabled: `{bool(scheduler is not None)}`",
            ]
        )
        if scheduler is not None:
            try:
                automations = scheduler.list_automations()
                lines.append(f"- Loaded automations: `{len(automations)}`")
                lines.append(f"- Active jobs: `{len(scheduler.jobs)}`")
                auto_states = await self._store.list_automation_states()
                recent_failures = [s for s in auto_states if s.last_error]
                if recent_failures:
                    lines.append(f"- Recent failures: `{len(recent_failures)}`")
                    for af in recent_failures[:3]:
                        err_preview = (af.last_error or "")[:80]
                        lines.append(f"  - `{af.name}`: {err_preview}")
                self._append_scheduler_liveness(lines, scheduler)
            except Exception:
                lines.append("- Scheduler summary unavailable.")
        lines.extend(
            [
                "",
                "**Auth health**",
                f"- Active auth waits: `{active_auth_waits}`",
                "",
                "**Log pointers**",
                f"- Service log: `{self.service_log_path}`",
                f"- Thread log root: `{self.thread_logs_root}`",
            ]
        )
        failure_hints = self._recent_failure_hints()
        if failure_hints:
            lines.extend(
                [
                    "",
                    "**Recent failure hints**",
                    f"```text\n{failure_hints}\n```",
                ]
            )
        return "\n".join(lines)[:3800]

    def _append_scheduler_liveness(self, lines: list[str], scheduler) -> None:
        """Append scheduler watchdog liveness details to a /doctor report."""
        try:
            from datetime import datetime, timedelta, timezone as _tz

            now = datetime.now(_tz.utc).astimezone()
            findings = scheduler.evaluate_job_health()
            findings_by_job: dict[str, str] = {
                f.name: f.reason for f in findings if f.scope == "job" and f.name
            }
            reload_findings = [f for f in findings if f.scope == "reload"]

            reload_state = scheduler.get_reload_runtime_state()
            if reload_state is not None:
                lines.append(
                    f"- Reload loop last progress: `{reload_state.last_progress_at.isoformat()}`"
                )
                if reload_findings:
                    lines.append(
                        f"  - ⚠️ reload loop stale: {reload_findings[0].reason}"
                    )
                if reload_state.last_restart_at is not None:
                    since = now - reload_state.last_restart_at.astimezone(now.tzinfo)
                    if since <= timedelta(hours=24):
                        lines.append(
                            f"  - Last restart: {reload_state.last_restart_at.isoformat()}"
                            f" ({reload_state.last_restart_reason or 'unknown'})"
                        )

            all_states = scheduler.list_job_runtime_state()
            stale_states = [s for s in all_states if s.name in findings_by_job]

            if stale_states:
                lines.append(f"- ⚠️ Stale jobs: `{len(stale_states)}`")
                for s in stale_states[:10]:
                    reason = findings_by_job.get(s.name, "?")
                    lines.append(
                        f"  - `{s.name}` ({reason}): phase={s.phase} "
                        f"next_fire={_fmt_dt(s.next_fire_at)} "
                        f"last_progress={_fmt_dt(s.last_progress_at)}"
                    )
            elif all_states:
                preview = all_states[:8]
                lines.append(f"- Active jobs preview: `{len(preview)}/{len(all_states)}`")
                for s in preview:
                    if s.phase == "firing" and s.fire_started_at is not None:
                        lines.append(
                            f"  - `{s.name}`: firing since {_fmt_dt(s.fire_started_at)}"
                        )
                    else:
                        lines.append(
                            f"  - `{s.name}`: sleeping until {_fmt_dt(s.next_fire_at)}"
                        )
                if len(all_states) > len(preview):
                    lines.append(f"  - ... {len(all_states) - len(preview)} more jobs omitted")

            recent_restarts = [
                s for s in all_states
                if s.last_restart_at is not None
                and (now - s.last_restart_at.astimezone(now.tzinfo)) <= timedelta(hours=24)
            ]
            for s in recent_restarts[:5]:
                lines.append(
                    f"  - `{s.name}` restarted at {_fmt_dt(s.last_restart_at)}: "
                    f"{s.last_restart_reason or 'unknown'}"
                )
        except Exception:
            lines.append("- Scheduler liveness unavailable.")

    def _recent_failure_hints(self) -> str:
        if not self.service_log_path.exists():
            return ""
        try:
            text = self.service_log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        lines = [
            line.strip()
            for line in text.splitlines()
            if "[ERROR]" in line or "Traceback" in line or "[WARNING]" in line
        ]
        if not lines:
            return ""
        return "\n".join(lines[-6:])[:1000]

    async def mark_thread_ask_user_required(
        self,
        *,
        platform: str,
        channel_id: str,
        thread_id: str,
        actor_id: str,
        agent_name: str,
        question: str,
        details: str | None,
        choices: tuple[AskUserChoice, ...],
        control_envelope_json: str,
        resume_context: dict[str, Any] | None = None,
        session_id_snapshot: str | None = None,
    ) -> str:
        if not self._owner_user_ids:
            return "Interactive ask_user prompts are disabled because no owner_user_ids are configured."
        if not self._is_authorized(actor_id):
            return "This action is restricted to the configured owner."
        session = self._sessions.get(self._key(platform, channel_id))
        if session is None:
            return f"Thread `{thread_id}` has no live session bound to its channel."

        existing = await self._store.get_active_hitl_prompt_for_thread(
            platform=platform,
            channel_id=channel_id,
            thread_id=thread_id,
        )
        if existing is not None:
            return f"Thread `{thread_id}` is already waiting for input."

        prompt = await self._store.create_hitl_prompt(
            prompt_id=uuid.uuid4().hex[:12],
            target_kind="thread",
            platform=platform,
            channel_id=channel_id,
            thread_id=thread_id,
            task_id=None,
            agent_name=agent_name,
            status="waiting",
            question=question,
            details=details,
            choices_json=[self._choice_to_dict(choice) for choice in choices],
            selected_choice_id=None,
            selected_choice_label=None,
            selected_choice_description=None,
            control_envelope_json=control_envelope_json,
            resume_context_json=resume_context or {},
            session_id_snapshot=session_id_snapshot,
            prompt_message_id=None,
            created_by=actor_id,
        )
        message_id = await self._send_hitl_prompt(session.channel, prompt)
        if message_id is None:
            await self._store.update_hitl_prompt(
                prompt.id,
                status="failed",
                completed_at_now=True,
            )
            return (
                "The agent requested an interactive choice, but this channel does not support "
                "button-based responses yet."
            )
        await self._store.update_hitl_prompt(prompt.id, prompt_message_id=message_id)
        await self._notify_thread_ask_user_required(
            platform=platform,
            channel_id=channel_id,
            thread_id=thread_id,
            question=question,
        )
        return f"Thread `{thread_id}` is waiting for input."

    async def mark_task_ask_user_required(
        self,
        task_id: str,
        *,
        question: str,
        details: str | None,
        choices: tuple[AskUserChoice, ...],
        control_envelope_json: str,
    ) -> str:
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."
        if not self._owner_user_ids:
            return "Interactive ask_user prompts are disabled because no owner_user_ids are configured."
        if not self._is_authorized(task.created_by):
            return f"Task `{task.id}` was not created by an authorized owner."
        session = self._session_for(task)
        if session is None:
            return f"Task `{task.id}` has no live session bound to its channel."

        existing = await self._store.get_active_hitl_prompt_for_task(task.id)
        if existing is not None:
            return f"Task `{task.id}` is already waiting for input."

        prompt = await self._store.create_hitl_prompt(
            prompt_id=uuid.uuid4().hex[:12],
            target_kind="task",
            platform=task.platform,
            channel_id=task.channel_id,
            thread_id=task.thread_id,
            task_id=task.id,
            agent_name=task.preferred_agent or self._default_agent,
            status="waiting",
            question=question,
            details=details,
            choices_json=[self._choice_to_dict(choice) for choice in choices],
            selected_choice_id=None,
            selected_choice_label=None,
            selected_choice_description=None,
            control_envelope_json=control_envelope_json,
            resume_context_json={},
            session_id_snapshot=None,
            prompt_message_id=None,
            created_by=task.created_by,
        )
        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_WAITING_USER_INPUT,
            blocked_reason=self._summarize_hitl_question(question),
            resume_instruction=None,
            ended_at=None,
        )
        await self._store.add_runtime_event(
            task.id,
            "task.ask_user",
            {
                "prompt_id": prompt.id,
                "question": question,
                "choices": [self._choice_to_dict(choice) for choice in choices],
            },
        )
        message_id = await self._send_hitl_prompt(session.channel, prompt)
        if message_id is None:
            await self._store.update_hitl_prompt(
                prompt.id,
                status="failed",
                completed_at_now=True,
            )
            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_BLOCKED,
                blocked_reason="Interactive user choice is not supported on this channel.",
                ended_at=None,
            )
            await self._notify(
                task,
                "The task requested an interactive choice, but this channel does not support button-based responses yet.",
                terminal=bool(task.automation_name),
            )
            return (
                "The task requested an interactive choice, but this channel does not support "
                "button-based responses yet."
            )
        await self._store.update_hitl_prompt(prompt.id, prompt_message_id=message_id)
        await self._notify_task_ask_user_required(task, question=question)
        return f"Task `{task.id}` is waiting for owner input."

    async def answer_hitl_prompt(
        self,
        prompt_id: str,
        *,
        choice_id: str,
        actor_id: str,
    ) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can answer interactive prompts."
        prompt = await self._store.get_hitl_prompt(prompt_id)
        if prompt is None:
            return f"Interactive prompt `{prompt_id}` not found."
        if prompt.status not in {"waiting", "resolving"}:
            return f"Interactive prompt `{prompt.id}` is already `{prompt.status}`."

        choice = self._find_hitl_choice(prompt, choice_id)
        if choice is None:
            return f"Choice `{choice_id}` is not valid for prompt `{prompt.id}`."

        await self._store.update_hitl_prompt(
            prompt.id,
            status="resolving",
            selected_choice_id=choice["id"],
            selected_choice_label=choice["label"],
            selected_choice_description=choice.get("description"),
            resume_context_json={
                **(prompt.resume_context or {}),
                "last_hitl_answer": {
                    "prompt_id": prompt.id,
                    "question": prompt.question,
                    "choice_id": choice["id"],
                    "choice_label": choice["label"],
                    "choice_description": choice.get("description") or "",
                    "answered_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "target_kind": prompt.target_kind,
                },
            },
        )
        prompt = await self._store.get_hitl_prompt(prompt.id) or prompt

        if prompt.target_kind == "task":
            result = await self._answer_task_hitl_prompt(prompt)
        else:
            result = await self._answer_thread_hitl_prompt(prompt)
        return result

    async def cancel_hitl_prompt(self, prompt_id: str, *, actor_id: str) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can cancel interactive prompts."
        prompt = await self._store.get_hitl_prompt(prompt_id)
        if prompt is None:
            return f"Interactive prompt `{prompt_id}` not found."
        if prompt.status not in {"waiting", "resolving"}:
            return f"Interactive prompt `{prompt.id}` is already `{prompt.status}`."

        if prompt.target_kind == "task" and prompt.task_id:
            task = await self._store.get_runtime_task(prompt.task_id)
            if task is not None:
                await self._store.update_runtime_task(
                    task.id,
                    status=TASK_STATUS_BLOCKED,
                    blocked_reason="User cancelled HITL prompt.",
                    resume_instruction=None,
                    ended_at=None,
                )
                await self._store.add_runtime_event(
                    task.id,
                    "task.ask_user_cancelled",
                    {"prompt_id": prompt.id, "actor_id": actor_id},
                )
                await self._send_hitl_cancel_record(prompt)
                await self._signal_status_by_id(task, TASK_STATUS_BLOCKED)
                await self._resolve_notification("ask_user", task_id=task.id, status="cancelled")
        else:
            await self._send_hitl_cancel_record(prompt)
            await self._resolve_notification("ask_user", thread_id=prompt.thread_id, status="cancelled")

        await self._store.update_hitl_prompt(
            prompt.id,
            status="cancelled",
            completed_at_now=True,
        )
        return f"Interactive prompt `{prompt.id}` cancelled."

    async def resume_suspended_agent_run(self, run_id: str) -> str:
        run = await self._store.get_suspended_agent_run(run_id)
        if run is None:
            return f"Suspended run `{run_id}` not found."
        session = self._sessions.get(self._key(run.platform, run.channel_id))
        registry = self._registries.get(self._key(run.platform, run.channel_id))
        if session is None or registry is None:
            return f"Thread `{run.thread_id}` has no live session/registry to resume."

        logger.info(
            "SUSPENDED_RESUME_START run=%s platform=%s channel=%s thread=%s agent=%s provider=%s",
            run.id,
            run.platform,
            run.channel_id,
            run.thread_id,
            run.agent_name,
            run.provider,
        )
        await self._store.update_suspended_agent_run(run.id, status="resuming")
        agent = registry.get_agent(run.agent_name)
        if agent is None:
            await self._store.update_suspended_agent_run(run.id, status="failed", completed_at_now=True)
            return f"Agent `{run.agent_name}` is not available for suspended run `{run.id}`."

        await self._restore_thread_agent_session(
            session=session,
            thread_id=run.thread_id,
            agent=agent,
            fallback_session_id=run.session_id_snapshot,
        )
        current_session_id = agent.get_session_id(run.thread_id) if hasattr(agent, "get_session_id") else None
        logger.info(
            "SUSPENDED_RESUME_SESSION run=%s thread=%s session_id=%s",
            run.id,
            run.thread_id,
            (current_session_id[:12] if isinstance(current_session_id, str) else None),
        )

        resume_context = run.resume_context or {}
        skill_timeout_override = self._skill_timeout_seconds_by_name(
            str(resume_context.get("skill_name") or "") or None
        )
        skill_max_turns_override = self._skill_max_turns_by_name(
            str(resume_context.get("skill_name") or "") or None
        )
        prompt = self._build_suspended_run_resume_prompt(run, include_original_request=True)
        log_path = self.chat_agent_log_base_path(
            thread_id=run.thread_id,
            request_id=run.id,
            purpose="resume",
        )
        response = await self._invoke_thread_agent(
            registry=registry,
            session=session,
            prompt=prompt,
            thread_id=run.thread_id,
            force_agent=run.agent_name,
            log_path=log_path,
            purpose="resume",
            skill_name=str(resume_context.get("skill_name") or "") or None,
            timeout_override_seconds=skill_timeout_override,
            max_turns_override=skill_max_turns_override,
        )
        if response.error and response.error_kind != "max_turns" and getattr(agent, "get_session_id", None):
            logger.warning(
                "SUSPENDED_RESUME_PRIMARY_FAILED run=%s thread=%s agent=%s error=%s",
                run.id,
                run.thread_id,
                run.agent_name,
                response.error[:400],
            )
            self._clear_thread_agent_session(agent, run.thread_id)
            prompt = self._build_suspended_run_resume_prompt(run, include_original_request=True)
            response = await self._invoke_thread_agent(
                registry=registry,
                session=session,
                prompt=prompt,
                thread_id=run.thread_id,
                force_agent=run.agent_name,
                log_path=self.chat_agent_log_base_path(
                    thread_id=run.thread_id,
                    request_id=f"{run.id}-fresh",
                    purpose="resume",
                ),
                purpose="resume_fresh",
                skill_name=str(resume_context.get("skill_name") or "") or None,
                timeout_override_seconds=skill_timeout_override,
                max_turns_override=skill_max_turns_override,
            )

        await self._sync_thread_agent_session(session=session, thread_id=run.thread_id, agent=agent)
        logger.info(
            "SUSPENDED_RESUME_AGENT_DONE run=%s thread=%s agent=%s response_error=%s response_len=%d",
            run.id,
            run.thread_id,
            run.agent_name,
            bool(response.error),
            len(response.text or ""),
        )
        if response.error:
            await self._store.update_suspended_agent_run(run.id, status="failed", completed_at_now=True)
            await session.channel.send(
                run.thread_id,
                self._format_agent_failure_text(
                    response,
                    prefix=f"Login completed, but resuming `{run.agent_name}` failed.",
                ),
            )
            return f"Suspended run `{run.id}` failed to resume."

        auth_challenge = None
        try:
            envelope = parse_control_envelope(response.text) if extract_control_frame(response.text) else None
            if envelope is not None:
                auth_challenge = parse_auth_challenge(envelope)
        except ProtocolError as exc:
            logger.warning("Suspended run=%s control frame parse failed: %s", run.id, exc)
            envelope = None
        if envelope is not None and auth_challenge is not None:
            await self._send_auth_challenge_progress(
                session=session,
                thread_id=run.thread_id,
                agent_name=run.agent_name,
                text=response.text,
                provider=auth_challenge.provider,
                skill_name=str(resume_context.get("skill_name") or "") or None,
                original_user_content=str(resume_context.get("original_user_content") or "") or None,
                usage=response.usage,
            )
            await self._store.update_suspended_agent_run(
                run.id,
                status="waiting_auth",
                control_envelope_json=envelope.raw_json,
                completed_at=None,
            )
            logger.info(
                "SUSPENDED_RESUME_REAUTH_REQUIRED run=%s thread=%s provider=%s",
                run.id,
                run.thread_id,
                auth_challenge.provider,
            )
            return await self.mark_thread_auth_required(
                platform=run.platform,
                channel_id=run.channel_id,
                thread_id=run.thread_id,
                provider=auth_challenge.provider,
                reason=auth_challenge.reason,
                actor_id=run.created_by,
                agent_name=run.agent_name,
                control_envelope_json=envelope.raw_json,
                resume_context=run.resume_context,
                session_id_snapshot=agent.get_session_id(run.thread_id) if hasattr(agent, "get_session_id") else run.session_id_snapshot,
            )

        await session.append_assistant(run.thread_id, response.text, run.agent_name)
        await self._send_thread_agent_response(
            session=session,
            thread_id=run.thread_id,
            agent_name=run.agent_name,
            text=response.text,
            usage=response.usage,
        )
        await self._store.update_suspended_agent_run(run.id, status="completed", completed_at_now=True)
        logger.info(
            "SUSPENDED_RESUME_COMPLETED run=%s thread=%s agent=%s",
            run.id,
            run.thread_id,
            run.agent_name,
        )
        return f"Suspended run `{run.id}` resumed successfully."

    async def merge_task(self, task_id: str, *, actor_id: str) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can merge tasks."
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."
        if not self._uses_merge_flow(task):
            return f"Task `{task.id}` does not use merge completion."
        return await self._execute_merge(task, actor_id=actor_id, source="slash")

    async def wait_task(self, task_id: str, *, actor_id: str) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can keep merge tasks waiting."
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."
        if not self._uses_merge_flow(task):
            return f"Task `{task.id}` does not use merge completion."
        if task.status not in {TASK_STATUS_WAITING_MERGE, TASK_STATUS_APPLIED, TASK_STATUS_MERGE_FAILED}:
            return f"Task `{task.id}` is not waiting merge (status: {task.status})."
        updates: dict[str, Any] = {}
        if task.status == TASK_STATUS_MERGE_FAILED:
            updates["status"] = TASK_STATUS_WAITING_MERGE
        if updates:
            await self._store.update_runtime_task(task.id, **updates)
        await self._store.add_runtime_event(task.id, "task.merge_wait", {"actor_id": actor_id})
        await self._notify(
            task,
            (
                f"Task `{task.id}` remains pending merge. "
                "Wait until the main repository is ready, then reply `retry merge`."
            ),
        )
        await self._signal_status_by_id(task, TASK_STATUS_WAITING_MERGE)
        return f"Task `{task.id}` kept in WAITING_MERGE."

    async def discard_task(self, task_id: str, *, actor_id: str) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can discard tasks."
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."
        if not self._uses_merge_flow(task):
            return f"Task `{task.id}` does not use merge completion."
        if task.status not in {TASK_STATUS_WAITING_MERGE, TASK_STATUS_APPLIED, TASK_STATUS_MERGE_FAILED}:
            return f"Task `{task.id}` is not waiting merge (status: {task.status})."
        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_DISCARDED,
            summary="Discarded by user.",
            ended_at_now=True,
        )
        await self._store.add_runtime_event(task.id, "task.discarded", {"actor_id": actor_id})
        await self._notify(task, f"Task `{task.id}` discarded.")
        await self._signal_status_by_id(task, TASK_STATUS_DISCARDED)
        return f"Task `{task.id}` discarded."

    async def consume_decision_nonce(
        self,
        *,
        task_id: str,
        nonce: str,
        action: str,
        actor_id: str,
        source: str,
    ) -> bool:
        """Validate + mark a decision nonce consumed. Public wrapper for the store call."""
        return await self._store.consume_runtime_decision_nonce(
            task_id=task_id,
            nonce=nonce,
            action=action,
            actor_id=actor_id,
            source=source,
            result="accepted",
        )

    async def replace_draft_task(self, task_id: str, *, actor_id: str) -> tuple[str, str | None]:
        """Discard a blocking DRAFT scheduler task so a fresh cron tick can run.

        Returns ``(message, automation_name)``; ``automation_name`` is ``None``
        when the task wasn't in a replaceable state, so the caller knows not
        to re-fire.
        """
        if not self._is_authorized(actor_id):
            return "Only configured owners can replace tasks.", None
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found.", None
        if task.status != TASK_STATUS_DRAFT:
            return f"Task `{task.id}` is not a DRAFT (status: {task.status}).", None
        if not task.automation_name:
            return f"Task `{task.id}` has no automation_name; cannot refire.", None
        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_DISCARDED,
            summary="Replaced by user (refired automation).",
            ended_at_now=True,
        )
        await self._store.add_runtime_event(
            task.id,
            "task.replaced",
            {"actor_id": actor_id, "automation_name": task.automation_name},
        )
        await self._signal_status_by_id(task, TASK_STATUS_DISCARDED)
        return (
            f"Task `{task.id}` discarded; refiring automation `{task.automation_name}`.",
            task.automation_name,
        )

    async def get_task_changes(self, task_id: str) -> str:
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."

        changes = await self._collect_task_changes(task, limit=200)

        if not changes:
            return f"Task `{task.id}` has no detectable file changes."

        lines = [f"Task `{task.id}` changes ({len(changes)}):"]
        lines.extend(f"- `{line}`" for line in changes[:80])
        if len(changes) > 80:
            lines.append(f"- ... and {len(changes) - 80} more")
        return "\n".join(lines)[:1900]

    async def get_task_logs(self, task_id: str) -> str:
        task = await self._store.get_runtime_task(task_id)
        if task is None:
            return f"Task `{task_id}` not found."

        lines = [
            f"**Task Logs** `{task.id}`",
            f"- Status: `{task.status}`",
            f"- Step: {task.step_no}/{task.max_steps}",
        ]
        if task.summary:
            lines.append(f"- Summary: {task.summary[:240]}")
        if task.output_summary:
            lines.append(f"- Output: {task.output_summary[:240]}")
        if task.error:
            lines.append(f"- Error: {task.error[:240]}")
        if task.artifact_manifest:
            lines.append(f"- Artifacts: {', '.join(task.artifact_manifest[:8])[:240]}")
        thread_log_path = self._thread_log_path(task.thread_id)
        if thread_log_path.exists():
            lines.append(f"- Thread log: `{thread_log_path}`")
        live_log_path = self._live_agent_logs.get(task.id)
        if task.status in _TASK_LIVE_STATUSES and live_log_path and live_log_path.exists():
            lines.append(f"- Live agent log: `{live_log_path}`")

        events = await self._store.list_runtime_events(task.id, limit=self._log_event_limit)
        if events:
            lines.append("")
            lines.append("**Recent events**")
            for event in events[-8:]:
                payload = event.get("payload", {})
                summary = self._summarize_event_payload(payload)
                lines.append(
                    f"- `{event['event_type']}`"
                    + (f": {summary}" if summary else "")
                )

        hitl_prompt = await self._store.get_active_hitl_prompt_for_task(task.id)
        if hitl_prompt is None:
            answered = await self._last_hitl_answer_payload_for_task(task.id)
            if answered:
                lines.append("")
                lines.append("**Last HITL checkpoint**")
                lines.append(f"- Question: {answered.get('question', '')[:200]}")
                lines.append(
                    f"- Answer: **{answered.get('choice_label', '')}** (`{answered.get('choice_id', '')}`)"
                )
        else:
            lines.append("")
            lines.append("**Active HITL prompt**")
            lines.append(f"- Status: `{hitl_prompt.status}`")
            lines.append(f"- Question: {hitl_prompt.question[:200]}")
            if hitl_prompt.selected_choice_id:
                lines.append(
                    f"- Answer: **{hitl_prompt.selected_choice_label or ''}** (`{hitl_prompt.selected_choice_id}`)"
                )

        ckpt = await self._store.get_last_runtime_checkpoint(task.id)
        live_agent_tail = None
        if task.status in _TASK_LIVE_STATUSES and live_log_path and live_log_path.exists():
            try:
                live_agent_tail = self._tail_text(live_log_path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                live_agent_tail = None
        if live_agent_tail:
            lines.append("")
            lines.append("**Live agent log tail**")
            lines.append(f"```text\n{live_agent_tail}\n```")
        else:
            thread_excerpt = self._extract_thread_log_excerpt(thread_id=task.thread_id, task_id=task.id)
            if thread_excerpt:
                lines.append("")
                lines.append("**Thread log excerpt**")
                lines.append(f"```text\n{thread_excerpt}\n```")
        if ckpt:
            agent_tail = self._tail_text(str(ckpt.get("agent_result", "")))
            test_tail = self._format_test_output(str(ckpt.get("test_result", "")))
            if agent_tail:
                lines.append("")
                lines.append("**Last agent output tail**")
                lines.append(f"```text\n{agent_tail}\n```")
            if test_tail:
                lines.append("")
                lines.append("**Last test result**")
                lines.append(f"```text\n{test_tail}\n```")
        return "\n".join(lines)[:3800]

    async def cleanup_tasks(self, *, actor_id: str, task_id: str | None = None) -> str:
        if not self._is_authorized(actor_id):
            return "Only configured owners can clean runtime tasks."

        if task_id:
            task = await self._store.get_runtime_task(task_id)
            if task is None:
                return f"Task `{task_id}` not found."
            if task.status not in _TERMINAL_CLEANUP_STATUSES:
                return f"Task `{task.id}` is not in cleanable terminal state (status: {task.status})."
            cleaned = await self._cleanup_single_task(task)
            if cleaned:
                return f"Task `{task.id}` workspace cleaned."
            return f"Task `{task.id}` had no workspace to clean."

        cleaned = await self._cleanup_expired_tasks()
        return f"Cleanup completed. {cleaned} task workspace(s) removed."

    async def handle_decision_event(self, event: TaskDecisionEvent) -> str:
        if not self._is_authorized(event.actor_id):
            return "Only configured owners can perform task decisions."

        task = await self._store.get_runtime_task(event.task_id)
        if task is None:
            return f"Task `{event.task_id}` not found."

        if event.action in {"approve", "reject", "suggest"}:
            valid = {TASK_STATUS_DRAFT, TASK_STATUS_BLOCKED}
            if task.status not in valid:
                return f"Task `{task.id}` is not waiting approval (status: {task.status})."
        elif event.action in {"merge", "discard", "request_changes"}:
            if not self._uses_merge_flow(task):
                return f"Task `{task.id}` does not use merge completion."
            valid = {TASK_STATUS_WAITING_MERGE, TASK_STATUS_APPLIED, TASK_STATUS_MERGE_FAILED}
            if task.status not in valid:
                return f"Task `{task.id}` is not waiting merge (status: {task.status})."
        else:
            return f"Unsupported decision action: {event.action}"

        if not await self._store.consume_runtime_decision_nonce(
            task_id=task.id,
            nonce=event.nonce,
            action=event.action,
            actor_id=event.actor_id,
            source=event.source,
            result="accepted",
        ):
            return "Decision token is invalid or expired."

        if event.action == "approve":
            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_PENDING,
                blocked_reason=None,
            )
            await self._store.add_runtime_event(
                task.id,
                "task.approved",
                {"actor_id": event.actor_id, "source": event.source},
            )
            await self._notify(task, f"Task `{task.id}` approved and queued.")
            await self._signal_status_by_id(task, TASK_STATUS_PENDING)
            await self._resolve_notification("task_draft", task_id=task.id)
            return f"Task `{task.id}` approved."

        if event.action == "reject":
            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_REJECTED,
                ended_at_now=True,
                summary="Rejected by user.",
            )
            await self._store.add_runtime_event(
                task.id,
                "task.rejected",
                {"actor_id": event.actor_id, "source": event.source},
            )
            await self._notify(task, f"Task `{task.id}` rejected.")
            await self._signal_status_by_id(task, TASK_STATUS_REJECTED)
            await self._resolve_notification("task_draft", task_id=task.id)
            return f"Task `{task.id}` rejected."

        if event.action == "suggest":
            suggestion = (event.suggestion or "").strip()
            new_nonce = await self._store.create_runtime_decision_nonce(
                task.id,
                ttl_minutes=self._decision_ttl_minutes,
            )
            await self._store.update_runtime_task(
                task.id,
                resume_instruction=suggestion or task.resume_instruction,
            )
            await self._store.add_runtime_event(
                task.id,
                "task.suggested",
                {"actor_id": event.actor_id, "source": event.source, "suggestion": suggestion},
            )
            session = self._session_for(task)
            if session is not None:
                suggestion_preview = suggestion or task.resume_instruction or "(none)"
                suggest_text = (
                    f"### Runtime Task `{task.id}` — Suggestion Recorded\n"
                    f"> {suggestion_preview}\n\n"
                    "Approve to run with this guidance, or reject to discard."
                )
                await self._send_decision_surface(
                    session,
                    event.thread_id,
                    suggest_text,
                    task.id,
                    new_nonce,
                    ["approve", "reject"],
                )
            return f"Task `{task.id}` suggestion recorded."

        if event.action == "merge":
            return await self._execute_merge(task, actor_id=event.actor_id, source=event.source)

        if event.action == "discard":
            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_DISCARDED,
                summary="Discarded by user.",
                ended_at_now=True,
            )
            await self._store.add_runtime_event(
                task.id,
                "task.discarded",
                {"actor_id": event.actor_id, "source": event.source},
            )
            await self._notify(task, f"Task `{task.id}` discarded.")
            await self._signal_status_by_id(task, TASK_STATUS_DISCARDED)
            await self._resolve_notification("task_waiting_merge", task_id=task.id)
            return f"Task `{task.id}` discarded."

        # request_changes: move back to BLOCKED and keep suggestion as resume hint.
        suggestion = (event.suggestion or "").strip()
        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_BLOCKED,
            blocked_reason="Requested changes before merge.",
            resume_instruction=suggestion or task.resume_instruction,
            ended_at=None,
        )
        await self._store.add_runtime_event(
            task.id,
            "task.request_changes",
            {"actor_id": event.actor_id, "source": event.source, "suggestion": suggestion},
        )
        await self._notify(
            task,
            (
                f"Task `{task.id}` marked as BLOCKED for additional changes. "
                f"Use `/task_resume {task.id} <instruction>` to continue."
            ),
        )
        await self._signal_status_by_id(task, TASK_STATUS_BLOCKED)
        await self._resolve_notification("task_waiting_merge", task_id=task.id)
        return f"Task `{task.id}` moved to BLOCKED."

    async def build_slash_decision_event(
        self,
        *,
        platform: str,
        channel_id: str,
        thread_id: str,
        task_id: str,
        action: str,
        actor_id: str,
        suggestion: str | None = None,
    ) -> TaskDecisionEvent | None:
        nonce = await self._store.get_active_runtime_decision_nonce(task_id)
        if not nonce:
            nonce = await self._store.create_runtime_decision_nonce(
                task_id,
                ttl_minutes=self._decision_ttl_minutes,
            )
        return TaskDecisionEvent(
            platform=platform,
            channel_id=channel_id,
            thread_id=thread_id,
            task_id=task_id,
            action=action,  # type: ignore[arg-type]
            actor_id=actor_id,
            nonce=nonce,
            source="slash",
            suggestion=suggestion,
        )

    async def _worker_loop(self, idx: int) -> None:
        while not self._stop_event.is_set():
            try:
                task = await self._store.claim_pending_runtime_task()
                if task is None:
                    await asyncio.sleep(0.8)
                    continue
                logger.info("Runtime worker=%d claimed task=%s", idx, task.id)
                await self._run_task(task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Runtime worker %s crashed: %s", idx, exc)
                await asyncio.sleep(1.5)

    async def _janitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                cleaned = await self._cleanup_expired_tasks()
                cleaned_logs = await self._cleanup_expired_agent_logs()
                if cleaned or cleaned_logs:
                    logger.info(
                        "Runtime janitor cleaned %d expired task workspace(s) and %d expired agent log(s)",
                        cleaned,
                        cleaned_logs,
                    )
                await asyncio.sleep(max(1, self._cleanup_interval_minutes) * 60)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Runtime janitor failed: %s", exc)
                # Avoid tight error loops when storage is temporarily unavailable during shutdown.
                await asyncio.sleep(1.5)

    async def _run_task(self, task: RuntimeTask) -> None:
        session = self._session_for(task)
        registry = self._registry_for(task)
        if session is None or registry is None:
            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_BLOCKED,
                blocked_reason="No active session/registry for platform+channel.",
            )
            return

        try:
            workspace = await self._prepare_task_workspace(task)
        except WorktreeError as exc:
            await self._fail(task, f"Failed to prepare workspace: {exc}")
            return

        await self._store.update_runtime_task(task.id, workspace_path=str(workspace))
        logger.info(
            "Runtime task=%s start workspace=%s goal=%r",
            task.id,
            workspace,
            task.goal[:140],
        )
        await self._store.add_runtime_event(
            task.id,
            "task.started",
            {"workspace": str(workspace), "goal": task.goal[:200]},
        )
        await self._notify(task, f"Task `{task.id}` started. Workspace is ready; entering autonomous loop.")
        await self._signal_status_by_id(task, TASK_STATUS_RUNNING)

        start = time.monotonic()
        step = task.step_no
        prior_failure: str | None = None
        total_agent_s = 0.0
        total_test_s = 0.0
        last_agent_name: str = task.preferred_agent or self._default_agent or ""
        latest = await self._store.get_last_runtime_checkpoint(task.id)
        if latest:
            prior_failure = latest.get("test_result")

        while step < task.max_steps:
            current = await self._store.get_runtime_task(task.id)
            if current is None:
                return
            if current.status in {TASK_STATUS_STOPPED, TASK_STATUS_PAUSED}:
                return
            if (time.monotonic() - start) > (task.max_minutes * 60):
                await self._store.update_runtime_task(
                    task.id,
                    status=TASK_STATUS_TIMEOUT,
                    ended_at_now=True,
                    summary="Task exceeded runtime budget.",
                )
                await self._notify(task, f"Task `{task.id}` timed out.")
                await self._signal_status_by_id(task, TASK_STATUS_TIMEOUT)
                return

            step += 1
            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_RUNNING,
                step_no=step,
            )
            logger.info(
                "Runtime task=%s step=%d/%d status=RUNNING",
                task.id,
                step,
                task.max_steps,
            )
            await self._store.add_runtime_event(
                task.id,
                "task.phase",
                {"step": step, "phase": "agent_running"},
            )
            await self._notify(
                task,
                f"Task `{task.id}` step {step}/{task.max_steps}: running agent `{task.preferred_agent or self._default_agent}`.",
            )
            last_hitl_answer = await self._last_hitl_answer_payload_for_task(task.id)
            if task.task_type == TASK_TYPE_SKILL_CHANGE and task.skill_name:
                prompt = build_skill_prompt(
                    skill_name=task.skill_name,
                    goal=task.goal,
                    original_request=current.original_request,
                    step_no=step,
                    max_steps=task.max_steps,
                    prior_failure=prior_failure,
                    resume_instruction=current.resume_instruction,
                    last_hitl_answer=last_hitl_answer,
                )
                if self._has_external_source_signals(current.original_request or task.goal):
                    prompt += (
                        "\n\nExternal-source adaptation requirements:\n"
                        "- If this skill adapts a repo/tool/reference from the request, set SKILL.md frontmatter metadata:\n"
                        "  metadata.source_urls: [<urls>]\n"
                        "  metadata.adapted_from: <repo or tool name>\n"
                        "  metadata.adaptation_notes: <what was internalized or intentionally omitted>\n"
                        "- Do not claim adaptation without concrete source-grounded changes."
                    )
            else:
                prompt = build_runtime_prompt(
                    goal=task.goal,
                    original_request=current.original_request,
                    step_no=step,
                    max_steps=task.max_steps,
                    prior_failure=prior_failure,
                    resume_instruction=current.resume_instruction,
                    last_hitl_answer=last_hitl_answer,
                )

            t_agent = time.perf_counter()
            agent_name, response = await self._run_agent(
                registry=registry,
                task=task,
                prompt=prompt,
                workspace=workspace,
                step=step,
            )
            last_agent_name = agent_name
            elapsed_agent = time.perf_counter() - t_agent
            total_agent_s += elapsed_agent
            if response.error:
                # If the task was stopped or paused externally, don't overwrite its status.
                current_after = await self._store.get_runtime_task(task.id)
                if current_after and current_after.status in {TASK_STATUS_STOPPED, TASK_STATUS_PAUSED}:
                    return
                await self._fail(task, f"{agent_name}: {response.error}", response=response)
                return

            envelope = None
            auth_challenge = None
            ask_user_challenge = None
            if extract_control_frame(response.text) is not None:
                try:
                    envelope = parse_control_envelope(response.text)
                    auth_challenge = parse_auth_challenge(envelope)
                    ask_user_challenge = parse_ask_user_challenge(envelope)
                except ProtocolError as exc:
                    logger.warning(
                        "Runtime task=%s step=%d control frame parse failed: %s",
                        task.id,
                        step,
                        exc,
                    )
            if auth_challenge is not None:
                await self._send_auth_challenge_progress(
                    session=self._session_for(task),
                    thread_id=task.thread_id,
                    agent_name=agent_name,
                    text=response.text,
                    provider=auth_challenge.provider,
                    skill_name=task.skill_name,
                    original_user_content=task.original_request,
                    usage=response.usage,
                )
                await self._store.add_runtime_event(
                    task.id,
                    "task.auth_challenge",
                    {
                        "provider": auth_challenge.provider,
                        "reason": auth_challenge.reason,
                        "control_envelope": envelope.raw_json if envelope else None,
                    },
                )
                await self.mark_task_auth_required(
                    task.id,
                    provider=auth_challenge.provider,
                    reason=auth_challenge.reason,
                )
                return
            if ask_user_challenge is not None:
                visible_text = strip_control_frame_text(response.text)
                if visible_text:
                    await self._send_runtime_ask_user_progress(
                        task=task,
                        agent_name=agent_name,
                        text=visible_text,
                        usage=response.usage,
                    )
                await self._store.add_runtime_event(
                    task.id,
                    "task.ask_user_challenge",
                    {
                        "question": ask_user_challenge.question,
                        "choice_ids": [choice.id for choice in ask_user_challenge.choices],
                        "control_envelope": envelope.raw_json if envelope else None,
                    },
                )
                await self.mark_task_ask_user_required(
                    task.id,
                    question=ask_user_challenge.question,
                    details=ask_user_challenge.details,
                    choices=ask_user_challenge.choices,
                    control_envelope_json=envelope.raw_json if envelope else "",
                )
                return

            state, block_reason = parse_task_state(response.text)
            logger.info(
                "Runtime task=%s step=%d AGENT_OK agent=%s elapsed=%.2fs response_len=%d state=%s",
                task.id,
                step,
                agent_name,
                elapsed_agent,
                len(response.text),
                state,
            )
            changed_files = await self._collect_changed_files(task, workspace)
            if self._uses_merge_flow(task):
                guard_error = self._validate_changed_paths(changed_files)
                if guard_error:
                    await self._fail(task, guard_error)
                    return

            skip_validation = bool(task.automation_name and task.test_command.strip() == "true")
            if skip_validation:
                rc = 0
                test_ok = True
                test_summary = ""
                test_display = ""
                test_timed_out = False
                await self._store.add_runtime_event(
                    task.id,
                    "task.phase",
                    {"step": step, "phase": "test_skipped", "command": task.test_command},
                )
            else:
                await self._store.update_runtime_task(task.id, status=TASK_STATUS_VALIDATING)
                logger.info(
                    "Runtime task=%s step=%d status=VALIDATING test=%r changed=%d",
                    task.id,
                    step,
                    task.test_command,
                    len(changed_files),
                )
                await self._store.add_runtime_event(
                    task.id,
                    "task.phase",
                    {"step": step, "phase": "test_running", "command": task.test_command},
                )
                await self._notify(
                    task,
                    f"Task `{task.id}` step {step}: agent finished. Running tests: `{task.test_command}`",
                )
                await self._signal_status_by_id(task, TASK_STATUS_VALIDATING)
                test_notice_state = {"last_notice": 0.0, "last_persist": 0.0}

                async def _on_test_heartbeat(elapsed: float) -> None:
                    logger.info(
                        "Runtime task=%s step=%d TEST_RUNNING elapsed=%.2fs command=%r",
                        task.id,
                        step,
                        elapsed,
                        task.test_command,
                    )
                    if elapsed - test_notice_state["last_persist"] >= self._progress_persist_seconds:
                        test_notice_state["last_persist"] = elapsed
                        await self._store.add_runtime_event(
                            task.id,
                            "task.test_progress",
                            {"step": step, "elapsed_seconds": round(elapsed, 2), "command": task.test_command},
                        )
                    if elapsed - test_notice_state["last_notice"] >= self._progress_notice_seconds:
                        test_notice_state["last_notice"] = elapsed
                        await self._notify(
                            task,
                            f"Task `{task.id}` step {step}: tests still running ({int(elapsed)}s elapsed).",
                        )

                t_test = time.perf_counter()
                rc, out, err, test_timed_out = await self._worktree.run_shell(
                    workspace,
                    task.test_command,
                    timeout_seconds=self._test_timeout_seconds,
                    heartbeat_seconds=self._test_heartbeat_seconds,
                    on_heartbeat=_on_test_heartbeat,
                )
                total_test_s += time.perf_counter() - t_test
                test_ok = rc == 0
                test_summary = (out + ("\n" + err if err else "")).strip()
                if not test_summary:
                    test_summary = f"exit={rc}"
                test_display = self._format_test_output(test_summary)
                logger.info(
                    "Runtime task=%s step=%d TEST_DONE rc=%d",
                    task.id,
                    step,
                    rc,
                )
                if test_timed_out:
                    timeout_msg = (
                        f"Test command exceeded timeout ({int(self._test_timeout_seconds)}s). "
                        f"Recent output:\n{test_display}"
                    )
                    await self._store.add_runtime_event(
                        task.id,
                        "task.test_timeout",
                        {"step": step, "timeout_seconds": self._test_timeout_seconds},
                    )
                    await self._store.update_runtime_task(
                        task.id,
                        status=TASK_STATUS_TIMEOUT,
                        ended_at_now=True,
                        summary="Test command timed out.",
                        error=timeout_msg[:2000],
                    )
                    await self._notify(
                        task,
                        f"Task `{task.id}` timed out during tests.\n```text\n{test_display}\n```",
                        record_history=True,
                        terminal=True,
                    )
                    await self._signal_status_by_id(task, TASK_STATUS_TIMEOUT)
                    return

            await self._store.add_runtime_checkpoint(
                task_id=task.id,
                step_no=step,
                status=TASK_STATUS_VALIDATING,
                prompt_digest=prompt[:500],
                agent_result=response.text[:4000],
                test_result=test_summary[:2000],
                files_changed=changed_files,
            )
            await self._store.add_runtime_event(
                task.id,
                "task.step",
                {
                    "step": step,
                    "agent": agent_name,
                    "test_exit_code": rc,
                    "changed_files": changed_files,
                    "test_output_tail": test_display,
                },
            )

            if (
                test_ok
                and state == "BLOCKED"
                and self._should_ignore_agent_block(response.text, block_reason)
            ):
                override_state = "DONE" if changed_files else "CONTINUE"
                await self._store.add_runtime_event(
                    task.id,
                    "task.block_override",
                    {
                        "step": step,
                        "from_state": "BLOCKED",
                        "to_state": override_state,
                        "reason": "runtime_test_authoritative",
                    },
                )
                logger.info(
                    "Runtime task=%s step=%d overriding agent BLOCKED -> %s because runtime tests passed",
                    task.id,
                    step,
                    override_state,
                )
                state = override_state
                block_reason = None

            if state == "BLOCKED":
                await self._store.update_runtime_task(
                    task.id,
                    status=TASK_STATUS_BLOCKED,
                    blocked_reason=block_reason or "Agent reported blocked.",
                )
                logger.info(
                    "Runtime task=%s BLOCKED reason=%r",
                    task.id,
                    block_reason or "unknown reason",
                )
                await self._notify(
                    task,
                    (
                        f"Task `{task.id}` blocked: {block_reason or 'unknown reason'}\n"
                        f"Provide missing context and resume with `/task_resume {task.id} <instruction>`."
                    ),
                    record_history=True,
                    terminal=True,
                )
                await self._signal_status_by_id(task, TASK_STATUS_BLOCKED)
                return

            if test_ok and state == "DONE":
                evaluation_findings: list[dict[str, Any]] = []
                if task.task_type == TASK_TYPE_SKILL_CHANGE:
                    evaluation_findings = await self._evaluate_skill_task(
                        task,
                        registry=registry,
                        workspace=workspace,
                    )
                total_elapsed_s = time.monotonic() - start
                summary = self._build_completion_summary(
                    task=task,
                    step=step,
                    changed_files=changed_files,
                    test_summary=test_summary,
                    total_agent_s=total_agent_s,
                    total_test_s=total_test_s,
                    total_elapsed_s=total_elapsed_s,
                    waiting_merge=self._uses_merge_flow(task) and self._merge_gate_enabled,
                )
                finding_lines = self._format_evaluation_findings(evaluation_findings)
                if finding_lines:
                    summary += "\nReview findings:\n" + "\n".join(finding_lines)
                output_summary = (
                    strip_control_frame_text(response.text).strip()
                    if task.automation_name
                    else self._build_output_summary(
                        task=task,
                        changed_files=changed_files,
                        test_summary=test_summary,
                    )
                )
                artifact_manifest = changed_files if task.completion_mode != TASK_COMPLETION_MERGE else None
                if self._uses_merge_flow(task) and self._merge_gate_enabled:
                    new_state = TASK_STATUS_WAITING_MERGE
                else:
                    new_state = TASK_STATUS_COMPLETED

                await self._store.update_runtime_task(
                    task.id,
                    status=new_state,
                    ended_at_now=True,
                    summary=summary,
                    output_summary=output_summary,
                    artifact_manifest=artifact_manifest,
                    blocked_reason=None,
                    merge_error=None,
                )
                await self._store.add_runtime_event(
                    task.id,
                    "task.completed",
                    {
                        "status": new_state,
                        "step": step,
                        "total_agent_s": round(total_agent_s, 2),
                        "total_test_s": round(total_test_s, 2),
                        "total_elapsed_s": round(total_elapsed_s, 2),
                    },
                )

                if self._uses_merge_flow(task) and self._merge_gate_enabled:
                    auto_merge_allowed = not any(
                        item.get("status") == "review_required"
                        and (
                            item.get("evaluation_type") != "source_grounded"
                            or self._skill_source_grounded_block_auto_merge
                        )
                        for item in evaluation_findings
                    )
                    # Auto-merge skill tasks when skill_auto_approve is enabled
                    if self._skill_auto_approve and task.task_type == TASK_TYPE_SKILL_CHANGE and auto_merge_allowed:
                        try:
                            refreshed = await self._store.get_runtime_task(task.id) or task
                            result = await self._execute_merge(
                                refreshed, actor_id="system", source="skill_auto_merge",
                            )
                            logger.info(
                                "Runtime task=%s skill auto-merge result: %s", task.id, result,
                            )
                            return
                        except Exception:
                            logger.warning(
                                "Runtime task=%s skill auto-merge failed, falling back to manual",
                                task.id,
                                exc_info=True,
                            )
                            # Fall through to normal merge decision surface

                    merge_nonce = await self._store.create_runtime_decision_nonce(
                        task.id,
                        ttl_minutes=self._decision_ttl_minutes,
                    )
                    refreshed = await self._store.get_runtime_task(task.id)
                    merge_task = refreshed or task
                    text = await self._merge_gate_text(merge_task)
                    msg_id = await self._send_decision_surface(
                        session,
                        task.thread_id,
                        text,
                        task.id,
                        merge_nonce,
                        ["merge", "discard", "request_changes"],
                    )
                    if msg_id:
                        await self._store.update_runtime_task(task.id, decision_message_id=msg_id)
                    waiting_note = f"Task `{task.id}` completed in workspace and is waiting merge decision."
                    if finding_lines:
                        waiting_note += "\nReview findings:\n" + "\n".join(finding_lines)
                    await self._notify(
                        task,
                        waiting_note,
                        record_history=True,
                        terminal=True,
                    )
                    await self._signal_status_by_id(task, TASK_STATUS_WAITING_MERGE)
                    await self._notify_task_waiting_merge_required(merge_task)
                    logger.info("Runtime task=%s WAITING_MERGE step=%d", task.id, step)
                    return

                logger.info("Runtime task=%s COMPLETED step=%d", task.id, step)
                completed_task = await self._store.get_runtime_task(task.id) or task
                delivery = None
                if completed_task.task_type == TASK_TYPE_ARTIFACT:
                    delivery = await self._deliver_artifacts(
                        task=completed_task,
                        changed_files=changed_files,
                    )
                    if delivery is not None:
                        await self._store.add_runtime_event(
                            task.id,
                            "task.artifacts_delivered",
                            {
                                "mode": delivery.mode,
                                "paths": delivery.delivered_paths[:8],
                                "attachments": delivery.attachment_names[:8],
                            },
                        )
                if task.automation_name:
                    await self._store.upsert_automation_state(
                        task.automation_name,
                        platform=task.platform,
                        channel_id=task.channel_id,
                        last_success_at="__NOW__",
                        last_error=None,
                    )
                    completion_text = await self._automation_completed_text(
                        task=completed_task,
                        changed_files=changed_files,
                        delivery=delivery,
                    )
                else:
                    completion_text = self._completed_text(
                        task=completed_task,
                        changed_files=changed_files,
                        test_summary=test_summary,
                        delivery=delivery,
                    )
                await self._notify(
                    task,
                    completion_text,
                    record_history=True,
                    terminal=True,
                    usage=response.usage,
                )
                await self._signal_status_by_id(task, TASK_STATUS_COMPLETED)
                return

            prior_failure = test_summary if not test_ok else None

        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_TIMEOUT,
            ended_at_now=True,
            summary="Task exceeded step budget.",
        )
        logger.info("Runtime task=%s TIMEOUT max_steps=%d", task.id, task.max_steps)
        if task.automation_name:
            await self._store.upsert_automation_state(
                task.automation_name,
                platform=task.platform,
                channel_id=task.channel_id,
                last_error=f"task {task.id} timed out (max_steps={task.max_steps})",
            )
        await self._notify(task, f"Task `{task.id}` reached max steps and stopped.")
        await self._signal_status_by_id(task, TASK_STATUS_TIMEOUT)

    async def _run_agent(
        self,
        *,
        registry: AgentRegistry,
        task: RuntimeTask,
        prompt: str,
        workspace: Path,
        step: int,
    ) -> tuple[str, AgentResponse]:
        if task.preferred_agent:
            forced = registry.get_agent(task.preferred_agent)
            if forced is not None:
                response = await self._invoke_agent(forced, prompt, workspace, task.id, task, step)
                return forced.name, response

        last_name = registry.agents[-1].name
        last_response = AgentResponse(text="", error="No agents available.")
        for agent in registry.agents:
            response = await self._invoke_agent(agent, prompt, workspace, task.id, task, step)
            if not response.error:
                return agent.name, response
            if response.error_kind == "max_turns":
                return agent.name, response
            last_name = agent.name
            last_response = response
        return last_name, last_response

    async def _invoke_agent(
        self,
        agent,
        prompt: str,
        workspace: Path,
        runtime_thread_id: str,
        task: RuntimeTask,
        step: int,
    ) -> AgentResponse:
        sig = inspect.signature(agent.run)
        kwargs = {}
        if "thread_id" in sig.parameters:
            kwargs["thread_id"] = runtime_thread_id
        if "workspace_override" in sig.parameters:
            kwargs["workspace_override"] = workspace
        log_path = self._agent_log_path(task, step, agent.name)
        if "log_path" in sig.parameters:
            kwargs["log_path"] = log_path
        async def _run_with_overrides() -> AgentResponse:
            with AgentRegistry._temporary_timeout(agent, task.agent_timeout_seconds):
                with AgentRegistry._temporary_max_turns(agent, task.agent_max_turns):
                    return await agent.run(prompt, [], **kwargs)

        run_task = asyncio.create_task(_run_with_overrides())
        self._running_tasks[task.id] = run_task
        self._live_agent_logs[task.id] = log_path
        started = asyncio.get_running_loop().time()
        last_notice = 0.0
        last_persist = 0.0
        result: AgentResponse | None = None
        try:
            while True:
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(run_task),
                        timeout=self._agent_heartbeat_seconds,
                    )
                    return result
                except asyncio.TimeoutError:
                    elapsed = asyncio.get_running_loop().time() - started
                    # Check if user stopped or paused mid-run
                    current = await self._store.get_runtime_task(task.id)
                    if current and current.status in {TASK_STATUS_STOPPED, TASK_STATUS_PAUSED}:
                        run_task.cancel()
                        reason = "paused" if current.status == TASK_STATUS_PAUSED else "stopped"
                        result = AgentResponse(text="", error=f"Task {reason} by user.")
                        return result
                    logger.info(
                        "Runtime task=%s step=%d AGENT_RUNNING agent=%s elapsed=%.2fs",
                        task.id,
                        step,
                        agent.name,
                        elapsed,
                    )
                    if elapsed - last_persist >= self._progress_persist_seconds:
                        last_persist = elapsed
                        await self._store.add_runtime_event(
                            task.id,
                            "task.agent_progress",
                            {"step": step, "agent": agent.name, "elapsed_seconds": round(elapsed, 2)},
                        )
                    if elapsed - last_notice >= self._progress_notice_seconds:
                        last_notice = elapsed
                        await self._notify(
                            task,
                            f"Task `{task.id}` step {step}: agent `{agent.name}` still running ({int(elapsed)}s elapsed).",
                        )
        finally:
            self._running_tasks.pop(task.id, None)
            if result is None and run_task.done() and not run_task.cancelled():
                try:
                    run_result = run_task.result()
                    if isinstance(run_result, AgentResponse):
                        result = run_result
                except Exception:
                    result = None
            if result is not None and result.error and not result.partial_text:
                result.partial_text = _bounded_log_excerpt(log_path, max_chars=_PARTIAL_EXCERPT_MAX_CHARS)
                if not result.terminal_reason and result.error_kind in {"timeout", "max_turns"}:
                    result.terminal_reason = result.error_kind
            if result is not None:
                await self._record_thread_agent_run(
                    thread_id=task.thread_id,
                    mode=await self._task_log_mode(task),
                    agent_name=agent.name,
                    live_log_path=log_path,
                    duration_s=asyncio.get_running_loop().time() - started,
                    task_id=task.id,
                    skill_name=task.skill_name,
                    request_id=f"{task.id}-step{step}",
                    error=result.error,
                )

    def _agent_log_path(self, task: RuntimeTask, step: int, agent_name: str) -> Path:
        safe_agent = re.sub(r"[^a-zA-Z0-9._-]+", "-", agent_name).strip("-") or "agent"
        safe_thread = re.sub(r"[^a-zA-Z0-9._-]+", "-", task.thread_id).strip("-") or "thread"
        return self._agent_logs_root / f"thread-{safe_thread}-{task.id}-step{step}-{safe_agent}.log"

    def chat_agent_log_base_path(
        self,
        *,
        thread_id: str,
        request_id: str,
        purpose: str,
    ) -> Path:
        safe_thread = re.sub(r"[^a-zA-Z0-9._-]+", "-", thread_id).strip("-") or "thread"
        safe_purpose = re.sub(r"[^a-zA-Z0-9._-]+", "-", purpose).strip("-") or "chat"
        return self._agent_logs_root / f"thread-{safe_thread}-{safe_purpose}-{request_id}.log"

    async def _execute_merge(self, task: RuntimeTask, *, actor_id: str, source: str) -> str:
        if task.status not in {TASK_STATUS_WAITING_MERGE, TASK_STATUS_APPLIED, TASK_STATUS_MERGE_FAILED}:
            return f"Task `{task.id}` is not waiting merge (status: {task.status})."
        if not self._merge_gate_enabled:
            return "Merge gate is disabled."
        if self._merge_target_branch_mode != "current":
            return "Only target_branch_mode=current is supported in v0.5.2."

        workspace = Path(task.workspace_path) if task.workspace_path else None
        if workspace is None or not workspace.exists():
            return await self._mark_merge_blocked(task, "Workspace path is missing; cannot build patch.")

        try:
            if self._merge_require_clean_repo and not await self._worktree.repo_is_clean():
                return await self._mark_merge_blocked(
                    task,
                    "Main repository is not clean. Commit/stash changes before merging runtime task.",
                )

            patch = await self._worktree.create_patch(workspace)
            if not patch.strip():
                return await self._mark_merge_blocked(task, "No patch produced from task workspace.")

            if self._merge_preflight_check:
                await self._worktree.apply_patch_check(patch)
            await self._worktree.apply_patch(patch)

            commit_hash: str | None = None
            if self._merge_auto_commit:
                msg = self._merge_commit_template.format(
                    task_id=task.id,
                    goal_short=self._goal_short(task.goal),
                )
                commit_hash = await self._worktree.commit_repo_changes(msg)

            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_MERGED,
                merge_commit_hash=commit_hash,
                merge_error=None,
                summary="Merged into current branch.",
                ended_at_now=True,
            )
            await self._store.add_runtime_event(
                task.id,
                "task.merged",
                {
                    "actor_id": actor_id,
                    "source": source,
                    "commit_hash": commit_hash,
                    "auto_commit": self._merge_auto_commit,
                },
            )
            if task.task_type == TASK_TYPE_SKILL_CHANGE:
                await self._on_skill_task_merged(
                    task,
                    await self._resolve_last_agent_name(task),
                    commit_hash or "",
                )
            extra = f" commit `{commit_hash}`" if commit_hash else ""
            logger.info("Runtime task=%s MERGED commit=%s", task.id, commit_hash or "none")
            if self._cleanup_merged_immediately:
                cleaned = await self._cleanup_single_task(task)
                if cleaned:
                    logger.info("Runtime task=%s workspace cleaned immediately after merge", task.id)
                    if self._cleanup_prune_worktrees:
                        try:
                            await self._worktree.prune_worktrees()
                        except Exception:
                            logger.debug("git worktree prune failed after immediate merge cleanup", exc_info=True)
            merged_note = f"Task `{task.id}` merged successfully.{extra}"
            if task.task_type == TASK_TYPE_SKILL_CHANGE and task.skill_name:
                merged_note += (
                    f" Skill `{task.skill_name}` merged and synced to active Claude/Gemini workspaces. "
                    "If an existing session still does not see it, run `/reload-skills`."
                )
            await self._notify(task, merged_note, record_history=True, terminal=True)
            await self._signal_status_by_id(task, TASK_STATUS_MERGED)
            await self._resolve_notification("task_waiting_merge", task_id=task.id)
            return merged_note
        except WorktreeError as exc:
            return await self._mark_merge_blocked(task, str(exc))
        except Exception as exc:
            return await self._mark_merge_blocked(task, f"Unexpected merge error: {exc}")

    async def _mark_merge_blocked(self, task: RuntimeTask, error: str) -> str:
        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_WAITING_MERGE,
            merge_error=error[:2000],
        )
        await self._store.add_runtime_event(task.id, "task.merge_blocked", {"error": error[:1000]})
        logger.warning("Runtime task=%s MERGE_BLOCKED error=%s", task.id, error[:600])

        refreshed = await self._store.get_runtime_task(task.id)
        blocked_task = refreshed or task
        session = self._session_for(blocked_task)
        if session is not None:
            nonce = await self._store.create_runtime_decision_nonce(
                task.id,
                ttl_minutes=self._decision_ttl_minutes,
            )
            text = await self._merge_gate_text(blocked_task)
            msg_id = await self._send_decision_surface(
                session,
                blocked_task.thread_id,
                text,
                blocked_task.id,
                nonce,
                ["merge", "discard", "request_changes"],
            )
            if msg_id:
                await self._store.update_runtime_task(task.id, decision_message_id=msg_id)

        await self._notify(
            blocked_task,
            (
                f"Task `{task.id}` merge blocked: {error[:400]}\n"
                "Check whether another process or uncommitted change is touching the main repository. "
                "Reply `retry merge` after cleanup, `wait` to keep it pending, or `discard` to end it."
            ),
            record_history=True,
            terminal=True,
        )
        await self._signal_status_by_id(blocked_task, TASK_STATUS_WAITING_MERGE)
        return f"Task `{task.id}` merge blocked: {error[:200]}"

    async def _cleanup_expired_tasks(self) -> int:
        candidates: list[RuntimeTask] = []
        delayed_statuses = sorted(
            status
            for status in _TERMINAL_CLEANUP_STATUSES
            if not (self._cleanup_merged_immediately and status == TASK_STATUS_MERGED)
        )
        if delayed_statuses:
            candidates.extend(
                await self._store.list_runtime_cleanup_candidates(
                    statuses=delayed_statuses,
                    older_than_hours=self._cleanup_retention_hours,
                    limit=200,
                )
            )
        if self._cleanup_merged_immediately:
            candidates.extend(
                await self._store.list_runtime_cleanup_candidates(
                    statuses=[TASK_STATUS_MERGED],
                    older_than_hours=0,
                    limit=200,
                )
            )
        cleaned = 0
        seen: set[str] = set()
        for task in candidates:
            if task.id in seen:
                continue
            seen.add(task.id)
            if await self._cleanup_single_task(task):
                cleaned += 1
        if cleaned and self._cleanup_prune_worktrees:
            try:
                await self._worktree.prune_worktrees()
            except Exception:
                logger.debug("git worktree prune failed", exc_info=True)
        return cleaned

    async def _cleanup_expired_agent_logs(self) -> int:
        roots = [root for root in (self._agent_logs_root, self._thread_logs_root) if root.exists()]
        if not roots:
            return 0
        cutoff_ts = time.time() - (max(0, self._cleanup_retention_hours) * 3600)
        cleaned = 0
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    if path.stat().st_mtime > cutoff_ts:
                        continue
                    path.unlink(missing_ok=True)
                    cleaned += 1
                except Exception as exc:
                    logger.warning("Failed to remove stale agent log %s: %s", path, exc)
        self._live_agent_logs = {
            task_id: path
            for task_id, path in self._live_agent_logs.items()
            if path.exists()
        }
        for root in roots:
            for directory in sorted(root.rglob("*"), reverse=True):
                if directory.is_dir():
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
        return cleaned

    async def _cleanup_single_task(self, task: RuntimeTask) -> bool:
        if not task.workspace_path:
            return False
        workspace = Path(task.workspace_path)
        if workspace.exists():
            try:
                if self._uses_merge_flow(task):
                    await self._worktree.remove_worktree(workspace)
                else:
                    shutil.rmtree(workspace, ignore_errors=True)
            except Exception as exc:
                logger.warning("Failed to remove workspace for task=%s: %s", task.id, exc)
                return False
        await self._store.update_runtime_task(
            task.id,
            workspace_path=None,
            workspace_cleaned_at="__NOW__",
        )
        await self._store.add_runtime_event(task.id, "task.workspace_cleaned", {"workspace": str(workspace)})
        return True

    async def _fail(self, task: RuntimeTask, error: str, *, response: AgentResponse | None = None) -> None:
        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_FAILED,
            error=error[:2000],
            ended_at_now=True,
        )
        await self._store.add_runtime_event(task.id, "task.failed", {"error": error[:1000]})
        logger.error("Runtime task=%s FAILED error=%s", task.id, error[:600])
        if task.automation_name:
            await self._store.upsert_automation_state(
                task.automation_name,
                platform=task.platform,
                channel_id=task.channel_id,
                last_error=error[:1000],
            )
        notify_text = f"Task `{task.id}` failed: {error[:400]}"
        if response is not None:
            notify_text = self._format_agent_failure_text(
                response,
                prefix=f"Task `{task.id}` failed.",
            )
        await self._notify(
            task,
            notify_text,
            record_history=True,
            terminal=True,
            usage=response.usage if response is not None else None,
        )
        await self._signal_status_by_id(task, TASK_STATUS_FAILED)

    async def _on_skill_task_merged(
        self,
        task: RuntimeTask,
        agent_name: str,
        merge_commit_hash: str,
    ) -> None:
        if self._skill_syncer is not None:
            try:
                self._skill_syncer.sync()
                self._skill_syncer.refresh_workspace_dirs(self._workspace_skills_dirs)
            except Exception as exc:
                logger.warning("Post-merge skill sync failed for task %s: %s", task.id, exc)

        warnings: list[str] = []
        if self._skills_path and task.skill_name:
            try:
                from oh_my_agent.skills.validator import SkillValidator

                result = SkillValidator().validate(self._skills_path / task.skill_name)
                warnings = result.warnings
            except Exception:
                logger.debug("SkillValidator failed after merge for task %s", task.id, exc_info=True)

        if task.skill_name:
            await self._store.upsert_skill_provenance(
                task.skill_name,
                source_task_id=task.id,
                created_by=task.created_by,
                agent_name=agent_name,
                platform=task.platform,
                channel_id=task.channel_id,
                thread_id=task.thread_id,
                validation_mode="quick_validate",
                validated=1,
                validation_warnings=warnings,
                merged_commit_hash=merge_commit_hash or None,
            )

    async def _prepare_task_workspace(self, task: RuntimeTask) -> Path:
        if self._uses_merge_flow(task):
            return await self._worktree.ensure_worktree(task.id)

        workspace = self._runtime_workspace_root / "_artifacts" / task.id
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    async def _collect_changed_files(self, task: RuntimeTask, workspace: Path) -> list[str]:
        if self._uses_merge_flow(task):
            return await self._worktree.changed_files(workspace)
        return self._list_workspace_files(workspace)

    @staticmethod
    def _list_workspace_files(workspace: Path) -> list[str]:
        if not workspace.exists():
            return []
        return sorted(
            str(path.relative_to(workspace)).replace("\\", "/")
            for path in workspace.rglob("*")
            if path.is_file()
        )

    @staticmethod
    def _uses_merge_flow(task: RuntimeTask) -> bool:
        return task.completion_mode == TASK_COMPLETION_MERGE

    def _build_output_summary(self, task: RuntimeTask, changed_files: list[str], test_summary: str) -> str | None:
        if task.completion_mode == TASK_COMPLETION_MERGE:
            return None
        if task.automation_name:
            return None
        parts: list[str] = []
        if changed_files:
            parts.append(f"Artifacts ({len(changed_files)}): " + ", ".join(changed_files[:10]))
            if len(changed_files) > 10:
                parts[-1] += f" and {len(changed_files) - 10} more"
        formatted = self._format_test_output(test_summary)
        if formatted:
            parts.append(f"Validation: {formatted}")
        return " | ".join(parts)[:1000] if parts else None

    def _completed_text(
        self,
        *,
        task: RuntimeTask,
        changed_files: list[str],
        test_summary: str,
        delivery: ArtifactDeliveryResult | None = None,
    ) -> str:
        lines = [f"Task `{task.id}` completed."]
        if task.output_summary:
            lines.append(task.output_summary)
        elif changed_files:
            lines.append("Artifacts ready:")
            lines.extend(f"- `{path}`" for path in changed_files[:8])
            if len(changed_files) > 8:
                lines.append(f"- ... and {len(changed_files) - 8} more")
        formatted = self._format_test_output(test_summary)
        if formatted:
            lines.append("")
            lines.append("Validation result:")
            lines.append(f"```text\n{formatted[:500]}\n```")
        delivery_lines = self._render_delivery_lines(delivery)
        if delivery_lines:
            lines.append("")
            lines.extend(delivery_lines)
        return "\n".join(lines)[:1900]

    def _artifact_paths_for_task(self, task: RuntimeTask, changed_files: list[str]) -> list[Path]:
        if not task.workspace_path:
            return []
        workspace = Path(task.workspace_path)
        results: list[Path] = []
        manifest = task.artifact_manifest or changed_files
        for rel_path in manifest:
            candidate = workspace / rel_path
            if candidate.is_file():
                results.append(candidate)
        return results

    def _archive_artifact_files(self, task_id: str, paths: list[Path]) -> list[str]:
        """Copy artifact files into the central reports archive.

        Returns absolute string paths of the archived copies. Returns [] if
        `reports_dir` is disabled, paths is empty, or archiving fails.
        """
        if not self._reports_dir or not paths:
            return []
        try:
            archive_dir = self._reports_dir / "artifacts"
            archive_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("Failed to create reports dir %s", self._reports_dir, exc_info=True)
            return []
        archived: list[str] = []
        suffix_tag = (task_id or "")[:8] or "task"
        for path in paths:
            try:
                source = path.resolve()
                dest = archive_dir / path.name
                if dest.exists() and dest.resolve() != source:
                    dest = archive_dir / f"{dest.stem}-{suffix_tag}{dest.suffix}"
                shutil.copy2(source, dest)
                archived.append(str(dest.resolve()))
            except OSError:
                logger.warning(
                    "Failed to archive artifact %s to %s",
                    path,
                    self._reports_dir,
                    exc_info=True,
                )
        return archived

    def _render_delivery_lines(self, delivery: ArtifactDeliveryResult | None) -> list[str]:
        if delivery is None:
            return []
        lines = [f"Delivery mode: `{delivery.mode}`"]
        if delivery.mode == "attachment" and delivery.attachment_names:
            lines.append("Attachments:")
            lines.extend(f"- `{name}`" for name in delivery.attachment_names[:8])
            if len(delivery.attachment_names) > 8:
                lines.append(f"- ... and {len(delivery.attachment_names) - 8} more")
        elif delivery.delivered_paths:
            lines.append("Artifact paths:")
            lines.extend(f"- `{path}`" for path in delivery.delivered_paths[:8])
            if len(delivery.delivered_paths) > 8:
                lines.append(f"- ... and {len(delivery.delivered_paths) - 8} more")
        if delivery.archived_paths:
            lines.append("Archived to:")
            lines.extend(f"- `{path}`" for path in delivery.archived_paths[:8])
            if len(delivery.archived_paths) > 8:
                lines.append(f"- ... and {len(delivery.archived_paths) - 8} more")
        return lines

    async def _deliver_artifacts(
        self,
        *,
        task: RuntimeTask,
        changed_files: list[str],
    ) -> ArtifactDeliveryResult | None:
        session = self._session_for(task)
        if session is None:
            return None
        artifact_paths = self._artifact_paths_for_task(task, changed_files)
        if not artifact_paths:
            return None
        archived_paths = self._archive_artifact_files(task.id, artifact_paths)
        summary_text = task.output_summary or f"{len(artifact_paths)} artifact(s) ready."
        return await self.deliver_files(
            session=session,
            thread_id=task.thread_id,
            artifact_paths=artifact_paths,
            summary_text=summary_text,
            log_label=f"task={task.id}",
            archived_paths=archived_paths,
        )

    async def deliver_files(
        self,
        *,
        session: ChannelSession,
        thread_id: str,
        artifact_paths: list[Path],
        summary_text: str,
        log_label: str = "",
        archived_paths: list[str] | None = None,
    ) -> ArtifactDeliveryResult | None:
        """Deliver files via attachment upload with local path fallback.

        This is the reusable core of artifact delivery, decoupled from
        RuntimeTask so it can serve future non-task callers.
        """
        if not artifact_paths:
            return None
        archived = list(archived_paths or [])

        channel_impl = type(session.channel)
        supports_attachment_upload = (
            getattr(channel_impl, "send_attachment", None) is not BaseChannel.send_attachment
            or getattr(channel_impl, "send_attachments", None) is not BaseChannel.send_attachments
        )
        total_size = 0
        attachments = []
        for path in artifact_paths:
            try:
                size = path.stat().st_size
            except OSError:
                size = self._artifact_attachment_max_bytes + 1
            total_size += size
            if size > self._artifact_attachment_max_bytes:
                attachments = []
                break
            attachments.append(
                OutgoingAttachment(
                    filename=path.name,
                    content_type="text/markdown" if path.suffix.lower() == ".md" else "application/octet-stream",
                    local_path=path,
                )
            )

        if (
            supports_attachment_upload
            and attachments
            and len(attachments) <= self._artifact_attachment_max_count
            and total_size <= self._artifact_attachment_max_total_bytes
        ):
            try:
                message_ids = await session.channel.send_attachments(thread_id, attachments)
                return ArtifactDeliveryResult(
                    mode="attachment",
                    delivered_paths=[str(path.resolve()) for path in artifact_paths],
                    message_ids=message_ids,
                    summary_text=summary_text,
                    attachment_names=[attachment.filename for attachment in attachments],
                    archived_paths=archived,
                )
            except Exception:
                logger.warning("Artifact attachment delivery failed %s", log_label, exc_info=True)

        if (
            attachments
            and not supports_attachment_upload
        ):
            logger.info("Artifact delivery falling back to path mode because channel does not support uploads")

        return ArtifactDeliveryResult(
            mode="path",
            delivered_paths=[str(path.resolve()) for path in artifact_paths],
            message_ids=[],
            summary_text=summary_text,
            attachment_names=[],
            archived_paths=archived,
        )

    async def _automation_completed_text(
        self,
        *,
        task: RuntimeTask,
        changed_files: list[str],
        delivery: ArtifactDeliveryResult | None = None,
    ) -> str:
        artifact_preview = self._automation_artifact_preview(task, changed_files)
        source_text = ""
        artifact_path: str | None = None
        if artifact_preview:
            artifact_path, source_text = artifact_preview
        elif task.output_summary:
            source_text = task.output_summary

        body, notes = self._split_automation_output(source_text)
        if artifact_path:
            notes.append(f"artifact: `{artifact_path}`")
        elif changed_files:
            notes.append(
                "artifacts: " + ", ".join(f"`{path}`" for path in changed_files[:4])
            )
            if len(changed_files) > 4:
                notes[-1] += f" and {len(changed_files) - 4} more"
        if task.workspace_path:
            run_dir = Path(task.workspace_path).name
            notes.append(f"run dir: `_artifacts/{run_dir}`")

        lines: list[str] = []
        if body:
            lines.append("**Output**")
            lines.append(body)
        elif notes:
            lines.append("**Output**")
            lines.append("Automation run completed.")
        else:
            lines.append("**Output**")
            lines.append("Automation run completed.")

        if notes:
            lines.append("")
            for note in notes:
                lines.append(f"-# {note}")

        lines.append("")
        lines.append("-# ✅ automation run complete")
        if delivery:
            lines.append(f"-# delivery: `{delivery.mode}`")
            for path in delivery.delivered_paths[:4]:
                lines.append(f"-# path: `{path}`")
        return "\n".join(lines)

    def _automation_artifact_preview(
        self,
        task: RuntimeTask,
        changed_files: list[str],
    ) -> tuple[str, str] | None:
        if not task.workspace_path or len(changed_files) != 1:
            return None
        rel_path = changed_files[0]
        candidate = Path(task.workspace_path) / rel_path
        if not candidate.is_file():
            return None
        if candidate.stat().st_size > 32_000:
            return None
        try:
            content = candidate.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            return None
        if not content or "\x00" in content:
            return None
        return rel_path, content[:10000]

    @staticmethod
    def _split_automation_output(text: str) -> tuple[str, list[str]]:
        cleaned = _TASK_STATE_LINE_RE.sub("", text or "")
        cleaned = _BLOCK_REASON_LINE_RE.sub("", cleaned)
        cleaned = cleaned.strip()
        if not cleaned:
            return "", []

        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
        if not paragraphs:
            return "", []

        body_parts: list[str] = []
        notes: list[str] = []
        for paragraph in paragraphs:
            if RuntimeService._is_automation_note_paragraph(paragraph):
                notes.append(RuntimeService._normalize_automation_note(paragraph))
                continue
            body_parts.append(paragraph)

        if not body_parts and notes:
            return "", notes

        if len(body_parts) > 1:
            leading = body_parts[0]
            if RuntimeService._is_automation_prep_paragraph(leading):
                notes.insert(0, RuntimeService._normalize_automation_note(leading))
                body_parts = body_parts[1:]

        body = "\n\n".join(body_parts).strip()
        return body, notes

    @staticmethod
    def _is_automation_note_paragraph(text: str) -> bool:
        lowered = " ".join(text.split()).lower()
        if "`" in text and "/" in text:
            return True
        return lowered.startswith(
            (
                "created ",
                "implemented in ",
                "saved ",
                "wrote ",
                "written to ",
                "artifact:",
                "artifacts:",
                "output:",
            )
        )

    @staticmethod
    def _is_automation_prep_paragraph(text: str) -> bool:
        lowered = " ".join(text.split()).lower()
        return lowered.startswith(
            (
                "i'll ",
                "i will ",
                "i’m going to ",
                "i'm going to ",
                "the workspace is empty",
                "the directory is empty",
                "i have the current ",
                "i'll fetch ",
                "i will fetch ",
                "i'm generating ",
                "i am generating ",
            )
        )

    @staticmethod
    def _normalize_automation_note(text: str) -> str:
        return " ".join(text.split())

    async def _resolve_last_agent_name(self, task: RuntimeTask) -> str:
        events = await self._store.list_runtime_events(task.id, limit=20)
        for event in reversed(events):
            payload = event.get("payload", {})
            agent = payload.get("agent")
            if isinstance(agent, str) and agent:
                return agent
        return task.preferred_agent or self._default_agent or ""

    @property
    def service_log_path(self) -> Path:
        return self._service_log_path

    @property
    def thread_logs_root(self) -> Path:
        return self._thread_logs_root

    def _thread_log_path(self, thread_id: str) -> Path:
        safe_thread = re.sub(r"[^A-Za-z0-9_.-]+", "-", thread_id or "thread").strip("-") or "thread"
        return self._thread_logs_root / f"{safe_thread}.log"

    @staticmethod
    def _agent_specific_log_path(log_path: Path | None, agent_name: str) -> Path | None:
        if log_path is None:
            return None
        safe_agent = re.sub(r"[^A-Za-z0-9_.-]+", "-", agent_name).strip("-") or "agent"
        suffix = log_path.suffix or ".log"
        return log_path.with_name(f"{log_path.stem}-{safe_agent}{suffix}")

    async def _resolve_task_source(self, task: RuntimeTask) -> str:
        cached = self._task_sources.get(task.id)
        if cached:
            return cached
        events = await self._store.list_runtime_events(task.id, limit=8)
        for event in events:
            if event.get("event_type") == "task.created":
                source = str(event.get("payload", {}).get("source") or "").strip()
                if source:
                    self._task_sources[task.id] = source
                    return source
            if event.get("event_type") == "task.resumed":
                self._task_sources[task.id] = "resume"
                return "resume"
            if event.get("event_type") == "task.ask_user_answered":
                self._task_sources[task.id] = "hitl_resume"
                return "hitl_resume"
        return ""

    async def _task_log_mode(self, task: RuntimeTask) -> str:
        source = await self._resolve_task_source(task)
        if source == "repair_skill":
            return "repair_skill"
        if source == "resume":
            return "resume"
        if source == "hitl_resume":
            return "hitl_resume"
        return task.task_type

    async def _last_hitl_answer_payload_for_task(self, task_id: str) -> dict[str, Any] | None:
        events = await self._store.list_runtime_events(task_id, limit=12)
        for event in reversed(events):
            if event.get("event_type") != "task.ask_user_answered":
                continue
            payload = event.get("payload")
            if isinstance(payload, dict):
                return payload
        return None

    async def _record_thread_agent_run(
        self,
        *,
        thread_id: str,
        mode: str,
        agent_name: str,
        live_log_path: Path | None,
        duration_s: float,
        task_id: str | None = None,
        skill_name: str | None = None,
        request_id: str | None = None,
        error: str | None = None,
    ) -> None:
        thread_log = self._thread_log_path(thread_id)
        thread_log.parent.mkdir(parents=True, exist_ok=True)
        ended_ts = time.time()
        started_ts = max(0.0, ended_ts - max(duration_s, 0.0))
        started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started_ts))
        ended_at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ended_ts))
        content = ""
        if live_log_path and live_log_path.exists():
            try:
                content = live_log_path.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                content = ""
        lines = [
            "=== run start ===",
            f"started_at={started_at}",
            f"thread_id={thread_id}",
            f"mode={mode}",
            f"agent={agent_name}",
        ]
        if task_id:
            lines.append(f"task_id={task_id}")
        if skill_name:
            lines.append(f"skill_name={skill_name}")
        if request_id:
            lines.append(f"request_id={request_id}")
        if live_log_path:
            lines.append(f"live_log_path={live_log_path}")
        lines.append("--- output ---")
        if content:
            lines.append(content)
        else:
            lines.append("(no live output captured)")
        lines.extend(
            [
                "=== run end ===",
                f"ended_at={ended_at}",
                f"duration_seconds={duration_s:.2f}",
                f"error={error or ''}",
                "",
            ]
        )
        with thread_log.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))

    async def record_thread_agent_run(
        self,
        *,
        thread_id: str,
        mode: str,
        agent_name: str,
        live_log_path: Path | None,
        duration_s: float,
        task_id: str | None = None,
        skill_name: str | None = None,
        request_id: str | None = None,
        error: str | None = None,
    ) -> None:
        await self._record_thread_agent_run(
            thread_id=thread_id,
            mode=mode,
            agent_name=agent_name,
            live_log_path=live_log_path,
            duration_s=duration_s,
            task_id=task_id,
            skill_name=skill_name,
            request_id=request_id,
            error=error,
        )

    def _extract_thread_log_excerpt(
        self,
        *,
        thread_id: str,
        task_id: str | None = None,
        request_id: str | None = None,
    ) -> str | None:
        thread_log = self._thread_log_path(thread_id)
        if not thread_log.exists():
            return None
        try:
            text = thread_log.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
        blocks = [block.strip() for block in text.split("=== run start ===") if block.strip()]
        if not blocks:
            return None
        for block in reversed(blocks):
            if task_id and f"task_id={task_id}" not in block:
                continue
            if request_id and f"request_id={request_id}" not in block:
                continue
            return self._tail_text("=== run start ===\n" + block)
        return self._tail_text("=== run start ===\n" + blocks[-1])

    def _validate_changed_paths(self, paths: list[str]) -> str | None:
        for raw in paths:
            path = raw.replace("\\", "/")
            if any(fnmatch.fnmatch(path, pat) for pat in self._denied_paths):
                return f"Changed forbidden path: {path}"
            if self._path_policy_mode == "allow_all_with_denylist":
                continue
            if self._allowed_paths and not any(fnmatch.fnmatch(path, pat) for pat in self._allowed_paths):
                return f"Changed path outside allow-list: {path}"
        return None

    async def _send_decision_surface(
        self,
        session: ChannelSession,
        thread_id: str,
        text: str,
        task_id: str,
        nonce: str,
        actions: list[str],
    ) -> str | None:
        sender = getattr(session.channel, "send_task_draft", None)
        if sender and callable(sender):
            try:
                return await sender(
                    thread_id=thread_id,
                    draft_text=text,
                    task_id=task_id,
                    nonce=nonce,
                    actions=actions,
                )
            except Exception as exc:
                logger.warning("send_task_draft failed, falling back to plain text: %s", exc)
        await session.channel.send(thread_id, text)
        return None

    async def _remind_blocking_draft(
        self,
        *,
        session: ChannelSession,
        thread_id: str,
        task: RuntimeTask,
        automation_name: str,
    ) -> None:
        """Re-post a decision surface when a scheduler tick is skipped by an active DRAFT."""
        try:
            nonce = await self._store.create_runtime_decision_nonce(
                task.id,
                ttl_minutes=self._decision_ttl_minutes,
            )
        except Exception as exc:
            logger.warning("Failed to mint reminder nonce for task %s: %s", task.id, exc)
            return
        text = (
            f"⏰ 定时任务 `{automation_name}` 被跳过：DRAFT task `{task.id}` 还在等审批。\n"
            f"- **approve / reject / suggest**：决定这个 draft\n"
            f"- **discard**：扔掉它\n"
            f"- **replace**：丢弃并立刻用当前 cron 重跑（使用原始 prompt）"
        )
        msg_id = await self._send_decision_surface(
            session,
            thread_id,
            text,
            task.id,
            nonce,
            ["approve", "reject", "suggest", "discard", "replace"],
        )
        if msg_id:
            await self._store.update_runtime_task(task.id, decision_message_id=msg_id)

    def _latest_activity_for_task(self, task_id: str, max_chars: int = 200) -> str | None:
        """Read a bounded tail from the live agent log for a running task."""
        log_path = self._live_agent_logs.get(task_id)
        if not log_path or not log_path.exists():
            return None
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            return None
        if not content:
            return None
        tail = content[-max_chars:] if len(content) > max_chars else content
        last_newline = tail.find("\n")
        if last_newline > 0 and last_newline < len(tail) - 1:
            tail = tail[last_newline + 1:]
        return tail.strip() or None

    async def _notify(
        self,
        task: RuntimeTask,
        text: str,
        *,
        record_history: bool = False,
        terminal: bool = False,
        usage: dict[str, Any] | None = None,
    ) -> None:
        session = self._session_for(task)
        if session is None:
            return
        if task.automation_name:
            if record_history:
                await session.append_assistant(task.thread_id, text[:4000], "runtime")
            if terminal:
                await self._send_automation_terminal_message(task, text, usage=usage)
            return
        current = await self._store.get_runtime_task(task.id)
        status_message_id = current.status_message_id if current else task.status_message_id
        effective_status = current.status if current else task.status
        enriched_text = text
        if effective_status == TASK_STATUS_RUNNING and not terminal:
            activity = self._latest_activity_for_task(task.id)
            if activity:
                enriched_text = f"{text}\n\n**Latest activity**\n```text\n{activity}\n```"
        body = self._format_status_message(enriched_text)
        upsert = getattr(session.channel, "upsert_status_message", None)
        if upsert and callable(upsert):
            msg_id = await upsert(task.thread_id, body[:1900], message_id=status_message_id)
        else:
            msg_id = await session.channel.send(task.thread_id, body[:1900])
        if msg_id:
            if not current or current.status_message_id != msg_id:
                await self._store.update_runtime_task(task.id, status_message_id=msg_id)
        if record_history:
            await session.append_assistant(task.thread_id, text[:4000], "runtime")
        if terminal:
            await session.channel.send(task.thread_id, self._format_terminal_message(text)[:1900])

    async def _send_automation_terminal_message(
        self,
        task: RuntimeTask,
        text: str,
        *,
        usage: dict[str, Any] | None = None,
    ) -> None:
        session = self._session_for(task)
        if session is None:
            return
        agent_name = await self._resolve_last_agent_name(task)
        attribution = append_usage_audit(
            f"-# automation `{task.automation_name}` · run `{task.id}` · via **{agent_name}**",
            usage,
        )
        first_chunk_budget = max(1, 2000 - len(attribution) - 1)
        chunks = chunk_message(text, max_size=first_chunk_budget)
        if not chunks:
            await session.channel.send(task.thread_id, f"{attribution}\n*(empty automation output)*")
            return
        await session.channel.send(task.thread_id, f"{attribution}\n{chunks[0]}")
        remainder = text[len(chunks[0]):].lstrip()
        for chunk in chunk_message(remainder) if remainder else []:
            await session.channel.send(task.thread_id, chunk)

    async def _signal_status_by_id(self, task: RuntimeTask, status: str) -> None:
        emoji = self._emoji_for_status(status)
        if not emoji:
            return
        session = self._session_for(task)
        if session is None:
            return
        current = await self._store.get_runtime_task(task.id)
        message_id = None
        if current:
            message_id = current.status_message_id or current.decision_message_id
        else:
            message_id = task.status_message_id or task.decision_message_id
        if not message_id:
            return
        signaler = getattr(session.channel, "signal_task_status", None)
        if signaler and callable(signaler):
            try:
                await signaler(task.thread_id, message_id, emoji)
            except Exception:
                logger.debug("signal_task_status failed for task %s", task.id, exc_info=True)

    def _session_for(self, task: RuntimeTask) -> ChannelSession | None:
        return self._sessions.get(self._key(task.platform, task.channel_id))

    def _registry_for(self, task: RuntimeTask) -> AgentRegistry | None:
        return self._registries.get(self._key(task.platform, task.channel_id))

    @staticmethod
    def _key(platform: str, channel_id: str) -> str:
        return f"{platform}:{channel_id}"

    async def _restore_thread_agent_session(
        self,
        *,
        session: ChannelSession,
        thread_id: str,
        agent,
        fallback_session_id: str | None = None,
    ) -> None:
        if not hasattr(agent, "set_session_id") or not hasattr(agent, "get_session_id"):
            return
        if agent.get_session_id(thread_id):
            return
        stored = await self._store.load_session(
            session.platform,
            session.channel_id,
            thread_id,
            agent.name,
        )
        if stored:
            agent.set_session_id(thread_id, stored)
            return
        if fallback_session_id:
            agent.set_session_id(thread_id, fallback_session_id)

    async def _sync_thread_agent_session(
        self,
        *,
        session: ChannelSession,
        thread_id: str,
        agent,
    ) -> None:
        if not hasattr(agent, "get_session_id"):
            return
        current = agent.get_session_id(thread_id)
        if current:
            await self._store.save_session(
                session.platform,
                session.channel_id,
                thread_id,
                agent.name,
                current,
            )
        else:
            await self._store.delete_session(
                session.platform,
                session.channel_id,
                thread_id,
                agent.name,
            )

    @staticmethod
    def _clear_thread_agent_session(agent, thread_id: str) -> None:
        if hasattr(agent, "clear_session"):
            agent.clear_session(thread_id)

    async def _invoke_thread_agent(
        self,
        *,
        registry: AgentRegistry,
        session: ChannelSession,
        prompt: str,
        thread_id: str,
        force_agent: str,
        log_path: Path | None,
        purpose: str,
        skill_name: str | None = None,
        timeout_override_seconds: int | None = None,
        max_turns_override: int | None = None,
    ) -> AgentResponse:
        history = await session.get_history(thread_id)
        logger.info(
            "THREAD_AGENT_START purpose=%s thread=%s force_agent=%s skill_timeout_override=%r history_turns=%d",
            purpose,
            thread_id,
            force_agent,
            timeout_override_seconds,
            len(history),
        )
        run_task = asyncio.create_task(
            registry.run(
                prompt,
                history,
                thread_id=thread_id,
                force_agent=force_agent,
                log_path=log_path,
                run_label=f"{purpose} thread={thread_id}",
                timeout_override_seconds=timeout_override_seconds,
                max_turns_override=max_turns_override,
            )
        )
        started_at = time.perf_counter()
        interval = self._agent_heartbeat_seconds
        try:
            while True:
                try:
                    agent_used, response = await asyncio.wait_for(asyncio.shield(run_task), timeout=interval)
                    elapsed = time.perf_counter() - started_at
                    effective_log_path = self._agent_specific_log_path(log_path, agent_used.name)
                    if response.error and not response.partial_text:
                        response.partial_text = _bounded_log_excerpt(
                            effective_log_path,
                            max_chars=_PARTIAL_EXCERPT_MAX_CHARS,
                        )
                        if not response.terminal_reason and response.error_kind in {"timeout", "max_turns"}:
                            response.terminal_reason = response.error_kind
                    await self._record_thread_agent_run(
                        thread_id=thread_id,
                        mode=purpose,
                        agent_name=agent_used.name,
                        live_log_path=effective_log_path,
                        duration_s=elapsed,
                        skill_name=skill_name,
                        request_id=effective_log_path.stem if effective_log_path else purpose,
                        error=response.error,
                    )
                    logger.info(
                        "THREAD_AGENT_DONE purpose=%s thread=%s agent=%s elapsed=%.2fs response_error=%s response_len=%d",
                        purpose,
                        thread_id,
                        agent_used.name,
                        elapsed,
                        bool(response.error),
                        len(response.text or ""),
                    )
                    return response
                except asyncio.TimeoutError:
                    elapsed = time.perf_counter() - started_at
                    logger.info(
                        "THREAD_AGENT_RUNNING purpose=%s thread=%s agent=%s elapsed=%.2fs",
                        purpose,
                        thread_id,
                        force_agent,
                        elapsed,
                    )
        finally:
            if not run_task.done():
                run_task.cancel()
                with suppress(asyncio.CancelledError):
                    await run_task

    def _build_suspended_run_resume_prompt(
        self,
        run: SuspendedAgentRun,
        *,
        include_original_request: bool = False,
    ) -> str:
        context = run.resume_context or {}
        lines = [
            "[System Resume Context]",
            f"- The previous run paused because provider auth was required for `{run.provider}`.",
            f"- Auth for provider `{run.provider}` has now completed successfully.",
            "- Continue from where you left off.",
            "- Do not ask the user to login again unless a new auth challenge occurs.",
            "- For transcript/video extraction, trust only data fetched in this resumed run.",
            "- Do not use transcript-like text from old local logs/sessions as evidence.",
        ]
        credential_path = str(context.get("auth_credential_path") or "").strip()
        if credential_path:
            lines.append(f"- Auth credential path: `{credential_path}`")
            if str(run.provider).lower() == "bilibili":
                lines.append(
                    f"- When calling the bilibili extractor, pass `--cookies-path '{credential_path}'`."
                )
        source_text = str(context.get("original_user_content") or context.get("agent_prompt") or "")
        url_match = re.search(r"https?://\S+", source_text)
        if url_match and str(run.provider).lower() == "bilibili":
            target_url = url_match.group(0).rstrip(").,;!?")
            lines.append(f"- Target URL: `{target_url}`")
            lines.append("- Re-run the bilibili extraction for this URL in this resumed run and base your summary only on that JSON output.")
        if context.get("skill_name"):
            lines.append(f"- Continue using skill `{context['skill_name']}` if still relevant.")
        if context.get("original_user_content"):
            lines.append(f"- Original user request: {context['original_user_content']}")
        if include_original_request and context.get("agent_prompt"):
            lines.append("")
            lines.append("Original request context:")
            lines.append(str(context["agent_prompt"]))
        return "\n".join(lines)

    async def _send_thread_agent_response(
        self,
        *,
        session: ChannelSession,
        thread_id: str,
        agent_name: str,
        text: str,
        usage: dict[str, Any] | None,
    ) -> None:
        attribution = append_usage_audit(f"-# via **{agent_name}**", usage)
        first_chunk_budget = max(1, 2000 - len(attribution) - 1)
        first_chunks = chunk_message(text, max_size=first_chunk_budget)
        if not first_chunks:
            await session.channel.send(thread_id, f"{attribution}\n*(empty response)*")
            return
        await session.channel.send(thread_id, f"{attribution}\n{first_chunks[0]}")
        remainder = text[len(first_chunks[0]):].lstrip()
        for chunk in chunk_message(remainder) if remainder else []:
            await session.channel.send(thread_id, chunk)

    async def _send_auth_challenge_progress(
        self,
        *,
        session: ChannelSession | None,
        thread_id: str,
        agent_name: str,
        text: str,
        provider: str,
        skill_name: str | None,
        original_user_content: str | None,
        usage: dict[str, Any] | None,
    ) -> None:
        if session is None:
            return
        visible_text = self._build_auth_pause_message(
            raw_text=text,
            provider=provider,
            skill_name=skill_name,
            original_user_content=original_user_content,
        )
        if not visible_text:
            return
        await session.append_assistant(thread_id, visible_text, agent_name)
        await self._send_thread_agent_response(
            session=session,
            thread_id=thread_id,
            agent_name=agent_name,
            text=visible_text,
            usage=usage,
        )

    async def _send_runtime_ask_user_progress(
        self,
        *,
        task: RuntimeTask,
        agent_name: str,
        text: str,
        usage: dict[str, Any] | None,
    ) -> None:
        session = self._session_for(task)
        if session is None:
            return
        cleaned = self._clean_hitl_visible_text(text)
        if not cleaned:
            return
        await session.append_assistant(task.thread_id, cleaned, agent_name)
        if task.automation_name:
            await self._send_automation_terminal_message(task, cleaned, usage=usage)
            return
        await self._send_thread_agent_response(
            session=session,
            thread_id=task.thread_id,
            agent_name=agent_name,
            text=cleaned,
            usage=usage,
        )

    @staticmethod
    def _choice_to_dict(choice: AskUserChoice | dict[str, Any]) -> dict[str, Any]:
        if isinstance(choice, AskUserChoice):
            return {
                "id": choice.id,
                "label": choice.label,
                "description": choice.description,
            }
        return {
            "id": str(choice.get("id") or ""),
            "label": str(choice.get("label") or ""),
            "description": (
                str(choice.get("description")).strip()
                if choice.get("description") is not None
                else None
            ),
        }

    @staticmethod
    def _find_hitl_choice(prompt: HitlPrompt, choice_id: str) -> dict[str, Any] | None:
        for choice in prompt.choices:
            if str(choice.get("id") or "") == choice_id:
                return {
                    "id": str(choice.get("id") or ""),
                    "label": str(choice.get("label") or ""),
                    "description": (
                        str(choice.get("description")).strip()
                        if choice.get("description") is not None
                        else None
                    ),
                }
        return None

    @staticmethod
    def _summarize_hitl_question(question: str) -> str:
        compact = " ".join((question or "").split()).strip()
        if len(compact) > 160:
            compact = compact[:157].rstrip() + "..."
        return f"Awaiting user choice: {compact}" if compact else "Awaiting user choice."

    @staticmethod
    def _clean_hitl_visible_text(text: str) -> str:
        stripped = _TASK_STATE_LINE_RE.sub("", text or "")
        stripped = _BLOCK_REASON_LINE_RE.sub("", stripped)
        stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip()
        return stripped

    @classmethod
    def _build_hitl_answer_payload(cls, prompt: HitlPrompt) -> dict[str, Any]:
        return {
            "prompt_id": prompt.id,
            "question": prompt.question,
            "choice_id": prompt.selected_choice_id or "",
            "choice_label": prompt.selected_choice_label or "",
            "choice_description": prompt.selected_choice_description or "",
            "answered_at": prompt.completed_at or prompt.updated_at or "",
            "target_kind": prompt.target_kind,
        }

    @classmethod
    def _build_hitl_answer_block(cls, prompt: HitlPrompt) -> str:
        description = prompt.selected_choice_description or ""
        lines = [
            "[HITL Answer]",
            f"Question: {prompt.question}",
            f"Selected choice id: {prompt.selected_choice_id or ''}",
            f"Selected choice label: {prompt.selected_choice_label or ''}",
            f"Selected choice description: {description}",
        ]
        return "\n".join(lines)

    @classmethod
    def _build_hitl_resume_prompt(cls, prompt: HitlPrompt, *, include_original_request: bool = False) -> str:
        context = prompt.resume_context or {}
        answer_payload = context.get("last_hitl_answer")
        lines = [
            "[System Resume Context]",
            "- The previous run paused because it required a single explicit user choice.",
            "- The user has now answered that question.",
            "- Continue from where you left off.",
            "- Do not ask the same question again unless a new, necessary ambiguity remains.",
            "",
            "Resolved user choice:",
            cls._build_hitl_answer_block(prompt),
        ]
        if isinstance(answer_payload, dict) and answer_payload:
            lines.extend(
                [
                    "",
                    "Structured HITL answer payload:",
                    json.dumps(answer_payload, ensure_ascii=False),
                ]
            )
        if context.get("skill_name"):
            lines.append(f"- Continue using skill `{context['skill_name']}` if still relevant.")
        if context.get("original_user_content"):
            lines.append(f"- Original user request: {context['original_user_content']}")
        if include_original_request and context.get("agent_prompt"):
            lines.extend(["", "Original request context:", str(context["agent_prompt"])])
        return "\n".join(lines)

    async def _send_hitl_prompt(self, channel, prompt: HitlPrompt) -> str | None:
        sender = getattr(channel, "send_hitl_prompt", None)
        if not callable(sender):
            return None
        try:
            return await sender(thread_id=prompt.thread_id, prompt=prompt)
        except Exception:
            logger.warning(
                "HITL prompt delivery failed prompt=%s thread=%s",
                prompt.id,
                prompt.thread_id,
                exc_info=True,
            )
            return None

    async def _send_hitl_answer_record(self, prompt: HitlPrompt) -> None:
        session = self._sessions.get(self._key(prompt.platform, prompt.channel_id))
        if session is None:
            return
        description = prompt.selected_choice_description or ""
        lines = [
            "**Input recorded**",
            f"Prompt: `{prompt.id}`",
            f"Question: {prompt.question}",
            (
                f"Selected: **{prompt.selected_choice_label or prompt.selected_choice_id or 'unknown'}** "
                f"(`{prompt.selected_choice_id or ''}`)"
            ),
        ]
        if description:
            lines.append(f"Details: {description}")
        if prompt.task_id:
            lines.append(f"Task: `{prompt.task_id}`")
        await session.channel.send(prompt.thread_id, "\n".join(lines)[:1900])

    async def _send_hitl_cancel_record(self, prompt: HitlPrompt) -> None:
        session = self._sessions.get(self._key(prompt.platform, prompt.channel_id))
        if session is None:
            return
        lines = [
            "**Input cancelled**",
            f"Prompt: `{prompt.id}`",
            f"Question: {prompt.question}",
        ]
        if prompt.task_id:
            lines.append(f"Task: `{prompt.task_id}`")
        await session.channel.send(prompt.thread_id, "\n".join(lines)[:1900])

    async def _answer_task_hitl_prompt(self, prompt: HitlPrompt) -> str:
        if not prompt.task_id:
            await self._store.update_hitl_prompt(prompt.id, status="failed", completed_at_now=True)
            return f"Interactive prompt `{prompt.id}` is missing a task id."
        task = await self._store.get_runtime_task(prompt.task_id)
        if task is None:
            await self._store.update_hitl_prompt(prompt.id, status="failed", completed_at_now=True)
            return f"Task `{prompt.task_id}` not found for prompt `{prompt.id}`."

        answer_block = self._build_hitl_answer_block(prompt)
        answer_payload = self._build_hitl_answer_payload(prompt)
        await self._store.update_runtime_task(
            task.id,
            status=TASK_STATUS_PENDING,
            blocked_reason=None,
            resume_instruction=answer_block,
            ended_at=None,
        )
        await self._store.add_runtime_event(
            task.id,
            "task.ask_user_answered",
            answer_payload,
        )
        self._task_sources[task.id] = "hitl_resume"
        await self._send_hitl_answer_record(prompt)
        await self._store.update_hitl_prompt(prompt.id, status="completed", completed_at_now=True)
        await self._resolve_notification("ask_user", task_id=task.id)
        updated = await self._store.get_runtime_task(task.id)
        if updated is not None:
            await self._signal_status_by_id(updated, TASK_STATUS_PENDING)
        return f"Interactive prompt `{prompt.id}` answered; task `{task.id}` re-queued."

    async def _answer_thread_hitl_prompt(self, prompt: HitlPrompt) -> str:
        session = self._sessions.get(self._key(prompt.platform, prompt.channel_id))
        registry = self._registries.get(self._key(prompt.platform, prompt.channel_id))
        if session is None or registry is None:
            await self._store.update_hitl_prompt(prompt.id, status="failed", completed_at_now=True)
            return f"Thread `{prompt.thread_id}` has no live session/registry to resume."

        await self._send_hitl_answer_record(prompt)
        answer_block = self._build_hitl_answer_block(prompt)
        answer_payload = self._build_hitl_answer_payload(prompt)
        await session.append_user(prompt.thread_id, answer_block, "HITL")

        agent = registry.get_agent(prompt.agent_name)
        if agent is None:
            await self._store.update_hitl_prompt(prompt.id, status="failed", completed_at_now=True)
            return f"Agent `{prompt.agent_name}` is not available for prompt `{prompt.id}`."

        await self._restore_thread_agent_session(
            session=session,
            thread_id=prompt.thread_id,
            agent=agent,
            fallback_session_id=prompt.session_id_snapshot,
        )
        skill_timeout_override = self._skill_timeout_seconds_by_name(
            str(prompt.resume_context.get("skill_name") or "") or None
        )
        skill_max_turns_override = self._skill_max_turns_by_name(
            str(prompt.resume_context.get("skill_name") or "") or None
        )
        response = await self._invoke_thread_agent(
            registry=registry,
            session=session,
            prompt=self._build_hitl_resume_prompt(prompt, include_original_request=True),
            thread_id=prompt.thread_id,
            force_agent=prompt.agent_name,
            log_path=self.chat_agent_log_base_path(
                thread_id=prompt.thread_id,
                request_id=prompt.id,
                purpose="hitl_resume",
            ),
            purpose="hitl_resume",
            skill_name=str(prompt.resume_context.get("skill_name") or "") or None,
            timeout_override_seconds=skill_timeout_override,
            max_turns_override=skill_max_turns_override,
        )
        if response.error and response.error_kind != "max_turns" and getattr(agent, "get_session_id", None):
            self._clear_thread_agent_session(agent, prompt.thread_id)
            response = await self._invoke_thread_agent(
                registry=registry,
                session=session,
                prompt=self._build_hitl_resume_prompt(prompt, include_original_request=True),
                thread_id=prompt.thread_id,
                force_agent=prompt.agent_name,
                log_path=self.chat_agent_log_base_path(
                    thread_id=prompt.thread_id,
                    request_id=f"{prompt.id}-fresh",
                    purpose="hitl_resume",
                ),
                purpose="hitl_resume_fresh",
                skill_name=str(prompt.resume_context.get("skill_name") or "") or None,
                timeout_override_seconds=skill_timeout_override,
                max_turns_override=skill_max_turns_override,
            )

        await self._sync_thread_agent_session(session=session, thread_id=prompt.thread_id, agent=agent)
        if response.error:
            await self._store.update_hitl_prompt(prompt.id, status="failed", completed_at_now=True)
            await session.channel.send(
                prompt.thread_id,
                self._format_agent_failure_text(
                    response,
                    prefix=f"Input recorded, but resuming `{prompt.agent_name}` failed.",
                ),
            )
            return f"Interactive prompt `{prompt.id}` failed to resume."

        envelope = None
        auth_challenge = None
        ask_user_challenge = None
        try:
            envelope = parse_control_envelope(response.text) if extract_control_frame(response.text) else None
            if envelope is not None:
                auth_challenge = parse_auth_challenge(envelope)
                ask_user_challenge = parse_ask_user_challenge(envelope)
        except ProtocolError as exc:
            logger.warning("HITL prompt=%s control frame parse failed during resume: %s", prompt.id, exc)
            envelope = None

        if envelope is not None and auth_challenge is not None:
            await self._send_auth_challenge_progress(
                session=session,
                thread_id=prompt.thread_id,
                agent_name=prompt.agent_name,
                text=response.text,
                provider=auth_challenge.provider,
                skill_name=str(prompt.resume_context.get("skill_name") or "") or None,
                original_user_content=str(prompt.resume_context.get("original_user_content") or "") or None,
                usage=response.usage,
            )
            await self._store.update_hitl_prompt(prompt.id, status="completed", completed_at_now=True)
            await self._resolve_notification("ask_user", thread_id=prompt.thread_id)
            return await self.mark_thread_auth_required(
                platform=prompt.platform,
                channel_id=prompt.channel_id,
                thread_id=prompt.thread_id,
                provider=auth_challenge.provider,
                reason=auth_challenge.reason,
                actor_id=prompt.created_by,
                agent_name=prompt.agent_name,
                control_envelope_json=envelope.raw_json,
                resume_context={
                    **(prompt.resume_context or {}),
                    "last_hitl_answer": answer_payload,
                },
                session_id_snapshot=agent.get_session_id(prompt.thread_id) if hasattr(agent, "get_session_id") else prompt.session_id_snapshot,
            )

        if envelope is not None and ask_user_challenge is not None:
            visible_text = self._clean_hitl_visible_text(strip_control_frame_text(response.text))
            if visible_text:
                await session.append_assistant(prompt.thread_id, visible_text, prompt.agent_name)
                await self._send_thread_agent_response(
                    session=session,
                    thread_id=prompt.thread_id,
                    agent_name=prompt.agent_name,
                    text=visible_text,
                    usage=response.usage,
                )
            await self._store.update_hitl_prompt(prompt.id, status="completed", completed_at_now=True)
            await self._resolve_notification("ask_user", thread_id=prompt.thread_id)
            return await self.mark_thread_ask_user_required(
                platform=prompt.platform,
                channel_id=prompt.channel_id,
                thread_id=prompt.thread_id,
                actor_id=prompt.created_by,
                agent_name=prompt.agent_name,
                question=ask_user_challenge.question,
                details=ask_user_challenge.details,
                choices=ask_user_challenge.choices,
                control_envelope_json=envelope.raw_json,
                resume_context={
                    **(prompt.resume_context or {}),
                    "last_hitl_answer": answer_payload,
                },
                session_id_snapshot=agent.get_session_id(prompt.thread_id) if hasattr(agent, "get_session_id") else prompt.session_id_snapshot,
            )

        if envelope is not None:
            await self._store.update_hitl_prompt(prompt.id, status="failed", completed_at_now=True)
            await session.channel.send(
                prompt.thread_id,
                "The resumed agent requested an unsupported interactive step. This challenge type is not implemented yet.",
            )
            return f"Interactive prompt `{prompt.id}` resumed into an unsupported challenge."

        await session.append_assistant(prompt.thread_id, response.text, prompt.agent_name)
        await self._send_thread_agent_response(
            session=session,
            thread_id=prompt.thread_id,
            agent_name=prompt.agent_name,
            text=response.text,
            usage=response.usage,
        )
        await self._store.update_hitl_prompt(prompt.id, status="completed", completed_at_now=True)
        await self._resolve_notification("ask_user", thread_id=prompt.thread_id)
        return f"Interactive prompt `{prompt.id}` answered and resumed successfully."

    @staticmethod
    def _build_auth_pause_message(
        *,
        raw_text: str,
        provider: str,
        skill_name: str | None,
        original_user_content: str | None,
    ) -> str:
        visible_text = strip_control_frame_text(raw_text)
        if visible_text:
            return visible_text

        wants_zh = bool(original_user_content and re.search(r"[\u4e00-\u9fff]", original_user_content))
        if wants_zh:
            if skill_name:
                return (
                    f"继续按 `{skill_name}` 流程提取内容时，发现继续执行前需要先完成 `{provider}` 登录。"
                    "我先暂停在这里，等你完成认证后自动继续。"
                )
            return (
                f"继续处理这条请求时，发现继续执行前需要先完成 `{provider}` 登录。"
                "我先暂停在这里，等你完成认证后自动继续。"
            )
        if skill_name:
            return (
                f"I continued via `{skill_name}`, but `{provider}` login is required before I can keep going. "
                "I am pausing here and will resume automatically after authentication."
            )
        return (
            f"I hit a `{provider}` login requirement while continuing this request. "
            "I am pausing here and will resume automatically after authentication."
        )

    def _is_authorized(self, actor_id: str) -> bool:
        if not self._owner_user_ids:
            return True
        return actor_id in self._owner_user_ids

    @staticmethod
    def _notification_dedupe_key(
        kind: str,
        *,
        thread_id: str | None = None,
        task_id: str | None = None,
    ) -> str:
        if task_id:
            suffix = "draft" if kind == "task_draft" else "waiting_merge" if kind == "task_waiting_merge" else kind
            return f"task:{task_id}:{suffix}"
        if not thread_id:
            raise ValueError("thread_id is required when task_id is not set")
        return f"thread:{thread_id}:{kind}"

    async def _emit_notification(self, event: NotificationEvent) -> None:
        await self._notifications.emit(event)

    async def _resolve_notification(
        self,
        kind: str,
        *,
        thread_id: str | None = None,
        task_id: str | None = None,
        status: str = "resolved",
    ) -> int:
        dedupe_key = self._notification_dedupe_key(kind, thread_id=thread_id, task_id=task_id)
        return await self._notifications.resolve(dedupe_key, status=status)

    async def _notify_thread_auth_required(
        self,
        *,
        platform: str,
        channel_id: str,
        thread_id: str,
        provider: str,
    ) -> None:
        await self._emit_notification(
            NotificationEvent(
                kind="auth_required",
                platform=platform,
                channel_id=channel_id,
                thread_id=thread_id,
                title="Action required",
                body=(
                    f"Provider: `{provider}`\n"
                    "Next step: complete login in this thread."
                ),
                dedupe_key=self._notification_dedupe_key("auth_required", thread_id=thread_id),
                payload={"provider": provider, "scope": "thread"},
            )
        )

    async def _notify_task_auth_required(self, task: RuntimeTask, *, provider: str) -> None:
        await self._emit_notification(
            NotificationEvent(
                kind="auth_required",
                platform=task.platform,
                channel_id=task.channel_id,
                thread_id=task.thread_id,
                task_id=task.id,
                title="Action required",
                body=(
                    f"Provider: `{provider}`\n"
                    "Next step: complete login in this thread."
                ),
                dedupe_key=self._notification_dedupe_key("auth_required", task_id=task.id),
                payload={"provider": provider, "scope": "task"},
            )
        )

    async def _notify_thread_ask_user_required(
        self,
        *,
        platform: str,
        channel_id: str,
        thread_id: str,
        question: str,
    ) -> None:
        await self._emit_notification(
            NotificationEvent(
                kind="ask_user",
                platform=platform,
                channel_id=channel_id,
                thread_id=thread_id,
                title="Action required",
                body=(
                    f"Question: {question}\n"
                    "Next step: answer the prompt in this thread."
                ),
                dedupe_key=self._notification_dedupe_key("ask_user", thread_id=thread_id),
                payload={"question": question, "scope": "thread"},
            )
        )

    async def _notify_task_ask_user_required(self, task: RuntimeTask, *, question: str) -> None:
        await self._emit_notification(
            NotificationEvent(
                kind="ask_user",
                platform=task.platform,
                channel_id=task.channel_id,
                thread_id=task.thread_id,
                task_id=task.id,
                title="Action required",
                body=(
                    f"Question: {question}\n"
                    "Next step: answer the prompt in this thread."
                ),
                dedupe_key=self._notification_dedupe_key("ask_user", task_id=task.id),
                payload={"question": question, "scope": "task"},
            )
        )

    async def _notify_task_draft_required(self, task: RuntimeTask, *, reasons: list[str] | None = None) -> None:
        reason_text = self._human_risk_reasons(reasons or [], task)
        await self._emit_notification(
            NotificationEvent(
                kind="task_draft",
                platform=task.platform,
                channel_id=task.channel_id,
                thread_id=task.thread_id,
                task_id=task.id,
                title="Action required",
                body="Next step: approve, reject, or suggest changes in this thread.",
                dedupe_key=self._notification_dedupe_key("task_draft", task_id=task.id),
                payload={"status": task.status, "reason_text": reason_text},
            )
        )

    async def _notify_task_waiting_merge_required(self, task: RuntimeTask) -> None:
        await self._emit_notification(
            NotificationEvent(
                kind="task_waiting_merge",
                platform=task.platform,
                channel_id=task.channel_id,
                thread_id=task.thread_id,
                task_id=task.id,
                title="Action required",
                body="Next step: merge, discard, or request changes in this thread.",
                dedupe_key=self._notification_dedupe_key("task_waiting_merge", task_id=task.id),
                payload={"status": task.status},
            )
        )

    def _tail_text(self, text: str) -> str:
        if not text:
            return ""
        text = text.strip()
        if len(text) <= self._log_tail_chars:
            return text
        return text[-self._log_tail_chars :]

    def _format_test_output(self, text: str) -> str:
        if not text:
            return ""
        summary = self._summarize_pytest_output(text)
        if summary:
            return summary[: self._log_tail_chars]
        return self._tail_text(text)

    def _summarize_pytest_output(self, text: str) -> str:
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        if not lines:
            return ""

        summary_line = ""
        summary_re = re.compile(r"\b\d+\s+passed\b|\b\d+\s+failed\b|\b\d+\s+error\b|\b\d+\s+errors\b|\b\d+\s+skipped\b")
        for line in reversed(lines):
            cleaned = line.strip().strip("=").strip()
            if summary_re.search(cleaned) and " in " in cleaned:
                summary_line = cleaned
                break

        failure_lines: list[str] = []
        seen: set[str] = set()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("FAILED ", "ERROR ")):
                if stripped not in seen:
                    seen.add(stripped)
                    failure_lines.append(stripped)
            elif re.match(r"^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Failure):", stripped):
                if stripped not in seen:
                    seen.add(stripped)
                    failure_lines.append(stripped)

        if summary_line and not failure_lines:
            return summary_line

        parts: list[str] = []
        if summary_line:
            parts.append(f"Summary: {summary_line}")
        parts.extend(failure_lines[:4])
        if parts:
            return "\n".join(parts)
        return ""

    @staticmethod
    def _should_ignore_agent_block(agent_text: str, block_reason: str | None) -> bool:
        reason = (block_reason or "").strip()
        hay = reason.lower() if reason else (agent_text or "").lower()
        if not hay:
            return False

        positive_hints = (
            "sandbox",
            "socket-bind",
            "127.0.0.1",
            "permissionerror",
            "operation not permitted",
            "environment-specific",
        )
        negative_hints = (
            "missing content",
            "missing context",
            "missing dependency",
            "missing file",
            "missing api key",
            "missing credential",
            "need user input",
        )
        return any(hint in hay for hint in positive_hints) and not any(hint in hay for hint in negative_hints)

    @staticmethod
    def _format_status_message(text: str) -> str:
        return f"{_STATUS_MESSAGE_PREFIX}\n{text}"

    @staticmethod
    def _format_terminal_message(text: str) -> str:
        return f"{_TERMINAL_MESSAGE_PREFIX}\n{text}"

    @staticmethod
    def _summarize_event_payload(payload: dict[str, Any]) -> str:
        if not payload:
            return ""
        interesting = []
        for key in (
            "phase",
            "step",
            "agent",
            "elapsed_seconds",
            "test_exit_code",
            "timeout_seconds",
            "command",
            "status",
            "error",
        ):
            if key in payload and payload[key] not in (None, ""):
                interesting.append(f"{key}={payload[key]}")
        return ", ".join(interesting)[:220]

    @staticmethod
    def _human_risk_reasons(reasons: list[str], task: RuntimeTask) -> str:
        if not reasons:
            return "requires explicit approval"
        labels = []
        for r in reasons:
            if r == "minutes_over_20":
                labels.append(f"estimated runtime {task.max_minutes} min exceeds 20 min threshold")
            elif r == "steps_over_8":
                labels.append(f"step budget {task.max_steps} exceeds 8-step threshold")
            elif r == "contains_sensitive_keywords":
                labels.append("prompt contains sensitive keywords (network/deploy/database etc.)")
            elif r == "possible_large_change":
                labels.append("possible large-scope change")
            else:
                labels.append(r)
        return " · ".join(labels)

    def _draft_text(self, task: RuntimeTask, *, reasons: list[str]) -> str:
        reason_text = self._human_risk_reasons(reasons, task)
        return (
            f"### Runtime Task Draft `{task.id}`\n"
            f"Task type: `{task.task_type}` · completion: `{task.completion_mode}`\n"
            f"Goal: {task.goal}\n"
            f"Agent: `{task.preferred_agent or self._default_agent}`\n"
            f"Budget: {task.max_steps} steps / {task.max_minutes} min\n"
            f"Test command: `{task.test_command}`\n"
            f"⚠️ Reason: {reason_text}\n"
            "Use Approve / Reject / Suggest."
        )

    async def _merge_gate_text(self, task: RuntimeTask) -> str:
        lines = [
            f"### Runtime Task `{task.id}` Ready to Merge",
            f"Task type: `{task.task_type}`",
            f"Goal: {task.goal[:220]}",
            f"Agent: `{task.preferred_agent or self._default_agent}`",
            f"Completed step: {task.step_no}/{task.max_steps}",
            f"Test command: `{task.test_command}`",
        ]

        if task.merge_error:
            lines.append("")
            lines.append("Last merge attempt:")
            lines.append(f"```text\n{task.merge_error[:400]}\n```")

        changes = await self._collect_task_changes(task, limit=10)
        if changes:
            lines.append("")
            lines.append("Changed files:")
            lines.extend(f"- `{line}`" for line in changes[:8])
            if len(changes) > 8:
                lines.append(f"- ... and {len(changes) - 8} more")

        ckpt = await self._store.get_last_runtime_checkpoint(task.id)
        if ckpt:
            test_tail = self._format_test_output(str(ckpt.get("test_result", "")))
            if test_tail:
                lines.append("")
                lines.append("Latest test result:")
                lines.append(f"```text\n{test_tail[:500]}\n```")

        if task.task_type == TASK_TYPE_SKILL_CHANGE and task.skill_name and hasattr(self._store, "get_latest_skill_evaluations"):
            evals = await self._store.get_latest_skill_evaluations(task.skill_name)
            if evals:
                lines.append("")
                lines.append("Skill evaluation findings:")
                for item in evals:
                    lines.append(f"- `{item['evaluation_type']}` [{item['status']}] {item['summary']}")

        lines.extend(
            [
                "",
                "Choose one action:",
                "- Merge: apply patch to current branch and auto commit" + (" (retry)" if task.merge_error else ""),
                "- Discard: keep audit metadata, drop this task result",
                "- Request Changes: send task back to BLOCKED for another iteration",
                "- Wait: keep the task pending and retry later",
                "",
                "Use `/task_changes` or `/task_logs` for full details, if available. You can also reply `retry merge`, `wait`, or `discard` in-thread.",
            ]
        )
        return "\n".join(lines)[:1900]

    async def _collect_task_changes(self, task: RuntimeTask, *, limit: int = 80) -> list[str]:
        changes: list[str] = []
        if task.workspace_path:
            workspace = Path(task.workspace_path)
            if workspace.exists():
                try:
                    changes = await self._worktree.list_workspace_changes(workspace, limit=limit)
                except Exception as exc:
                    logger.warning("Failed to list workspace changes for %s: %s", task.id, exc)

        if changes:
            return changes[:limit]

        ckpt = await self._store.get_last_runtime_checkpoint(task.id)
        raw = ckpt.get("files_changed_json") if ckpt else None
        if not raw:
            return []
        try:
            files = json.loads(raw)
        except Exception:
            return []
        return [f"M\t{p}" for p in files][:limit]

    @staticmethod
    def _goal_short(goal: str) -> str:
        one_line = " ".join(goal.strip().split())
        return one_line[:72] if one_line else "task"

    @staticmethod
    def _emoji_for_status(status: str) -> str | None:
        if status == TASK_STATUS_RUNNING:
            return "👀"
        if status == TASK_STATUS_VALIDATING:
            return "🧪"
        if status in {TASK_STATUS_DRAFT, TASK_STATUS_PENDING, TASK_STATUS_WAITING_MERGE}:
            return "⏳"
        if status == TASK_STATUS_WAITING_USER_INPUT:
            return "🔐"
        if status in {TASK_STATUS_MERGED, TASK_STATUS_APPLIED, TASK_STATUS_COMPLETED}:
            return "✅"
        if status == TASK_STATUS_DISCARDED:
            return "🗑️"
        if status == TASK_STATUS_PAUSED:
            return "⏸️"
        if status in {
            TASK_STATUS_BLOCKED,
            TASK_STATUS_FAILED,
            TASK_STATUS_TIMEOUT,
            TASK_STATUS_STOPPED,
            TASK_STATUS_REJECTED,
            TASK_STATUS_MERGE_FAILED,
        }:
            return "⚠️"
        return None

    @staticmethod
    def _parse_control_intent(text: str, task: RuntimeTask | None = None) -> tuple[str, str] | None:
        """Return (action, instruction) if text is a runtime control command, else None."""
        stripped = text.strip()
        lower = stripped.lower()
        if lower in {"stop", "stop the task", "cancel"}:
            return ("stop", "")
        if lower in {"pause", "pause the task"}:
            return ("pause", "")
        for prefix in ("resume ", "continue "):
            if lower.startswith(prefix):
                return ("resume", stripped[len(prefix):].strip())

        if task and task.status in {TASK_STATUS_WAITING_MERGE, TASK_STATUS_APPLIED, TASK_STATUS_MERGE_FAILED}:
            retry_hints = (
                "retry merge",
                "merge again",
                "remerge",
                "retry the merge",
                "重新merge",
                "重新 merge",
                "重新合并",
                "重试merge",
                "重试合并",
                "再merge",
                "再试一次merge",
                "再试一次合并",
                "能重新merge吗",
                "能重新合并吗",
                "清理好了，重新merge",
                "清理好了，重新合并",
            )
            if any(hint in lower for hint in retry_hints) or any(hint in stripped for hint in ("重新合并", "重试合并", "再试一次合并", "能重新合并吗", "清理好了，重新合并")):
                return ("retry_merge", "")

            wait_hints = {"wait", "hold", "wait for now", "later"}
            if lower in wait_hints or any(hint in stripped for hint in ("先等等", "先等", "等待", "先放着", "先放一放")):
                return ("wait", "")

            discard_hints = (
                "discard",
                "drop it",
                "end this task",
                "cancel this task",
                "give up",
            )
            if lower in discard_hints or any(hint in stripped for hint in ("结束这个任务", "直接结束", "放弃这个任务", "放弃吧", "算了")):
                return ("discard", "")
        return None

    @staticmethod
    def _is_auth_retry_intent(text: str) -> bool:
        lowered = text.strip().lower()
        if lowered in {"retry login", "retry auth"}:
            return True
        stripped = text.strip()
        return any(hint in stripped for hint in ("重新登录", "重新扫码"))

    async def _active_task_for_thread(
        self, platform: str, channel_id: str, thread_id: str
    ) -> "RuntimeTask | None":
        """Return the most recent active task in the given thread, or None."""
        active_statuses = {
            TASK_STATUS_RUNNING,
            TASK_STATUS_VALIDATING,
            TASK_STATUS_BLOCKED,
            TASK_STATUS_WAITING_USER_INPUT,
            TASK_STATUS_PAUSED,
            TASK_STATUS_PENDING,
            TASK_STATUS_WAITING_MERGE,
            TASK_STATUS_APPLIED,
            TASK_STATUS_MERGE_FAILED,
        }
        tasks = await self._store.list_runtime_tasks(
            platform=platform,
            channel_id=channel_id,
            limit=20,
        )
        for task in tasks:
            if task.thread_id == thread_id and task.status in active_statuses:
                return task
        return None

    async def _send_auth_prompt(self, channel, flow: AuthFlow) -> None:
        intro_msg_id = await channel.send(
            flow.thread_id,
            (
                f"**Auth Login Required**\n"
                f"Provider: `{flow.provider}`\n"
                f"Flow: `{flow.id}`\n"
                "Open the Bilibili app and scan the QR code. It is valid for about 3 minutes."
            ),
        )
        logger.info(
            "Auth prompt intro sent flow=%s provider=%s thread=%s message_id=%s",
            flow.id,
            flow.provider,
            flow.thread_id,
            intro_msg_id,
        )
        if not flow.qr_image_path:
            return
        from oh_my_agent.gateway.base import OutgoingAttachment

        qr_msg_id = await channel.send_attachment(
            flow.thread_id,
            attachment=OutgoingAttachment(
                filename=Path(flow.qr_image_path).name,
                content_type="image/png",
                local_path=Path(flow.qr_image_path),
                caption="Bilibili login QR code",
            ),
        )
        logger.info(
            "Auth prompt QR sent flow=%s provider=%s thread=%s message_id=%s path=%s",
            flow.id,
            flow.provider,
            flow.thread_id,
            qr_msg_id,
            flow.qr_image_path,
        )

    async def _on_auth_flow_event(
        self,
        event_type: str,
        flow: AuthFlow,
        credential: CredentialHandle | None,
        message: str | None,
    ) -> None:
        logger.info(
            "AUTH_FLOW_EVENT event=%s flow=%s provider=%s platform=%s channel=%s thread=%s linked_task=%s has_credential=%s",
            event_type,
            flow.id,
            flow.provider,
            flow.platform,
            flow.channel_id,
            flow.thread_id,
            flow.linked_task_id,
            credential is not None,
        )
        session = self._sessions.get(self._key(flow.platform, flow.channel_id))
        if session is not None:
            if event_type == "approved":
                await session.channel.send(
                    flow.thread_id,
                    "Login confirmed. Continuing the linked task if one is waiting.",
                )
            elif event_type == "expired":
                await session.channel.send(
                    flow.thread_id,
                    "QR code expired. Reply `retry login` or run `/auth_login bilibili` to generate a new one.",
                )
            elif event_type == "failed":
                await session.channel.send(
                    flow.thread_id,
                    f"Auth flow failed: {(message or 'unknown error')[:300]}",
                )

        if not flow.linked_task_id:
            suspended = await self._store.get_active_suspended_agent_run(
                platform=flow.platform,
                channel_id=flow.channel_id,
                thread_id=flow.thread_id,
                provider=flow.provider,
            )
            if suspended is None:
                if event_type in {"approved", "cancelled"}:
                    await self._resolve_notification("auth_required", thread_id=flow.thread_id, status="cancelled" if event_type == "cancelled" else "resolved")
                return
            if event_type == "approved":
                updated_context = dict(suspended.resume_context or {})
                if credential and credential.storage_path:
                    updated_context["auth_credential_path"] = credential.storage_path
                    logger.info(
                        "AUTH_FLOW_EVENT credential injected into suspended run run=%s provider=%s path=%s",
                        suspended.id,
                        flow.provider,
                        credential.storage_path,
                    )
                await self._store.update_suspended_agent_run(
                    suspended.id,
                    resume_context_json=updated_context,
                )
                await self._resolve_notification("auth_required", thread_id=flow.thread_id)
                asyncio.create_task(self.resume_suspended_agent_run(suspended.id))
            elif event_type == "cancelled":
                await self._store.update_suspended_agent_run(
                    suspended.id,
                    status="cancelled",
                    resume_context_json={
                        **suspended.resume_context,
                        "auth_error": message or f"{flow.provider} login was cancelled.",
                    },
                    completed_at_now=True,
                )
                await self._resolve_notification("auth_required", thread_id=flow.thread_id, status="cancelled")
            elif event_type in {"expired", "failed"}:
                await self._store.update_suspended_agent_run(
                    suspended.id,
                    status="waiting_auth",
                    resume_context_json={
                        **suspended.resume_context,
                        "auth_error": message or f"{flow.provider} login did not complete.",
                    },
                )
            return
        task = await self._store.get_runtime_task(flow.linked_task_id)
        if task is None:
            if event_type in {"approved", "cancelled"}:
                await self._resolve_notification("auth_required", task_id=flow.linked_task_id, status="cancelled" if event_type == "cancelled" else "resolved")
            return
        if event_type == "approved":
            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_PENDING,
                blocked_reason=None,
                resume_instruction="Auth flow completed.",
                error=None,
                ended_at=None,
            )
            await self._store.add_runtime_event(
                task.id,
                "task.auth_completed",
                {"provider": flow.provider, "flow_id": flow.id},
            )
            updated = await self._store.get_runtime_task(task.id)
            if updated is not None:
                await self._signal_status_by_id(updated, TASK_STATUS_PENDING)
            await self._resolve_notification("auth_required", task_id=task.id)
        elif event_type == "cancelled":
            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_BLOCKED,
                blocked_reason=message or f"{flow.provider} login was cancelled.",
                ended_at=None,
            )
            await self._resolve_notification("auth_required", task_id=task.id, status="cancelled")
        elif event_type in {"expired", "failed"}:
            await self._store.update_runtime_task(
                task.id,
                status=TASK_STATUS_WAITING_USER_INPUT,
                blocked_reason=message or f"{flow.provider} login did not complete.",
                ended_at=None,
            )

    @staticmethod
    def _build_completion_summary(
        task: RuntimeTask,
        step: int,
        changed_files: list[str],
        test_summary: str,
        total_agent_s: float,
        total_test_s: float,
        total_elapsed_s: float,
        waiting_merge: bool,
    ) -> str:
        parts: list[str] = []
        goal_short = " ".join(task.goal.strip().split())[:120]
        parts.append(f"Goal: {goal_short}")
        parts.append(f"Completed in {step} step(s)")

        # Changed files
        if changed_files:
            shown = changed_files[:10]
            parts.append(f"Changed files ({len(changed_files)}): " + ", ".join(f"`{f}`" for f in shown))
            if len(changed_files) > 10:
                parts[-1] += f" and {len(changed_files) - 10} more"

        # Test result excerpt
        if test_summary:
            summary_re = re.compile(
                r"\b\d+\s+passed\b|\b\d+\s+failed\b|\b\d+\s+error\b|\b\d+\s+errors\b|\b\d+\s+skipped\b"
            )
            for line in reversed(test_summary.splitlines()):
                cleaned = line.strip().strip("=").strip()
                if summary_re.search(cleaned) and " in " in cleaned:
                    parts.append(f"Tests: {cleaned}")
                    break

        # Latency metrics
        parts.append(
            f"Timing: agent {total_agent_s:.1f}s | tests {total_test_s:.1f}s | total {total_elapsed_s:.1f}s"
        )

        if waiting_merge:
            parts.append("Waiting merge confirmation.")
        return " | ".join(parts)
