# Deal Schema

JSON and Markdown structure definitions for deals-scanner reports.

## JSON base payload (daily_scan reference report)

```json
{
  "version": 1,
  "mode": "daily_scan",
  "source": "<credit-cards|uscardforum|rakuten|slickdeals|dealmoon>",
  "title": "信用卡优惠日报｜2026-04-04",
  "generated_at": "2026-04-04T08:13:27+00:00",
  "report_timezone": "America/Los_Angeles",
  "report_date": "2026-04-04",
  "period_start": "2026-04-04",
  "period_end": "2026-04-04",
  "summary": "一句话结论",
  "top_deals": [],
  "source_mix_note": "来源构成说明",
  "sources": [],
  "sections": [],
  "lower_confidence_watchlist": [],
  "high_confidence_count": 0,
  "coverage_floor_met": false
}
```

`generated_at`、`report_timezone`、`report_date` 由 helper 在 persist 时统一覆盖，不接受模型自填的占位值。

## Daily summary extra fields

For broad daily bundles, `source` is `summary` and the JSON should also include:

```json
{
  "action_buckets": {
    "apply_now": [],
    "buy_now": [],
    "stack_now": [],
    "watchlist": []
  },
  "source_snapshots": [
    {
      "source": "credit-cards",
      "summary": "",
      "high_confidence_count": 0,
      "watchlist_count": 0,
      "met_floor": false
    }
  ],
  "coverage_status": {
    "target_floor": 10,
    "sources_below_floor": []
  },
  "reference_reports": [
    {
      "source": "credit-cards",
      "label": "信用卡优惠",
      "markdown_path": "references/credit-cards.md",
      "json_path": "references/credit-cards.json"
    }
  ]
}
```

## Weekly-only extra fields

```json
{
  "iso_week": "2026-W14",
  "trend_summary": "本周跨渠道趋势一句话总结",
  "cross_source_highlights": []
}
```

- `trend_summary`: one-line cross-source trend summary
- `cross_source_highlights`: list of deal entries that are notable across multiple sources or represent cross-channel stacking opportunities

## Deal entry schema

Used in `top_deals[]`, `sections[].deals[]`, and `cross_source_highlights[]`.

```json
{
  "deal_title": "Chase Sapphire Preferred 开卡奖励 80K UR",
  "value": "80,000 UR points (~$1,000)",
  "merchant": "Chase",
  "url": "https://...",
  "expires": "2026-04-30",
  "quality_score": 5,
  "notes": "需在3个月内消费$4,000"
}
```

### Field definitions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `deal_title` | string | yes | Concise deal description in Chinese |
| `value` | string | yes | Discount amount, cashback %, or point value |
| `merchant` | string | yes | Brand or retailer name |
| `url` | string | yes | Direct link to deal page |
| `expires` | string | no | Expiration date (ISO) or "ongoing" or empty if unknown |
| `quality_score` | int (1-5) | yes | See scale below |
| `notes` | string | no | Conditions, restrictions, tips |

### quality_score scale (1-5 integer)

| Score | Label | Criteria |
|-------|-------|----------|
| 5 | 必抢 | All-time low price / top-tier sign-up bonus / limited time ending within 48h |
| 4 | 很值 | Well below typical price; high community validation (upvotes, multiple comments) |
| 3 | 值得关注 | Reasonable discount; good for those who need it |
| 2 | 一般 | Mediocre discount; appears frequently |
| 1 | 凑数 | Informational record only; not recommended to chase |

Do not use fractional scores. Always use an integer from 1 to 5.

## Source entry schema

Used in `sources[]`.

```json
{
  "title": "Doctor of Credit: Chase Sapphire Preferred 80K Offer",
  "url": "https://www.doctorofcredit.com/...",
  "source_type": "blog",
  "publisher": "Doctor of Credit",
  "published_at": "2026-04-03",
  "notes": ""
}
```

### source_type values

