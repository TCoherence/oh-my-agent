# v0.5 Runtime 计划

## 历史说明

本文档描述的是 v0.5 runtime-first 阶段。v0.6 的产品主线已转向 skill-first autonomy，v0.7 再扩展到 ops-first autonomy 与 hybrid autonomy。

## 目标

构建一个可持久化的自主任务 runtime，让 bot 在长任务里可以持续循环，而不是每一步都等待用户输入。

## Runtime 模型

任务状态：
- `DRAFT`
- `PENDING`
- `RUNNING`
- `VALIDATING`
- `APPLIED`（legacy）
- `WAITING_MERGE`
- `MERGED`
- `MERGE_FAILED`
- `DISCARDED`
- `BLOCKED`
- `FAILED`
- `TIMEOUT`
- `STOPPED`
- `REJECTED`

SQLite runtime 表：
- `runtime_tasks`
- `runtime_task_checkpoints`
- `runtime_task_events`
- `runtime_task_decisions`

进程重启后，`RUNNING` / `VALIDATING` 中的任务会被重新放回 `PENDING`。

## 任务入口

1. 消息意图可自动创建 runtime task。
2. Scheduler job 可创建 runtime task。
3. `/task_start` 可显式创建任务。
4. 可选 LLM router 可在启发式判断前先提议 runtime task。

## 风险分流

默认策略是 `strict`。

只有满足低风险约束时任务才会自动运行，否则进入 `DRAFT` 等待审批。

## 审批交互

- 主路径：Discord 按钮（`Approve`、`Reject`、`Suggest`、`Merge`、`Discard`、`Request Changes`）
- 兜底：slash 命令
- decision nonce 一次性使用，并带 TTL
- reaction 只做状态信号
- runtime 进度应尽量复用并更新单条状态消息，而不是频繁刷消息

## 循环协议

每一步：
1. 基于目标、step、上轮失败信息和 resume instruction 构建 runtime prompt。
2. 在独立 git worktree 中运行 agent。
3. 校验修改路径。
4. 执行测试命令。
5. 持久化 checkpoint 和事件。
6. 根据 `TASK_STATE`、测试结果和预算更新状态。
7. 成功后进入 `WAITING_MERGE`。

## 可观测性

- `/task_logs` 用于查看最近 runtime 事件和输出 tail。
- 全量 heartbeat 保留在进程日志中。
- SQLite 只保存采样后的 progress 事件（`task.agent_progress`、`task.test_progress`），而不是每次 heartbeat。
- Discord 尽量只维护一条可更新的状态消息。

## 命令

- `/task_start`
- `/task_status`
- `/task_list`
- `/task_approve`
- `/task_reject`
- `/task_suggest`
- `/task_resume`
- `/task_stop`
- `/task_merge`
- `/task_discard`
- `/task_changes`
- `/task_logs`
- `/task_cleanup`

## 已知缺口

- stop/resume 还没有通过自然语言消息接入
- 当前 `stop` 还不是对活跃子进程的强制中断
- skill 生成还不是一类一等的 runtime task
