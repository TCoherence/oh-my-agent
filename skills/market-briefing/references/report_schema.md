# Report Schema

Use these schemas when filling the Markdown and JSON outputs.

## Common JSON contract

All report JSON sidecars should include:

```json
{
  "version": 1,
  "mode": "daily_digest",
  "domain": "politics",
  "title": "",
  "generated_at": "",
  "report_timezone": "",
  "report_date": "",
  "period_start": "",
  "period_end": "",
  "summary": "",
  "key_takeaways": [],
  "source_mix_note": "",
  "verification_note": "",
  "coverage_gaps": [],
  "confidence_flags": [],
  "sources": [],
  "sections": []
}
```

`sources` entries should use:

```json
{
  "title": "",
  "url": "",
  "source_type": "primary / official",
  "publisher": "",
  "published_at": "",
  "notes": ""
}
```

`source_type` may be one of:

- `primary / official`
- `company / filing`
- `media / analysis`
- `community / social`

`sections` entries should use:

```json
{
  "slug": "",
  "heading": "",
  "summary": "",
  "bullets": [],
  "evidence_links": []
}
```

## Body citation rule

Main-body paragraphs and important bullets should include compact inline source links, not only a final source appendix.

## Politics daily

Markdown shape:

```md
# 政治日报｜<date>

一句话结论：...

## 摘要

## 中国中央政策与决策信号

## 美国联邦政策与决策信号

## 中美与地缘政治动态

## 影响判断与后续观察点

## 来源与交叉验证说明
```

## Finance daily

Markdown shape:

```md
# 财经日报｜<date>

一句话结论：...

## 摘要

## 中国宏观与政策

## 美国宏观与政策

## 美国市场波动与风险偏好

## 中国 / 香港市场脉搏

## 中国房地产政策与融资信号

## 重点持仓财报 / 管理层 / CEO 公开发言

## 市场与指数基金视角

## 后续观察点

## 来源与交叉验证说明
```

Finance JSON should also include:

```json
{
  "tracked_universe": ["NVDA", "MSFT", "AAPL", "AMZN", "GOOG", "TSLA", "META", "VOO", "SPY", "S&P 500"],
  "holdings_window_days": 7,
  "china_macro_policy_summary": "",
  "us_macro_policy_summary": "",
  "us_market_volatility_view": "",
  "china_market_pulse": "",
  "china_property_policy_view": "",
  "market_index_view": ""
}
```

Finance sections should use:

```json
[
  {"slug": "cn-macro-policy", "heading": "中国宏观与政策", "summary": "", "bullets": [], "evidence_links": []},
  {"slug": "us-macro-policy", "heading": "美国宏观与政策", "summary": "", "bullets": [], "evidence_links": []},
  {"slug": "us-market-volatility", "heading": "美国市场波动与风险偏好", "summary": "", "bullets": [], "evidence_links": []},
  {"slug": "china-market-pulse", "heading": "中国 / 香港市场脉搏", "summary": "", "bullets": [], "evidence_links": []},
  {"slug": "china-property-policy", "heading": "中国房地产政策与融资信号", "summary": "", "bullets": [], "evidence_links": []},
  {"slug": "tracked-holdings", "heading": "重点持仓财报 / 管理层 / CEO 公开发言", "summary": "", "bullets": [], "evidence_links": []},
  {"slug": "market-index-view", "heading": "市场与指数基金视角", "summary": "", "bullets": [], "evidence_links": []},
  {"slug": "watchlist", "heading": "后续观察点", "summary": "", "bullets": [], "evidence_links": []}
]
```

## AI daily

Markdown shape:

```md
# AI 日报｜<date>

一句话结论：...

## 摘要

## Frontier Labs / Frontier Model Radar

## 关键人物与社区信号

## Energy

## Chips

## Infra

## Model

## Application

## 层间联动影响

## 候选池变化与后续关注

## 来源与交叉验证说明
```

AI JSON should also include:

```json
{
  "tracked_people_groups": [
    "claude-code-builders",
    "openai-builders",
    "oss-ai-builders",
    "ai-generalists"
  ],
  "tracked_people": [],
  "frontier_lab_watch": ["OpenAI", "Anthropic", "Google DeepMind", "Meta", "xAI", "Mistral", "Qwen", "DeepSeek"],
  "frontier_signal_summary": "",
  "unverified_frontier_signals": [],
  "people_signal_summary": "",
  "new_candidate_people": [],
  "promoted_people": [],
  "candidate_queue_summary": ""
}
```

## Weekly synthesis

Weekly JSON remains structurally light. Strengthen:

- `trend_summary`
- `cross_domain_links`
- `sections[]`

Do not copy all finance/AI daily-only JSON fields into weekly JSON.
