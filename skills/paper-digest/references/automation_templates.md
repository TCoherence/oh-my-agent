# paper-digest 自动化 YAML 模板

把模板拷贝到 `~/.oh-my-agent/automations/paper-digest.yaml`，填好 `channel_id`，改 `enabled: true` 后 scheduler 会热加载。

## 每日 9 点日报（Claude 版）

```yaml
name: daily-paper-digest
enabled: false
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
delivery: channel
skill_name: paper-digest

prompt: |
  使用 paper-digest skill 生成今日论文雷达日报。
  1. 运行 scripts/paper_fetch.py --source all 取候选。
  2. 运行 scripts/report_store.py context 取最近 7 天上下文；scripts/paper_seen_pool.py context 取过去 14 天 seen-pool。
  3. 运行 scripts/report_store.py scaffold 生成空模板。
  4. 按 references/prompt_recipes.md 的「标准日报」流程撰写：
     - Top picks 上限 8 篇，按 ranking_score 挑选，每篇一句中文速读，保留英文标题。
     - Watchlist 分类命中按 arXiv category 分小节。
     - 延伸阅读基于 Top picks 的 Semantic Scholar 相似论文。
     - 新出现的作者 / 团队需附证据链接。
  5. 若任何源失败，在 coverage_gaps 和 confidence_flags 显式标注。
  6. 运行 scripts/report_store.py persist 落盘并回帖保存路径。

agent: claude
cron: "0 9 * * *"
timeout_seconds: 1500
auto_approve: true
author: scheduler
```

## 每日 7 点日报（Codex 版）

把 `agent: claude` 换成 `agent: codex`，其余保持一致。Codex 的全自主模式更适合长链路 shell 调用，但需要 `codex exec --full-auto --json --skip-git-repo-check` 支持。

## 周末 10 点周刊（v2 预留）

当前 skill v1 只做 daily_digest，但 JSON schema 已预留 `period_start/period_end/sections`，未来 v2 加 `weekly_synthesis` 模式后这里可以直接加：

```yaml
name: weekly-paper-digest
enabled: false
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
skill_name: paper-digest
prompt: "[v2 pending] 使用 paper-digest weekly_synthesis 模式..."
cron: "0 10 * * 0"
author: scheduler
```

## 手动触发

不想等 cron 的时候，直接 Discord：

```
/automation_run name:daily-paper-digest
```

或直接 `/ask` 走 prompt_recipes.md 里的一条食谱。

## 注意事项

- `auto_approve: true` 跳过 runtime 的 DRAFT 风险评估。仅适合只读抓取 + 写 skill-owned reports 目录的自动化；不要在写仓库文件的自动化里设。
- `timeout_seconds: 1500`（25 分钟）用于包住三源抓取 + LLM 整理 + 持久化。S2 慢或 arXiv 限流时有必要。
- 失败重试不内置 —— 由 scheduler 的 job cycle 决定下次再跑。
- 第一次开启前建议先手动跑一次 `scripts/paper_fetch.py` 确认三源联通正常，避免自动化首日全红。
