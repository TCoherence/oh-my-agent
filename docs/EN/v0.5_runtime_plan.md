# v0.5 Runtime Plan

## Historical Note

This document captures the v0.5 runtime-first phase. The next product phase moves toward skill-first autonomy, and ops-first / hybrid autonomy are planned for v0.7.

## Goal

Build a durable autonomous task runtime so the bot can continue long coding loops without waiting for user messages at every step.

## Runtime Model

Task states:
- `DRAFT`
- `PENDING`
- `RUNNING`
- `VALIDATING`
- `APPLIED` (legacy)
- `WAITING_MERGE`
- `MERGED`
- `MERGE_FAILED`
- `DISCARDED`
- `BLOCKED`
- `FAILED`
- `TIMEOUT`
- `STOPPED`
- `REJECTED`

SQLite runtime tables:
- `runtime_tasks`
- `runtime_task_checkpoints`
- `runtime_task_events`
- `runtime_task_decisions`

Inflight tasks (`RUNNING`, `VALIDATING`) are re-queued to `PENDING` on restart.

## Entry Points

1. Message intent can auto-create runtime tasks.
2. Scheduler jobs can enqueue runtime tasks.
3. `/task_start` creates tasks explicitly.
4. Optional LLM router can propose runtime tasks before heuristic intent checks.

## Risk Gating

Default profile is `strict`.

Low-risk tasks auto-run only when all constraints hold. Otherwise they start in `DRAFT` and require approval.

## Decision Surface

- Primary: Discord buttons (`Approve`, `Reject`, `Suggest`, `Merge`, `Discard`, `Request Changes`)
- Fallback: slash commands
- Decision nonce is one-time and TTL-bound
- Reactions are status-only signals
- Runtime progress should prefer updating a single status message instead of posting many messages

## Loop Contract

Each step:
1. Build runtime prompt with goal, step index, prior failure, and resume instruction.
2. Run agent in per-task git worktree.
3. Validate changed paths.
4. Run the configured test command.
5. Persist checkpoint and event.
6. Transition state based on `TASK_STATE`, test result, and budget.
7. If execution succeeds, enter `WAITING_MERGE`.

## Observability

- `/task_logs` shows recent runtime events and output tails.
- Full heartbeat stays in process logs.
- SQLite stores sampled progress events (`task.agent_progress`, `task.test_progress`) instead of every heartbeat.
- Discord prefers a single updatable status message.

## Commands

- `/task_start`
- `/task_status`
- `/task_list`
- `/task_approve`
- `/task_reject`
- `/task_suggest`
- `/task_resume`
- `/task_stop`
- `/task_merge`
- `/task_discard`
- `/task_changes`
- `/task_logs`
- `/task_cleanup`

## Known Gaps

- stop/resume is not yet message-driven
- current `stop` is not yet a hard interrupt of the active subprocess
- skill generation is not yet a first-class runtime task type
