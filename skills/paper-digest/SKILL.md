---
name: paper-digest
description: 每日抓取 arXiv + HuggingFace Daily Papers + Semantic Scholar 三源候选，生成中文论文雷达日报（Top picks + 分类命中 + 延伸阅读 + 新作者发现），以 Markdown + JSON 双文件落盘到 ~/.oh-my-agent/reports/paper-digest/。与 market-briefing AI daily 正交：本 skill 锚在论文 PDF / 方法 / 结果层，AI daily 锚在 frontier lab 产品发布与宏观事件层。
metadata:
  timeout_seconds: 1500
  max_turns: 60
---

# Paper Digest

每日论文雷达 skill，面向工程师的**技术层阅读 feed**。

## 与 market-briefing AI daily 的边界

| | paper-digest | market-briefing AI daily |
| --- | --- | --- |
| 锚点 | arXiv PDF / 方法 / benchmark / 结果 | frontier lab 产品发布、社区信号、宏观 AI 事件 |
| 候选源 | arXiv + HF Daily + Semantic Scholar | 官方公告 + 媒体 + X/播客 |
| 产出节奏 | 每日 1 篇 JSON + Markdown | 每日 1 篇 JSON + Markdown |
| 交集策略 | 同一篇 DeepSeek V4 论文可能两边都出现：paper-digest 写「方法和基准」，AI daily 写「产品影响」，**通过 JSON contract 让 AI daily 可引用 paper-digest 的 `top_picks[0:3]`，避免重复策划** |

**不要**把 market-briefing AI daily 的 Frontier Labs Radar 改写成论文清单。本 skill 存在的意义就是把论文层 carve out 成独立视角。

## When to use

- `daily_digest`（v1，唯一支持的 mode）——用户请求今日 / 某日论文雷达
- `weekly_synthesis`（v2 预留，当前不实现）——JSON schema 已留 `period_start/period_end/sections` 字段

不要把 `market-briefing` 的 `bootstrap_backfill` / `weekly_synthesis` 概念混进来。paper-digest 只做 daily，周末需要回顾时用 `references/prompt_recipes.md` 里的「回看整理」人工 prompt。

## Required workflow

**5 步，目标 ≤ 15 个 agent turns**：

### 步骤 1：一次 shell 串联准备工作

用**单个 Bash 调用**跑完抓取 + 两份 context + scaffold（节省 turns）：

```bash
DATE=$(date +%F) && \
./.venv/bin/python ${OMA_AGENT_HOME}/skills/paper-digest/scripts/paper_fetch.py --source all --output json > /tmp/paper_candidates.json 2>/tmp/paper_fetch.err && \
./.venv/bin/python ${OMA_AGENT_HOME}/skills/paper-digest/scripts/report_store.py context --days 7 > /tmp/paper_context.json && \
./.venv/bin/python ${OMA_AGENT_HOME}/skills/paper-digest/scripts/paper_seen_pool.py context > /tmp/paper_seen.json && \
./.venv/bin/python ${OMA_AGENT_HOME}/skills/paper-digest/scripts/report_store.py scaffold --report-date "$DATE" --markdown-file /tmp/paper-digest.md --json-file /tmp/paper-digest.json && \
echo "prep done; date=$DATE; candidates_bytes=$(wc -c < /tmp/paper_candidates.json)"
```

任一源失败，`paper_fetch.py` 仍 exit 0，但会在 stderr 打 WARN——看 `/tmp/paper_fetch.err` 判断是否需要在 `coverage_gaps` 写 `arxiv_unavailable` / `hf_daily_unavailable` / `s2_unavailable`。

### 步骤 2：Read 三份 JSON

- Read `/tmp/paper_candidates.json` — 顶层是**数组**（按 `ranking_score` 降序），每个元素是**真相来源**，包含 `title / authors / affiliations / abstract / arxiv_url / hf_url / s2_url / categories / venue / hf_upvotes / s2_tldr / ranking_score / ranking_reasons / seen_before`
- Read `/tmp/paper_context.json` — 最近 7 天报告精简视图，判断是否需要排除已推荐过的
- Read `/tmp/paper_seen.json` — seen-pool 视图，候选里 `seen_before=true` 的降级到延伸阅读

