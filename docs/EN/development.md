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
- Optional LLM intent routing (`reply_once` vs `propose_task`)
- Short conversation transient workspaces with TTL cleanup persisted in SQLite
- Runtime observability baseline:
  - `/task_logs`
  - sampled progress snapshots in SQLite
  - full heartbeat in process logs
  - single updatable Discord status message

Still missing:
- true stop/pause/resume with subprocess interruption
- message-driven runtime control
- skill generation as a first-class runtime task type
- skill routing and validation loop
- ops/event autonomy remains future work

## Next Product Direction

- v0.5 delivered runtime-first foundations.
- v0.6 shifts to skill-first autonomy.
- v0.7 expands into ops-first and hybrid autonomy.
- Source-code self-modification is not the default autonomy path; it remains a high-risk, strongly gated capability.

## Historical Milestones

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
