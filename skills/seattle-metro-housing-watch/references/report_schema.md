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
  "rate_sources": [],
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

## `rate_sources[]`

The rate block must show a multi-source spread, not a single PMMS headline. Use one entry per source:

```json
{
  "source_id": "freddie_mac_pmms",
  "label": "Freddie Mac PMMS",
  "tier": 1,
  "thirty_year": "6.36%",
  "fifteen_year": "5.71%",
  "snapshot_date": "2026-05-14",
  "url": "https://www.freddiemac.com/pmms",
  "notes": "national weekly average"
}
```

`source_id` enum (extend as new sources are onboarded):

- `freddie_mac_pmms` — Tier 1 baseline (always include)
- `mba_weekly` — Tier 2 (industry survey, weekly Wednesday)
- `mortgage_news_daily` — Tier 2 (daily index)
- `chase` / `bofa` / `wells_fargo` / `us_bank` — Tier 3 (big-lender rack rates; include the Seattle metro ZIP used in `notes`)
- `becu` / `wsecu` / `sound_cu` — Tier 4 (local credit union rack rates)

Render rules:

- Markdown body must include a table with columns: `source | 30Y | 15Y | snapshot date | notes`
- A "national vs local" one-liner must appear in `rate_context` whenever both Tier 3 and Tier 4 rows are present
- If a source is unreachable, still include the row with `thirty_year: "n/a"` and add the corresponding gap to `coverage_gaps`

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
  "source_site": "realtor",
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

`source_site` enum:

- `realtor` — Realtor.com listing detail page (preferred primary)
- `windermere` / `johnlscott` / `coldwellbankerbain` / `compass` — local brokerage listing detail (preferred primary)
- `redfin` / `zillow` — only when the listing **detail** page is reachable (these have been 403'ing — see source_policy.md)
- `builder` — builder-direct pages (TriPointe, Toll Brothers, etc.) for new construction
- `none` — no listing-level URL was reachable; `url` MUST be empty string, and the Markdown row must render as plain text without a markdown link

`url` rules:

- MUST resolve to a single-property detail page (contains MLS ID / listing ID / unique address slug)
- MUST NOT be a city / neighborhood / search-results URL — see source_policy.md §Listing link discipline for the full denylist
- Set to empty string when no listing-level URL is reachable (paired with `source_site: "none"`)

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
