# AGENT.md

This file provides guidance to AI coding agents (Claude Code, Gemini CLI, etc.) when working with this repository.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                  # core deps
pip install -e ".[all]"           # include anthropic + openai SDKs

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

The system has three abstraction layers between the user (chat platform) and the AI agent.

**Gateway layer** (`src/oh_my_agent/gateway/`)

- `BaseChannel` ABC: platform adapter with `start()`, `create_thread()`, `send()`, `typing()`. Implemented for Discord; Slack is a stub.
- `GatewayManager`: holds `(BaseChannel, AgentRegistry)` pairs, routes `IncomingMessage` to `handle_message()`. Manages `ChannelSession`s and triggers history compression in the background.
- `ChannelSession`: per-channel state with async API. Loads/persists per-thread conversation histories via `MemoryStore`. In-memory cache avoids repeated DB reads.
- Message flow: `on_message` → `IncomingMessage` → `GatewayManager.handle_message()` → `AgentRegistry.run()` → `channel.send()`.

**Agent layer** (`src/oh_my_agent/agents/`)

- `BaseAgent` ABC: `async run(prompt, history) → AgentResponse`.
- `AgentRegistry`: ordered `list[BaseAgent]` with automatic fallback — tries each in sequence, returns first success.
- `BaseCLIAgent` (`agents/cli/base.py`): subprocess runner for CLI agents. Flattens `history` into prompt text. Subclasses override `_build_command()`.
- `BaseAPIAgent` (`agents/api/base.py`): for SDK-based agents receiving `history` as native messages.
- Concrete: `ClaudeAgent`, `GeminiCLIAgent`, `AnthropicAPIAgent`, `OpenAIAPIAgent`.

**Memory layer** (`src/oh_my_agent/memory/`)

- `MemoryStore` ABC + `SQLiteMemoryStore`: persists all turns to `data/memory.db` with WAL mode, FTS5 full-text search, and thread-level CRUD.
- `HistoryCompressor`: when a thread exceeds `max_turns`, compresses old turns into a summary (via agent) or truncates (fallback). Runs asynchronously after each response.

**Skill system** (`src/oh_my_agent/skills/`)

- `SkillSync`: symlinks skills from `skills/` to `.gemini/skills/` and `.claude/skills/` for native CLI discovery. Skills follow the Agent Skills standard (`SKILL.md`).

**Config** (`config.py` + `config.yaml`)

`load_config()` reads `config.yaml` with `${ENV_VAR}` substitution. Sections: `memory`, `skills`, `gateway`, `agents`.

**Adding a new platform**: subclass `BaseChannel`, implement `start/create_thread/send`, add a branch in `main._build_channel()`.

**Adding a new agent**: subclass `BaseCLIAgent` (override `_build_command`) or `BaseAPIAgent` (override `run`), add a branch in `main._build_agent()`, reference in `config.yaml`.

**Adding a new skill**: create `skills/{name}/SKILL.md` (+ optional `scripts/`). `SkillSync` will symlink it to CLI directories on startup.
