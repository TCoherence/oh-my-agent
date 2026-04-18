# Changelog

All notable changes to this project are documented in this file.

The format is intentionally lightweight and release-oriented rather than exhaustive.

## Unreleased

### Added

- **v0.9 Contract-Freeze Phase A — restart/recovery + upgrade path tests** (toward v1.0 acceptance criteria #3 / #7 / #9 / #10)
  - `tests/test_restart_recovery.py` (11 tests): in-process close/reopen round-trips for chat thread history, CLI session resume bridge, runtime tasks (DRAFT / RUNNING / WAITING_USER_INPUT) with `event_log`, HITL prompts (DB + Discord `_rehydrate_hitl_prompt_views` view rehydration), auth-wait (runtime task path + direct-chat `suspended_agent_run` path), automation `last_run_at` / `next_run_at`, and `schema_version` idempotence.
  - `tests/test_upgrade_paths.py` (12 tests): end-to-end `scripts/migrate_memory_to_judge.py` exercise (dry-run + real run + backup dir assertions); `memory.adaptive` → `memory.judge` alias lock-in (compat fallback preserved, not a regression); deprecation warnings for `memory.adaptive` config and for legacy `curated.yaml` / `daily/` layouts; builder-mode runtime.db schema-migration test (no binary fixture — `aiosqlite` constructs a v0.7-era `runtime_tasks` missing the modern columns + `task_type='code'` enum, then asserts `_ensure_column` backfill + enum normalisation to `'repo_change'`); `schema_version` lands at `CURRENT_SCHEMA_VERSION` after `init()` even on legacy DBs lacking the `schema_version` table.
  - `_warn_if_legacy_memory_config()` and `_warn_if_legacy_memory_layout()` helpers in `main.py` emit deprecation warnings at startup (visible in `service.log`) so `/doctor` and operators notice stale v0.8 state without breaking the boot.
  - Total tests: 504 → 527.

## v0.9.0 - 2026-04-17

### Changed (BREAKING)

- **Memory subsystem rewritten as a Judge model**: the legacy daily/curated tier system, post-turn `MemoryExtractor`, and explicit `/promote` workflow are removed and replaced by a single-tier `JudgeStore` plus an event-driven `Judge` agent.
  - **New on-disk schema**: `~/.oh-my-agent/memory/memories.yaml` (flat list with `id`, `summary`, `category`, `scope`, `confidence`, `observation_count`, `evidence_log[]`, `status`/`superseded_by`); `MEMORY.md` is synthesized from active entries on the same path.
  - **Triggers**: thread idle (default 15 min), explicit `/memorize` slash command (optionally with literal `summary` + `scope` to short-circuit the LLM), and natural-language keywords (`记一下` / `remember this` / etc.) — extraction no longer runs on every assistant turn.
  - **Action-based judgment**: the Judge emits `add` / `strengthen` / `supersede` / `no_op` actions against the existing memory list (passed in as context), eliminating the paraphrase-driven duplication that left the old store stuck at `obs=1` for every entry.
  - **Removed modules**: `oh_my_agent.memory.adaptive`, `oh_my_agent.memory.date_based`, `oh_my_agent.memory.extractor` and their tests are deleted.
  - **Removed Discord command**: `/promote` (no two-tier system to promote between); `/memories` and `/forget` continue to work against the new store, and `/memorize` is added.
  - **Config**: `memory.adaptive` is replaced by `memory.judge` (`enabled`, `memory_dir`, `inject_limit`, `idle_seconds`, `idle_poll_seconds`, `synthesize_after_seconds`, `max_evidence_per_entry`, `keyword_patterns`).
  - **Migration**: run `python scripts/migrate_memory_to_judge.py <memory_dir>` to back up the old layout and write a fresh `memories.yaml` from `curated.yaml` (pass `--include-daily` to also import daily entries). Subsequent startup will rebuild `MEMORY.md` automatically.

## v0.8.2 - 2026-04-17

### Added

- **`paper-digest` skill**: daily research-paper radar covering arXiv, HuggingFace Daily, and Semantic Scholar; workflow slimmed to keep within agent `max_turns` budget on large days
- **`youtube-podcast-digest` skill**: weekly digest of YouTube podcast subscriptions; filters out Shorts and supports configurable catch-up windows so missed runs are picked up on the next fire
- **Claude agent `--output-format stream-json --verbose`**: per-turn output visibility during long Claude CLI runs — intermediate reasoning and tool calls surface in real time rather than only at completion
- **Scheduler DRAFT-skip reminder + `/task_replace`**: when a scheduler-fired task is still in DRAFT at the next cron tick, the system reminds the operator; `/task_replace` discards a DRAFT and immediately refires its automation cron
- **`market-briefing` podcast — finance daily coverage**: `🎙️ 播客动态` section extended from AI-only to both AI and finance daily reports; finance group seeded with 6 channels (锦供参考, 起朱楼宴宾客, 小Lin说, 商业就是这样, 知行小酒馆, 面基); AI group expanded to 8 channels (+42章经, AI局内人, 科技早知道); `general` feed group removed — shared channels merged into their primary domain group
- **AI people-pool discovery rules**: SKILL.md now specifies concrete discovery sources (X/Twitter, GitHub trending, podcast guests, paper authors), candidate qualification criteria, minimum JSON fields, and a target range of 1–3 candidates per AI daily report; `report_store.py persist` auto-calls `ai_people_pool.py record` for AI daily reports so the state file stays in sync without relying on the agent to remember a manual step
- **Per-automation `auto_approve` flag**: automation YAML files now accept `auto_approve: true` to skip the runtime risk-evaluation gate (DRAFT → PENDING without manual approval); default is `false` (safe); all existing automations updated to `auto_approve: true`
- **`/automation_run` slash command**: manually fire any enabled automation job on demand — useful for retrying failed cron jobs without waiting for the next schedule
- **Human-readable risk reasons**: DRAFT task cards and DM notifications now show descriptive reason text (e.g. "estimated runtime 26 min exceeds 20 min threshold") instead of raw labels like `minutes_over_20` or `draft`
- **`automation.yaml.example`**: annotated reference template for all automation YAML fields including the new `auto_approve` flag
- **README progressive-disclosure redesign**: top-level README rewritten to lead with a 60-second quickstart and progressively reveal advanced configuration

### Fixed

- **Automation YAML `skill_name` fix**: all 7 automation YAML files now carry explicit `skill_name`, enabling correct `timeout_seconds` inheritance from skill metadata (e.g. deals-scanner 900s, market-briefing 1500s instead of falling back to agent default 300s); automation prompts rewritten to reference SKILL.md workflows and helper scripts instead of hardcoding output paths
- **Scheduler tasks stuck in DRAFT**: scheduler-fired tasks with `timeout_seconds > 1200` (market-briefing, deals-scanner) were blocked by `evaluate_strict_risk()` producing `minutes_over_20`; scheduler tasks now respect per-automation `auto_approve` flag to bypass the risk gate

### Removed

- **`agents/api/` deleted**: deprecated since v0.4.0 and no longer used in any shipping config — `AnthropicAPIAgent`, `OpenAIAPIAgent`, `BaseAPIAgent`, and the `type: api` branch in `main._build_agent()` are removed; CLI agents (Claude / Gemini / Codex) are the only supported path. `skills/market-intel-report/` (consolidated into `market-briefing` earlier) also removed.

## v0.8.1 - 2026-04-15

### Added

- **Memory extraction hygiene** (Slice A): extraction window rewritten to use the most recent 6 turns (≤800 chars per assistant turn) instead of silently truncating from the front of the full history; ensures recent user evidence always reaches the extractor regardless of thread length
- **Extraction trigger optimization**: per-thread `last_extracted_user_turn_count` + `last_extraction_empty` in-memory state; extraction is skipped when no new user turns have occurred since the last pass *and* that pass returned empty — eliminates redundant agent calls on idle threads
- **Extraction prompt hardening**: explicit negative rules block one-off task details, temporary plans, slash command habits, file paths, implementation steps, and future speculation (`"the user may…"`); `[user]` / `[assistant]` role tags guide the model to treat assistant content as context only
- **Parse-failure retry**: on JSON parse failure the extractor retries once with a simplified schema; on second failure it returns empty and logs `parse_failure=true` with the skip reason — no raw LLM output leaks to the user
- **`MemoryEntry` schema expansion — Batch 1**: added `explicitness` (`explicit`/`inferred`), `status` (`active`/`superseded`), `evidence` (≤140 char user-side snippet), and `last_observed_at`; old YAML files migrate lazily on load with safe defaults
- **`MemoryEntry` schema expansion — Batch 2**: added `scope` (`global_user`/`workspace`/`skill`/`thread`), `durability` (`ephemeral`/`medium`/`long`), `source_skills`, and `source_workspace`; `scope_matches()`, `scope_score_multiplier()`, `broadened_scope()`, and `stronger_durability()` helpers added to `adaptive.py`
- **Two-stage deduplication**: Stage 1 normalises (lowercase, strip punctuation, stopword filter) and classifies pairs as `same_memory` (normalised equality or Jaccard ≥ 0.75), `candidate` (Jaccard 0.35–0.75), or `distinct` (< 0.35); Stage 2 batches all candidates in a single agent call that returns `same_memory` / `related_but_distinct` / `contradictory`; contradictory hits mark the old entry `status=superseded` with a timestamped `last_observed_at`
- **Fast-path / slow-path promotion**: explicit, high-confidence memories (`explicitness=explicit`, `confidence ≥ 0.85`, `observation_count ≥ 2`, category in preference/workflow/project_knowledge) fast-promote to curated in 1–2 observations; inferred memories slow-promote only after `confidence ≥ 0.80` plus either `observation_count ≥ 3` or ≥ 2 distinct source threads; `fact` category never takes the fast path; `superseded` entries are ineligible for promotion
- **Scope-aware bucketed retrieval**: `get_relevant()` now accepts `skill_name`, `thread_id`, and `workspace` context and routes memories into four buckets (`skill_scoped`, `workspace_project`, `global_preference`, `recent_daily`) each with a configurable per-bucket limit; scope-match multipliers boost contextually relevant memories without hard-excluding unmatched global preferences
- **Injection filter**: `superseded` entries are permanently excluded from prompt injection and `MEMORY.md` synthesis; `thread`-scoped and `ephemeral` entries respect their scope boundaries
- **Structured memory trace logs**: four new log event types — `memory_extract` (thread_id, turn_count, extracted/rejected counts, retry_used, skip_reason, parse_failure), `memory_merge` (candidate/same/distinct/contradictory counts), `memory_promote` (fast/slow path counts, skipped_reason), `memory_inject` (selected/filtered-superseded counts, per-bucket breakdown)
- **`/memories` view enhanced**: Discord display now shows `explicitness`, `status`, `observation_count`, and `last_observed_at` alongside existing tier and confidence bar
- **`seattle-metro-housing-watch` skill — coverage and contract update**: Bothell and Lynnwood promoted from optional expansion to default 7-area contract; Zillow added as formal area-trend second source alongside Redfin (no longer listing-only fallback); rate section now explicitly compares 30Y and 15Y fixed (MORTGAGE30US + MORTGAGE15US with direction and relative meaning); listing contract: single-family/townhouse only, per-area baseline 2 + priority-allocated 4 surplus slots (by high-quality sample availability → inventory activity → core-area preference), hard cap 18, price filter vs area-own median; `sample_listings[]` extended with `source_site`, `property_type`, `listed_at`, `original_list_price`, `price_history_summary`; `market_snapshot` stays lightweight (1/area, max 7); `area_deep_dive` gets 4–6 samples
- **`market-briefing` skill — coverage and structure update**: finance daily expanded to 8 fixed sections (adds 中国/香港市场脉搏, 美国市场波动与风险偏好, 中国房地产政策与融资信号); AI daily expanded to 9 sections with a new Frontier Labs / Frontier Model Radar section before the five-layer stack; frontier watchlist fixed to 8 labs (OpenAI, Anthropic, Google DeepMind, Meta, xAI, Mistral, Qwen, DeepSeek); rumor discipline codified (official source > quality media > social/leak only in `unverified_frontier_signals`); finance/politics boundary rule written into reference docs; `timeout_seconds` raised to 1500; weekly synthesis absorbs new finance and AI sections structurally; new `references/finance_watchlist.md` and `references/ai_frontier_watchlist.md` reference files
- **Usage audit attribution unified**: direct chat / explicit skill replies, runtime thread-agent replies, and automation terminal messages now share the same usage audit suffix contract (`in/out`, cache read/write, cost) while preserving each path's own prefix metadata such as automation name, run ID, and agent attribution
- **`market-briefing` podcast integration**: AI daily reports now include a `🎙️ 播客动态` section sourced from xiaoyuzhoufm.com subscriptions; new `scripts/podcast_fetch.py` prefetches latest episodes (48h freshness window, parallel fetch); subscription list externalized to `references/podcast_feeds.yaml` grouped by domain — editable without code changes; `timeout_seconds` raised from 1200 to 1500 to accommodate the prefetch step

### Fixed

- `DateBasedMemoryStore.max_memories` now applies correctly across both daily and curated tiers, respecting status-aware eviction order
- Merge hits on non-today daily files now write the updated entry back to the originating daily file rather than silently dropping the update
- `promote_memory()` now deduplicates against the curated tier before writing, preventing identical entries accumulating in `curated.yaml`
- `last_observed_at` is kept consistent across merge, promotion, and contradiction-supersede paths

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
