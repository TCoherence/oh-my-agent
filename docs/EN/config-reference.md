# Config Reference

Field-by-field walkthrough of `config.yaml`. Every key in `config.yaml.example` has an entry here.

Resolution order:
1. CLI flag (`--config /path/to/config.yaml`) wins.
2. Else `./config.yaml` in the working directory.
3. Else error.

`${ENV_VAR}` substitution is applied to all string values during load.

---

## `memory`

Conversation history persistence and Judge-driven long-term memory.

| Key | Type | Default | Notes |
|---|---|---|---|
| `backend` | string | `sqlite` | Only `sqlite` is supported in 1.0. |
| `path` | string | `~/.oh-my-agent/runtime/memory.db` | SQLite file. WAL mode is enabled automatically. |
| `max_turns` | int | `20` | Max user/assistant turn pairs kept verbatim per thread before compression. |
| `summary_max_chars` | int | `500` | Cap on the compressed-summary block size. |

### `memory.judge`

Long-term memory (event-driven Judge writing into `memories.yaml`).

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Master switch for the Judge agent. |
| `memory_dir` | string | `~/.oh-my-agent/memory` | Holds `memories.yaml` + `MEMORY.md`. |
| `inject_limit` | int | `12` | Max active memories injected into agent prompts as `[Remembered context]`. |
| `idle_seconds` | int | `900` | Judge fires after this many seconds of thread silence. |
| `idle_poll_seconds` | int | `60` | Idle scanner tick interval. Smaller = faster reaction, more CPU. |
| `synthesize_after_seconds` | int | `21600` (6 h) | Rebuild `MEMORY.md` if older than this and `memories.yaml` is dirty. |
| `max_evidence_per_entry` | int | `8` | Cap on `evidence_log` entries per memory. |
| `keyword_patterns` | list[str] | see example | Natural-language phrases that trigger immediate `/memorize` (e.g. "记一下", "remember this"). |

> **Deprecated alias**: `memory.adaptive` is accepted as a fallback for `memory.judge` and emits a startup warning. Rename in 1.0.

---

## `skills`

Skill loading + telemetry + auto-disable.

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | If false, no skills are loaded or synced. |
| `path` | string | `skills/` | Source directory; files here are symlinked into `.claude/skills/` and `.gemini/skills/` (or copied into `workspace/...`). |
| `telemetry_path` | string | `~/.oh-my-agent/runtime/skills.db` | SQLite DB for invocation history and feedback. |

### `skills.evaluation`

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Master switch for evaluation/feedback collection. |
| `stats_recent_days` | int | `7` | Window for `/skill_stats` "recent" counts. Clamped to ≥ 1. |
| `feedback_emojis` | list[str] | `["👍", "👎"]` | Reactions counted as feedback. Up = +1, down = -1. |

### `skills.evaluation.auto_disable`

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Auto-disable skills that fail repeatedly. |
| `rolling_window` | int | `20` | Last-N invocations considered. |
| `min_invocations` | int | `5` | Below this count, never auto-disable (avoids cold-start lockouts). |
| `failure_rate_threshold` | float | `0.60` | Trip the disable when failure ratio in window ≥ threshold. |

### `skills.evaluation.overlap_guard`

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Block agent-created skills that overlap heavily with existing ones. |
| `review_similarity_threshold` | float | `0.45` | Cosine-similarity threshold over SKILL.md descriptions. |

### `skills.evaluation.source_grounded`

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Validate that source citations in skill outputs resolve. |
| `block_auto_merge` | bool | `true` | Refuse to auto-merge a skill task that fails source grounding. |

---

## `access`

Owner gate.

| Key | Type | Default | Notes |
|---|---|---|---|
| `owner_user_ids` | list[str] | `[]` | Discord user IDs allowed to use the bot. Empty list = open to channel members. System messages bypass this gate. |

---

## `auth`

Built-in OAuth/QR login flows for third-party providers (currently Bilibili).

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Master switch. |
| `storage_root` | string | `~/.oh-my-agent/runtime/auth` | Where credential blobs are written. |
| `qr_poll_interval_seconds` | int | `3` | How often the bot polls upstream for QR scan completion. |
| `qr_default_timeout_seconds` | int | `180` | Auth flow gives up after this many seconds. |

### `auth.providers.bilibili`

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Toggle just this provider. |
| `scope_key` | string | `default` | Logical credential namespace; lets multiple identities coexist. |

---

