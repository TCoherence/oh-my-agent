"""Adaptive memory — learns user preferences and project knowledge from conversations."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

VALID_CATEGORIES = frozenset({"preference", "project_knowledge", "workflow", "fact"})


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
    observation_count: int = 1


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
                MemoryEntry(**{k: v for k, v in item.items() if k in MemoryEntry.__dataclass_fields__})
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

    async def add_memories(self, entries: list[MemoryEntry]) -> int:
        """Deduplicate, merge, insert, prune. Returns count of new entries added."""
        async with self._lock:
            added = 0
            for entry in entries:
                if entry.category not in VALID_CATEGORIES:
                    entry.category = "fact"
                entry.confidence = max(0.0, min(1.0, entry.confidence))

                match = self._find_duplicate(entry.summary)
                if match is not None:
                    # Merge: boost confidence, union threads, bump count
                    existing = self._memories[match]
                    existing.confidence = min(1.0, existing.confidence + 0.15)
                    existing.observation_count += 1
                    existing.last_referenced = datetime.now(timezone.utc).isoformat()
                    for t in entry.source_threads:
                        if t not in existing.source_threads:
                            existing.source_threads.append(t)
                else:
                    self._memories.append(entry)
                    added += 1

            # Prune below min_confidence
            self._memories = [m for m in self._memories if m.confidence >= self._min_confidence]

            # Cap at max_memories by evicting lowest score
            if len(self._memories) > self._max_memories:
                self._memories.sort(key=lambda m: self._eviction_score(m), reverse=True)
                self._memories = self._memories[: self._max_memories]

            await self.save()
            return added

    async def get_relevant(self, context: str, budget_chars: int = 500) -> list[MemoryEntry]:
        """Return top memories relevant to context, fitting within budget."""
        if not self._memories:
            return []

        context_words = self._word_set(context)
        scored: list[tuple[float, MemoryEntry]] = []
        for m in self._memories:
            sim = self._similarity_score(context_words, self._word_set(m.summary))
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

    def _find_duplicate(self, summary: str) -> int | None:
        """Find index of existing memory with high Jaccard overlap, or None."""
        words_new = self._word_set(summary)
        for i, existing in enumerate(self._memories):
            if self._similarity_score(words_new, self._word_set(existing.summary)) >= 0.6:
                return i
        return None

    @staticmethod
    def _similarity_score(words_a: set[str], words_b: set[str]) -> float:
        """Jaccard similarity on word sets."""
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        union = words_a | words_b
        return len(intersection) / len(union)

    @staticmethod
    def _word_set(text: str) -> set[str]:
        return set(text.lower().split())

    @staticmethod
    def _eviction_score(m: MemoryEntry) -> float:
        """Higher = more likely to keep."""
        try:
            ref = datetime.fromisoformat(m.last_referenced)
            age_days = (datetime.now(timezone.utc) - ref).total_seconds() / 86400
        except (ValueError, TypeError):
            age_days = 30.0
        recency_weight = 1.0 / (1.0 + age_days * 0.1)
        return m.confidence * recency_weight
