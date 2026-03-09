# Todo / 路线图

## 当前快照（2026-03-01）

- `/search` 已实现。
- SkillSync reverse sync 已实现。
- CLI-first 基础设施已经到位。
- v0.5 runtime-first 已完成（包括 runtime hardening pass）。
- 可选 LLM router 已实现。
- Runtime 可观测性基线已实现。
- Runtime live agent logging 已实现。
- 多类型 runtime 已实现（`artifact`、`repo_change`、`skill_change`）。
- Adaptive Memory 已实现（自动提取、注入、`/memories`、`/forget`）。
- 基于日期的记忆系统已实现（daily/curated 两层架构、自动晋升、MEMORY.md 合成、`/promote`）。
- 图片附件支持已实现（Discord 下载、per-agent 处理、临时文件生命周期管理）。

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
- [x] 跨 agent skill 分发：统一 SKILL.md 格式，SkillSync 分发到 `.claude/`、`.gemini/` 和 `.agents/skills/`；生成的 `AGENTS.md` 汇总 repo 规则和本地 Codex 可见 skill
- [x] Codex 接入：官方 repo/workspace `.agents/skills/` + 生成的 `AGENTS.md`；reverse sync 现在扫描 Claude/Gemini/Codex 原生 skill 目录
- [x] Skill 调用与修改分离：`/skill-name` → 普通对话路径；"创建 skill" → `TASK_TYPE_SKILL_CHANGE` runtime task，专用 prompt、验证和自动合并

### Adaptive Memory（已完成）

- [x] `MemoryExtractor`：对话压缩后自动提取记忆（复用现有 agent）
- [x] 文件系统存储：YAML 格式，每条记忆 = 一句话摘要 + 结构化元数据（category, confidence, source_thread, observation_count）
- [x] 记忆注入：新对话时，从记忆库中选取相关条目注入 agent prompt（token budget 控制，Jaccard 相似度评分）
- [x] `/memories` 命令：展示提取的记忆，带置信度条 + 类别筛选
- [x] `/forget` 命令：按 ID 删除指定记忆
- [x] 记忆冲突合并：Jaccard 去重（阈值 0.6）→ 合并并提升 confidence；按 confidence × 时效性 淘汰
- [x] 跨 agent 共享：记忆属于用户，所有 agent 共用同一 YAML 文件

## v0.7 - 基于日期的记忆系统 + Human-in-the-Loop Ops 基础

### 基于日期的记忆（已完成）

将 adaptive memory 从扁平 YAML 升级为按日期组织的两层架构，参考 [OpenClaw 记忆系统](https://docs.openclaw.ai/concepts/memory)。

- [x] **每日记忆日志**（`memory/daily/YYYY-MM-DD.yaml`）：按日追加的观察记录。系统启动时加载今天 + 昨天，保持近期上下文。
- [x] **长期策展记忆**（`memory/curated.yaml` + `memory/MEMORY.md`）：将稳定记忆提升到持久化长期存储。MEMORY.md 是 agent 合成的自然语言视图。
- [x] **时间衰减评分**：daily 条目按指数衰减（可配置半衰期）。curated 条目不衰减。
- [x] **晋升生命周期**：daily → curated，当 `observation_count ≥ N` 且 `confidence ≥ 阈值` 且 age ≥ 1 天。启动时自动晋升 + `/promote` 手动晋升。
- [x] **压缩前记忆刷写**：记忆提取在历史压缩之前执行（顺序调换），确保不丢失。
- [x] **Discord 命令**：`/memories` 显示 `[C]`/`[D]` 层级标记，新增 `/promote` 命令。

### Ops 基础

- [ ] Scheduler 驱动的主动任务（对接 `automations` 到 runtime task 类型）
- [ ] 超越 cron 的事件驱动触发器（webhook 接入、文件监控、外部通知）
- [ ] **面向 operator 的 doctor 命令**：增加 Discord 优先的自诊断入口（`/doctor` 或等价能力），在进程异常或服务挂掉后，能够汇报最近失败状态、启动健康情况、日志位置和建议排查步骤，而不要求先手动登录服务器找日志

### Human-in-the-Loop Runtime

- [ ] **一等等待状态**：增加专门的 `WAITING_USER_INPUT` runtime 状态，不再把所有人工交互都塞进 `BLOCKED`
- [ ] **Agent 主动提问界面**：允许运行中的 task 在 Discord 里向用户发起带上下文和可选项的明确问题
- [ ] **结构化答案绑定**：把用户的下一条回复绑定到 pending question，并以结构化 answer payload 恢复 task，而不是只靠自由文本 resume instruction
- [ ] **任务中途审批检查点**：对高风险或高歧义步骤支持显式人工确认，而不是只在 merge 阶段做人类审批

### Skill 评估

- [x] **结果追踪**：记录普通聊天路径上的 skill 调用结果（成功/错误/超时/取消），并保留路由来源、延迟和 usage 遥测
- [x] **用户反馈信号**：对第一条带 agent attribution 的 skill 回复加 thumbs-up/down reaction，会按逐次调用持久化评分
- [x] **Skill 健康看板**：`/skill_stats [skill]` 展示成功率、使用频率、最近调用时间、平均延迟和最近评估结论
- [x] **自动降级**：当 skill 失败率超过滚动窗口阈值时，将其从自动路由中移除，但保留显式 `/skill-name`；`/skill_enable` 可人工恢复
- [x] **重复 skill 防重护栏**：新 skill 自动合并前，对名称/描述/请求与现有 skills 做重叠判断；如果能力明显重合，则强制进入人工 merge review
- [x] **基于来源的 skill 评估**：当 skill task 要内化外部 repo/tool/reference 时，要求补齐来源元数据，并在自动合并前跑 source-grounded review

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
- [x] Docker 隔离（host 挂载到 `/home`，repo 挂载到 `/repo`，配置从 repo 读取，启动时 editable install，并预装 CLI 工具）
- [ ] Discord `/restart` 运维命令：触发 host 侧受控容器重启链路（安全边界先定，具体实现后续细化）
- [ ] Adaptive Memory 加密存储 + 认证后明文访问
- [ ] Adaptive Memory 编辑权限控制
- [ ] 在 `.agents/skills/` 迁移稳定后，再评估是否还需要保留生成的 workspace `AGENTS.md`
- [ ] 重新梳理 agent turn-budget 语义：决定是否继续暴露 `max_turns`，明确它与 `timeout`、runtime `max_steps` 的职责边界，并清理或说明当前不同 provider 上并不一致的实际生效情况
- [x] Codex / Gemini CLI session resume
- [ ] Skill feedback UX 后续优化：支持对同一次 skill 结果的任意消息分块做 reaction 反馈，并可选在 skill 完成后单独发一条 feedback prompt/message；反馈范围只针对已完成的 skill 输出，不覆盖 auth/system/普通聊天消息
- [ ] Feishu/Lark 适配器
- [ ] Slack 适配器
