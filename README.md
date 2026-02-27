# Oh My Agent

Multi-platform bot that routes messages to CLI-based AI agents (Claude, Gemini, Codex). Each platform channel maps to an independent agent session with persistent conversation memory and slash commands.

Inspired by [OpenClaw](https://openclaw.dev).

## Architecture

```
User (Discord / Slack / ...)
         │ message, @agent mention, or /ask command
         ▼
   GatewayManager
         │ routes to ChannelSession (per channel, isolated)
         ▼
   AgentRegistry ── [claude, gemini, codex]
         │ fallback order, or force specific agent
         ▼
   BaseCLIAgent.run(prompt, history)
     ├── ClaudeAgent    (claude CLI, session resume via --resume)
     ├── GeminiCLIAgent (gemini CLI)
     └── CodexCLIAgent  (codex CLI, built-in OS-level sandbox)
         │
         ▼   cwd = workspace/  (isolated from dev repo)
   Response → chunk → thread.send()
   (-# via **agent-name** attribution)
```

**Key layers:**
- **Gateway** — platform adapters (Discord implemented, Slack stub) with slash commands
- **Agents** — CLI subprocess wrappers with workspace isolation and ordered fallback
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
  path: .workspace/memory.db
  max_turns: 20

skills:
  enabled: true
  path: skills/

access:
  owner_user_ids: ["123456789012345678"]   # optional owner-only mode

# Sandbox isolation: agents run in this dir instead of the repo root.
# AGENT.md and skills are copied here on startup. Env vars are sanitized.
# Leave unset if you want agents to edit this repository directly.
# workspace: .workspace/agent

automations:
  enabled: true
  jobs:
    - name: daily-refactor
      platform: discord
      channel_id: "${DISCORD_CHANNEL_ID}"
      thread_id: "1476736679120207983"      # optional
      prompt: "Review TODOs and implement one coding task."
      agent: codex                            # optional
      interval_seconds: 86400
      initial_delay_seconds: 10

gateway:
  channels:
    - platform: discord
      token: ${DISCORD_BOT_TOKEN}
      channel_id: "${DISCORD_CHANNEL_ID}"
      agents: [claude, codex, gemini]    # fallback order

agents:
  claude:
    type: cli
    model: sonnet
    timeout: 300
    allowed_tools: [Bash, Read, Write, Edit, Glob, Grep]
    env_passthrough: [ANTHROPIC_API_KEY]   # only these env vars reach the subprocess
  gemini:
    type: cli
    model: gemini-3-flash-preview
    timeout: 120
  codex:
    type: cli
    model: gpt-5.3-codex
    timeout: 300
    skip_git_repo_check: true
    env_passthrough: [OPENAI_API_KEY]
```

Secrets can live in a `.env` file — `${VAR}` placeholders are substituted automatically.

Runtime files (SQLite DB, WAL/SHM, log files) are expected under `.workspace/`, which should be gitignored.

### 4. Run

```bash
source .venv/bin/activate
oh-my-agent
```

## Usage

### Messages
- **Post a message** in the configured channel → bot creates a thread and replies
- **Reply in the thread** → bot responds with full conversation context
- **Prefix with `@agent`** (for example `@gemini`, `@claude`, `@codex`) to force a specific agent for that message
- Each reply is prefixed with `-# via **agent-name**`
- If an agent fails, the next one in the fallback chain takes over
- If `access.owner_user_ids` is configured, only listed users can trigger the bot

### Slash Commands
| Command | Description |
|---------|-------------|
| `/ask <question> [agent]` | Ask the AI (creates a new thread, optional agent override) |
| `/reset` | Clear conversation history for current thread |
| `/history` | Show thread history (debug helper) |
| `/agent` | Show available agents and their status |
| `/search <query>` | Search across all conversation history |

### Agent Targeting
- **In-thread targeting**: send `@codex fix this` to run only Codex for that turn
- **New-thread targeting**: use `/ask` with the optional `agent` argument
- Prefix is stripped before dispatch, so the model receives only your actual question
- Unknown names are rejected early in `/ask` with a list of valid agents

### Session Resume
Claude session IDs are persisted per `(platform, channel_id, thread_id, agent)` in SQLite `agent_sessions`.
- On successful reply, latest `session_id` is upserted
- On bot restart, session IDs are loaded before handling the next message
- If `--resume` fails, in-memory + DB session entries are cleared and next turn falls back to flattened history

### Automations (MVP)
- Configure recurring jobs in `automations.jobs` (interval-based scheduler)
- Jobs reuse the same routing stack (`GatewayManager -> AgentRegistry`)
- Set `thread_id` to post into an existing thread, or omit to create a new thread each run
- Use `agent` to force a specific model for the job

## Agents

| Agent | CLI | Sandbox | Notes |
|-------|-----|---------|-------|
| Claude | `claude` | `--allowedTools` + workspace cwd | Session resume via `--resume`, persisted in DB |
| Gemini | `gemini` | `--yolo` + workspace cwd | Auto-approve all tool calls, shorter default timeout for faster fallback |
| Codex | `codex` | `--full-auto` (OS-level, built-in) | Uses `--json` parsing and `--skip-git-repo-check` by default |

## Sandbox Isolation

When `workspace` is set in `config.yaml`, three layers activate:

| Layer | What it does |
|-------|-------------|
| **L0 — Workspace cwd** | Agents run with `cwd=workspace` — CLI sandboxes (Codex `--full-auto`, Gemini cwd-write) are scoped to workspace, not the dev repo |
| **L1 — Env sanitization** | Only `PATH`, `HOME`, `LANG` etc. pass through; secrets require explicit `env_passthrough` per agent |
| **L2 — CLI-native sandbox** | Codex `--full-auto` (network blocked), Gemini `--yolo`, Claude `--allowedTools` |

Without `workspace`, the bot falls back to inheriting the full environment and running in the process cwd (backward-compatible).

## Skills

Skills are Markdown-described tools in `skills/{name}/SKILL.md` that CLI agents auto-discover. `SkillSync` runs bidirectional sync on startup:

- **Forward**: symlinks `skills/` → `.claude/skills/` and `.gemini/skills/` (dev mode)
- **Reverse**: copies agent-created skills back to `skills/` (canonical source)
- **Workspace**: copies skills into `workspace/.claude/skills/` and `workspace/.gemini/skills/` when workspace is configured

To add a skill: create `skills/{name}/SKILL.md`. It will be picked up on the next startup.
This repo includes a built-in `scheduler` skill to help agents manage `automations.jobs`.

## Development

```bash
pip install -e ".[dev]"
pytest                        # run all tests
pytest -k "test_fallback"     # run a specific test
```

See [`docs/todo.md`](docs/todo.md) for the roadmap and [`docs/development.md`](docs/development.md) for architecture decisions.
