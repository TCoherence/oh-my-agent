# Todo / 路线图

## 当前快照（2026-04-16）

- `/search` 已实现。
- SkillSync reverse sync 已实现。
- CLI-first 基础设施已经到位。
- `v0.7.3` 已发布（phase 1–3 完成）。
- `v0.8.0` 已发布（全部四个 phase 完成）。详见 CHANGELOG。
- `v0.8.1` 已发布：记忆质量优化、skill contract 加固、播客集成、automation YAML 修复。详见 CHANGELOG。
- v0.5 runtime-first 已完成（包括 runtime hardening pass）。
- 可选 LLM router 已实现。
- Runtime 可观测性基线已实现。
- Runtime live agent logging 已实现。
- 多类型 runtime 已实现（`artifact`、`repo_change`、`skill_change`）。
- `WAITING_USER_INPUT` 和通用单选式 `ask_user` HITL 已实现。
- `repair_skill` router 意图已实现。
- Adaptive Memory 已实现（自动提取、注入、`/memories`、`/forget`）。
- 基于日期的记忆系统已实现（daily/curated 两层架构、自动晋升、MEMORY.md 合成、`/promote`）。
- 图片附件支持已实现（Discord 下载、per-agent 处理、临时文件生命周期管理）。
- Codex repo/workspace skill 发现已切到官方 `.agents/skills/`；生成的 workspace `AGENTS.md` 只保留 rules/metadata。
- Service-layer 提取完成（task、ask、doctor、automation、HITL 服务）。
- Markdown-aware chunker、结构化日志、graceful shutdown、错误展示契约、速率限制、并发隔离测试均已实现。
- 首个正式 `compose.yaml` 和运营指南（EN + CN）已发布。
- 记忆系统质量优化已完成（提取窗口修复、两段式去重、快慢晋升路径、scope-aware 分桶检索）。
- `seattle-metro-housing-watch` 和 `market-briefing` skill contract 已更新；`market-briefing` AI 和财经日报均已支持播客预取。
- Automation YAML 文件已补全 `skill_name` 字段，确保 timeout 正确继承 skill metadata；prompt 改为引用 SKILL.md workflow，不再硬编码输出路径。
- 按 automation 配置 `auto_approve` 标志（默认关闭）；DRAFT 卡片和 DM 通知展示人类可读的风险原因；新增 `/automation_run` 手动触发命令。
- `market-briefing` AI people-pool 发现规则已写入 SKILL.md；`report_store.py persist` 自动记录 people pool 条目。
- 当前下一个目标：`v0.9`（1.0 RC / Contract Freeze）。详见 `v1.0-plan.md`。

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
- [x] 跨 agent skill 分发：统一 SKILL.md 格式，SkillSync 分发到 `.claude/`、`.gemini/` 和 `.agents/skills/`；生成的 `AGENTS.md` 汇总 repo 规则和 workspace 元信息
- [x] Codex 接入：官方 repo/workspace `.agents/skills/` + 用于 rules/metadata 的生成 `AGENTS.md`；reverse sync 现在扫描 Claude/Gemini/Codex 原生 skill 目录
- [x] Skill 调用与修改分离：`/skill-name` → 普通对话路径；"创建 skill" → `TASK_TYPE_SKILL_CHANGE` runtime task，专用 prompt、验证和 merge gate

### Adaptive Memory（已完成）

- [x] `MemoryExtractor`：对话压缩后自动提取记忆（复用现有 agent）
- [x] 文件系统存储：YAML 格式，每条记忆 = 一句话摘要 + 结构化元数据（category, confidence, source_thread, observation_count）
- [x] 记忆注入：新对话时，从记忆库中选取相关条目注入 agent prompt（token budget 控制，Jaccard 相似度评分）
- [x] `/memories` 命令：展示提取的记忆，带置信度条 + 类别筛选
- [x] `/forget` 命令：按 ID 删除指定记忆
- [x] 记忆冲突合并：Jaccard 去重（阈值 0.6）→ 合并并提升 confidence；按 confidence × 时效性 淘汰
- [x] 跨 agent 共享：记忆属于用户，所有 agent 共用同一 YAML 文件

## v0.7 - 基于日期的记忆系统 + HITL/Ops 基础

### 基于日期的记忆（已完成）

