# Changelog

All notable changes to this project are documented in this file.

The format is intentionally lightweight and release-oriented rather than exhaustive.

## Unreleased

### Added

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

### Fixed

- Hardened persisted CLI session resume for Claude, Codex, and Gemini:
  - invalid/stale resumed sessions are now cleared more selectively
  - stale persisted sessions are deleted even when fallback succeeds through another agent

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
