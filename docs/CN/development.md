# 开发记录

## 项目概览

Oh My Agent 是一个多平台 bot，执行层直接使用 CLI Agent，而不是直接调用模型 API。自 v0.4 起，整体架构方向明确为 CLI-first；API agent 已进入弃用路径。

## 真相来源

1. `README.md` / `docs/CN/README.md`
2. `CHANGELOG.md`
3. `docs/EN/todo.md` / `docs/CN/todo.md`
4. `docs/archive/` 存放历史规划文档

## 当前 Runtime 基线

已发布：`v0.9.5`（2026-05-04）。分支处于 v1.0 契约冻结阶段：九个主要子系统加一层 config（详见 [`architecture.md`](architecture.md)）。Memory 在 v0.9.0 重写为事件驱动 Judge 模型（迁移说明见 CHANGELOG）；平台抽象在 v0.8 完成 service 层抽离；Slack stub 在 v0.9.1 移除（1.0 唯一支持平台是 Discord）。后续 v0.9.x 加固了 streaming、push notifications、中央 scheduler due-loop、dump channels、dashboard、weekly reflection、cost chart，以及 PR #41 / #44 借助新 harness 暴露并修掉的 cwd 一致性 / cached-credential / 完成体内容三个 runtime 修复。

已实现（v0.9.5 实际表面）：
- **Gateway**：Discord 适配器、slash 命令、消息路由、`@agent` 指定、图片附件、自动化完成消息的 dump-channel 路由、mention-peek push 通知。
- **Agents**：CLI 子进程封装（`claude` / `gemini` / `codex`）+ fallback 注册表；三个 agent 都支持跨重启的 session 持久化恢复；session 存储按 cwd 区分键，匹配真实 CLI 语义。
- **Memory**：SQLite history + 事件驱动 Judge agent 写单层 `memories.yaml` + agent 合成的 `MEMORY.md`；触发器 = 空闲 15 min / `/memorize` / 关键词；daily diary reflection（v0.9.5 起默认开启）+ weekly reflection（默认 Tuesday 03:00 local）。
- **Runtime**：持久化状态机（DRAFT / RUNNING / VALIDATING / WAITING_MERGE / WAITING_USER_INPUT / COMPLETED / FAILED / TIMEOUT / PAUSED / STOPPED / BLOCKED）；per-task worktree；真正的子进程中断；消息驱动控制（自然语言里说 `stop` / `pause` / `resume`）；带答案绑定 + 重启重新注册的 HITL `ask_user` checkpoint；task + chat-reply prompt 中自动注入 cached-credential 提示（PR #41）；`runtime.reports_dir/` 下单一发布产物路径，无平铺重复；COMPLETED post-notify watermark（任何看到 `status=COMPLETED` 的 poller 保证 channel message 已落地）。
- **Skills**：`skills/` 与各 CLI 原生目录的双向同步；agent 驱动的创建 + 校验 + merge gate；outcome 追踪、reaction 反馈、滚动失败率自动 disable、overlap guard、source-grounded review。
- **Router**：可选 OpenAI 兼容 LLM 意图分类（5 个 canonical 意图：`chat_reply` / `invoke_skill` / `oneoff_artifact` / `propose_repo_change` / `update_skill`）+ 置信度阈值 + heuristic 回退。
- **Automation**：cron / interval scheduler，central due-loop（v0.9.4）；per-automation `auto_approve`；reply-to-automation-post 自动升格为 follow-up thread；持久化 runtime state（`/automation_status` 跨重启可见）。
- **Auth**：每 provider QR flow（已落 bilibili）；通过 `get_valid_credential` + 自动注入 `--cookies-path` 提示复用 cached credential（PR #41）。
- **Push notifications**：跨平台外推送层（首发 Bark；ntfy / wecom / feishu 待加）；按事件类型白名单 + per-kind Bark level；绝不阻塞主事件循环。
- **Sandbox 隔离**：workspace cwd + env 白名单 + CLI 原生 sandbox（Codex `--full-auto`、Gemini `--yolo`、Claude `--dangerously-skip-permissions` + `--allowedTools`）。
- **Test harness**（PR #44）：`tests/harness/` 下脚本化离线 E2E 驱动器，覆盖完整 GatewayManager + RuntimeService 栈，仅依赖 `BaseChannel` 契约；3 个 yaml 回归场景 + smoke-regression guard test；cross-platform-ready（不引用 Discord），未来加 Slack / Feishu 时可复用同一批场景。
- **Operator surfaces**：Discord `/doctor`、`/automation_status`、`/usage_today`、`/usage_thread`、`/task_logs`、`/skill_stats`、持久化 automation runtime state、可选只读的 `oma-dashboard` HTTP 服务（按部署约定 loopback-only）。