若候选数组为空（三源全挂或 watchlist 不命中），跳到步骤 3 直接写一份空报告：`summary` 解释原因，`top_picks: []`，`coverage_gaps` 写具体原因，照常 persist。

### 步骤 3：直接写 Markdown + JSON（**禁止 per-paper WebFetch**）

**硬性约束**：
- **不要** WebFetch、curl、或 `Bash` 去访问 arxiv / hf / semanticscholar 页面。候选 JSON 里的 `abstract` 和 `s2_tldr` 已经足以写 `tldr_cn`。
- **不要** 去搜作者主页 / 代码仓补 `evidence_urls`——用候选 JSON 里的 `arxiv_url` / `hf_url` / `s2_url` 即可。
- **不要** 再次跑 `paper_fetch.py`——候选 JSON 已是本次 ground truth。
- **不要**对 Top picks 按其他维度重排——`ranking_score` 已排好，直接取前 `top_picks_max` 条。

允许的补充 Bash 只有：快速计算（`wc -l`、`jq` 过滤）、`ls`、读本地文件。

每条 Top picks 写法：
- `tldr_cn`：一句中文（≤ 30 字），从 `s2_tldr`（若非空）或 `abstract` 第一句浓缩翻译。
- `reason`：一句中文说明为何入选，引用 `ranking_reasons`（如 `"hf_trending_rank:2 + watchlist_keyword:moe"`）。
- `evidence_links`：直接用 `arxiv_url`。若 `hf_url` / `s2_url` 非空且提供增量信息，追加；否则留空数组。
- `tldr_en` 从 `s2_tldr` 拷贝（若有），没有就留空字符串——**不许自行翻译/创作**。

延伸阅读：
- 候选 JSON 里**可能没**预取 `similar_papers` 字段——**不要**为此单独跑 Bash 查 S2。
- 若当天某 Top pick 的 S2 条目有 `similar_papers`，挑 1-2 篇放进 `extended_reading`。
- 若完全没有，写 `coverage_gaps: ["s2_similar_unavailable"]`，`extended_reading: []`，段落正文写 `本段今日无高置信度增量信号（S2 相似论文未返回）`。

新作者发现（`new_authors`）：
- 只扫候选 JSON 里的 `authors` 字段。命中规则见 `references/discovery_rules.md`。
- 无候选时写 `new_authors: []` 并在正文写 `本日发现扫描未发现达标候选人`。
- **不**为了凑数去外部搜索。

Write 两份文件：`/tmp/paper-digest.md` 和 `/tmp/paper-digest.json`。JSON 字段见 `references/report_schema.md`。两份必须内容一致。

### 步骤 4：落盘 + 自动 record seen-pool

```bash
./.venv/bin/python ${OMA_AGENT_HOME}/skills/paper-digest/scripts/report_store.py persist \
  --report-date "$DATE" \
  --markdown-file /tmp/paper-digest.md \
  --json-file /tmp/paper-digest.json
```

persist 会：
- 原子落到 `~/.oh-my-agent/reports/paper-digest/daily/<DATE>.md|json`
- 动态 import `paper_seen_pool.py record`（**无需单独调用**）

### 步骤 5：在 chat 中给出结构化摘要 + 存储路径

完整 digest 已经落盘；**不要再把整篇 Markdown verbatim 贴到 chat**。把 5–30 KB 的报告再以 output token 重生一遍，会在 run 末尾吃掉大量 wall-clock 预算（系统级修复跟踪在 runtime backlog 的 "Long-output final delivery" 条目里）。这一步要回的是结构化摘要——让用户**不打开 file 也能拿到核心结论**。

**chat reply 必含内容：**

