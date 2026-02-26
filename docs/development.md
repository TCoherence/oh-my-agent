# Development Log

## Project Overview

**Oh My Agent** — a Discord bot that uses CLI-based AI agents (starting with `claude`) as the execution layer, instead of calling model APIs directly. Inspired by OpenClaw.

## MVP Scope (v0.1.0)

### Core Features
- [x] Connect to a Discord channel
- [x] Reply in threads (not separate messages)
- [x] Use `claude` CLI as the underlying agent via subprocess
- [x] Extensible agent abstraction (`BaseAgent` ABC) for future CLI agents
- [x] Basic local system permissions

### Implementation Status

| Component | File | Status |
|---|---|---|
| Project scaffolding | `pyproject.toml`, `.env.example`, `.gitignore` | Done |
| Config layer | `src/oh_my_agent/config.py` | Done |
| Agent abstraction | `src/oh_my_agent/agents/base.py` | Done |
| Claude agent | `src/oh_my_agent/agents/claude.py` | Done |
| Message chunker | `src/oh_my_agent/utils/chunker.py` | Done |
| Discord bot | `src/oh_my_agent/bot.py` | Done |
| Entry point | `src/oh_my_agent/main.py` | Done |
| End-to-end testing | — | Pending |

### Not Yet Implemented (Future)
- [ ] Conversation memory within threads (currently stateless per message)
- [ ] Streaming responses (edit Discord message in-place as tokens arrive)
- [ ] Multiple CLI agents (codex, gemini) with routing via mentions
- [ ] Slash commands (`/ask claude ...`)
- [ ] Markdown-aware chunking (avoid splitting inside code blocks)
- [ ] Rate limiting / request queuing
- [ ] File attachment support
- [ ] Multi-channel support

## Architecture Decisions

### Why CLI subprocess instead of API SDK?

The `claude` CLI already provides a complete agentic loop (tool use, file read/write, bash execution, context management). Wrapping it as a subprocess means we get all of that for free without reimplementing it. This also keeps the bot decoupled from any specific Python SDK version.

### Why `discord.Client` instead of `commands.Bot`?

The MVP doesn't need slash commands or prefix commands. The bot listens to raw messages and responds in threads. `discord.Client` is simpler and sufficient.

### Why `--output-format text` instead of `json`?

For the MVP we only need the final text answer. The text format gives us exactly that with zero parsing overhead. When we need session tracking or cost metrics, we'll switch to JSON.

### Why stateless (no conversation memory)?

Each message gets a fresh `claude -p` invocation. This is a deliberate MVP simplification. Adding memory later can be done by:
1. Collecting thread history and passing as multi-turn prompt context
2. Using `claude --resume <session_id>` with JSON output to capture session IDs

### Why `asyncio.create_subprocess_exec`?

`subprocess.run()` is blocking and would freeze the Discord event loop while Claude is thinking. `asyncio.create_subprocess_exec` is non-blocking and lets the bot handle other events concurrently.

### Why `--dangerously-skip-permissions`?

Required for headless/non-interactive subprocess mode. Without this, the CLI would prompt for permission confirmations that cannot be answered by a subprocess. The `--allowedTools` flag still constrains which tools are available.

## Testing Checklist

- [ ] Bot logs "Bot is online" on startup
- [ ] Message in target channel creates a thread
- [ ] Thread name is the first ~90 chars of the message
- [ ] Bot shows typing indicator while processing
- [ ] Claude response appears in the thread
- [ ] Reply in existing thread sends response in the same thread
- [ ] Messages in other channels are ignored
- [ ] Messages from the bot itself are ignored
- [ ] Responses > 2000 chars are split into multiple messages
- [ ] Claude CLI not found → error message in thread
- [ ] Claude CLI timeout → timeout error in thread
