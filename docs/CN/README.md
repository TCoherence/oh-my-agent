# Oh My Agent

一个把消息路由到 CLI Agent（Claude、Gemini、Codex）的多平台 Bot。每个平台频道都会映射到独立的 agent session，并带有持久化会话记忆和 slash 命令。

灵感来自 [OpenClaw](https://openclaw.dev)。

## 当前状态（2026-02-27）

- `/search` 已通过 SQLite FTS5 实现跨线程检索。
- `SkillSync` reverse sync 已实现，并在启动时执行。
- v0.5 当前主线是 runtime-first：重点是可恢复的自主任务循环（`DRAFT -> RUNNING -> WAITING_MERGE -> MERGED/...`）。
- v0.6 主线调整为 skill-first autonomy；v0.7 再扩展到 ops-first autonomy 和 hybrid autonomy。
- Discord 审批交互采用按钮优先、slash 兜底，reaction 只做状态信号。
- 可选的 LLM 路由已实现：消息可被分类为 `reply_once`、`invoke_existing_skill`、`propose_artifact_task`、`propose_repo_task` 或 `create_skill`。
- Runtime 可观测性已实现：支持 `/task_logs`、SQLite 中采样式 progress 事件，以及 Discord 中单条可更新的状态消息。
- 多类型 runtime 已落地：只有 `repo_change` 和 `skill_change` 任务会进入 merge gate，`artifact` 任务不会要求 merge。

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
- 显式调用已安装 skill（例如 `@claude /weather Shanghai`、`@claude /top-5-daily-news`）会直接走普通聊天流，不会创建 runtime task。
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
- Runtime 现在区分三类任务：
  - `artifact`：长执行但只返回回复或产物，不进入 merge gate
  - `repo_change`：修改 repo 中代码/文档/测试/配置，最终需要 merge
  - `skill_change`：修改 canonical `skills/<name>`，验证后需要 merge
- `repo_change` 和 `skill_change` 会在独立的 git worktree 中执行：`~/.oh-my-agent/runtime/tasks/<task_id>`。
- `artifact` 任务也会走 runtime orchestration，但 `TASK_STATE: DONE` 且校验通过后直接进入 `COMPLETED`，不会出现 `WAITING_MERGE`。
- `strict` 风险策略下，高风险任务进入 `DRAFT`；低风险 `artifact` 任务默认可直接执行。
- `MERGED` 任务在合并成功后会立即清理 worktree；其他终态任务默认保留 72 小时，再由 janitor 清理。
- 短对话 `/ask` 使用 `~/.oh-my-agent/agent-workspace/sessions/` 下按 thread 隔离的临时 workspace，并按 TTL 清理；它不是 runtime worktree。
- `/task_logs` 用来查看最近 runtime 事件和输出 tail。
- Discord 中 runtime 进度会尽量复用并更新同一条状态消息，避免刷屏。

## Artifact Delivery

- 当前交付方向是：
  - 先尝试直接上传附件
  - 如果产物过大，则回退为链接
  - 交付能力做成抽象层，便于本地运行时直接访问文件、远端部署时接入对象存储
- 这层能力应由平台/runtime 控制，而不是只靠 agent prompt 自由发挥。
- 远端托管方向优先推荐 S3 兼容对象存储；默认建议 Cloudflare R2，便于做 presigned link 交付。

## Codex 接入说明

- 当前 Codex 的稳定接入基础是 CLI 执行、`AGENTS.md` 和平台层的 routing/runtime 逻辑。
- 目前不把“project-level native Codex skill discovery”当成可靠能力。
- 近阶段的实际策略是：
  - Claude/Gemini 通过 workspace skill dirs + `SkillSync` 刷新使用 skill
  - Codex 依赖全局 Codex skills，以及自动生成的 workspace `AGENTS.md`（其中引用 `workspace/.codex/skills/`）
- `.codex/skills` 继续延后，直到确认 project-level 原生发现机制可靠。

## Workspace 布局

- `~/.oh-my-agent/agent-workspace/` 是 CLI agent 的基础外置 workspace。
- `~/.oh-my-agent/agent-workspace/sessions/` 存的是普通聊天 thread 的临时工作区。
- `~/.oh-my-agent/agent-workspace/.codex/skills/` 会被刷新，用来通过 `AGENTS.md` 向 Codex 暴露 workspace 内的 skill 引用。
- `~/.oh-my-agent/runtime/tasks/` 存的是 runtime 长任务的 worktree 和 artifact task 产物。
- 外置 workspace 现在只使用生成的 `AGENTS.md` 作为统一上下文注入入口。repo 根的 `AGENT.md`、`CLAUDE.md`、`GEMINI.md` 不再被镜像到外置 workspace 或 session workspace。

## 自主性方向

- v0.5 建立 runtime-first 基线：重点是长任务执行、恢复、审批和合并闭环。
- v0.6 聚焦 skill-first autonomy：重点是 skill 创建、skill 路由、skill 验证，以及可复用能力的持续增长。
- v0.7 再扩展到 ops-first autonomy 和 hybrid autonomy：把 scheduler / trigger 驱动的主动执行和 skill growth 结合起来。
- 源代码自我更迭可以作为高风险、强审批的特殊能力存在，但不是默认自主性主线。

## 当前限制

- Runtime 的 stop/resume 目前仍主要依赖命令入口，消息驱动控制还未实现。
- 现在的 `stop` 会修改任务状态，但还不能保证立即中断正在运行的 agent/test 子进程。
- artifact delivery 还没完全做完：运行时已经能记录产物，但“附件优先、链接兜底”的交付适配层还需要补齐。
- Codex 的 skill 接入目前仍弱于 Claude/Gemini，因为还没有确认 project-level native Codex skill discovery 的可靠路径。
