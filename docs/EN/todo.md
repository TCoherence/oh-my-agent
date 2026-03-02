# Todo / Roadmap

## Snapshot (2026-03-01)

- `/search` is implemented.
- SkillSync reverse sync is implemented.
- CLI-first foundations are in place.
- v0.5 runtime-first is complete (including runtime hardening pass).
- Optional LLM router is implemented.
- Runtime observability baseline is implemented.
- Runtime live agent logging is implemented.
- Multi-type runtime is implemented (`artifact`, `repo_change`, `skill_change`).
- Adaptive memory is implemented (auto-extraction, injection, `/memories`, `/forget`).
- Date-based memory is implemented (daily/curated two-tier, auto-promotion, MEMORY.md synthesis, `/promote`).
- Image attachment support is implemented (Discord download, per-agent handling, temp file lifecycle).

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
- [x] Cross-agent skill delivery: unified SKILL.md format, SkillSync distributes to `.claude/`, `.gemini/`, and `.agents/skills/`; generated `AGENTS.md` summarizes repo rules and local Codex-visible skills
- [x] Codex integration: official repo/workspace `.agents/skills/` support plus generated `AGENTS.md`; reverse sync scans Claude/Gemini/Codex native skill dirs
- [x] Skill invocation vs mutation: `/skill-name` → normal chat path; "create skill" → `TASK_TYPE_SKILL_CHANGE` runtime task with dedicated prompt, validation, and auto-merge

### Adaptive Memory (complete)

- [x] `MemoryExtractor`: auto-extract memories after history compression (reuse existing agents)
- [x] File-system storage: YAML-backed, each memory = one-line summary + structured metadata (category, confidence, source_thread, observation_count)
- [x] Memory injection: select relevant memories and inject into agent prompt (token-budget aware, Jaccard similarity scoring)
- [x] `/memories` command: list extracted memories with confidence bar + category filter
- [x] `/forget` command: delete a specific memory by ID
- [x] Memory conflict resolution: Jaccard dedup (threshold 0.6) → merge with confidence boost; eviction by confidence × recency
- [x] Cross-agent sharing: memories belong to the user, shared across all agents (YAML file, not per-agent)

## v0.7 - Date-Based Memory + Human-in-the-Loop Ops Foundation

### Date-Based Memory (complete)

Upgrade adaptive memory from flat YAML to a date-organized, two-tier architecture inspired by [OpenClaw's memory system](https://docs.openclaw.ai/concepts/memory).

- [x] **Daily memory logs** (`memory/daily/YYYY-MM-DD.yaml`): append-only daily records. System loads today + yesterday at session start for recency.
- [x] **Curated long-term memory** (`memory/curated.yaml` + `memory/MEMORY.md`): promote stable, high-confidence memories into durable long-term storage. MEMORY.md is agent-synthesized natural language.
- [x] **Temporal decay scoring**: daily entries decay exponentially (configurable half-life). Curated entries are evergreen (no decay).
- [x] **Promotion lifecycle**: daily → curated when `observation_count ≥ N` and `confidence ≥ threshold` and age ≥ 1 day. Auto-promotion on load + manual `/promote` command.
- [x] **Pre-compaction memory flush**: memory extraction runs before history compression (order swapped), ensuring no memory loss.
- [x] **Discord commands**: `/memories` shows `[C]`/`[D]` tier tags, `/promote` for manual promotion.

### Ops Foundation

- [ ] Scheduler-driven operational tasks (connect `automations` to runtime task types)
- [ ] Event-driven triggers beyond cron (webhook ingestion, file-watch, external notifications)
- [ ] **Operator-facing doctor command**: add a Discord-first self-diagnostics entrypoint (`/doctor` or equivalent) that can report recent crash/failure state, startup health, log pointers, and recommended next checks without requiring direct server log access

### Human-in-the-Loop Runtime

- [ ] **First-class waiting state**: add a dedicated `WAITING_USER_INPUT` runtime state instead of overloading `BLOCKED` for every human handoff
- [ ] **Agent-initiated question surface**: let a running task ask the user a scoped question (with context and optional choices) directly in Discord
- [ ] **Structured answer binding**: bind the next user reply to the pending question and resume the task with explicit answer payloads instead of ad hoc free-text resume instructions
- [ ] **Mid-task approval checkpoints**: support explicit human checkpoints for risky or ambiguous steps without forcing the task into merge-only approval semantics

### Skill Evaluation

- [x] **Outcome tracking**: record chat-path skill invocation results (success/error/timeout/cancelled) with route source, latency, and usage telemetry
- [x] **User feedback signal**: thumbs-up/down reaction on the first attributed skill response → persisted per-invocation rating
- [x] **Skill health dashboard**: `/skill_stats [skill]` showing success rate, usage frequency, last invoked, average latency, and latest evaluation findings
- [x] **Auto-disable**: if a skill's failure rate exceeds threshold over a rolling window, remove it from automatic routing while preserving explicit `/skill-name`; `/skill_enable` clears the flag
- [x] **Duplicate-skill guard**: before auto-merging a new skill, compare its name/description/request against existing skills and force manual merge review when it substantially overlaps an existing capability
- [x] **Source-grounded skill evaluation**: when a skill task adapts an external repo/tool/reference, require source metadata and run a review pass before allowing auto-merge

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
- [ ] Revisit whether generated workspace `AGENTS.md` is still needed at all once the `.agents/skills/` migration settles
- [ ] Revisit agent turn-budget semantics: decide whether `max_turns` should remain exposed, clarify its boundary against `timeout` and runtime `max_steps`, and remove or document provider-specific gaps where the setting is not uniformly enforced
- [x] Codex / Gemini CLI session resume
- [ ] Feishu/Lark adapter
- [ ] Slack adapter
