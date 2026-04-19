# 配置参考

`config.yaml` 字段逐项说明。`config.yaml.example` 里的每个 key 都在这里有条目。

加载顺序：
1. CLI flag（`--config /path/to/config.yaml`）优先。
2. 否则当前目录的 `./config.yaml`。
3. 否则报错。

加载时所有字符串值会做 `${ENV_VAR}` 替换。

---

## `memory`

对话历史持久化 + Judge 长期记忆。

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `backend` | string | `sqlite` | 1.0 仅支持 `sqlite`。 |
| `path` | string | `~/.oh-my-agent/runtime/memory.db` | SQLite 文件，自动启用 WAL。 |
| `max_turns` | int | `20` | 每条 thread 在压缩前保留多少轮 user/assistant 原文对。 |
| `summary_max_chars` | int | `500` | 压缩后 summary 块的字符上限。 |

### `memory.judge`

长期记忆（事件驱动 Judge 写入 `memories.yaml`）。

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | Judge agent 总开关。 |
| `memory_dir` | string | `~/.oh-my-agent/memory` | 存放 `memories.yaml` + `MEMORY.md`。 |
| `inject_limit` | int | `12` | 注入到 agent prompt `[Remembered context]` 块的最大 active 记忆数。 |
| `idle_seconds` | int | `900` | thread 沉默这么多秒后 Judge 触发。 |
| `idle_poll_seconds` | int | `60` | idle 扫描的 tick 间隔。越小反应越快但 CPU 越高。 |
| `synthesize_after_seconds` | int | `21600`（6 小时） | `MEMORY.md` 比这老且 `memories.yaml` 是 dirty 时重建。 |
| `max_evidence_per_entry` | int | `8` | 每条记忆 `evidence_log` 上限。 |
| `keyword_patterns` | list[str] | 见 example | 触发即时 `/memorize` 的自然语言关键词（例如「记一下」、「remember this」）。 |

> **废弃别名**：`memory.adaptive` 仍作为 `memory.judge` 的 fallback 被接受，启动时会发 warning。1.0 中应改名。

---

## `skills`

Skill 加载、telemetry、auto-disable。

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | False 时不加载也不同步 skill。 |
| `path` | string | `skills/` | 源目录；这里的文件会被软链到 `.claude/skills/` 和 `.gemini/skills/`（或复制到 `workspace/...`）。 |
| `telemetry_path` | string | `~/.oh-my-agent/runtime/skills.db` | 调用历史和反馈的 SQLite。 |

### `skills.evaluation`

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 评估/反馈采集总开关。 |
| `stats_recent_days` | int | `7` | `/skill_stats` 「最近」窗口。最低 1。 |
| `feedback_emojis` | list[str] | `["👍", "👎"]` | 计入反馈的表情。👍 = +1、👎 = -1。 |

### `skills.evaluation.auto_disable`

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 反复失败的 skill 自动停用。 |
| `rolling_window` | int | `20` | 看最近 N 次调用。 |
| `min_invocations` | int | `5` | 低于此次数永不自动停用（避免冷启被锁）。 |
| `failure_rate_threshold` | float | `0.60` | 窗口内失败率 ≥ 阈值就停用。 |

### `skills.evaluation.overlap_guard`

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 阻止 agent 创建与现有 skill 高度重叠的新 skill。 |
| `review_similarity_threshold` | float | `0.45` | SKILL.md description 的余弦相似度阈值。 |

### `skills.evaluation.source_grounded`

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 校验 skill 输出里的引用是否解析得到。 |
| `block_auto_merge` | bool | `true` | source-grounded 失败的 skill task 拒绝自动合并。 |

---

## `access`

owner 访问门。

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `owner_user_ids` | list[str] | `[]` | 允许使用 bot 的 Discord 用户 ID。空列表 = 频道成员皆可。系统消息绕过此门。 |

---

## `auth`

第三方提供方的内置 OAuth/QR 登录流程（目前仅 Bilibili）。

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 总开关。 |
| `storage_root` | string | `~/.oh-my-agent/runtime/auth` | 凭证 blob 存放位置。 |
| `qr_poll_interval_seconds` | int | `3` | 轮询上游 QR 扫描完成的间隔。 |
| `qr_default_timeout_seconds` | int | `180` | 鉴权流程超时秒数。 |

### `auth.providers.bilibili`

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 单独开关此 provider。 |
| `scope_key` | string | `default` | 凭证逻辑命名空间，允许多身份共存。 |

---

## `workspace`

沙箱隔离根（Layer 0）。设了之后每个 CLI agent 都用 `cwd=workspace`、env 走白名单、skill 复制（不软链）进 workspace。

