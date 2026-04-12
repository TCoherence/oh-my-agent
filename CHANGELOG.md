# Changelog

All notable changes to this project are documented in this file.

The format is intentionally lightweight and release-oriented rather than exhaustive.

## Unreleased

## v0.8.0 - 2026-04-12

### Added

- Service-layer package (`gateway/services/`) with shared result types (`ServiceResult`, `TaskActionResult`, `MemoryResult`, etc.) and a `BaseService` ABC that wires logger, config, channel, and optional registry
- `TaskService`: extracted all task-control business logic (start, stop, pause, resume, approve, reject, suggest, discard, merge, changes, logs, cleanup, list, status) from `discord.py` into a platform-agnostic service; Discord slash commands are now thin call-through wrappers
- `AskService`: extracted ask / agent-select / thread-reset / history / search logic from `discord.py`; session state managed cleanly in one place
- `DoctorService` and `AutomationService`: doctor diagnostics and automation operator commands (status, reload, enable, disable) separated from the Discord adapter
- `HITLService`: interactive HITL surface (button callbacks, approval/rejection/suggestion/request-changes flows) extracted from the Discord `View` lifecycle into a testable, platform-agnostic layer
- `BaseChannel` extensions: `edit_message()`, `upload_file()`, and `interactive_prompt()` added to the contract with Discord implementations
- `--validate-config` CLI flag: startup config schema validation with fail-fast reporting of missing fields and invalid values
- Schema version tracking in `SQLiteScopedStore`: `schema_versions` table stores per-scope schema version; `migrate_runtime_schema()` applies forward-only migrations without data loss
- Markdown-aware message chunker (`utils/chunker.py`): fenced code blocks (`` ``` `` / `~~~`) are never split mid-block; oversized blocks split by line with fence close/re-open; plain text chunks respect word boundaries
- Structured logging (`logging_setup.py`): `KeyValueFormatter` emits `key=value` pairs for machine-parseable log lines; `setup_logging()` wires `TimedRotatingFileHandler` (daily rotation, 7-day retention) alongside console output; startup cleanup removes stale rotated `service.log.YYYY-MM-DD` files older than the retention window
- Graceful shutdown contract: `GatewayManager.stop()` signals the Discord gateway, drains in-flight runtime workers, cancels agent subprocesses, and flushes SQLite WAL before exit; `main.py` hooks into `SIGINT`/`SIGTERM`
- User-visible error contract: unhandled exceptions in command handlers and skill dispatch now surface a short, readable message to the user instead of a raw traceback; full traceback is preserved in the log
- Rate-limit / request queue: per-channel async semaphore limits concurrent agent calls; overflow messages receive a "busy" reply instead of silently queuing indefinitely
- Concurrent thread/task isolation tests: `tests/test_concurrent_isolation.py` covers simultaneous task creation, per-channel semaphore enforcement, and cross-thread memory isolation
- `compose.yaml`: first-class Docker Compose config with named `oma-runtime` volume, environment forwarding for API keys and agent overrides, health-check, and restart policy
- Operator guide (`docs/EN/operator-guide.md` and `docs/CN/operator-guide.md`): covers local install, Docker/Compose install, restart, diagnostics, automation, backup, and upgrade/migration SOPs
- `seattle-metro-housing-watch` skill: tri-weekly housing market analysis for the Greater Seattle area across five domains (affordability/inventory, luxury/condo, rental, interest-rate/macro, regional/suburb); supports `bootstrap_backfill` and `weekly_digest` modes with persisted report storage

### Fixed

- Existing `runtime.db` databases missing newly added tables (e.g., `automation_runtime_state`) on startup: `SQLiteScopedStore.init()` now re-runs the full `SCHEMA_SQL` DDL (`CREATE TABLE IF NOT EXISTS`) on both new and existing databases before committing
- Stale rotated log files not cleaned up when the process never ran through midnight: `_cleanup_old_logs()` now runs at startup and removes `service.log.YYYY-MM-DD` files beyond the 7-day retention window

### Changed

- Discord slash command handlers in `discord.py` are now thin wrappers that delegate entirely to the corresponding service; no business logic remains in the adapter
- `_setup_logging()` in `main.py` delegates to `logging_setup.setup_logging()` and passes runtime config (log level, retention days)
- `v0.8` items in `docs/EN/todo.md` and `docs/CN/todo.md` marked complete; snapshot date updated to 2026-04-12

## v0.7.3 - 2026-04-10

### Added

- Discord-first owner notifications for `auth_required`, `ask_user`, `DRAFT`, and `WAITING_MERGE`, with a separate ping message in the same thread plus best-effort owner DMs
- Persistent `notification_events` rows in SQLite for notification dedupe and future escalation/reminder support
- Split SQLite runtime layout with dedicated conversation, runtime-state, and skills-telemetry databases
- Automatic startup migration from legacy monolithic `memory.db` into `memory.db`, `runtime.db`, and `skills.db`, with preserved `.monolith.bak` backup bundles
- `market-briefing` AI people-pool helper, curated seed file, runtime candidate queue, and X.com/community signal workflow
- Attachment-first artifact delivery with local absolute-path fallback and delivery-aware completion summaries
- Thread-scoped unified logs under `~/.oh-my-agent/runtime/logs/threads/` across direct chat, explicit skill invocation, runtime tasks, and HITL resume flows
- Structured HITL answer payloads carried through task/thread resume context alongside backward-compatible `[HITL Answer]` prompt blocks
- Discord `/doctor` operator diagnostics for gateway/runtime/HITL/auth/log health snapshots
- Persisted automation runtime state (`last_run_at`, `last_success_at`, `last_error`, `last_task_id`, `next_run_at`) surfaced through `/automation_status` and `/doctor`

### Changed

- Human-input states now fan out through an internal notification layer instead of each flow hand-rolling Discord reminders
- `auth_required`, `ask_user`, `DRAFT`, and `WAITING_MERGE` notifications now resolve explicitly when the underlying waiting state is cleared, while routine runtime progress still stays notification-free
- `market-intel-report` has been renamed to `market-briefing`, and persisted report storage now lives under `~/.oh-my-agent/reports/market-briefing/`
- `market-briefing` finance daily now defaults to China macro/policy, US macro/policy, tracked holdings over the last 7 days, and a market/index-fund lens, and all daily domains now carry stricter no-signal / low-confidence guidance in schema and prompts
- `market-briefing` AI daily now adds tracked people/community signals, a bounded X.com discovery sweep, candidate/promotion state under `~/.oh-my-agent/reports/market-briefing/state/`, and explicit no-signal fallbacks for thin layers
- Report-store helpers for `market-briefing` and `deals-scanner` now derive their default local report date from `OMA_REPORT_TIMEZONE` / `TZ` instead of implicitly inheriting UTC-like container defaults, and Docker helper scripts now pass an explicit report timezone into the container
- `deals-scanner` daily scans now use source-specific default lookback windows (`3` days for credit-cards/uscardforum/rakuten, `7` days for slickdeals/dealmoon/summary), expose `lookback_window_days` in daily JSON, and treat older carryover items as `Watchlist`-only instead of mixing them into the main summary buckets
- `memory.path` now refers to the conversation store only; runtime task/auth/HITL/notification/session state moves to `runtime.state_path`, and skill provenance/telemetry moves to `skills.telemetry_path`
- Runtime task claiming no longer opens a nested `BEGIN IMMEDIATE` transaction on a shared SQLite connection
- `artifact`, `repo_change`, and `skill_change` runtime flows are now fully closed out for v0.7.3, with delivery/logging/HITL behavior aligned to task type instead of merge-only assumptions
- `/automation_status` and `/doctor` now read persisted automation runtime state instead of relying on in-memory scheduler snapshots alone
- Automation-backed executions now honor skill `metadata.timeout_seconds` the same way direct skill invocations do
- Live task status, merge/discard/request changes, and answered/cancelled HITL prompts now settle into stable final Discord views instead of lingering in a loading state

## v0.7.2 - 2026-03-16

### Added

- File-driven automation scheduling under `~/.oh-my-agent/automations/*.yaml`
- Polling-based automation hot reload for file add/update/delete and per-file `enabled` toggles
- Cron-based automation schedules with `interval_seconds` retained for high-frequency local testing
- Discord operator automation commands: `/automation_status`, `/automation_reload`, `/automation_enable`, `/automation_disable`
- Temporary `docs/archive/next_up.md` note for near-term execution focus
- Long-running Docker helper scripts: `docker-start.sh`, `docker-logs.sh`, `docker-stop.sh`, `docker-status.sh`
- `market-briefing` skill for persisted politics / finance / AI bootstrap, daily, and weekly reports
- Report-store helper for canonical Markdown + JSON outputs under `~/.oh-my-agent/reports/market-briefing/`
- Generic Discord-first HITL `ask_user` control path for owner-only single-choice questions across direct chat and runtime tasks
- Skill-specific `metadata.timeout_seconds` frontmatter override for slow direct-chat skill invocations

### Changed

- `config.yaml` no longer embeds `automations.jobs`; automation config now only carries global scheduler settings
- Scheduler startup now watches the automation storage directory even when it initially contains 0 jobs
- Scheduler now keeps a visible in-memory snapshot of valid active + disabled automations for operator commands, while invalid/conflicting files remain log-only
- Scheduler-triggered automations now use reply/artifact runtime tasks with a single-step `true` validation path instead of repo-change loops
- Duplicate fires of the same automation name are now skipped while an earlier run is still in flight
- Automation runs now post the final artifact/result directly in Discord instead of runtime task status/update spam
- Requeued in-flight runtime tasks now roll back one step before retry, avoiding immediate `TIMEOUT max_steps=1` failures after restart for single-step automation runs
- Runtime cleanup now uses a 7-day default retention window and prunes stale agent logs along with old task workspaces
- README and Chinese README now document the external automation directory, hot-reload semantics, and current in-memory-only runtime state behavior
- Docker docs now distinguish attached development runs from detached long-running service runs, including postmortem debugging expectations around `docker logs` and persistent application log files
- Scheduler skill and validator now target file-driven automation YAML under `~/.oh-my-agent/automations/` instead of the old `config.yaml` job model
- README and Chinese README now document the market-briefing skill, report storage layout, and bounded bootstrap workflow
- `OMA_CONTROL` now supports generic `ask_user` challenges alongside `auth_required`, with persisted `hitl_prompts`, visible Discord choice prompts, auto-resume behavior, and persistent-view recovery after restart
- `WAITING_USER_INPUT` now covers both QR auth pauses and generic owner-choice pauses for runtime tasks and automations
- Direct-chat skill invocations can now temporarily override the normal per-agent CLI timeout from `SKILL.md` frontmatter `metadata.timeout_seconds` without changing default chat timeouts for the rest of the system

## v0.7.1 - 2026-03-08

### Added

- Auth-first QR login and structured agent control-flow integration:
  - owner-only Discord auth commands: `/auth_login`, `/auth_status`, `/auth_clear`
  - Bilibili QR login provider with local credential persistence
  - runtime `WAITING_USER_INPUT` state for auth-blocked task execution
  - `OMA_CONTROL` challenge envelope parsing for direct chat and runtime task paths
  - suspended direct-chat agent runs that can resume after auth succeeds
  - resumable auth-triggered agent runs with session-first resume and fresh-run fallback
- Outbound Discord attachment support for auth QR delivery
- Docker runtime workflow:
  - image build/run helper scripts
  - repo-mounted `/repo` source of truth for config and source code
  - host-mounted `/home` runtime state root
  - startup-time editable install from `/repo` so normal source edits only require restart
  - image now carries runtime dependencies only instead of relying on a separate in-image source snapshot for execution
  - preinstalled `claude`, `gemini`, and `codex` CLIs in the Docker image
  - fail-fast startup validation for configured CLI binaries
- Transcript-first video skills:
  - `youtube-video-summary` using `yt-dlp` for subtitle and metadata extraction
  - `bilibili-video-summary` using `yt-dlp`, explicit `auth_required` signaling, and transcript-backed article-style summaries

### Changed

- Docker startup now reads config from `OMA_CONFIG_PATH` (default `/repo/config.yaml`) and loads `.env` from the config directory rather than relying on process `cwd`
- Skill sync now anchors repo-native `.claude/`, `.gemini/`, and `.agents/skills/` paths to the resolved project root instead of the current working directory
- Docker entrypoint was simplified to require prepared repo config instead of seeding runtime copies of `config.yaml`, `.env`, `skills/`, or root `AGENTS.md`
- Docker docs now describe the mounted-repo workflow, restart-vs-rebuild expectations, and CLI/login requirements
- Date-based memory now keeps promoting eligible daily observations into `curated.yaml` during normal runtime operation instead of only on startup
- `MEMORY.md` synthesis is now wired into startup, memory extraction, and manual `/promote` flows when curated memory changes
- Auth challenge UX now surfaces the agent's progress/update message before sending the QR prompt, so resume no longer appears to jump in abruptly

### Fixed

- Runtime shutdown order now stops runtime workers/janitor before closing SQLite, preventing `no active connection` janitor crashes during process teardown
- Runtime janitor now backs off after exceptions instead of spinning in a tight error loop
- Auth QR PNG files are now cleaned up when flows reach terminal states (`approved`, `expired`, `failed`, `cancelled`)
- Resumed CLI sessions are now documented as potentially stale with respect to newly added skills, matching actual router-vs-session behavior

## v0.7.0 - 2026-03-01

### Added

- Skill evaluation end-to-end loop:
  - chat-path skill invocation telemetry stored in SQLite
  - per-invocation Discord reaction feedback (`👍` / `👎`)
  - `/skill_stats [skill]` operator surface
  - `/skill_enable <skill>` manual recovery for auto-disabled skills
  - auto-disable guard that removes unhealthy skills from automatic routing while preserving explicit `/skill-name`
  - duplicate-skill overlap review before skill auto-merge
  - source-grounded review for external repo/tool/reference skill adaptations
- Image attachment support for Discord messages:
  - `Attachment` dataclass with `is_image` property in gateway base
  - Discord `on_message` downloads `image/*` attachments (≤10 MB) to temp dir
  - `IncomingMessage.attachments` field carries attachments through the pipeline
  - Image-only messages (no text) get a default analysis prompt
  - Claude/Gemini: copy images to `workspace/_attachments/` and augment prompt with file-reference instructions
  - Codex: native `--image` flag support
  - Session history stores attachment metadata (filename, content_type)
  - Temp files cleaned up after agent response
- Date-based two-tier memory system (`DateBasedMemoryStore`):
  - Daily logs (`memory/daily/YYYY-MM-DD.yaml`) with exponential time decay
  - Curated long-term memories (`memory/curated.yaml`) — no decay
  - Auto-promotion: daily entries meeting observation count + confidence + age thresholds are promoted to curated on load
  - `MEMORY.md` synthesis: agent-generated natural-language view of curated memories
  - Pre-compaction flush: memory extraction now runs before history compression
- Discord `/promote` slash command for manual daily-to-curated promotion
- Discord `/memories` now shows tier tags (`[C]` curated / `[D]` daily)
- Module-level utility functions: `word_set`, `jaccard_similarity`, `eviction_score`, `find_duplicate`
- `MemoryEntry.tier` field (`"daily"` | `"curated"`)
- `oh-my-agent --version`
- Single-source package versioning via `src/oh_my_agent/_version.py`
- Generated workspace `AGENTS.md` metadata with source path, source hash, and generation timestamp
- Codex repo/workspace skill delivery via official `.agents/skills/`
- Request-scoped observability labels for gateway agent runs and background memory/compression follow-up work

### Fixed

- Runtime skill mutation flow no longer auto-merges likely duplicate skills or weak external-source adaptations without review
- Router-visible skill list now excludes auto-disabled skills, while explicit skill invocation remains available
- Strict risk detection no longer misclassifies `adapt ...` requests as `apt ...` package-management operations
- Hardened persisted CLI session resume for Claude, Codex, and Gemini:
  - invalid/stale resumed sessions are now cleared more selectively
  - stale persisted sessions are deleted even when fallback succeeds through another agent
- Added tests to keep package version and changelog state aligned
- Base workspace now refreshes synced skills and generated `AGENTS.md` together when repo `AGENTS.md` or canonical `skills/` change
- Legacy workspace `.codex/` compatibility directories are removed during workspace refresh and session sync
- Generated workspace `AGENTS.md` no longer enumerates workspace skill extensions now that Codex uses official `.agents/skills/`
- Background memory extraction and history compression logs now carry the originating request ID, and gateway reply logs now expose `purpose=...` across start/success/error lines

## v0.6.1 - 2026-03-01

### Added

- Codex CLI session resume via `codex exec resume`
- Gemini CLI session resume via `gemini --resume`
- Session persistence and restore for Codex/Gemini through the existing gateway session store

### Fixed

- Resume state handling now survives normal non-session CLI failures without dropping valid sessions
- Gateway session sync now clears stale persisted session IDs after fallback

## v0.6.0 - 2026-02-28

### Added

- Adaptive memory with YAML-backed storage
- Memory extraction from conversation history
- Memory injection into agent prompts
- Discord `/memories` and `/forget` commands
- Skill auto-approve and auto-merge for skill tasks

## v0.5.3 - 2026-02-27

### Added

- True subprocess interruption for running runtime agent/test subprocesses
- Message-driven runtime control for `stop`, `pause`, and `resume`
- PAUSED state with workspace preservation and resumable instructions
- Structured task completion summaries and runtime timing metrics

### Improved

- Suggestion UX for blocked tasks and approval flows

## v0.5.2 - 2026-02-26

### Added

- Durable runtime state machine
- Merge gate for repository and skill changes
- External runtime workspace layout and migration
- Runtime janitor cleanup
- Discord task commands and approval flow

## v0.4.2 - 2026-02-26

### Added

- Owner-only access gate
- Scheduler MVP
- Delivery mode selection per scheduled job
- Scheduler skill

## v0.4.1 - 2026-02-25

### Added

- `@agent` targeting in thread messages
- `/ask` agent override
- Persisted CLI session IDs and resume-across-restart support
- CLI error observability improvements

## v0.4.0 - 2026-02-25

### Added

- CLI-first cleanup of the agent stack
- Codex CLI agent
- SkillSync reverse sync
- Discord slash commands
- Claude CLI session resume
- Memory export/import
