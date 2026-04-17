# paper-digest 来源策略

## 四类来源

每条论文必须能归入以下其中一类，并在 `sources[].note` 字段显式标注：

- `primary` — arXiv 预印本（PDF / abs 页）：一手文本来源，所有具体结果引述应基于此
- `curated` — HuggingFace Daily Papers：社区精选信号，用于判断「今天大家在看什么」
- `metadata` — Semantic Scholar（venue / citations / tldr / similar）：元数据与引用图
- `other` — 作者博客、代码库 README、官方公告等补充证据

## 偏向与权重

- **主要结论必须基于 primary 源**。不要把 HF 的 trending 直接当作「论文结果」的证据，它只表明「社区目前关注这篇」。
- `citation_count` / `citation_velocity` 来自 metadata 源，刚发出的预印本往往为 0 或 null，不要以缺少引用为理由把新论文排低。
- 有冲突时：`primary > metadata > curated > other`。

## 交叉验证

每个 Top pick 应尽量具备：

1. arXiv abs 链接（primary）
2. HF Daily 或 S2 的补充信息（curated 或 metadata）
3. 至少一条附加证据（作者博客 / 代码仓 / 演示页 / 二次报道）时，标记 `cross_checked: true`

无法交叉验证的新发现（例如仅有 arXiv 原文、无其他社区信号），写进 `coverage_gaps` 或 `confidence_flags`，例如 `confidence_flags: ["single_source_primary_only"]`。

## 不允许的做法

- 不把 `/search` 当作外部来源 —— 这是本仓库内部的 thread 历史搜索，跟论文库无关。
- 不伪造引用 / 伪造 venue。`venue` 字段仅在 S2 返回时填写。
- 不为了凑数把已经看过 14 天内的老论文塞进 Top picks：老论文应归入 `extended_reading`。
- 不放置「作者 X 说 Y」这类无链接的社区信号；引入作者声量时必须附帖子链接并标注来源类型。

## 来源注脚

报告正文里关键主张要行内加链接（markdown `[title](url)`），不要把所有链接挤到末尾 appendix。附录 `## 来源与交叉验证说明` 段简短描述本期：

- 三源是否都成功抓取
- S2 元数据覆盖率
- 是否有 seen-pool 命中导致内容迁移
- 覆盖缺口与补救建议
