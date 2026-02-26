# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
pip install pytest pytest-asyncio
pytest                            # all tests
pytest tests/test_chunker.py      # single file
pytest -k "test_fallback"         # single test by name
```

## Architecture

The system has two abstraction layers that sit between the user (chat platform) and the AI agent.

**Gateway layer** (`src/oh_my_agent/gateway/`)

- `BaseChannel` ABC: platform adapter interface with `start()`, `create_thread()`, `send()`, `typing()`. Currently implemented for Discord; Slack is a stub.
- `GatewayManager`: holds a list of `(BaseChannel, AgentRegistry)` pairs, starts them concurrently, and routes each `IncomingMessage` to `handle_message()`. Maintains `sessions` dict keyed by `"platform:channel_id"`.
- `ChannelSession`: per-channel state. Stores per-thread conversation histories as `{thread_id: [{"role", "content", "author"/"agent"}]}`. Sessions are fully isolated from each other.
- Message flow: `on_message` (platform) → `IncomingMessage` (platform-agnostic) → `GatewayManager.handle_message()` → `AgentRegistry.run()` → `channel.send()`.

**Agent layer** (`src/oh_my_agent/agents/`)

- `BaseAgent` ABC: single method `async run(prompt, history) -> AgentResponse`.
- `AgentRegistry`: wraps an ordered `list[BaseAgent]` and tries each in sequence, returning the first non-error response. The caller always gets `(agent_used, response)` so attribution can be shown.
- `BaseCLIAgent` (`agents/cli/base.py`): shared subprocess runner for all CLI-based agents. Converts `history` list into a flattened conversation string prepended to the prompt. Subclasses only override `_build_command()`.
- `BaseAPIAgent` (`agents/api/base.py`): for SDK-based agents. They receive `history` as a native messages array.
- Concrete agents: `ClaudeAgent` (CLI), `GeminiCLIAgent` (CLI stub), `AnthropicAPIAgent` (SDK), `OpenAIAPIAgent` (SDK stub).

**Config** (`config.py` + `config.yaml`)

`load_config()` reads `config.yaml` and does `${ENV_VAR}` substitution from the environment (`.env` is loaded via `python-dotenv`). `main.py` then constructs agents and channels from the parsed dict — no `Config` dataclass anymore.

**Adding a new platform**: subclass `BaseChannel`, implement `start/create_thread/send`, add a branch in `main._build_channel()`.

**Adding a new agent**: subclass `BaseCLIAgent` (override `_build_command`) or `BaseAPIAgent` (override `run`), add a branch in `main._build_agent()`, and reference the name in `config.yaml`.
