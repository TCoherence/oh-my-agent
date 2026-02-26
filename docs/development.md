# Development Log

## Project Overview

**Oh My Agent** — a multi-platform bot that uses CLI-based AI agents (Claude, Gemini, etc.) as the execution layer, instead of calling model APIs directly. Inspired by OpenClaw.

> **Architecture direction (v0.4+):** CLI-first. API agents (`agents/api/`) are deprecated — CLI agents provide a complete agentic loop (tool use, skills, context management) that API SDK calls cannot match without significant reimplementation. See [future_planning_discussion.md](future_planning_discussion.md) for full rationale.

---

## v0.4.0 — CLI-First Cleanup + Skill Sync (Planned)

### Goals

1. **Deprecate API agent layer** — `agents/api/` kept for reference but no longer maintained. Remove from `config.yaml.example` and README. CLI agents are the sole execution path going forward.
2. **SkillSync reverse sync** — when a CLI agent creates a new skill in `.gemini/skills/` or `.claude/skills/`, detect and copy it back to the canonical `skills/` directory. Two-pronged approach:
   - **Instruction**: update `AGENT.md` to tell agents to write skills directly to `skills/`.
   - **Safety net**: post-response hook in `GatewayManager.handle_message()` diffs CLI skill dirs for new non-symlink entries.
3. **Streaming responses** — CLI-only via `--output-format stream-json`. Edit Discord messages in-place.
4. **Slash commands** — `/reset`, `/agent`, `/search`. Requires `discord.app_commands`.

### Architecture Impact

```
BaseAgent
  └── BaseCLIAgent  →  claude, gemini, codex (future)
       │
       └── agents/api/  DEPRECATED — no new development
```

SkillSync becomes bidirectional:

```
skills/ (canonical) ←──reverse sync──┐
  └─ SkillSync.sync() ──→ .gemini/skills/ (symlink)
                        ──→ .claude/skills/ (symlink)
                                      │
                        CLI agent creates skill here
```

See [todo.md](todo.md) for the full versioned roadmap (v0.4 → v0.5 → v0.6).

---

## v0.3.0 — Memory + Skills

### What Changed

1. **Memory layer** — `MemoryStore` ABC + `SQLiteMemoryStore` persists all conversation turns to `data/memory.db`. WAL mode, FTS5 full-text search, thread-level CRUD.
2. **History compression** — `HistoryCompressor` auto-summarises old turns when `len(turns) > max_turns`. Uses the first available agent to generate a summary; falls back to truncation if all agents fail. Runs asynchronously after each response.
3. **Skill system** — `SkillSync` symlinks skills from `skills/` to `.gemini/skills/` and `.claude/skills/` on startup. Both CLIs auto-discover `SKILL.md` files via the Agent Skills standard.
4. **Async session API** — `ChannelSession.get_history()`, `append_user()`, `append_assistant()` are now async. In-memory cache avoids repeated DB reads.
5. **Agent config** — `CLAUDE.md` → `AGENT.md` (shared via symlinks to `CLAUDE.md` and `GEMINI.md` so all CLIs read the same project context).

### New Files

```
src/oh_my_agent/
  memory/
    store.py                     # MemoryStore ABC + SQLiteMemoryStore (WAL, FTS5)
    compressor.py                # HistoryCompressor (agent summary + truncation fallback)
  skills/
    skill_sync.py                # SkillSync: symlinks skills/ → CLI native dirs

skills/                          # Skill definitions (Agent Skills standard)
  weather/
    SKILL.md
    scripts/weather.sh
```

### Database Schema

```sql
turns(id, platform, channel_id, thread_id, role, content, author, agent, created_at)
turns_fts(content)               -- FTS5 full-text index
summaries(id, platform, channel_id, thread_id, summary, turns_start, turns_end, created_at)
```

### New Config Sections

```yaml
memory:
  backend: sqlite
  path: data/memory.db
  max_turns: 20
  summary_max_chars: 500

skills:
  enabled: true
  path: skills/
```

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

> Moved to [todo.md](todo.md) — versioned roadmap starting from v0.4.0.

---

## v0.1.0 — MVP (Discord + Claude CLI)

Initial working bot: Discord channel → Claude CLI → thread reply.

- Single platform (Discord), single agent (Claude CLI)
- No conversation history (stateless per message)
- Environment variable config

---

## Architecture Decisions

### Why CLI subprocess instead of API SDK?
The `claude` CLI provides a complete agentic loop (tool use, file operations, bash, context management). Wrapping it as a subprocess means all of that is available without reimplementing it.

### Why deprecate API agents? (v0.4+)
CLI agents and API agents have a fundamental incompatibility in abstraction level:

| Dimension | CLI Agent | API Agent |
|-----------|-----------|----------|
| Context Engineering | CLI manages it (AGENT.md, skills, tool use) | Must build from scratch |
| Tool Use | Built-in (Bash, Read, Edit, Grep…) | Must define function schemas |
| Skill System | Native (SKILL.md auto-discovery) | Cannot use |
| Iteration Cost | Zero — CLI upgrades are free | Must track API changes + build infra |

Maintaining both paths doubles the surface area without proportional value. The API agent code is kept for reference but receives no new development.

### Why session-per-channel?
Each channel is an independent workspace. Cross-channel contamination would be confusing and potentially leak context between users/projects. Future: opt-in shared memory via explicit memory module.

### Why flatten history into prompt for CLI agents?
CLI agents are stateless by design. Flattening history into the prompt string is the simplest approach. The alternative (`claude --resume <session_id>`) requires JSON output parsing and session ID storage — added complexity for future versions.

### Why `asyncio.create_subprocess_exec` not `subprocess.run`?
`subprocess.run` blocks the event loop. With multiple concurrent channel sessions and async Discord/Slack clients, a blocking call would stall all other processing.

### Why `--dangerously-skip-permissions`?
Required for headless mode. Without it, the CLI prompts for permission confirmations that cannot be answered in a subprocess. `--allowedTools` still constrains what the agent can do.
