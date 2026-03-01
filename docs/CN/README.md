# Oh My Agent

一个把消息路由到 CLI Agent（Claude、Gemini、Codex）的多平台 Bot。每个平台频道都会映射到独立的 agent session，并带有持久化会话记忆和 slash 命令。

灵感来自 [OpenClaw](https://openclaw.dev)。

## 当前状态（2026-02-28）

- `/search` 已通过 SQLite FTS5 实现跨线程检索。
- `SkillSync` reverse sync 已实现，并在启动时执行。
- v0.5 runtime-first 已完成（包括 runtime hardening pass）。
- v0.6 主线是 skill-first autonomy + adaptive memory；全部已完成。
- v0.7 升级记忆为日期驱动架构，增加 ops 基础和 skill 评估。
- v0.8+ 增加语义记忆检索（向量搜索）和 hybrid autonomy。
- Discord 审批交互采用按钮优先、slash 兜底，reaction 只做状态信号。
- 可选的 LLM 路由已实现：消息可被分类为 `reply_once`、`invoke_existing_skill`、`propose_artifact_task`、`propose_repo_task` 或 `create_skill`。
- Runtime 可观测性已实现：支持 `/task_logs`、SQLite 中采样式 progress 事件，以及 Discord 中单条可更新的状态消息。
- Runtime hardening 已完成：真正的子进程中断、消息驱动控制（stop/pause/resume）、PAUSED 状态、完成摘要、metrics。
- Adaptive Memory 已实现：对话中自动提取记忆、注入 agent prompt、`/memories` 和 `/forget` 命令。

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
- Memory：SQLite + FTS5 持久化对话历史 + YAML 自适应记忆
- Skills：`skills/` 与 CLI 原生技能目录之间的同步
- Runtime：自主任务编排，支持 merge gate 和中断恢复

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

配置说明：
- 把敏感信息（token、API key）写入 `.env`
- `config.yaml` 里只放 `${ENV_VAR}` 引用
- 填写 `DISCORD_BOT_TOKEN` 和 `DISCORD_CHANNEL_ID`
- 如果开启 router，还需要设置 `DEEPSEEK_API_KEY`

### 关键配置

```yaml
memory:
  backend: sqlite
  path: ~/.oh-my-agent/runtime/memory.db
  adaptive:
    enabled: true
    path: ~/.oh-my-agent/memories.yaml

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
  cleanup:
    enabled: true
    interval_minutes: 60
    retention_hours: 72
    prune_git_worktrees: true
    merged_immediate: true
```

敏感信息放在 `.env` 文件中，`config.yaml` 里的 `${VAR}` 会自动替换。

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
- 显式调用已安装 skill（例如 `@claude /weather Shanghai`）会直接走普通聊天流，不会创建 runtime task。
- 如果当前 agent 失败，会自动切换到 fallback 链中的下一个 agent。
- 如果配置了 `access.owner_user_ids`，只有白名单用户可以触发 bot。

### Slash 命令

- `/ask <question> [agent]`
- `/reset`
- `/history`
- `/agent`
- `/search <query>`
- `/memories [category]`
- `/forget <memory_id>`
- `/reload-skills`
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
- Runtime 区分三类任务：
  - `artifact`：长执行但只返回回复或产物，不进入 merge gate
  - `repo_change`：修改 repo 中代码/文档/测试/配置，最终需要 merge
  - `skill_change`：修改 canonical `skills/<name>`，验证后需要 merge
- `repo_change` 和 `skill_change` 在独立 git worktree 中执行：`~/.oh-my-agent/runtime/tasks/<task_id>`。
- `MERGED` 任务在合并成功后立即清理 worktree；其他终态任务默认保留 72 小时后由 janitor 清理。
- 消息驱动控制：在线程内发送 `stop`、`pause`、`resume` 可直接控制任务状态。
- `/task_logs` 用来查看最近 runtime 事件和输出 tail。
- Runtime 写两层日志：service log 和 per-agent 底层日志，均在 `~/.oh-my-agent/runtime/logs/`。

## Artifact Delivery

- 先尝试直接上传附件，过大时回退为链接。
- 交付能力做成抽象层，本地运行直接访问文件，远端部署接入对象存储（推荐 Cloudflare R2）。

## Codex 接入说明

- Codex 接入基础是 CLI 执行 + `AGENTS.md` + workspace `.codex/skills/`。
- `SkillSync` 把所有 skill 同步到 workspace `.codex/skills/`，并生成 `AGENTS.md` 列出每个 skill 的路径和描述供 Codex 读取。
- Claude/Gemini 通过原生 skill 目录发现；Codex 通过 AGENTS.md 桥接，等待官方支持 project-level skill 后平滑迁移。

## Workspace 布局

- `~/.oh-my-agent/agent-workspace/` — CLI agent 的基础外置 workspace
- `~/.oh-my-agent/agent-workspace/sessions/` — 普通聊天 thread 的临时工作区（TTL 清理）
- `~/.oh-my-agent/runtime/tasks/` — runtime 长任务的 worktree 和 artifact 产物
- `~/.oh-my-agent/runtime/logs/` — service log + per-agent 底层日志
- `~/.oh-my-agent/memories.yaml` — adaptive memory 持久化存储

## 自主性方向

- v0.5 建立 runtime-first 基线：长任务执行、恢复、审批和合并闭环（已完成）。
- v0.6 聚焦 skill-first autonomy + adaptive memory：skill 创建路由验证、跨 session 用户记忆（已完成）。
- v0.7 升级记忆为日期驱动架构，增加 ops 基础和 skill 评估。
- v0.8+ 增加语义记忆检索和 hybrid autonomy。
- 源代码自我更迭可以作为高风险、强审批的特殊能力存在，但不是默认自主性主线。

## 当前限制

- Artifact delivery 还没完全做完：附件优先、链接兜底的交付适配层还需要补齐。
- Runtime 可观测性还缺少内存级 live excerpt；`/task_logs` 可读 live agent log tail，但 Discord 状态卡不会直接展示"最近在做什么"的摘要。
- Adaptive memory 目前使用 Jaccard 词重叠做相似度；日期驱动组织计划在 v0.7 实现，语义检索（向量搜索）在 v0.8+。

## 文档

- English README: [README.md](../../README.md)
- 英文路线图: [docs/EN/todo.md](../EN/todo.md)
- 中文路线图: [docs/CN/todo.md](todo.md)
- 英文开发记录: [docs/EN/development.md](../EN/development.md)
- 中文开发记录: [docs/CN/development.md](development.md)
