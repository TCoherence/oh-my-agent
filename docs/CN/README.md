# Oh My Agent

一个把消息路由到 CLI Agent（Claude、Gemini、Codex）的多平台 Bot，支持持久化记忆、自主任务执行和定时自动化。

灵感来自 [OpenClaw](https://openclaw.dev)。

## 功能亮点

- **多 Agent 自动降级** — Claude、Gemini、Codex 作为 CLI 子进程运行；一个失败后自动切到下一个
- **持久化记忆** — SQLite 对话历史 + FTS5 全文检索，以及基于日期的自适应记忆系统（自动提取、晋升、跨会话注入）
- **自主 Runtime** — 持久化任务状态机，支持 merge gate、worktree 隔离、HITL 交互和 Discord 审批按钮
- **Skill 系统** — 跨 agent 目录双向同步、skill 评估与自动降级、agent 驱动的 skill 创建
- **定时自动化** — 基于 YAML 文件的 cron / interval 调度，支持热加载、按 job 配置 `auto_approve`、`/automation_run` 手动触发
- **Workspace 隔离** — 三层沙箱：workspace cwd 限制、环境变量白名单、CLI 原生沙箱
- **意图路由** — 可选的 LLM 分类器，将消息路由到回复、skill 调用、任务提案或 skill 创建
- **图片支持** — Discord 附件下载、per-agent 图片处理、临时文件生命周期管理
- **平台适配** — Discord（完整功能 + slash 命令）、Slack（stub）、可通过 `BaseChannel` ABC 扩展

## 架构

```text
User (Discord / Slack / ...)
         │ message, @agent prefix, or /ask command
         ▼
   GatewayManager
         │ routes to ChannelSession (per channel, isolated)
         ▼
   AgentRegistry ── [claude, gemini, codex]
         │ fallback order, or force specific agent
         ▼
   BaseCLIAgent.run(prompt, history)
     ├── ClaudeAgent      (session resume via --resume)
     ├── GeminiCLIAgent   (--yolo mode)
     └── CodexCLIAgent    (--full-auto, JSONL output)
         │
         ▼   cwd = workspace/ (sandbox-isolated)
   Response → Markdown-aware chunk → thread.send()
```

七大子系统：**Gateway**（平台适配、slash 命令、消息路由）、**Agents**（CLI 子进程封装 + 自动降级）、**Memory**（SQLite + 日期自适应记忆）、**Skills**（双向同步、评估、创建）、**Runtime**（自主任务编排）、**Router**（LLM 意图分类）、**Automation**（cron/interval 调度器）。

→ 完整架构说明：[EN](../EN/architecture.md) · [中文](architecture.md)

## 快速开始

### 前置条件

- Python 3.11+
- 至少安装一个 CLI agent：[`claude`](https://docs.anthropic.com/en/docs/claude-code)、[`gemini`](https://github.com/google-gemini/gemini-cli) 或 [`codex`](https://github.com/openai/codex)
- 一个开启了 Message Content Intent 的 Discord Bot Token

### 安装

```bash
git clone https://github.com/TCoherence/oh-my-agent.git
cd oh-my-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### 配置

```bash
cp .env.example .env                   # 填入 token 等敏感信息
cp config.yaml.example config.yaml     # 调整 channel、agent、功能开关
```

主要配置段：`gateway`（平台 + agent）、`memory`、`skills`、`runtime`、`automations`、`workspace`、`router`。敏感信息通过 `.env` 文件中的 `${ENV_VAR}` 自动替换。

→ 完整配置参考：[`config.yaml.example`](../../config.yaml.example)

### 启动

```bash
oh-my-agent           # 启动 bot
oh-my-agent --version # 查看版本
```

### Docker

```bash
./scripts/docker-build.sh                # 构建镜像
./scripts/docker-run.sh                  # 开发/前台模式
./scripts/docker-start.sh                # 长期托管/后台模式
./scripts/docker-logs.sh                 # 查看日志
```

镜像内预装 `claude`、`gemini`、`codex` CLI。宿主机 repo 挂载在 `/repo`，运行时状态挂载在 `/home`。

→ 完整 Docker 与部署指南：[EN](../EN/operator-guide.md) · [中文](operator-guide.md)

## 使用方式

### 消息交互

- 在配置频道发消息 → 自动创建 thread 并回复
- 在 thread 内继续对话，bot 带着完整上下文回答
- 前缀 `@claude`、`@gemini`、`@codex` 可强制指定本轮 agent
- 支持图片附件（≤10 MB）

### Slash 命令

| 分类 | 命令 |
|------|------|
| **对话** | `/ask`, `/reset`, `/history`, `/agent`, `/search` |
| **Runtime 任务** | `/task_start`, `/task_status`, `/task_list`, `/task_approve`, `/task_reject`, `/task_suggest`, `/task_resume`, `/task_stop`, `/task_merge`, `/task_discard`, `/task_changes`, `/task_logs`, `/task_cleanup` |
| **Skills** | `/reload-skills`, `/skill_stats`, `/skill_enable` |
| **Automations** | `/automation_status`, `/automation_reload`, `/automation_enable`, `/automation_disable`, `/automation_run` |
| **记忆** | `/memories`, `/forget`, `/promote` |
| **认证** | `/auth_login`, `/auth_status`, `/auth_clear` |

### 定时自动化

Automation 以 YAML 文件形式定义在 `~/.oh-my-agent/automations/` 下。Scheduler 会自动热加载文件变更，无需重启。

```yaml
name: daily-ai-briefing
enabled: true
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
prompt: "执行 market-briefing skill，生成今日 AI 简报。"
agent: claude
skill_name: market-briefing
cron: "0 9 * * *"
auto_approve: true
```

→ 完整 automation 参考：[`automation.yaml.example`](../../automation.yaml.example)

### 自主 Runtime

长任务通过持久化状态机编排：

```
DRAFT → RUNNING → VALIDATING → WAITING_MERGE → MERGED / COMPLETED
                              ↕ PAUSED          ↕ FAILED / STOPPED
```

- **任务类型**：`artifact`（无 merge gate）、`repo_change`（需要 merge）、`skill_change`（验证 + merge）
- **隔离**：每个任务在独立 git worktree 中运行，位于 `~/.oh-my-agent/runtime/tasks/`
- **HITL**：任务可暂停等待 owner 审批、QR 登录或自定义单选问题
- **控制**：Discord 按钮审批 + slash 命令兜底 + 自然语言 stop/pause/resume

## 内置 Skills

| Skill | 说明 |
|-------|------|
| `market-briefing` | 政治 / 财经 / AI 日报与周报，含持久化报告存储 |
| `seattle-metro-housing-watch` | 西雅图都会区房市快照与深度分析 |
| `scheduler` | 创建和校验 automation YAML 文件 |

Skills 放在 `skills/<name>/SKILL.md`。`SkillSync` 系统会自动同步到所有 CLI agent 目录。

→ 添加新 skill：创建 `skills/<name>/SKILL.md`（可选 `scripts/`），下次启动或 `/reload-skills` 后自动生效。

## 文档

| 文档 | EN | 中文 |
|------|----|------|
| 架构说明 | [architecture.md](../EN/architecture.md) | [architecture.md](architecture.md) |
| 运营指南 | [operator-guide.md](../EN/operator-guide.md) | [operator-guide.md](operator-guide.md) |
| 路线图 | [todo.md](../EN/todo.md) | [todo.md](todo.md) |
| 开发记录 | [development.md](../EN/development.md) | [development.md](development.md) |
| 变更日志 | [CHANGELOG.md](../../CHANGELOG.md) | — |
| v1.0 规划 | [v1.0-plan.md](../EN/v1.0-plan.md) | [v1.0-plan.md](v1.0-plan.md) |

## 版本管理

版本号来源于 [`src/oh_my_agent/_version.py`](../../src/oh_my_agent/_version.py)。`CHANGELOG.md` 跟踪已发布和未发布的变更。

## License

MIT. 见 [LICENSE](../../LICENSE)。
