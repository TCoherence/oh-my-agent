# 升级指南

按版本的升级流程。每段都遵循同一结构：**改动**、**备份范围**、**步骤**、**验证**。

「current → next」是逐版本累加的：跨版本升级时按顺序跑每一段。

---

## 通用 SOP — 每次升级都要做

```bash
# 1. 记录当前版本
pip show oh-my-agent || git -C /path/to/oh-my-agent rev-parse HEAD

# 2. 停 bot
docker compose down       # Docker
# 或本地 Ctrl-C

# 3. 状态快照
cp -a ~/.oh-my-agent ~/.oh-my-agent.backup-$(date +%Y%m%d)

# 4. 拉新版本
git pull
pip install -e .          # 或重建 Docker 镜像

# 5. 启动前校验配置
oh-my-agent --validate-config

# 6. 启动
docker compose up -d      # 或本地 `oh-my-agent`

# 7. 看前 60 秒日志找 warning
tail -f ~/.oh-my-agent/runtime/logs/service.log
```

出问题时还原备份并降级：

```bash
rm -rf ~/.oh-my-agent
mv ~/.oh-my-agent.backup-YYYYMMDD ~/.oh-my-agent
git checkout <previous-version>
pip install -e .
```

---

## v0.7.x → v0.8.0

**改动**：
- 从 `discord.py` 抽出 service 层——adapter 行为对用户没变化。
- 新增 `--validate-config` CLI flag。
- 仓库根新增 first-class `compose.yaml`。
- 新捆绑 `seattle-metro-housing-watch` skill。

**备份**：通用 SOP 备份足够。无 schema 变化。

**步骤**：
1. 跑通用 SOP。
2.（可选）如果你维护了自己的 `compose.yaml`，跟仓库根新版 diff 后调和。
3.（可选）`oh-my-agent --validate-config`——1.0 之前只 warning，不熟悉的 warning 可以暂时忽略。

**验证**：
- `/doctor` 显示 `Bot online: true`、`Runtime health: enabled: true`。
- 现有 automation 和 skill 行为一致。

---

## v0.8.0 → v0.8.1

**改动**（memory hygiene 一轮）：
- 新增 `MemoryEntry` schema 字段（`scope`、`status`、`evidence`、`last_observed_at` 等）。
- 加载时 lazy 迁移 YAML——旧文件继续可用。
- 两阶段去重、fast/slow promotion、scope 感知 bucket 检索。

**备份**：除通用 SOP 外加上 `~/.oh-my-agent/memory/`。

**步骤**：只跑通用 SOP，无需手动迁移。

**验证**：
- 首次重启后，`/memories` 列出已有条目并补齐新字段（旧行用合理默认值）。
- 下一次 idle judge 触发后，`service.log` 里出现 `memory_extract` / `memory_merge` / `memory_promote` / `memory_inject` 行。

---

## v0.8.1 → v0.8.2

**改动**：
- 新增 `paper-digest` skill。
- 新增 `youtube-podcast-digest` skill。
- automation 支持 `auto_approve` 字段。
- Claude agent 改用 `--output-format stream-json --verbose`。
- 删除 `agents/api/`（自 v0.4.0 已废弃）。仍用 `type: api` 的 config 校验会失败。

**备份**：除通用 SOP 外加上 `~/.oh-my-agent/automations/`。

**步骤**：
1. 在 `config.yaml` 里搜 `type: api`——把任何这样的 agent 块换成等价的 `type: cli` 块（Claude / Codex / Gemini）。
2.（可选）给长跑（≥ 20 分钟）的 automation 加 `auto_approve: true`，避免堆在 DRAFT。仓库自带的 automation 已设好。
3. 跑通用 SOP。

**验证**：
- `oh-my-agent --validate-config` 不再报 `type: api` 错误。
- 长跑 automation 在一个 cron tick 内推进过 DRAFT。

---

## v0.8.2 → v0.9.0（BREAKING — memory 子系统重写）

**改动**：
- 旧的 daily/curated 双层制度、轮后 `MemoryExtractor`、`/promote` slash 命令**全部移除**。
- 新：单层 `JudgeStore` 落在 `~/.oh-my-agent/memory/memories.yaml` + 事件驱动 `Judge` agent。
- 新触发：thread idle（默认 15 分钟）、显式 `/memorize`、自然语言关键词。
- 配置改名：`memory.adaptive` → `memory.judge`。旧 key 仍作 fallback 接受，启动会报 warning。