| Value | Meaning | Examples |
|-------|---------|---------|
| `deal-site` | Deal aggregation platform | Slickdeals, Dealmoon, Rakuten |
| `official` | Issuer or merchant official page | Chase.com, Amazon.com |
| `blog` | Credit card / deal analysis blog | Doctor of Credit, The Points Guy, NerdWallet |
| `forum` | Community discussion forum | uscardforum.com, Reddit r/churning |

## Section schema

```json
{
  "slug": "signup-bonuses",
  "heading": "开卡奖励（Sign-up Bonuses）",
  "summary": "本节摘要",
  "deals": []
}
```

Sections use `deals[]` arrays (not `bullets[]`). Each element follows the deal entry schema above.

## Markdown structure by source

### credit-cards

```
# 信用卡优惠日报｜<date>
一句话结论：
## 摘要
## 开卡奖励（Sign-up Bonuses）
## 消费返现与积分活动
## 年费减免与升降级优惠
## 即将到期的优惠
## 来源与说明
```

### uscardforum

```
# 美卡论坛日报｜<date>
一句话结论：
## 摘要
## 热门讨论与数据点
## 开卡审批经验
## 积分兑换策略
## 银行政策变动
## 来源与说明
```

### rakuten

```
# Rakuten 返现日报｜<date>
一句话结论：
## 摘要
## 今日高返现商家
## 限时闪购返现
## 新上线商家与活动
## 值得关注的叠加策略
## 来源与说明
```

### slickdeals

```
# Slickdeals 精选日报｜<date>
一句话结论：
## 摘要
## 今日热门 Frontpage 优惠
## 电子产品与科技
## 家居生活与日用
## 服饰 / 户外 / 其他值得关注
## 来源与说明
```

### dealmoon

```
# 北美省钱快报日报｜<date>
一句话结论：
## 摘要
## 今日精选折扣
## 独家折扣码与优惠
## 美妆个护
## 电子数码
## 家居生活
## 来源与说明
```

### daily summary

```
# 优惠扫描总览｜<date>
一句话结论：
## 今日判断
## Apply now
## Buy now
## Stack now
## Watchlist
## 各渠道一句话结论
## Coverage / Confidence
## Reference 索引
- [信用卡优惠](references/credit-cards.md)
- [美卡论坛](references/uscardforum.md)
- [Rakuten 返现](references/rakuten.md)
- [Slickdeals](references/slickdeals.md)
- [Dealmoon](references/dealmoon.md)
## 来源与说明
```

### weekly all-sources

```
# 优惠情报周报｜<iso-week>
一句话结论：
## 本周总览
## 信用卡优惠回顾
## 美卡论坛回顾
## Rakuten 返现回顾
## Slickdeals 热门回顾
## Dealmoon 精选回顾
## 跨渠道策略与趋势
## 下周关注点
## 来源与说明
```

## Inline citation rules

Deal descriptions **must** carry inline source links in the body. This is a hard requirement.

- Style: `- Chase Sapphire 开卡奖励 80K 积分（[DoC](https://...), [Chase](https://...)）`
- Per deal: 1-2 high-signal links, not a long chain.
- The final `来源与说明` section serves as a compact appendix, not the only place links appear.
- Prefer a deal-level link:
  - concrete deal page
  - concrete forum thread
  - concrete merchant / issuer / product page
- Homepage, list pages, and roundup pages are acceptable only as supplemental evidence or in `lower_confidence_watchlist`. They should not be the only link for a mainline deal entry.

## Coverage floor

- Daily reference reports should target `12-15` verified items and should not stop below `10` unless the source genuinely lacks enough credible candidates.
- High-confidence floor is `10`.
- If a source cannot reach `10` high-confidence items:
  - keep the main正文 focused on high-confidence entries only
  - put weaker candidates into `lower_confidence_watchlist`
  - set `coverage_floor_met=false`
  - call the gap out explicitly in the summary report's `Coverage / Confidence` section