仍缺少（post-1.0 / 1.x — 详见 [`todo.md`](todo.md) 和 [`v1.0-plan.md`](v1.0-plan.md)）：
- Slack / Feishu / Lark / WeChat 平台适配
- 语义记忆检索（BM25 + 向量混合，MMR re-rank）
- Hybrid autonomy（从历史里发现重复 pattern → 自动起草 skill）
- 超越 cron 的事件驱动触发器（webhook、文件监听、外部通知）
- 比单选 checkpoint 更丰富的 HITL 家族
- Guest session / tenant 隔离
- 远端对象存储 delivery 后端（R2/S3 风格）
- Real-mode harness CI 集成（今天通过 `OMA_HARNESS_ALLOW_REAL=1` gate，仍 raise `NotImplementedError`）

## 下一阶段产品方向

- 当前分支：`v0.9.5` 已发布；目标 `v1.0` 稳定版。
- `v0.8` 完成平台抽象 + 可靠性加固 + 部署加固（service 抽离、graceful shutdown、log 卫生、error contract、docker compose、restart/recovery 测试、operator 文档）。
- `v0.9.0` 把 memory 重写为事件驱动 Judge 模型 — BREAKING；老的 daily/curated 双层 + 手动 promote 命令移除（迁移脚本在 `scripts/`）。
- `v0.9.1`–`v0.9.3` 完成剩余 service 抽离、restart/recovery 加固、experimental surface 清理、Slack stub 移除。
- `v0.9.4` 上 streaming anchor edits、push notifications、watchdog、单一发布产物路径、dump channels、central scheduler due-loop、CI 三段 gate。
- `v0.9.5` 上 weekly reflection、daily reflection 默认开启、dashboard + Docker 部署、带坐标轴的 cost chart、可配置 refresh、AI daily section checkpointing、Docker entrypoint flock 串行化。同期落了 PR #41 的 cwd 统一 / cached credential / 完成体修复 + PR #44 的 scripted E2E harness。
- `v1.0` 是稳定契约冻结 — Discord-only、单用户、自部署；验收标准在 [`v1.0-plan.md`](v1.0-plan.md)。
- post-1.0 扩展（Slack / Feishu / WeChat、语义检索、hybrid autonomy）不在 1.0 关键路径上；cross-platform `BaseChannel` 契约 + harness 是后续扩展不需要 re-tooling 的基础。
- 源代码自我更迭仍是高风险、强审批的特殊能力 — 不是默认自主性路径。

## 历史阶段

### v0.9.5（2026-05-04）

- Cwd 统一 + cached bilibili 凭证复用 + 非空完成体（PR #41）；`tests/harness/` 下脚本化离线 E2E harness 含 3 个回归 scenario + cwd-keyed `StubAgent`（PR #44）；`BaseChannel` ABC 把 `signal_task_status` 和 `send_hitl_prompt` 提到 ABC 默认走文本回退
- Weekly memory reflection（在 daily 之上的多尺度"梦境"通道）；daily reflection 默认开启；dashboard Docker 部署 + scripts/ 启动脚本路径；带 x/y 坐标轴的 cost chart 替换 inline sparkline；可配置 dashboard refresh 间隔；AI daily Stage 2.2（section 级别 checkpoint 存储 + paper-digest JSON 直读）；Docker entrypoint `flock` 串行化并发 editable install

### v0.9.4

- Streaming anchor edits；push notifications layer（首发 Bark）；scheduler liveness watchdog；`runtime.reports_dir/` 下单一发布产物路径；automation 完成消息的 dump-channel 路由（一个 bot token 多个只发 channel）；CI 三段 gate（lint → typecheck → tests）；中央 scheduler due-loop；canonical 5-intent router 契约；可选只读 `oma-dashboard` HTTP 服务

### v0.9.0–v0.9.3

- v0.9.0：Memory 子系统重写为事件驱动 Judge 模型 — BREAKING。老的双层 date-based memory + post-turn extractor 移除；单层 `memories.yaml` + agent 合成 `MEMORY.md`；空闲 / `/memorize` / 关键词触发；附迁移脚本
- v0.9.1：Slack stub 移除；剩余 service-layer 抽离；chat / skill / runtime / HITL / auth / automations 的 restart/recovery 测试；老 state layout 升级路径校验
- v0.9.2：Watchdog（首发）；operator 文档收紧
- v0.9.3：artifact archive；automation follow-up thread

