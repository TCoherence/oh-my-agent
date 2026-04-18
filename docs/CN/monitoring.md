# 监控

生产环境里要盯什么：值得报警的日志模式，以及 `/doctor` 每节怎么读。

bot 设计是单用户/单机。没有 Prometheus exporter；监控靠 tail `service.log` 和按需跑 `/doctor`。

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
