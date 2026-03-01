# 开发记录

## 项目概览

Oh My Agent 是一个多平台 bot，执行层直接使用 CLI Agent，而不是直接调用模型 API。自 v0.4 起，整体架构方向明确为 CLI-first；API agent 已进入弃用路径。

## 真相来源

1. `README.md` / `docs/CN/README.md`
2. `docs/EN/todo.md` / `docs/CN/todo.md`
3. `docs/archive/` 存放历史规划文档

## 当前 Runtime 基线

已实现：
- 可选 LLM 意图路由（`reply_once`、`invoke_existing_skill`、`propose_artifact_task`、`propose_repo_task`、`create_skill`）
- 短对话临时 workspace + TTL 清理，状态持久化到 SQLite
- 多类型 runtime orchestration：
  - `artifact` 任务可直接完成，不进入 merge
  - `repo_change` 与 `skill_change` 继续走 merge gate
- Runtime 可观测性基线：
  - `/task_logs`
  - SQLite 中采样式 progress snapshot
  - 进程日志中的完整 heartbeat
  - Discord 中单条可更新的状态消息
  - `runtime/logs/agents/` 下独立的底层 agent 日志
- 真正的子进程中断（heartbeat 循环检查 PAUSED/STOPPED，取消运行中 agent/test）
- 消息驱动的 runtime 控制（通过 `_parse_control_intent` 从普通 thread 消息触发 stop/pause/resume）
- PAUSED 状态：非终态，workspace 保留，可带指令 resume
- 结构化任务完成摘要（目标、变更文件、测试统计、耗时）
- Runtime 指标（`total_agent_s`、`total_test_s`、`total_elapsed_s`）
- Adaptive Memory：对话中自动提取记忆、注入 agent prompt、`/memories` 和 `/forget` 命令

仍缺少：
- 针对运行中任务的内存级 live ring buffer 和状态卡 live excerpt
- artifact delivery 适配层（附件优先、链接兜底）
- 超出当前 `全局 skills + AGENTS.md` 折中的 Codex skill 接入方案
- 基于日期的记忆组织（计划 v0.7）；语义检索（v0.8+）
- Skill 评估（成功率追踪、用户反馈、健康看板；计划 v0.7）
- ops/event autonomy 仍属于后续阶段

## 下一阶段产品方向

- v0.5 已完成 runtime-first 基线（全部完成）。
- v0.6 转向 skill-first autonomy + adaptive memory（记忆已完成，skill 进行中）。
- v0.7 升级记忆为日期驱动架构，增加 ops 基础和 skill 评估。
- v0.8+ 增加语义记忆检索（向量搜索）和 hybrid autonomy。
- 源代码自我更迭不是默认自主性路径，而是高风险、强审批的特殊能力。

## 历史阶段

### v0.6.0

- Adaptive Memory：YAML 存储 + Jaccard 去重 + confidence 评分 + 淘汰策略
- `MemoryExtractor`：对话压缩后由 agent 驱动提取记忆
- 记忆注入：`[Remembered context]` 前置到 agent prompt
- Discord `/memories`（列表 + 类别筛选）和 `/forget`（按 ID 删除）
- Skill task 自动审批 + 自动合并
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
