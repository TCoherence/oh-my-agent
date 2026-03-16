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
  "period_start": "",
  "period_end": "",
  "summary": "",
  "key_takeaways": [],
  "source_mix_note": "",
  "verification_note": "",
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

`evidence_links` entries should use:

```json
{
  "label": "",
  "url": "",
  "source_type": "primary / official"
}
```

## Body citation rule

Main-body paragraphs and important bullets should include compact inline source links, not only a final source appendix.

Preferred style:

```md
某项政策信号正在改变预期管理方式（[中国政府网](https://...), [Reuters](https://...)）。
```

or:

```md
- 公司把 2026 财年 capex 指引继续上调（[10-K/8-K](https://...), [Bloomberg](https://...)）
```

## Politics daily

Markdown shape:

```md
# 政治日报｜<date>

一句话结论：...

## 摘要

<2-4 段，允许在段末加入紧凑来源链接>

## 中国中央政策与决策信号

<短段落 + 要点，每个关键判断尽量有内联来源>

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

<2-4 段，允许在段末加入紧凑来源链接>

## 大公司财报与指引

<公司级别信号、指引和市场含义，正文内联来源>

## 宏观与政策调整

## 市场 / 经济含义

## 后续观察点

## 来源与交叉验证说明
```

## AI daily

Markdown shape:

```md
# AI 日报｜<date>

一句话结论：...

## 摘要

<2-4 段，允许在段末加入紧凑来源链接>

## Energy

## Chips

## Infra

## Model

## Application

## 层间联动影响

## 来源与交叉验证说明
```

## Bootstrap dossier

Markdown shape:

```md
# <domain> bootstrap dossier｜<date>

一句话结论：...

## 范围与时间窗

## 结构性主线

<允许做更长的历史脉络分析，但关键判断仍要内联来源>

## 关键事件与信号

## 当前状态判断

## 后续跟踪清单

## 来源与交叉验证说明
```

## Weekly synthesis

Markdown shape:

```md
# 市场情报周报｜<iso-week>

一句话结论：...

## 本周总览

<2-4 段，摘要不应只是一句话>

## 政治主线

## 财经主线

## AI 五层演进

## 跨域联动与结构性趋势

## 下周观察点

## 来源与交叉验证说明
```

Weekly JSON should also include:

```json
{
  "trend_summary": "",
  "cross_domain_links": []
}
```

Weekly sections should prefer richer prose plus selective bullets rather than only headlines.
