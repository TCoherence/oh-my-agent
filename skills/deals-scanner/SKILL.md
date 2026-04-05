---
name: deals-scanner
description: Produce Chinese-first deal/discount scanning reports covering US credit cards (Doctor of Credit, NerdWallet, TPG, issuer sites), uscardforum (美卡论坛), Rakuten cashback, Slickdeals, and Dealmoon (北美省钱快报). Persists Markdown and JSON outputs under ~/.oh-my-agent/reports/deals-scanner/. Use this skill for daily deal scans per source channel and weekly cross-source deal digests that reuse prior stored reports.
metadata:
  timeout_seconds: 900
---

# Deals Scanner

Use this skill for recurring deal/discount intelligence across five source channels. One core skill with two explicit modes:

- `daily_scan`
- `weekly_digest`

The skill is report-centric. It writes durable report files under `~/.oh-my-agent/reports/deals-scanner/` so weekly digests can build on stored daily report history.

## When to use

- User wants credit card deal updates (sign-up bonuses, cashback promotions, fee waivers).
- User wants uscardforum (美卡论坛) highlights — approval data points, redemption strategies, bank policy changes.
- User wants Rakuten cashback tracking — high cashback merchants, flash deals, stacking strategies.
- User wants Slickdeals highlights — frontpage deals, community-voted picks across categories.
- User wants Dealmoon (北美省钱快报) roundups — featured deals, exclusive codes, category picks.
- User wants a weekly cross-source digest.

## Mode/source/date discipline

- Always make `mode`, `source`, and `report date` explicit in the working plan.
- Do not silently default to one source just because the user asked for a generic scan.
- If the user intent clearly matches one source, lock that source explicitly.
- If the user intent spans multiple sources, prefer:
  - multiple source daily reports, or
  - one `weekly_digest` cross-source report
- If no report date is specified, default to the current local date and state it explicitly in the title and JSON metadata.

## Mode and source model

### Modes

- `daily_scan`
  - Generate one daily report for a single source channel.
  - Focuses on today's active deals and notable changes.
- `weekly_digest`
  - Generate one cross-source weekly report using recent stored daily reports.
  - Focuses on trends, best deals of the week, and upcoming opportunities.

### Sources

- `credit-cards` — **topic bucket**: aggregates Doctor of Credit, NerdWallet, The Points Guy, Frequent Miler, One Mile at a Time, and issuer official activity pages. Not a single site.
- `uscardforum` — **single site**: uscardforum.com (美卡论坛)
- `rakuten` — **single site**: rakuten.com
- `slickdeals` — **single site**: slickdeals.net
- `dealmoon` — **single site**: dealmoon.com (北美省钱快报)
- `all-sources` — used only for `weekly_digest`

### Validation rules

- `daily_scan` only accepts the 5 named sources; rejects `all-sources`.
- `weekly_digest` only accepts `all-sources`; rejects named sources.
- Invalid combinations raise an error.

## Required workflow

1. Pick the explicit mode and source.
2. Load prior stored context with the helper script:

```bash
./.venv/bin/python skills/deals-scanner/scripts/deal_store.py context \
  --mode daily_scan \
  --source credit-cards
```

For weekly digest:

```bash
./.venv/bin/python skills/deals-scanner/scripts/deal_store.py context \
  --mode weekly_digest \
  --source all-sources
```

3. Generate a starter Markdown + JSON scaffold:

```bash
./.venv/bin/python skills/deals-scanner/scripts/deal_store.py scaffold \
  --mode daily_scan \
  --source credit-cards \
  --markdown-file /tmp/credit-cards_daily.md \
  --json-file /tmp/credit-cards_daily.json
```

4. Do external web research for the requested mode/source. Follow `references/source_policy.md` for search strategies.
5. Fill the Markdown + JSON with researched content. Every deal must have an inline source link.
6. Persist both files into the canonical report store:

```bash
./.venv/bin/python skills/deals-scanner/scripts/deal_store.py persist \
  --mode daily_scan \
  --source credit-cards \
  --markdown-file /tmp/credit-cards_daily.md \
  --json-file /tmp/credit-cards_daily.json
```