## `workspace`

Sandbox isolation root (Layer 0). When set, every CLI agent runs with `cwd=workspace`, env is whitelisted, and skills are copied (not symlinked) into the workspace.

| Type | Default | Notes |
|---|---|---|
| string \| null | `~/.oh-my-agent/agent-workspace` | Set to `null` (or omit) for legacy "process cwd + full env" mode (not recommended for production). |

---

## `short_workspace`

Per-thread transient workspaces for `/ask` artifacts. Each thread gets a subdir; janitor deletes by TTL.

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | If false, falls back to the main `workspace`. |
| `root` | string | `~/.oh-my-agent/agent-workspace/sessions` | Base directory; threads create subdirs here. |
| `ttl_hours` | int | `24` | Subdirs older than this are deleted. |
| `cleanup_interval_minutes` | int | `1440` (1 day) | How often the cleaner sweeps. |

---

## `router`

Optional LLM-based intent classification for incoming messages.

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `false` | Off by default — heuristic flow handles most cases. |
| `provider` | string | `openai_compatible` | Currently only this provider is supported. |
| `base_url` | string | required if enabled | OpenAI-compatible endpoint (DeepSeek, Together, etc.). |
| `api_key_env` | string | required if enabled | Env var name to read the key from. |
| `model` | string | required if enabled | e.g. `deepseek-chat`. |
| `timeout_seconds` | int | `8` | Hard cap per classification call. |
| `max_retries` | int | `1` | On HTTP error or parse failure. |
| `confidence_threshold` | float | `0.55` | Below this, fall back to heuristics. |
| `context_turns` | int | `10` | Recent turns sent for context. |
| `require_user_confirm` | bool | `true` | Ask the user before acting on a high-confidence classification. |

> Marked **experimental** in 1.0 — may be removed if usage data shows low signal.

---

## `automations`

Cron / interval recurring jobs.

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Master switch. If false, no scheduler is constructed. |
| `storage_dir` | string | `~/.oh-my-agent/automations` | Each `*.yaml` file = one job definition. Hot-reloaded. |
| `reload_interval_seconds` | int | `5` | File-poll interval for hot reload. |
| `timezone` | string | `local` | `local` or an IANA zone like `America/Los_Angeles`. |

Per-automation YAML schema lives in [development.md](development.md) (`Adding a new automation`).

---

## `runtime`

Autonomous task orchestration.

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Master switch for `/task_*` slash commands. If false, only chat works. |
| `state_path` | string | `~/.oh-my-agent/runtime/runtime.db` | SQLite DB for task state machine. |
| `worker_concurrency` | int | `3` | Max concurrent runtime tasks. |
| `worktree_root` | string | `~/.oh-my-agent/runtime/tasks` | Per-task worktree base. |
| `reports_dir` | string | `~/.oh-my-agent/reports` | Artifact archive root, grouped by `YYYY-MM-DD/`. Set to `""` or `false` to disable archiving. Not auto-pruned. |
| `default_agent` | string | `codex` | Agent used when a task does not specify one. |
| `default_test_command` | string | `pytest -q` | Command run during VALIDATING. |
| `default_max_steps` | int | `8` | Hard agent-step cap per task. |
| `default_max_minutes` | int | `20` | Wall-clock cap per task. **Distinct from** `agents.<x>.timeout` (per agent invocation) and `skills.evaluation.<name>.timeout` (per skill). |
| `skill_auto_approve` | bool | `true` | Skip DRAFT and auto-merge for `skill_change` tasks. |
| `risk_profile` | string | `strict` | `strict` / `lenient`. Drives `evaluate_strict_risk`. |
| `path_policy_mode` | string | `allow_all_with_denylist` | Currently the only supported mode. |
| `denied_paths` | list[str] | see example | Glob patterns the agent must not touch. |
| `decision_ttl_minutes` | int | `1440` | DRAFT tasks auto-expire after this. |
| `agent_heartbeat_seconds` | int | `20` | Tick rate for the agent-cancellation watcher. |
| `test_heartbeat_seconds` | int | `15` | Tick rate during VALIDATING. |
| `test_timeout_seconds` | int | `600` | Hard cap for a single test invocation. |
| `progress_notice_seconds` | int | `30` | Send a progress message after this much silence. |
| `progress_persist_seconds` | int | `60` | Persist a progress checkpoint after this much silence. |
| `log_event_limit` | int | `12` | `/task_logs` returns this many events by default. |
| `log_tail_chars` | int | `1200` | Per-event tail size. |
| `service_retention_days` | int | `7` (in code) | `service.log` rotation retention. Override at top level if needed. |
| `shutdown_timeout_seconds` | int | `30` (in code) | Drain budget on SIGTERM. |

