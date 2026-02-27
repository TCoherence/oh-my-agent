# Todo / Roadmap

## Snapshot / 当前快照 (2026-02-27)

- ✅ `/search` is already implemented (SQLite FTS5 across all threads).
- ✅ SkillSync reverse sync is already implemented (`full_sync()` on startup).
- ✅ Core CLI-first foundations are in place (workspace isolation, env sanitization, scheduler, slash commands).
- 🎯 v0.5 is now **runtime-first**: durable autonomous task loops.

---

## v0.5.2 — Autonomous Runtime (Primary)

### Core Loop

- [x] **Runtime task state machine** — `DRAFT -> PENDING -> RUNNING -> VALIDATING -> WAITING_MERGE -> MERGED|MERGE_FAILED|DISCARDED` (+ `BLOCKED/FAILED/TIMEOUT/STOPPED/REJECTED`).
- [x] **Checkpoint + event persistence** — runtime tables in SQLite:
  - `runtime_tasks`
  - `runtime_task_checkpoints`
  - `runtime_task_events`
  - `runtime_task_decisions`
- [x] **Crash recovery baseline** — inflight tasks (`RUNNING/VALIDATING`) are re-queued to `PENDING` on startup.

### Autonomous Execution

- [x] **Per-task worktree isolation** — one git worktree per task under `~/.oh-my-agent/runtime/tasks/<task_id>`.
- [x] **Step loop execution** — code change -> test command -> retry until done or budget exhausted.
- [x] **Budget guards** — step budget + wall-time budget.
- [x] **Path guards** — default `allow_all_with_denylist` (`denied_paths` only, supports root docs/code edits).

### Approval Surface

- [x] **Decision model** — nonce-based task decisions (`approve/reject/suggest/merge/discard/request_changes`).
- [x] **Discord buttons (primary)** — Approve/Reject/Suggest + Merge/Discard/Request Changes.
- [x] **Slash fallback** — `/task_approve`, `/task_reject`, `/task_suggest`, `/task_merge`, `/task_discard`.
- [x] **Reaction policy** — reactions are status-only signals (`⏳`, `👀`, `🧪`, `✅`, `⚠️`, `🗑️`), not approval actions.

### Merge Gate / Cleanup / External Runtime

- [x] **Merge gate** — runtime completion lands in `WAITING_MERGE`, not direct apply.
- [x] **Merge execution** — patch from task worktree -> `git apply --check` -> apply -> auto commit to current branch.
- [x] **Strict merge guardrails** — owner-only, clean repo required, merge failure tracked as `MERGE_FAILED`.
- [x] **Externalized runtime paths** — workspace/memory/worktrees/logs default to `~/.oh-my-agent/...`.
- [x] **Legacy migration** — startup migrates `.workspace` to external layout with backup + marker.
- [x] **Janitor cleanup** — retention-based cleanup removes worktree artifacts and keeps DB audit metadata.
- [x] **Manual cleanup** — `/task_cleanup [task_id]` for immediate admin cleanup.
- [x] **Short conversation workspace TTL** — `/ask` thread artifacts stored in transient sub-workspaces and cleaned every 24h (state persisted in SQLite).

### Runtime Entry Points

- [x] **Message intent entry** — long-task intent can create runtime tasks.
- [x] **Scheduler entry** — scheduler jobs can enqueue runtime tasks when runtime is enabled.
- [x] **Manual slash entry** — `/task_start` supports explicit task creation.

### Remaining v0.5 Hardening

- [ ] **Task resume UX refinement** — richer unblock prompts and partial context replay.
- [ ] **Suggestion UX refinement** — regenerate draft/button surface cleanly after suggest.
- [ ] **Task output summarization** — structured completion summary (changed files, test outcome, next steps).
- [ ] **Runtime metrics** — per-task latency/step stats in logs.

---

## v0.6.0 — Multi-Agent Intelligence (After Runtime Stability)

- [ ] **Smart agent routing** — route by task profile instead of plain fallback.
- [ ] **Agent collaboration** — write/review and planner/executor pipelines.
- [ ] **Intent-based agent selection** — auto select model by query/task type.

---

## Backlog

- [ ] **Feishu/Lark adapter** (platform integration)
- [ ] **Slack adapter**
- [ ] **File attachment pipeline**
- [ ] **Markdown-aware chunking**
- [ ] **Rate limiting / request queue**
- [ ] **Docker-based agent isolation**
- [ ] **Semantic memory retrieval** (current `/search` is lexical FTS5)

---

## Maintenance

- [ ] `ruff` / formatting baseline
- [ ] type checking (`mypy` or `pyright`)
- [ ] GitHub Actions CI pipeline