1. **一句话结论（1-2 句）**：今日论文层的主线判断 + 最强的 1 个观察点（如某主题命中多篇、某 lab 当日多产、某 benchmark 结果反转等）。
2. **Top picks（3 条，复制自 JSON `top_picks[0:3]`）**：每条一行，格式 `- [<arxiv_id>](<arxiv_url>) <title> — <tldr_cn>`。这是用户来这次想看的实质内容，必须在 chat 里。
3. **🏷 Watchlist 命中 / 🧑‍🔬 新作者 简报（可选）**：如果当日有 watchlist 主题命中或新作者发现，1-2 句概述（不展开列表，详情在文件里）。
4. **📉 Coverage gaps（如非空）**：列 `coverage_gaps[]` 里的 slug（`arxiv_unavailable` / `hf_daily_unavailable` / `s2_unavailable` 等）+ 1 句解释。如果都没有 gap 就跳过。
5. **存储路径**：`Saved: ~/.oh-my-agent/reports/paper-digest/daily/<DATE>.md`。

格式：

```
<一句话结论>

**Top picks**

- [<arxiv_id>](<arxiv_url>) <title> — <tldr_cn>
- [<arxiv_id>](<arxiv_url>) <title> — <tldr_cn>
- [<arxiv_id>](<arxiv_url>) <title> — <tldr_cn>

**Watchlist 命中 / 新作者**（可选）

- <主题或团队 + 1 句话>

**Coverage gaps**（如非空）

- <slug>: <1 句话>

Saved: ~/.oh-my-agent/reports/paper-digest/daily/<DATE>.md
```

❌ 不要用 "Done."、"Report saved."、一句话总结收尾 —— 那是状态注释，不是 answer。
❌ 不要只回 storage path —— 用户在 Discord 打不开文件，需要看到摘要。
❌ 不要把整篇 Markdown 正文 verbatim 贴 chat —— 浪费 output token + wall-clock，文件已经是 canonical artifact。
❌ 不要把一句话结论写成 "今日论文层无明显信号" 这种空话 —— 哪怕日子薄，也要给 1 个具体观察点（或明确说 "三源都返回空" 并指出原因）。
✅ 摘要本身要让一个不打开文件的读者也能拿到核心结论；文件用于 deep dive。

### Turn budget 目标

- 步骤 1：1 turn（一个 Bash）
- 步骤 2：3 turn（三次 Read）
- 步骤 3：2 turn（两次 Write）
- 步骤 4：1 turn（一个 Bash）
- 步骤 5：1 turn（final text）
- **合计 ~8 turns**，`max_turns: 60` 给足安全余量。如果超过 20 turn 说明违反了「步骤 3 硬性约束」——立刻停止额外 fetch，用手头数据完成报告。

## Storage layout

**单 domain**，不使用 domain 子路径（这是与 market-briefing 的关键差异）：

```
~/.oh-my-agent/reports/paper-digest/
├── daily/
│   ├── 2026-04-16.md       # 人类可读 Markdown
│   └── 2026-04-16.json     # 结构化契约数据
└── state/
    └── paper_seen_pool.json  # 14 天滚动 seen-pool
```

- `report_date` 的日期解析使用 `OMA_REPORT_TIMEZONE` 或 `TZ`，默认为系统时区。
- 绝不 hand-roll 路径；统一走 `report_store.py` 的 `build_report_paths()`。

## Report structure

Markdown 7 段（由 scaffold 生成骨架）：

```
# 论文雷达日报｜<YYYY-MM-DD>

一句话结论：

## 摘要
## 📌 Top picks (交叉命中)
## 🏷 Watchlist 分类命中
## 🔗 延伸阅读 (Semantic Scholar 相似论文)
## 🧑‍🔬 新出现的作者 / 团队
## 📉 覆盖缺口与不确定性
## 来源与交叉验证说明
```

JSON 顶层必需字段、`PaperEntry` / `AuthorEntry` / `LabEntry` / `SourceRef` 子 schema 全部见 `references/report_schema.md`。

**稳定契约**：`top_picks[].{arxiv_id, title, tldr_cn, arxiv_url}` 是对外 v1 冻结字段（见 `references/integration_contract.md`）。新增字段允许，语义修改必须 bump `version`。

## Source policy

严格区分四类来源（`sources[].note` 必填）：

- `primary` — arXiv 预印本
- `curated` — HF Daily
- `metadata` — Semantic Scholar
- `other` — 博客 / 代码库 / 演示页

**结论必须锚在 primary**。不把 HF trending 当论文结果证据；`citation_count == 0` 不是降权理由（新预印本 S2 往往还没索引）。

