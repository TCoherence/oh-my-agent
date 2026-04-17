# paper-digest 报告 schema

## Markdown + JSON 双文件落盘

单 domain，无 sub-domain 子路径：

- `~/.oh-my-agent/reports/paper-digest/daily/<YYYY-MM-DD>.md`
- `~/.oh-my-agent/reports/paper-digest/daily/<YYYY-MM-DD>.json`

seen-pool state：`~/.oh-my-agent/reports/paper-digest/state/paper_seen_pool.json`

## Markdown 结构

由 `scripts/report_store.py scaffold` 生成的 skeleton：

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

### 分段写作约定

- **摘要**：不超过 5 条 bullet，每条 ≤ 2 句中文。
- **Top picks**：按 ranking_score 降序；每篇格式 `**[English Title](arxiv_url)**（hf_upvotes / venue / tracked-author 命中原因） → 中文一句速读`。上限见 `paper_watchlist.yaml::limits.top_picks_max`（默认 8）。
- **Watchlist 分类命中**：按 arXiv category 分小节（`### cs.CL` 等），每类上限 `per_category_max`（默认 4）。仅放本次 raw fresh 抓来、关键词命中但未进 Top picks 的论文。
- **延伸阅读**：S2 引用图相似论文（`similar_papers_max` 默认 5），标注它们是从哪篇 Top pick 扩散来的。
- **新出现的作者 / 团队**：过去 48h 首次命中 watchlist 的作者 / 机构，每条需附证据链接。无候选时写 `本日无达标候选`。
- **覆盖缺口与不确定性**：三源抓取异常 / S2 未索引 / HF endpoint 下线 / 关键词未命中任何候选等情况在此显式写出。
- **来源与交叉验证说明**：简短一段，说明本期依赖 arXiv + HF + S2 的权重、是否有单源降级。

## JSON 必需字段（top-level）

```
version                1
mode                   "daily_digest"
domain                 "paper-digest"          # 稳定字面量，用于外部 skill 识别
title                  str                     # 中文标题
generated_at           ISO-8601 UTC
report_timezone        str
report_date            YYYY-MM-DD              # 本地时区下的报告日期
period_start           YYYY-MM-DD
period_end             YYYY-MM-DD
summary                str                     # 一段摘要文字
arxiv_categories       [str]                   # 本次使用的 arXiv 分类快照
top_picks              [PaperEntry]            # 稳定契约，对外可消费
category_hits          {category: [PaperEntry]}
extended_reading       [PaperEntry]
new_authors            [AuthorEntry]
tracked_labs_seen      [LabEntry]
coverage_gaps          [str]
confidence_flags       [str]
source_mix_note        str
verification_note      str
sources                [SourceRef]
sections               [SectionSkeleton]       # 与 market-briefing 同构
```

### `PaperEntry` schema

```
arxiv_id            str
doi                 str                        # 可空
s2_paper_id         str                        # 可空
title               str                        # 英文原题
authors             [str]
affiliations        [str]                      # 可能为空（HF JSON 不附机构）
arxiv_url           str                        # 必填
pdf_url             str
hf_url              str                        # 可空
s2_url              str                        # 可空
published_at        ISO-8601
categories          [str]                      # arXiv 分类
venue               str                        # 仅当 S2 返回
citation_count      int | null
citation_velocity   float | null               # 每周引用数
hf_upvotes          int | null
hf_trending_rank    int | null
ranking_score       float
ranking_reasons     [str]                      # e.g. "hf_trending_rank:2", "watchlist_keyword:moe"
seen_before         bool                       # 是否在过去 14 天内已出现
tldr_en             str                        # 由 agent 或 S2 tldr 填
tldr_cn             str                        # 必填；中文一句速读
reason              str                        # 为什么进本段（agent 写）
evidence_links      [str]                      # 补充证据（博客、代码库）
```

### `AuthorEntry` schema

```
name                str                        # Full name（英文）
aliases             [str]
affiliation         str                        # 最主要机构
arxiv_url           str                        # 代表作链接
reason              str                        # 一句话：为何加入本日发现
evidence_urls       [str]                      # 至少 1 条
cross_checked       bool
group_hint          str                        # "frontier-labs" / "oss-ai-labs" / "robotics-labs" / "systems-labs"
```

### `LabEntry` schema

```
lab_id              str                        # kebab-case
name                str
s2_affiliation_match [str]
aliases             [str]
group               str
```

### `SourceRef` schema

```
type                "arxiv" | "hf_daily" | "semantic_scholar" | "other"
label               str
url                 str
note                str                        # e.g. "primary" / "curated" / "metadata"
```

## 签名稳定性承诺

- `top_picks[]` 里的 `{arxiv_id, title, tldr_cn, arxiv_url}` 是**对外稳定契约 v1**，供 `market-briefing` AI daily 等消费方读取。
- 新增字段必须 additive，不改已有字段语义；要破坏性变更必须 bump `version`。
- `domain` 永远是字面量 `"paper-digest"`，不跟随 skill 改名。

## Density rule

- 每段不得退化为一句 generic filler。
- 无高信号时显式写 `本段今日无高置信度增量信号`，配合 `coverage_gaps` 解释缺失原因。
- 宁愿空也不要凑数。
