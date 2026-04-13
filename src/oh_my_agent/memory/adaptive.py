"""Adaptive memory — learns user preferences and project knowledge from conversations."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

VALID_CATEGORIES = frozenset({"preference", "project_knowledge", "workflow", "fact"})
VALID_EXPLICITNESS = frozenset({"explicit", "inferred"})
VALID_STATUS = frozenset({"active", "superseded"})
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "uses",
        "using",
        "user",
        "with",
        "your",
    }
)


@dataclass
class MemoryEntry:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    summary: str = ""
    category: str = "fact"  # preference | project_knowledge | workflow | fact
    confidence: float = 0.6
    source_threads: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_referenced: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_observed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    observation_count: int = 1
    tier: str = "daily"  # "daily" | "curated"
    explicitness: str = "inferred"  # "explicit" | "inferred"
    status: str = "active"  # "active" | "superseded"
    evidence: str = ""


# ---------------------------------------------------------------------------
# Module-level utility functions (shared by AdaptiveMemoryStore & DateBasedMemoryStore)
# ---------------------------------------------------------------------------

def word_set(text: str) -> set[str]:
    """Split text into a lowercase word set."""
    return set(text.lower().split())


def normalized_tokens(text: str) -> list[str]:
    """Return normalized lexical tokens for lightweight similarity checks."""
    tokens = []
    for token in _TOKEN_RE.findall(text.lower()):
        if token in _STOPWORDS:
            continue
        if token.endswith("ies") and len(token) > 4:
            token = token[:-3] + "y"
        elif token.endswith("es") and len(token) > 4:
            token = token[:-2]
        elif token.endswith("s") and len(token) > 3:
            token = token[:-1]
        tokens.append(token)
    return tokens


def normalized_text(text: str) -> str:
    return " ".join(normalized_tokens(text))


def normalized_word_set(text: str) -> set[str]:
    return set(normalized_tokens(text))


def jaccard_similarity(words_a: set[str], words_b: set[str]) -> float:
    """Jaccard similarity on word sets."""
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def eviction_score(m: MemoryEntry, decay_half_life_days: float = 10.0) -> float:
    """Higher = more likely to keep. Uses confidence * recency weight."""
    try:
        ref = datetime.fromisoformat(m.last_referenced)
        age_days = (datetime.now(timezone.utc) - ref).total_seconds() / 86400
    except (ValueError, TypeError):
        age_days = 30.0
    recency_weight = 1.0 / (1.0 + age_days * 0.1)
    return m.confidence * recency_weight


def find_duplicate(
    summary: str,
    memories: list[MemoryEntry],
    threshold: float = 0.6,
) -> int | None:
    """Find index of existing memory with high Jaccard overlap, or None."""
    words_new = normalized_word_set(summary)
    for i, existing in enumerate(memories):
        existing_norm = normalized_text(existing.summary)
        if existing_norm and existing_norm == normalized_text(summary):
            return i
        if jaccard_similarity(words_new, normalized_word_set(existing.summary)) >= threshold:
            return i
    return None


def lexical_match_kind(summary_a: str, summary_b: str) -> str:
    """Classify lexical similarity into same/candidate/distinct buckets."""
    norm_a = normalized_text(summary_a)
    norm_b = normalized_text(summary_b)
    if norm_a and norm_a == norm_b:
        return "same_memory"
    score = jaccard_similarity(set(norm_a.split()), set(norm_b.split()))
    if score >= 0.75:
        return "same_memory"
    if score >= 0.35:
        return "candidate"
    return "distinct"


def memory_entry_from_dict(data: dict) -> MemoryEntry:
    fields = MemoryEntry.__dataclass_fields__
    payload = {k: v for k, v in data.items() if k in fields}
    if "last_observed_at" not in payload and payload.get("created_at"):
        payload["last_observed_at"] = payload["created_at"]
    if payload.get("explicitness") not in VALID_EXPLICITNESS:
        payload["explicitness"] = "inferred"
    if payload.get("status") not in VALID_STATUS:
        payload["status"] = "active"
    if not payload.get("evidence"):
        payload["evidence"] = ""
    return MemoryEntry(**payload)


class AdaptiveMemoryStore:
    """YAML-backed persistent store for extracted user memories."""

    def __init__(
        self,
        path: str | Path,
        max_memories: int = 100,
        min_confidence: float = 0.3,
    ) -> None:
        self._path = Path(path).expanduser().resolve()
        self._max_memories = max_memories
        self._min_confidence = min_confidence
        self._memories: list[MemoryEntry] = []
        self._lock = asyncio.Lock()
        self._last_retrieval_stats: dict[str, int] = {}

    @property
    def memories(self) -> list[MemoryEntry]:
        return list(self._memories)

    async def load(self) -> None:
        """Read YAML file into memory. No-op if file is missing."""
        if not self._path.exists():
            self._memories = []
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            if not isinstance(data, list):
                self._memories = []
                return
            self._memories = [
                memory_entry_from_dict(item)
                for item in data
                if isinstance(item, dict)
            ]
        except Exception as exc:
            logger.warning("Failed to load adaptive memories from %s: %s", self._path, exc)
            self._memories = []

    async def save(self) -> None:
        """Atomic write: write to .tmp then rename."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        try:
            data = [asdict(m) for m in self._memories]
            tmp.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
            os.rename(str(tmp), str(self._path))
        except Exception as exc:
            logger.warning("Failed to save adaptive memories: %s", exc)
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    async def add_memories(self, entries: list[MemoryEntry], registry=None, req_id: str | None = None) -> int:
        """Deduplicate, merge, insert, prune. Returns count of new entries added."""
        del registry, req_id
        async with self._lock:
            added = 0
            for entry in entries:
                if entry.category not in VALID_CATEGORIES:
                    entry.category = "fact"
                if entry.explicitness not in VALID_EXPLICITNESS:
                    entry.explicitness = "inferred"
                if entry.status not in VALID_STATUS:
                    entry.status = "active"
                entry.confidence = max(0.0, min(1.0, entry.confidence))
                if not entry.last_observed_at:
                    entry.last_observed_at = entry.created_at

                match = find_duplicate(entry.summary, self._memories)
                if match is not None:
                    # Merge: boost confidence, union threads, bump count
                    existing = self._memories[match]
                    existing.confidence = min(1.0, existing.confidence + 0.15)
                    existing.observation_count += 1
                    existing.last_referenced = datetime.now(timezone.utc).isoformat()
                    existing.last_observed_at = datetime.now(timezone.utc).isoformat()
                    if entry.explicitness == "explicit":
                        existing.explicitness = "explicit"
                    if entry.evidence and len(entry.evidence) >= len(existing.evidence):
                        existing.evidence = entry.evidence
                    for t in entry.source_threads:
                        if t not in existing.source_threads:
                            existing.source_threads.append(t)
                else:
                    self._memories.append(entry)
                    added += 1

            # Prune below min_confidence
            self._memories = [
                m for m in self._memories
                if m.confidence >= self._min_confidence and m.status == "active"
            ]

            # Cap at max_memories by evicting lowest score
            if len(self._memories) > self._max_memories:
                self._memories.sort(key=lambda m: eviction_score(m), reverse=True)
                self._memories = self._memories[: self._max_memories]

            await self.save()
            return added

    async def get_relevant(self, context: str, budget_chars: int = 500) -> list[MemoryEntry]:
        """Return top memories relevant to context, fitting within budget."""
        self._last_retrieval_stats = {
            "filtered_superseded_count": len([m for m in self._memories if m.status != "active"]),
        }
        if not self._memories:
            return []

        context_words = normalized_word_set(context)
        scored: list[tuple[float, MemoryEntry]] = []
        for m in self._memories:
            if m.status != "active":
                continue
            sim = jaccard_similarity(context_words, normalized_word_set(m.summary))
            # Preferences always get a minimum score boost
            if m.category == "preference":
                sim = max(sim, 0.1)
            score = sim * m.confidence
            if score > 0:
                scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)

        result: list[MemoryEntry] = []
        chars_used = 0
        for _score, m in scored:
            entry_chars = len(m.summary) + 10  # overhead for formatting
            if chars_used + entry_chars > budget_chars and result:
                break
            result.append(m)
            chars_used += entry_chars

        return result

    @property
    def last_retrieval_stats(self) -> dict[str, int]:
        return dict(self._last_retrieval_stats)

    async def list_all(self) -> list[MemoryEntry]:
        return list(self._memories)

    async def delete_memory(self, memory_id: str) -> bool:
        async with self._lock:
            before = len(self._memories)
            self._memories = [m for m in self._memories if m.id != memory_id]
            if len(self._memories) < before:
                await self.save()
                return True
            return False

    # Legacy aliases — delegate to module-level functions for backward compat
    _find_duplicate = staticmethod(lambda summary, mems=None: find_duplicate(summary, mems or []))
    _similarity_score = staticmethod(jaccard_similarity)
    _word_set = staticmethod(word_set)
    _eviction_score = staticmethod(eviction_score)
