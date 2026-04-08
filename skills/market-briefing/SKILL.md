---
name: market-briefing
description: Produce Chinese-first politics, finance, and AI market briefings with persisted Markdown and JSON outputs under ~/.oh-my-agent/reports/market-briefing/. Use this skill for bounded historical bootstrap dossiers, domain daily digests, and cross-domain weekly synthesis that should reuse prior stored reports rather than relying on Discord history.
metadata:
  timeout_seconds: 900
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
2. Load prior stored context with the helper script:

```bash
./.venv/bin/python skills/market-briefing/scripts/report_store.py context \
  --mode daily_digest \
  --domain politics
```

For weekly synthesis:

```bash
./.venv/bin/python skills/market-briefing/scripts/report_store.py context \
  --mode weekly_synthesis \
  --domain cross-domain
```

3. Generate a starter Markdown + JSON scaffold:

```bash
./.venv/bin/python skills/market-briefing/scripts/report_store.py scaffold \
  --mode daily_digest \
  --domain politics \
  --markdown-file /tmp/politics_daily.md \
  --json-file /tmp/politics_daily.json
```

4. Do external research for the requested mode/domain.
5. Fill the Markdown + JSON with the researched content.
6. Persist both files into the canonical report store:

```bash
./.venv/bin/python skills/market-briefing/scripts/report_store.py persist \
  --mode daily_digest \
  --domain politics \
  --markdown-file /tmp/politics_daily.md \
  --json-file /tmp/politics_daily.json
```

7. In the final answer, return the report content directly and mention where it was stored.

## Storage layout

Canonical storage paths:

- `~/.oh-my-agent/reports/market-briefing/bootstrap/<domain>/<date>.md|json`
- `~/.oh-my-agent/reports/market-briefing/daily/<date>/<domain>.md|json`
- `~/.oh-my-agent/reports/market-briefing/weekly/<iso-week>/cross-domain.md|json`

Use the helper script for path generation and persistence. Do not hand-roll paths unless you are patching the helper itself.

## Report structure

Read the relevant reference before drafting:

- Report schemas: `references/report_schema.md`
- Source policy: `references/source_policy.md`
- Automation templates: `references/automation_templates.md`
- Prompt recipes: `references/prompt_recipes.md`

### Daily report structure

- `politics`
  - 中国中央政策 / 决策信号
  - 美国联邦政策 / 决策信号
  - 中美 / 地缘政治动态
  - 影响判断与后续观察点
- `finance`
  - 中国宏观与政策
  - 美国宏观与政策
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
  - 中国财经默认只做到宏观 + 政策，不默认扩展到中国上市公司日常扫盘
- `ai`
  - 固定五层：
    - `energy`
    - `chips`
    - `infra`
    - `model`
    - `application`
  - 每层关键变化
  - 层间联动影响

### Weekly synthesis structure

Use:

- recent 7 daily reports
- latest bootstrap dossier for each domain
- a bounded number of previous weekly reports

The weekly report should stay cross-domain and focus on structure, trend, and continuity rather than repeating raw headlines. The finance weekly mainline must explicitly absorb:

- the last 7 days of China macro / policy signals
- the last 7 days of tracked-holdings earnings / management / CEO signal
- implications for broad-market positioning and index concentration

## Source policy

The report must explicitly distinguish source types:

- `primary / official`
- `company / filing`
- `media / analysis`

Bias slightly toward primary sources for key conclusions, but do not force a primary-only workflow. Cross-check important claims with at least one additional source family where possible.

Every report should include:

- a short source mix note
- a short verification note
- inline source links in the main body, not only in the final source appendix

Do not treat `/search` as an external news source. In this repo, `/search` is internal conversation-history search only.

## JSON requirements

The JSON sidecar is part of the report contract. Keep these fields present:

- `version`
- `mode`
- `domain`
- `title`
- `generated_at`
- `report_timezone`
- `report_date`
- `period_start`
- `period_end`
- `summary`
- `key_takeaways`
- `source_mix_note`
- `verification_note`
- `coverage_gaps`
- `confidence_flags`
- `sources`
- `sections`

For finance daily, also keep:

- `tracked_universe`
- `holdings_window_days`
- `china_macro_policy_summary`
- `us_macro_policy_summary`
- `market_index_view`

For weekly synthesis, also include:

- `trend_summary`
- `cross_domain_links`

## Output rules

- Default output language is Chinese.
- Markdown should be readable as a finished report, not just raw bullets.
- Reports should be richer than a short digest. Prefer:
  - an executive summary
  - section-level analysis
  - explicit implications
  - a concise watchlist
- Each daily section should normally contain:
  - at least one analysis paragraph
  - `2-4` meaningful bullets or observations
  - inline links on key claims
- If a section has no high-confidence incremental signal that day:
  - do not fill the section with generic prose
  - explicitly say there is no high-confidence new signal
  - explain why
  - state what to keep watching
- JSON should stay compact and structured for later machine reuse.
- If evidence is incomplete, say so directly and keep the uncertainty localized to the affected section.
- Weekly trend claims must be grounded in stored report files and current-source cross-checking, not vague memory.

## Inline citation rules

- Do not only dump all sources at the bottom.
- Important claims in the body should carry compact inline citations.
- Preferred style:
  - paragraph ending: `...（[中国政府网](https://...), [Reuters](https://...)）`
  - bullet ending: `- 关键变化 ...（[NVIDIA](https://...), [FT](https://...)）`
- For one paragraph or bullet, use 1-2 high-signal citations rather than a long citation chain.
- Keep the final `来源与交叉验证说明` section as a compact source appendix and confidence note.

## Finance-specific rules

- Normalize `APPL` to `AAPL`.
- Treat `VOO` / `SPY` / `S&P 500` as market and allocation lenses rather than pseudo-company earnings events.
- Finance daily must cover:
  - China macro / policy
  - US macro / policy
  - tracked holdings within the last 7 days
  - market / index implications
- If a tracked holding has no qualifying 7-day signal, say so explicitly instead of fabricating filler.

## Backfill rules

- `bootstrap_backfill` is bounded and explicit.
- Do not generate one fake daily file per historical day.
- Produce one bootstrap dossier per domain and store it under `bootstrap/<domain>/`.
- The dossier should summarize structural background, recent trajectory, and what future daily reports should track.

## Automation notes

When the user wants automation, use the templates in `references/automation_templates.md` and the file-driven model under `~/.oh-my-agent/automations/*.yaml`.

Do not point users back to inline `config.yaml` automation jobs.

## Prompting notes

- When the user asks for a reusable prompt or automation prompt, do not give only one generic version.
- Use `references/prompt_recipes.md` and choose the recipe that matches the intended breadth:
  - broad daily
  - focused sub-scope daily
  - bootstrap
  - weekly synthesis
- For automation prompts, keep the mode/domain/date-window explicit in the prompt text.
