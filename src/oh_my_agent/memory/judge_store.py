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
import shutil
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

try:  # C-accelerated yaml (libyaml) when available
    from yaml import CSafeDumper as _YamlDumper
    from yaml import CSafeLoader as _YamlLoader
except ImportError:  # pragma: no cover — depends on how PyYAML was built
    from yaml import SafeDumper as _YamlDumper  # type: ignore[assignment]
    from yaml import SafeLoader as _YamlLoader  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class MemoryStoreLoadError(Exception):
    """Raised when ``memories.yaml`` top-level structure is unrecoverable.

    Used to flip the store into read-only mode so a later ``save()`` does not
    overwrite a possibly-recoverable file with empty state.
    """


VALID_CATEGORIES = frozenset(
    {"preference", "project_knowledge", "workflow", "fact", "self_eval"}
)
VALID_SCOPES = frozenset(
    {"global_user", "workspace", "skill", "thread", "automation"}
)
VALID_STATUS = frozenset({"active", "superseded"})
# Load-time compaction horizon: superseded entries and self_eval entries whose
# last observation is older than this are dropped at load() so the YAML file
# does not grow monotonically (every save rewrites the full file).
_COMPACTION_MAX_AGE_DAYS = 90
VALID_FEEDBACK_SOURCES = frozenset({"llm_judge", "implicit", "explicit"})
VALID_QUALITIES = frozenset({"pass", "borderline", "fail"})
# M1 PR4 tie-break: when two signals share confidence, prefer the more
# deliberate source. explicit (user typed) > implicit (user reacted) >
# llm_judge (automated guess).
_SIGNAL_SOURCE_RANK = {"explicit": 3, "implicit": 2, "llm_judge": 1}

