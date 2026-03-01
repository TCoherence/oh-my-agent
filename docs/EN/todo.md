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

## v0.6 - Skill-First Autonomy + Adaptive Memory

### Skill-First (complete)

- [x] Promote skill creation to a first-class runtime task type
- [x] Add skill routing for "turn this workflow into a skill" requests
- [x] Add skill validation loop before merge
- [x] Add skill memory / provenance metadata
- [x] Cross-agent skill delivery: unified SKILL.md format, SkillSync distributes to `.claude/`, `.gemini/`, `.codex/` directories; AGENTS.md bridges Codex discovery
- [x] Codex integration: `AGENTS.md` + workspace `.codex/skills/`; reverse sync now scans all three CLI dirs
- [x] Skill invocation vs mutation: `/skill-name` → normal chat path; "create skill" → `TASK_TYPE_SKILL_CHANGE` runtime task with dedicated prompt, validation, and auto-merge

### Adaptive Memory (complete)

- [x] `MemoryExtractor`: auto-extract memories after history compression (reuse existing agents)
- [x] File-system storage: YAML-backed, each memory = one-line summary + structured metadata (category, confidence, source_thread, observation_count)
- [x] Memory injection: select relevant memories and inject into agent prompt (token-budget aware, Jaccard similarity scoring)
- [x] `/memories` command: list extracted memories with confidence bar + category filter
- [x] `/forget` command: delete a specific memory by ID
- [x] Memory conflict resolution: Jaccard dedup (threshold 0.6) → merge with confidence boost; eviction by confidence × recency
- [x] Cross-agent sharing: memories belong to the user, shared across all agents (YAML file, not per-agent)

## v0.7 - Date-Based Memory + Ops Foundation

### Date-Based Memory (core)

Upgrade adaptive memory from flat YAML to a date-organized, two-tier architecture inspired by [OpenClaw's memory system](https://docs.openclaw.ai/concepts/memory).

- [ ] **Daily memory logs** (`memory/YYYY-MM-DD.md`): append-only daily records capturing day-to-day observations, context, and session notes. System loads today + yesterday at session start for recency.
- [ ] **Curated long-term memory** (`MEMORY.md`): promote stable, high-confidence memories from daily logs into durable long-term storage. Decisions, preferences, and confirmed facts.
- [ ] **Temporal decay scoring**: recent memories score higher; older daily entries decay exponentially (configurable half-life). `MEMORY.md` entries are evergreen (no decay).
- [ ] **Promotion lifecycle**: daily → long-term when `observation_count ≥ N` and `confidence ≥ threshold` across multiple days. Auto-promote or agent-assisted curation.
- [ ] **Pre-compaction memory flush**: before context window compaction, trigger a silent turn reminding the agent to persist durable observations. Ensures no memory loss during long sessions.
- [ ] **Migration path**: auto-migrate existing `memories.yaml` entries into the new date-based format on first load.

### Ops Foundation

- [ ] Scheduler-driven operational tasks (connect `automations` to runtime task types)
- [ ] Event-driven triggers beyond cron (webhook ingestion, file-watch, external notifications)

### Skill Evaluation

- [ ] **Outcome tracking**: record skill invocation results (success/failure/timeout) in `skill_provenance`
- [ ] **User feedback signal**: thumbs-up/down reaction on skill output → persisted rating per skill
- [ ] **Skill health dashboard**: `/skill_stats` showing success rate, usage frequency, last invoked, average latency per skill
- [ ] **Auto-disable**: if a skill's failure rate exceeds threshold over a window, demote it from auto-invocation and notify the owner

### Guest Session (temp isolation)

- [ ] **Temp session mode**: flag a session as `guest` so it uses an isolated ephemeral memory scope (no writes to owner's adaptive memory, no skill mutation permissions)
- [ ] Configurable via `/guest` toggle or per-user config

## v0.8+ - Memory Intelligence + Hybrid Autonomy

### Semantic Memory

- [ ] **Semantic memory search**: vector-indexed retrieval over memory files (embedding-based `memory_search`), replacing Jaccard word-overlap. BM25 + vector hybrid for exact tokens + semantic paraphrases.
- [ ] **Chunking and indexing**: split memory files into semantic chunks (~400 tokens, 80 overlap), per-agent SQLite index, auto-reindex on file changes.
- [ ] **MMR diversity re-ranking**: when selecting memories to inject, balance relevance with diversity to avoid redundant near-duplicates from daily notes.

### Hybrid Autonomy

- [ ] Repeated-pattern detection from history (identify recurring workflows)
- [ ] Skill recommendation / auto-draft from recurring workflows
- [ ] Hybrid missions combining skill creation and scheduled execution
- [ ] Unified operator surface for active ops and skill-growth workflows

### Agent Quality Feedback

- [ ] **Per-turn quality signal**: reaction-based (thumbs-up/down) or explicit `/rate` command, persisted per `(thread, turn, agent)`
- [ ] **Agent selection feedback loop**: use accumulated quality signals to reweight agent fallback order or inform agent selection hints
- [ ] **Skill-agent affinity**: track which agent produces best results for which skill, inform auto-routing

## Backlog (no version commitment)

- [ ] Live observability ring buffer + status-card live excerpt
- [ ] Artifact delivery abstraction (`attachment first`, link fallback)
- [ ] Object-storage adapter for remote artifact delivery (R2/S3 style)
- [ ] Delivery policy abstraction (`inline summary`, attachment, link)
- [ ] Markdown-aware chunking for message delivery
- [ ] Rate limiting / request queue
- [ ] Docker-based agent isolation
- [ ] Adaptive Memory encrypted storage + authenticated plaintext access
- [ ] Adaptive Memory edit permission control
- [ ] Codex / Gemini CLI session resume
- [ ] Feishu/Lark adapter
- [ ] Slack adapter
