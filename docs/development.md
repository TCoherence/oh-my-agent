# Development Log

## Project Overview

**Oh My Agent** — a multi-platform bot that uses CLI-based AI agents (Claude, Gemini, etc.) or API agents (Anthropic, OpenAI) as the execution layer, instead of calling model APIs directly. Inspired by OpenClaw.

---

## v0.2.0 — Gateway + Multi-Agent Architecture

### What Changed

Full architectural refactor to support:
1. **Gateway layer** — platform-agnostic channel abstraction, each channel maps to an independent session
2. **Agent registry** — ordered fallback across multiple agents, with attribution in replies
3. **Thread-level conversation history** — same thread retains context across messages
4. **API agents** — direct SDK calls to Anthropic and OpenAI (alongside CLI agents)
5. **YAML config** — `config.yaml` replaces flat `.env` for structured multi-channel setup

### Architecture

```
User (Discord / Slack / ...)
         │ message
         ▼
   GatewayManager
         │ routes to ChannelSession
         ▼
   ChannelSession ──── histories: {thread_id → [turns]}
         │
         ▼
   AgentRegistry ─── [claude, gemini, anthropic_api, ...]
         │ fallback chain
         ▼
   BaseAgent.run(prompt, history)
     ├── BaseCLIAgent (claude, gemini)
     │     subprocess → flatten history into prompt
     └── BaseAPIAgent (anthropic, openai)
           SDK → native messages array
         │
         ▼
   Response → chunk → send to thread
   (with `-# via **agent_name**` attribution)
```

### Project Structure

```
src/oh_my_agent/
  main.py                        # Entry: load config.yaml, build GatewayManager
  config.py                      # YAML loader with ${ENV_VAR} substitution
  gateway/
    base.py                      # IncomingMessage, BaseChannel ABC
    session.py                   # ChannelSession (per-channel state + thread histories)
    manager.py                   # GatewayManager: routes messages, calls agents
    platforms/
      discord.py                 # Discord adapter
      slack.py                   # Slack stub (NotImplementedError)
  agents/
    base.py                      # BaseAgent ABC + AgentResponse
    registry.py                  # AgentRegistry with ordered fallback
    cli/
      base.py                    # BaseCLIAgent (shared subprocess + history → prompt)
      claude.py                  # Claude CLI agent
      gemini.py                  # Gemini CLI agent
    api/
      base.py                    # BaseAPIAgent
      anthropic.py               # Anthropic API agent (multi-turn)
      openai.py                  # OpenAI API agent (multi-turn)
  utils/
    chunker.py                   # Message chunking for platform limits
```

### Configuration

```yaml
# config.yaml
gateway:
  channels:
    - platform: discord
      token: ${DISCORD_BOT_TOKEN}
      channel_id: "${DISCORD_CHANNEL_ID}"
      agents: [claude, gemini]   # fallback order

agents:
  claude:
    type: cli
    cli_path: claude
    max_turns: 25
    model: sonnet
    allowed_tools: [Bash, Read, Edit, Glob, Grep]
  gemini:
    type: cli
    cli_path: gemini
  anthropic_api:
    type: api
    provider: anthropic
    model: claude-sonnet-4-6
    api_key: ${ANTHROPIC_API_KEY}
```

### Implementation Status (v0.2.0)

| Component | Status |
|---|---|
| `config.py` — YAML loader | Done |
| `agents/base.py` — history param | Done |
| `agents/registry.py` — fallback | Done |
| `agents/cli/base.py` — subprocess + history | Done |
| `agents/cli/claude.py` | Done |
| `agents/cli/gemini.py` | Done |
| `agents/api/anthropic.py` | Done |
| `agents/api/openai.py` | Done |
| `gateway/base.py` — IncomingMessage, BaseChannel | Done |
| `gateway/session.py` — ChannelSession | Done |
| `gateway/manager.py` — GatewayManager | Done |
| `gateway/platforms/discord.py` | Done |
| `gateway/platforms/slack.py` | Stub |
| End-to-end testing | Pending |

### Roadmap (Future)

- [ ] Slack adapter (full implementation)
- [ ] Telegram adapter
- [ ] Streaming responses (incremental message edits)
- [ ] Cross-session memory sharing
- [ ] Agent selection via @mention (`@claude`, `@gemini`)
- [ ] Slash commands
- [ ] Rate limiting / request queue
- [ ] File attachment support

---

## v0.1.0 — MVP (Discord + Claude CLI)

Initial working bot: Discord channel → Claude CLI → thread reply.

- Single platform (Discord), single agent (Claude CLI)
- No conversation history (stateless per message)
- Environment variable config

---

## Architecture Decisions

### Why CLI subprocess instead of API SDK?
The `claude` CLI provides a complete agentic loop (tool use, file operations, bash, context management). Wrapping it as a subprocess means all of that is available without reimplementing it. The `BaseAPIAgent` path exists for cases where you want simpler stateless or multi-turn calls without tool use.

### Why session-per-channel?
Each channel is an independent workspace. Cross-channel contamination would be confusing and potentially leak context between users/projects. Future: opt-in shared memory via explicit memory module.

### Why flatten history into prompt for CLI agents?
CLI agents are stateless by design. Flattening history into the prompt string is the simplest approach. The alternative (`claude --resume <session_id>`) requires JSON output parsing and session ID storage — added complexity for future versions.

### Why `asyncio.create_subprocess_exec` not `subprocess.run`?
`subprocess.run` blocks the event loop. With multiple concurrent channel sessions and async Discord/Slack clients, a blocking call would stall all other processing.

### Why `--dangerously-skip-permissions`?
Required for headless mode. Without it, the CLI prompts for permission confirmations that cannot be answered in a subprocess. `--allowedTools` still constrains what the agent can do.