7. In the final answer, return the report content directly and mention where it was stored.

## Storage layout

Canonical storage paths:

- `~/.oh-my-agent/reports/deals-scanner/daily/<date>/<source>.md|json`
- `~/.oh-my-agent/reports/deals-scanner/weekly/<iso-week>/all-sources.md|json`

Use the helper script for path generation and persistence. Do not hand-roll paths.

## Report structure

Read the relevant reference before drafting:

- Deal schemas: `references/deal_schema.md`
- Source policy: `references/source_policy.md`
- Prompt recipes: `references/prompt_recipes.md`

### Daily report structures

- `credit-cards`
  - 开卡奖励（Sign-up Bonuses）
  - 消费返现与积分活动
  - 年费减免与升降级优惠
  - 即将到期的优惠
- `uscardforum`
  - 热门讨论与数据点
  - 开卡审批经验
  - 积分兑换策略
  - 银行政策变动
- `rakuten`
  - 今日高返现商家
  - 限时闪购返现
  - 新上线商家与活动
  - 值得关注的叠加策略
- `slickdeals`
  - 今日热门 Frontpage 优惠
  - 电子产品与科技
  - 家居生活与日用
  - 其他值得关注
- `dealmoon`
  - 今日精选折扣
  - 独家折扣码与优惠
  - 美妆个护
  - 时尚服饰与鞋包
  - 美食与生活

### Weekly digest structure

Uses recent 7 days of daily reports across all 5 sources plus prior weekly reports:

- 本周总览
- 信用卡优惠回顾
- 美卡论坛回顾
- Rakuten 返现回顾
- Slickdeals 热门回顾
- Dealmoon 精选回顾
- 跨渠道策略与趋势
- 下周关注点

The weekly report should focus on the best deals, notable trends, and actionable strategies rather than repeating every daily item.

## Inline citation rules (hard requirement)

Deal descriptions in the body **must** carry inline source links. Do not only dump all sources at the bottom.

Preferred style:
- paragraph ending: `...（[Doctor of Credit](https://...), [Chase](https://...)）`
- bullet ending: `- Chase Sapphire 开卡奖励 80K 积分（[DoC](https://...), [Chase](https://...)）`
- Per deal, use 1-2 high-signal citations rather than a long chain.
- Keep the final `来源与说明` section as a compact source appendix and confidence note.

## JSON requirements

The JSON sidecar is part of the report contract. Keep these fields present:

- `version`
- `mode`
- `source`
- `title`
- `generated_at`
- `period_start`
- `period_end`
- `summary`
- `top_deals`
- `source_mix_note`
- `sources`
- `sections`

For weekly digest, also include:

- `iso_week`
- `trend_summary`
- `cross_source_highlights`

### Deal entry schema

Each entry in `top_deals` and `sections[].deals[]`:

```json
{
  "deal_title": "",
  "value": "",
  "merchant": "",
  "url": "",
  "expires": "",
  "quality_score": 3,
  "notes": ""
}
```

### quality_score (1-5 integer)

| Score | Label | Criteria |
|-------|-------|----------|
| 5 | 必抢 | All-time low / top sign-up bonus / limited time ending soon |
| 4 | 很值 | Well below typical price, high community validation |
| 3 | 值得关注 | Reasonable discount, good for those with the need |
| 2 | 一般 | Mediocre discount, appears frequently |
| 1 | 凑数 | Informational only, not recommended to chase |

## Output rules

- Default output language is Chinese.
- Markdown should be readable as a finished deal report, not just raw bullets.
- Each deal should include: deal title, value/discount amount, expiration info, link, and a brief quality assessment.
- JSON should stay compact and structured for later machine reuse.
- If a source has no notable deals today, say so explicitly rather than padding with filler.
- Weekly trend claims must be grounded in stored daily report files, not vague memory.

## Research approach

This skill uses web research (search), not automated scraping. The agent searches for current deals using the strategies defined in `references/source_policy.md`. Do not build or invoke scraping scripts.

## Prompting notes

- When the user asks for a reusable prompt, use `references/prompt_recipes.md`.
- For automation prompts, keep the mode/source/date explicit in the prompt text.
