# paper-digest 对外消费契约

本文件是 **paper-digest v1 JSON 消费契约**的权威定义，供其他 skill / automation 引用。

## 读写边界

- 外部消费方（如 `market-briefing` AI daily）**只读**。
- 写入只由 `paper-digest` 自己的 `scripts/report_store.py persist` 完成。
- 任何消费方都不得修改 `~/.oh-my-agent/reports/paper-digest/**` 下的文件。

## 稳定路径

```
~/.oh-my-agent/reports/paper-digest/daily/<YYYY-MM-DD>.json
```

- 日期使用报告生成时所在时区的本地日期（由 `OMA_REPORT_TIMEZONE` 或 `TZ` 决定，默认为系统时区）。
- 文件不存在 → 消费方 fallback 到「今日论文雷达暂无更新」，不报错。

## 稳定字段（v1）

以下是被承诺跨版本保持的字段契约。新增字段仅追加，不改语义：

```
version          1
mode             "daily_digest"
domain           "paper-digest"
report_date      YYYY-MM-DD
top_picks        [PaperContract]
```

### `PaperContract`（Top picks 数组里每一项）

**v1 承诺字段：**

- `arxiv_id`（str，必填）— 例 `"2604.15311"`，可空字符串表示无 arXiv id
- `title`（str，必填）— 英文原题
- `tldr_cn`（str，必填）— 中文一句速读
- `arxiv_url`（str，必填）— arXiv abs 页链接

消费方可假设这四个字段存在；其他字段可能有但不保证。

## 典型消费范例（market-briefing AI daily）

在 AI daily prompt 里可以加一段：

```
在撰写「Frontier Labs / Frontier Model Radar」之后，读取
~/.oh-my-agent/reports/paper-digest/daily/<today>.json 的 top_picks[0:3]，
在「关键人物与社区信号」之前插入一个「今日论文雷达」子段：

## 今日论文雷达

- [title](arxiv_url)：tldr_cn
- ...

若文件缺失或 top_picks 为空，写「今日论文雷达暂无更新」。
不要改写原文件，不要跑 paper-digest 的脚本。
```

## 版本迁移

- 破坏性变更必须 bump `version`（v1 → v2）。消费方在读取时应检查 `version == 1`；不兼容时降级到 fallback 文案。
- 字段重命名不允许 —— 改用新字段，老字段保留至少两个 minor version 的 deprecation 期。
- `domain` 字面量 `"paper-digest"` 永远不变，即使 skill 目录改名也不改它。

## 不被承诺的

- `category_hits`、`extended_reading`、`new_authors`、`tracked_labs_seen` 当前内部使用，未来可能重构。外部消费方不应依赖。
- `sections[]` 内部字段（`slug`/`heading`/`summary`/`bullets`）对齐 market-briefing 家族的 schema，但不是跨家族稳定契约。
- `ranking_score` / `ranking_reasons` 是调试信号，可能随 watchlist 或排序算法变化。
