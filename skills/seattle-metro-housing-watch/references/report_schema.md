# Report Schema

Use these schemas when filling Markdown and JSON outputs.

## Common JSON contract

All reports should include at least:

```json
{
  "version": 1,
  "mode": "weekly_pulse",
  "title": "",
  "generated_at": "",
  "report_timezone": "",
  "report_date": "",
  "is_first_report": false,
  "data_freshness_note": "",
  "region_scope": ["seattle", "bellevue", "redmond", "kirkland", "issaquah", "bothell", "lynnwood"],
  "summary": "",
  "key_takeaways": [],
  "rate_context": "",
  "metro_context": "",
  "area_scoreboard": [],
  "sample_listings": [],
  "source_mix_note": "",
  "verification_note": "",
  "coverage_gaps": [],
  "confidence_flags": [],
  "sources": [],
  "sections": []
}
```

If the report is first-run, set:

```json
{
  "is_first_report": true,
  "data_freshness_note": "..."
}
```

## `area_scoreboard[]`

Use:

```json
{
  "area": "bellevue",
  "label": "Bellevue",
  "median_sale_price": null,
  "median_list_price": null,
  "inventory_signal": "",
  "days_on_market": null,
  "sale_to_list": null,
  "price_drop_signal": "",
  "notes": ""
}
```

`area_scoreboard` default areas:

- `seattle`
- `bellevue`
- `redmond`
- `kirkland`
- `issaquah`
- `bothell`
- `lynnwood`

## `sample_listings[]`

Use:

```json
{
  "area": "seattle",
  "address_or_label": "",
  "url": "",
  "source_site": "redfin",
  "property_type": "single-family",
  "list_price": "",
  "original_list_price": "",
  "beds_baths_sqft": "",
  "listing_status": "",
  "listed_at": "",
  "days_on_market": "",
  "price_history_summary": "",
  "why_it_matters": ""
}
```

Rules:

- `weekly_pulse`
  - all 7 areas get 2 listings first
  - total cap `18`
  - remaining 4 slots go to areas with stronger active inventory and better high-price sample availability
- `market_snapshot`
  - at most 7 listings total
  - default 1 per area
- `area_deep_dive`
  - target 4-6 listings for the selected area
- listing samples are supporting evidence, not the main report spine
- price filtering is based on the **area's own** median baseline, not a metro-wide median

## Body citation rule

Use inline links in main paragraphs and important bullets:

```md
Seattle 库存较去年同期更宽松，但融资成本仍压制买方节奏（[NWMLS](https://...), [FRED](https://...)）。
```

## Weekly pulse Markdown shape

```md
# 西雅图房市周脉搏｜<date>

一句话结论：...

## 摘要

## 数据新鲜度说明

## 利率与融资环境

## Seattle Metro 核心市场脉搏

## 区域 Scoreboard

## 分区域买方观察

## 代表性挂牌样本

## 后续观察点

## 来源与交叉验证说明
```

## Market snapshot Markdown shape

```md
# 西雅图房市快照｜<date>

一句话结论：...

## 摘要

## 数据新鲜度说明

## 利率与融资环境

## Seattle Metro 核心市场脉搏

## 区域 Scoreboard

## 代表性挂牌样本

## 来源与交叉验证说明
```

## Area deep dive Markdown shape

```md
# Bellevue 房市深挖｜<date>

一句话结论：...

## 摘要

## 数据新鲜度说明

## Bellevue 市场背景

## Bellevue 买方观察

## Bellevue 代表性挂牌样本

## 相对 Seattle Metro 的位置判断

## 后续观察点

## 来源与交叉验证说明
```
