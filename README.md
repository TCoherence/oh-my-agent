# Oh My Agent

Multi-platform bot that routes messages to CLI-based AI agents (Claude, Gemini, Codex). Each platform channel maps to an independent agent session with persistent conversation memory and slash commands.

Inspired by [OpenClaw](https://openclaw.dev).

## Status Snapshot (2026-03-16)

- `/search` is implemented with SQLite FTS5 across all threads.
- `SkillSync` reverse sync is implemented and runs on startup.
- v0.5 is runtime-first: durable autonomous task loops (`DRAFT -> RUNNING -> WAITING_MERGE -> MERGED/...`).
- v0.6 skill-first autonomy + adaptive memory is complete.
- v0.7.2 extends the v0.7 line with auth-first runtime pause/resume, file-driven automations, generic Discord-first `ask_user` HITL, market-intel reporting, and skill-specific timeout overrides for slow direct skill invocations.
- Discord approvals use buttons first, slash fallback, reactions as status-only signals.
- Optional LLM routing is implemented: incoming messages can be classified as `reply_once`, `invoke_existing_skill`, `propose_artifact_task`, `propose_repo_task`, or `create_skill`.
- Runtime observability is implemented: `/task_logs`, sampled progress events in SQLite, and a single updatable Discord status message.
- Runtime logging is split into service-level and per-agent logs under `~/.oh-my-agent/runtime/logs/`.
- Gateway/message logs now distinguish direct replies, explicit skill invocations, and router-driven reply paths via `purpose=...`; background memory/compression agent runs inherit the same request ID for traceability.
- Multi-type runtime is implemented: only `repo_change` and `skill_change` tasks use merge gate; `artifact` tasks complete without merge.
- Runtime hardening is complete: true subprocess interruption, message-driven control (stop/pause/resume), PAUSED state, completion summaries, metrics.
- Automations are now file-driven under `~/.oh-my-agent/automations/`, with polling-based hot reload and per-file enable/disable.
- `market-intel-report` adds persisted bootstrap/daily/weekly report workflows under `~/.oh-my-agent/reports/market-intel/` for politics, finance, and AI trend tracking.
- Adaptive memory is implemented: auto-extraction from conversations, injection into agent prompts, `/memories` and `/forget` commands.
- CLI session resume is implemented for Claude, Codex, and Gemini, with persisted session IDs restored after restart.
- Auth-first QR login infrastructure is implemented for Discord owner flows, with local credential persistence and runtime resume hooks.
- Agent/control cooperation now supports `OMA_CONTROL` envelopes for both `auth_required` and generic `ask_user` challenges, so direct chat runs and runtime tasks can pause for owner input and then resume.

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

For a fuller architecture walkthrough, see:

- [docs/EN/architecture.md](docs/EN/architecture.md)
- [docs/CN/architecture.md](docs/CN/architecture.md)

## Setup

### Prerequisites

- Python 3.11+
- For local host execution, at least one CLI agent installed:
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
./.venv/bin/pip install -e .
cp .env.example .env
cp config.yaml.example config.yaml
```

Then:

- put secrets in `.env`
- keep `config.yaml` limited to `${ENV_VAR}` references
- update `DISCORD_BOT_TOKEN` and `DISCORD_CHANNEL_ID`
- optionally set `DEEPSEEK_API_KEY` if router is enabled

### Config Highlights

```yaml
memory:
  backend: sqlite
  path: ~/.oh-my-agent/runtime/memory.db
  adaptive:
    enabled: true
    memory_dir: ~/.oh-my-agent/memory

workspace: ~/.oh-my-agent/agent-workspace

short_workspace:
  enabled: true
  root: ~/.oh-my-agent/agent-workspace/sessions
  ttl_hours: 24
  cleanup_interval_minutes: 1440

skills:
  enabled: true
  path: skills/
  evaluation:
    enabled: true
    stats_recent_days: 7
    feedback_emojis: ["👍", "👎"]
    auto_disable:
      enabled: true
      rolling_window: 20
      min_invocations: 5
      failure_rate_threshold: 0.60
    overlap_guard:
      enabled: true
      review_similarity_threshold: 0.45
    source_grounded:
      enabled: true
      block_auto_merge: true

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

automations:
  enabled: true
  storage_dir: ~/.oh-my-agent/automations
  reload_interval_seconds: 5

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
    retention_hours: 168
    prune_git_worktrees: true
    merged_immediate: true

auth:
  enabled: true
  storage_root: ~/.oh-my-agent/runtime/auth
  qr_poll_interval_seconds: 3
  qr_default_timeout_seconds: 180
  providers:
    bilibili:
      enabled: true
      scope_key: default
```

Secrets should live in `.env`; `${VAR}` placeholders are substituted automatically.

Runtime cleanup removes old task workspaces and agent log files after the retention window. The default window is 7 days (`168` hours).

Runtime artifacts default to `~/.oh-my-agent/runtime/` (memory DB, logs, task worktrees). Legacy `.workspace/` is migrated automatically on startup.
Automation definitions now live under `~/.oh-my-agent/automations/*.yaml`; edits there are picked up automatically without restarting the process.

### Run

```bash
./.venv/bin/oh-my-agent
```

Check the installed version:

```bash
./.venv/bin/oh-my-agent --version
```

### Docker (Host-Isolated Runtime)

Run the bot inside Docker while keeping two bind mounts:

- runtime/state mount (`/home`) for `~/.oh-my-agent` data and runtime files
- repo mount (default current repo) so the agent can edit project code directly

Build image:

```bash
./scripts/docker-build.sh
```

Development / foreground mode (attached, `--rm`, good for interactive debugging):

```bash
./scripts/docker-run.sh
```

Long-running / managed mode (detached, `--restart unless-stopped`, keeps the container for `docker logs` / `docker inspect`):

```bash
./scripts/docker-start.sh
```

Inspect and manage the long-running container:

```bash
./scripts/docker-status.sh
./scripts/docker-logs.sh
./scripts/docker-stop.sh
```

Default config source is `/repo/config.yaml` (`OMA_CONFIG_PATH`).
Environment substitution is loaded from the config directory (typically `/repo/.env`).
Container start now expects repo config to be prepared before launch.
The image installs runtime dependencies only; normal execution does not rely on a second in-image source snapshot.
On each container start, the entrypoint installs `/repo` as an editable Python package (`pip install -e /repo --no-deps`), so the mounted repo is the runtime source of truth.
The image preinstalls `claude`, `gemini`, and `codex` CLIs.
Startup performs a fail-fast check for configured `agents.*.cli_path` binaries (`OMA_FAIL_FAST_CLI=0` to disable).
CLI login/auth state is still required and should be completed inside the mounted `/home` runtime path.
`./scripts/docker-run.sh` also applies Docker-only agent permission overrides: Claude runs with `--dangerously-skip-permissions`, and Codex runs with `danger-full-access` plus bypass enabled. Direct host startup keeps the safer config defaults instead.

Override mount paths when needed:

```bash
OMA_DOCKER_MOUNT=/path/to/your/mount ./scripts/docker-run.sh
OMA_DOCKER_REPO=/path/to/repo ./scripts/docker-run.sh
```

The same environment overrides also apply to `docker-start.sh`, `docker-logs.sh`, `docker-stop.sh`, and `docker-status.sh`.
These helper scripts target the container by exact name, not by fuzzy search. The default name is `oh-my-agent`, and you can override it with `OMA_CONTAINER_NAME` when running multiple copies:

```bash
OMA_CONTAINER_NAME=oma-prod ./scripts/docker-start.sh
OMA_CONTAINER_NAME=oma-prod ./scripts/docker-logs.sh
```

Override the Docker-only permission behavior when needed:

```bash
OMA_AGENT_CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=false \
OMA_AGENT_CODEX_SANDBOX_MODE=workspace-write \
OMA_AGENT_CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX=false \
./scripts/docker-run.sh
```

By default, the container workdir is `/home`, while the host repo is mounted at `/repo`.
This keeps day-to-day runtime/config work in `/home`, while still allowing edits and git operations in `/repo`.

If you prefer running from the mounted repo directly:

```bash
OMA_WORKDIR_IN_CONTAINER=/repo ./scripts/docker-run.sh
```

Then edit runtime config under the state mount:

- `${HOME}/oh-my-agent-docker-mount/...` (runtime outputs/state only)

Edit source config in repo:

- `/repo/config.yaml` (host path: your mounted repo)
- `/repo/.env` (host path: your mounted repo)

Run one-off commands in the same image:

```bash
./scripts/docker-run.sh oh-my-agent --version
```

For long-running mode, application logs still persist under the mounted runtime path, while `docker-logs.sh` gives you the container stdout/stderr stream. Keeping detached mode non-`--rm` is intentional so `docker logs` and `docker inspect` remain available for postmortem debugging.

Rebuild the image only when you change container-layer concerns such as `Dockerfile`, `docker/entrypoint.sh`, or Python/Node/system dependencies. Pure source edits under `/repo/src` normally only need a restart.

## Usage

### Messages

- Post a message in the configured channel to create a thread and get a reply.
- Reply inside the thread to continue with full context.
- Prefix with `@gemini`, `@claude`, or `@codex` to force an agent for that turn.
- Explicit installed skill invocation such as `@claude /weather Shanghai` or `@claude /top-5-daily-news` stays in direct chat flow and does not create a runtime task.
- A skill can optionally set `metadata.timeout_seconds` in its `SKILL.md` frontmatter to override the normal per-agent CLI timeout for that skill invocation only. This is intended for slow report/research skills, not as a global replacement for agent timeouts.
- If an agent fails, the next one in the fallback chain takes over.
- If `access.owner_user_ids` is configured, only those users can trigger the bot.

### CLI Session Resume

- Claude, Codex, and Gemini all persist CLI session IDs per thread.
- On restart, the gateway restores stored session IDs from SQLite and attempts to continue the original CLI conversation instead of flattening full history every turn.
- If a stored session is clearly stale or invalid, it is cleared automatically so the next turn can start fresh.
- Persisted stale sessions are also deleted when fallback succeeds through another agent.
- Important caveat: router skill discovery reads the current canonical `skills/` directory, but an existing resumed CLI session may still answer from older session context and not immediately recognize a newly added skill.
- In practice, new skills are most reliable in a fresh thread or fresh CLI session. `/reload-skills` refreshes skill directories, but it does not guarantee that an already-resumed Claude/Codex/Gemini session will pick up the new skill immediately.

### Workspace Refresh

- `~/.oh-my-agent/agent-workspace/AGENTS.md` is a generated file, not a hand-maintained source file.
- It mirrors repo-root `AGENTS.md` with visible generation metadata at the top.
- The base workspace stores a small source-state manifest and refreshes automatically before short-workspace turns when repo `AGENTS.md` or canonical `skills/` change.
- Session workspaces inherit the refreshed base workspace, so normal chat turns see updated rules and skills without a manual rebuild.

### Skill Evaluation

- Chat-path skill executions are tracked as structured telemetry with route source, latency, usage, and outcome.
- Discord `👍` / `👎` reactions on the first attributed skill response are stored as per-invocation feedback.
- `/skill_stats [skill]` reports recent success rate, usage, latency, feedback, and latest evaluation findings.
- `/skill_enable <skill>` clears an auto-disabled skill so router-based invocation can use it again.
- Auto-disable only removes a skill from automatic routing. Explicit `/skill-name` still works.
- Skill mutation tasks now gate auto-merge on:
  - overlap review for likely duplicate skills
  - source-grounded review for external repo/tool/reference adaptations
- External-source skill adaptations should populate `SKILL.md` frontmatter metadata:
  - `metadata.source_urls`
  - `metadata.adapted_from`
  - `metadata.adaptation_notes`

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
- `/auth_login [provider]`
- `/auth_status [provider]`
- `/auth_clear [provider]`
- `/automation_status [name]`
- `/automation_reload`
- `/automation_enable <name>`
- `/automation_disable <name>`

### Automations

- Automation source of truth is `~/.oh-my-agent/automations/*.yaml`, not `config.yaml`.
- The scheduler polls that directory and hot-reloads file additions, edits, deletes, and `enabled` flips without restarting the process.
- Scheduling supports:
  - `cron: "0 9 * * *"` for normal wall-clock schedules
  - `interval_seconds` for high-frequency local testing
- `cron` and `interval_seconds` are mutually exclusive.
- `initial_delay_seconds` is supported only with `interval_seconds`.
- Discord operator commands:
  - `/automation_status [name]` shows valid active + disabled automations
  - `/automation_reload` forces an immediate rescan instead of waiting for the polling interval
  - `/automation_enable <name>` and `/automation_disable <name>` update the YAML source file and reload scheduler state immediately
- Scheduler-fired automations now use the reply/artifact runtime path (`test_command=true`, single-step budget) instead of repo-change validation loops.
- If an automation is still running, the next fire for the same automation name is skipped instead of queueing overlapping runs.
- Completed automation messages now post the final result directly in Discord with the automation name, run ID, and an `_artifacts/<task_id>` locator for the generated files.
- Invalid or conflicting automation files remain log-visible for now; they are intentionally excluded from `/automation_status`.
- Runtime state is intentionally in-memory only for now:
  - restart recomputes the next fire time
  - there is no persisted `last_run` / `next_run` / `last_error`
  - missed jobs while the process is down are not replayed

Example automation file:

```yaml
name: daily-standup
enabled: true
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
delivery: channel
prompt: "Summarize open TODOs and suggest top 3 coding tasks."
agent: codex
cron: "0 9 * * *"
author: scheduler
```

Local testing example:

```yaml
name: hello-from-codex
enabled: false
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
delivery: dm
prompt: "Hello from the other side! Just checking in."
agent: codex
interval_seconds: 20
initial_delay_seconds: 10
author: scheduler
```

### Market-Intel Reports

- `market-intel-report` is one core skill for:
  - `bootstrap_backfill`
  - `daily_digest`
  - `weekly_synthesis`
- Reports are persisted under `~/.oh-my-agent/reports/market-intel/` as both Markdown and JSON:
  - `bootstrap/<domain>/<date>.md|json`
  - `daily/<date>/<domain>.md|json`
  - `weekly/<iso-week>/cross-domain.md|json`
- Domain model:
  - daily: `politics`, `finance`, `ai`
  - weekly: one `cross-domain` synthesis
- Bounded bootstrap defaults:
  - politics: 30 days
  - finance: 30 days
  - ai: 14 days
- Trend continuity is expected to come from persisted report files plus current-source research, not just Discord history.
- The bundled helper script lives at:
  - `skills/market-intel-report/scripts/report_store.py`
- The `scheduler` skill now validates file-driven automation YAML under `~/.oh-my-agent/automations/` instead of the old inline `config.yaml` job model.

### Auth QR Login

- Auth flows are owner-only; if `access.owner_user_ids` is empty, `/auth_*` commands stay disabled.
- Current provider support is intentionally narrow: `bilibili` only.
- `/auth_login bilibili` sends a QR code image into the current configured channel or thread.
- Successful scans persist cookies under `~/.oh-my-agent/runtime/auth/providers/bilibili/<owner_user_id>/`.
- QR PNGs under `~/.oh-my-agent/runtime/auth/qr/` are temporary and are deleted when the flow reaches a terminal state.
- Runtime tasks can move into `WAITING_USER_INPUT` when an agent emits an `OMA_CONTROL` auth challenge; once the QR flow completes, the linked task is re-queued automatically.
- Direct chat / explicit skill runs can also suspend on `OMA_CONTROL` auth challenges, store a resumable suspended run record, and continue the same agent session after login when possible.
- When a thread or task enters `auth_required`, Discord now sends an extra owner-ping message in the same thread and best-effort DMs all configured owners.
- Generic `ask_user` challenges are now supported on Discord:
  - direct chat / explicit skill runs can post a visible single-choice prompt, wait for an owner button click, and auto-resume the same run
  - runtime tasks can enter `WAITING_USER_INPUT`, post a visible single-choice prompt, and resume automatically after selection
  - each ask_user prompt always includes a built-in `Cancel` action
- `ask_user`, `DRAFT`, and `WAITING_MERGE` now also emit high-signal owner notifications: one separate ping message in the same thread plus best-effort owner DMs.
- Active ask_user prompts are persisted in SQLite and Discord button views are rehydrated on bot restart, so pending questions survive process restarts.
- In a waiting thread, replying `retry login`, `重新登录`, or `重新扫码` reissues the QR flow.
- `/memories [category]`
- `/forget <memory_id>`
- `/reload-skills`
- `/skill_stats [skill]`
- `/skill_enable <skill>`

## Autonomous Runtime

- Long-task intent can create runtime tasks automatically.
- Runtime now distinguishes task types:
  - `artifact`: long-running execution that returns a reply or generated artifact and does not use merge gate
  - `repo_change`: code/docs/test/config changes that run in worktrees and require merge
  - `skill_change`: canonical `skills/<name>` changes that validate and then require merge
- `WAITING_USER_INPUT` is the runtime pause state for owner interaction:
  - QR auth challenges
  - generic single-choice `ask_user` challenges
- `DRAFT`, `WAITING_MERGE`, `auth_required`, and `ask_user` are the only states that currently trigger owner notifications; routine runtime progress does not.
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
- Normal reply logs include `purpose=...` on `AGENT starting`, `AGENT_OK`, and `AGENT_ERROR`.
- Background memory extraction and history compression runs carry the originating request ID in both service logs and registry agent-attempt labels.

## Artifact Delivery

- The current delivery direction is:
  - try direct attachment upload first
  - fall back to a link when the artifact is too large for the target platform
  - keep delivery behind an abstraction so local-first runs can use direct filesystem access now and remote deployments can plug in object storage later
- This delivery layer is a platform/runtime capability, not just prompt behavior.
- Recommended storage direction for remote deployment is S3-compatible object storage, with Cloudflare R2 as the preferred default because it keeps the integration simple and works well for presigned-link delivery.

## Codex Integration Notes

- Codex support is currently grounded in CLI execution, `AGENTS.md`, and platform-level routing/runtime behavior.
- The practical near-term assumption is:
  - Claude/Gemini use workspace skill directories refreshed by `SkillSync`
  - Codex uses repo/workspace `.agents/skills/`

## Workspace Layout

- `~/.oh-my-agent/agent-workspace/` is the base external workspace used by CLI agents.
- `~/.oh-my-agent/agent-workspace/sessions/` stores per-thread transient workspaces for normal chat turns.
- `~/.oh-my-agent/memory/` stores date-based memory files:
  - `daily/YYYY-MM-DD.yaml` for append-only daily observations
  - `curated.yaml` for promoted long-term memories
  - `MEMORY.md` for the synthesized natural-language view of curated memory
- `~/.oh-my-agent/agent-workspace/.agents/skills/` is refreshed so Codex can use official repo/workspace skill discovery in external workspaces too.
- `~/.oh-my-agent/runtime/tasks/` stores isolated runtime task worktrees and artifact task output directories.
- `~/.oh-my-agent/automations/` stores file-driven automation definitions with hot reload.
- The external workspace now uses a generated `AGENTS.md` as the single injected context document. Repo-root `AGENT.md`, `CLAUDE.md`, and `GEMINI.md` are no longer mirrored into the external workspace or session workspaces.
- The generated workspace `AGENTS.md` includes visible metadata so it is clear when you are looking at a derived file instead of the repo source file.

## Autonomy Direction

- v0.5 establishes the runtime-first baseline: durable task execution, merge gating, and recovery.
- v0.6 focuses on skill-first autonomy + adaptive memory: skill creation, skill routing, skill validation, reusable capability growth, and cross-session user knowledge.
- v0.7 delivers date-based memory; v0.7.2 closes the current auth/runtime/video/automation/HITL pass on top of it.
- v0.8+ adds semantic memory retrieval (vector search) and broader hybrid autonomy.
- Source-code self-modification may exist as a high-risk, strongly gated capability, but it is not the default autonomy path.

## Current Limits

- Artifact delivery is not finished yet: generated artifacts are tracked, but attachment-first and link-fallback delivery still needs a dedicated adapter layer.
- Runtime observability still lacks an in-memory live excerpt layer; `/task_logs` can read live agent log tails, but Discord status cards do not yet show the latest agent activity summary.
- There is still no operator-facing doctor/self-diagnostics entrypoint in Discord when the service crashes or fails to start; today, debugging still requires direct access to server logs.
- Human-in-the-loop v1 is now implemented for Discord buttons only:
  - single-choice `ask_user` prompts
  - owner-only response handling
  - visible prompt + visible answer/cancel record
  - auto-resume for direct chat and runtime task paths
- Free-text HITL, multi-select prompts, non-Discord interactive UIs, and prompt expiry policies are still intentionally out of scope.
- Codex repo/workspace skill discovery now uses official `.agents/skills/`; the generated `AGENTS.md` is no longer used to enumerate workspace skills.
- Memory retrieval still uses Jaccard word-overlap for similarity; semantic (vector) retrieval remains a v0.8+ item.

## Documentation

- Documentation index: [docs/README.md](docs/README.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Chinese README: [docs/CN/README.md](docs/CN/README.md)
- English roadmap: [docs/EN/todo.md](docs/EN/todo.md)
- Chinese roadmap: [docs/CN/todo.md](docs/CN/todo.md)
- English development log: [docs/EN/development.md](docs/EN/development.md)
- Chinese development log: [docs/CN/development.md](docs/CN/development.md)
- Router smoke test: [docs/router_smoke.md](docs/router_smoke.md)
- Archived: [docs/archive/](docs/archive/)

## Versioning

- The package version is sourced from [`src/oh_my_agent/_version.py`](src/oh_my_agent/_version.py).
- `oh-my-agent --version` prints the installed version without requiring `config.yaml`.
- `CHANGELOG.md` is expected to move with the package version; released sections use `vX.Y.Z`.

## License

MIT. See [LICENSE](LICENSE).
