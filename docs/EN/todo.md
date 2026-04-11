# Todo / Roadmap

## Snapshot (2026-04-10)

- `/search` is implemented.
- SkillSync reverse sync is implemented.
- CLI-first foundations are in place.
- Current branch state is released `v0.7.3`.
- v0.5 runtime-first is complete (including runtime hardening pass).
- Optional LLM router is implemented.
- Runtime observability baseline is implemented.
- Runtime live agent logging is implemented.
- Multi-type runtime is implemented (`artifact`, `repo_change`, `skill_change`).
- `WAITING_USER_INPUT` and generic single-choice `ask_user` HITL are implemented.
- `repair_skill` router intent is implemented.
- Adaptive memory is implemented (auto-extraction, injection, `/memories`, `/forget`).
- Date-based memory is implemented (daily/curated two-tier, auto-promotion, MEMORY.md synthesis, `/promote`).
- Image attachment support is implemented (Discord download, per-agent handling, temp file lifecycle).
- Codex repo/workspace skill discovery now uses official `.agents/skills/`; generated workspace `AGENTS.md` is reduced to rules/metadata.
- `v0.7.3` is now fully implemented (phases 1–3).
- Current next target: `v0.8` (1.0 hardening). See `v1.0-plan.md` for full roadmap.

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
- [x] Cross-agent skill delivery: unified SKILL.md format, SkillSync distributes to `.claude/`, `.gemini/`, and `.agents/skills/`; generated `AGENTS.md` summarizes repo rules and workspace metadata
- [x] Codex integration: official repo/workspace `.agents/skills/` support plus generated `AGENTS.md` for rules/metadata; reverse sync scans Claude/Gemini/Codex native skill dirs
- [x] Skill invocation vs mutation: `/skill-name` → normal chat path; "create skill" → `TASK_TYPE_SKILL_CHANGE` runtime task with dedicated prompt, validation, and merge gate

### Adaptive Memory (complete)

- [x] `MemoryExtractor`: auto-extract memories after history compression (reuse existing agents)
- [x] File-system storage: YAML-backed, each memory = one-line summary + structured metadata (category, confidence, source_thread, observation_count)
- [x] Memory injection: select relevant memories and inject into agent prompt (token-budget aware, Jaccard similarity scoring)
- [x] `/memories` command: list extracted memories with confidence bar + category filter
- [x] `/forget` command: delete a specific memory by ID
- [x] Memory conflict resolution: Jaccard dedup (threshold 0.6) → merge with confidence boost; eviction by confidence × recency
- [x] Cross-agent sharing: memories belong to the user, shared across all agents (YAML file, not per-agent)

## v0.7 - Date-Based Memory + HITL/Ops Foundations

### Date-Based Memory (complete)

