# Todo / Roadmap

## Snapshot (2026-02-28)

- `/search` is implemented.
- SkillSync reverse sync is implemented.
- CLI-first foundations are in place.
- v0.5 runtime-first is complete (including runtime hardening pass).
- Optional LLM router is implemented.
- Runtime observability baseline is implemented.
- Runtime live agent logging is implemented.
- Multi-type runtime is implemented (`artifact`, `repo_change`, `skill_change`).
- Adaptive memory is implemented (auto-extraction, injection, `/memories`, `/forget`).

## v0.5 Runtime Hardening (complete)

- [x] Runtime task state machine
- [x] SQLite checkpoints/events/decisions
- [x] Crash recovery baseline
- [x] Per-task worktree isolation
- [x] Step loop execution
- [x] Budget guards
- [x] Allow-all-with-denylist path policy
- [x] Discord buttons + slash fallback
- [x] Merge gate
- [x] External runtime paths + migration
- [x] Janitor cleanup
- [x] Short conversation workspace TTL
- [x] Optional LLM router draft-confirm flow
- [x] `/task_logs` + sampled progress persistence + status message upsert
- [x] True runtime interruption for running agent/test subprocesses
- [x] Message-driven runtime control (`stop`, `pause`, `resume` from normal thread messages)
- [x] Resume UX refinement
- [x] Suggestion UX refinement
- [x] Structured task completion summary
- [x] Runtime metrics and latency stats
- [x] Clear paused/interrupted semantics in the state model
- [ ] Live observability upgrade: per-task ring buffer + status-card live excerpt

## v0.6 - Skill-First Autonomy + Adaptive Memory

- [x] Promote skill creation to a first-class runtime task type
- [x] Add skill routing for "turn this workflow into a skill" requests
- [x] Add skill validation loop before merge
- [ ] Add cross-agent skill abstraction
- [ ] Lock the Codex integration tradeoff (`global skills + AGENTS.md/MCP`, not assumed project-native skills)
- [x] Add skill memory / provenance metadata
- [ ] Keep `mission` model and operator surface as enabling architecture for skill autonomy
- [ ] Keep thread-native runtime control as supporting work, not the headline
- [ ] Separate skill invocation from skill mutation across all agents

### Adaptive Memory (complete)

Automatically extract and accumulate user preferences and project knowledge from conversations, building a persistent user profile across sessions.

- [x] `MemoryExtractor`: auto-extract memories after history compression (reuse existing agents)
- [x] File-system storage: YAML-backed, each memory = one-line summary + structured metadata (category, confidence, source_thread, observation_count)
- [x] Memory injection: select relevant memories and inject into agent prompt (token-budget aware, Jaccard similarity scoring)
- [x] `/memories` command: list extracted memories with confidence bar + category filter
- [x] `/forget` command: delete a specific memory by ID
- [x] Memory conflict resolution: Jaccard dedup (threshold 0.6) → merge with confidence boost; eviction by confidence × recency
- [x] Cross-agent sharing: memories belong to the user, shared across all agents (YAML file, not per-agent)

## v0.7 - Date-Based Memory + Ops-First Autonomy

### Date-Based Memory Upgrade

Upgrade adaptive memory from flat YAML to a date-organized, two-tier architecture inspired by [OpenClaw's memory system](https://docs.openclaw.ai/concepts/memory).

- [ ] **Daily memory logs** (`memory/YYYY-MM-DD.md`): append-only daily records capturing day-to-day observations, context, and session notes. System loads today + yesterday at session start for recency.
- [ ] **Curated long-term memory** (`MEMORY.md`): promote stable, high-confidence memories from daily logs into durable long-term storage. Decisions, preferences, and confirmed facts.
- [ ] **Temporal decay scoring**: recent memories score higher; older daily entries decay exponentially (configurable half-life). `MEMORY.md` entries are evergreen (no decay).
- [ ] **Promotion lifecycle**: daily → long-term when `observation_count ≥ N` and `confidence ≥ threshold` across multiple days. Auto-promote or agent-assisted curation.
- [ ] **Semantic memory search**: vector-indexed retrieval over memory files (embedding-based `memory_search`), replacing current Jaccard word-overlap. BM25 + vector hybrid for exact tokens + semantic paraphrases.
- [ ] **Chunking and indexing**: split memory files into semantic chunks (~400 tokens, 80 overlap), per-agent SQLite index, auto-reindex on file changes.
- [ ] **Pre-compaction memory flush**: before context window compaction, trigger a silent turn reminding the agent to persist durable observations. Ensures no memory loss during long sessions.
- [ ] **MMR diversity re-ranking**: when selecting memories to inject, balance relevance with diversity to avoid redundant near-duplicates from daily notes.
- [ ] **Migration path**: auto-migrate existing `memories.yaml` entries into the new date-based format on first load.

### Ops-First and Hybrid Autonomy

- [ ] Scheduler-driven operational tasks
- [ ] Event-driven triggers beyond cron
- [ ] Repeated-pattern detection from history
- [ ] Skill recommendation / auto-draft from recurring workflows
- [ ] Hybrid missions combining skill creation and scheduled execution
- [ ] Unified operator surface for active ops and skill-growth workflows

## OpenClaw Gap Summary

Current positioning is still closer to a Discord-native coding runtime than to a general assistant control plane.

Main gaps relative to OpenClaw:

- The strongest gap is not coding runtime alone, but autonomy layering above it.
- The current system is runtime-first; the next needed layer is skill-first autonomy.
- Ops-first and hybrid autonomy should follow later, not compete with v0.6 priorities.
- Skills as platform capability are now partially first-class, but invocation semantics and Codex compatibility still need consolidation.
- Operator surface and artifact model still need to mature for sustained autonomous work.
- Memory system is functional but lacks date-based organization and semantic retrieval (planned for v0.7).

Recommended next architecture step:

- Use runtime-first infrastructure as the base layer.
- Make skill-first autonomy the primary v0.6 product focus.
- Treat `mission`, operator surface, and thread-native control as enabling architecture rather than the headline.
- Upgrade memory to date-based + semantic search in v0.7 alongside ops autonomy.

## Backlog

- [ ] Feishu/Lark adapter
- [ ] Slack adapter
- [ ] Artifact delivery abstraction (`attachment first`, link fallback)
- [ ] Object-storage adapter for remote artifact delivery (R2/S3 style)
- [ ] Delivery policy abstraction (`inline summary`, attachment, link`)
- [ ] Markdown-aware chunking
- [ ] Rate limiting / request queue
- [ ] Docker-based agent isolation
- [ ] Adaptive Memory encrypted storage + authenticated plaintext access
- [ ] Adaptive Memory edit permission control (prevent accidental user edits)
- [ ] Codex / Gemini CLI session resume