| 类型 | 默认 | 说明 |
|---|---|---|
| string \| null | `~/.oh-my-agent/agent-workspace` | 设为 `null`（或省略）= 兼容旧的「进程 cwd + 全 env」模式（生产不推荐）。 |

---

## `short_workspace`

按 thread 临时 workspace，给 `/ask` 的 artifact 用。每条 thread 一个子目录，janitor 按 TTL 删。

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | False 时回退到主 `workspace`。 |
| `root` | string | `~/.oh-my-agent/agent-workspace/sessions` | 基础目录，thread 在这里建子目录。 |
| `ttl_hours` | int | `24` | 老于此小时数的子目录会被删。 |
| `cleanup_interval_minutes` | int | `1440`（1 天） | 清理器扫描频率。 |

---

## `router`

可选的 LLM 意图分类，用于入站消息。

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `false` | 默认关——启发式流程已经能 cover 大多数场景。 |
| `provider` | string | `openai_compatible` | 当前只支持这个 provider。 |
| `base_url` | string | 启用时必填 | OpenAI 兼容接口（DeepSeek、Together 等）。 |
| `api_key_env` | string | 启用时必填 | 读 key 的环境变量名。 |
| `model` | string | 启用时必填 | 例如 `deepseek-chat`。 |
| `timeout_seconds` | int | `8` | 单次分类硬上限。 |
| `max_retries` | int | `1` | HTTP 错或解析失败时重试次数。 |
| `confidence_threshold` | float | `0.55` | 低于此置信度回退到启发式。 |
| `context_turns` | int | `10` | 提供给 router 的最近轮数。 |
| `require_user_confirm` | bool | `true` | 高置信分类前先问用户确认。 |

> 1.0 中标记为 **experimental**——若使用数据显示价值低可能下线。

---

## `automations`

Cron / interval 周期任务。

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 总开关。False 时不构造调度器。 |
| `storage_dir` | string | `~/.oh-my-agent/automations` | 每个 `*.yaml` = 一个 job 定义。支持热重载。 |
| `reload_interval_seconds` | int | `5` | 文件轮询间隔（热重载用）。 |
| `timezone` | string | `local` | `local` 或 IANA 时区如 `America/Los_Angeles`。 |

每个 automation 的 YAML schema 见 [development.md](development.md) 的「Adding a new automation」段。

---

## `runtime`

自治任务编排。

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | `/task_*` slash 命令总开关。False 时只剩 chat 能用。 |
| `state_path` | string | `~/.oh-my-agent/runtime/runtime.db` | task 状态机的 SQLite。 |
| `worker_concurrency` | int | `3` | 并发 runtime task 上限。 |
| `worktree_root` | string | `~/.oh-my-agent/runtime/tasks` | task worktree 根目录。 |
| `reports_dir` | string | `~/.oh-my-agent/reports` | artifact 归档根目录，按 `YYYY-MM-DD/` 分层。设为 `""` 或 `false` 关闭归档。**不会**自动清理。 |
| `default_agent` | string | `codex` | task 没指定 agent 时用这个。 |
| `default_test_command` | string | `pytest -q` | VALIDATING 阶段执行的命令。 |
| `default_max_steps` | int | `8` | 单 task 的 agent 步数硬上限。 |
| `default_max_minutes` | int | `20` | 单 task wall-clock 上限。**不同于** `agents.<x>.timeout`（单次 agent 调用）和 `skills.evaluation.<name>.timeout`（单次 skill）。 |
| `skill_auto_approve` | bool | `true` | `skill_change` 任务跳过 DRAFT、自动合并。 |
| `risk_profile` | string | `strict` | `strict` / `lenient`。决定 `evaluate_strict_risk` 行为。 |
| `path_policy_mode` | string | `allow_all_with_denylist` | 当前唯一支持的模式。 |
| `denied_paths` | list[str] | 见 example | agent 不可触碰的 glob 模式。 |
| `decision_ttl_minutes` | int | `1440` | DRAFT 任务超时自动失效。 |
| `agent_heartbeat_seconds` | int | `20` | agent 取消监视的 tick 间隔。 |
| `test_heartbeat_seconds` | int | `15` | VALIDATING 期间的 tick 间隔。 |
| `test_timeout_seconds` | int | `600` | 单次测试调用硬上限。 |
| `progress_notice_seconds` | int | `30` | 沉默这么久后发进度消息。 |
| `progress_persist_seconds` | int | `60` | 沉默这么久后持久化进度 checkpoint。 |
| `log_event_limit` | int | `12` | `/task_logs` 默认返回的事件数。 |
| `log_tail_chars` | int | `1200` | 每个事件 tail 字符数。 |
| `service_retention_days` | int | `7`（代码中默认） | `service.log` 轮转保留期。需要时在顶层覆盖。 |
| `shutdown_timeout_seconds` | int | `30`（代码中默认） | SIGTERM 后排空预算。 |

