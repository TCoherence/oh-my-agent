# Oh My Agent

Multi-platform bot that routes messages to CLI-based AI agents (Claude, Gemini, Codex). Each platform channel maps to an independent agent session with persistent conversation memory and slash commands.

Inspired by [OpenClaw](https://openclaw.dev).

## Status Snapshot (2026-02-27)

- `/search` is implemented with SQLite FTS5 across all threads.
- `SkillSync` reverse sync is implemented and runs on startup.
- v0.5 is runtime-first: durable autonomous task loops (`DRAFT -> RUNNING -> WAITING_MERGE -> MERGED/...`).
- Discord approvals use buttons first, slash fallback, reactions as status-only signals.
- Optional LLM routing is implemented: incoming messages can be classified as `reply_once` or `propose_task`, with human confirmation before task execution.
- Runtime observability is implemented: `/task_logs`, sampled progress events in SQLite, and a single updatable Discord status message.

## Architecture

```text
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
     ├── ClaudeAgent
     ├── GeminiCLIAgent
     └── CodexCLIAgent
         │
         ▼   cwd = workspace/ (isolated from dev repo)
   Response → chunk → thread.send()
```

Key layers:
- Gateway: platform adapters and slash commands
- Agents: CLI subprocess wrappers with workspace isolation and ordered fallback
- Memory: SQLite + FTS5 persistent conversation history
- Skills: sync between `skills/` and CLI-native directories

## Setup

### Prerequisites

- Python 3.11+
- At least one CLI agent installed:
  - [`claude`](https://docs.anthropic.com/en/docs/claude-code)
  - [`gemini`](https://github.com/google-gemini/gemini-cli)
  - [`codex`](https://github.com/openai/codex)
- A Discord bot token with Message Content Intent enabled

### Install

```bash
git clone <repo-url>
cd oh-my-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp config.yaml.example config.yaml
```

### Config Highlights

```yaml
memory:
  backend: sqlite
  path: ~/.oh-my-agent/runtime/memory.db

workspace: ~/.oh-my-agent/agent-workspace

short_workspace:
  enabled: true
  root: ~/.oh-my-agent/agent-workspace/sessions
  ttl_hours: 24
  cleanup_interval_minutes: 1440

router:
  enabled: true
  provider: openai_compatible
  base_url: https://api.deepseek.com/v1
  api_key_env: DEEPSEEK_API_KEY
  model: deepseek-chat
  timeout_seconds: 8
  max_retries: 1
  confidence_threshold: 0.55
  require_user_confirm: true

runtime:
  enabled: true
  worker_concurrency: 3
  worktree_root: ~/.oh-my-agent/runtime/tasks
  default_agent: codex
  default_test_command: "pytest -q"
  path_policy_mode: allow_all_with_denylist
  denied_paths: [".env", "config.yaml", ".workspace/**", ".git/**"]
  agent_heartbeat_seconds: 20
  test_heartbeat_seconds: 15
  test_timeout_seconds: 600
  progress_notice_seconds: 30
  progress_persist_seconds: 60
  log_event_limit: 12
  log_tail_chars: 1200
  cleanup:
    enabled: true
    interval_minutes: 60
    retention_hours: 72
    prune_git_worktrees: true
    merged_immediate: true
```

Secrets can live in `.env`; `${VAR}` placeholders are substituted automatically.

Runtime artifacts default to `~/.oh-my-agent/runtime/` (memory DB, logs, task worktrees). Legacy `.workspace/` is migrated automatically on startup.

### Run

```bash
source .venv/bin/activate
oh-my-agent
```

## Usage

### Messages

- Post a message in the configured channel to create a thread and get a reply.
- Reply inside the thread to continue with full context.
- Prefix with `@gemini`, `@claude`, or `@codex` to force an agent for that turn.
- If an agent fails, the next one in the fallback chain takes over.
- If `access.owner_user_ids` is configured, only those users can trigger the bot.

### Slash Commands

- `/ask <question> [agent]`
- `/reset`
- `/history`
- `/agent`
- `/search <query>`
- `/task_start`
- `/task_status <task_id>`
- `/task_list [status]`
- `/task_approve <task_id>`
- `/task_reject <task_id>`
- `/task_suggest <task_id> <suggestion>`
- `/task_resume <task_id> <instruction>`
- `/task_stop <task_id>`
- `/task_merge <task_id>`
- `/task_discard <task_id>`
- `/task_changes <task_id>`
- `/task_logs <task_id>`
- `/task_cleanup [task_id]`

## Autonomous Runtime

- Long-task intent can create runtime tasks automatically.
- Runtime tasks execute in isolated git worktrees under `~/.oh-my-agent/runtime/tasks/<task_id>`.
- Loop contract: code changes -> tests -> retry until `TASK_STATE: DONE` and tests pass.
- High-risk tasks go to `DRAFT`; low-risk tasks can auto-run under `strict` policy.
- Completed execution enters `WAITING_MERGE`; final apply requires merge/discard/request-changes.
- `MERGED` tasks clean their worktree immediately after merge; other terminal states are retained for 72 hours before janitor cleanup.
- Short `/ask` conversations use transient per-thread workspaces with TTL cleanup.
- `/task_logs` exposes recent runtime events plus output tails.
- Discord progress prefers updating one status message instead of spamming many messages.

## Current Limits

- Runtime stop/resume is still command-driven; message-driven runtime control is not implemented yet.
- `stop` changes task state but does not yet guarantee immediate interruption of a running agent/test subprocess.
- Skill creation exists as tooling/workflow foundation, but not yet as a first-class runtime task type with intent routing.

## Documentation

- Chinese README: [docs/CN/README.md](docs/CN/README.md)
- English roadmap: [docs/EN/todo.md](docs/EN/todo.md)
- Chinese roadmap: [docs/CN/todo.md](docs/CN/todo.md)
- English development log: [docs/EN/development.md](docs/EN/development.md)
- Chinese development log: [docs/CN/development.md](docs/CN/development.md)
- English runtime plan: [docs/EN/v0.5_runtime_plan.md](docs/EN/v0.5_runtime_plan.md)
- Chinese runtime plan: [docs/CN/v0.5_runtime_plan.md](docs/CN/v0.5_runtime_plan.md)
- Archived discussion: [docs/archive/future_planning_discussion.md](docs/archive/future_planning_discussion.md)