### `runtime.cleanup`

Janitor sweep for stale worktrees + thread logs.

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Master switch. |
| `interval_minutes` | int | `60` | Sweep frequency. |
| `retention_hours` | int | `168` (7 days) | Tasks older than this are eligible for cleanup. |
| `prune_git_worktrees` | bool | `true` | Run `git worktree prune` after deleting a workspace. |
| `merged_immediate` | bool | `true` | Delete merged-task workspaces on transition rather than waiting for the sweep. |

### `runtime.merge_gate`

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | If false, `repo_change` tasks skip the merge step entirely. |
| `auto_commit` | bool | `true` | Stage + commit the worktree changes before merge. |
| `require_clean_repo` | bool | `true` | Refuse to merge into a dirty parent repo. |
| `preflight_check` | bool | `true` | Run a final test pass against the merged result. |
| `target_branch_mode` | string | `current` | `current` = parent repo's HEAD; other modes are reserved. |
| `commit_message_template` | string | see example | `{task_id}` and `{goal_short}` are interpolated. |

---

## `gateway`

Channel adapters and access plumbing.

### `gateway.channels[]`

Each entry binds one platform/channel pair.

| Key | Type | Default | Notes |
|---|---|---|---|
| `platform` | string | required | `discord` only in 1.0. **`slack` is rejected** by config validator — see [upgrade-guide.md](upgrade-guide.md). |
| `token` | string | required | Bot token. Use `${ENV_VAR}` substitution. |
| `channel_id` | string | required | Numeric Discord channel ID, as string. |
| `agents` | list[str] | required | Agent fallback order. First-success wins. Names must match keys in the top-level `agents:` block. |

---

## `agents`

Each top-level key under `agents:` is a logical agent name referenced from `gateway.channels[].agents`.

### Common keys (all CLI agents)

| Key | Type | Default | Notes |
|---|---|---|---|
| `type` | string | required | Currently only `cli` is supported. (`api` was deprecated in v0.4.0.) |
| `cli_path` | string | required | Executable name (resolved via PATH) or absolute path. |
| `model` | string | varies | Passed to the CLI as `--model`. |
| `timeout` | int (sec) | varies | Per-invocation hard cap. Trips fallback to the next agent. |
| `extra_args` | list[str] | `[]` | Appended verbatim to the CLI invocation. Use sparingly — easy to break the contract. |
| `env_passthrough` | list[str] | `[]` | Env var whitelist. Only relevant when `workspace` is set. |

### Agent-specific keys

**`claude`**:

| Key | Notes |
|---|---|
| `max_turns` | Multi-turn ceiling within one invocation. |
| `allowed_tools` | List of tool names the agent may use (e.g. `[Bash, Read, Write, Edit]`). |
| `dangerously_skip_permissions` | If true, skips per-tool prompts. Only set with `workspace` configured. |
| `permission_mode` | Override Claude's permission mode. |

**`gemini`**:

| Key | Notes |
|---|---|
| `max_turns` | Multi-turn ceiling. |
| `yolo` | `true` enables `--yolo` (auto-confirm). Only set with `workspace`. |

**`codex`**:

| Key | Notes |
|---|---|
| `skip_git_repo_check` | Set true to allow non-trusted git directories. |
| `sandbox_mode` | `workspace-write` is the recommended default. |
| `dangerously_bypass_approvals_and_sandbox` | Leave `false` unless you know exactly why. |

---

## Cross-field cheatsheet

Three different timeouts often confuse new operators:

| Setting | Scope | Trips when |
|---|---|---|
| `agents.<name>.timeout` | One CLI invocation | Subprocess wallclock exceeds the value → fallback to next agent |
| `runtime.default_max_minutes` | One runtime task (whole orchestration) | Total task wallclock exceeds → task → FAILED |
| `skills.evaluation.<skill>.timeout` (per-skill, in skill yaml/SKILL.md) | One skill invocation | Skill execution exceeds → skill marked failed, may auto-disable |

Pick the shortest that bounds the thing you actually want to bound. Setting `agents.<name>.timeout` longer than `runtime.default_max_minutes` makes it irrelevant.
