# Development Log

## Project Overview

Oh My Agent is a multi-platform bot that uses CLI-based AI agents as the execution layer instead of direct model API calls. The architecture direction since v0.4 is CLI-first; API agents are deprecated.

## Source of Truth

1. `README.md` / `docs/CN/README.md`
2. `CHANGELOG.md`
3. `docs/EN/todo.md` / `docs/CN/todo.md`
4. `docs/archive/` for historical planning documents

## Current Runtime Baseline

Implemented:
- Optional LLM intent routing (`reply_once`, `invoke_existing_skill`, `propose_artifact_task`, `propose_repo_task`, `create_skill`, `repair_skill`)
- Short conversation transient workspaces with TTL cleanup persisted in SQLite
- Multi-type runtime orchestration:
  - `artifact` tasks complete without merge
  - `repo_change` and `skill_change` tasks continue through merge gate
- First-class `WAITING_USER_INPUT` plus single-choice `ask_user` flows for direct chat and runtime tasks
- Owner notifications, prompt persistence, and restart rehydration for active `ask_user` prompts
- Runtime observability baseline:
  - `/task_logs`
  - sampled progress snapshots in SQLite
  - full heartbeat in process logs
  - single updatable Discord status message
  - Discord `/doctor`
  - thread-scoped unified logs in `runtime/logs/threads/`
  - internal live agent spool logs in `runtime/logs/agents/`
- Codex skill integration now uses official repo/workspace `.agents/skills/`; generated workspace `AGENTS.md` is reduced to repo rules and metadata
- True subprocess interruption (heartbeat loop checks PAUSED/STOPPED, cancels running agent/test)
- Message-driven runtime control (`stop`, `pause`, `resume` from normal thread messages via `_parse_control_intent`)
- PAUSED state with workspace preservation and resumable with instruction
- Structured task completion summary (goal, files changed, test counts, timing)
- Runtime metrics (`total_agent_s`, `total_test_s`, `total_elapsed_s`)
- Adaptive memory: auto-extraction from conversations, injection into agent prompts, `/memories` and `/forget` commands
- Skill evaluation is implemented: outcome tracking, user feedback, stats, auto-disable, overlap guard, and source-grounded review
- Runtime skill repair routing is implemented via `repair_skill`

Still missing:
- in-memory live excerpt surfaced directly into Discord status cards
- remote delivery backends beyond local attachment/path fallback
- event-driven triggers beyond cron
- richer HITL families beyond structured single-choice checkpoints
- guest session isolation
- semantic retrieval (v0.8+)

## Next Product Direction

- Current branch state is released `v0.7.3`.
- v0.7.3 closes the HITL/delivery/operator loop on top of the v0.7.2 baseline.
- The next target is deferred items plus `v0.8+`, not another v0.7.3 phase.
- v0.5 delivered runtime-first foundations (complete).
- v0.6 delivered skill-first autonomy + adaptive memory.
- v0.7 delivered date-based memory, multi-type runtime, skill evaluation, and the current auth/HITL/runtime pass.
- v0.8+ adds semantic memory retrieval (vector search) and hybrid autonomy.
- Source-code self-modification is not the default autonomy path; it remains a high-risk, strongly gated capability.

## Historical Milestones

### v0.7.2 baseline + follow-up work

- Auth-first runtime pause/resume and generic Discord-first `ask_user`
- File-driven automations and current market/reporting workflows
- Multi-type runtime (`artifact`, `repo_change`, `skill_change`)
- `repair_skill` routing for feedback on existing skills
- Runtime live agent logging plus richer Discord status handling
- Codex repo/workspace `.agents/skills/` delivery with generated workspace `AGENTS.md` reduced to rules/metadata

### v0.7.3

- Artifact delivery abstraction with attachment-first delivery and local absolute-path fallback
- Thread-scoped unified agent logs across chat, invoke, runtime task, and HITL resume flows
- Structured HITL answer binding carried into task/thread resume context
- Discord `/doctor` for operator-facing gateway/runtime/HITL/auth/log health snapshots
- Persisted automation runtime state powering `/automation_status` and `/doctor`
- Skill `metadata.timeout_seconds` propagation through automation-backed execution
- Delivery closeout on `ArtifactDeliveryResult` without a parallel result type
- HITL checkpoint/resume closeout with recent-answer-only inheritance and no cross-task leakage
- Stable final Discord views for merge/discard/request changes and answered/cancelled HITL prompts

### v0.7.0

- Two-tier date-based memory system shipped (rewritten as Judge in v0.9.0)
- Manual promotion command and tier-promotion lifecycle (removed in v0.9.0)
- Image attachment support across Discord + Claude/Gemini/Codex
- Workspace refresh now regenerates synced skills and generated `AGENTS.md` together
- Codex repo/workspace skill delivery now uses official `.agents/skills/`
- Request-scoped observability improved across gateway replies and background memory/compression runs

### v0.6.1

- Codex CLI session resume
- Gemini CLI session resume
- Persisted CLI session restore after restart for all three CLI agents
- Resume hardening: stale persisted sessions are cleared more safely and synced correctly across fallback

### v0.6.0

- Adaptive memory: YAML-backed store with Jaccard dedup, confidence scoring, eviction
- Agent-powered memory extraction from conversation turns after compression (replaced by event-driven Judge in v0.9.0)
- Memory injection: `[Remembered context]` prepended to agent prompts
- Discord `/memories` (list with category filter) and `/forget` (delete by ID)
- Initial skill auto-approve and auto-merge prototype for skill tasks
- 189 tests passing

### v0.5.3

- PAUSED state: non-terminal, workspace preserved
- True subprocess interruption: heartbeat loop in `_invoke_agent` checks stop/pause and cancels agent
- Message-driven control: `_parse_control_intent(text)` detects stop/pause/resume from thread messages
- Suggestion UX: re-sends decision surface with suggestion text using new nonce
- Completion summary stored in `task.summary` (goal, files, test counts, timing)
- Runtime metrics: `total_agent_s`, `total_test_s`, `total_elapsed_s` in task events

### v0.5.2

- Durable runtime state machine
- Merge gate
- External runtime workspace layout
- Janitor cleanup
- Discord task commands and buttons

### v0.4.2

- Owner gate
- Scheduler MVP
- Delivery mode per job
- Scheduler skill

### v0.4.1

- `@agent` targeting in thread messages
- `/ask` agent override
- persisted session IDs
- resume across restart
- Codex compatibility hardening
- CLI error observability

### v0.4.0

- CLI-first cleanup
- Codex CLI agent
- SkillSync reverse sync
- Discord slash commands
- session resume support for Claude
- memory export/import
