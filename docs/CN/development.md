# 开发记录

## 项目概览

Oh My Agent 是一个多平台 bot，执行层直接使用 CLI Agent，而不是直接调用模型 API。自 v0.4 起，整体架构方向明确为 CLI-first；API agent 已进入弃用路径。

## 真相来源

1. `README_EN.md` / `README_CN.md`
2. `docs/todo_EN.md` / `docs/todo_CN.md`
3. `docs/v0.5_runtime_plan_EN.md` / `docs/v0.5_runtime_plan_CN.md`
4. `docs/future_planning_discussion.md` 作为历史讨论归档

## 当前 Runtime 基线

已实现：
- 可选 LLM 意图路由（`reply_once` vs `propose_task`）
- 短对话临时 workspace + TTL 清理，状态持久化到 SQLite
- Runtime 可观测性基线：
  - `/task_logs`
  - SQLite 中采样式 progress snapshot
  - 进程日志中的完整 heartbeat
  - Discord 中单条可更新的状态消息

仍缺少：
- 能中断活跃子进程的真正 stop/pause/resume
- 消息驱动的 runtime 控制
- 作为一类一等 runtime task 的 skill 生成

## 历史阶段

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
