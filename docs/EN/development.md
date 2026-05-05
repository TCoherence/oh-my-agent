# Development Log

## Project Overview

Oh My Agent is a multi-platform bot that uses CLI-based AI agents as the execution layer instead of direct model API calls. The architecture direction since v0.4 is CLI-first; API agents are deprecated.

## Source of Truth

1. `README.md` / `docs/CN/README.md`
2. `CHANGELOG.md`
3. `docs/EN/todo.md` / `docs/CN/todo.md`
4. `docs/archive/` for historical planning documents

## Current Runtime Baseline

Released: `v0.9.5` (2026-05-04). Branch is in v1.0 contract-freeze: nine major subsystems plus a config layer (see [`architecture.md`](architecture.md)). Memory was rewritten as the event-driven Judge model in v0.9.0 (see Migration in CHANGELOG); platform abstraction completed in v0.8 with service extraction; Slack stub was removed in v0.9.1 (Discord is the single supported platform for 1.0). Subsequent v0.9.x releases hardened streaming, push notifications, the central scheduler due-loop, dump channels, dashboard, weekly reflection, cost charting, and the runtime cwd / cached-credential / completion-body fixes that PR #41 / #44 surfaced via the new harness.

Implemented (v0.9.5 surface):
- **Gateway**: Discord adapter, slash commands, message routing, `@agent` targeting, image attachments, dump-channel routing for automation completions, mention-peek push notifications.
- **Agents**: CLI subprocess wrappers (`claude`, `gemini`, `codex`) with fallback registry; persisted session resume across restart for all three; cwd-keyed session storage matching real CLI semantics.
- **Memory**: SQLite history + event-driven Judge agent writing single-tier `memories.yaml` + agent-synthesized `MEMORY.md`; idle / `/memorize` / keyword triggers; daily diary reflection (default-on as of v0.9.5) + weekly reflection (Tuesday 03:00 local default).
- **Runtime**: durable state machine (DRAFT / RUNNING / VALIDATING / WAITING_MERGE / WAITING_USER_INPUT / COMPLETED / FAILED / TIMEOUT / PAUSED / STOPPED / BLOCKED); per-task worktree; true subprocess interruption; message-driven control (`stop`/`pause`/`resume` from natural language); HITL `ask_user` checkpoints with answer binding + restart rehydration; cached-credential injection into task + chat-reply prompts (PR #41); single published-artifact path under `runtime.reports_dir/` with no flat-copy duplication; COMPLETED post-notify watermark (callers reading `status=COMPLETED` are guaranteed the channel message landed).
- **Skills**: bidirectional sync between `skills/` and CLI-native dirs; agent-driven creation + validation + merge gate; outcome tracking, feedback reactions, auto-disable on rolling failure, overlap guard, source-grounded review.
- **Router**: optional OpenAI-compatible LLM intent classification (5 canonical intents: `chat_reply` / `invoke_skill` / `oneoff_artifact` / `propose_repo_change` / `update_skill`) with confidence threshold + heuristic fallback.
- **Automation**: cron / interval scheduler with central due-loop (v0.9.4); per-automation `auto_approve`; reply-to-automation-post promotes to follow-up thread; persisted runtime state (`/automation_status` survives restart).
- **Auth**: per-provider QR flow (bilibili shipped); cached credential reuse via `get_valid_credential` + auto-injected `--cookies-path` hints (PR #41).
- **Push notifications**: off-platform delivery layer (Bark first; ntfy/wecom/feishu future); allow-listed event kinds with per-kind Bark levels; never blocks the main event loop.
- **Sandbox isolation**: workspace cwd + env whitelist + CLI-native sandbox (Codex `--full-auto`, Gemini `--yolo`, Claude `--dangerously-skip-permissions` + `--allowedTools`).
- **Test harness** (PR #44): scripted offline E2E driver under `tests/harness/` covering the full GatewayManager + RuntimeService stack against `BaseChannel`-only contracts; 3 yaml regression scenarios + smoke-regression guard test; cross-platform-ready (no Discord imports) so future Slack/Feishu work reuses the same scenarios.
- **Operator surfaces**: Discord `/doctor`, `/automation_status`, `/usage_today`, `/usage_thread`, `/task_logs`, `/skill_stats`, persisted automation runtime state, opt-in read-only `oma-dashboard` HTTP service (loopback-only by deployment convention).

Still missing (post-1.0 / 1.x — see [`todo.md`](todo.md) and [`v1.0-plan.md`](v1.0-plan.md)):
- Slack / Feishu / Lark / WeChat platform adapters
- Semantic memory retrieval (BM25 + vector hybrid, MMR re-rank)
- Hybrid autonomy (recurring-pattern detection from history → skill drafting)
- Event-driven triggers beyond cron (webhook, file-watch, external notifications)
- Richer HITL families beyond structured single-choice checkpoints
- Guest session / tenant isolation
- Remote object-storage delivery backends (R2/S3 style)
- Real-mode harness CI integration (today gated behind `OMA_HARNESS_ALLOW_REAL=1` and raises `NotImplementedError`)

## Next Product Direction

- Current branch state: `v0.9.5` released; aiming at `v1.0` stable.
- `v0.8` delivered platform abstraction + reliability hardening + deployment hardening (service extraction, graceful shutdown, log hygiene, error contract, docker compose, restart/recovery tests, operator docs).
- `v0.9.0` rewrote memory as the event-driven Judge model — BREAKING; old daily/curated tiers + manual promotion command removed (migration script in `scripts/`).
- `v0.9.1`–`v0.9.3` finished service extraction, restart/recovery hardening, experimental-surface cleanup, Slack stub removal.
- `v0.9.4` shipped streaming anchor edits, push notifications, watchdog, single published-artifact path, dump channels, central scheduler due-loop, CI three-stage gate.
- `v0.9.5` shipped weekly reflection, daily reflection default-on, dashboard + Docker deployment, cost chart with axes, configurable refresh, AI daily section-checkpointing, Docker entrypoint `flock` serialization. Also shipped the cwd-unification / cached-credential / completion-body fixes (PR #41) and the scripted E2E harness (PR #44).
- `v1.0` is the stable contract freeze — Discord-only, single-user, self-hosted; acceptance criteria in [`v1.0-plan.md`](v1.0-plan.md).
- Post-1.0 expansion (Slack / Feishu / WeChat, semantic retrieval, hybrid autonomy) lives off the 1.0 critical path; the cross-platform `BaseChannel` contract + harness are the foundation that will let those land without re-tooling.
- Source-code self-modification remains a high-risk, strongly gated capability — not the default autonomy path.

## Historical Milestones

### v0.9.5 (2026-05-04)

- Cwd-unification + cached bilibili credential reuse + non-empty completion body (PR #41); scripted offline E2E harness under `tests/harness/` with 3 regression scenarios + cwd-keyed `StubAgent` (PR #44); `BaseChannel` ABC promoted `signal_task_status` and `send_hitl_prompt` with text-fallback defaults
- Weekly memory reflection (multi-scale "dream" pass on top of daily); daily reflection default-on; dashboard Docker deployment + scripts/ launcher path; cost chart with x/y axes replacing inline sparkline; configurable dashboard refresh interval; AI daily Stage 2.2 (section-level checkpoint storage + paper-digest JSON reuse); Docker entrypoint `flock`-serialized concurrent editable installs

### v0.9.4

- Streaming anchor edits; push notifications layer (Bark first); scheduler liveness watchdog; single published-artifact path under `runtime.reports_dir/`; dump-channel routing for automation completions (one bot token, multiple send-only channels); CI three-stage gate (lint → typecheck → tests); central scheduler due-loop; canonical 5-intent router contract; opt-in read-only `oma-dashboard` HTTP service

### v0.9.0–v0.9.3

- v0.9.0: Memory subsystem rewritten as event-driven Judge model — BREAKING. Old two-tier date-based memory + post-turn extractor removed; single-tier `memories.yaml` + agent-synthesized `MEMORY.md`; idle / `/memorize` / keyword triggers; migration script
- v0.9.1: Slack stub removed; remaining service-layer extraction; restart/recovery tests across chat/skill/runtime/HITL/auth/automations; older-state-layout upgrade validation
- v0.9.2: Watchdog (initial); operator docs tightened
- v0.9.3: artifact archive; automation follow-up thread

### v0.8

- 1.0 hardening — full four-phase plan complete: platform abstraction (service-layer extraction; `BaseChannel` contract review), reliability hardening (graceful shutdown, startup config validation, upgrade/migration contract, markdown-aware chunking, rate-limit, log hygiene, user-visible error contract), deployment hardening (first-class `compose.yaml`, operator-facing restart/upgrade SOPs, health-checks), and documentation (EN + CN parity)

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
