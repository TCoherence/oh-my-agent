"""Single-tier judge-driven memory store.

Replaces the daily/curated tier system with a flat ``memories.yaml`` file plus a
natural-language ``MEMORY.md`` synthesized from active entries.

The store does not run extraction itself — it only applies actions emitted by
:class:`oh_my_agent.memory.judge.Judge`. Promotion / dedup decisions live in the
Judge prompt, not in the store.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


VALID_CATEGORIES = frozenset({"preference", "project_knowledge", "workflow", "fact"})
VALID_SCOPES = frozenset({"global_user", "workspace", "skill", "thread"})
VALID_STATUS = frozenset({"active", "superseded"})

_SCOPE_PRIORITY = {"thread": 0, "skill": 1, "workspace": 2, "global_user": 3}
_RETRIEVAL_SCOPE_BONUS = {"thread": 1.30, "skill": 1.20, "workspace": 1.10, "global_user": 1.00}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class EvidenceRecord:
    thread_id: str = ""
    ts: str = field(default_factory=_now_iso)
    snippet: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceRecord":
        return cls(
            thread_id=str(data.get("thread_id", "")),
            ts=str(data.get("ts") or _now_iso()),
            snippet=str(data.get("snippet", ""))[:280],
        )


@dataclass
class MemoryEntry:
    id: str = field(default_factory=_new_id)
    summary: str = ""
    category: str = "fact"  # preference | workflow | project_knowledge | fact
    scope: str = "global_user"  # global_user | workspace | skill | thread
    confidence: float = 0.7
    observation_count: int = 1
    evidence_log: list[EvidenceRecord] = field(default_factory=list)
    source_skills: list[str] = field(default_factory=list)
    source_workspace: str = ""
    status: str = "active"  # active | superseded
    superseded_by: str | None = None
    created_at: str = field(default_factory=_now_iso)
    last_observed_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryEntry":
        evidence_raw = data.get("evidence_log") or []
        evidence_log: list[EvidenceRecord] = []
        if isinstance(evidence_raw, list):
            for item in evidence_raw:
                if isinstance(item, dict):
                    evidence_log.append(EvidenceRecord.from_dict(item))
        category = str(data.get("category", "fact"))
        if category not in VALID_CATEGORIES:
            category = "fact"
        scope = str(data.get("scope", "global_user"))
        if scope not in VALID_SCOPES:
            scope = "global_user"
        status = str(data.get("status", "active"))
        if status not in VALID_STATUS:
            status = "active"
        try:
            confidence = float(data.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        confidence = max(0.0, min(1.0, confidence))
        try:
            observation_count = int(data.get("observation_count", 1))
        except (TypeError, ValueError):
            observation_count = 1
        observation_count = max(1, observation_count)
        source_skills = data.get("source_skills") or []
        if not isinstance(source_skills, list):
            source_skills = []
        source_skills = [str(s) for s in source_skills if s]
        return cls(
            id=str(data.get("id") or _new_id()),
            summary=str(data.get("summary", "")).strip(),
            category=category,
            scope=scope,
            confidence=confidence,
            observation_count=observation_count,
            evidence_log=evidence_log,
            source_skills=source_skills,
            source_workspace=str(data.get("source_workspace", "")),
            status=status,
            superseded_by=(str(data["superseded_by"]) if data.get("superseded_by") else None),
            created_at=str(data.get("created_at") or _now_iso()),
            last_observed_at=str(data.get("last_observed_at") or _now_iso()),
        )


_SYNTHESIS_PROMPT = """\
You are a memory synthesis system. Below are structured memory entries about a user, \
organized by category. Synthesize them into a concise, natural-language memory document \
that an AI assistant can reference to personalize future interactions.

Rules:
- Write in second person ("You prefer…", "Your project uses…").
- Group by category with markdown headers.
- Be concise — each memory should be one sentence or phrase.
- Do NOT include IDs, confidence scores, or metadata.
- If a category has no entries, skip it entirely.
- Output ONLY the markdown document. No preamble.

