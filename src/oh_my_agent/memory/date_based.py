"""Date-based two-tier memory store — daily logs + curated long-term memories."""

from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from oh_my_agent.memory.adaptive import (
    VALID_CATEGORIES,
    MemoryEntry,
    eviction_score,
    find_duplicate,
    jaccard_similarity,
    word_set,
)

logger = logging.getLogger(__name__)

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


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _daily_filename(d: date) -> str:
    return f"{d.isoformat()}.yaml"


def _parse_date(iso_str: str) -> date | None:
    try:
        return datetime.fromisoformat(iso_str).date()
    except (ValueError, TypeError):
        return None


def _age_days(created_at: str) -> float:
    d = _parse_date(created_at)
    if d is None:
        return 30.0
    return max(0.0, (_today() - d).days)


def _decay_factor(age_days: float, half_life: float) -> float:
    if half_life <= 0:
        return 1.0
    return math.exp(-0.693 * age_days / half_life)


def _load_yaml_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    except Exception as exc:
        logger.warning("Failed to load %s: %s", path, exc)
    return []


def _save_yaml_list(path: Path, entries: list[MemoryEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        data = [asdict(m) for m in entries]
        tmp.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
        os.rename(str(tmp), str(path))
    except Exception as exc:
        logger.warning("Failed to save %s: %s", path, exc)
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _dicts_to_entries(raw: list[dict]) -> list[MemoryEntry]:
    fields = MemoryEntry.__dataclass_fields__
    return [
        MemoryEntry(**{k: v for k, v in d.items() if k in fields})
        for d in raw
    ]


class DateBasedMemoryStore:
    """Two-tier date-organized memory store with daily logs and curated long-term memories."""

    def __init__(
        self,
        memory_dir: str | Path,
        max_memories: int = 100,
        min_confidence: float = 0.3,
        decay_half_life_days: float = 7.0,
        promotion_observation_threshold: int = 3,
        promotion_confidence_threshold: float = 0.8,
    ) -> None:
        self._memory_dir = Path(memory_dir).expanduser().resolve()
        self._daily_dir = self._memory_dir / "daily"
        self._curated_path = self._memory_dir / "curated.yaml"
        self._memory_md_path = self._memory_dir / "MEMORY.md"
        self._max_memories = max_memories
        self._min_confidence = min_confidence
        self._decay_half_life_days = decay_half_life_days
        self._promotion_obs = promotion_observation_threshold
        self._promotion_conf = promotion_confidence_threshold
        self._curated: list[MemoryEntry] = []
        self._daily_cache: dict[date, list[MemoryEntry]] = {}  # date → entries
        self._lock = asyncio.Lock()
        self._needs_synthesis = False

    # ------------------------------------------------------------------
    # Public interface (duck-typed compatible with AdaptiveMemoryStore)
    # ------------------------------------------------------------------

    @property
    def memories(self) -> list[MemoryEntry]:
        return self._curated + self._all_daily()

    async def load(self) -> None:
        """Load curated + today/yesterday daily, then auto-promote eligible entries."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._daily_dir.mkdir(parents=True, exist_ok=True)

        # Load curated
        self._curated = _dicts_to_entries(_load_yaml_list(self._curated_path))
        for m in self._curated:
            m.tier = "curated"

        # Load today + yesterday daily files
        today = _today()
        for d in (today, today.replace(day=today.day)):  # always load today
            self._load_daily(d)
        yesterday = today.toordinal() - 1
        yd = date.fromordinal(yesterday)
        self._load_daily(yd)

        # Auto-promote eligible entries
        promoted = await self._promote_eligible()
        if promoted:
            self._needs_synthesis = True

    async def save(self) -> None:
        """Persist curated and today's daily."""
        _save_yaml_list(self._curated_path, self._curated)
        today = _today()
        if today in self._daily_cache:
            _save_yaml_list(self._daily_dir / _daily_filename(today), self._daily_cache[today])

    async def add_memories(self, entries: list[MemoryEntry]) -> int:
        """Add memories to today's daily log. Dedup against curated + daily."""
        async with self._lock:
            added = 0
            today = _today()
            if today not in self._daily_cache:
                self._daily_cache[today] = []
            today_entries = self._daily_cache[today]

            all_pool = self._curated + self._all_daily()

            for entry in entries:
                if entry.category not in VALID_CATEGORIES:
                    entry.category = "fact"
                entry.confidence = max(0.0, min(1.0, entry.confidence))
                entry.tier = "daily"

                # Check curated first
                curated_match = find_duplicate(entry.summary, self._curated)
                if curated_match is not None:
                    existing = self._curated[curated_match]
                    existing.confidence = min(1.0, existing.confidence + 0.15)
                    existing.observation_count += 1
                    existing.last_referenced = datetime.now(timezone.utc).isoformat()
                    for t in entry.source_threads:
                        if t not in existing.source_threads:
                            existing.source_threads.append(t)
                    continue

                # Check daily cache
                daily_match = find_duplicate(entry.summary, self._all_daily())
                if daily_match is not None:
                    daily_all = self._all_daily()
                    existing = daily_all[daily_match]
                    existing.confidence = min(1.0, existing.confidence + 0.15)
                    existing.observation_count += 1
                    existing.last_referenced = datetime.now(timezone.utc).isoformat()
                    for t in entry.source_threads:
                        if t not in existing.source_threads:
                            existing.source_threads.append(t)
                    continue

                today_entries.append(entry)
                added += 1

            # Prune below min_confidence
            self._daily_cache[today] = [
                m for m in today_entries if m.confidence >= self._min_confidence
            ]

            await self.save()
            return added

    async def get_relevant(self, context: str, budget_chars: int = 500) -> list[MemoryEntry]:
        """Return memories relevant to context. Curated first (60% budget), then daily with decay."""
        all_mems = self.memories
        if not all_mems:
            return []

        context_words = word_set(context)

        def _score(m: MemoryEntry) -> float:
            sim = jaccard_similarity(context_words, word_set(m.summary))
            if m.category == "preference":
                sim = max(sim, 0.1)
            base = sim * m.confidence
            if m.tier == "daily":
                age = _age_days(m.created_at)
                base *= _decay_factor(age, self._decay_half_life_days)
            return base

        curated_scored = [(s, m) for m in self._curated if (s := _score(m)) > 0]
        daily_scored = [(s, m) for m in self._all_daily() if (s := _score(m)) > 0]

        curated_scored.sort(key=lambda x: x[0], reverse=True)
        daily_scored.sort(key=lambda x: x[0], reverse=True)

        # Curated gets 60% budget
        curated_budget = int(budget_chars * 0.6)
        result: list[MemoryEntry] = []
        chars_used = 0

        for _score_val, m in curated_scored:
            entry_chars = len(m.summary) + 10
            if chars_used + entry_chars > curated_budget and result:
                break
            result.append(m)
            chars_used += entry_chars

        # Remaining budget goes to daily
        remaining_budget = budget_chars - chars_used
        for _score_val, m in daily_scored:
            entry_chars = len(m.summary) + 10
            if chars_used + entry_chars > budget_chars and len(result) > len([r for r in result if r.tier == "curated"]):
                break
            result.append(m)
            chars_used += entry_chars

        return result

    async def list_all(self) -> list[MemoryEntry]:
        return list(self.memories)

    async def delete_memory(self, memory_id: str) -> bool:
        async with self._lock:
            # Check curated
            before = len(self._curated)
            self._curated = [m for m in self._curated if m.id != memory_id]
            if len(self._curated) < before:
                await self.save()
                return True

            # Check daily
            for d, entries in self._daily_cache.items():
                before = len(entries)
                self._daily_cache[d] = [m for m in entries if m.id != memory_id]
                if len(self._daily_cache[d]) < before:
                    _save_yaml_list(
                        self._daily_dir / _daily_filename(d),
                        self._daily_cache[d],
                    )
                    return True
            return False

    # ------------------------------------------------------------------
    # New methods
    # ------------------------------------------------------------------

    async def promote_memory(self, memory_id: str) -> bool:
        """Manually promote a daily memory to curated."""
        async with self._lock:
            for d, entries in self._daily_cache.items():
                for i, m in enumerate(entries):
                    if m.id == memory_id:
                        m.tier = "curated"
                        self._curated.append(m)
                        entries.pop(i)
                        _save_yaml_list(
                            self._daily_dir / _daily_filename(d),
                            entries,
                        )
                        await self.save()
                        self._needs_synthesis = True
                        return True
            return False

    async def synthesize_memory_md(self, registry) -> None:
        """Use an agent to synthesize curated memories into a natural-language MEMORY.md."""
        if not self._curated:
            return

        # Group by category
        by_cat: dict[str, list[str]] = {}
        for m in self._curated:
            by_cat.setdefault(m.category, []).append(m.summary)

        lines = []
        for cat in ("preference", "workflow", "project_knowledge", "fact"):
            if cat not in by_cat:
                continue
            lines.append(f"\n## {cat}")
            for s in by_cat[cat]:
                lines.append(f"- {s}")

        entries_text = "\n".join(lines)
        prompt = _SYNTHESIS_PROMPT.format(entries_text=entries_text)

        try:
            _agent, response = await registry.run(prompt)
            if response.error:
                logger.warning("MEMORY.md synthesis agent error: %s", response.error)
                return
            self._memory_md_path.write_text(response.text, encoding="utf-8")
            logger.info("MEMORY.md synthesized (%d chars)", len(response.text))
        except Exception as exc:
            logger.warning("MEMORY.md synthesis failed (keeping old file): %s", exc)

    @property
    def needs_synthesis(self) -> bool:
        return self._needs_synthesis

    def clear_synthesis_flag(self) -> None:
        self._needs_synthesis = False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _all_daily(self) -> list[MemoryEntry]:
        result = []
        for entries in self._daily_cache.values():
            result.extend(entries)
        return result

    def _load_daily(self, d: date) -> None:
        if d in self._daily_cache:
            return
        path = self._daily_dir / _daily_filename(d)
        entries = _dicts_to_entries(_load_yaml_list(path))
        for m in entries:
            m.tier = "daily"
        if entries:
            self._daily_cache[d] = entries

    async def _promote_eligible(self) -> int:
        """Scan ALL daily files and promote entries that meet thresholds."""
        promoted = 0

        # Scan all daily files on disk (not just cached ones)
        if self._daily_dir.exists():
            for yaml_file in sorted(self._daily_dir.glob("*.yaml")):
                try:
                    d = date.fromisoformat(yaml_file.stem)
                except ValueError:
                    continue
                # Load if not cached
                if d not in self._daily_cache:
                    entries = _dicts_to_entries(_load_yaml_list(yaml_file))
                    for m in entries:
                        m.tier = "daily"
                    if entries:
                        self._daily_cache[d] = entries

        # Now check all cached daily entries for promotion
        for d in list(self._daily_cache.keys()):
            entries = self._daily_cache[d]
            to_promote = []
            remaining = []
            for m in entries:
                age = _age_days(m.created_at)
                if (
                    m.observation_count >= self._promotion_obs
                    and m.confidence >= self._promotion_conf
                    and age >= 1.0
                ):
                    # Check if duplicate already in curated
                    dup = find_duplicate(m.summary, self._curated)
                    if dup is not None:
                        # Merge into curated
                        existing = self._curated[dup]
                        existing.confidence = min(1.0, existing.confidence + 0.1)
                        existing.observation_count += m.observation_count
                        for t in m.source_threads:
                            if t not in existing.source_threads:
                                existing.source_threads.append(t)
                    else:
                        m.tier = "curated"
                        to_promote.append(m)
                else:
                    remaining.append(m)

            if to_promote:
                self._curated.extend(to_promote)
                self._daily_cache[d] = remaining
                promoted += len(to_promote)
                # Save updated daily file
                if remaining:
                    _save_yaml_list(self._daily_dir / _daily_filename(d), remaining)
                else:
                    # Remove empty daily file
                    daily_path = self._daily_dir / _daily_filename(d)
                    if daily_path.exists():
                        daily_path.unlink()
                    del self._daily_cache[d]

        if promoted:
            await self.save()
            logger.info("Promoted %d daily memories to curated", promoted)
        return promoted

