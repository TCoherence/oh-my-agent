# AGENT.md

This file provides guidance to AI coding agents (Claude Code, Gemini CLI, etc.) when working with this repository.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                  # core deps

# Run
cp config.yaml.example config.yaml   # then fill in tokens
oh-my-agent

# Tests
pip install -e ".[dev]"
pytest                            # full test suite
pytest tests/test_memory_store.py # single file
pytest -k "test_fallback"         # single test by name
```

## Architecture

The system has seven major subsystems.

**Gateway layer** (`src/oh_my_agent/gateway/`)

- `BaseChannel` ABC: platform adapter with `start()`, `create_thread()`, `send()`, `typing()`. Implemented for Discord with slash commands (other platforms post-1.0).
- `GatewayManager`: holds `(BaseChannel, AgentRegistry)` pairs, routes `IncomingMessage` to `handle_message()`. Manages `ChannelSession`s, triggers history compression in the background, touches the `IdleTracker` per message, and dispatches `Judge` runs on idle / `/memorize` / keyword triggers.
- `ChannelSession`: per-channel state with async API. Loads/persists per-thread conversation histories via `MemoryStore`. In-memory cache avoids repeated DB reads.
- Discord slash commands via `app_commands.CommandTree`:
  - Conversation: `/reset`, `/history`, `/agent`, `/search`
  - Runtime tasks: `/task_start`, `/task_status`, `/task_list`, `/task_approve`, `/task_reject`, `/task_suggest`, `/task_resume`, `/task_stop`, `/task_merge`, `/task_discard`, `/task_replace`, `/task_changes`, `/task_logs`, `/task_cleanup`
  - Skills: `/reload-skills`, `/skill_stats`, `/skill_enable`
  - Automations: `/automation_status`, `/automation_reload`, `/automation_enable`, `/automation_disable`, `/automation_run`
  - Auth: `/auth_login`, `/auth_status`, `/auth_clear`
  - Operator: `/doctor`, `/usage_today`, `/usage_thread`
  - Memory: `/memories`, `/forget`, `/memorize`, `/reflect_yesterday`
- Agent targeting:
  - Thread messages support `@claude` / `@gemini` / `@codex` prefix to force one agent for that turn.
  - Prefix is stripped before dispatch; agent name is passed via `IncomingMessage.preferred_agent`.
- Image attachments: `Attachment` dataclass (`filename`, `content_type`, `local_path`, `original_url`, `size_bytes`, `is_image` property). Discord `on_message` downloads `image/*` attachments (≤10 MB) to temp dir. `IncomingMessage.attachments` carries them through the pipeline. Image-only messages get a default analysis prompt.
- Message flow: `on_message` → `IncomingMessage` (+ attachments) → `GatewayManager.handle_message()` → (optional intent routing) → `AgentRegistry.run()` → `channel.send()` → temp file cleanup.
- Owner gate: `GatewayManager` can enforce `access.owner_user_ids`; system-generated messages bypass this gate.

**Agent layer** (`src/oh_my_agent/agents/`)

- `BaseAgent` ABC: `async run(prompt, history) → AgentResponse`.
- `AgentRegistry`: ordered `list[BaseAgent]` with automatic fallback — tries each in sequence, returns first success. Supports `force_agent` to bypass fallback. Passes `thread_id`, `image_paths` etc. via `inspect.signature` dispatch.
- `BaseCLIAgent` (`agents/cli/base.py`): subprocess runner for CLI agents. Accepts `workspace: Path | None` (sets subprocess `cwd`) and `passthrough_env: list[str] | None` (env var whitelist). Flattens `history` into prompt text. Subclasses override `_build_command()`.
- Concrete CLI agents: `ClaudeAgent` (session resume via `--resume`), `GeminiCLIAgent`, `CodexCLIAgent`.
  - Codex runs `codex exec --full-auto --json --skip-git-repo-check` and extracts assistant text from JSONL events.
  - Image handling: Claude and Gemini copy images to `workspace/_attachments/` and augment the prompt with file-reference instructions; Codex uses `--image` flag natively.

**Memory layer** (`src/oh_my_agent/memory/`)

- `MemoryStore` ABC + `SQLiteMemoryStore`: persists all turns to `~/.oh-my-agent/runtime/memory.db` (default) with WAL mode, FTS5 full-text search, thread-level CRUD, and `export_data()`/`import_data()` for backup.
- `agent_sessions` table persists CLI session IDs with primary key `(platform, channel_id, thread_id, agent)`.
- `GatewayManager` loads persisted session IDs on message handling and upserts/deletes them based on agent outcome.
- `HistoryCompressor`: when a thread exceeds `max_turns`, compresses old turns into a summary (via agent) or truncates (fallback). Runs asynchronously after each response.
- `JudgeStore` (`memory/judge_store.py`): single-tier YAML-backed memory store at `~/.oh-my-agent/memory/memories.yaml`. `MemoryEntry` fields: `id`, `summary`, `category` (`preference`/`workflow`/`project_knowledge`/`fact`), `scope` (`global_user`/`workspace`/`skill`/`thread`), `confidence`, `observation_count`, `evidence_log` (list of `EvidenceRecord{thread_id, ts, snippet}`), `source_skills`, `source_workspace`, `status` (`active`/`superseded`), `superseded_by`, `created_at`, `last_observed_at`. Atomic `save()`. `apply_actions()` executes judge-emitted ops (`add` / `strengthen` / `supersede` / `no_op`); `manual_supersede()` powers `/forget`. `get_relevant()` ranks active entries with scope bonus multipliers for injection. `should_synthesize()` returns true on dirty state, missing `MEMORY.md`, or mtime > 6 h; `synthesize_memory_md(registry)` regenerates the natural-language `MEMORY.md` via agent.
- `Judge` (`memory/judge.py`): event-driven LLM agent that replaces the per-turn memory extractor. `run(thread_id, conversation, store, registry)` builds a prompt that includes the full thread plus the current `active` memory list and asks the agent for an `actions` array. `add` creates entries; `strengthen` increments observation_count and appends evidence; `supersede` chains old → new; `no_op` is required when nothing is worth saving. Parse failures fall back to a simplified schema retry. Explicit `/memorize <summary>` short-circuits the LLM step.
- `IdleTracker` (`memory/idle_trigger.py`): per-thread `last_message_ts` tracker with a background polling task. When a thread is silent for `idle_seconds` (default 900 s = 15 min), invokes the registered `_on_fire` callback, which triggers `Judge.run()`. `touch()` resets the timer; `mark_judged()` prevents re-fire until the next user message; `forget()` drops state.
- Memory triggers: (1) idle 15 min, (2) Discord `/memorize [summary] [scope]`, (3) natural-language keyword match (configurable via `memory.judge.keyword_patterns`, e.g. `记一下` / `remember this`). All paths converge on `Judge.run()`. Injection still happens as a `[Remembered context]` block before agent prompts; only `status=active` entries are eligible.
- `SessionDiaryWriter` (`memory/session_diary.py`): append-only per-day markdown log under `runtime_root/diary/YYYY-MM-DD.md`. Fire-and-forget enqueue from `ChannelSession.append_user/append_assistant`; a single background worker drains the queue so writes never interleave. Operator-visible only — never read back by the agent. Disable via `memory.diary.enabled: false`; override path with `memory.diary.path`.

**Skill system** (`src/oh_my_agent/skills/`)

- `SkillSync`: bidirectional sync between `skills/` and CLI-native directories.
  - `sync()` — forward: symlinks `skills/` → `.gemini/skills/` and `.claude/skills/`.
  - `reverse_sync()` — copies non-symlink skills from CLI dirs back to `skills/`.
  - `full_sync()` — runs reverse then forward on startup.
- When `workspace` is configured, `_setup_workspace()` in `main.py` copies skills into `workspace/.claude/skills/` and `workspace/.gemini/skills/` (real files, not symlinks) so CLI agents find them from the workspace cwd.
- `SkillValidator` (`skills/validator.py`): validates SKILL.md frontmatter (name+description required), script syntax, and executable permissions.
- Agent-driven skill creation: `_try_skill_sync()` in `GatewayManager` detects new agent-created skills after each response, runs `full_sync()`, validates, and notifies via Discord.
- Bundled skills under `skills/` (10): `adapt-community-skill`, `bilibili-video-summary`, `deals-scanner`, `market-briefing`, `paper-digest`, `scheduler`, `seattle-metro-housing-watch`, `skill-creator`, `youtube-podcast-digest`, `youtube-video-summary`. The `scheduler` skill creates/updates recurring jobs in `config.yaml` and validates job schema.

**Runtime layer** (`src/oh_my_agent/runtime/`)

- `RuntimeService`: autonomous task orchestration with durable state machine (`DRAFT → RUNNING → VALIDATING → WAITING_MERGE → MERGED/COMPLETED/FAILED/...`).
- Task types: `artifact` (no merge), `repo_change` (merge gate), `skill_change` (validate + merge).
- Per-task worktree isolation under `~/.oh-my-agent/runtime/tasks/`.
- True subprocess interruption: heartbeat loop checks PAUSED/STOPPED and cancels running agent/test.
- Message-driven control: `_parse_control_intent(text)` detects stop/pause/resume from normal thread messages.
- PAUSED state: non-terminal, workspace preserved, resumable with instruction.
- Completion summary with goal, files changed, test counts, and timing metrics.
- **COMPLETED is a post-notify watermark** (reply / artifact path). The completion branch calls `_notify` **before** flipping the DB row to `COMPLETED` — so any poller that sees `status=COMPLETED` is guaranteed the channel message has already landed. A failing `_notify` marks the task `FAILED` with `error=notification_failure: …` and emits a `task.notification_failed` event; `summary` / `output_summary` / `artifact_manifest` are persisted, so a future manual resend can rebuild the completion text without new schema. Merge flow (`WAITING_MERGE`) and the `_fail` path still write status first then notify — same observer race exists there, out of scope for this fix.
- **Single published artifact path**. On `completion_mode=reply`, each delivered file is published to exactly one durable location under `runtime.reports_dir/...` (default `~/.oh-my-agent/reports/`) — no parallel flat-copy. Four publish rules:
  1. Files whose resolved path is already under `reports_dir/` (and not under the task workspace) are **reused in place** — no copy.
  2. Workspace files under `reports/<sub-tree>/...` are published at `reports_dir/<sub-tree>/...`, preserving structure. **Canonical collisions overwrite in place, no suffix** — same canonical path identifies the same logical file, so suffixing would reintroduce duplication.
  3. Workspace files not under `reports/` fall back to `reports_dir/artifacts/<basename>` with a `-<task_id[:8]>` suffix on basename collisions.
  4. Absolute paths (from `artifact_manifest`) that live under neither the workspace nor `reports_dir` use the same flat-fallback rule as (3).

  The completion message renders `Published to:` as the primary path block; transport details (`Delivered via:`, `Attachments:`) and the ephemeral scratch dir (`_artifacts/<task_id>/`) are labeled subordinate. Follow-up threads seed the system turn with the absolute published path. Set `runtime.reports_dir: ""` to disable publishing.
- Discord buttons for approval + slash command fallback.
- Retry on transient agent errors (`rate_limit` / `api_5xx` / `timeout`) with per-kind backoff; on `max_turns` failure, `_fail` surfaces a "Re-run +30 turns" button that spawns a sibling task with a bumped `agent_max_turns` (parent+30, fallback base 25). Terminal kinds (`auth` / `cli_error`) never retry.
- Janitor cleanup with configurable retention. Published tree under `reports_dir/` is **not** auto-pruned.

See [`docs/EN/task-model.md`](docs/EN/task-model.md) ([中文](docs/CN/task-model.md)) for the full task-type, router-intent, status, and delivery catalog — plus known sharp edges.

**Router layer** (`src/oh_my_agent/gateway/router.py`)

- `OpenAICompatibleRouter`: optional LLM-based intent classification for incoming messages.
- Intents: `reply_once`, `invoke_existing_skill`, `propose_artifact_task`, `propose_repo_task`, `create_skill`, `repair_skill`.
- Confidence threshold gating (default `0.55`); falls back to heuristic detection.

**Automation layer** (`src/oh_my_agent/automation/`)

- `Scheduler`: cron / interval-based recurring job runner. Loads YAML definitions from `~/.oh-my-agent/automations/*.yaml` with hot-reload on file changes.
- Per-automation `auto_approve: bool` (default `false`): when `true`, scheduler-fired runtime tasks skip risk evaluation and start immediately; when `false`, tasks go through normal `evaluate_strict_risk()` and may land in DRAFT.
- `fire_job_now(name)`: programmatic one-shot trigger for manual `/automation_run` command.
- Jobs dispatch back into `GatewayManager.handle_message()` as system messages.
- Follow-up thread on reply: each automation-posted channel message is recorded in the `automation_posts` SQLite table (7-day TTL via the runtime janitor). Replying to that message spawns a Discord thread rooted on it, seeded with a system turn listing the original run's published artifact paths; the agent continues there as a normal conversation (CLI session is not resumed — artifact paths are injected as context).
- **Dump channel routing (opt-in)**: `automations.dump_channels` defines named send-only channel aliases (e.g. `oma_dump: {platform: discord, channel_id: "<id>"}`). An automation YAML that sets `target_channel: oma_dump` resolves to `ScheduledJob.notify_channel_id`, threads through to the `RuntimeTask` row, and only the **completion terminal message** + corresponding `automation_posts` row land in the dump channel. DRAFT / approval / progress traffic stays on the source channel. Single bot token = single gateway connection, so dump channels share the source channel's `DiscordChannel` via `register_dump_channel()` + `RuntimeService.register_session_alias()`; `on_message` accepts replies from either channel, and `create_followup_thread(..., parent_channel_id=...)` anchors the follow-up thread on whichever channel the original message lives in.

**Sandbox isolation** (`main.py` + `BaseCLIAgent`)

Three-layer model, activated by the `workspace` config field:

- **Layer 0 — Workspace cwd**: `_setup_workspace()` creates the directory, copies `AGENTS.md` and skills into it. `BaseCLIAgent` sets `cwd=workspace` on every subprocess. CLI sandboxes are cwd-scoped, so agents are confined to workspace rather than the dev repo.
- **Layer 1 — Env sanitization**: `_build_env()` uses a whitelist (`_SAFE_ENV_KEYS`: `PATH`, `HOME`, `LANG`, …). Secrets only reach the subprocess if listed in `env_passthrough` per agent.
- **Layer 2 — CLI-native sandbox**: Codex `--full-auto` (network blocked), Gemini `--yolo`, Claude `--dangerously-skip-permissions` + `--allowedTools`.

Without `workspace` in config, the bot runs in backward-compatible mode (full env, process cwd).

**Config** (`config.py` + `config.yaml`)

`load_config()` reads `config.yaml` with `${ENV_VAR}` substitution. Sections:

```yaml
memory:         # SQLite backend, max_turns, summary_max_chars, judge (idle_seconds, keyword_patterns, inject_limit), diary (enabled, path)
access:         # optional owner-only mode: owner_user_ids
skills:         # enabled, path
automations:    # optional recurring jobs (interval_seconds)
workspace:      # optional — activates sandbox isolation (Layer 0 + L1)
short_workspace: # per-thread transient workspaces with TTL cleanup
router:         # optional LLM intent classification (OpenAI-compatible)
runtime:        # autonomous task orchestration, merge gate, cleanup
gateway:        # channels list (platform, token, channel_id, agents)
agents:         # per-agent: type, cli_path, model, timeout, allowed_tools, env_passthrough, skip_git_repo_check
experiment:     # opt-in short-term features — promoted to top-level sections when stable; currently: tool_trace (enabled, path)
```

**Adding a new platform**: subclass `BaseChannel`, implement `start/create_thread/send`, add a branch in `main._build_channel()`.

**Adding a new CLI agent**: subclass `BaseCLIAgent` (override `_build_command`, accept `workspace` and `passthrough_env` kwargs, pass to `super().__init__()`), add a branch in `main._build_agent()`, reference in `config.yaml`.

**Adding a new skill**: create `skills/{name}/SKILL.md` (+ optional `scripts/`). `SkillSync` will pick it up on startup and symlink it to CLI directories (and copy to workspace if configured).