将 adaptive memory 从扁平 YAML 升级为按日期组织的两层架构，参考 [OpenClaw 记忆系统](https://docs.openclaw.ai/concepts/memory)。

- [x] **每日记忆日志**（`memory/daily/YYYY-MM-DD.yaml`）：按日追加的观察记录。系统启动时加载今天 + 昨天，保持近期上下文。
- [x] **长期策展记忆**（`memory/curated.yaml` + `memory/MEMORY.md`）：将稳定记忆提升到持久化长期存储。MEMORY.md 是 agent 合成的自然语言视图。
- [x] **时间衰减评分**：daily 条目按指数衰减（可配置半衰期）。curated 条目不衰减。
- [x] **晋升生命周期**：daily → curated，当 `observation_count ≥ N` 且 `confidence ≥ 阈值` 且 age ≥ 1 天。启动时自动晋升 + `/promote` 手动晋升。
- [x] **压缩前记忆刷写**：记忆提取在历史压缩之前执行（顺序调换），确保不丢失。
- [x] **Discord 命令**：`/memories` 显示 `[C]`/`[D]` 层级标记，新增 `/promote` 命令。

### Human-in-the-Loop 基线（已完成）

- [x] **一等等待状态**：`WAITING_USER_INPUT` 已实现，用于 thread 和 task 级暂停
- [x] **Agent 主动提问界面**：通用单选式 `ask_user` challenge 已接到 Discord
- [x] **结构化单选答案**：owner 按钮选择会持久化，并用于恢复 direct chat 和 runtime task
- [x] **owner 通知与持久化**：`ask_user` prompt 会落到 SQLite、重启后重新注册，并触发可见 owner 提醒

### Skill 评估

- [x] **结果追踪**：记录普通聊天路径上的 skill 调用结果（成功/错误/超时/取消），并保留路由来源、延迟和 usage 遥测
- [x] **用户反馈信号**：对第一条带 agent attribution 的 skill 回复加 thumbs-up/down reaction，会按逐次调用持久化评分
- [x] **Skill 健康看板**：`/skill_stats [skill]` 展示成功率、使用频率、最近调用时间、平均延迟和最近评估结论
- [x] **自动降级**：当 skill 失败率超过滚动窗口阈值时，将其从自动路由中移除，但保留显式 `/skill-name`；`/skill_enable` 可人工恢复
- [x] **重复 skill 防重护栏**：新 skill 自动合并前，对名称/描述/请求与现有 skills 做重叠判断；如果能力明显重合，则强制进入人工 merge review
- [x] **基于来源的 skill 评估**：当 skill task 要内化外部 repo/tool/reference 时，要求补齐来源元数据，并在合并审批前跑 source-grounded review

## v0.7.3 - HITL Completion、Delivery、Operator Observability

### Phase 1（已完成）
- [x] **Artifact delivery 抽象**：统一平台/runtime 交付层，先尝试附件上传；上传不可用或产物超限时回退为本地绝对路径
- [x] **按 thread 聚合的统一日志**：`~/.oh-my-agent/runtime/logs/threads/<thread_id>.log` 已成为 chat/invoke/runtime/HITL resume 的主要 agent 审计入口
- [x] **HITL completion 语义补齐**：现有 `WAITING_USER_INPUT` 基线之上的单选 answer binding、resume context injection 和 checkpoint 复用语义已实现
- [x] **面向 operator 的 doctor 命令**：Discord `/doctor` 输出 runtime、HITL、auth、scheduler 和日志路径健康快照

### Phase 2 — Automation State + Operator Surfaces + Delivery + Live Observability（已完成）
- [x] **Automation 运行时状态持久化**：`automation_runtime_state` SQLite 表，含 `last_run_at`、`last_success_at`、`last_error`、`last_task_id`、`next_run_at`；scheduler fire/complete/fail 路径写入状态；跨重启持久化；disabled 的 automation `next_run_at = NULL`
- [x] **Operator surfaces 收尾**：`/doctor` 现在展示 HITL waiting/resolving 分布和近期 automation 失败；`/automation_status` 展示持久化的运行时状态（last run、last success、next run、last error、last task ID）以及定义信息
- [x] **Skill timeout 传递**：automation YAML `skill_name` 字段把 `metadata.timeout_seconds` 传递到 scheduler 触发的 artifact task 的 `max_minutes`
- [x] **Delivery 收尾**：`_completed_text` 统一渲染 `ArtifactDeliveryResult` 的交付信息；`deliver_files()` 提取为可复用的核心方法，与 `RuntimeTask` 解耦
- [x] **Live observability 收尾**：运行中 task 的状态卡包含来自 live agent log 的有界 `Latest activity`；按钮在所有终态操作后进入稳定 disabled 状态

### Phase 3 — HITL Checkpoint Semantics Closeout（已完成）
- [x] **Checkpoint 模型归一化**：`HITL_CHOICES_APPROVAL` 和 `HITL_CHOICES_CONTINUE` 作为标准选项族内部常量；`WAITING_USER_INPUT` 仍为统一等待状态
- [x] **Answer binding 契约收尾**：answer payload 包含 `prompt_id`、`target_kind`、`question`、`choice_id`、`choice_label`、`choice_description`、`answered_at`；结构化 payload 为 truth source，`[HITL Answer]` 文本块保留用于 agent 兼容
- [x] **Resume 语义收尾**：task HITL 恢复到 PENDING 并携带结构化 + 文本 payload；thread HITL 自动恢复并继承 `last_hitl_answer`（仅保留最新，无链式传递）；跨 task 隔离通过 `task_id` 范围的事件查询实现
- [x] **Operator 可见的 HITL 状态**：`/task_logs` 展示活跃/最近的 HITL checkpoint 问题和已选答案

## v0.8 — 1.0 Hardening（已完成）

详见 [`v1.0-plan.md`](v1.0-plan.md)。

### 1. 平台抽象（Platform Abstraction）
- [x] 从 `discord.py` 提取 service-layer 架构（平台无关的业务逻辑）
- [x] task control service（最高优先级 — 命令最多、状态逻辑最重）
- [x] ask service（核心入口路径）
- [x] doctor / automation / auth / memory services
- [x] BaseChannel contract review：message edit、attachment upload、interactive prompt 等

### 2. 可靠性加固（Reliability Hardening）
- [x] graceful shutdown contract（gateway、runtime workers、subprocesses、SQLite/WAL）
- [x] startup config validation（schema 校验、fail-fast、CLI binary 检查）
- [x] upgrade/migration contract（SQLite schema、config 兼容性、skill/workspace 路径迁移）
- [x] markdown-aware chunking
- [x] rate-limit / request queue
- [x] concurrent thread/task isolation testing
- [x] log hygiene（rotation、log-level config、structured logging）
- [x] user-visible error contract（readable messages, not tracebacks）
- [x] missed-job policy = `skip`（已实现，需文档化和测试覆盖）

### 3. 部署加固（Deployment Hardening）
- [x] first-class `docker-compose`
- [x] local vs Docker 安装/运行文档统一
- [x] runtime directories / backup / restore instructions
- [x] operator-facing restart and upgrade SOP
- [x] health-check for long-running service mode

## v0.8 后续 — 记忆系统质量优化（已完成）

- [x] **提取窗口重写**：改用最近 6 个 turn（每条 assistant turn ≤800 字符），不再从 full history 头部截断，确保最新 user 证据始终进入提取窗口
- [x] **提取触发优化**：无新 user turn 且上次提取为空时跳过；进程内 per-thread 状态，无需持久化
- [x] **提取 prompt 收紧**：user-only 证据规则，明确负面规则（一次性任务细节、临时计划、文件路径、slash command、未来推测）
- [x] **parse 失败回退**：首次失败用简化 schema 重试；二次失败返回空并写入 `parse_failure` 日志
- [x] **`MemoryEntry` schema 第一批**：`explicitness`、`status`、`evidence`、`last_observed_at`；老 YAML 文件懒迁移
- [x] **`MemoryEntry` schema 第二批**：`scope`、`durability`、`source_skills`、`source_workspace`；scope 相关 helper 函数加入 `adaptive.py`
- [x] **两段式去重**：词法归一化阶段 + 单次 batch agent merge 判定；矛盾条目标记 `superseded`
- [x] **快慢晋升路径**：显式高置信 memory 1-2 次即可晋升 curated；inferred memory 需要跨 thread 或跨日期重复；`fact` 类不走 fast-path
- [x] **scope-aware 分桶检索**：四桶排名（skill_scoped / workspace_project / global_preference / recent_daily）；scope 分数乘数；`superseded` 永不注入或进 `MEMORY.md`
- [x] **结构化 trace 日志**：`memory_extract`、`memory_merge`、`memory_promote`、`memory_inject` 事件，含逐决策字段
- [x] **`/memories` 展示增强**：新增 `explicitness`、`status`、`observation_count`、`last_observed_at` 字段显示
- [x] **实现缺口修复**：`max_memories` 生效、跨文件 merge 持久化、`promote_memory()` curated 去重、`last_observed_at` 一致性

## v0.8 后续 — Skill Contract 更新（已完成）

- [x] **`seattle-metro-housing-watch`**：默认 7 区 contract（Bothell + Lynnwood 升为默认覆盖）；Zillow 成为 area trend 正式第二来源；30Y + 15Y 固定利率并列比较；listing contract（仅 single-family/townhouse，每区保底 2 套 + 4 个优先级名额，hard cap 18，按区自身中位价过滤）；`sample_listings[]` 扩展 source_site / property_type / listed_at / original_list_price / price_history_summary；各 mode 样本配额分层（snapshot 1/区，deep-dive 4-6 套）
- [x] **`market-briefing`**：finance daily 扩为 8 段固定结构（新增中国/港股脉搏、美国波动/风险偏好、中国房地产政策）；AI daily 扩为 9 段，新增 Frontier Labs Radar；frontier watchlist（8 家 lab）含 rumor 纪律规则；finance/politics 边界规则写入 reference；`timeout_seconds: 1200`；新增 `references/finance_watchlist.md` 和 `references/ai_frontier_watchlist.md`

## v0.9 — Memory 子系统重构 + 1.0 RC / Contract Freeze

### Memory 子系统重构（已完成，v0.9.0 发布）

替换原 daily/curated 两层架构 + per-turn `MemoryExtractor`（旧实现因 LLM paraphrase 导致 dedup 永远命中不了，整个 store 卡在 `obs=1`），改为单层 `JudgeStore` + 事件驱动的 `Judge` agent，judge 在 prompt 里能看到现有 memory。

- [x] 单层 `memories.yaml` schema，带 `status` / `superseded_by` 链路
- [x] 三路触发：thread idle 15 分钟、`/memorize` slash command、自然语言关键词（`记一下` / `remember this`）
- [x] action 模型：`add` / `strengthen` / `supersede` / `no_op`
- [x] `MEMORY.md` 在 dirty / 缺失 / mtime > 6h 时自动 synthesize
- [x] 数据迁移脚本（`scripts/migrate_memory_to_judge.py`）
- [x] 删除 `/promote` slash command 和 `memory.adaptive` config 段

### 1.0 RC / Contract Freeze（待开始）

- [ ] 完成剩余的 service-layer 抽取
- [ ] 清除 adapter 中残留的业务逻辑
- [ ] 端到端 restart/recovery 测试（chat、skill invoke、runtime tasks、HITL、auth、automations）
- [ ] 旧版本 state layout 升级验证
- [ ] 文档升级为 operator-grade product docs
- [ ] 裁剪或延后未就绪的实验性 surface

## Post-1.0 / 1.x

以下移出 `1.0` 关键路径。详见 [`v1.0-plan.md`](v1.0-plan.md)。

### 平台扩展
- [ ] Slack 适配器
- [ ] Feishu/Lark 适配器
- [ ] WeChat 适配器

### 语义记忆
- [ ] 语义记忆搜索（BM25 + vector hybrid）
- [ ] 分块与索引
- [ ] MMR 多样性重排

### Hybrid Autonomy
- [ ] 基于历史的重复模式发现（识别 recurring workflows）
- [ ] recurring workflow → skill draft 自动建议
- [ ] hybrid missions（skill growth + ops automation）
- [ ] 统一 operator surface

### Agent 质量反馈
- [ ] 逐 turn 质量信号（reaction 或 `/rate` 命令）
- [ ] Agent 选择反馈闭环
- [ ] Skill-agent 亲和度

### 其他延后项
- [ ] 超越 cron 的事件驱动触发器（webhook、file-watch、外部通知）
- [ ] Scheduler 驱动的运维任务（automations 对接 runtime task 类型）
- [ ] 临时会话 / guest mode（`/guest` 切换或 per-user 配置）
- [ ] free-text HITL
- [ ] 远端对象存储交付（R2/S3 风格）
- [ ] 更丰富的 automation 调度模型（RRULE 或完整 cron 语义）

## Backlog（无版本承诺）

- [ ] Live observability ring buffer + 状态卡 live excerpt
- [ ] 交付策略细化（inline summary / attachment / link），包括 Discord 友好的 markdown-heavy 产物展示：表格自动降级为 code block / list、可选 CSV/HTML/图片附件，以及适合 scoreboard 的 embed/card 交付模式
- [x] Docker 隔离（host 挂载到 `/home`，repo 挂载到 `/repo`，配置从 repo 读取，启动时 editable install，并预装 CLI 工具）
- [ ] Discord `/restart` 运维命令：触发 host 侧受控容器重启链路（安全边界先定，具体实现后续细化）
- [ ] Adaptive Memory 加密存储 + 认证后明文访问
- [ ] Adaptive Memory 编辑权限控制
- [ ] 在 `.agents/skills/` 迁移稳定后，再评估是否还需要保留生成的 workspace `AGENTS.md`
- [ ] 重新梳理 agent turn-budget 语义：决定是否继续暴露 `max_turns`，明确它与 `timeout`、runtime `max_steps` 的职责边界，并清理或说明当前不同 provider 上并不一致的实际生效情况
- [ ] Automation 并发策略与可观测性：明确 runtime worker-pool 与队列语义，暴露 queued/running job 与 worker occupancy，并评估是否需要 per-automation 并发控制或优先级
- [ ] Scheduler liveness watchdog：discord gateway 剧烈抖动（密集 `session invalidated` + `websocket behind`）后，scheduler cron loop 可能静默 stall —— 进程、discord.py、short-workspace janitor 都活着，只有 scheduler tick 停转，直到手动 SIGINT 重启才恢复。观察到的真实案例：2026-04-18 08:30 PDT paper-digest-daily-0830 错过触发，10 小时内零 scheduler 事件。需要给 scheduler 加存活指标（最近 tick 的 mtime 或心跳计数），manager 层周期性检查，stale 时重启内部 loop；不要做 catch-up/backfill（漏跑由 `/automation_run` 补）
- [ ] CLI agent credential recovery：统一识别 `claude` / `codex` / `gemini` 的认证失效（401、invalid credentials、login required），避免无意义 fallback；补 owner-facing 提示、可恢复状态，以及按 provider 区分的自动/半自动重新登录链路
- [x] Codex / Gemini CLI session resume
- [ ] 增加内部 CLI agent 生命周期 hook（`pre-run`、`post-run`、`failure`、`resume`），用于 system-owned 的后处理能力，例如 reverse sync、artifact 后处理和可观测性收尾；这应保持为内部机制，而不是用户可见的新功能面
- [ ] Skill feedback UX 后续优化：支持对同一次 skill 结果的任意消息分块做 reaction 反馈，并可选在 skill 完成后单独发一条 feedback prompt/message；反馈范围只针对已完成的 skill 输出，不覆盖 auth/system/普通聊天消息
- [x] 持久化 automation 运行时状态（`last_run`、`next_run`、`last_error`），而不是每次重启后全部重算
- [x] 增加 automation 的 operator 控制面，例如 `/automation_status`、`/automation_reload`、`/automation_enable`、`/automation_disable`（当前是 Discord-only、owner-only、ephemeral 的 MVP）
- [x] PRIORITY：把 skill 级别的 `metadata.timeout_seconds` 继续传递到 runtime task / automation 执行链路里，让长耗时的 automation-backed skill 也能继承和直接 skill 调用一致的 timeout override
- [x] missed-job policy 已定为 `skip`（不补跑、不追赶）
- [x] 面向 operator 的 automation 可观测性（`/automation_status` 显示运行时状态，`/doctor` 显示近期失败）
- [x] 按 automation 配置 `auto_approve` 标志（默认 `false`）：scheduler 任务可在显式开启时跳过风险评估；保守默认值下高风险任务仍需手动审批
- [x] `/automation_run` 手动触发：按需执行任意已启用的 automation job（owner-only）
- [x] DRAFT 通知展示人类可读的风险原因：thread 状态卡和 owner DM 现在展示具体风险标签（如"prompt 包含敏感关键词"），而非笼统的"Reason: draft"
- [x] `market-briefing` AI people-pool 发现流水线：SKILL.md 中写入详细发现规则，`report_store.py persist` 自动调用 people pool 记录
