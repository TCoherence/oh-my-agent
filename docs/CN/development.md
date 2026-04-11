# 开发记录

## 项目概览

Oh My Agent 是一个多平台 bot，执行层直接使用 CLI Agent，而不是直接调用模型 API。自 v0.4 起，整体架构方向明确为 CLI-first；API agent 已进入弃用路径。

## 真相来源

1. `README.md` / `docs/CN/README.md`
2. `CHANGELOG.md`
3. `docs/EN/todo.md` / `docs/CN/todo.md`
4. `docs/archive/` 存放历史规划文档

## 当前 Runtime 基线

已实现：
- 可选 LLM 意图路由（`reply_once`、`invoke_existing_skill`、`propose_artifact_task`、`propose_repo_task`、`create_skill`、`repair_skill`）
- 短对话临时 workspace + TTL 清理，状态持久化到 SQLite
- 多类型 runtime orchestration：
  - `artifact` 任务可直接完成，不进入 merge
  - `repo_change` 与 `skill_change` 继续走 merge gate
- 一等 `WAITING_USER_INPUT` 状态，以及 direct chat / runtime task 的单选式 `ask_user`
- active `ask_user` prompt 的 owner 通知、SQLite 持久化和重启后重新注册
- Runtime 可观测性基线：
  - `/task_logs`
  - SQLite 中采样式 progress snapshot
  - 进程日志中的完整 heartbeat
  - Discord 中单条可更新的状态消息
  - Discord `/doctor`
  - `runtime/logs/threads/` 下按 thread 聚合的统一日志
  - `runtime/logs/agents/` 下内部 live agent spool 日志
- Codex skill 接入已切到官方 repo/workspace `.agents/skills/`；生成的 workspace `AGENTS.md` 只保留 repo 规则和元信息
- 真正的子进程中断（heartbeat 循环检查 PAUSED/STOPPED，取消运行中 agent/test）
- 消息驱动的 runtime 控制（通过 `_parse_control_intent` 从普通 thread 消息触发 stop/pause/resume）
- PAUSED 状态：非终态，workspace 保留，可带指令 resume
- 结构化任务完成摘要（目标、变更文件、测试统计、耗时）
- Runtime 指标（`total_agent_s`、`total_test_s`、`total_elapsed_s`）
- Adaptive Memory：对话中自动提取记忆、注入 agent prompt、`/memories` 和 `/forget` 命令
- Skill 评估已实现：结果追踪、用户反馈、健康统计、自动降级、重叠防重、source-grounded review
- 基于 router 的 `repair_skill` 技能修复意图已实现

仍缺少：
- 直接展示到 Discord 状态卡里的内存级 live excerpt
- 超出本地 attachment/path 兜底之外的远端 delivery backend
- 超越 cron 的事件驱动触发器
- 超出结构化单选 checkpoint 的更丰富 HITL 家族
- guest session 隔离
- 语义检索（v0.8+）

## 下一阶段产品方向

- 当前分支已发布为 `v0.7.3`。
- v0.7.3 在 v0.7.2 基线之上补齐了 HITL、delivery、operator observability 的闭环。
- 下一个目标是 deferred items 与 `v0.8+`，不是继续拆 v0.7.3 phase。
- v0.5 已完成 runtime-first 基线（全部完成）。
- v0.6 已交付 skill-first autonomy + adaptive memory。
- v0.7 已交付日期驱动记忆、多类型 runtime、skill 评估，以及当前这轮 auth/HITL/runtime 基础设施。
- v0.8+ 增加语义记忆检索（向量搜索）和 hybrid autonomy。
- 源代码自我更迭不是默认自主性路径，而是高风险、强审批的特殊能力。

## 历史阶段

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

- 日期驱动的两层记忆系统（`daily/` + `curated.yaml` + `MEMORY.md`）正式交付
- 手动 `/promote` 命令和 daily 到 curated 的晋升生命周期
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
- `MemoryExtractor`：对话压缩后由 agent 驱动提取记忆
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
