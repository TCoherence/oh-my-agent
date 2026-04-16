# Oh My Agent

Multi-platform bot that routes messages to CLI-based AI agents (Claude, Gemini, Codex) with persistent memory, autonomous task execution, and scheduled automations.

Inspired by [OpenClaw](https://openclaw.dev).

## Features

- **Multi-Agent Fallback** — Claude, Gemini, and Codex run as CLI subprocesses; if one fails, the next in line takes over automatically
- **Persistent Memory** — SQLite conversation history with FTS5 search, plus a date-based adaptive memory system that auto-extracts, promotes, and injects user knowledge across sessions
- **Autonomous Runtime** — durable task state machine with merge gates, worktree isolation, HITL prompts, and Discord approval buttons
- **Skill System** — bidirectional skill sync across agent directories, skill evaluation with auto-disable, and agent-driven skill creation
- **Scheduled Automations** — cron / interval jobs defined as YAML files with hot-reload, per-job `auto_approve`, and `/automation_run` manual trigger
- **Workspace Isolation** — three-layer sandbox: workspace cwd confinement, env-var whitelisting, and CLI-native sandboxing
- **Intent Router** — optional LLM-based classification routes messages to reply, skill invocation, task proposal, or skill creation
- **Image Support** — Discord attachment download, per-agent image handling, and temp file lifecycle management
- **Platform Adapters** — Discord (full-featured with slash commands), Slack (stub), extensible via `BaseChannel` ABC

## Architecture

```text
User (Discord / Slack / ...)
         │ message, @agent prefix, or /ask command
         ▼
   GatewayManager
         │ routes to ChannelSession (per channel, isolated)
         ▼
   AgentRegistry ── [claude, gemini, codex]
         │ fallback order, or force specific agent
         ▼
   BaseCLIAgent.run(prompt, history)
     ├── ClaudeAgent      (session resume via --resume)
     ├── GeminiCLIAgent   (--yolo mode)
     └── CodexCLIAgent    (--full-auto, JSONL output)
         │
         ▼   cwd = workspace/ (sandbox-isolated)
   Response → Markdown-aware chunk → thread.send()
```

Seven subsystems: **Gateway** (platform adapters, slash commands, message routing), **Agents** (CLI subprocess wrappers with fallback), **Memory** (SQLite + date-based adaptive memory), **Skills** (bidirectional sync, evaluation, creation), **Runtime** (autonomous task orchestration), **Router** (LLM intent classification), **Automation** (cron/interval scheduler).

→ Full architecture walkthrough: [EN](docs/EN/architecture.md) · [中文](docs/CN/architecture.md)

## Quick Start

### Prerequisites

- Python 3.11+
- At least one CLI agent installed: [`claude`](https://docs.anthropic.com/en/docs/claude-code), [`gemini`](https://github.com/google-gemini/gemini-cli), or [`codex`](https://github.com/openai/codex)
- A Discord bot token with Message Content Intent enabled

### Install

```bash
git clone https://github.com/TCoherence/oh-my-agent.git
cd oh-my-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Configure

```bash
cp .env.example .env          # put secrets here (DISCORD_BOT_TOKEN, etc.)
cp config.yaml.example config.yaml   # adjust channels, agents, features
```

Key config sections: `gateway` (platform + agents), `memory`, `skills`, `runtime`, `automations`, `workspace`, `router`. Secrets use `${ENV_VAR}` substitution from `.env`.

→ Full config reference: [`config.yaml.example`](config.yaml.example)

### Run

```bash
oh-my-agent           # start the bot
oh-my-agent --version # check installed version
```

### Docker

```bash
./scripts/docker-build.sh                # build image
./scripts/docker-run.sh                  # dev/foreground mode
./scripts/docker-start.sh                # long-running/detached mode
./scripts/docker-logs.sh                 # inspect logs
```

The Docker image preinstalls `claude`, `gemini`, and `codex` CLIs. The host repo is mounted at `/repo` and runtime state at `/home`.

→ Full Docker & deployment guide: [EN](docs/EN/operator-guide.md) · [中文](docs/CN/operator-guide.md)

## Usage

### Messages

- Post in the configured channel → auto-creates a thread with an AI reply
- Reply inside the thread to continue the conversation with full context
- Prefix with `@claude`, `@gemini`, or `@codex` to force a specific agent for that turn
- Attach images (≤10 MB) for visual analysis

### Slash Commands

| Category | Commands |
|----------|----------|
| **Conversation** | `/ask`, `/reset`, `/history`, `/agent`, `/search` |
| **Runtime Tasks** | `/task_start`, `/task_status`, `/task_list`, `/task_approve`, `/task_reject`, `/task_suggest`, `/task_resume`, `/task_stop`, `/task_merge`, `/task_discard`, `/task_changes`, `/task_logs`, `/task_cleanup` |
| **Skills** | `/reload-skills`, `/skill_stats`, `/skill_enable` |
| **Automations** | `/automation_status`, `/automation_reload`, `/automation_enable`, `/automation_disable`, `/automation_run` |
| **Memory** | `/memories`, `/forget`, `/promote` |
| **Auth** | `/auth_login`, `/auth_status`, `/auth_clear` |

### Automations

Automation jobs are defined as YAML files in `~/.oh-my-agent/automations/`. The scheduler hot-reloads on file changes — no restart needed.

```yaml
name: daily-ai-briefing
enabled: true
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
prompt: "Run the market-briefing skill for today's AI digest."
agent: claude
skill_name: market-briefing
cron: "0 9 * * *"
auto_approve: true
```

→ Full automation reference: [`automation.yaml.example`](automation.yaml.example)

### Autonomous Runtime

Long-running tasks are orchestrated through a durable state machine:

```
DRAFT → RUNNING → VALIDATING → WAITING_MERGE → MERGED / COMPLETED
                              ↕ PAUSED          ↕ FAILED / STOPPED
```

- **Task types**: `artifact` (no merge gate), `repo_change` (merge required), `skill_change` (validate + merge)
- **Isolation**: each task runs in its own git worktree under `~/.oh-my-agent/runtime/tasks/`
- **HITL**: tasks can pause for owner approval, QR auth, or custom single-choice questions
- **Controls**: Discord buttons for approval + slash command fallback + natural language stop/pause/resume

## Built-in Skills

| Skill | Description |
|-------|-------------|
| `market-briefing` | Daily/weekly briefings for politics, finance, and AI with persisted report storage |
| `seattle-metro-housing-watch` | Seattle metro area housing market snapshots and deep-dives |
| `scheduler` | Create and validate automation YAML files |

Skills live in `skills/<name>/SKILL.md`. The `SkillSync` system distributes them to all CLI agent directories automatically.

→ Adding a new skill: create `skills/<name>/SKILL.md` (+ optional `scripts/`); it will be picked up on next startup or `/reload-skills`.

## Documentation

| Document | EN | 中文 |
|----------|----|------|
| Architecture | [architecture.md](docs/EN/architecture.md) | [architecture.md](docs/CN/architecture.md) |
| Operator Guide | [operator-guide.md](docs/EN/operator-guide.md) | [operator-guide.md](docs/CN/operator-guide.md) |
| Roadmap | [todo.md](docs/EN/todo.md) | [todo.md](docs/CN/todo.md) |
| Development Log | [development.md](docs/EN/development.md) | [development.md](docs/CN/development.md) |
| Changelog | [CHANGELOG.md](CHANGELOG.md) | — |
| v1.0 Plan | [v1.0-plan.md](docs/EN/v1.0-plan.md) | [v1.0-plan.md](docs/CN/v1.0-plan.md) |

## Versioning

The package version is sourced from [`src/oh_my_agent/_version.py`](src/oh_my_agent/_version.py). `CHANGELOG.md` tracks released and unreleased changes.

## License

MIT. See [LICENSE](LICENSE).
