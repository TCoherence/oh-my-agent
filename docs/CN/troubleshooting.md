# 故障排查

具体的故障模式和定位方法。每条都遵循相同结构：**症状 → 诊断命令 → 解决方案**。

如果以下条目都对不上，请把对应的 `~/.oh-my-agent/runtime/logs/service.log` 片段和 `/doctor` 输出贴进 issue。

---

## 1. Discord 上 bot 无回应

**症状**：在配置的频道里发消息没有回复，没有 typing 提示，日志里也没有该消息的痕迹。

**诊断**：

```bash
# 进程还在吗？
docker compose ps                # Docker
ps aux | grep oh-my-agent        # 本地

# bot 认为自己听的是哪个频道？
grep -E 'channel_id|owner_user_ids' config.yaml

# Discord 是否真的把消息送到了？
tail -n 200 ~/.oh-my-agent/runtime/logs/service.log | grep -i 'on_message\|received'
```

**解决**：

- 进程已死：重启，看最近 50 行日志找 crash 堆栈。
- `config.yaml` 里的 channel id 与你发消息的频道不一致：bot 默认忽略频道外的消息。
- 配了 `access.owner_user_ids` 但你的 Discord 用户 id 不在列表里：访问门会静默丢消息（设计如此）。把 id 加进去或去掉门。
- gateway intent 缺失：去 Discord 开发者后台开启 **Message Content Intent**，重新邀请 bot。

---

## 2. Task 卡在 DRAFT

**症状**：`/task_start`（或自动化）创建了 task，但一直停在 `DRAFT`。

**诊断**：

```bash
# task 自己是什么状态？
sqlite3 ~/.oh-my-agent/runtime/runtime.db \
  "SELECT id, type, state, risk_level, risk_reason FROM tasks ORDER BY created_at DESC LIMIT 5;"

# 谁该来批准？
grep -E 'risk|draft' ~/.oh-my-agent/runtime/logs/service.log | tail -n 40
```

**解决**：

- DRAFT 表示风险评估器认为该任务需人工确认。用 `/task_approve <id>` 放行，或 `/task_reject <id>` 丢弃。
- 自动化触发的、本应跳过风险的任务：在 `~/.oh-my-agent/automations/<name>.yaml` 里设 `auto_approve: true`。
- 想让某类任务整体跳过 DRAFT：可以调低 `runtime.risk_evaluation.*` 阈值——但这等于关掉了安全门，更推荐 per-automation 的 `auto_approve`。

---

## 3. Task 卡在 RUNNING（或 VALIDATING）一直不动

**症状**：`/task_status <id>` 显示 task 处于 `RUNNING` 已远超 `default_max_minutes`。日志里也没新消息。

**诊断**：

```bash
# 每个 task 都有心跳日志，按 task id 搜：
grep '<task_id>' ~/.oh-my-agent/runtime/logs/service.log | tail -n 30

# agent 子进程还在吗？
ps aux | grep -E 'claude|gemini|codex'
```

**解决**：

- 在 thread 里发 `/task_stop <id>`——心跳循环会在下一 tick（≤ 5 s）取消 agent 子进程。
- agent 进程已死但 task 没转移：查 `service.log` 找 `agent fallback` 或未捕获异常；多半需要 `/task_resume <id>` 或 `/task_discard <id>`。
- 同一个 skill 反复卡：检查 `skills.evaluation.<name>.timeout`——超时设得太紧会让 `VALIDATING` 永远不收敛。

---

## 4. Agent fallback 全员失败

**症状**：每次 `/ask` 都报 `AgentRegistry: all agents exhausted`，或永远是次级 fallback agent 在回复。

**诊断**：

```bash
# 配置里的 agent 顺序？
grep -A1 'agents:' config.yaml

# CLI 二进制在 bot 的 PATH 里吗？
docker compose exec oh-my-agent which claude codex gemini   # Docker
.venv/bin/python -c "import shutil; print(shutil.which('claude'))"  # 本地

# 失败的 agent 报了什么？
grep -E 'agent_run|agent fallback|SubprocessError' ~/.oh-my-agent/runtime/logs/service.log | tail -n 40
```

