# paper-digest prompt recipes

这里是可以粘贴进 Discord `/ask` 或 automation 的 prompt 模板，供 agent 使用 paper-digest skill 时直接调用。

## 1. 标准日报

```
使用 paper-digest skill 生成今天的论文雷达日报。

步骤：
1. 运行 scripts/paper_fetch.py --source all 取候选（48h 窗口，默认 watchlist）。
2. 运行 scripts/report_store.py context 取最近 7 天上下文，避免重复推荐。
3. 运行 scripts/paper_seen_pool.py context 查看过去 14 天已出现过的论文。
4. 运行 scripts/report_store.py scaffold 生成空模板。
5. 根据候选 JSON 撰写：
   - Top picks：最多 8 篇，按 ranking_score 挑选，每篇一句中文速读，保留英文标题。
   - Watchlist 分类命中：按 arXiv category 分小节，每类最多 4 篇。
   - 延伸阅读：从 Top picks 中挑选 1-2 篇做 Semantic Scholar 相似论文扩散。
   - 新出现的作者 / 团队：必须基于本次候选里首次命中 watchlist 的作者 / 机构，附证据链接。
6. 写 coverage_gaps 和 confidence_flags（若任何源失败）。
7. 运行 scripts/report_store.py persist 落盘并回发保存路径。
```

## 2. 主题深挖

用于「我今天关心 long context / MoE / VLA」一类收窄需求：

```
使用 paper-digest skill 做主题日报，主题是「{TOPIC}」。

在默认日报流程基础上：
- 只保留 Top picks 里 ranking_reasons 包含 "{TOPIC_KEYWORDS}" 或 abstract 命中主题的论文
- Watchlist 分类命中段合并为一段「{TOPIC} 相关新工作」
- 延伸阅读扩到最多 8 篇
- 在摘要里显式写「本报告已按主题 {TOPIC} 过滤」
```

## 3. 单源排障

HF 抓取坏了 / S2 限流时：

```
使用 paper-digest skill 做降级日报。
- 只用 scripts/paper_fetch.py --source arxiv 取候选
- Top picks 按 watchlist 关键词命中 + tracked_authors 命中打分，不使用 HF trending
- 在 coverage_gaps 显式标注 "hf_daily_unavailable" 和/或 "s2_unavailable"
```

## 4. 回看整理

用于周末把本周积累的 seen-pool 整理出周刊雏形（v2 feature，v1 先用此 prompt 手动做）：

```
使用 paper-digest skill 生成本周论文回顾。

- 用 scripts/report_store.py context --days 7 拿最近 7 份日报
- 合并各日的 Top picks，去重后按总 ranking_score 重排，取前 12 篇
- 按方向分 3-5 个主题簇（agent 自己归纳）
- 每簇给 2-3 句结构性观察：本周这个方向的新趋势、关键争议、值得追的后续工作
```

## 写作风格

- 中文为主，论文标题保留英文原名
- 每条带链接（markdown `[title](url)`）
- 不说「据说」「据报道」—— 要么给 link，要么写进 coverage_gaps
- 每段若无高置信度增量信号，显式写 `本段今日无高置信度增量信号`
