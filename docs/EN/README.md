# Oh My Agent

A multi-platform bot that routes messages to CLI agents (Claude, Gemini, Codex), with persistent memory, autonomous task execution, and scheduled automations.

Inspired by [OpenClaw](https://openclaw.dev).

## Highlights

- **Multi-agent fallback** — Claude, Gemini, and Codex run as CLI subprocesses; if one fails, the registry tries the next
- **Persistent memory** — SQLite conversation history with FTS5 full-text search, plus a date-aware adaptive memory system (automatic extraction, promotion, cross-session injection)
- **Autonomous runtime** — durable task state machine with merge gate, worktree isolation, HITL prompts, and Discord approval buttons
- **Skill system** — bidirectional sync across CLI agent directories, skill evaluation and fallback, agent-driven skill creation
- **Scheduled automations** — YAML-file-driven cron / interval scheduling with hot reload, per-job `auto_approve`, and manual `/automation_run`
- **Workspace isolation** — three-layer sandbox: workspace cwd, environment whitelist, CLI-native sandbox
- **Intent router** — optional LLM classifier that routes messages to reply / skill invocation / task proposal / skill creation
- **Image support** — Discord attachment download, per-agent image handling, temp-file lifecycle
- **Platform adapters** — Discord (full, with slash commands) today; extend via `BaseChannel` ABC

## Architecture

```text
User (Discord / ...)
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

Seven major subsystems: **Gateway** (platform adapter, slash commands, message routing), **Agents** (CLI subprocess wrappers + automatic fallback), **Memory** (SQLite + date-adaptive memory), **Skills** (bidirectional sync, evaluation, creation), **Runtime** (autonomous task orchestration), **Router** (LLM intent classification), **Automation** (cron/interval scheduler).

→ Full architecture: [EN](architecture.md) · [中文](../CN/architecture.md)

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
cp .env.example .env                   # fill in tokens and secrets
cp config.yaml.example config.yaml     # tune channels, agents, features
```

Main sections: `gateway` (platform + agents), `memory`, `skills`, `runtime`, `automations`, `workspace`, `router`. Secrets are substituted from `.env` via `${ENV_VAR}`.

→ Full config reference: [`config.yaml.example`](../../config.yaml.example)

### Run

```bash
oh-my-agent           # start the bot
oh-my-agent --version # show version
```

### Docker

```bash
./scripts/docker-build.sh                # build image
./scripts/docker-run.sh                  # foreground (development)
./scripts/docker-start.sh                # background (long-running)
./scripts/docker-logs.sh                 # tail logs
```

The image preinstalls `claude`, `gemini`, and `codex`. The host repo is mounted at `/repo`; runtime state at `/home`.

→ Full Docker and deployment guide: [EN](operator-guide.md) · [中文](../CN/operator-guide.md)

## Usage

### Messages

- Post in a configured channel → bot creates a thread and replies
- Continue in the thread; the bot carries full context
- Prefix with `@claude`, `@gemini`, or `@codex` to force one agent for that turn
- Image attachments up to 10 MB are supported

### Slash commands

| Category | Commands |
|------|------|
| **Chat** | `/ask`, `/reset`, `/history`, `/agent`, `/search` |
| **Runtime tasks** | `/task_start`, `/task_status`, `/task_list`, `/task_approve`, `/task_reject`, `/task_suggest`, `/task_resume`, `/task_stop`, `/task_merge`, `/task_discard`, `/task_changes`, `/task_logs`, `/task_cleanup` |
| **Skills** | `/reload-skills`, `/skill_stats`, `/skill_enable` |
| **Automations** | `/automation_status`, `/automation_reload`, `/automation_enable`, `/automation_disable`, `/automation_run` |
| **Memory** | `/memories`, `/forget`, `/memorize` |
| **Auth** | `/auth_login`, `/auth_status`, `/auth_clear` |
| **Operator** | `/doctor` |

### Scheduled automations

Automations are YAML files under `~/.oh-my-agent/automations/`. The scheduler hot-reloads on file changes — no restart required.

```yaml
name: daily-ai-briefing
enabled: true
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
prompt: "Run the market-briefing skill and produce today's AI brief."
agent: claude
skill_name: market-briefing
cron: "0 9 * * *"
auto_approve: true
```

→ Full automation reference: [`automation.yaml.example`](../../automation.yaml.example)

### Autonomous runtime

Long tasks are orchestrated via a durable state machine:

```
DRAFT → RUNNING → VALIDATING → WAITING_MERGE → MERGED / COMPLETED
                              ↕ PAUSED          ↕ FAILED / STOPPED
```

- **Task types**: `artifact` (no merge), `repo_change` (merge gate), `skill_change` (validate + merge)
- **Isolation**: each task runs in its own git worktree at `~/.oh-my-agent/runtime/tasks/`
- **HITL**: tasks can pause for owner approval, QR login, or custom single-choice prompts
- **Control**: Discord buttons + slash-command fallback + natural-language stop/pause/resume

## Bundled skills

| Skill | Summary |
|-------|---------|
| `market-briefing` | Politics / finance / AI daily + weekly briefings with persistent report storage |
| `seattle-metro-housing-watch` | Seattle metro housing snapshot and deep-dive analysis |
| `scheduler` | Create and validate automation YAML files |

Skills live at `skills/<name>/SKILL.md`. `SkillSync` syncs them to all CLI agent directories.

→ Add a new skill: create `skills/<name>/SKILL.md` (optional `scripts/`). Active on next startup or `/reload-skills`.

## Docs

| Doc | EN | 中文 |
|-----|----|------|
| Architecture | [architecture.md](architecture.md) | [architecture.md](../CN/architecture.md) |
| Task model (types, router, states) | [task-model.md](task-model.md) | [task-model.md](../CN/task-model.md) |
| Operator guide | [operator-guide.md](operator-guide.md) | [operator-guide.md](../CN/operator-guide.md) |
| Roadmap | [todo.md](todo.md) | [todo.md](../CN/todo.md) |
| Development notes | [development.md](development.md) | [development.md](../CN/development.md) |
| Upgrade guide | [upgrade-guide.md](upgrade-guide.md) | [upgrade-guide.md](../CN/upgrade-guide.md) |
| Monitoring | [monitoring.md](monitoring.md) | [monitoring.md](../CN/monitoring.md) |
| Troubleshooting | [troubleshooting.md](troubleshooting.md) | [troubleshooting.md](../CN/troubleshooting.md) |
| Config reference | [config-reference.md](config-reference.md) | [config-reference.md](../CN/config-reference.md) |
| Release process | [release-process.md](release-process.md) | [release-process.md](../CN/release-process.md) |
| v1.0 plan | [v1.0-plan.md](v1.0-plan.md) | [v1.0-plan.md](../CN/v1.0-plan.md) |
| Changelog | [CHANGELOG.md](../../CHANGELOG.md) | — |

## Contributing + security

- [CONTRIBUTING.md](../../CONTRIBUTING.md) — developer setup, testing, PR conventions
- [SECURITY.md](../../SECURITY.md) — vulnerability disclosure process

## Versioning

The version is tracked in [`src/oh_my_agent/_version.py`](../../src/oh_my_agent/_version.py). `CHANGELOG.md` records released and unreleased changes.

## License

MIT. See [LICENSE](../../LICENSE).
