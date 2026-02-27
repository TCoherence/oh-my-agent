# Oh My Agent / 多平台 Agent Bot

Multi-platform bot that routes messages to CLI-based AI agents (Claude, Gemini, Codex). Each platform channel maps to an independent agent session with persistent conversation memory and slash commands.

一个把消息路由到 CLI Agent（Claude、Gemini、Codex）的多平台 Bot。每个平台频道都会映射到独立的 agent session，并带有持久化会话记忆和 slash 命令。

Inspired by [OpenClaw](https://openclaw.dev).

## Status Snapshot / 当前状态 (2026-02-27)

- `/search` is already implemented with SQLite FTS5 across all threads.
- `/search` 已通过 SQLite FTS5 实现跨线程检索。
- `SkillSync` reverse sync is already implemented and runs on startup.
- `SkillSync` reverse sync 已实现，并在启动时执行。
- v0.5 focus is now **runtime-first**: durable autonomous task loops (`DRAFT -> RUNNING -> WAITING_MERGE -> MERGED/...`), not smart routing.
- v0.5 当前主线是 **runtime-first**：重点是可恢复的自主任务循环（`DRAFT -> RUNNING -> WAITING_MERGE -> MERGED/...`），而不是智能路由本身。
- Task approvals use **buttons first + slash fallback** on Discord; reactions are status-only signals.
- Discord 审批交互采用 **按钮优先 + slash 兜底**；reaction 只做状态信号，不参与审批。
- Optional LLM routing is implemented: incoming messages can be classified as `reply_once` or `propose_task`, with human confirmation before task execution.
- 可选的 LLM 路由已实现：消息可先被分类为 `reply_once` 或 `propose_task`，命中任务后先确认再执行。
- Runtime observability is implemented: `/task_logs`, sampled progress events in SQLite, and a single updatable Discord status message.
- Runtime 可观测性已实现：支持 `/task_logs`、SQLite 中采样式 progress 事件，以及 Discord 中单条可更新的状态消息。

## Architecture / 架构

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

**Key layers / 核心层次:**
- **Gateway** — platform adapters (Discord implemented, Slack stub) with slash commands
- **Gateway** — 平台适配层（Discord 已实现，Slack 仍是占位）和 slash 命令入口
- **Agents** — CLI subprocess wrappers with workspace isolation and ordered fallback
- **Agents** — CLI 子进程封装，带 workspace 隔离和 fallback 顺序
- **Memory** — SQLite + FTS5 persistent conversation history with auto-compression
- **Memory** — SQLite + FTS5 持久化对话历史，支持自动压缩
- **Skills** — bidirectional sync between `skills/` and CLI-native directories
- **Skills** — `skills/` 与 CLI 原生技能目录之间的双向同步

## Prerequisites / 前置条件

- Python 3.11+
- At least one CLI agent installed:
  - [`claude`](https://docs.anthropic.com/en/docs/claude-code) — Claude Code CLI
  - [`gemini`](https://github.com/google-gemini/gemini-cli) — Gemini CLI
  - [`codex`](https://github.com/openai/codex) — OpenAI Codex CLI
- A Discord bot token with **Message Content Intent** enabled

## Setup / 安装与配置

### 1. Discord Bot / Discord 机器人

1. Go to [Discord Developer Portal](https://discord.com/developers/applications) → create application
2. **Bot** tab → copy token → enable **Message Content Intent**
3. **OAuth2 → URL Generator** → scope `bot` + `applications.commands` → permissions: Send Messages, Create Public Threads, Send Messages in Threads, Read Message History
4. Open the generated URL to invite the bot to your server

### 2. Install / 安装

```bash
git clone <repo-url>
cd oh-my-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. Configure / 配置

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml`:

```yaml
memory:
  backend: sqlite
  path: ~/.oh-my-agent/runtime/memory.db
  max_turns: 20

skills:
  enabled: true
  path: skills/

access:
  owner_user_ids: ["123456789012345678"]   # optional owner-only mode

# Sandbox isolation: agents run in this dir instead of the repo root.
# AGENT.md and skills are copied here on startup. Env vars are sanitized.
workspace: ~/.oh-my-agent/agent-workspace

short_workspace:
  enabled: true
  root: ~/.oh-my-agent/agent-workspace/sessions
  ttl_hours: 24
  cleanup_interval_minutes: 1440

router:
  enabled: true
  provider: openai_compatible
  base_url: "https://api.deepseek.com/v1"
  api_key_env: DEEPSEEK_API_KEY
  model: deepseek-chat
  timeout_seconds: 8
  max_retries: 1
  confidence_threshold: 0.55
  require_user_confirm: true

automations:
  enabled: true
  jobs:
    - name: daily-refactor
      enabled: true
      platform: discord
      channel_id: "${DISCORD_CHANNEL_ID}"
      delivery: channel                      # "channel" (default) | "dm"
      thread_id: "1476736679120207983"      # optional for channel delivery
      # target_user_id: "123456789012345678" # optional for dm; defaults to first owner_user_ids
      prompt: "Review TODOs and implement one coding task."
      agent: codex                            # optional
      interval_seconds: 86400
      initial_delay_seconds: 10

runtime:
  enabled: true
  worker_concurrency: 3
  worktree_root: ~/.oh-my-agent/runtime/tasks
  default_agent: codex
  default_test_command: "pytest -q"
  default_max_steps: 8
  default_max_minutes: 20
  risk_profile: strict
  path_policy_mode: allow_all_with_denylist
  denied_paths: [".env", "config.yaml", ".workspace/**", ".git/**"]
  decision_ttl_minutes: 1440
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
    retention_hours: 24
    prune_git_worktrees: true
  merge_gate:
    enabled: true
    auto_commit: true
    require_clean_repo: true
    preflight_check: true
    target_branch_mode: current
    commit_message_template: "runtime(task:{task_id}): {goal_short}"

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

敏感信息可以放在 `.env` 文件中，`config.yaml` 内的 `${VAR}` 会自动替换。

Runtime artifacts default to `~/.oh-my-agent/runtime/` (memory DB, logs, task worktrees). Legacy `.workspace/` is migrated automatically on startup.

Runtime 产物默认放在 `~/.oh-my-agent/runtime/`（包括 memory DB、日志、task worktree）。旧版 `.workspace/` 会在启动时自动迁移。

### 4. Run / 启动

```bash
source .venv/bin/activate
oh-my-agent
```

## Usage / 使用方式

### Messages / 消息交互
- **Post a message** in the configured channel → bot creates a thread and replies
- **在目标频道直接发消息** → bot 会创建 thread 并回复
- **Reply in the thread** → bot responds with full conversation context
- **在线程内继续回复** → bot 会带着完整上下文继续回答
- **Prefix with `@agent`** (for example `@gemini`, `@claude`, `@codex`) to force a specific agent for that message
- **使用 `@agent` 前缀**（如 `@gemini`、`@claude`、`@codex`）可强制本轮指定 agent
- Each reply is prefixed with `-# via **agent-name**`
- 每条回复都会带 `-# via **agent-name**` 标注实际使用的 agent
- If an agent fails, the next one in the fallback chain takes over
- 如果当前 agent 失败，会自动切换到 fallback 链中的下一个 agent
- If `access.owner_user_ids` is configured, only listed users can trigger the bot
- 如果配置了 `access.owner_user_ids`，只有白名单用户可以触发 bot

### Slash Commands / Slash 命令
| Command | Description |
|---------|-------------|
| `/ask <question> [agent]` | Ask the AI (creates a new thread, optional agent override) |
| `/reset` | Clear conversation history for current thread |
| `/history` | Show thread history (debug helper) |
| `/agent` | Show available agents and their status |
| `/search <query>` | Search across all conversation history |
| `/task_start` | Create a runtime task (manual entry) |
| `/task_status <task_id>` | Inspect a runtime task |
| `/task_list [status]` | List runtime tasks for the channel |
| `/task_approve <task_id>` | Approve a DRAFT/BLOCKED task |
| `/task_reject <task_id>` | Reject a task |
| `/task_suggest <task_id> <suggestion>` | Keep draft, attach suggestion |
| `/task_resume <task_id> <instruction>` | Resume a blocked task |
| `/task_stop <task_id>` | Stop a running task |
| `/task_merge <task_id>` | Merge a `WAITING_MERGE` task into current branch |
| `/task_discard <task_id>` | Discard a `WAITING_MERGE` task |
| `/task_changes <task_id>` | Show task workspace changes |
| `/task_logs <task_id>` | Show recent runtime events and output tails |
| `/task_cleanup [task_id]` | Cleanup expired/specified task workspace |

这些命令当前主要覆盖 runtime 的手动入口、审批、合并、日志和清理能力。

### Agent Targeting / Agent 定向
- **In-thread targeting**: send `@codex fix this` to run only Codex for that turn
- **线程内定向**：发送 `@codex fix this`，这一轮只用 Codex
- **New-thread targeting**: use `/ask` with the optional `agent` argument
- **新线程定向**：使用 `/ask`，并传入可选的 `agent` 参数
- Prefix is stripped before dispatch, so the model receives only your actual question
- 发送前会移除前缀，真正传给模型的只有你的实际问题
- Unknown names are rejected early in `/ask` with a list of valid agents
- `/ask` 中填写未知 agent 名称会被立即拒绝，并返回可用列表

### Session Resume / Session 恢复
Claude session IDs are persisted per `(platform, channel_id, thread_id, agent)` in SQLite `agent_sessions`.
- On successful reply, latest `session_id` is upserted
- On bot restart, session IDs are loaded before handling the next message
- If `--resume` fails, in-memory + DB session entries are cleared and next turn falls back to flattened history

Claude 的 session ID 会按 `(platform, channel_id, thread_id, agent)` 写入 SQLite `agent_sessions` 表。
- 成功回复后会更新最新 `session_id`
- bot 重启后会在下一次处理消息前恢复这些 session
- 如果 `--resume` 失败，会清除内存和数据库里的 session，退回到普通历史拼接模式

### Automations (MVP) / 自动化调度（MVP）
- Configure recurring jobs in `automations.jobs` (interval-based scheduler)
- Jobs can route to runtime tasks when runtime is enabled (same risk policy applies)
- Set `thread_id` to post into an existing thread; omit it to post directly in the parent channel
- Use `delivery: dm` to send directly to a user; set `target_user_id` (or rely on first `owner_user_ids`)
- Each job has its own `enabled` flag, so you can pause jobs without turning off `automations.enabled`
- Use `agent` to force a specific model for the job

- 在 `automations.jobs` 中配置定时任务（当前是 interval-based scheduler）
- runtime 启用时，job 也可以进入 runtime task 流程（同样受风险策略约束）
- 配置 `thread_id` 可投递到指定 thread；不配置则直接发到父频道
- 使用 `delivery: dm` 可直接发给用户；`target_user_id` 可显式指定
- 每个 job 都有自己的 `enabled` 开关，不必关闭整个 scheduler
- 可通过 `agent` 强制该 job 使用某个模型

### Autonomous Runtime / 自主任务运行时
- Message intent can auto-create runtime tasks for long coding requests.
- 长任务消息意图可自动创建 runtime task。
- Runtime tasks execute in per-task git worktrees under `~/.oh-my-agent/runtime/tasks/<task_id>`.
- 每个 runtime task 都在独立的 git worktree 中执行：`~/.oh-my-agent/runtime/tasks/<task_id>`。
- Loop contract: code changes -> tests -> retry, until `TASK_STATE: DONE` + passing tests.
- 循环协议是：改代码 -> 跑测试 -> 失败后继续修，直到 `TASK_STATE: DONE` 且测试通过。
- Risk policy (`strict`): low-risk tasks auto-run; high-risk tasks enter `DRAFT` and require approval.
- `strict` 风险策略下：低风险任务自动开跑，高风险任务进入 `DRAFT` 等待审批。
- Decision surface: Discord buttons first + slash fallback.
- 审批交互：Discord 按钮优先，slash 兜底。
- Optional LLM intent router can classify incoming messages (`reply_once` vs `propose_task`) before heuristic intent checks.
- 可选 LLM 路由器会在启发式意图判断前，先把消息分类为 `reply_once` 或 `propose_task`。
- Execution completion now enters `WAITING_MERGE`; final apply requires `Merge/Discard/Request Changes`.
- 执行成功后不会直接落地主仓库，而是进入 `WAITING_MERGE`，需要 `Merge/Discard/Request Changes`。
- Reactions are non-blocking status signals only (`⏳`, `👀`, `🧪`, `✅`, `⚠️`, `🗑️`).
- reaction 仅作状态信号（`⏳`, `👀`, `🧪`, `✅`, `⚠️`, `🗑️`），不会影响主流程。
- Short `/ask` conversations use per-thread transient workspaces under `short_workspace.root` and are TTL-cleaned (default 24h, metadata persisted in SQLite).
- 短对话 `/ask` 会使用按 thread 隔离的临时 workspace，默认 24h TTL 清理，元数据写入 SQLite。
- Long-running agent/test phases emit full heartbeat logs, but only sampled progress snapshots are persisted to SQLite; `/task_logs` shows recent phase/progress events plus last agent/test output tail.
- 长任务中的 agent/test 阶段会持续输出 heartbeat 日志，但只会把采样后的 progress snapshot 写入 SQLite；`/task_logs` 用来查看最近 phase/progress 事件和最后的输出 tail。
- Discord 中 runtime 状态默认会尽量复用并更新同一条 status message，避免 thread 被状态消息刷屏。

### Current Limits / 当前限制
- Task stop/resume is still command-driven today; message-driven runtime control is not implemented yet.
- 当前 task 的 stop/resume 仍主要依赖命令入口，消息驱动的运行时控制还未实现。
- `stop` currently changes runtime state, but does not yet guarantee immediate interruption of a running agent/test subprocess.
- 现在的 `stop` 会修改 runtime 状态，但还不能保证立即中断正在运行的 agent/test 子进程。
- Skill creation exists as a workflow/tooling foundation, but not yet as a first-class runtime task type with intent routing.
- skill 生成已有 workflow 和工具基础，但还没有作为一类一等的 runtime task 与意图路由打通。

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

See [`docs/todo.md`](docs/todo.md) for the roadmap, [`docs/development.md`](docs/development.md) for architecture decisions, and [`docs/v0.5_runtime_plan.md`](docs/v0.5_runtime_plan.md) for the runtime-first implementation spec.