**备份**：`~/.oh-my-agent/memory/` **必须**备份。迁移脚本会自己写一份备份目录，但额外备一次很便宜。

**步骤**：

1. 停 bot。
2. 备份：
   ```bash
   cp -a ~/.oh-my-agent/memory ~/.oh-my-agent/memory.pre-v0.9
   ```
3. 跑迁移脚本，仓库根目录下：
   ```bash
   # 先 dry-run 看会做什么
   python scripts/migrate_memory_to_judge.py ~/.oh-my-agent/memory --dry-run

   # 然后实跑（仅 curated）
   python scripts/migrate_memory_to_judge.py ~/.oh-my-agent/memory

   # 或者也导入 daily 条目：
   python scripts/migrate_memory_to_judge.py ~/.oh-my-agent/memory --include-daily
   ```
   脚本会在源目录旁边写自己的备份目录。
4. 改 `config.yaml`：
   ```diff
    memory:
      backend: sqlite
      path: ~/.oh-my-agent/runtime/memory.db
      max_turns: 20
      summary_max_chars: 500
   -  adaptive:
   +  judge:
        enabled: true
        memory_dir: ~/.oh-my-agent/memory
   -    promotion_threshold: 0.7
   +    inject_limit: 12
   +    idle_seconds: 900
   +    idle_poll_seconds: 60
   +    synthesize_after_seconds: 21600
   +    max_evidence_per_entry: 8
   +    keyword_patterns:
   +      - 记一下
   +      - remember this
   ```
   （留 `adaptive:` 仍能跑，但启动会有 deprecation warning——一次性改干净。）
5. 把运维笔记里所有 `/promote` 引用删掉——命令已不存在。
6. 启动 bot。

**验证**：
- `~/.oh-my-agent/memory/memories.yaml` 存在且含已迁移条目。
- `~/.oh-my-agent/memory/MEMORY.md` 在 ~10 分钟内重建（thread 进入 idle 时更早）。
- `/memories` 列出 active 条目。
- `/memorize "test pin"` 写入新条目，`/memories` 看得到。
- `service.log` 里没有 `MemoryExtractor` 行——只有 `memory_extract`（Judge）行，且只在触发时出现。

**如果跳过迁移**：bot 能干净启动，但 memory store 是空的。你旧的 `daily/`、`curated.yaml`、`/promote` 历史从新代码路径不可达，必须跑脚本才回来。

---

## v0.9.0 → v0.9.x（Phase A）— restart/recovery + warnings

**改动**：
- 新增 `tests/test_restart_recovery.py` 和 `tests/test_upgrade_paths.py`（仅开发者层面）。
- 启动现在显式 emit deprecation warning，当检测到：
  - 配置里有 `memory.adaptive`（应改成 `memory.judge`）。
  - `memory_dir` 里有遗留的 `daily/` 或 `curated.yaml`（跑 v0.9.0 迁移脚本）。

**备份**：通用 SOP。

**步骤**：只跑通用 SOP。

**验证**：
- 如果 v0.9.0 时迁移干净，没有新 warning。
- 如果出现 warning，会点名具体文件路径或 config key——按提示修。

---

## v0.9.x → v1.0（BREAKING — Slack 移除）

**改动**：
- 1.0 **不支持** Slack。之前的 Slack adapter 是 stub，从未达到 parity。
- `config_validator` 现在拒绝 `platform: slack` 并报错指向本指南。
- 删除 `src/oh_my_agent/gateway/platforms/slack.py`。
- 这是 1.0 契约冻结的一部分：1.0 = Discord-only、单用户、自托管。post-1.0 可能以真实现的形式重回 Slack。

**备份**：通用 SOP。

**步骤**：
1. 如果 `config.yaml` 里有 `gateway.channels[]` 含 `platform: slack`，**删掉这条**。否则 validator 会拒绝，bot 起不来。
2.（可选）顺手把 `config.yaml` 里被注释的 `# - platform: slack` 示例段也删了。
3. 跑通用 SOP。

**验证**：
- `oh-my-agent --validate-config` 返回 0。
- `/doctor` 行为如常。

**如果你依赖 Slack stub**：实际上它从来没工作过，你也没收到过消息。这是契约改动而非行为改动。post-1.0 真 Slack 计划请提 issue 描述用例。

---

## 未来版本

本指南随每次发版更新。新版本发布时在顶部加新段——已有段不会改。