Upgrade adaptive memory from flat YAML to a date-organized, two-tier architecture inspired by [OpenClaw's memory system](https://docs.openclaw.ai/concepts/memory).

- [x] **Daily memory logs** (`memory/daily/YYYY-MM-DD.yaml`): append-only daily records. System loads today + yesterday at session start for recency.
- [x] **Curated long-term memory** (`memory/curated.yaml` + `memory/MEMORY.md`): promote stable, high-confidence memories into durable long-term storage. MEMORY.md is agent-synthesized natural language.
- [x] **Temporal decay scoring**: daily entries decay exponentially (configurable half-life). Curated entries are evergreen (no decay).
- [x] **Promotion lifecycle**: daily → curated when `observation_count ≥ N` and `confidence ≥ threshold` and age ≥ 1 day. Auto-promotion on load + manual `/promote` command.
- [x] **Pre-compaction memory flush**: memory extraction runs before history compression (order swapped), ensuring no memory loss.
- [x] **Discord commands**: `/memories` shows `[C]`/`[D]` tier tags, `/promote` for manual promotion.

### Human-in-the-Loop Baseline (complete)

- [x] **First-class waiting state**: `WAITING_USER_INPUT` is implemented for task and thread-level pauses
- [x] **Agent-initiated question surface**: generic single-choice `ask_user` challenges are supported on Discord
- [x] **Structured single-choice answers**: owner button responses are persisted and used to resume direct chat and runtime task flows
- [x] **Owner notifications and persistence**: `ask_user` prompts are stored in SQLite, rehydrated on restart, and emit visible owner notifications

### Skill Evaluation

- [x] **Outcome tracking**: record chat-path skill invocation results (success/error/timeout/cancelled) with route source, latency, and usage telemetry
- [x] **User feedback signal**: thumbs-up/down reaction on the first attributed skill response → persisted per-invocation rating
- [x] **Skill health dashboard**: `/skill_stats [skill]` showing success rate, usage frequency, last invoked, average latency, and latest evaluation findings
- [x] **Auto-disable**: if a skill's failure rate exceeds threshold over a rolling window, remove it from automatic routing while preserving explicit `/skill-name`; `/skill_enable` clears the flag
- [x] **Duplicate-skill guard**: before auto-merging a new skill, compare its name/description/request against existing skills and force manual merge review when it substantially overlaps an existing capability
- [x] **Source-grounded skill evaluation**: when a skill task adapts an external repo/tool/reference, require source metadata and run a review pass before allowing merge approval

## v0.7.3 - HITL Completion, Delivery, and Operator Observability

### Phase 1 (complete)
- [x] **Artifact delivery abstraction**: platform/runtime delivery layer now tries attachment upload first and falls back to local absolute paths when upload is unavailable or artifacts exceed local limits
- [x] **Thread-scoped unified logs**: `~/.oh-my-agent/runtime/logs/threads/<thread_id>.log` is now the main agent-audit surface across chat/invoke/runtime/HITL resume flows
- [x] **HITL completion semantics**: single-choice answer binding, structured resume-context injection, and checkpoint reuse on top of `WAITING_USER_INPUT` are implemented
- [x] **Operator-facing doctor command**: Discord `/doctor` now reports runtime, HITL, auth, scheduler, and log-pointer health snapshots

### Phase 2 — Automation State + Operator Surfaces + Delivery + Live Observability (complete)
- [x] **Automation runtime state persistence**: `automation_runtime_state` SQLite table with `last_run_at`, `last_success_at`, `last_error`, `last_task_id`, `next_run_at`; scheduler fire/complete/fail paths write state; persists across restarts; disabled automations have `next_run_at = NULL`
- [x] **Operator surfaces closeout**: `/doctor` now shows HITL waiting/resolving breakdown and recent automation failures; `/automation_status` shows persisted runtime state (last run, last success, next run, last error, last task ID) alongside definitions
- [x] **Skill timeout propagation**: automation YAML `skill_name` field propagates `metadata.timeout_seconds` into `max_minutes` for scheduler-fired artifact tasks
- [x] **Delivery closeout**: `_completed_text` unifies delivery info from `ArtifactDeliveryResult`; `deliver_files()` extracted as reusable core decoupled from `RuntimeTask`
- [x] **Live observability closeout**: running task status cards include bounded `Latest activity` from live agent log; buttons already enter stable disabled state on all terminal actions

### Phase 3 — HITL Checkpoint Semantics Closeout (complete)
- [x] **Checkpoint model normalization**: `HITL_CHOICES_APPROVAL` and `HITL_CHOICES_CONTINUE` standard choice families as internal constants; `WAITING_USER_INPUT` remains the unified waiting state
- [x] **Answer binding contract closeout**: answer payload includes `prompt_id`, `target_kind`, `question`, `choice_id`, `choice_label`, `choice_description`, `answered_at`; structured payload is the truth source, `[HITL Answer]` text block kept for agent compatibility
- [x] **Resume semantics closeout**: task HITL resumes to PENDING with structured + text payload; thread HITL auto-resumes with `last_hitl_answer` inheritance (latest only, no chain); cross-task isolation via `task_id`-scoped event queries
- [x] **Operator-visible HITL status**: `/task_logs` shows active/last HITL checkpoint question and selected answer

## v0.8 — 1.0 Hardening

Full details in [`v1.0-plan.md`](v1.0-plan.md).

### 1. Platform Abstraction
- [ ] Extract service-layer architecture from `discord.py` (platform-agnostic business logic)
- [ ] Task control service (highest priority — most commands, heaviest state logic)
- [ ] Ask service (core entry path)
- [ ] Doctor / automation / auth / memory services
- [ ] BaseChannel contract review: message edit, attachment upload, interactive prompt, etc.

### 2. Reliability Hardening
- [ ] Graceful shutdown contract (gateway, runtime workers, subprocesses, SQLite/WAL)
- [ ] Startup config validation (schema checks, fail-fast, CLI binary validation)
- [ ] Upgrade/migration contract (SQLite schema, config compat, skill/workspace path migrations)
- [ ] Markdown-aware chunking
- [ ] Rate-limit / request queue
- [ ] Concurrent thread/task isolation testing
- [ ] Log hygiene (rotation, log-level config, structured logging)
- [ ] User-visible error contract (readable messages, not tracebacks)
- [x] Missed-job policy = `skip` (implemented; needs documentation and test coverage)

### 3. Deployment Hardening
- [ ] First-class `docker-compose`
- [ ] Local vs Docker install/run documentation parity
- [ ] Runtime directories / backup / restore instructions
- [ ] Operator-facing restart and upgrade SOPs
- [ ] Health-check for long-running service mode

## v0.9 — 1.0 RC / Contract Freeze

- [ ] Finish remaining service-layer extraction
- [ ] Eliminate remaining adapter-owned business logic
- [ ] End-to-end restart/recovery tests (chat, skill invoke, runtime tasks, HITL, auth, automations)
- [ ] Validate upgrades from older state layouts
- [ ] Tighten docs into operator-grade product docs
- [ ] Defer or remove experimental surfaces not ready for long-term support

## Post-1.0 / 1.x

All items below move off the `1.0` critical path. See [`v1.0-plan.md`](v1.0-plan.md) for rationale.

### Platform Expansion
- [ ] Slack adapter
- [ ] Feishu/Lark adapter
- [ ] WeChat adapter

### Semantic Memory
- [ ] Semantic memory search (BM25 + vector hybrid)
- [ ] Chunking and indexing
- [ ] MMR diversity re-ranking

### Hybrid Autonomy
- [ ] Repeated-pattern detection from history (identify recurring workflows)
- [ ] Skill recommendation / auto-draft from recurring workflows
- [ ] Hybrid missions combining skill creation and scheduled execution
- [ ] Unified operator surface for active ops and skill-growth workflows

### Agent Quality Feedback
- [ ] Per-turn quality signal (reaction-based or `/rate` command)
- [ ] Agent selection feedback loop
- [ ] Skill-agent affinity

### Other Deferred
- [ ] Event-driven triggers beyond cron (webhook, file-watch, external notifications)
- [ ] Scheduler-driven operational tasks (connect automations to runtime task types)
- [ ] Guest session / tenant isolation (via `/guest` toggle or per-user config)
- [ ] Free-text HITL
- [ ] Remote object storage delivery (R2/S3 style)
- [ ] Richer automation scheduling model (RRULE or full cron semantics)

## Backlog (no version commitment)

- [ ] Live observability ring buffer + status-card live excerpt
- [ ] Delivery policy refinement (`inline summary`, attachment, link)
- [x] Docker-based agent isolation (host-mounted `/home`, repo-mounted `/repo`, config from repo, editable install on start, preinstalled CLI tools)
- [ ] Discord `/restart` operator command that triggers a host-managed container restart path (securely scoped, implementation detail TBD)
- [ ] Adaptive Memory encrypted storage + authenticated plaintext access
- [ ] Adaptive Memory edit permission control
- [ ] Revisit whether generated workspace `AGENTS.md` is still needed at all once the `.agents/skills/` migration settles
- [ ] Revisit agent turn-budget semantics: decide whether `max_turns` should remain exposed, clarify its boundary against `timeout` and runtime `max_steps`, and remove or document provider-specific gaps where the setting is not uniformly enforced
- [x] Codex / Gemini CLI session resume
- [ ] Add internal CLI agent lifecycle hooks (`pre-run`, `post-run`, `failure`, `resume`) for system-owned follow-up work such as reverse sync, artifact post-processing, and observability; keep this as an internal mechanism rather than a user-facing feature surface
- [ ] Skill feedback UX follow-up: allow reactions on any chunk of a multi-message skill result, and optionally emit a dedicated feedback prompt/message after a completed skill result; keep feedback scoped to completed skill outputs only, not auth/system/general chat messages
- [x] Persist automation runtime state (`last_run`, `next_run`, `last_error`) instead of recomputing everything after restart
- [x] Add operator automation controls such as `/automation_status`, `/automation_reload`, `/automation_enable`, and `/automation_disable` (Discord-only, owner-only, ephemeral MVP)
- [x] PRIORITY: propagate skill-level `metadata.timeout_seconds` into runtime task / automation execution so long-running automation-backed skills can inherit the same timeout override as direct skill invocations
- [x] Missed-job policy finalized as `skip` (no replay, no catch-up)
- [x] Operator-facing automation observability (`/automation_status` shows runtime state, `/doctor` shows recent failures)
