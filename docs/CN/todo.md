# Todo / 路线图

## 当前快照（2026-02-27）

- `/search` 已实现。
- SkillSync reverse sync 已实现。
- CLI-first 基础设施已经到位。
- v0.5 当前主线是 runtime-first。
- 可选 LLM router 已实现。
- Runtime 可观测性基线已实现。

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

## v0.6 方向

- [ ] 按任务特征做智能 agent 路由
- [ ] 多 agent 协作
- [ ] 基于意图的 agent 选择
- [ ] skill-oriented task 类型
- [ ] 针对“把这个流程变成 skill”的意图路由
- [ ] merge 前的 skill 验证闭环

## Backlog

- [ ] Feishu/Lark 适配器
- [ ] Slack 适配器
- [ ] 文件附件流水线
- [ ] Markdown 感知的分块发送
- [ ] Rate limiting / request queue
- [ ] Docker 隔离
- [ ] 语义记忆检索
- [ ] 跨 agent skill 抽象
- [ ] Codex 原生 skill 接入策略
