# Oh My Agent

一个把消息路由到 CLI Agent（Claude、Gemini、Codex）的多平台 Bot。每个平台频道都会映射到独立的 agent session，并带有持久化会话记忆和 slash 命令。

灵感来自 [OpenClaw](https://openclaw.dev)。

## 当前状态（2026-02-27）

- `/search` 已通过 SQLite FTS5 实现跨线程检索。
- `SkillSync` reverse sync 已实现，并在启动时执行。
- v0.5 当前主线是 runtime-first：重点是可恢复的自主任务循环（`DRAFT -> RUNNING -> WAITING_MERGE -> MERGED/...`）。
- Discord 审批交互采用按钮优先、slash 兜底，reaction 只做状态信号。
- 可选的 LLM 路由已实现：消息可先被分类为 `reply_once` 或 `propose_task`，命中任务后先确认再执行。
- Runtime 可观测性已实现：支持 `/task_logs`、SQLite 中采样式 progress 事件，以及 Discord 中单条可更新的状态消息。

## 架构

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

核心层次：
- Gateway：平台适配层和 slash 命令入口
- Agents：CLI 子进程封装，带 workspace 隔离和 fallback 顺序
- Memory：SQLite + FTS5 持久化对话历史
- Skills：`skills/` 与 CLI 原生技能目录之间的同步

## 安装与配置

### 前置条件

- Python 3.11+
- 至少安装一个 CLI agent：
  - [`claude`](https://docs.anthropic.com/en/docs/claude-code)
  - [`gemini`](https://github.com/google-gemini/gemini-cli)
  - [`codex`](https://github.com/openai/codex)
- 一个开启了 Message Content Intent 的 Discord Bot Token

### 安装

```bash
git clone <repo-url>
cd oh-my-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp config.yaml.example config.yaml
```

### 关键配置

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

敏感信息可以放在 `.env` 文件中，`config.yaml` 里的 `${VAR}` 会自动替换。

Runtime 产物默认放在 `~/.oh-my-agent/runtime/`（包括 memory DB、日志、task worktree）。旧版 `.workspace/` 会在启动时自动迁移。

### 启动

```bash
source .venv/bin/activate
oh-my-agent
```

## 使用方式

### 消息交互

- 在目标频道直接发消息，bot 会创建 thread 并回复。
- 在线程内继续回复，bot 会带着完整上下文继续回答。
- 使用 `@gemini`、`@claude`、`@codex` 前缀可强制本轮指定 agent。
- 如果当前 agent 失败，会自动切换到 fallback 链中的下一个 agent。
- 如果配置了 `access.owner_user_ids`，只有白名单用户可以触发 bot。

### Slash 命令

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

## 自主任务 Runtime

- 长任务消息意图可以自动创建 runtime task。
- 每个 runtime task 都在独立的 git worktree 中执行：`~/.oh-my-agent/runtime/tasks/<task_id>`。
- 循环协议是：改代码 -> 跑测试 -> 失败后继续修，直到 `TASK_STATE: DONE` 且测试通过。
- `strict` 风险策略下，高风险任务进入 `DRAFT`，低风险任务可自动开跑。
- 执行成功后不会直接落地主仓库，而是进入 `WAITING_MERGE`，需要 merge/discard/request-changes。
- `MERGED` 任务在合并成功后会立即清理 worktree；其他终态任务默认保留 72 小时，再由 janitor 清理。
- 短对话 `/ask` 使用按 thread 隔离的临时 workspace，并按 TTL 清理。
- `/task_logs` 用来查看最近 runtime 事件和输出 tail。
- Discord 中 runtime 进度会尽量复用并更新同一条状态消息，避免刷屏。

## 当前限制

- Runtime 的 stop/resume 目前仍主要依赖命令入口，消息驱动控制还未实现。
- 现在的 `stop` 会修改任务状态，但还不能保证立即中断正在运行的 agent/test 子进程。
- skill 生成已有工具链和 workflow 基础，但还没有作为一类一等的 runtime task 与意图路由打通。
