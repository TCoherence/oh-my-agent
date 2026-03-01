# Development Log

## Project Overview

Oh My Agent is a multi-platform bot that uses CLI-based AI agents as the execution layer instead of direct model API calls. The architecture direction since v0.4 is CLI-first; API agents are deprecated.

## Source of Truth

1. `README.md` / `docs/CN/README.md`
2. `docs/EN/todo.md` / `docs/CN/todo.md`
3. `docs/EN/v0.5_runtime_plan.md` / `docs/CN/v0.5_runtime_plan.md`
4. `docs/archive/future_planning_discussion.md` for historical rationale

## Current Runtime Baseline

Implemented:
- Optional LLM intent routing (`reply_once`, `invoke_existing_skill`, `propose_artifact_task`, `propose_repo_task`, `create_skill`)
- Short conversation transient workspaces with TTL cleanup persisted in SQLite
- Multi-type runtime orchestration:
  - `artifact` tasks complete without merge
  - `repo_change` and `skill_change` tasks continue through merge gate
- Runtime observability baseline:
  - `/task_logs`
  - sampled progress snapshots in SQLite
  - full heartbeat in process logs
  - single updatable Discord status message
  - separate underlying agent logs in `runtime/logs/agents/`
- True subprocess interruption (heartbeat loop checks PAUSED/STOPPED, cancels running agent/test)
- Message-driven runtime control (`stop`, `pause`, `resume` from normal thread messages via `_parse_control_intent`)
- PAUSED state with workspace preservation and resumable with instruction
- Structured task completion summary (goal, files changed, test counts, timing)
- Runtime metrics (`total_agent_s`, `total_test_s`, `total_elapsed_s`)
- Adaptive memory: auto-extraction from conversations, injection into agent prompts, `/memories` and `/forget` commands

Still missing:
- in-memory live ring buffer and status-card live excerpt for running tasks
- artifact delivery adapter (`attachment first`, link fallback)
- stronger Codex skill integration strategy beyond current global-skills / `AGENTS.md` tradeoff
- date-based memory organization with semantic retrieval (planned for v0.7)
- ops/event autonomy remains future work

## Next Product Direction

- v0.5 delivered runtime-first foundations (complete).
- v0.6 shifts to skill-first autonomy + adaptive memory (memory done, skills in progress).
- v0.7 upgrades memory to date-based architecture + ops-first and hybrid autonomy.
- Source-code self-modification is not the default autonomy path; it remains a high-risk, strongly gated capability.

## Historical Milestones

### v0.6.0

- Adaptive memory: YAML-backed store with Jaccard dedup, confidence scoring, eviction
- `MemoryExtractor`: agent-powered extraction from conversation turns after compression
- Memory injection: `[Remembered context]` prepended to agent prompts
- Discord `/memories` (list with category filter) and `/forget` (delete by ID)
- Skill auto-approve and auto-merge for skill tasks
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
