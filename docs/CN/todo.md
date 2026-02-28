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

- [ ] 引入一层高于 thread 和 runtime task 的一等 `mission` 模型
- [ ] 支持普通消息触发的 thread-native runtime 控制（`pause` / `resume` / `stop` / `summarize`）
- [ ] 支持对运行中 agent/test 子进程的真正中断，以及基于 checkpoint 的恢复
- [ ] 把 skill 创建升级成一等 runtime task 类型
- [ ] 支持“把这个 workflow 变成 skill”这类请求的 skill 路由
- [ ] 增加 operator surface，用于查看 active/blocked missions、approvals、artifacts
- [ ] 增加一等任务产物模型（diff、test summary、生成文件、commit、截图等）
- [ ] 按任务特征做智能 agent 路由
- [ ] 多 agent 协作
- [ ] 基于意图的 agent 选择
- [ ] skill-oriented task 类型
- [ ] 针对“把这个流程变成 skill”的意图路由
- [ ] merge 前的 skill 验证闭环

## 与 OpenClaw 的差距总结

当前产品形态更接近“Discord-native coding runtime”，还没有完全进入“assistant control plane”阶段。

相对 OpenClaw 的主要差距：

- Mission 模型：OpenClaw 更像 assistant/session/control-plane；当前 repo 仍主要是 `thread + router + runtime task` 的组合。
- Runtime 控制：已经有自主执行循环，但 operator 控制仍偏命令式，也还缺真正的中断和自然恢复。
- Skill 平台化：skill sync 和 tooling 已有，但 skill 生成与复用还不是一等任务类型。
- Operator surface：已有 task logs 和状态消息，但还没有统一视角来查看 active missions、blocked reasons、pending approvals 和 artifacts。
- Artifact 模型：merge review 仍主要依赖文本摘要，任务输出还没有完全升格成一等产物。

推荐的下一步架构方向：

- 把 `mission` 作为长期存在的工作单元。
- 把 thread 视为 mission 的一种交互界面。
- 把 runtime task 视为 mission 下面的一次执行尝试。
- 把 human control 和 skill creation 一起纳入 mission 生命周期。

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
