# Development Log

## Project Overview

Oh My Agent is a multi-platform bot that uses CLI-based AI agents as the execution layer instead of direct model API calls. The architecture direction since v0.4 is CLI-first; API agents are deprecated.

## Source of Truth

1. `README_EN.md` / `README_CN.md`
2. `docs/todo_EN.md` / `docs/todo_CN.md`
3. `docs/v0.5_runtime_plan_EN.md` / `docs/v0.5_runtime_plan_CN.md`
4. `docs/future_planning_discussion.md` for historical rationale

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