Structured memories:
{entries_text}
"""


class JudgeStore:
    """Flat YAML-backed memory store driven by Judge actions.

    File layout under ``memory_dir``::

        memory_dir/
            memories.yaml     # all entries (active + superseded)
            MEMORY.md         # synthesized natural-language view
    """

    def __init__(
        self,
        memory_dir: str | Path,
        *,
        synthesize_after_seconds: int = 6 * 3600,
        max_evidence_per_entry: int = 8,
    ) -> None:
        self._memory_dir = Path(memory_dir).expanduser().resolve()
        self._entries_path = self._memory_dir / "memories.yaml"
        self._memory_md_path = self._memory_dir / "MEMORY.md"
        self._memories: list[MemoryEntry] = []
        self._lock = asyncio.Lock()
        self._dirty = False
        self._synthesize_after_seconds = synthesize_after_seconds
        self._max_evidence_per_entry = max_evidence_per_entry

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def memory_dir(self) -> Path:
        return self._memory_dir

    @property
    def memory_md_path(self) -> Path:
        return self._memory_md_path

    @property
    def all_entries(self) -> list[MemoryEntry]:
        return list(self._memories)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def load(self) -> None:
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        if not self._entries_path.exists():
            self._memories = []
            return
        try:
            raw = self._entries_path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
        except Exception as exc:
            logger.warning("Failed to read %s: %s", self._entries_path, exc)
            self._memories = []
            return
        if not isinstance(data, list):
            self._memories = []
            return
        self._memories = [
            MemoryEntry.from_dict(item) for item in data if isinstance(item, dict)
        ]

    async def save(self) -> None:
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._entries_path.with_suffix(".tmp")
        try:
            payload = [entry.to_dict() for entry in self._memories]
            tmp.write_text(
                yaml.dump(payload, allow_unicode=True, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
            os.rename(str(tmp), str(self._entries_path))
        except Exception as exc:
            logger.warning("Failed to save %s: %s", self._entries_path, exc)
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def get_active(self) -> list[MemoryEntry]:
        return [m for m in self._memories if m.status == "active"]

    def get_by_id(self, memory_id: str) -> MemoryEntry | None:
        for m in self._memories:
            if m.id == memory_id:
                return m
        return None

    def get_relevant(
        self,
        *,
        skill_name: str | None = None,
        thread_id: str | None = None,
        workspace: str | None = None,
        limit: int = 12,
    ) -> list[MemoryEntry]:
        scored: list[tuple[float, MemoryEntry]] = []
        for entry in self.get_active():
            if entry.scope == "thread" and (not thread_id or thread_id not in [e.thread_id for e in entry.evidence_log]):
                continue
            if entry.scope == "skill" and (not skill_name or skill_name not in entry.source_skills):
                continue
            if entry.scope == "workspace" and (not workspace or entry.source_workspace != workspace):
                continue
            base = entry.confidence
            scope_bonus = _RETRIEVAL_SCOPE_BONUS.get(entry.scope, 1.0)
            obs_bonus = 1.0 + min(entry.observation_count - 1, 4) * 0.05
            scored.append((base * scope_bonus * obs_bonus, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:limit]]

    # ------------------------------------------------------------------
    # Action application (called by Judge)
    # ------------------------------------------------------------------

    async def apply_actions(
        self,
        actions: list[dict[str, Any]],
        *,
        thread_id: str | None = None,
        skill_name: str | None = None,
        source_workspace: str | None = None,
    ) -> dict[str, int]:
        stats = {"add": 0, "strengthen": 0, "supersede": 0, "no_op": 0, "rejected": 0}
        if not actions:
            return stats
        async with self._lock:
            for action in actions:
                if not isinstance(action, dict):
                    stats["rejected"] += 1
                    continue
                op = str(action.get("op", "")).lower().strip()
                try:
                    if op == "add":
                        if self._apply_add(action, thread_id, skill_name, source_workspace):
                            stats["add"] += 1
                            self._dirty = True
                        else:
                            stats["rejected"] += 1
                    elif op == "strengthen":
                        if self._apply_strengthen(action, thread_id):
                            stats["strengthen"] += 1
                            self._dirty = True
                        else:
                            stats["rejected"] += 1
                    elif op == "supersede":
                        if self._apply_supersede(action, thread_id, skill_name, source_workspace):
                            stats["supersede"] += 1
                            self._dirty = True
                        else:
                            stats["rejected"] += 1
                    elif op == "no_op":
                        stats["no_op"] += 1
                    else:
                        stats["rejected"] += 1
                except Exception as exc:
                    logger.warning("apply_action failed for %r: %s", action, exc)
                    stats["rejected"] += 1
            if self._dirty:
                await self.save()
        return stats

    async def manual_supersede(self, memory_id: str) -> bool:
        """Mark an entry as superseded with no replacement (used by /forget)."""
        async with self._lock:
            entry = self.get_by_id(memory_id)
            if entry is None or entry.status != "active":
                return False
            entry.status = "superseded"
            entry.superseded_by = None
            entry.last_observed_at = _now_iso()
            self._dirty = True
            await self.save()
            return True

    # ------------------------------------------------------------------
    # Synthesis (MEMORY.md)
    # ------------------------------------------------------------------

    def should_synthesize(self) -> bool:
        if self._dirty:
            return True
        if not self._memory_md_path.exists():
            return bool(self.get_active())
        try:
            mtime = self._memory_md_path.stat().st_mtime
        except OSError:
            return True
        return (time.time() - mtime) > self._synthesize_after_seconds

    def clear_synthesis_flag(self) -> None:
        self._dirty = False

    async def synthesize_memory_md(self, registry) -> bool:
        active = self.get_active()
        if not active:
            try:
                if self._memory_md_path.exists():
                    self._memory_md_path.unlink()
            except OSError:
                pass
            self.clear_synthesis_flag()
            return True

        by_cat: dict[str, list[str]] = {}
        for m in active:
            by_cat.setdefault(m.category, []).append(m.summary)

        lines: list[str] = []
        for cat in ("preference", "workflow", "project_knowledge", "fact"):
            if cat not in by_cat:
                continue
            lines.append(f"\n## {cat}")
            for s in by_cat[cat]:
                lines.append(f"- {s}")

        if not lines:
            self.clear_synthesis_flag()
            return True

        prompt = _SYNTHESIS_PROMPT.format(entries_text="\n".join(lines))
        try:
            _agent, response = await registry.run(prompt, run_label="memory_md_synthesis")
        except Exception as exc:
            logger.warning("memory_md synthesis failed: %s", exc)
            return False
        if response.error:
            logger.warning("memory_md synthesis returned error: %s", response.error)
            return False
        try:
            self._memory_md_path.write_text(response.text.strip() + "\n", encoding="utf-8")
            self.clear_synthesis_flag()
            logger.info("MEMORY.md synthesized (%d chars)", len(response.text))
            return True
        except Exception as exc:
            logger.warning("Failed to write MEMORY.md: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internals — action handlers
    # ------------------------------------------------------------------

    def _apply_add(
        self,
        action: dict[str, Any],
        thread_id: str | None,
        skill_name: str | None,
        source_workspace: str | None,
    ) -> bool:
        summary = str(action.get("summary", "")).strip()
        if not summary:
            return False
        category = str(action.get("category", "fact"))
        if category not in VALID_CATEGORIES:
            category = "fact"
        scope = str(action.get("scope", "global_user"))
        if scope not in VALID_SCOPES:
            scope = "global_user"
        try:
            confidence = float(action.get("confidence", 0.75))
        except (TypeError, ValueError):
            confidence = 0.75
        confidence = max(0.0, min(1.0, confidence))
        evidence_snippet = str(action.get("evidence", ""))[:280]
        evidence_log: list[EvidenceRecord] = []
        if evidence_snippet:
            evidence_log.append(
                EvidenceRecord(thread_id=thread_id or "", ts=_now_iso(), snippet=evidence_snippet)
            )
        source_skills = [skill_name] if skill_name else []
        entry = MemoryEntry(
            summary=summary,
            category=category,
            scope=scope,
            confidence=confidence,
            observation_count=1,
            evidence_log=evidence_log,
            source_skills=source_skills,
            source_workspace=source_workspace or "",
        )
        self._memories.append(entry)
        return True

    def _apply_strengthen(self, action: dict[str, Any], thread_id: str | None) -> bool:
        memory_id = str(action.get("id", "")).strip()
        if not memory_id:
            return False
        entry = self.get_by_id(memory_id)
        if entry is None or entry.status != "active":
            return False
        entry.observation_count += 1
        entry.last_observed_at = _now_iso()
        bump = float(action.get("confidence_bump", 0.05))
        bump = max(0.0, min(0.20, bump))
        entry.confidence = min(1.0, entry.confidence + bump)
        evidence_snippet = str(action.get("evidence", ""))[:280]
        if evidence_snippet:
            entry.evidence_log.append(
                EvidenceRecord(thread_id=thread_id or "", ts=_now_iso(), snippet=evidence_snippet)
            )
            if len(entry.evidence_log) > self._max_evidence_per_entry:
                entry.evidence_log = entry.evidence_log[-self._max_evidence_per_entry :]
        return True

    def _apply_supersede(
        self,
        action: dict[str, Any],
        thread_id: str | None,
        skill_name: str | None,
        source_workspace: str | None,
    ) -> bool:
        old_id = str(action.get("old_id", "")).strip()
        new_summary = str(action.get("new_summary", "")).strip()
        if not old_id or not new_summary:
            return False
        old_entry = self.get_by_id(old_id)
        if old_entry is None or old_entry.status != "active":
            return False

        category = str(action.get("category", old_entry.category))
        if category not in VALID_CATEGORIES:
            category = "fact"
        scope = str(action.get("scope", old_entry.scope))
        if scope not in VALID_SCOPES:
            scope = "global_user"
        try:
            confidence = float(action.get("confidence", max(old_entry.confidence, 0.8)))
        except (TypeError, ValueError):
            confidence = max(old_entry.confidence, 0.8)
        confidence = max(0.0, min(1.0, confidence))
        evidence_snippet = str(action.get("evidence", ""))[:280]
        evidence_log: list[EvidenceRecord] = []
        if evidence_snippet:
            evidence_log.append(
                EvidenceRecord(thread_id=thread_id or "", ts=_now_iso(), snippet=evidence_snippet)
            )
        source_skills = list(old_entry.source_skills)
        if skill_name and skill_name not in source_skills:
            source_skills.append(skill_name)
        new_entry = MemoryEntry(
            summary=new_summary,
            category=category,
            scope=scope,
            confidence=confidence,
            observation_count=max(1, old_entry.observation_count),
            evidence_log=evidence_log,
            source_skills=source_skills,
            source_workspace=source_workspace or old_entry.source_workspace,
        )
        self._memories.append(new_entry)
        old_entry.status = "superseded"
        old_entry.superseded_by = new_entry.id
        old_entry.last_observed_at = _now_iso()
        return True

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def to_judge_context(self, *, max_active: int = 60) -> list[dict[str, Any]]:
        """Compact representation of current active memories for Judge prompt."""
        active = sorted(self.get_active(), key=lambda m: m.last_observed_at, reverse=True)
        if len(active) > max_active:
            active = active[:max_active]
        return [
            {
                "id": m.id,
                "summary": m.summary,
                "category": m.category,
                "scope": m.scope,
                "confidence": round(m.confidence, 2),
                "observation_count": m.observation_count,
            }
            for m in active
        ]

    def stats(self) -> dict[str, int]:
        active = self.get_active()
        return {
            "total": len(self._memories),
            "active": len(active),
            "superseded": len(self._memories) - len(active),
        }


def parse_judge_actions(raw_text: str) -> list[dict[str, Any]]:
    """Parse a Judge agent response into a list of action dicts.

    Tolerates surrounding prose / fenced code blocks. Returns ``[]`` on parse
    failure (callers log + treat as no-op).
    """
    if not raw_text:
        return []
    text = raw_text.strip()
    # Strip fenced code blocks
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl > 0:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
    # Try direct JSON parse
    candidates: list[str] = [text]
    # Try to slice {...}
    start = text.find("{")
    if start > 0:
        candidates.append(text[start:])
    # Try to slice [...]
    bracket = text.find("[")
    if bracket > 0:
        candidates.append(text[bracket:])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("actions"), list):
            return [a for a in data["actions"] if isinstance(a, dict)]
        if isinstance(data, list):
            return [a for a in data if isinstance(a, dict)]
    return []
