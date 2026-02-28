# Todo / 路线图

## 当前快照（2026-02-27）

- `/search` 已实现。
- SkillSync reverse sync 已实现。
- CLI-first 基础设施已经到位。
- v0.5 当前主线是 runtime-first。
- 可选 LLM router 已实现。
- Runtime 可观测性基线已实现。
- 多类型 runtime 已实现（`artifact`、`repo_change`、`skill_change`）。

## v0.5 Runtime 加固

- [x] Runtime 任务状态机
- [x] SQLite checkpoints / events / decisions
- [x] 崩溃恢复基线
- [x] 按任务隔离的 worktree
- [x] 多步执行循环
- [x] 步数/时长预算护栏
- [x] allow-all-with-denylist 路径策略
- [x] Discord 按钮审批 + slash 兜底
- [x] Merge gate
- [x] Runtime 外置路径 + 启动迁移
- [x] Janitor 清理
- [x] 短对话 workspace TTL
- [x] 可选 LLM router draft-confirm 流程
- [x] `/task_logs` + 采样式 progress 持久化 + 单条状态消息更新

## v0.5 剩余工作

- [ ] 对运行中 agent/test 子进程的真正中断能力
- [ ] 消息驱动的 runtime 控制（普通 thread 消息触发 `stop/pause/resume`）
- [ ] Resume 体验优化
- [ ] Suggestion 体验优化
- [ ] 结构化任务完成摘要
- [ ] Runtime metrics 和耗时统计
- [ ] 更清晰的 paused/interrupted 状态语义

## v0.6 - Skill-First Autonomy

- [x] 把 skill 创建升级成一等 runtime task 类型
- [x] 支持“把这个 workflow 变成 skill”类请求的路由
- [x] 增加 merge 前的 skill 验证闭环
- [ ] 增加跨 agent skill 抽象
- [ ] 锁定 Codex 接入折中方案（`全局 skills + AGENTS.md/MCP`，不假设 project-level native skills）
- [x] 增加 skill memory / provenance 元数据
- [ ] 将 `mission` 模型和 operator surface 定义为支撑 skill autonomy 的基础设施
- [ ] 将 thread-native runtime control 定义为配套能力，而不是 v0.6 headline
- [ ] 把“调用已有 skill”和“修改 skill”彻底从语义上拆开

## v0.7 - Ops-First 与 Hybrid Autonomy

- [ ] 基于 scheduler / cron 的主动任务
- [ ] 不止 cron 的 event-driven triggers
- [ ] 基于历史对话/任务的重复模式发现
- [ ] recurring workflow -> skill draft 的自动建议
- [ ] skill growth + ops automation 的 hybrid missions
- [ ] 支撑主动性运行的统一 operator surface

## 与 OpenClaw 的差距总结

当前产品形态更接近“Discord-native coding runtime”，还没有完全进入“assistant control plane”阶段。

相对 OpenClaw 的主要差距：

- 当前最强的差距不是“会不会写代码”，而是自主性的分层建设还不完整。
- 当前系统已经完成 runtime-first 基线，下一层最需要的是 skill-first autonomy。
- ops-first autonomy 和 hybrid autonomy 应该放在更后阶段，而不是和 v0.6 主线竞争。
- Skill 平台化已经部分进入一等任务类型，但调用语义和 Codex 兼容性还未完全收敛。
- Operator surface 和 artifact 模型仍需继续补齐，才能支撑长期自主运行。

推荐的下一步架构方向：

- 以 runtime-first 基线作为底层。
- 把 skill-first autonomy 定义为 v0.6 的主产品方向。
- 把 `mission`、operator surface、thread-native control 视为支撑层，而不是 headline。
- 在 skill autonomy 稳定之后，再扩展到 ops-first 与 hybrid autonomy。

## Backlog

- [ ] Feishu/Lark 适配器
- [ ] Slack 适配器
- [ ] Artifact delivery 抽象（附件优先、链接兜底）
- [ ] 面向远端部署的对象存储交付适配器（R2/S3 风格）
- [ ] Markdown 感知的分块发送
- [ ] Rate limiting / request queue
- [ ] Docker 隔离
- [ ] 语义记忆检索
