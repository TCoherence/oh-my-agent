# AGENT.md

This file provides guidance to AI coding agents (Claude Code, Gemini CLI, etc.) when working with this repository.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                  # core deps

# Run
cp config.yaml.example config.yaml   # then fill in tokens
oh-my-agent

# Tests
pip install -e ".[dev]"
pytest                            # all tests
pytest tests/test_memory_store.py # single file
pytest -k "test_fallback"         # single test by name
```

## Architecture

The system has four abstraction layers between the user (chat platform) and the AI agent.

**Gateway layer** (`src/oh_my_agent/gateway/`)

- `BaseChannel` ABC: platform adapter with `start()`, `create_thread()`, `send()`, `typing()`. Implemented for Discord (with slash commands); Slack is a stub.
- `GatewayManager`: holds `(BaseChannel, AgentRegistry)` pairs, routes `IncomingMessage` to `handle_message()`. Manages `ChannelSession`s and triggers history compression in the background.
- `ChannelSession`: per-channel state with async API. Loads/persists per-thread conversation histories via `MemoryStore`. In-memory cache avoids repeated DB reads.
- Discord slash commands: `/ask`, `/reset`, `/history`, `/agent`, `/search` via `app_commands.CommandTree`.
- Agent targeting:
  - `/ask` supports optional `agent` argument for new threads.
  - Thread messages support `@claude` / `@gemini` / `@codex` prefix to force one agent for that turn.
  - Prefix is stripped before dispatch; agent name is passed via `IncomingMessage.preferred_agent`.
- Message flow: `on_message` → `IncomingMessage` → `GatewayManager.handle_message()` → `AgentRegistry.run()` → `channel.send()`.

**Agent layer** (`src/oh_my_agent/agents/`)

- `BaseAgent` ABC: `async run(prompt, history) → AgentResponse`.
- `AgentRegistry`: ordered `list[BaseAgent]` with automatic fallback — tries each in sequence, returns first success. Supports `force_agent` to bypass fallback. Passes `thread_id` for session resume.
- `BaseCLIAgent` (`agents/cli/base.py`): subprocess runner for CLI agents. Accepts `workspace: Path | None` (sets subprocess `cwd`) and `passthrough_env: list[str] | None` (env var whitelist). Flattens `history` into prompt text. Subclasses override `_build_command()`.
- Concrete CLI agents: `ClaudeAgent` (session resume via `--resume`), `GeminiCLIAgent`, `CodexCLIAgent`.
  - Codex runs `codex exec --full-auto --json --skip-git-repo-check` and extracts assistant text from JSONL events.
- `agents/api/` — **deprecated since v0.4.0**. `AnthropicAPIAgent`, `OpenAIAPIAgent` kept for reference only.

**Memory layer** (`src/oh_my_agent/memory/`)

- `MemoryStore` ABC + `SQLiteMemoryStore`: persists all turns to `.workspace/memory.db` (default) with WAL mode, FTS5 full-text search, thread-level CRUD, and `export_data()`/`import_data()` for backup.
- `agent_sessions` table persists CLI session IDs with primary key `(platform, channel_id, thread_id, agent)`.
- `GatewayManager` loads persisted session IDs on message handling and upserts/deletes them based on agent outcome.
- `HistoryCompressor`: when a thread exceeds `max_turns`, compresses old turns into a summary (via agent) or truncates (fallback). Runs asynchronously after each response.

**Skill system** (`src/oh_my_agent/skills/`)

- `SkillSync`: bidirectional sync between `skills/` and CLI-native directories.
  - `sync()` — forward: symlinks `skills/` → `.gemini/skills/` and `.claude/skills/`.
  - `reverse_sync()` — copies non-symlink skills from CLI dirs back to `skills/`.
  - `full_sync()` — runs reverse then forward on startup.
- When `workspace` is configured, `_setup_workspace()` in `main.py` copies skills into `workspace/.claude/skills/` and `workspace/.gemini/skills/` (real files, not symlinks) so CLI agents find them from the workspace cwd.

**Sandbox isolation** (`main.py` + `BaseCLIAgent`)

Three-layer model, activated by the `workspace` config field:

- **Layer 0 — Workspace cwd**: `_setup_workspace()` creates the directory, copies `AGENT.md` and skills into it. `BaseCLIAgent` sets `cwd=workspace` on every subprocess. CLI sandboxes are cwd-scoped, so agents are confined to workspace rather than the dev repo.
- **Layer 1 — Env sanitization**: `_build_env()` uses a whitelist (`_SAFE_ENV_KEYS`: `PATH`, `HOME`, `LANG`, …). Secrets only reach the subprocess if listed in `env_passthrough` per agent.
- **Layer 2 — CLI-native sandbox**: Codex `--full-auto` (network blocked), Gemini `--yolo`, Claude `--dangerously-skip-permissions` + `--allowedTools`.

Without `workspace` in config, the bot runs in backward-compatible mode (full env, process cwd).

**Config** (`config.py` + `config.yaml`)

`load_config()` reads `config.yaml` with `${ENV_VAR}` substitution. Sections:

```yaml
memory:         # SQLite backend, max_turns, summary_max_chars
skills:         # enabled, path
workspace:      # optional — activates sandbox isolation (Layer 0 + L1)
gateway:        # channels list (platform, token, channel_id, agents)
agents:         # per-agent: type, cli_path, model, timeout, allowed_tools, env_passthrough, skip_git_repo_check
```

**Adding a new platform**: subclass `BaseChannel`, implement `start/create_thread/send`, add a branch in `main._build_channel()`.

**Adding a new CLI agent**: subclass `BaseCLIAgent` (override `_build_command`, accept `workspace` and `passthrough_env` kwargs, pass to `super().__init__()`), add a branch in `main._build_agent()`, reference in `config.yaml`.

**Adding a new skill**: create `skills/{name}/SKILL.md` (+ optional `scripts/`). `SkillSync` will pick it up on startup and symlink it to CLI directories (and copy to workspace if configured).
