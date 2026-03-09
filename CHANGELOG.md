# Changelog

All notable changes to this project are documented in this file.

The format is intentionally lightweight and release-oriented rather than exhaustive.

## Unreleased

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