### `runtime.cleanup`

陈旧 worktree + thread log 的 janitor。

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | 总开关。 |
| `interval_minutes` | int | `60` | 扫描频率。 |
| `retention_hours` | int | `168`（7 天） | 老于此小时的 task 进入清理候选。 |
| `prune_git_worktrees` | bool | `true` | 删 workspace 后跑 `git worktree prune`。 |
| `merged_immediate` | bool | `true` | 已合并的 task 立即删 workspace，不等扫描。 |

### `runtime.merge_gate`

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | `true` | False 时 `repo_change` task 整个跳过合并步骤。 |
| `auto_commit` | bool | `true` | 合并前自动 stage + commit worktree 修改。 |
| `require_clean_repo` | bool | `true` | 拒绝合并到脏父仓库。 |
| `preflight_check` | bool | `true` | 合并结果再跑一次测试。 |
| `target_branch_mode` | string | `current` | `current` = 父仓库的 HEAD；其他模式预留。 |
| `commit_message_template` | string | 见 example | `{task_id}` 和 `{goal_short}` 会被插值。 |

---

## `gateway`

频道适配 + 接入管道。

### `gateway.channels[]`

每条绑定一个 platform/channel。

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `platform` | string | 必填 | 1.0 仅 `discord`。**`slack` 会被 config validator 拒绝**——见 [upgrade-guide.md](upgrade-guide.md)。 |
| `token` | string | 必填 | Bot token。可用 `${ENV_VAR}` 替换。 |
| `channel_id` | string | 必填 | Discord 频道数字 ID（字符串）。 |
| `agents` | list[str] | 必填 | agent fallback 顺序，第一个成功就停。名字要对应顶层 `agents:` 下的 key。 |

---

## `agents`

`agents:` 顶层每个 key 是一个逻辑 agent 名，由 `gateway.channels[].agents` 引用。

### 公共字段（所有 CLI agent）

| Key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `type` | string | 必填 | 当前只支持 `cli`。（`api` 自 v0.4.0 起废弃。） |
| `cli_path` | string | 必填 | 可执行文件名（PATH 解析）或绝对路径。 |
| `model` | string | 因 agent 而异 | 作为 `--model` 透传给 CLI。 |
| `timeout` | int（秒） | 因 agent 而异 | 单次调用硬上限。触发会 fallback 到下一个 agent。 |
| `extra_args` | list[str] | `[]` | 原样追加到 CLI 命令。慎用——容易破坏契约。 |
| `env_passthrough` | list[str] | `[]` | env 变量白名单。仅当 `workspace` 设了才生效。 |

### 各 agent 专属字段

**`claude`**：

| Key | 说明 |
|---|---|
| `max_turns` | 单次调用内的多轮上限。 |
| `allowed_tools` | agent 可用的工具名列表（例如 `[Bash, Read, Write, Edit]`）。 |
| `dangerously_skip_permissions` | true 时跳过逐工具 prompt。仅在配了 `workspace` 时设。 |
| `permission_mode` | 覆盖 Claude 的 permission mode。 |

**`gemini`**：

| Key | 说明 |
|---|---|
| `max_turns` | 多轮上限。 |
| `yolo` | `true` 启用 `--yolo`（自动确认）。仅在配了 `workspace` 时设。 |

**`codex`**：

| Key | 说明 |
|---|---|
| `skip_git_repo_check` | True 时允许在非 trusted git 目录运行。 |
| `sandbox_mode` | `workspace-write` 是推荐默认。 |
| `dangerously_bypass_approvals_and_sandbox` | 不清楚后果就保持 `false`。 |

---

## 跨字段速查

新接手 operator 常被三个 timeout 搞混：

| 字段 | 作用域 | 触发时机 |
|---|---|---|
| `agents.<name>.timeout` | 一次 CLI 调用 | 子进程 wallclock 超过 → fallback 下一个 agent |
| `runtime.default_max_minutes` | 一个 runtime task（整个编排） | task 总 wallclock 超过 → task → FAILED |
| `skills.evaluation.<skill>.timeout`（单 skill，写在 skill yaml/SKILL.md） | 一次 skill 调用 | skill 执行超过 → skill 标记失败、可能被自动停用 |

挑能真正限住你想限的最短那个。把 `agents.<name>.timeout` 设得比 `runtime.default_max_minutes` 还长就形同虚设。