冲突优先级：`primary > metadata > curated > other`。

详见 `references/source_policy.md`。

## Watchlist 与发现

- **配置**：`references/paper_watchlist.yaml`（arXiv categories + keywords + tracked_authors + tracked_affiliations + venues_must_read + ranking_weights + limits）
- **机构种子**：`references/paper_groups_seed.yaml`（frontier-labs / oss-ai-labs / robotics-labs / systems-labs 四组）
- **发现规则**：`references/discovery_rules.md`——每日尝试 1-3 位新作者 / 新团队，无候选时写 `本日发现扫描未发现达标候选`，不要为了凑数硬塞
- **机构自动沉淀**：`paper_seen_pool.py sync-seed --min-observations 2` 可把 runtime 观察到的机构回写到 seed YAML；作者不做自动沉淀（需要人工 review）

## Seen-pool workflow

- 14 天滚动窗口，目的是防止 HF Daily trending 的老论文天天上榜
- key 优先级：`arxiv:<id> > doi:<doi> > s2:<id> > title:<normalized>`
- `persist` 会自动 record，无需单独跑 `paper_seen_pool.py record`
- record 时顺带 prune（默认 14 天），state 稳态大小 < 1MB
- 候选 JSON 里的 `seen_before=true` 意味着这篇过去 14 天进过别的日报——**应该归入延伸阅读或忽略，而不是 Top picks**

## 与 market-briefing AI daily 的集成

paper-digest 发布**只读 JSON 契约 v1**，不反向依赖 market-briefing：

- 稳定路径：`~/.oh-my-agent/reports/paper-digest/daily/<YYYY-MM-DD>.json`
- 稳定字段：`top_picks[].{arxiv_id, title, tldr_cn, arxiv_url}`
- 消费方（market-briefing AI daily）可读该文件，在「关键人物与社区信号」前插一个「今日论文雷达」子段
- 文件缺失 / 过期 / 解析失败——消费方写 `今日论文雷达暂无更新`，不报错
- **paper-digest 绝不修改 market-briefing 的报告目录**，反之亦然

完整契约见 `references/integration_contract.md`。本 skill **不**负责改 market-briefing 的 prompt；契约发布后由 AI daily 侧按自己的节奏引用。

## Density rule

- 每段不得退化为一句 generic filler
- 无高信号时显式写 `本段今日无高置信度增量信号`，配合 `coverage_gaps[]` 解释
- 三源中任何一个失败——在 `coverage_gaps` 写对应 slug（`arxiv_unavailable` / `hf_daily_unavailable` / `s2_unavailable`），在 `confidence_flags` 写降级原因
- 宁愿空也不要凑数

## Language rule

- 正文中文为主
- **论文标题保留英文原名**（arXiv 是什么写什么）
- 作者名保留英文 full name
- 机构名英文原名（e.g. `Google DeepMind`，不要翻译成「谷歌深智」）
- 每条 Top picks 必须附中文一句速读（`tldr_cn`）
- `tldr_en` 可选（S2 返回时直接用），不是必填

## 自动化入口

把 `references/automation_templates.md` 里的 YAML 拷到 `~/.oh-my-agent/automations/paper-digest.yaml`，填 channel_id 并改 `enabled: true`。默认模板：

- `cron: "0 9 * * *"`——每日北京时间 9:00 执行
- `agent: claude`——也可切 codex
- `auto_approve: true`——跳过 runtime DRAFT（仅限只读抓取 + 写 skill-owned reports）
- `timeout_seconds: 1500`——25 分钟上限，够三源抓取 + LLM 整理 + 持久化

用户也可以在 Discord 手动触发：`/automation_run name:daily-paper-digest`，或直接 `/ask use paper-digest skill 生成今日论文日报`。

## Prompt 食谱

见 `references/prompt_recipes.md`，包含四种复用模板：

1. **标准日报**——最常用，对应 automation prompt
2. **主题深挖**——用户说「今天只关心 long context」之类收窄需求
3. **单源排障**——HF 或 S2 挂了时的降级版本
4. **回看整理**——周末手动周刊（v2 自动化前的 workaround）