### v0.8

- 1.0 hardening — 全四阶段完成：平台抽象（service 抽离、`BaseChannel` 契约 review）、可靠性加固（graceful shutdown、startup config 校验、升级/迁移契约、markdown-aware chunking、rate-limit、log 卫生、user-visible error contract）、部署加固（first-class `compose.yaml`、operator 重启/升级 SOP、health-check）、文档（EN + CN 平价）

### v0.7.2 基线 + 后续演进

- auth-first runtime pause/resume 和通用 Discord `ask_user`
- 文件驱动 automation 和当前的 market/reporting workflows
- 多类型 runtime（`artifact`、`repo_change`、`skill_change`）
- 面向现有 skill 反馈的 `repair_skill` 路由
- runtime live agent logging 和更稳的 Discord 状态消息更新
- Codex repo/workspace `.agents/skills/` 分发，生成的 workspace `AGENTS.md` 降级为 rules/metadata

### v0.7.3

- artifact delivery 抽象：附件优先，失败时回退到本地绝对路径
- chat / invoke / runtime / HITL resume 统一沉淀到 thread-scoped agent 日志
- 结构化 HITL answer binding 进入 task/thread resume context
- Discord `/doctor` 提供 gateway/runtime/HITL/auth/log 健康快照
- automation runtime state 持久化，并供 `/automation_status` 与 `/doctor` 读取
- skill `metadata.timeout_seconds` 传播到 automation-backed execution
- 基于 `ArtifactDeliveryResult` 完成 delivery 收口，不再引入平行结果结构
- HITL checkpoint/resume 收口：只继承最近一轮 answer，不跨 task 泄漏
- merge/discard/request changes 与 answered/cancelled 的 Discord view 收敛到稳定终态

### v0.7.0

- 日期驱动的两层记忆系统正式交付（v0.9.0 已重写为 Judge）
- 手动晋升命令和层级晋升生命周期（v0.9.0 已移除）
- Discord 图片附件支持，以及 Claude/Gemini/Codex 的对应处理链路
- workspace refresh 现在会一起重建同步后的 skills 和生成的 `AGENTS.md`
- Codex 的 repo/workspace skill 分发切到官方 `.agents/skills/`
- gateway 回复链路和后台 memory/compression 链路的 request-scoped 可观测性增强

### v0.6.1

- Codex CLI session resume
- Gemini CLI session resume
- 三个 CLI agent 的 session ID 都支持重启后恢复
- Resume 加固：stale persisted session 会更安全地清理，并在 fallback 时正确同步删除

### v0.6.0

- Adaptive Memory：YAML 存储 + Jaccard 去重 + confidence 评分 + 淘汰策略
- 对话压缩后由 agent 驱动提取记忆（v0.9.0 已被事件驱动 Judge 替代）
- 记忆注入：`[Remembered context]` 前置到 agent prompt
- Discord `/memories`（列表 + 类别筛选）和 `/forget`（按 ID 删除）
- Skill task 的早期自动审批 + 自动合并原型
- 189 项测试全部通过

### v0.5.3

- PAUSED 状态：非终态，workspace 保留
- 真正的子进程中断：`_invoke_agent` 中 heartbeat 循环检查 stop/pause 并取消 agent
- 消息驱动控制：`_parse_control_intent(text)` 从 thread 消息识别 stop/pause/resume
- Suggestion 体验优化：用新 nonce 重新发送包含建议文本的决策界面
- 完成摘要存入 `task.summary`（目标、文件、测试统计、耗时）
- Runtime 指标：事件 payload 中包含 `total_agent_s`、`total_test_s`、`total_elapsed_s`

### v0.5.2

- 可持久化 runtime 状态机
- Merge gate
- 外置 runtime workspace 布局
- Janitor 清理
- Discord task 命令与按钮审批

### v0.4.2

- Owner gate
- Scheduler MVP
- 每个 job 的 delivery mode
- Scheduler skill

### v0.4.1

- thread 内 `@agent` 定向
- `/ask` 的 agent override
- session ID 持久化
- 重启后 resume
- Codex 兼容性加固
- CLI 错误可观测性增强

### v0.4.0

- CLI-first 清理
- Codex CLI agent
- SkillSync reverse sync
- Discord slash commands
- Claude session resume
- memory export/import
