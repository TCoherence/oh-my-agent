# Todo / Roadmap

Items are organized by target version, roughly prioritized top-to-bottom within each section.

See [future_planning_discussion.md](future_planning_discussion.md) for detailed rationale behind these decisions.

## v0.4.0 — CLI-First Cleanup + Skill Sync

- [ ] **Deprecate API agent layer** — mark `agents/api/` as deprecated. Keep code but remove from `config.yaml.example` and README. CLI agents provide a complete agentic loop (tool use, skills, context management); maintaining a parallel API path adds complexity without proportional value.
- [ ] **SkillSync reverse sync** — detect new skills created by CLI agents in `.gemini/skills/` or `.claude/skills/` and copy them back to the canonical `skills/` directory. Use a post-response hook in `GatewayManager.handle_message()`. Also update `AGENT.md` to instruct agents to write skills directly to `skills/`.
- [ ] **Streaming responses (CLI only)** — edit a Discord message in-place as tokens arrive. Use `--output-format stream-json` for CLI agents. Rate-limit edits to avoid Discord throttling (e.g. edit every 0.5s).
- [ ] **Slash commands** — `/ask`, `/reset` (clear thread history via `session.clear_history()`), `/agent claude` (switch agent for this session), `/search` (cross-thread memory search). Requires moving from `discord.Client` to `discord.app_commands`.
- [ ] **Update README.md** — rewrite to reflect v0.3.0+ architecture (gateway + memory + skills, CLI-first).

## v0.5.0 — Self-Evolution

- [ ] **Agent-driven skill creation** — end-to-end workflow: user requests a new skill → agent creates `SKILL.md` + scripts in `skills/` → auto sync → available to all agents.
- [ ] **Skill testing / validation** — after creating a skill, automatically verify it works (e.g. run the script, check SKILL.md format).
- [ ] **CLI session resume** — explore `claude --resume <session_id>` to avoid re-sending full history via prompt flattening on every turn. Complements `HistoryCompressor` for long conversations.
- [ ] **Cross-session memory search** — `memory/store.py` already supports FTS5 search across all threads. Wire up a `/search` command or agent-accessible query so agents can reference context from other threads.
- [ ] **Memory export/import API** — add `MemoryStore.export()` / `import()` to prepare for future memory decoupling from this repo.

## v0.6.0 — Multi-Agent Intelligence

- [ ] **Smart agent routing** — instead of simple fallback, route tasks to the best agent based on task type (e.g. code → Claude, search/research → Gemini). Requires richer `AgentRegistry` logic.
- [ ] **Agent collaboration** — one agent writes code, another reviews. Multi-agent workflows beyond simple fallback.
- [ ] **Agent selection via @mention** — user types `@claude fix this` or `@gemini explain` to route to a specific agent.
- [ ] **Telegram adapter** — `gateway/platforms/telegram.py`. Telegram supports message threads in groups via `reply_to_message_id`.
- [ ] **Feishu/Lark adapter** — `gateway/platforms/feishu.py`. Feishu has a mature bot SDK with thread support.

## Backlog (Unprioritized)

- [ ] **Slack adapter** — `gateway/platforms/slack.py` is currently a stub. Implement using `slack_sdk` (async client). Threads in Slack use `thread_ts`.
- [ ] **Codex CLI agent** — add `agents/cli/codex.py` for OpenAI Codex CLI once it's available.
- [ ] **Rate limiting / request queue** — prevent hammering the CLI when multiple messages arrive simultaneously. Per-session queue with configurable concurrency.
- [ ] **File attachment support** — download Discord file attachments, pass file paths to the agent as part of the prompt context.
- [ ] **Markdown-aware chunking** — `utils/chunker.py` currently may split inside code fences. Track open/close triple-backtick state when finding split points.
- [ ] **SQLite → PostgreSQL migration** — when scaling beyond a single-machine deployment, switch `MemoryStore` backend to PostgreSQL. The ABC already supports this.
- [ ] **End-to-end test with real Discord** — current tests are all unit tests with mocks. Add an integration test that spins up a real Discord bot against a test server/channel.

## Maintenance / Quality

- [ ] **Linting / formatting** — add `ruff` to dev deps, configure in `pyproject.toml`.
- [ ] **Type checking** — add `mypy` or `pyright` to dev deps, enable strict mode incrementally.
- [ ] **GitHub Actions CI** — run `pytest` on push/PR. Matrix over Python 3.11 and 3.12.

## Done (v0.3.0)

- [x] **Conversation memory within threads** — `MemoryStore` with SQLite backend persists all turns to `data/memory.db`. History survives bot restarts and supports FTS5 full-text search.
- [x] **Memory compression** — `HistoryCompressor` auto-summarises old turns when a thread exceeds `max_turns`. Falls back to truncation if summary agent fails.
- [x] **Skill system** — Skills defined in `skills/` using the Agent Skills standard (`SKILL.md`). `SkillSync` symlinks to `.gemini/skills/` and `.claude/skills/` for native CLI discovery. Includes example `weather` skill.
- [x] **Gemini CLI model update** — upgraded from `gemini-2.0-flash` to `gemini-3-flash-preview` (thinking-compatible).
- [x] **Gemini fallback** — `agents: [claude, gemini]` in config — Claude first, Gemini as fallback.
