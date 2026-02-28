# Oh My Agent

Multi-platform bot that routes messages to CLI-based AI agents (Claude, Gemini, Codex). Each platform channel maps to an independent agent session with persistent conversation memory and slash commands.

Inspired by [OpenClaw](https://openclaw.dev).

## Status Snapshot (2026-02-28)

- `/search` is implemented with SQLite FTS5 across all threads.
- `SkillSync` reverse sync is implemented and runs on startup.
- v0.5 is runtime-first: durable autonomous task loops (`DRAFT -> RUNNING -> WAITING_MERGE -> MERGED/...`).
- v0.6 direction is skill-first autonomy; v0.7 expands into ops-first and hybrid autonomy.
- Discord approvals use buttons first, slash fallback, reactions as status-only signals.
- Optional LLM routing is implemented: incoming messages can be classified as `reply_once`, `invoke_existing_skill`, `propose_artifact_task`, `propose_repo_task`, or `create_skill`.
- Runtime observability is implemented: `/task_logs`, sampled progress events in SQLite, and a single updatable Discord status message.
- Runtime logging is split into service-level and per-agent logs under `~/.oh-my-agent/runtime/logs/`.
- Multi-type runtime is implemented: only `repo_change` and `skill_change` tasks use merge gate; `artifact` tasks complete without merge.

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
- Explicit installed skill invocation such as `@claude /weather Shanghai` or `@claude /top-5-daily-news` stays in direct chat flow and does not create a runtime task.
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
- Runtime now distinguishes task types:
  - `artifact`: long-running execution that returns a reply or generated artifact and does not use merge gate
  - `repo_change`: code/docs/test/config changes that run in worktrees and require merge
  - `skill_change`: canonical `skills/<name>` changes that validate and then require merge
- `repo_change` and `skill_change` execute in isolated git worktrees under `~/.oh-my-agent/runtime/tasks/<task_id>`.
- `artifact` tasks use runtime orchestration without entering `WAITING_MERGE`; `TASK_STATE: DONE` plus successful validation leads to `COMPLETED`.
- High-risk tasks go to `DRAFT`; low-risk `artifact` tasks can run without approval by default.
- `MERGED` tasks clean their worktree immediately after merge; other terminal states are retained for 72 hours before janitor cleanup.
- Short `/ask` conversations use transient per-thread workspaces under `~/.oh-my-agent/agent-workspace/sessions/`; these are not runtime worktrees and are cleaned by TTL janitor.
- `/task_logs` exposes recent runtime events plus output tails.
- Runtime writes two log layers:
  - service log: `~/.oh-my-agent/runtime/logs/oh-my-agent.log`
  - underlying agent logs: `~/.oh-my-agent/runtime/logs/agents/<task>-step<step>-<agent>.log`
- Discord progress prefers updating one status message instead of spamming many messages.

## Artifact Delivery

- The current delivery direction is:
  - try direct attachment upload first
  - fall back to a link when the artifact is too large for the target platform
  - keep delivery behind an abstraction so local-first runs can use direct filesystem access now and remote deployments can plug in object storage later
- This delivery layer is a platform/runtime capability, not just prompt behavior.
- Recommended storage direction for remote deployment is S3-compatible object storage, with Cloudflare R2 as the preferred default because it keeps the integration simple and works well for presigned-link delivery.

## Codex Integration Notes

- Codex support is currently grounded in CLI execution, `AGENTS.md`, and platform-level routing/runtime behavior.
- Project-level native Codex skill discovery is not treated as a reliable primitive yet.
- The practical near-term assumption is:
  - Claude/Gemini use workspace skill directories refreshed by `SkillSync`
  - Codex uses global Codex skills plus a generated workspace `AGENTS.md` that references workspace-local `.codex/skills/`
- `.codex/skills` remains deferred until there is confirmed project-level native discovery behavior.

## Workspace Layout

- `~/.oh-my-agent/agent-workspace/` is the base external workspace used by CLI agents.
- `~/.oh-my-agent/agent-workspace/sessions/` stores per-thread transient workspaces for normal chat turns.
- `~/.oh-my-agent/agent-workspace/.codex/skills/` is refreshed so the workspace can expose Codex-oriented skill references through `AGENTS.md`.
- `~/.oh-my-agent/runtime/tasks/` stores isolated runtime task worktrees and artifact task output directories.
- The external workspace now uses a generated `AGENTS.md` as the single injected context document. Repo-root `AGENT.md`, `CLAUDE.md`, and `GEMINI.md` are no longer mirrored into the external workspace or session workspaces.

## Autonomy Direction

- v0.5 establishes the runtime-first baseline: durable task execution, merge gating, and recovery.
- v0.6 focuses on skill-first autonomy: skill creation, skill routing, skill validation, and reusable capability growth.
- v0.7 expands into ops-first and hybrid autonomy: scheduler-driven and trigger-driven operational workflows combined with skill growth.
- Source-code self-modification may exist as a high-risk, strongly gated capability, but it is not the default autonomy path.

## Current Limits

- Runtime stop/resume is still command-driven; message-driven runtime control is not implemented yet.
- `stop` changes task state but does not yet guarantee immediate interruption of a running agent/test subprocess.
- Artifact delivery is not finished yet: generated artifacts are tracked, but attachment-first and link-fallback delivery still needs a dedicated adapter layer.
- Runtime observability still lacks an in-memory live excerpt layer; `/task_logs` can read live agent log tails, but Discord status cards do not yet show the latest agent activity summary.
- Codex skill integration is still weaker than Claude/Gemini because project-level native Codex skill discovery is not yet a trusted path.

## Documentation

- Chinese README: [docs/CN/README.md](docs/CN/README.md)
- English roadmap: [docs/EN/todo.md](docs/EN/todo.md)
- Chinese roadmap: [docs/CN/todo.md](docs/CN/todo.md)
- English development log: [docs/EN/development.md](docs/EN/development.md)
- Chinese development log: [docs/CN/development.md](docs/CN/development.md)
- English runtime plan: [docs/EN/v0.5_runtime_plan.md](docs/EN/v0.5_runtime_plan.md)
- Chinese runtime plan: [docs/CN/v0.5_runtime_plan.md](docs/CN/v0.5_runtime_plan.md)
- Archived discussion: [docs/archive/future_planning_discussion.md](docs/archive/future_planning_discussion.md)