**解决**：

- 二进制缺失：把 CLI 装到 bot 同一环境里（或 mount 进容器）。
- 鉴权缺失：跑 `/auth_status`——任一 agent 报 `unauthorized` 就用 `/auth_login <agent>` 走流程。
- 每个 agent 的 `env_passthrough` 没把 API key 透传出去：确认环境变量在 `agents.<name>.env_passthrough` 白名单里。配了 `workspace` 之后环境会被清洗。
- 持续超时：调高 `agents.<name>.timeout`（秒），默认值偏保守。

---

## 5. 自动化从来不触发

**症状**：`~/.oh-my-agent/automations/` 下的 YAML 在，但调度从不触发。

**诊断**：

```bash
# 调度器看到了什么？
grep -E 'scheduler|automation' ~/.oh-my-agent/runtime/logs/service.log | tail -n 40

# Discord 里看所有 automation 状态：
/automation_status
```

**解决**：

- 文件没被加载：`oh-my-agent --validate-config` 检查 YAML 语法错误。热重载只挑语法合法的文件。
- YAML 里 `enabled: false`——改成 `true` 或用 `/automation_enable <name>`。
- cron 表达式错：丢到 cron 检查器里验证；调度器用标准 5 字段 cron（无秒字段）。
- 触发了但 task 被风险评估拦下：见模式 2（DRAFT）。如属意料之中，加 `auto_approve: true`。
- 用 `/automation_run <name>` 手动触发一次，分清是调度问题还是执行问题。

---

## 6. Memory 从不被注入到 prompt

**症状**：`/memories` 列出有 active 条目，但 agent 的 prompt 里从没出现 `[Remembered context]`。

**诊断**：

```bash
# Judge 真的跑了吗？看 memory_extract trace 行。
grep -E 'memory_extract|memory_inject' ~/.oh-my-agent/runtime/logs/service.log | tail -n 40

# bot 读的 yaml 跟 /memories 显示的是同一个吗？
ls -la ~/.oh-my-agent/memory/memories.yaml
```

**解决**：

- Judge 只在三种触发下跑：显式 `/memorize`、命中 `memory.judge.keyword_patterns` 配置的关键词、或者沉默 `idle_seconds`（默认 900）之后。**不会**每轮都跑。
- 注入是 scope 感知的。`thread` 域的记忆不会出现在另一条 thread 里；`workspace` 域的只在当前 workspace 匹配时注入。
- `superseded` 条目永远不注入。用 `/memories` 确认是 `status=active`。
- `memory.judge.inject_limit`（默认 8）控制 prompt 块里最多塞几条；如果你的库很大且确实相关，可以提高。

---

## 7. HITL prompt 发出来了，按钮点击没反应

**症状**：`WAITING_USER_INPUT` checkpoint 弹了带按钮的视图，点了之后日志没动静、状态也没转移。

**诊断**：

```bash
# bot 还活着吗？Discord 按钮视图重启后会失效。
docker compose ps

# 重启之后 rehydration 跑了吗？
grep '_rehydrate_hitl_prompt_views' ~/.oh-my-agent/runtime/logs/service.log | tail -n 5
```

**解决**：

- 重启之后按钮回调要重新注册。这一步由 `_rehydrate_hitl_prompt_views()` 自动完成。如果日志里没有这行，说明 rehydrator 没跑——多半因为 channel registry 还没就位。再重启一次。
- rehydrator 跑了但按钮还是没反应：可能撞到 Discord interaction-token 过期（15 分钟）。改用对应的 slash 命令（`/task_resume <id> <answer>`）。

---

## 8. 合并门把 repo_change 任务卡死

**症状**：一个 `repo_change` 任务到了 `WAITING_MERGE` 就不再前进，`/task_merge <id>` 报错。

**诊断**：

```bash
# 看 worktree：
ls ~/.oh-my-agent/runtime/tasks/<task_id>/

# 测试/校验那一步真的过了吗？
grep '<task_id>' ~/.oh-my-agent/runtime/logs/service.log | grep -iE 'test|valid'
```

**解决**：

