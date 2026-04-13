"""Date-based two-tier memory store — daily logs + curated long-term memories."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from oh_my_agent.memory.adaptive import (
    VALID_DURABILITY,
    VALID_EXPLICITNESS,
    VALID_SCOPES,
    VALID_STATUS,
    VALID_CATEGORIES,
    MemoryEntry,
    broadened_scope,
    eviction_score,
    find_duplicate,
    jaccard_similarity,
    lexical_match_kind,
    memory_bucket,
    memory_entry_from_dict,
    normalized_word_set,
    scope_matches,
    scope_score_multiplier,
    stronger_durability,
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

_MERGE_JUDGE_PROMPT = """\
You are a memory merge judge. Determine whether each candidate pair describes the same \
long-term memory, two related but distinct memories, or a contradiction.

Rules:
- Return ONLY JSON.
- decision must be one of: same_memory, related_but_distinct, contradictory.
- Use contradictory only when the new observation should replace or supersede the old one.
- Prefer same_memory for paraphrases of the same stable preference/workflow/project fact.

Pairs:
{pairs_text}

Output format:
[{{"pair_id":"p1","decision":"same_memory"}}]
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
    return [memory_entry_from_dict(d) for d in raw]


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
        self._last_retrieval_stats: dict[str, int] = {}

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
        await self._promote_eligible()

    async def save(self) -> None:
        """Persist curated and today's daily."""
        _save_yaml_list(self._curated_path, self._curated)
        today = _today()
        if today in self._daily_cache:
            _save_yaml_list(self._daily_dir / _daily_filename(today), self._daily_cache[today])

    def _save_daily_date(self, d: date) -> None:
        entries = self._daily_cache.get(d, [])
        path = self._daily_dir / _daily_filename(d)
        if entries:
            _save_yaml_list(path, entries)
        elif path.exists():
            path.unlink()

    def _ensure_all_daily_loaded(self) -> None:
        if not self._daily_dir.exists():
            return
        for yaml_file in sorted(self._daily_dir.glob("*.yaml")):
            try:
                d = date.fromisoformat(yaml_file.stem)
            except ValueError:
                continue
            self._load_daily(d)

    def _daily_date_for_memory(self, memory_id: str) -> date | None:
        for d, entries in self._daily_cache.items():
            for m in entries:
                if m.id == memory_id:
                    return d
        return None

    @staticmethod
    def _normalize_entry(entry: MemoryEntry) -> None:
        if entry.category not in VALID_CATEGORIES:
            entry.category = "fact"
        if entry.explicitness not in VALID_EXPLICITNESS:
            entry.explicitness = "inferred"
        if entry.status not in VALID_STATUS:
            entry.status = "active"
        if entry.scope not in VALID_SCOPES:
            entry.scope = "global_user"
        if entry.durability not in VALID_DURABILITY:
            entry.durability = "medium"
        entry.confidence = max(0.0, min(1.0, entry.confidence))
        entry.tier = "daily"
        if not entry.last_observed_at:
            entry.last_observed_at = entry.created_at
        if not entry.evidence:
            entry.evidence = ""

    @staticmethod
    def _merge_into(existing: MemoryEntry, entry: MemoryEntry) -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing.confidence = min(1.0, max(existing.confidence, entry.confidence) + 0.10)
        existing.observation_count += max(1, entry.observation_count)
        existing.last_referenced = now
        existing.last_observed_at = now
        if entry.explicitness == "explicit":
            existing.explicitness = "explicit"
        existing.scope = broadened_scope(existing.scope, entry.scope)
        existing.durability = stronger_durability(existing.durability, entry.durability)
        if entry.evidence and len(entry.evidence) >= len(existing.evidence):
            existing.evidence = entry.evidence
        for skill in entry.source_skills:
            if skill not in existing.source_skills:
                existing.source_skills.append(skill)
        if entry.source_workspace and not existing.source_workspace:
            existing.source_workspace = entry.source_workspace
        for t in entry.source_threads:
            if t not in existing.source_threads:
                existing.source_threads.append(t)

    def _active_curated(self) -> list[MemoryEntry]:
        return [m for m in self._curated if m.status == "active"]

    def _active_daily(self) -> list[MemoryEntry]:
        return [m for m in self._all_daily() if m.status == "active"]

    async def _judge_merge_candidates(
        self,
        pairs: list[dict],
        registry=None,
    ) -> dict[str, str]:
        if not pairs or registry is None:
            return {}
        lines = []
        for pair in pairs:
            lines.append(
                f"- pair_id={pair['pair_id']}\n"
                f"  new_summary: {pair['new_summary']}\n"
                f"  existing_summary: {pair['existing_summary']}"
            )
        prompt = _MERGE_JUDGE_PROMPT.format(pairs_text="\n".join(lines))
        try:
            _agent, response = await registry.run(prompt, run_label="memory_merge_judge")
        except Exception as exc:
            logger.warning("memory_merge candidate judge failed: %s", exc)
            return {}
        if response.error:
            logger.warning("memory_merge candidate judge returned error: %s", response.error)
            return {}
        try:
            data = json.loads(response.text.strip())
        except json.JSONDecodeError:
            logger.warning("memory_merge candidate judge returned invalid JSON")
            return {}
        result: dict[str, str] = {}
        if not isinstance(data, list):
            return result
        for item in data:
            if not isinstance(item, dict):
                continue
            pair_id = str(item.get("pair_id", ""))
            decision = str(item.get("decision", ""))
            if pair_id and decision in {"same_memory", "related_but_distinct", "contradictory"}:
                result[pair_id] = decision
        return result

    def _prune_to_max_memories(self) -> None:
        all_entries: list[tuple[str, date | None, MemoryEntry]] = [
            ("curated", None, m) for m in self._curated
        ]
        for d, entries in self._daily_cache.items():
            for m in entries:
                all_entries.append(("daily", d, m))
        if len(all_entries) <= self._max_memories:
            return
        ranked = sorted(
            all_entries,
            key=lambda item: (
                1 if item[2].tier == "curated" else 0,
                1 if item[2].status == "active" else 0,
                eviction_score(item[2], self._decay_half_life_days),
            ),
            reverse=True,
        )
        keep_ids = {entry.id for _, _, entry in ranked[: self._max_memories]}
        self._curated = [m for m in self._curated if m.id in keep_ids]
        for d, entries in list(self._daily_cache.items()):
            self._daily_cache[d] = [m for m in entries if m.id in keep_ids]

    async def add_memories(self, entries: list[MemoryEntry], registry=None, req_id: str | None = None) -> int:
        """Add memories to today's daily log. Dedup against curated + daily."""
        async with self._lock:
            added = 0
            req_prefix = f"[{req_id}] " if req_id else ""
            today = _today()
            if today not in self._daily_cache:
                self._daily_cache[today] = []
            today_entries = self._daily_cache[today]
            self._ensure_all_daily_loaded()
            dirty_daily_dates: set[date] = set()
            curated_changed = False

            incoming: list[MemoryEntry] = []
            for entry in entries:
                self._normalize_entry(entry)
                merged = False
                for existing in incoming:
                    if lexical_match_kind(entry.summary, existing.summary) == "same_memory":
                        self._merge_into(existing, entry)
                        merged = True
                        break
                if not merged:
                    incoming.append(entry)

            candidate_pairs: list[dict] = []
            for i, entry in enumerate(incoming):
                for existing in self._active_curated() + self._active_daily():
                    match_kind = lexical_match_kind(entry.summary, existing.summary)
                    if match_kind == "candidate":
                        candidate_pairs.append(
                            {
                                "pair_id": f"p{i}-{existing.id}",
                                "new_index": i,
                                "existing_id": existing.id,
                                "existing_summary": existing.summary,
                                "new_summary": entry.summary,
                            }
                        )
            decisions = await self._judge_merge_candidates(candidate_pairs, registry=registry)
            same_count = 0
            distinct_count = 0
            contradictory_count = 0

            for i, entry in enumerate(incoming):
                pool = self._active_curated() + self._active_daily()
                direct_same = None
                candidate_hits: list[tuple[float, str, MemoryEntry]] = []
                for existing in pool:
                    match_kind = lexical_match_kind(entry.summary, existing.summary)
                    score = jaccard_similarity(
                        normalized_word_set(entry.summary),
                        normalized_word_set(existing.summary),
                    )
                    if match_kind == "same_memory":
                        direct_same = existing
                        break
                    if match_kind == "candidate":
                        pair_id = f"p{i}-{existing.id}"
                        decision = decisions.get(pair_id)
                        if decision is None:
                            decision = "same_memory" if score >= 0.60 else "related_but_distinct"
                        candidate_hits.append((score, decision, existing))

                if direct_same is not None:
                    self._merge_into(direct_same, entry)
                    same_count += 1
                    if direct_same.tier == "curated":
                        curated_changed = True
                    else:
                        daily_date = self._daily_date_for_memory(direct_same.id)
                        if daily_date is not None:
                            dirty_daily_dates.add(daily_date)
                    continue

                candidate_hits.sort(key=lambda item: item[0], reverse=True)
                same_candidate = next((existing for _, decision, existing in candidate_hits if decision == "same_memory"), None)
                contradictory_candidate = next(
                    (existing for _, decision, existing in candidate_hits if decision == "contradictory"),
                    None,
                )

                if same_candidate is not None:
                    self._merge_into(same_candidate, entry)
                    same_count += 1
                    if same_candidate.tier == "curated":
                        curated_changed = True
                    else:
                        daily_date = self._daily_date_for_memory(same_candidate.id)
                        if daily_date is not None:
                            dirty_daily_dates.add(daily_date)
                    continue

                if contradictory_candidate is not None:
                    contradictory_candidate.status = "superseded"
                    contradictory_candidate.last_observed_at = datetime.now(timezone.utc).isoformat()
                    contradictory_count += 1
                    if contradictory_candidate.tier == "curated":
                        curated_changed = True
                    else:
                        daily_date = self._daily_date_for_memory(contradictory_candidate.id)
                        if daily_date is not None:
                            dirty_daily_dates.add(daily_date)

                today_entries.append(entry)
                added += 1
                distinct_count += 1
                dirty_daily_dates.add(today)

            # Prune below min_confidence from daily tier only.
            for d, entries_for_day in list(self._daily_cache.items()):
                filtered = [m for m in entries_for_day if m.confidence >= self._min_confidence]
                if len(filtered) != len(entries_for_day):
                    self._daily_cache[d] = filtered
                    dirty_daily_dates.add(d)

            curated_ids_before_prune = {m.id for m in self._curated}
            self._prune_to_max_memories()
            if {m.id for m in self._curated} != curated_ids_before_prune:
                curated_changed = True
            for d in list(self._daily_cache.keys()):
                dirty_daily_dates.add(d)

            if curated_changed:
                _save_yaml_list(self._curated_path, self._curated)
            for d in sorted(dirty_daily_dates):
                self._save_daily_date(d)

            logger.info(
                "%smemory_merge candidate_count=%d same_count=%d distinct_count=%d contradictory_count=%d",
                req_prefix,
                len(candidate_pairs),
                same_count,
                distinct_count,
                contradictory_count,
            )

            await self._promote_eligible(req_id=req_id)
            return added

    async def get_relevant(
        self,
        context: str,
        budget_chars: int = 500,
        *,
        skill_name: str | None = None,
        thread_id: str | None = None,
        workspace: str | None = None,
        thread_topic: str | None = None,
    ) -> list[MemoryEntry]:
        """Return memories relevant to context using scope-aware bucketed ranking."""
        all_mems = [m for m in self.memories if m.status == "active"]
        self._last_retrieval_stats = {
            "filtered_superseded_count": len([m for m in self.memories if m.status != "active"]),
        }
        if not all_mems:
            return []

        query_text = "\n".join(
            part for part in [context, thread_topic or "", skill_name or ""] if part
        ).strip()
        context_words = normalized_word_set(query_text)

        def _score(m: MemoryEntry) -> float:
            multiplier = scope_score_multiplier(
                m,
                skill_name=skill_name,
                thread_id=thread_id,
                workspace=workspace,
            )
            if multiplier <= 0:
                return 0.0
            sim = jaccard_similarity(context_words, normalized_word_set(m.summary))
            base = sim * m.confidence * multiplier
            if m.tier == "daily":
                age = _age_days(m.created_at)
                base *= _decay_factor(age, self._decay_half_life_days)
            return base

        buckets = {
            "skill_scoped": [],
            "workspace_project": [],
            "global_preference": [],
            "recent_daily": [],
        }
        for memory in all_mems:
            score = _score(memory)
            if score <= 0:
                continue
            if memory.scope != "global_user" and not scope_matches(
                memory,
                skill_name=skill_name,
                thread_id=thread_id,
                workspace=workspace,
            ):
                continue
            buckets[memory_bucket(memory)].append((score, memory))

        for values in buckets.values():
            values.sort(key=lambda item: item[0], reverse=True)

        bucket_limits = {
            "skill_scoped": 2,
            "workspace_project": 2,
            "global_preference": 2,
            "recent_daily": 2,
        }
        selected: list[MemoryEntry] = []
        selected_ids: set[str] = set()
        chars_used = 0
        selected_counts: dict[str, int] = {}
        for bucket_name in ("skill_scoped", "workspace_project", "global_preference", "recent_daily"):
            taken = 0
            for _score_val, memory in buckets[bucket_name]:
                if taken >= bucket_limits[bucket_name]:
                    break
                if memory.id in selected_ids:
                    continue
                entry_chars = len(memory.summary) + 10
                if chars_used + entry_chars > budget_chars and selected:
                    break
                selected.append(memory)
                selected_ids.add(memory.id)
                chars_used += entry_chars
                taken += 1
            selected_counts[bucket_name] = taken

        self._last_retrieval_stats.update(
            {
                "selected_count": len(selected),
                "selected_skill_scoped": selected_counts.get("skill_scoped", 0),
                "selected_workspace_project": selected_counts.get("workspace_project", 0),
                "selected_global_preference": selected_counts.get("global_preference", 0),
                "selected_recent_daily": selected_counts.get("recent_daily", 0),
            }
        )
        return selected

    @property
    def last_retrieval_stats(self) -> dict[str, int]:
        return dict(self._last_retrieval_stats)

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
                    self._save_daily_date(d)
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
                        if m.scope == "thread" or m.durability == "ephemeral":
                            return False
                        dup = find_duplicate(m.summary, self._active_curated(), threshold=0.75)
                        if dup is not None:
                            self._merge_into(self._active_curated()[dup], m)
                        else:
                            m.tier = "curated"
                            m.last_observed_at = datetime.now(timezone.utc).isoformat()
                            self._curated.append(m)
                        entries.pop(i)
                        self._save_daily_date(d)
                        _save_yaml_list(self._curated_path, self._curated)
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
            if m.status != "active":
                continue
            if m.scope == "thread" or m.durability == "ephemeral":
                continue
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
            _agent, response = await registry.run(prompt, run_label="memory_md_synthesis")
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

    async def _promote_eligible(self, req_id: str | None = None) -> int:
        """Scan ALL daily files and promote entries that meet thresholds."""
        promoted = 0
        changed = False
        req_prefix = f"[{req_id}] " if req_id else ""
        fast_path_count = 0
        slow_path_count = 0
        skipped: dict[str, int] = {}

        # Scan all daily files on disk (not just cached ones)
        self._ensure_all_daily_loaded()

        # Now check all cached daily entries for promotion
        for d in list(self._daily_cache.keys()):
            entries = self._daily_cache[d]
            to_promote = []
            remaining = []
            for m in entries:
                source_diverse = len(set(m.source_threads)) >= 2
                fast_path = (
                    m.explicitness == "explicit"
                    and m.status == "active"
                    and m.scope != "thread"
                    and m.durability != "ephemeral"
                    and m.confidence >= 0.85
                    and m.observation_count >= 2
                    and m.category in {"preference", "workflow", "project_knowledge"}
                )
                slow_path = (
                    m.explicitness == "inferred"
                    and m.status == "active"
                    and m.scope != "thread"
                    and m.durability != "ephemeral"
                    and m.confidence >= 0.80
                    and (m.observation_count >= 3 or source_diverse)
                )
                if fast_path or slow_path:
                    dup = find_duplicate(m.summary, self._active_curated(), threshold=0.75)
                    if dup is not None:
                        # Merge into curated
                        existing = self._active_curated()[dup]
                        self._merge_into(existing, m)
                        changed = True
                    else:
                        m.tier = "curated"
                        m.last_observed_at = datetime.now(timezone.utc).isoformat()
                        to_promote.append(m)
                    if fast_path:
                        fast_path_count += 1
                    else:
                        slow_path_count += 1
                else:
                    reason = "status"
                    if m.status != "active":
                        reason = "inactive"
                    elif m.explicitness == "explicit" and m.confidence < 0.85:
                        reason = "fast_confidence"
                    elif m.explicitness == "explicit" and m.observation_count < 2:
                        reason = "fast_observations"
                    elif m.explicitness == "inferred" and m.confidence < 0.80:
                        reason = "slow_confidence"
                    elif m.explicitness == "inferred" and m.observation_count < 3 and not source_diverse:
                        reason = "slow_diversity"
                    skipped[reason] = skipped.get(reason, 0) + 1
                    remaining.append(m)

            if to_promote or len(remaining) != len(entries):
                self._curated.extend(to_promote)
                self._daily_cache[d] = remaining
                promoted += len(to_promote)
                changed = True
                # Save updated daily file
                if remaining:
                    _save_yaml_list(self._daily_dir / _daily_filename(d), remaining)
                else:
                    # Remove empty daily file
                    daily_path = self._daily_dir / _daily_filename(d)
                    if daily_path.exists():
                        daily_path.unlink()
                    del self._daily_cache[d]

        if changed:
            self._prune_to_max_memories()
            _save_yaml_list(self._curated_path, self._curated)
            for d in list(self._daily_cache.keys()):
                self._save_daily_date(d)
            self._needs_synthesis = True
            if promoted:
                logger.info("Promoted %d daily memories to curated", promoted)
            else:
                logger.info("Updated curated memories from eligible daily observations")
        logger.info(
            "%smemory_promote fast_path_count=%d slow_path_count=%d skipped_reason=%s",
            req_prefix,
            fast_path_count,
            slow_path_count,
            ",".join(f"{k}:{v}" for k, v in sorted(skipped.items())) or "-",
        )
        return promoted
