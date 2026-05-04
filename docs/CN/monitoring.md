# 监控

生产环境里要盯什么：值得报警的日志模式，`/doctor` 每节怎么读，以及可选的只读 web 监控面板。

bot 设计是单用户/单机。没有 Prometheus exporter；监控有 3 种方式：

1. tail `service.log`。
2. 按需跑 `/doctor`。
3. （可选）跑 `oma-dashboard` 进程——一个把 SQLite + 日志 + 内存状态聚合成 HTML 的只读页面，60 秒自动刷新。详见 [§6 监控面板](#6-监控面板)。

---

## 1. 服务日志

**位置**：`~/.oh-my-agent/runtime/logs/service.log`（按天轮转，保留 `service_retention_days` 天，默认 7 天）。

**格式**：每行 `<ISO-时间戳> level=<LEVEL> logger=<模块> msg=<消息>`。绝大多数运行行以 `[<request_id>]` 开头——这是 8 字符前缀，方便把一条 inbound 消息串起所有下游行。

```bash
# 实时 tail + grep 过滤
tail -f ~/.oh-my-agent/runtime/logs/service.log | grep -E 'WARN|ERROR'

# 一个 request 的全部行
grep '\[abc12345\]' ~/.oh-my-agent/runtime/logs/service.log
```

---

## 2. 需要报警的日志模式

分两档：「首次出现就报」（P0）和「触阈值才报」（P1）。

### P0 — 首次出现就值得看

| 模式（regex） | 含义 | 第一步 |
|---|---|---|
| `AgentRegistry: all agents exhausted` | 一条消息让所有 agent 都失败了 | 见 [troubleshooting.md](troubleshooting.md) 模式 4 |
| `AGENT_ERROR purpose=` | 某个 agent 子进程抛了异常 | 看末尾的 `error=` 字段，按 request id 串起来 |
| `Gateway shutdown timed out` | SIGTERM 之后排空没赶上 deadline | 查命名的 in-flight task；考虑提高 `runtime.shutdown_timeout_seconds` |
| `Failed to signal scheduler stop` | 关停时调度器没干净退出 | 多半有 leak 的 job 任务；下次重启前抓堆栈 |
| `CONTROL_FRAME_AUTH_REQUIRED` | agent 发了需要鉴权的控制帧 | 用 `/auth_login <agent>` 鉴权；通过后任务会自动 requeue |

### P1 — 按速率/阈值报

| 模式（regex） | 阈值 | 可能原因 |
|---|---|---|
| `agent fallback` | > 5 / 小时 | 主 agent 不健康（二进制缺失、网络、配额） |
| `IGNORE unauthorized user` | 突然激增 | 频道里有外人；review `access.owner_user_ids` |
| `CONTROL_FRAME_PARSE_FAILED` | > 1 / 小时 | 某个 agent 输出了非法控制帧；查 agent 版本 |
| `SKILL_SYNC failed` | 持续出现 | 磁盘上有 skill 不合法；用 `/reload-skills` 让 validator 报错出来 |
| `COMPRESS failed` | > 1 / 天 | 历史压缩坏了——长 thread 会一直膨胀 |
| `Memory injection failed` | > 1 / 天 | `JudgeStore` 读路径抛异常——查 `memories.yaml` 完整性 |
| `Recent failures`（在 `/doctor` Scheduler health 里） | 非零 | 一个或多个 automation run 失败；看下面列出的 `<name>: <err>` 行 |
| `rate.?limit\|throttle` | > 10 / 小时 | 突发负载让 gateway 限流器饱和；见 [troubleshooting.md](troubleshooting.md) 模式 10 |

---

## 3. 读懂 `/doctor`

`/doctor` 输出的 markdown 报告分以下几节。任何异常报告之后第一时间用它定位。

### 3.1 Gateway health

```
- Bot online: `true`
- Channel bound: `<channel_id>`
```

| 字段 | 健康值 | 红了意味着 |
|---|---|---|
| `Bot online` | `true` | Discord 客户端断连——见 [troubleshooting.md](troubleshooting.md) 模式 1 |
| `Channel bound` | 与 `config.yaml` 一致 | channel id 错；该频道之外的消息被忽略 |

### 3.2 Runtime health

```
- Enabled: `true`
- Workers: `2`
- Default agent: `claude`
- Active tasks: `0`
- Recent tasks: `12`
- Task counts:
  - DRAFT: 0
  - RUNNING: 0
  - WAITING_MERGE: 0
  - WAITING_USER_INPUT: 0
  - BLOCKED: 0
```

| 字段 | 看什么 |
|---|---|
| `Enabled` | `false` 表示 runtime 任务整体关闭，只有 chat 工作 |
| `Workers` | 并发任务上限；调高意味更多 API 预算 |
| `Active tasks` | 持续 > workers 表示任务在排队——查卡死的任务 |
| `DRAFT` 计数 | 等待人工批准的任务；配合 `/task_list` 看 |
| `RUNNING` 计数 | 正在执行；和 `ps aux \| grep -E 'claude\|gemini\|codex'` 对照 |
| `WAITING_MERGE` 计数 | repo-change 任务等合并门 |
| `WAITING_USER_INPUT` 计数 | HITL prompt 等用户回复 |
| `BLOCKED` 计数 | 正常应为 0；非零意味依赖循环或鉴权等待 |

### 3.3 HITL health

```
- Active prompts: `3`
  - waiting: `2`
  - resolving: `1`
```

| 字段 | 含义 |
|---|---|
| `Active prompts` | 当前频道里所有未关闭的 prompt |
| `waiting` | 已 post 给用户、还没收到回应 |
| `resolving` | 用户已答，agent 正在消化答复 |

prompt 在 `resolving` 停留 > 1 分钟通常是 agent 在 resume 中途 crash；查 `service.log` 找父 task id。

### 3.4 Scheduler health

```
- Enabled: `true`
- Loaded automations: `4`
- Active jobs: `4`
- Recent failures: `1`
  - market_briefing: HTTP 502 from upstream feed
```

| 字段 | 含义 |
|---|---|
| `Enabled` | False 表示启动时没构造调度器实例 |
| `Loaded automations` | 成功解析的 YAML 文件数 |
| `Active jobs` | 活跃调度条目数；如果 `Active jobs` < `Loaded automations`，说明有的被 disable 了 |
| `Recent failures` | 每个 automation 最近一次错误；下一行点名失败的 automation 和截断的错误信息 |

### 3.5 Auth health

```
- Active auth waits: `0`
```

非零表示至少有一个 task 因等待 `/auth_login` 被挂起。`/auth_status` 看详情。

### 3.6 Log pointers

只是路径——确认存在且可写：

```
- Service log: `/Users/.../runtime/logs/service.log`
- Thread log root: `/Users/.../runtime/logs/threads`
```

### 3.7 Recent failure hints（条件出现）

仅在最近确实有失败时出现。这块是最近失败 task / agent error 的原始文本片段——问题刚发生时用它能跳过翻日志的步骤。

---

## 4. 磁盘占用

bot 会写以下位置：

| 路径 | 增长来源 | 清理方式 |
|---|---|---|
| `~/.oh-my-agent/runtime/logs/service.log*` | 每个 request | 自动轮转；配置 `runtime.service_retention_days` |
| `~/.oh-my-agent/runtime/logs/threads/<id>/` | 每个 task 的 verbose 日志 | 清理器按 `runtime.cleanup.thread_log_retention_hours` 扫 |
| `~/.oh-my-agent/runtime/tasks/<task_id>/` | 每个 task 的 worktree | 清理器按 `runtime.cleanup.task_workspace_retention_hours` 扫 |
| `~/.oh-my-agent/runtime/memory.db` | 对话历史 | 压缩会修剪旧轮；手动清理用 SQL |
| `~/.oh-my-agent/runtime/runtime.db` | task 状态 | 清理器按保留期删 terminal task |
| `~/.oh-my-agent/memory/memories.yaml` | Judge 条目 | 受 `memory.judge.max_memories` 上限约束 |

抽查增长：

```bash
du -sh ~/.oh-my-agent/runtime/* ~/.oh-my-agent/memory/*
```

---

## 5. 成本/预算信号

成本主要由 agent 子进程的 token 用量决定。bot 不直接计费，但可以做关联：

| 来源 | 计什么 |
|---|---|
| `AGENT_OK purpose=... elapsed=Xs response_len=Y` 行 | 每行 = 一次 agent 轮；长 response 倾向高费用 |
| 各 skill 的 `/skill_stats <name>` | `recent_invocations` × 你的单次平均成本 |
| Provider dashboard（Anthropic / OpenAI / Google） | 权威的实际花费 |

怀疑跑飞了：`grep AGENT_RUNNING ~/.oh-my-agent/runtime/logs/service.log | tail -n 50` 看哪些 thread 在背靠背跑。

---

## 6. 监控面板

**状态**：opt-in。需要 `dashboard` 可选依赖（`fastapi` / `uvicorn` / `jinja2`）。

**绑定契约**：仅 loopback。默认绑 `127.0.0.1:8080`，**没有鉴权**。在没有先实现鉴权的情况下把 dashboard 暴露到 `0.0.0.0` 或 LAN 是不安全的。

### 6.1 面板内容

dashboard 是单页 HTML，60 秒自动 meta-refresh，5 个 section：

| Section | 数据源 | 健康标志 |
|---|---|---|
| Automation 健康 | `automation_runtime_state` + 7 天 `runtime_tasks` | `success_rate` ≥ ~80%，启用的 automation 没有 `last_error`，`next_run_at` 有值 |
| Task / runtime 健康 | `runtime_tasks` 当前 + 7 天终态 | `RUNNING` 数 ≤ workers，最近失败列表短且时间老 |
| Cost / usage | `usage_events` 7 天 + 当日 by source / by skill | 日 trend 平稳或下行，sparkline 没近期突刺 |
| Memory & skill | `memories.yaml` + 30 天 per-skill `runtime_tasks` | `active` ≫ `superseded`，常用 skill `success_rate` ≥ ~80% |
| 系统层 | `service.log` + `oh-my-agent.log` 末尾 64 KB；disk usage；bot uptime | `total_error` 低，disk usage 没异常突增，uptime > 几分钟 |

系统层**同时读两份**日志——`service.log`（root logger 出口）和 `oh-my-agent.log`（RuntimeService 副日志）——合并 ERROR / WARNING 计数。用的是 Python `record.levelname` 字符串（`ERROR` / `WARNING` / `INFO`），**不是** `WARN`。

### 6.2 host 直跑（仅 bind mount 部署）

如果你的容器用 bind mount 把 `~/.oh-my-agent/` 暴露给 host（仓库 `compose.yaml` 默认是 named volume，需要自行调整），可以在 host 直接跑 dashboard 不走 Docker：

```bash
pip install -e '.[dashboard]'      # 装 fastapi/uvicorn/jinja2
oma-dashboard --config ./config.yaml
```

然后访问 `http://localhost:8080`。

如果你用仓库默认的 named volume，SQLite 在 host 上不可读——用下面 §6.3 的容器内部署。

### 6.3 容器内部署（下个 PR）

推荐的 Docker 部署方案——`compose.yaml` 增加第二个 service `oh-my-agent-dashboard`，复用同一镜像、挂同一 volume、跑 `oma-dashboard --host 0.0.0.0`，再用 `ports: ["127.0.0.1:8080:8080"]` 把端口仅发布到 host loopback——会在下一个 PR 里落地。

这个 PR 只 ship 了包、entry point 和 host 端 dev workflow。当前 `Dockerfile` **没有**预装 `fastapi` / `uvicorn` / `jinja2`，所以现在直接跑 `docker compose exec oh-my-agent oma-dashboard` 会因为缺依赖失败。

如果等不及，可以容器内手动装依赖：

```bash
docker compose exec oh-my-agent pip install fastapi 'uvicorn[standard]' jinja2
docker compose exec oh-my-agent oma-dashboard --host 0.0.0.0 --port 8080 &
# 再做一下从容器到 host 127.0.0.1 的端口转发
```

### 6.4 怎么读这个页面

- **首次加载全空**：bot 还没产生数据（runtime.db / 日志都空）。每个 section 显示占位文本而不是 500。
- **持续显示 "all log files missing"**：检查 `OMA_MOUNT_ROOT`（或你的 runtime root）是否真的指向 dashboard 进程能读的路径。
- **某 automation 的 `success_rate` 跌了**：跟 `/automation_status` 交叉验证拿完整 `last_error`；面板上 error 是截断的，`/doctor` 完整显示。
- **uptime 倒退或显示 "no Runtime started line found"**：`service.log` 已被轮转出 7 天保留窗口。下次 bot 重启后恢复。

