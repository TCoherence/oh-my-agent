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

- `BaseChannel` ABC: platform adapter with `start()`, `create_thread()`, `send()`, `send_message()`, `edit_message()`, `typing()`. Implemented for Discord (with slash commands); Slack is a stub.
- `GatewayManager`: holds `(BaseChannel, AgentRegistry)` pairs, routes `IncomingMessage` to `handle_message()`. Manages `ChannelSession`s and triggers history compression in the background.
- `ChannelSession`: per-channel state with async API. Loads/persists per-thread conversation histories via `MemoryStore`. In-memory cache avoids repeated DB reads.
- Discord slash commands: `/ask`, `/reset`, `/agent`, `/search` via `app_commands.CommandTree`.
- Message flow: `on_message` → `IncomingMessage` → `GatewayManager.handle_message()` → `AgentRegistry.run()` → stream-edit or `channel.send()`.

**Agent layer** (`src/oh_my_agent/agents/`)

- `BaseAgent` ABC: `async run(prompt, history) → AgentResponse`.
- `AgentRegistry`: ordered `list[BaseAgent]` with automatic fallback — tries each in sequence, returns first success. Passes `thread_id` for session resume.
- `BaseCLIAgent` (`agents/cli/base.py`): subprocess runner for CLI agents. Flattens `history` into prompt text. Subclasses override `_build_command()`.
- Concrete CLI agents: `ClaudeAgent` (session resume), `GeminiCLIAgent`, `CodexCLIAgent`.
- `agents/api/` — **deprecated since v0.4.0**. `AnthropicAPIAgent`, `OpenAIAPIAgent` kept for reference only.

**Memory layer** (`src/oh_my_agent/memory/`)

- `MemoryStore` ABC + `SQLiteMemoryStore`: persists all turns to `data/memory.db` with WAL mode, FTS5 full-text search, thread-level CRUD, and `export_data()`/`import_data()` for backup.
- `HistoryCompressor`: when a thread exceeds `max_turns`, compresses old turns into a summary (via agent) or truncates (fallback). Runs asynchronously after each response.

**Skill system** (`src/oh_my_agent/skills/`)

- `SkillSync`: bidirectional sync between `skills/` and CLI-native directories.
  - `sync()` — forward: symlinks `skills/` → `.gemini/skills/` and `.claude/skills/`.
  - `reverse_sync()` — copies non-symlink skills from CLI dirs back to `skills/`.
  - `full_sync()` — runs reverse then forward on startup.

**Config** (`config.py` + `config.yaml`)

`load_config()` reads `config.yaml` with `${ENV_VAR}` substitution. Sections: `memory`, `skills`, `gateway`, `agents`.

**Adding a new platform**: subclass `BaseChannel`, implement `start/create_thread/send`, add a branch in `main._build_channel()`.

**Adding a new CLI agent**: subclass `BaseCLIAgent` (override `_build_command`), add a branch in `main._build_agent()`, reference in `config.yaml`.

**Adding a new skill**: create `skills/{name}/SKILL.md` (+ optional `scripts/`). `SkillSync` will pick it up on startup and symlink it to CLI directories.