- 测试失败：进 worktree 手工复现，要么在 worktree 里修好再 `/task_merge`，要么 `/task_discard <id>` 重起任务。
- 父仓库工作树有未提交改动：合并步骤拒绝覆盖。先 stash 或 commit 你的本地修改。
- 一次性绕开：`/task_merge <id>` 只在 `runtime.merge.allow_force` 配了 true 时支持强制合并。默认关——保持关掉是对的。

---

## 9. Skill 被自动停用——之前能跑现在不动了

**症状**：原本健康的 skill 现在被静默跳过。`/skill_stats <name>` 显示 `auto_disabled: true`。

**诊断**：

```bash
# 最近几次执行的健康度：
/skill_stats <name>

# 自动停用的判定依据：
sqlite3 ~/.oh-my-agent/runtime/memory.db \
  "SELECT skill_name, auto_disabled_reason FROM skill_provenance WHERE skill_name = '<name>';"
```

**解决**：

- 用 `/skill_enable <name>` 重新启用，立即清掉 auto-disable flag。
- 如果 skill 确实因实际原因（脚本损坏、环境不对、第三方 API 挂了）在失败，会再次触发阈值。读 `skill_invocations` 里最近的执行看真实错误。
- 调整灵敏度：阈值在 `config.yaml` 的 `skills.evaluation.auto_disable.*`。默认比例故意设得严，只在误报频繁时才放宽。

---

## 10. 限流饱和——bot 丢消息

**症状**：消息突发时只有前几条得到回复，后面的要么提示限流、要么静默被丢。

**诊断**：

```bash
grep -E 'rate.?limit|throttle' ~/.oh-my-agent/runtime/logs/service.log | tail -n 40
```

**解决**：

- 这是设计行为。限流器保护上游 API 免受成本/配额爆炸。
- 真正的批量负载请用 `/task_start`（一个自治任务）而不是连续几十轮 `/ask`。
- gateway 层的限流配置在 `gateway.rate_limit`（或平台子键）。提高时谨慎，盯着 agent 端成本。

---

## 11. 启动时 config 校验失败

**症状**：`oh-my-agent` 立刻退出，打印 `Config validation failed:` 和一串错误。

**诊断**：

```bash
# 校验器单独跑——会输出结构化错误：
oh-my-agent --validate-config
oh-my-agent --config /path/to/config.yaml --validate-config

# 跟示例对照：
diff config.yaml.example config.yaml | head -n 60
```

**解决**：

- 每条错误都点名出错的 key 路径。修对应的 key，再跑一次。
- 常见情况：未知 agent type、缺 `cli_path`、不支持的平台（Slack 在 1.0 不再支持，见 [upgrade-guide.md](upgrade-guide.md)）、`${ENV_VAR}` 引用了但环境变量没设置。
- Warning（打印但不阻塞启动）包含废弃的 config 别名，例如 `memory.adaptive`（已改名为 `memory.judge`）。

---

## 12. CLI session 不 resume——每轮都从零开始

**症状**：回复完全没有上下文；agent 不记得同一 thread 里上一轮说了什么。

**诊断**：

```bash
# session 行真的持久化了吗？
sqlite3 ~/.oh-my-agent/runtime/memory.db \
  "SELECT platform, channel_id, thread_id, agent, session_id FROM agent_sessions ORDER BY updated_at DESC LIMIT 10;"

grep -E 'session resume|--resume' ~/.oh-my-agent/runtime/logs/service.log | tail -n 20
```

**解决**：

- 目前只有 Claude 支持 session resume。Codex 和 Gemini 是单轮无状态——靠 prompt 里携带历史。
- Claude 是当前 agent 但 `agent_sessions` 为空：上一轮多半失败或返回了非 success（只在 success 时写行）。看上一轮的日志找 agent outcome。
- `/reset` 之后该行会被删，下一轮起新 CLI session。这是预期行为。

---

## 13. 图片附件被忽略

**症状**：发问题时带了图片，agent 回复了但完全没提图片。

**诊断**：

```bash
grep -E 'attachment|image' ~/.oh-my-agent/runtime/logs/service.log | tail -n 30
```

**解决**：

