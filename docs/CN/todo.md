# Todo / 路线图

## 当前快照（2026-02-28）

- `/search` 已实现。
- SkillSync reverse sync 已实现。
- CLI-first 基础设施已经到位。
- v0.5 runtime-first 已完成（包括 runtime hardening pass）。
- 可选 LLM router 已实现。
- Runtime 可观测性基线已实现。
- Runtime live agent logging 已实现。
- 多类型 runtime 已实现（`artifact`、`repo_change`、`skill_change`）。
- Adaptive Memory 已实现（自动提取、注入、`/memories`、`/forget`）。

## v0.5 Runtime 加固（已完成）

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
- [x] 对运行中 agent/test 子进程的真正中断能力
- [x] 消息驱动的 runtime 控制（普通 thread 消息触发 `stop/pause/resume`）
- [x] Resume 体验优化
- [x] Suggestion 体验优化
- [x] 结构化任务完成摘要
- [x] Runtime metrics 和耗时统计
- [x] 更清晰的 paused/interrupted 状态语义

## v0.6 - Skill-First Autonomy + Adaptive Memory

### Skill-First（已完成）

- [x] 把 skill 创建升级成一等 runtime task 类型
- [x] 支持"把这个 workflow 变成 skill"类请求的路由
- [x] 增加 merge 前的 skill 验证闭环
- [x] 增加 skill memory / provenance 元数据
- [x] 跨 agent skill 分发：统一 SKILL.md 格式，SkillSync 分发到 `.claude/`、`.gemini/`、`.codex/` 目录；AGENTS.md 桥接 Codex 发现
- [x] Codex 接入：`AGENTS.md` + workspace `.codex/skills/`；reverse sync 现在扫描全部三个 CLI 目录
- [x] Skill 调用与修改分离：`/skill-name` → 普通对话路径；"创建 skill" → `TASK_TYPE_SKILL_CHANGE` runtime task，专用 prompt、验证和自动合并

### Adaptive Memory（已完成）

- [x] `MemoryExtractor`：对话压缩后自动提取记忆（复用现有 agent）
- [x] 文件系统存储：YAML 格式，每条记忆 = 一句话摘要 + 结构化元数据（category, confidence, source_thread, observation_count）
- [x] 记忆注入：新对话时，从记忆库中选取相关条目注入 agent prompt（token budget 控制，Jaccard 相似度评分）
- [x] `/memories` 命令：展示提取的记忆，带置信度条 + 类别筛选
- [x] `/forget` 命令：按 ID 删除指定记忆
- [x] 记忆冲突合并：Jaccard 去重（阈值 0.6）→ 合并并提升 confidence；按 confidence × 时效性 淘汰
- [x] 跨 agent 共享：记忆属于用户，所有 agent 共用同一 YAML 文件

## v0.7 - 基于日期的记忆系统 + Ops 基础

### 基于日期的记忆（核心）

将 adaptive memory 从扁平 YAML 升级为按日期组织的两层架构，参考 [OpenClaw 记忆系统](https://docs.openclaw.ai/concepts/memory)。

- [ ] **每日记忆日志**（`memory/YYYY-MM-DD.md`）：按日追加的观察记录。系统启动时加载今天 + 昨天的内容，保持近期上下文。
- [ ] **长期策展记忆**（`MEMORY.md`）：将稳定、高置信度的记忆从每日日志提升到持久化长期存储。包含决策、偏好和确认的事实。
- [ ] **时间衰减评分**：近期记忆得分更高；旧的每日条目按指数衰减（可配置半衰期）。`MEMORY.md` 条目为常青内容（不衰减）。
- [ ] **晋升生命周期**：每日 → 长期，当 `observation_count ≥ N` 且 `confidence ≥ 阈值` 跨越多天时触发。支持自动晋升或 agent 辅助策展。
- [ ] **压缩前记忆刷写**：上下文窗口压缩前，触发一次静默 turn 提醒 agent 持久化重要观察，确保长会话中不丢失记忆。
- [ ] **迁移路径**：首次加载时自动将现有 `memories.yaml` 条目迁移到新的日期格式。

### Ops 基础

- [ ] Scheduler 驱动的主动任务（对接 `automations` 到 runtime task 类型）
- [ ] 超越 cron 的事件驱动触发器（webhook 接入、文件监控、外部通知）

### Skill 评估

- [ ] **结果追踪**：记录 skill 调用结果（成功/失败/超时）到 `skill_provenance`
- [ ] **用户反馈信号**：skill 输出后的 thumbs-up/down 反应 → 持久化评分
- [ ] **Skill 健康看板**：`/skill_stats` 展示成功率、使用频率、最近调用时间、平均延迟
- [ ] **自动降级**：当 skill 失败率超过窗口阈值时，从自动调用中降级并通知 owner

### 访客会话（临时隔离）

- [ ] **临时会话模式**：将 session 标记为 `guest`，使用隔离的临时记忆空间（不写入 owner 的 adaptive memory，无 skill 修改权限）
- [ ] 通过 `/guest` 切换或 per-user 配置

## v0.8+ - 记忆智能 + Hybrid Autonomy

### 语义记忆

- [ ] **语义记忆搜索**：基于向量索引的记忆文件检索（embedding `memory_search`），取代 Jaccard 词重叠。BM25 + 向量混合检索，兼顾精确词匹配和语义近义。
- [ ] **分块与索引**：将记忆文件切分为语义块（~400 token，80 重叠），per-agent SQLite 索引，文件变更时自动重建索引。
- [ ] **MMR 多样性重排**：选取注入的记忆时，平衡相关性与多样性，避免每日笔记产生的近重复内容。

### Hybrid Autonomy

- [ ] 基于历史的重复模式发现（识别 recurring workflows）
- [ ] recurring workflow → skill draft 的自动建议
- [ ] skill growth + ops automation 的 hybrid missions
- [ ] 支撑主动性运行的统一 operator surface

### Agent 质量反馈

- [ ] **逐 turn 质量信号**：基于 reaction（thumbs-up/down）或 `/rate` 命令，按 `(thread, turn, agent)` 持久化
- [ ] **Agent 选择反馈闭环**：基于累积质量信号调整 fallback 权重或 agent 选择提示
- [ ] **Skill-agent 亲和度**：追踪哪个 agent 对哪个 skill 效果最好，辅助自动路由

## Backlog（无版本承诺）

- [ ] Live observability ring buffer + 状态卡 live excerpt
- [ ] Artifact delivery 抽象（附件优先、链接兜底）
- [ ] 面向远端部署的对象存储交付适配器（R2/S3 风格）
- [ ] 交付策略抽象（inline summary / attachment / link）
- [ ] Markdown 感知的分块发送
- [ ] Rate limiting / request queue
- [ ] Docker 隔离
- [ ] Adaptive Memory 加密存储 + 认证后明文访问
- [ ] Adaptive Memory 编辑权限控制
- [x] Codex / Gemini CLI session resume
- [ ] Feishu/Lark 适配器
- [ ] Slack 适配器
