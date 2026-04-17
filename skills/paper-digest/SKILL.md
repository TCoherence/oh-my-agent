---
name: paper-digest
description: 每日抓取 arXiv + HuggingFace Daily Papers + Semantic Scholar 三源候选，生成中文论文雷达日报（Top picks + 分类命中 + 延伸阅读 + 新作者发现），以 Markdown + JSON 双文件落盘到 ~/.oh-my-agent/reports/paper-digest/。与 market-briefing AI daily 正交：本 skill 锚在论文 PDF / 方法 / 结果层，AI daily 锚在 frontier lab 产品发布与宏观事件层。
metadata:
  timeout_seconds: 1500
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

**8 步，全部串行**：

1. **三源抓取候选**
   ```bash
   ./.venv/bin/python skills/paper-digest/scripts/paper_fetch.py --source all --output json > /tmp/paper_candidates.json
   ```
   - 默认窗口 48h，watchlist 见 `references/paper_watchlist.yaml`
   - 任一源失败不 abort，会在 stderr 打 WARN，CLI 始终 exit 0
   - 输出字段见 `references/report_schema.md`

2. **加载最近上下文**（避免重复推荐）
   ```bash
   ./.venv/bin/python skills/paper-digest/scripts/report_store.py context --days 7
   ```
   返回最近 7 份日报的 summary / top_picks 精简视图。

3. **加载 seen-pool**（过去 14 天已出现过的论文）
   ```bash
   ./.venv/bin/python skills/paper-digest/scripts/paper_seen_pool.py context
   ```
   候选 JSON 里已标注 `seen_before`；此步是额外 context，供 agent 判断「老论文是否应该进延伸阅读而不是 Top picks」。

4. **生成 scaffold**
   ```bash
   ./.venv/bin/python skills/paper-digest/scripts/report_store.py scaffold \
     --report-date 2026-04-16 \
     --markdown-file /tmp/paper-digest.md \
     --json-file /tmp/paper-digest.json
   ```
   scaffold 会生成标准分段的空 Markdown + 全字段占位 JSON。

5. **外部研究 + 整理**
   对 Top picks 候选做二次验证：
   - 打开 arXiv abs 页确认 abstract 与候选 JSON 一致
   - 若 S2 返回了 `similar_papers`，挑 1-2 篇做延伸阅读扩散
   - 对每条 Top picks 写一句中文速读（≤ 30 字，不翻译标题）

6. **填写 Markdown + JSON**
   按 `references/report_schema.md` 的字段契约写。两份文件内容必须一致：JSON 是结构化真相，Markdown 是人类可读视图。

7. **落盘 + 自动 record seen-pool**
   ```bash
   ./.venv/bin/python skills/paper-digest/scripts/report_store.py persist \
     --report-date 2026-04-16 \
     --markdown-file /tmp/paper-digest.md \
     --json-file /tmp/paper-digest.json
   ```
   persist 会：
   - 原子落到 `~/.oh-my-agent/reports/paper-digest/daily/2026-04-16.md|json`
   - 动态 import `paper_seen_pool.py record` 把本期论文写入 seen-pool state（**无需单独调用**）

8. **回帖保存路径** + 直接返回报告正文（Discord 回复要把 Markdown 正文贴出来，别让用户自己去文件里找）。

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