- 只有 `image/*` MIME、≤ 10 MB 的附件会被转发。其他类型静默丢。
- Codex 原生支持 `--image`。Claude 和 Gemini 拿到的是 `workspace/_attachments/` 下的副本 + prompt 里的指引——没配 `workspace` 时复制步骤被跳过、图片引用可能解析不了。配置 `workspace`（见 [config-reference.md](config-reference.md)）。
- 纯图片消息会被打上默认分析 prompt；想问具体问题就同条消息附文字。

---

## 14. Skill 改了之后不生效

**症状**：编辑了 `SKILL.md` 或脚本，agent 仍在用旧版本。

**诊断**：

```bash
# 软链是否最新？
ls -la .claude/skills/<name>
ls -la .gemini/skills/<name>

# bot 上次同步是什么时候？
grep -E 'skill_sync|full_sync' ~/.oh-my-agent/runtime/logs/service.log | tail -n 20
```

**解决**：

- 在 Discord 里跑 `/reload-skills`，会触发 `full_sync()` 并重新校验每个 skill。
- 校验失败会阻断 reload——slash 命令会报哪个 skill 失败、为什么。
- 配了 `workspace` 时 skill 是**复制**进 workspace 的（不是软链）。`/reload-skills` 会重新复制；不调它的话 `skills/` 的修改不会同步进 workspace。

---

## 15. `/doctor` 红了

**症状**：`/doctor` 把某个 section 标红。

**解决**：每个红色 section 对应一类问题。逐节释义见 [monitoring.md](monitoring.md)。

---

## 16. Runtime task `max_turns` 失败

**症状**：一个 runtime task（`/task_start` 或 automation 触发的）进 `FAILED`，错误信息含 "max_turns" 或 "reached maximum number of turns"。Automation 来的任务，线程里只有半截结果然后就没了。

**排查**：

```bash
# 哪个 task、哪个 agent hit 的？
grep 'hit max turns' ~/.oh-my-agent/runtime/logs/service.log | tail -n 10

# 确认 task 的预算（不是 skill 配置，是 task 行里的）：
sqlite3 ~/.oh-my-agent/runtime/memory.db \
  "SELECT id, automation_name, skill_name, agent_max_turns, status, error FROM runtime_tasks WHERE status='FAILED' ORDER BY ended_at DESC LIMIT 5;"

# skill 自己声明的是多少？
grep -E 'max_turns|timeout_seconds' skills/<name>/SKILL.md
```

**解决**：

- **一次性救场**：失败线程里应该出现一个 "Re-run +30 turns" 按钮（primary 样式）。点下去会创建一个 sibling task，`agent_max_turns = parent + 30`（parent 没设就以 25 为基准）。按钮的 TTL 是 `runtime.decision_ttl_minutes`，默认 24 小时。没看到按钮？先确认 `owner_user_ids` 配了，并且失败后日志里有 `_surface_rerun_bump_turns_button` 一行；都没有的话你大概率是在 chat path（`/ask` 或裸 slash skill）里 hit 的 —— 那条路径没有 runtime task，也没有按钮。改用 `/task_start` 或 automation 重发就能拿到按钮。
- **长期修复**：把 `skills/<name>/SKILL.md` 里的 `metadata.max_turns` 调上去（多源 digest 类通常 40–60）。只设 `timeout_seconds` 是不够的 —— claude 的 `--max-turns` 是独立开关。改完跑一次 `/reload-skills`。
- **不是 skill task**：`/task_start` 直建的任务会继承 claude 默认的 25 turns。要长期提高天花板，要么做一个专门的 skill 写高一点，要么接受当前这次走按钮 bump。
- **不要指望 retry 能解决**：`max_turns` 被归为 terminal（retry 也只是再烧一次一样的预算）。完整的 retry vs terminal 分类见 [task-model.md §7](task-model.md)。

---

## 何时升级到 issue

以上模式都不匹配时，把以下材料贴进 GitHub issue：

1. `~/.oh-my-agent/runtime/logs/service.log` 最近 200 行
2. `/doctor` 输出
3. 脱敏的 `config.yaml`（去掉 token）
4. 版本（`pip show oh-my-agent` 或 checkout 的 `git rev-parse HEAD`）
