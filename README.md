# Oh My Agent

Multi-platform bot that routes messages to CLI-based AI agents (Claude, Gemini, Codex). Each platform channel maps to an independent agent session with persistent conversation memory, streaming responses, and slash commands.

Inspired by [OpenClaw](https://openclaw.dev).

## Architecture

```
User (Discord / Slack / ...)
         │ message or /ask command
         ▼
   GatewayManager
         │ routes to ChannelSession (per channel, isolated)
         ▼
   AgentRegistry ── [claude, gemini, codex]
         │ tries in order, auto-fallback on error
         ▼
   BaseCLIAgent.run(prompt, history)
     ├── ClaudeAgent   (claude CLI, streaming, session resume)
     ├── GeminiCLIAgent (gemini CLI)
     └── CodexCLIAgent  (codex CLI, sandboxed)
         │
         ▼
   Response → stream-edit or chunk → thread.send()
   (-# via **agent-name** attribution)
```

**Key layers:**
- **Gateway** — platform adapters (Discord, Slack stub) with slash commands
- **Agents** — CLI subprocess wrappers with ordered fallback
- **Memory** — SQLite + FTS5 persistent conversation history with auto-compression
- **Skills** — bidirectional sync between `skills/` and CLI-native directories

## Prerequisites

- Python 3.11+
- At least one CLI agent installed:
  - [`claude`](https://docs.anthropic.com/en/docs/claude-code) — Claude Code CLI
  - [`gemini`](https://github.com/google-gemini/gemini-cli) — Gemini CLI
  - [`codex`](https://github.com/openai/codex) — OpenAI Codex CLI
- A Discord bot token with **Message Content Intent** enabled

## Setup

### 1. Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications) → create application
2. **Bot** tab → copy token → enable **Message Content Intent**
3. **OAuth2 → URL Generator** → scope `bot` + `applications.commands` → permissions: Send Messages, Create Public Threads, Send Messages in Threads, Read Message History
4. Open the generated URL to invite the bot to your server

### 2. Install

```bash
git clone <repo-url>
cd oh-my-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. Configure

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml`:

```yaml
memory:
  backend: sqlite
  path: data/memory.db
  max_turns: 20

skills:
  enabled: true
  path: skills/

gateway:
  channels:
    - platform: discord
      token: ${DISCORD_BOT_TOKEN}
      channel_id: "${DISCORD_CHANNEL_ID}"
      agents: [claude, gemini]    # fallback order

agents:
  claude:
    type: cli
    model: sonnet
    allowed_tools: [Bash, Read, Write, Edit, Glob, Grep]
  gemini:
    type: cli
    model: gemini-2.0-flash
  codex:
    type: cli
    model: o4-mini
```

Secrets can live in a `.env` file — `${VAR}` placeholders are substituted automatically.

### 4. Run

```bash
source .venv/bin/activate
oh-my-agent
```

## Usage

### Messages
- **Post a message** in the configured channel → bot creates a thread and replies
- **Reply in the thread** → bot responds with full conversation context
- Each reply is prefixed with `-# via **agent-name**`
- If an agent fails, the next one in the fallback chain takes over

### Slash Commands
| Command | Description |
|---------|-------------|
| `/ask <question>` | Ask the AI (creates a new thread) |
| `/reset` | Clear conversation history for current thread |
| `/agent` | Show available agents and their status |
| `/search <query>` | Search across all conversation history |

### Streaming
Claude agent streams responses in real-time — Discord messages are edited in-place as tokens arrive.

### Session Resume
Claude agent tracks session IDs per thread. Subsequent messages in the same thread use `--resume` to continue the session without re-flattening history.

## Agents

| Agent | CLI | Sandbox | Streaming | Notes |
|-------|-----|---------|-----------|-------|
| Claude | `claude` | `--allowedTools` | Yes (`stream-json`) | Session resume via `--resume` |
| Gemini | `gemini` | `--sandbox` (optional) | No | `--yolo` for auto-approve |
| Codex | `codex` | `--full-auto` (built-in) | No | OS-level sandbox by default |

## Development

```bash
pip install -e ".[dev]"
pytest                        # run all tests
pytest -k "test_fallback"     # run a specific test
```

See [`docs/todo.md`](docs/todo.md) for the roadmap and [`docs/development.md`](docs/development.md) for architecture decisions.
