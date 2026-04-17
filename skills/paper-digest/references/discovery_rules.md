# paper-digest 新作者 / 团队发现规则

每份日报应**尝试**发现 1-3 位新作者或新团队，让 watchlist 慢慢长大。不是硬性配额 —— 没达标候选时老老实实写「本日发现扫描未发现达标候选」。

## 在哪看

- 本次候选 JSON 里的 `authors` 字段，特别是在 Top picks / 延伸阅读里反复出现的名字
- `affiliations` 字段（当 S2 返回时）匹配到 watchlist 外的机构
- HF Daily 上 trending 榜前 5 的作者名单
- Semantic Scholar 返回的共作者、引用网络里的高频邻居

## 什么算达标候选（`new_authors` 条目）

**任意一条成立即可：**

- 在过去 48h 发表了 arXiv 预印本，且命中 watchlist keywords
- 是本期 Top picks 某篇的一作 / 通讯作者，且未在 tracked_authors 列表中
- 在本次候选的不同论文 / 不同来源里重复出现 ≥ 2 次
- 属于 watchlist 已追踪机构的新面孔（同机构但不在 tracked_authors）
- 论文被 S2 venue 字段标成 must-read venue（NeurIPS / ICML / ICLR / ACL / CVPR / ICRA / RSS / OSDI / SOSP / MLSys）

## 什么不算

- 「某某大佬曾经说过」这类历史引用
- 只在一条社区帖子里被提及但没有实际论文 / 代码产出
- 已在 `paper_watchlist.yaml::tracked_authors` 的老面孔
- 只在封面新闻里被点名的高管（除非他们有具体 arXiv 预印本产出）
- 全名拼写不确定的 fragment（宁可跳过也不要硬塞）

## 必填字段

```json
{
  "name": "Full Name",
  "aliases": ["可选别名"],
  "affiliation": "最主要机构",
  "arxiv_url": "代表作 arXiv abs 链接",
  "reason": "一句话：做了什么 + 为什么值得追",
  "evidence_urls": ["至少一条 URL 证明 signal"],
  "cross_checked": true|false,
  "group_hint": "frontier-labs | oss-ai-labs | robotics-labs | systems-labs"
}
```

可选字段：`x_handle`、`search_terms`、`promote_recommended`。

## 零候选的情况

写进 `new_authors`：

```json
[]
```

并在「🧑‍🔬 新出现的作者 / 团队」段落写：「本日发现扫描未发现达标候选人」+ 一句解释（例如「今日 Top picks 作者均已在追踪列表」）。

不要为了凑数硬塞。

## 自动沉淀机制

- `scripts/paper_seen_pool.py record` 会把报告里 `tracked_labs_seen` 的机构累计到 runtime state。
- 观察次数达到 `sync-seed --min-observations`（默认 2）时，可以运行 `paper_seen_pool.py sync-seed` 把它们从 runtime 回写到 `references/paper_groups_seed.yaml`。
- 对人（`new_authors`）目前不做自动沉淀，全靠人工周末 review runtime 状态后手动编辑 `paper_watchlist.yaml::tracked_authors`。这是有意保守的设计 —— 作者名字比机构名字更难自动去重。
