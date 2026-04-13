---
name: market-briefing
description: Produce Chinese-first politics, finance, and AI market briefings with persisted Markdown and JSON outputs under ~/.oh-my-agent/reports/market-briefing/. Use this skill for bounded historical bootstrap dossiers, domain daily digests, and cross-domain weekly synthesis that should reuse prior stored reports rather than relying on Discord history.
metadata:
  timeout_seconds: 1200
---

# Market Briefing

Use this skill for recurring politics, finance, and AI briefings. This is one core skill with three explicit modes:

- `bootstrap_backfill`
- `daily_digest`
- `weekly_synthesis`

The skill is report-centric. It writes durable report files under `~/.oh-my-agent/reports/market-briefing/` so later weekly synthesis can build on stored report history instead of only relying on Discord chat history.

## When to use

- User wants a politics / finance / AI daily report.
- User wants a weekly synthesis across those domains.
- User wants a bounded historical backfill to seed future reporting.
- User wants automation-ready prompts or templates for recurring market briefings.

## Mode/domain/date discipline

- Always make `mode`, `domain`, and `report date` explicit in the working plan.
- Do not silently default to `daily_digest + ai` just because the user asked for a generic report.
- If the user intent clearly matches one domain, lock that domain explicitly.
- If the user intent spans multiple domains, prefer:
  - multiple domain daily reports, or
  - one `weekly_synthesis` cross-domain report
- If no report date is specified, default to the current local date of the runtime environment (derived from `OMA_REPORT_TIMEZONE` or `TZ` when set) and state it explicitly in the title and JSON metadata.
- Do not invent a future date unless the user explicitly requests a future-dated planning memo.

## Mode and domain model

### Modes

- `bootstrap_backfill`
  - Build one bounded historical dossier for a domain.
  - Do **not** generate fake historical daily files.
- `daily_digest`
  - Generate one daily report for a single domain.
- `weekly_synthesis`
  - Generate one cross-domain weekly report using recent stored daily reports plus bootstrap context.

### Domains

- `politics`
- `finance`
- `ai`
- `cross-domain` is used only for `weekly_synthesis`

### Default backfill windows

- `politics`: 30 days
- `finance`: 30 days
- `ai`: 14 days

## Required workflow

1. Pick the explicit mode and domain.
2. Load prior stored context with the helper script.
3. Generate a starter Markdown + JSON scaffold.
4. Do external research for the requested mode/domain.
5. Fill the Markdown + JSON with the researched content.
6. Persist both files into the canonical report store.
7. In the final answer, return the report content directly and mention where it was stored.

## Storage layout

Canonical storage paths:

- `~/.oh-my-agent/reports/market-briefing/bootstrap/<domain>/<date>.md|json`
- `~/.oh-my-agent/reports/market-briefing/daily/<date>/<domain>.md|json`
- `~/.oh-my-agent/reports/market-briefing/weekly/<iso-week>/cross-domain.md|json`

Use the helper script for path generation and persistence. Do not hand-roll paths unless you are patching the helper itself.

## Report structure

Read the relevant references before drafting:

- `references/report_schema.md`
- `references/source_policy.md`
- `references/finance_watchlist.md`
- `references/ai_frontier_watchlist.md`
- `references/ai_people_seed.yaml`
- `references/automation_templates.md`
- `references/prompt_recipes.md`

### Daily report structure

- `politics`
  - 中国中央政策 / 决策信号
  - 美国联邦政策 / 决策信号
  - 中美 / 地缘政治动态
  - 影响判断与后续观察点
- `finance`
  - 中国宏观与政策
  - 美国宏观与政策
  - 美国市场波动与风险偏好
  - 中国 / 香港市场脉搏
  - 中国房地产政策与融资信号
  - 重点持仓财报 / 管理层表态 / CEO 公开发言
  - 市场与指数基金视角
  - 后续观察点
  - 默认持仓池：
    - `NVDA`
    - `MSFT`
    - `AAPL`
    - `AMZN`
    - `GOOG`
    - `TSLA`
    - `META`
    - `VOO`
    - `SPY`
    - `S&P 500`
  - 持仓池默认滚动窗口：`7 天`
- `ai`
  - Frontier Labs / Frontier Model Radar
  - 关键人物与社区信号
  - 固定五层：
    - `energy`
    - `chips`
    - `infra`
    - `model`
    - `application`
  - 层间联动影响
  - 候选池变化与后续关注

### Politics vs finance boundary

- `finance`
  - 关注政策对市场、融资、住房、信用、风险偏好的影响
- `politics`
  - 关注政策文本本身、立法/行政背景、地缘/安全/供应链政治含义
- 同一政策如果两边都提：
  - finance 写市场影响
  - politics 写政策与地缘背景
  - 不允许两边写成重复摘要

### Weekly synthesis structure

Use:

- recent 7 daily reports
- latest bootstrap dossier for each domain
- a bounded number of previous weekly reports

The weekly report should stay cross-domain and focus on structure, trend, and continuity rather than repeating raw headlines.

Finance weekly must explicitly absorb:

- US market volatility
- China / Hong Kong market pulse
- China property policy changes
- tracked holdings and broad-market implications

AI weekly must explicitly absorb:

- frontier-lab watch
- people/community signals
- five-layer developments

Weekly JSON remains structurally light and should not copy all daily-only JSON fields into the weekly sidecar.

## Source policy

The report must explicitly distinguish source types:

- `primary / official`
- `company / filing`
- `media / analysis`
- `community / social`

Bias slightly toward primary sources for key conclusions, but do not force a primary-only workflow. Cross-check important claims with at least one additional source family where possible.

Every report should include:

- a short source mix note
- a short verification note
- inline source links in the main body, not only in the final source appendix

Do not treat `/search` as an external news source. In this repo, `/search` is internal conversation-history search only.

## Density rule

- Do not let sections collapse into one sentence of generic filler.
- If a section has no high-confidence incremental signal, say so explicitly with `no high-confidence incremental signal` and explain what remains worth watching.
- Use `coverage_gaps` and `confidence_flags` instead of pretending a thin section is complete.

## AI people-pool workflow

For `daily_digest` with `domain=ai`:

1. Load prior report context with `report_store.py context`.
2. Load the current people pool.
3. Research both:
   - frontier-lab radar
   - five-layer AI developments
   - tracked people / community / X.com signals plus a bounded discovery sweep for new names
4. Fill the AI Markdown + JSON.
5. Record new candidates and promotions with `ai_people_pool.py record`.
6. Persist the report with `report_store.py persist`.

Only use `sync-repo` for explicit curated maintenance. Do not rewrite the repo seed file during a normal daily run.
