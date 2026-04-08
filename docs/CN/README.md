# Oh My Agent

一个把消息路由到 CLI Agent（Claude、Gemini、Codex）的多平台 Bot。每个平台频道都会映射到独立的 agent session，并带有持久化会话记忆和 slash 命令。

灵感来自 [OpenClaw](https://openclaw.dev)。

## 当前状态（2026-03-16）

- `/search` 已通过 SQLite FTS5 实现跨线程检索。
- `SkillSync` reverse sync 已实现，并在启动时执行。
- v0.5 runtime-first 已完成（包括 runtime hardening pass）。
- v0.6 主线是 skill-first autonomy + adaptive memory；全部已完成。
- v0.7.2 在 v0.7 基线上补齐了 auth-first 暂停/恢复、文件驱动 automation、通用 Discord `ask_user` HITL、market briefing 报告能力，以及慢速 direct skill 调用的 skill-specific timeout override。
- 后续版本继续推进语义记忆检索（向量搜索）和 hybrid autonomy。
- Discord 审批交互采用按钮优先、slash 兜底，reaction 只做状态信号。
- 可选的 LLM 路由已实现：消息可被分类为 `reply_once`、`invoke_existing_skill`、`propose_artifact_task`、`propose_repo_task` 或 `create_skill`。
- Runtime 可观测性已实现：支持 `/task_logs`、SQLite 中采样式 progress 事件，以及 Discord 中单条可更新的状态消息。
- Gateway/消息日志现在会用 `purpose=...` 区分普通回复、显式 skill 调用和 router 驱动回复；后台 memory/compression agent 调用会继承同一个 `req_id`，便于串联排查。
- Runtime hardening 已完成：真正的子进程中断、消息驱动控制（stop/pause/resume）、PAUSED 状态、完成摘要、metrics。
- Automation 已迁到 `~/.oh-my-agent/automations/` 文件驱动目录，并支持轮询热加载和单条开关。
- `market-briefing` 已加入，支持把 politics / finance / ai 的 bootstrap、日报、周报持久化到 `~/.oh-my-agent/reports/market-briefing/`。
- Adaptive Memory 已实现：对话中自动提取记忆、注入 agent prompt、`/memories` 和 `/forget` 命令。
- Claude / Codex / Gemini 的 CLI session resume 已实现，线程级 session ID 会持久化并在重启后恢复。
- Auth-first 二维码登录基础设施已实现：Discord owner 可手动发起登录，登录态会本地持久化，并可恢复等待中的 runtime task。
- Agent 与 core 之间现已支持 `OMA_CONTROL` 控制帧：除了 `auth_required` 之外，也支持通用单选式 `ask_user`，普通聊天和 runtime task 都能暂停、等待 owner 选择后恢复。

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

更完整的架构说明见：

- [../EN/architecture.md](../EN/architecture.md)
- [architecture.md](architecture.md)

## 安装与配置

### 前置条件

- Python 3.11+
- 如果是本地宿主机运行，至少安装一个 CLI agent：
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
./.venv/bin/pip install -e .
cp .env.example .env
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
    memory_dir: ~/.oh-my-agent/memory

skills:
  enabled: true
  path: skills/
  telemetry_path: ~/.oh-my-agent/runtime/skills.db
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

automations:
  enabled: true
  storage_dir: ~/.oh-my-agent/automations
  reload_interval_seconds: 5

runtime:
  enabled: true
  state_path: ~/.oh-my-agent/runtime/runtime.db
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

敏感信息放在 `.env` 文件中，`config.yaml` 里的 `${VAR}` 会自动替换。

`memory.path` 现在只表示对话记忆库：thread history、summaries 和 SQLite FTS。Runtime 控制面状态单独放在 `runtime.state_path`，skill 遥测单独放在 `skills.telemetry_path`。

Runtime 清理器会在保留窗口后删除旧的 task workspace 和 agent log 文件。默认保留 7 天（`168` 小时）。

Runtime 产物默认放在 `~/.oh-my-agent/runtime/`：
- `memory.db`：对话历史 + FTS
- `runtime.db`：task/auth/HITL/notification/session 状态
- `skills.db`：skill provenance / invocation / feedback / evaluation 遥测
- `logs/`、`tasks/`：runtime 文件产物

旧版单体 `memory.db` 会在启动时自动拆分成这三库，并把原始文件保留为 `.monolith.bak` 备份。
Automation 定义文件现在放在 `~/.oh-my-agent/automations/*.yaml`，修改这些文件不需要重启进程。

### 启动

```bash
./.venv/bin/oh-my-agent
```

### Docker（隔离 Host 运行）

容器启动时会使用两个挂载：

- 状态挂载（`/home`）：保存 `~/.oh-my-agent` 运行时数据和运行文件
- 仓库挂载（默认当前 repo）：让 agent 直接修改项目代码

构建镜像：

```bash
./scripts/docker-build.sh
```

开发 / 前台模式（attached、`--rm`，适合交互式调试）：

```bash
./scripts/docker-run.sh
```

长期托管 / 后台模式（detached、`--restart unless-stopped`，并保留容器对象供 `docker logs` / `docker inspect` 使用）：

```bash
./scripts/docker-start.sh
```

查看和管理长期托管容器：

```bash
./scripts/docker-status.sh
./scripts/docker-logs.sh
./scripts/docker-stop.sh
```

默认配置来源是 `/repo/config.yaml`（`OMA_CONFIG_PATH`）。
环境变量替换会从配置文件同目录加载（通常是 `/repo/.env`）。
因此容器启动前应先在 repo 中准备好配置。
镜像本身只安装运行依赖，正常执行时不再依赖镜像内另一份源码快照。
容器每次启动时会对 `/repo` 执行 editable install（`pip install -e /repo --no-deps`），因此挂载的 repo 就是运行时源码真源，普通源码修改通常不需要重新 build 镜像。
镜像内会预装 `claude`、`gemini`、`codex` 三个 CLI。
启动时会对 `agents.*.cli_path` 做 fail-fast 检查（可用 `OMA_FAIL_FAST_CLI=0` 关闭）。
但 CLI 登录态仍需在容器内完成，并持久化到挂载的 `/home` 路径。
`./scripts/docker-run.sh` 还会注入 Docker 专用的 agent 权限覆盖：Claude 默认开启 `--dangerously-skip-permissions`，Codex 默认使用 `danger-full-access` 并开启 bypass；直接在 host 上启动时仍使用更保守的配置默认值。

需要自定义挂载目录时可以覆盖环境变量：

```bash
OMA_DOCKER_MOUNT=/path/to/your/mount ./scripts/docker-run.sh
OMA_DOCKER_REPO=/path/to/repo ./scripts/docker-run.sh
```

这些环境变量同样适用于 `docker-start.sh`、`docker-logs.sh`、`docker-stop.sh` 和 `docker-status.sh`。
这些辅助脚本会按精确容器名定位，而不是在多个容器里做模糊猜测。默认容器名是 `oh-my-agent`；如果你同时跑多套实例，可以用 `OMA_CONTAINER_NAME` 区分：

```bash
OMA_CONTAINER_NAME=oma-prod ./scripts/docker-start.sh
OMA_CONTAINER_NAME=oma-prod ./scripts/docker-logs.sh
```

如果你想临时收紧 Docker 内的权限，也可以覆盖这些环境变量：

```bash
OMA_AGENT_CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=false \
OMA_AGENT_CODEX_SANDBOX_MODE=workspace-write \
OMA_AGENT_CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX=false \
./scripts/docker-run.sh
```

默认情况下，容器工作目录是 `/home`，宿主机 repo 挂载在 `/repo`。
这样日常运行与配置在 `/home`，同时仍可在 `/repo` 改代码并提交。

如果你希望直接在挂载 repo 目录运行：

```bash
OMA_WORKDIR_IN_CONTAINER=/repo ./scripts/docker-run.sh
```

这时主要编辑状态挂载下的：

- `${HOME}/oh-my-agent-docker-mount/...`（运行时状态/产物）

配置文件请编辑 repo 内：

- `/repo/config.yaml`（宿主机上就是挂载的 repo 路径）
- `/repo/.env`（宿主机上就是挂载的 repo 路径）

容器内 one-off 命令示例：

```bash
./scripts/docker-run.sh oh-my-agent --version
```

如果走长期托管模式，应用日志仍会持久化到挂载的 runtime 路径；`docker-logs.sh` 提供的是容器 stdout/stderr。后台模式刻意不再使用 `--rm`，这样出问题后还可以继续做 `docker logs` 和 `docker inspect` 排查。

只有在修改容器层内容时才需要重新 build 镜像，例如 `Dockerfile`、`docker/entrypoint.sh`、Python/Node/system 依赖。单纯修改 `/repo/src` 下源码，一般只需要重启容器。

## 使用方式

### 消息交互

- 在目标频道直接发消息，bot 会创建 thread 并回复。
- 在线程内继续回复，bot 会带着完整上下文继续回答。
- 使用 `@gemini`、`@claude`、`@codex` 前缀可强制本轮指定 agent。
- 显式调用已安装 skill（例如 `@claude /weather Shanghai`）会直接走普通聊天流，不会创建 runtime task。
- skill 可以在 `SKILL.md` frontmatter 的 `metadata.timeout_seconds` 里可选声明更长 timeout，仅覆盖该 skill 调用时的 CLI timeout。这个能力适合慢速报告/研究类 skill，不建议拿来替代全局 agent timeout。
- 如果当前 agent 失败，会自动切换到 fallback 链中的下一个 agent。
- 如果配置了 `access.owner_user_ids`，只有白名单用户可以触发 bot。

### CLI Session Resume

- Claude、Codex、Gemini 都会按 thread 持久化 CLI session ID。
- 进程重启后，gateway 会从 SQLite 恢复这些 session，并优先继续原始 CLI 会话，而不是每轮都重新拼接完整 history。
- 如果某个 session 已经明显失效或不可恢复，会自动清理，下一轮回退为 fresh session。
- 如果前置 agent 的 stale session 失效但 fallback agent 成功，旧的持久化 session 也会被一起删除。
- 一个容易误解的点：router 对 skill 的发现是直接读取当前 canonical `skills/` 目录，但已经恢复中的 CLI session 仍可能沿用旧会话上下文，因此不会立刻认识新加的 skill。
- 实际上，新 skill 在 fresh thread 或 fresh CLI session 中最可靠。`/reload-skills` 会刷新技能目录，但不保证已经 resume 的 Claude/Codex/Gemini 会话立刻获得这个新 skill 认知。

### Workspace 自动刷新

- `~/.oh-my-agent/agent-workspace/AGENTS.md` 是生成文件，不是手工维护的源文件。
- 它本质上是 repo 根 `AGENTS.md` 的镜像，并在顶部附带可见的生成元信息。
- base workspace 会保存一个很小的 source-state manifest；当 repo `AGENTS.md` 或 canonical `skills/` 变化时，会在短对话 workspace 创建前自动刷新。
- session workspace 继承刷新后的 base workspace，所以普通聊天不需要手动重建也能看到最新规则和 skill。

### Skill 评估

- 普通聊天路径上的 skill 调用现在会记录结构化遥测：路由来源、延迟、usage 和结果状态。
- Discord 上对第一条带 `via **agent**` 的 skill 回复加 `👍` / `👎`，会被持久化成逐次调用反馈。
- `/skill_stats [skill]` 可查看最近成功率、调用次数、平均延迟、反馈，以及最近的评估结论。
- `/skill_enable <skill>` 可清除 auto-disabled 状态，让 router 自动调用重新纳入该 skill。
- 自动降级只影响自动路由；显式 `/skill-name` 仍然可以继续执行。
- `skill_change` 任务现在会在自动合并前增加两层评估：
  - 重复能力 overlap review
  - 外部 repo/tool/reference 内化任务的 source-grounded review
- 如果 skill 要内化外部来源，需要在 `SKILL.md` frontmatter 的 `metadata` 中补齐：
  - `source_urls`
  - `adapted_from`
  - `adaptation_notes`

### Slash 命令

- `/ask <question> [agent]`
- `/reset`
- `/history`
- `/agent`
- `/search <query>`
- `/memories [category]`
- `/forget <memory_id>`
- `/reload-skills`
- `/skill_stats [skill]`
- `/skill_enable <skill>`
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

### Automation

- Automation 的 source of truth 现在是 `~/.oh-my-agent/automations/*.yaml`，不再内嵌在 `config.yaml` 里。
- Scheduler 会轮询该目录，对新增、修改、删除以及 `enabled` 开关变化自动热加载，无需重启进程。
- 调度方式支持：
  - `cron: "0 9 * * *"`：正常的日历时间调度
  - `interval_seconds`：高频本地测试
- `cron` 和 `interval_seconds` 互斥。
- `initial_delay_seconds` 只支持和 `interval_seconds` 一起使用。
- Discord operator 命令：
  - `/automation_status [name]`：展示有效 automation 中的 active + disabled 项
  - `/automation_reload`：立刻触发一次目录重扫，而不是等待下一次轮询
  - `/automation_enable <name>` 和 `/automation_disable <name>`：直接修改 YAML 源文件里的 `enabled`，并立刻 reload scheduler 状态
- Scheduler 触发的 automation 现在走 reply/artifact 风格的 runtime 路径（`test_command=true`、单步预算），不再误用 repo-change 验证循环。
- 如果同一个 automation 还在运行，下一个同名触发会直接跳过，而不是继续堆叠排队。
- automation 完成消息现在会直接把最终结果发到 Discord，并附带 automation 名称、run ID，以及生成文件对应的 `_artifacts/<task_id>` 定位信息。
- 无效或冲突的 automation 文件暂时仍然只通过日志暴露，不进入 `/automation_status`。
- 第一版运行时状态只保存在内存：
  - 重启后重新计算下一次触发时间
  - 不持久化 `last_run` / `next_run` / `last_error`
  - 进程停机期间错过的任务不会补跑

示例 automation 文件：

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

本地高频测试示例：

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

### Market Briefing 报告

- `market-briefing` 是一个核心 skill，统一支持：
  - `bootstrap_backfill`
  - `daily_digest`
  - `weekly_synthesis`
- 报告会以 Markdown + JSON 双份落盘到 `~/.oh-my-agent/reports/market-briefing/`：
  - `bootstrap/<domain>/<date>.md|json`
  - `daily/<date>/<domain>.md|json`
  - `weekly/<iso-week>/cross-domain.md|json`
- 领域模型：
  - 日报：`politics`、`finance`、`ai`
  - 周报：一份 `cross-domain` 跨域整合
- finance 日报默认覆盖：
  - 中国宏观 + 政策
  - 美国宏观 + 政策
  - 近 7 天重点持仓：`NVDA`、`MSFT`、`AAPL`、`AMZN`、`GOOG`、`TSLA`、`META`、`VOO`、`SPY`、`S&P 500`
  - 市场 / 指数基金视角
- 有限 bootstrap 默认窗口：
  - politics：30 天
  - finance：30 天
  - ai：14 天
- 趋势延续应主要基于持久化报告文件 + 当前外部研究，而不是只依赖 Discord 历史。
- 配套 helper 脚本：
  - `skills/market-briefing/scripts/report_store.py`
- `scheduler` skill 现在也已经切到 file-driven automation YAML 校验，不再指向旧的 `config.yaml` 内联 jobs。

### 二维码登录

- auth 能力默认只对 `access.owner_user_ids` 白名单用户开放；如果没配置 owner，`/auth_*` 命令会禁用。
- 第一版 provider 只支持 `bilibili`。
- `/auth_login bilibili` 会在当前配置频道或 thread 里发送二维码图片。
- 登录成功后，cookie 会落盘到 `~/.oh-my-agent/runtime/auth/providers/bilibili/<owner_user_id>/`。
- `~/.oh-my-agent/runtime/auth/qr/` 下的二维码 PNG 只是临时文件，flow 进入终态后会自动删除。
- 当 agent 输出 `OMA_CONTROL` 的 `auth_required` challenge 时，runtime task 会进入 `WAITING_USER_INPUT`。
- 普通聊天 / 显式 skill 路径现在也支持同样的 auth challenge：core 会挂起当前 run，扫码完成后优先恢复原 CLI session。
- 二维码登录完成后，绑定的 task 会自动回到 `PENDING`，无需用户再手动 resume。
- thread 或 task 进入 `auth_required` 时，Discord 现在会在**同一个 thread** 里额外发一条 owner ping 提醒，并 best-effort 给所有 configured owners 发 DM。
- 通用 `ask_user` challenge 现在也已接通：
  - 普通聊天 / 显式 skill 调用会发出可见的单选按钮问题，owner 选择后自动恢复原 run
  - runtime task 会进入 `WAITING_USER_INPUT`，等待 owner 通过按钮回答，然后自动恢复
  - 每个 ask_user prompt 都带内建 `Cancel` 按钮
- `ask_user`、`DRAFT`、`WAITING_MERGE` 现在也会触发同样的高信号提醒：原 thread 内单独一条 ping 消息 + best-effort owner DM。
- active 的 ask_user prompt 会持久化到 SQLite，并在 bot 重启后重新注册 Discord persistent views，所以待回答的问题不会因为进程重启失效。
- 在线程里回复 `retry login`、`重新登录`、`重新扫码` 可以重发二维码。

## 自主任务 Runtime

- 长任务消息意图可以自动创建 runtime task。
- Runtime 区分三类任务：
  - `artifact`：长执行但只返回回复或产物，不进入 merge gate
  - `repo_change`：修改 repo 中代码/文档/测试/配置，最终需要 merge
  - `skill_change`：修改 canonical `skills/<name>`，验证后需要 merge
- `WAITING_USER_INPUT` 是 runtime 里等待 owner 交互的统一暂停态：
  - 二维码登录
  - 通用单选式 `ask_user`
- 当前只有 `DRAFT`、`WAITING_MERGE`、`auth_required`、`ask_user` 会触发 owner 通知；普通运行进度不会刷通知。
- `repo_change` 和 `skill_change` 在独立 git worktree 中执行：`~/.oh-my-agent/runtime/tasks/<task_id>`。
- `MERGED` 任务在合并成功后立即清理 worktree；其他终态任务默认保留 72 小时后由 janitor 清理。
- 消息驱动控制：在线程内发送 `stop`、`pause`、`resume` 可直接控制任务状态。
- `/task_logs` 用来查看最近 runtime 事件和输出 tail。
- Runtime 写两层日志：service log 和 per-agent 底层日志，均在 `~/.oh-my-agent/runtime/logs/`。
- 普通对话日志会在 `AGENT starting`、`AGENT_OK`、`AGENT_ERROR` 上带 `purpose=...`。
- 后台 memory extraction 和 history compression 会把原始消息的 `req_id` 带到 service log 和 registry agent-attempt 日志里。

## Artifact Delivery

- 先尝试直接上传附件，过大时回退为链接。
- 交付能力做成抽象层，本地运行直接访问文件，远端部署接入对象存储（推荐 Cloudflare R2）。

## Codex 接入说明

- Codex 接入基础是 CLI 执行 + repo/workspace `.agents/skills/` + 生成的 `AGENTS.md`。
- `SkillSync` 会把 canonical `skills/` 同步到 repo/workspace `.agents/skills/`。
- Claude/Gemini 继续通过各自原生 skill 目录发现；Codex 使用官方 `.agents/skills/` 约定。生成的 `AGENTS.md` 只保留 repo 规则和元信息，不再列出 workspace skill 扩展。

## Workspace 布局

- `~/.oh-my-agent/agent-workspace/` — CLI agent 的基础外置 workspace
- `~/.oh-my-agent/agent-workspace/sessions/` — 普通聊天 thread 的临时工作区（TTL 清理）
- `~/.oh-my-agent/agent-workspace/.agents/skills/` — Codex 在外置 workspace 中使用的 repo/workspace 原生 skill 目录
- `~/.oh-my-agent/runtime/tasks/` — runtime 长任务的 worktree 和 artifact 产物
- `~/.oh-my-agent/runtime/logs/` — service log + per-agent 底层日志
- `~/.oh-my-agent/automations/` — 文件驱动的 automation 定义目录（支持热加载）
- `~/.oh-my-agent/memory/daily/YYYY-MM-DD.yaml` — 每日追加的短期记忆
- `~/.oh-my-agent/memory/curated.yaml` — 晋升后的长期记忆
- `~/.oh-my-agent/memory/MEMORY.md` — 基于长期记忆自动合成的人类可读摘要

## 自主性方向

- v0.5 建立 runtime-first 基线：长任务执行、恢复、审批和合并闭环（已完成）。
- v0.6 聚焦 skill-first autonomy + adaptive memory：skill 创建路由验证、跨 session 用户记忆（已完成）。
- v0.7 建立日期驱动记忆系统基线（已完成）。
- v0.7.2 补齐 auth-first runtime、Docker 隔离运行、文件驱动 automation、通用 HITL 和 transcript/reporting skill（已完成）。
- 后续版本继续推进语义记忆检索和 hybrid autonomy。
- 源代码自我更迭可以作为高风险、强审批的特殊能力存在，但不是默认自主性主线。

## 当前限制

- Artifact delivery 还没完全做完：附件优先、链接兜底的交付适配层还需要补齐。
- Runtime 可观测性还缺少内存级 live excerpt；`/task_logs` 可读 live agent log tail，但 Discord 状态卡不会直接展示"最近在做什么"的摘要。
- 服务挂掉或启动失败时，Discord 侧还没有面向 operator 的 doctor / 自诊断入口；当前排查仍然需要直接去服务器上看日志。
- 通用 HITL v1 目前只做到了 Discord 单选按钮：
  - owner-only
  - 可见 prompt + 可见答案/取消记录
  - direct chat / runtime task / automation 都能暂停后继续
- 自由文本 HITL、多选、非 Discord 平台交互、prompt 过期策略仍然故意留在后续版本。
- Codex repo/workspace skill 发现现在已经走官方 `.agents/skills/`；生成的 `AGENTS.md` 不再承担 workspace skill 列举逻辑。
- 记忆检索目前仍使用 Jaccard 词重叠做相似度；语义检索（向量搜索）仍是 v0.8+ 项目。

## 文档

- 文档索引: [../README.md](../README.md)
- 变更日志: [../../CHANGELOG.md](../../CHANGELOG.md)
- English README: [README.md](../../README.md)
- 英文路线图: [docs/EN/todo.md](../EN/todo.md)
- 中文路线图: [docs/CN/todo.md](todo.md)
- 英文开发记录: [docs/EN/development.md](../EN/development.md)
- 中文开发记录: [docs/CN/development.md](development.md)