_SCOPE_PRIORITY = {"thread": 0, "skill": 1, "workspace": 2, "global_user": 3, "automation": 0}
_RETRIEVAL_SCOPE_BONUS = {
    "thread": 1.30,
    "skill": 1.20,
    "workspace": 1.10,
    "global_user": 1.00,
    "automation": 1.40,  # automation self-eval is highest-priority for that automation
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_ts(value: str) -> datetime | None:
    """Best-effort ISO timestamp → aware datetime (None on parse failure)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=False)

    thread_id: str = ""
    ts: str = Field(default_factory=_now_iso)
    snippet: str = ""

    @field_validator("thread_id", "ts", mode="before")
    @classmethod
    def _coerce_str(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)

    @field_validator("ts", mode="after")
    @classmethod
    def _default_ts(cls, v: str) -> str:
        return v or _now_iso()

    @field_validator("snippet", mode="before")
    @classmethod
    def _clip_snippet(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)[:280]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceRecord":
        return cls.model_validate(data)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python")


class MemoryEntry(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=False)

    id: str = Field(default_factory=_new_id)
    summary: str = ""
    category: str = "fact"  # preference | workflow | project_knowledge | fact | self_eval
    scope: str = "global_user"  # global_user | workspace | skill | thread | automation
    confidence: float = 0.7
    observation_count: int = 1
    evidence_log: list[EvidenceRecord] = Field(default_factory=list)
    source_skills: list[str] = Field(default_factory=list)
    source_workspace: str = ""
    status: str = "active"  # active | superseded
    superseded_by: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    last_observed_at: str = Field(default_factory=_now_iso)
    # M0 PR1 additions — automation memory + self-eval support
    source_automation: str | None = None
    feedback_source: Literal["llm_judge", "implicit", "explicit"] | None = None
    signals: list[dict[str, Any]] = Field(default_factory=list)  # [{source, value, ts}]
    quality: Literal["pass", "borderline", "fail"] | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, v: Any) -> str:
        if not v:
            return _new_id()
        return str(v)

    @field_validator("summary", mode="before")
    @classmethod
    def _coerce_summary(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, v: Any) -> str:
        s = str(v) if v is not None else "fact"
        return s if s in VALID_CATEGORIES else "fact"

    @field_validator("scope", mode="before")
    @classmethod
    def _coerce_scope(cls, v: Any) -> str:
        s = str(v) if v is not None else "global_user"
        return s if s in VALID_SCOPES else "global_user"

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, v: Any) -> str:
        s = str(v) if v is not None else "active"
        return s if s in VALID_STATUS else "active"

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v: Any) -> float:
        try:
            f = float(v) if v is not None else 0.7
        except (TypeError, ValueError):
            f = 0.7
        return max(0.0, min(1.0, f))

    @field_validator("observation_count", mode="before")
    @classmethod
    def _coerce_observation_count(cls, v: Any) -> int:
        try:
            n = int(v) if v is not None else 1
        except (TypeError, ValueError):
            n = 1
        return max(1, n)

    @field_validator("source_skills", mode="before")
    @classmethod
    def _coerce_source_skills(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(s) for s in v if s]

    @field_validator("source_workspace", "created_at", "last_observed_at", mode="before")
    @classmethod
    def _coerce_str_field(cls, v: Any) -> str:
        if v is None or v == "":
            return ""
        return str(v)

    @field_validator("created_at", "last_observed_at", mode="after")
    @classmethod
    def _default_timestamp(cls, v: str) -> str:
        return v or _now_iso()

    @field_validator("superseded_by", mode="before")
    @classmethod
    def _coerce_superseded_by(cls, v: Any) -> str | None:
        if not v:
            return None
        return str(v)

    @field_validator("evidence_log", mode="before")
    @classmethod
    def _coerce_evidence_log(cls, v: Any) -> list[Any]:
        if not isinstance(v, list):
            return []
        return [item for item in v if isinstance(item, (dict, EvidenceRecord))]

    @field_validator("source_automation", mode="before")
    @classmethod
    def _coerce_source_automation(cls, v: Any) -> str | None:
        if v is None or v == "":
            return None
        return str(v)

    @field_validator("feedback_source", mode="before")
    @classmethod
    def _coerce_feedback_source(cls, v: Any) -> str | None:
        if v is None or v == "":
            return None
        s = str(v)
        return s if s in VALID_FEEDBACK_SOURCES else None

    @field_validator("signals", mode="before")
    @classmethod
    def _coerce_signals(cls, v: Any) -> list[dict[str, Any]]:
        if not isinstance(v, list):
            return []
        out: list[dict[str, Any]] = []
        for item in v:
            if isinstance(item, dict):
                out.append({str(k): item[k] for k in item})
        return out

    @field_validator("quality", mode="before")
    @classmethod
    def _coerce_quality(cls, v: Any) -> str | None:
        if v is None or v == "":
            return None
        s = str(v)
        return s if s in VALID_QUALITIES else None

    @model_validator(mode="after")
    def _enforce_self_eval_automation_binding(self) -> "MemoryEntry":
        """category=self_eval ↔ scope=automation 双向绑定 (M0 PR1).

        - ``category == "self_eval"`` ⇒ must have ``scope == "automation"`` and
          non-empty ``source_automation``.
        - ``scope == "automation"`` ⇒ must have ``category == "self_eval"``.

        Legacy pre-v6 yaml does not produce these values, so this only fires on
        new writes or malformed dev data.
        """
        if self.category == "self_eval":
            if self.scope != "automation":
                raise ValueError(
                    "category=self_eval requires scope=automation "
                    f"(got scope={self.scope!r})"
                )
            if not self.source_automation:
                raise ValueError(
                    "category=self_eval requires non-empty source_automation"
                )
        if self.scope == "automation" and self.category != "self_eval":
            raise ValueError(
                "scope=automation requires category=self_eval "
                f"(got category={self.category!r})"
            )
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="python")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryEntry":
        return cls.model_validate(data)


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
        # Orders on-disk renames only; state snapshots happen under _lock, so
        # last-snapshot-wins is safe (each snapshot carries full state).
        self._write_lock = asyncio.Lock()
        self._dirty = False
        # Monotonic counter advanced on every state mutation. Synthesis
        # snapshots it before its LLM call so clear_synthesis_flag() can tell
        # whether a write interleaved (and keep _dirty set if so).
        self._mutation_count = 0
        self._synthesize_after_seconds = synthesize_after_seconds
        self._max_evidence_per_entry = max_evidence_per_entry
        # M0 PR1: load defenses — flip to read-only on unrecoverable load to
        # prevent later save() from overwriting a possibly-recoverable file.
        self._load_failed_readonly: bool = False
        self._last_load_stats: dict[str, int] = {"loaded": 0, "skipped": 0}

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
        """Load ``memories.yaml`` with M0 PR1 defenses.

        Behavior:

        - File missing → empty store, normal write path.
        - File empty (yaml.safe_load returns ``None``) → empty store, normal
          write path.
        - YAML parse failure → log warning + empty store + flip to
          ``_load_failed_readonly=True`` (no subsequent save can overwrite the
          unreadable file).
        - Top-level structure NOT a list → raise :class:`MemoryStoreLoadError`.
          Boot is expected to log + degrade store to read-only mode.
        - Per-entry ValidationError → log warning + skip + count toward
          ``skipped``. Entry survives in the original file (see quarantine
          backup below).
        - On ``skipped > 0`` → before returning, copy the original
          ``memories.yaml`` to ``memories.yaml.quarantine-<ISO8601>`` so the
          first successful save does not silently drop skipped entries.
        - One-shot ops alert lines emit at WARNING level.
        """
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._last_load_stats = {"loaded": 0, "skipped": 0}
        if not self._entries_path.exists():
            self._memories = []
            return
        try:
            raw = self._entries_path.read_text(encoding="utf-8")
            data = yaml.load(raw, Loader=_YamlLoader)  # noqa: S506 — SafeLoader variant
        except Exception as exc:
            logger.warning(
                "Failed to read %s: %s — disabling further saves to protect file",
                self._entries_path,
                exc,
            )
            self._memories = []
            self._load_failed_readonly = True
            # TODO(M0 PR1 followup): transient disk errors (EBUSY, read timeout)
            # also land here and keep the store read-only for the rest of the
            # process lifetime. A periodic recovery retry or operator-triggered
            # reload would let the store rebound without a bot restart.
            return
        if data is None:
            # Empty file is a valid initial state.
            self._memories = []
            return
        if not isinstance(data, list):
            self._load_failed_readonly = True
            self._memories = []
            raise MemoryStoreLoadError(
                f"top-level structure in {self._entries_path} must be a list "
                f"(got {type(data).__name__}); read-only recovery mode engaged"
            )

        loaded: list[MemoryEntry] = []
        skipped = 0
        for item in data:
            try:
                entry = MemoryEntry.from_dict(item) if isinstance(item, dict) else None
            except Exception as exc:
                logger.warning(
                    "Skipping malformed entry in %s: %s (item=%r)",
                    self._entries_path,
                    exc,
                    item,
                )
                skipped += 1
                continue
            if entry is None:
                skipped += 1
                continue
            loaded.append(entry)

        loaded, compacted = self._compact_expired(loaded)
        if compacted:
            logger.info(
                "Memory store compaction dropped %d expired entries "
                "(superseded / self_eval older than %d days)",
                compacted,
                _COMPACTION_MAX_AGE_DAYS,
            )

        self._memories = loaded
        self._last_load_stats = {"loaded": len(loaded), "skipped": skipped}

        if skipped > 0:
            # Round-6 defense: load() skipped some entries. Next save() would
            # silently drop them from the file. Quarantine the original now so
            # they survive in a sidecar for manual recovery.
            #
            # Filename has 1-second resolution (microseconds stripped). Two
            # loads within the same second produce the same path, and
            # ``shutil.copy2`` silently overwrites. Acceptable for the current
            # startup-only load pattern (load runs once per boot); if load()
            # ever becomes hot-path, add microseconds or a counter.
            ts = _now_iso().replace(":", "").replace("-", "").split(".")[0]
            quarantine = self._entries_path.with_suffix(
                f".yaml.quarantine-{ts}"
            )
            try:
                shutil.copy2(self._entries_path, quarantine)
            except Exception as exc:
                logger.warning("Failed to write quarantine backup: %s", exc)
            else:
                logger.warning(
                    "Memory store loaded %d entries, skipped %d malformed. "
                    "Quarantine backup at %s.",
                    len(loaded),
                    skipped,
                    quarantine,
                )

    @staticmethod
    def _compact_expired(
        entries: list[MemoryEntry],
    ) -> tuple[list[MemoryEntry], int]:
        """Drop entries that can no longer influence behavior.

        - ``status=superseded`` older than ``_COMPACTION_MAX_AGE_DAYS``: nothing
          renders supersede chains (``superseded_by`` points old → new, so an
          active entry never references a superseded one) — safe to drop.
        - ``category=self_eval`` older than the same horizon: per-task feedback
          signals lose relevance long before 90 days.

        Entries with unparseable timestamps are kept (conservative).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=_COMPACTION_MAX_AGE_DAYS)
        kept: list[MemoryEntry] = []
        dropped = 0
        for entry in entries:
            if entry.status == "superseded" or entry.category == "self_eval":
                ts = _parse_iso_ts(entry.last_observed_at) or _parse_iso_ts(entry.created_at)
                if ts is not None and ts < cutoff:
                    dropped += 1
                    continue
            kept.append(entry)
        return kept, dropped

    async def save(self) -> None:
        """Snapshot state under the lock, then persist off the event loop."""
        if self._load_failed_readonly:
            # Round-6 defense: refuse to overwrite a file we could not load.
            logger.debug(
                "save() short-circuited: store is in read-only recovery mode (%s)",
                self._entries_path,
            )
            return
        async with self._lock:
            payload = self._snapshot_payload()
        await self._write_snapshot(payload)

    def _snapshot_payload(self) -> list[dict[str, Any]]:
        """Serialize current state. Caller must hold ``self._lock``."""
        return [entry.to_dict() for entry in self._memories]

    async def _write_snapshot(self, payload: list[dict[str, Any]]) -> None:
        """Dump + atomic-rename ``payload`` in a worker thread.

        ``_write_lock`` orders renames so two concurrent saves cannot
        interleave; each payload is a full-state snapshot, so the last writer
        winning is correct.
        """
        if self._load_failed_readonly:
            return
        async with self._write_lock:
            try:
                await asyncio.to_thread(self._write_payload_sync, payload)
            except Exception as exc:
                logger.warning("Failed to save %s: %s", self._entries_path, exc)

    def _write_payload_sync(self, payload: list[dict[str, Any]]) -> None:
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._entries_path.with_suffix(".tmp")
        try:
            tmp.write_text(
                yaml.dump(
                    payload,
                    Dumper=_YamlDumper,
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            os.rename(str(tmp), str(self._entries_path))
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    @property
    def is_readonly(self) -> bool:
        """True when load() detected unrecoverable state; saves are no-ops."""
        return self._load_failed_readonly

    @property
    def last_load_stats(self) -> dict[str, int]:
        return dict(self._last_load_stats)

    # ------------------------------------------------------------------
    # M1 PR4 — self_eval signal merge
    # ------------------------------------------------------------------

    @staticmethod
    def _self_eval_task_key(automation_name: str, task_id: str) -> str:
        """Stable evidence thread_id for per-task self_eval dedupe / merge.

        All three feedback sources (llm_judge / implicit / explicit) write
        through ``upsert_self_eval_signal`` keyed on this string so they
        consolidate into ONE entry per task rather than N siblings.
        """
        return f"automation:{automation_name}::task:{task_id}"

    def _find_self_eval_for_task(
        self, automation_name: str, task_id: str
    ) -> MemoryEntry | None:
        key = self._self_eval_task_key(automation_name, task_id)
        for entry in self.get_active():
            if entry.category != "self_eval":
                continue
            if entry.source_automation != automation_name:
                continue
            for ev in entry.evidence_log:
                if ev.thread_id == key:
                    return entry
        return None

    async def upsert_self_eval_signal(
        self,
        *,
        automation_name: str,
        task_id: str,
        source: str,
        quality: str,
        confidence: float,
        reason: str,
        skill_name: str | None = None,
        source_workspace: str | None = None,
    ) -> str | None:
        """Merge a feedback signal into the per-task self_eval entry.

        If an active self_eval entry already exists for (automation_name,
        task_id): append ``{source, value, ts, confidence}`` to its
        ``signals`` list, recompute ``confidence = max(signal confidences)``
        and ``quality = value of the highest-confidence signal``, bump
        ``observation_count``. Otherwise create a fresh entry.

        Returns the entry id, or None on validation reject / read-only.
        """
        if self._load_failed_readonly:
            return None
        if quality not in VALID_QUALITIES:
            logger.warning("upsert_self_eval_signal: invalid quality %r", quality)
            return None
        if source not in VALID_FEEDBACK_SOURCES:
            logger.warning("upsert_self_eval_signal: invalid source %r", source)
            return None
        confidence = max(0.0, min(1.0, float(confidence)))
        key = self._self_eval_task_key(automation_name, task_id)
        signal = {
            "source": source,
            "value": quality,
            "confidence": confidence,
            "ts": _now_iso(),
        }
        async with self._lock:
            existing = self._find_self_eval_for_task(automation_name, task_id)
            if existing is not None:
                existing.signals.append(signal)
                # Quality = value of the WINNING signal. Tie-break (Codex M1
                # PR4): (1) highest confidence, (2) source rank
                # explicit > implicit > llm_judge, (3) latest timestamp.
                best = max(
                    existing.signals,
                    key=lambda s: (
                        float(s.get("confidence", 0)),
                        _SIGNAL_SOURCE_RANK.get(str(s.get("source", "")), 0),
                        str(s.get("ts", "")),
                    ),
                )
                best_quality = str(best.get("value", quality))
                if best_quality in VALID_QUALITIES:
                    existing.quality = cast(
                        Literal["pass", "borderline", "fail"], best_quality
                    )
                existing.confidence = max(
                    float(s.get("confidence", 0)) for s in existing.signals
                )
                # Latest-writer feedback_source wins as the "primary" tag.
                existing.feedback_source = cast(
                    Literal["llm_judge", "implicit", "explicit"], source
                )
                existing.observation_count += 1
                existing.last_observed_at = _now_iso()
                existing.evidence_log.append(
                    EvidenceRecord(thread_id=key, ts=_now_iso(), snippet=reason[:280])
                )
                if len(existing.evidence_log) > self._max_evidence_per_entry:
                    existing.evidence_log = existing.evidence_log[
                        -self._max_evidence_per_entry :
                    ]
                self._note_mutation()
                entry_id = existing.id
                payload = self._snapshot_payload()
            else:
                # New entry
                try:
                    entry = MemoryEntry(
                        summary=f"reason={reason}; task={task_id}"[:280],
                        category="self_eval",
                        scope="automation",
                        source_automation=automation_name,
                        feedback_source=cast(
                            Literal["llm_judge", "implicit", "explicit"], source
                        ),
                        quality=cast(Literal["pass", "borderline", "fail"], quality),
                        confidence=confidence,
                        observation_count=1,
                        signals=[signal],
                        source_skills=[skill_name] if skill_name else [],
                        source_workspace=source_workspace or "",
                        evidence_log=[
                            EvidenceRecord(thread_id=key, ts=_now_iso(), snippet=reason[:280])
                        ],
                    )
                except Exception as exc:
                    logger.warning("upsert_self_eval_signal: entry construction failed: %s", exc)
                    return None
                self._memories.append(entry)
                self._note_mutation()
                entry_id = entry.id
                payload = self._snapshot_payload()
        # Disk write happens outside the state lock so other mutations are
        # never blocked on YAML serialization.
        await self._write_snapshot(payload)
        return entry_id

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

    @staticmethod
    def format_memory_block(
        entries: list[MemoryEntry], *, header: str = "Remembered context"
    ) -> str:
        """Render entries as a ``[<header>]`` prompt prefix block.

        Returns an empty string when entries is empty. The format matches the
        previous inline rendering used by chat path so chat + runtime paths
        produce identical text.

        Output shape::

            [Remembered context]
            - first summary
            - second summary

        Callers concatenate this to the user prompt (with a separating ``\\n\\n``).
        """
        if not entries:
            return ""
        lines = [f"- {entry.summary}" for entry in entries]
        return f"[{header}]\n" + "\n".join(lines)

    def get_relevant(
        self,
        *,
        skill_name: str | None = None,
        thread_id: str | None = None,
        workspace: str | None = None,
        automation_name: str | None = None,
        limit: int = 12,
    ) -> list[MemoryEntry]:
        """Score + return active entries for prompt injection.

        Behavior matrix for ``scope=automation`` entries (M0 PR1):

        - ``automation_name`` matches ``entry.source_automation`` → include
        - ``automation_name`` does NOT match → exclude (strict isolation)
        - ``automation_name is None`` (chat path) → exclude (chat does not read
          automation memories)

        Other scopes are unaffected by ``automation_name``.
        """
        scored: list[tuple[float, MemoryEntry]] = []
        for entry in self.get_active():
            if entry.scope == "thread" and (not thread_id or thread_id not in [e.thread_id for e in entry.evidence_log]):
                continue
            if entry.scope == "skill" and (not skill_name or skill_name not in entry.source_skills):
                continue
            if entry.scope == "workspace" and (not workspace or entry.source_workspace != workspace):
                continue
            if entry.scope == "automation":
                if not automation_name or entry.source_automation != automation_name:
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
        payload: list[dict[str, Any]] | None = None
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
                            self._note_mutation()
                        else:
                            stats["rejected"] += 1
                    elif op == "strengthen":
                        if self._apply_strengthen(action, thread_id):
                            stats["strengthen"] += 1
                            self._note_mutation()
                        else:
                            stats["rejected"] += 1
                    elif op == "supersede":
                        if self._apply_supersede(action, thread_id, skill_name, source_workspace):
                            stats["supersede"] += 1
                            self._note_mutation()
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
                payload = self._snapshot_payload()
        if payload is not None:
            await self._write_snapshot(payload)
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
            self._note_mutation()
            payload = self._snapshot_payload()
        await self._write_snapshot(payload)
        return True

    # ------------------------------------------------------------------
    # Synthesis (MEMORY.md)
    # ------------------------------------------------------------------

    def _note_mutation(self) -> None:
        """Mark state dirty and advance the write counter.

        The counter lets ``clear_synthesis_flag`` detect writes that landed
        while a synthesis pass was awaiting its LLM call — those must not be
        wiped by the (now stale) synthesis clearing ``_dirty``.
        """
        self._dirty = True
        self._mutation_count += 1

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

    def clear_synthesis_flag(self, *, observed_mutations: int | None = None) -> None:
        """Clear the dirty flag.

        ``observed_mutations`` is the write-counter value snapshotted before a
        synthesis pass started. If writes landed during the pass the counter
        has advanced and ``_dirty`` is left set so the next trigger
        re-synthesizes. ``None`` clears unconditionally (legacy callers).
        """
        if observed_mutations is not None and observed_mutations != self._mutation_count:
            return
        self._dirty = False

    async def synthesize_memory_md(self, registry) -> bool:
        # Snapshot before any await: writes interleaving with the LLM call
        # below advance the counter and keep the store dirty.
        observed_mutations = self._mutation_count
        active = self.get_active()
        if not active:
            try:
                if self._memory_md_path.exists():
                    self._memory_md_path.unlink()
            except OSError:
                pass
            self.clear_synthesis_flag(observed_mutations=observed_mutations)
            return True

        # M0 PR1: split self_eval entries from user-fact entries.
        # User-facts go through LLM synthesis (2nd-person, about user).
        # Self-eval entries get rendered directly in a separate section so they
        # don't pollute the user-fact prose.
        by_cat: dict[str, list[MemoryEntry]] = {}
        for m in active:
            by_cat.setdefault(m.category, []).append(m)

        # User-facts → LLM synthesis
        user_fact_lines: list[str] = []
        for cat in ("preference", "workflow", "project_knowledge", "fact"):
            entries = by_cat.get(cat) or []
            if not entries:
                continue
            user_fact_lines.append(f"\n## {cat}")
            for entry in entries:
                user_fact_lines.append(f"- {entry.summary}")

        synthesized_user_facts = ""
        if user_fact_lines:
            prompt = _SYNTHESIS_PROMPT.format(entries_text="\n".join(user_fact_lines))
            try:
                _agent, response = await registry.run(prompt, run_label="memory_md_synthesis")
            except Exception as exc:
                logger.warning("memory_md synthesis failed: %s", exc)
                return False
            if response.error:
                logger.warning("memory_md synthesis returned error: %s", response.error)
                return False
            synthesized_user_facts = response.text.strip()

        # Self-evals → direct render (no LLM, structured per automation)
        self_eval_section = ""
        self_evals = by_cat.get("self_eval") or []
        if self_evals:
            # Group by automation_name for readability.
            by_auto: dict[str, list[MemoryEntry]] = {}
            for entry in self_evals:
                key = entry.source_automation or "(unknown automation)"
                by_auto.setdefault(key, []).append(entry)
            lines = ["## Past Self-Evaluations"]
            for auto_name in sorted(by_auto.keys()):
                lines.append(f"\n### {auto_name}")
                # Sort latest-first by last_observed_at
                entries = sorted(by_auto[auto_name], key=lambda m: m.last_observed_at, reverse=True)
                for entry in entries[:5]:  # cap at 5 most recent per automation
                    q = entry.quality or "unknown"
                    src = entry.feedback_source or "?"
                    lines.append(f"- [{q}] ({src}) {entry.summary}")
            self_eval_section = "\n".join(lines)

        # Combine sections
        parts = [synthesized_user_facts, self_eval_section]
        body = "\n\n".join(p for p in parts if p).strip()
        if not body:
            self.clear_synthesis_flag(observed_mutations=observed_mutations)
            return True
        try:
            self._memory_md_path.write_text(body + "\n", encoding="utf-8")
            self.clear_synthesis_flag(observed_mutations=observed_mutations)
            logger.info("MEMORY.md synthesized (%d chars)", len(body))
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
        source_automation: str | None = None,
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

        # M0 PR1: pull new fields from action (judge may emit) OR from caller context.
        # action-level value wins over caller-supplied default.
        action_source_automation = action.get("source_automation")
        effective_source_automation = (
            str(action_source_automation) if action_source_automation else source_automation
        )
        feedback_source_raw = action.get("feedback_source")
        feedback_source: Literal["llm_judge", "implicit", "explicit"] | None = (
            cast(Literal["llm_judge", "implicit", "explicit"], str(feedback_source_raw))
            if feedback_source_raw and str(feedback_source_raw) in VALID_FEEDBACK_SOURCES
            else None
        )
        signals_raw = action.get("signals")
        signals = signals_raw if isinstance(signals_raw, list) else []
        quality_raw = action.get("quality")
        quality: Literal["pass", "borderline", "fail"] | None = (
            cast(Literal["pass", "borderline", "fail"], str(quality_raw))
            if quality_raw and str(quality_raw) in VALID_QUALITIES
            else None
        )

        try:
            entry = MemoryEntry(
                summary=summary,
                category=category,
                scope=scope,
                confidence=confidence,
                observation_count=1,
                evidence_log=evidence_log,
                source_skills=source_skills,
                source_workspace=source_workspace or "",
                source_automation=effective_source_automation,
                feedback_source=feedback_source,
                signals=signals,
                quality=quality,
            )
        except Exception as exc:
            # model_validator (self_eval ↔ automation binding) rejection lands here
            logger.warning("_apply_add rejected: %s (action=%r)", exc, action)
            return False
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
        source_automation: str | None = None,
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

        # M0 PR1: inherit + override new fields from old + action
        action_source_automation = action.get("source_automation")
        effective_source_automation = (
            str(action_source_automation)
            if action_source_automation
            else (source_automation or old_entry.source_automation)
        )
        feedback_source_raw = action.get("feedback_source")
        feedback_source: Literal["llm_judge", "implicit", "explicit"] | None = (
            cast(Literal["llm_judge", "implicit", "explicit"], str(feedback_source_raw))
            if feedback_source_raw and str(feedback_source_raw) in VALID_FEEDBACK_SOURCES
            else old_entry.feedback_source
        )
        signals_raw = action.get("signals")
        signals = signals_raw if isinstance(signals_raw, list) else list(old_entry.signals)
        quality_raw = action.get("quality")
        quality: Literal["pass", "borderline", "fail"] | None = (
            cast(Literal["pass", "borderline", "fail"], str(quality_raw))
            if quality_raw and str(quality_raw) in VALID_QUALITIES
            else old_entry.quality
        )

        try:
            new_entry = MemoryEntry(
                summary=new_summary,
                category=category,
                scope=scope,
                confidence=confidence,
                observation_count=max(1, old_entry.observation_count),
                evidence_log=evidence_log,
                source_skills=source_skills,
                source_workspace=source_workspace or old_entry.source_workspace,
                source_automation=effective_source_automation,
                feedback_source=feedback_source,
                signals=signals,
                quality=quality,
            )
        except Exception as exc:
            logger.warning("_apply_supersede rejected: %s (action=%r)", exc, action)
            return False
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
