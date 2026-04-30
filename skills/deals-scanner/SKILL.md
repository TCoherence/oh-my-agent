---
name: deals-scanner
description: Produce Chinese-first deal/discount scanning reports covering US credit cards (Doctor of Credit, NerdWallet, TPG, issuer sites), uscardforum (美卡论坛), Rakuten cashback, Slickdeals, and Dealmoon (北美省钱快报). Persists Markdown and JSON outputs under ~/.oh-my-agent/reports/deals-scanner/. Use this skill for daily deal scans per source channel and weekly cross-source deal digests that reuse prior stored reports.
metadata:
  timeout_seconds: 2400
  max_turns: 100
---

# Deals Scanner

Use this skill for recurring deal/discount intelligence across five source channels. One core skill with two explicit modes:

- `daily_scan`
- `weekly_digest`

The skill is report-centric. It writes durable report files under `~/.oh-my-agent/reports/deals-scanner/` so weekly digests can build on stored daily report history.
For broad daily scans, the durable output is a bundle:

- one day-level `summary.md|json`
- five per-source reference reports under `references/`

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
  - one broad daily bundle with `summary + references`, or
  - one `weekly_digest` cross-source report
- If no report date is specified, default to the current local date (derived from `OMA_REPORT_TIMEZONE` or `TZ` when set) and state it explicitly in the title and JSON metadata.

## Mode and source model

### Modes

- `daily_scan`
  - Generate one daily report for a single source channel.
  - Focuses on the source-specific recent window rather than a flat same-day sweep.
- `weekly_digest`
  - Generate one cross-source weekly report using recent stored daily reports.
  - Focuses on trends, best deals of the week, and upcoming opportunities.

### Sources

- `credit-cards` — **topic bucket**: aggregates Doctor of Credit, NerdWallet, The Points Guy, Frequent Miler, One Mile at a Time, and issuer official activity pages. Not a single site.
- `uscardforum` — **single site**: uscardforum.com (美卡论坛)
- `rakuten` — **single site**: rakuten.com
- `slickdeals` — **single site**: slickdeals.net
- `dealmoon` — **single site**: dealmoon.com (北美省钱快报)
- `summary` — **internal daily aggregation target** used only when building a broad daily bundle
- `all-sources` — used only for `weekly_digest`

### Validation rules

- `daily_scan` accepts the 5 named sources plus the internal aggregation target `summary`; rejects `all-sources`.
- `weekly_digest` only accepts `all-sources`; rejects named sources.
- Invalid combinations raise an error.

### Daily lookback defaults

When `daily_scan` runs without an explicit `--days` override, use these defaults:

- `credit-cards`: recent `3` days
- `uscardforum`: recent `3` days
- `rakuten`: recent `3` days
- `slickdeals`: recent `7` days
- `dealmoon`: recent `7` days
- `summary`: recent `7` days

`weekly_digest` does not inherit these defaults. It keeps a fixed recent `7` day window across all sources.

## Required workflow

1. Pick the explicit mode and source.
2. Load prior stored context with the helper script:

```bash
./.venv/bin/python ${OMA_AGENT_HOME}/skills/deals-scanner/scripts/deal_store.py context \
  --mode daily_scan \
  --source credit-cards
```

For weekly digest:

```bash
./.venv/bin/python ${OMA_AGENT_HOME}/skills/deals-scanner/scripts/deal_store.py context \
  --mode weekly_digest \
  --source all-sources
```

3. Generate a starter Markdown + JSON scaffold:

```bash
./.venv/bin/python ${OMA_AGENT_HOME}/skills/deals-scanner/scripts/deal_store.py scaffold \
  --mode daily_scan \
  --source credit-cards \
  --markdown-file /tmp/credit-cards_daily.md \
  --json-file /tmp/credit-cards_daily.json
```

4. Do external web research for the requested mode/source. Follow `references/source_policy.md` for search strategies.
5. Fill the Markdown + JSON with researched content. Every deal must have an inline source link.
6. Persist both files into the canonical report store:

```bash
./.venv/bin/python ${OMA_AGENT_HOME}/skills/deals-scanner/scripts/deal_store.py persist \
  --mode daily_scan \
  --source credit-cards \
  --markdown-file /tmp/credit-cards_daily.md \
  --json-file /tmp/credit-cards_daily.json
```

7. Output the report — see **Final answer format** below. (Mandatory; the user only sees your final assistant message.)

## Final answer format

**You MUST end your turn with the full Markdown report body in your reply** — the same Markdown content you persisted in step 6. The Discord user receives only your final assistant message; they cannot see file contents. If you skip this they only see progress narration ("scanning Doctor of Credit..." / "fetching Slickdeals...") and have no way to read the deals you found.

Layout:

```
<full Markdown report — every section, every deal item, verbatim from the .md you persisted>

📁 Stored at:
- ~/.oh-my-agent/reports/deals-scanner/<mode>/<date>/<source>.md
- ~/.oh-my-agent/reports/deals-scanner/<mode>/<date>/<source>.json
```

(For broad daily bundles, list every per-source path plus the combined bundle.)

❌ Don't end the turn with "Done.", "Report saved.", or a short progress summary — those are status notes, not the answer.
❌ Don't reply with only the storage path — the user cannot open files in Discord.
❌ Don't truncate, paraphrase, or "summarize for chat" because the report is long — the gateway auto-chunks messages > 2000 chars across multiple Discord posts, so paste the full body anyway.
✅ The exact Markdown body you wrote to the deal store goes into your reply, verbatim, followed by the storage paths.

### Broad daily bundle workflow

When the user asks for a broad daily scan or leaves the source intentionally broad:

**Execution strategy (parallel preferred)**: the 5 source `daily_scan`s
are independent — none of them needs to read another source's output to
do its job. If your runtime exposes a sub-agent / Task / Agent tool
(Claude Code's `Task` tool, Gemini CLI's `@agent_name`, or equivalent),
prefer fanning out the 5 source scans as parallel sub-agent calls so
they run concurrently and each gets its own context window. Each
sub-agent should:

- be told its single `source` (one of the 5 names below)
- own the full per-source workflow end-to-end (web research → JSON +
  Markdown via the helper script → write under `references/<source>.{md,json}`)
- return only a short success/failure summary + the persisted file path

The cross-source dedupe / multi-link aggregation (a deal showing up on
multiple sources → one entry with multiple links) happens in the
**summary step (Step 3)**, which reads the 5 per-source JSON files
back from disk — so isolated sub-agent contexts do **not** weaken
cross-source verification.

If sub-agent tooling is not available, fall back to running the 5
source scans sequentially in the parent context — same outputs, same
filesystem layout, just slower.

1. Run five `daily_scan` source reports for:
   - `credit-cards`
   - `uscardforum`
   - `rakuten`
   - `slickdeals`
   - `dealmoon`
2. Persist each one under the day-level `references/` directory.
3. Generate one additional `daily_scan` report with `source=summary`.
4. The summary report must:
   - read like a morning brief rather than a directory page
   - group top items into `Apply now`, `Buy now`, `Stack now`, and `Watchlist`
   - carry at least `8-12` concrete items across the first three action buckets unless the day is genuinely thin
   - open with a `简短结论` that uses `2` short sentences rather than a slogan
   - make the first `简短结论` sentence state today's priority action and cite `1-2` concrete drivers
   - make the second `简短结论` sentence state the main limitation / risk / coverage caveat
   - give one compact per-source snapshot
   - explicitly state any source that missed the high-confidence floor
   - explicitly state each source's lookback window in `Coverage / Confidence`
   - include explicit links to `references/<source>.md`
   - keep `各渠道快照` as a supporting layer, not the main content
   - make each source snapshot a compact brief of `main opportunity + current caveat`, not a floor-only label
5. In the final answer, paste the full **summary.md** body verbatim (the bundle's primary deliverable), then list the per-source paths under it as drill-down references. The same rules in **Final answer format** above apply — substitute `summary.md` for the per-source `.md`.

## Storage layout

Canonical storage paths:

- `~/.oh-my-agent/reports/deals-scanner/daily/<date>/summary.md|json`
- `~/.oh-my-agent/reports/deals-scanner/daily/<date>/references/<source>.md|json`
- `~/.oh-my-agent/reports/deals-scanner/weekly/<iso-week>/all-sources.md|json`

Use the helper script for path generation and persistence. Do not hand-roll paths.

## Report structure

Read the relevant reference before drafting:

- Deal schemas: `references/deal_schema.md`
- Source policy: `references/source_policy.md`
- Prompt recipes: `references/prompt_recipes.md`

### Daily report structures

- `summary` (internal daily aggregation target for broad scans)
  - 今日判断
  - Apply now
  - Buy now
  - Stack now
  - Watchlist
  - 各渠道快照
  - Coverage / Confidence
  - Reference 索引
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
  - 服饰 / 户外 / 其他值得关注
- `dealmoon`
  - 今日精选折扣
  - 独家折扣码与优惠
  - 美妆个护
  - 电子数码
  - 家居生活

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
- `report_timezone`
- `report_date`
- `period_start`
- `period_end`
- `lookback_window_days`
- `summary`
- `top_deals`
- `source_mix_note`
- `sources`
- `sections`

For daily reference reports, also include:

- `lower_confidence_watchlist`
- `high_confidence_count`
- `coverage_floor_met`

For `source=summary`, also include:

- `action_buckets`
- `source_snapshots`
- `coverage_status`

Every summary source snapshot should include its own `lookback_window_days`.

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
  "notes": "",
  "carryover": false
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
- `summary.md` should read like a decisive morning brief, not a directory page.
- `summary.md` should open with `简短结论：` and use `2` short sentences rather than a slogan.
- The first `简短结论` sentence should state today's priority and `1-2` concrete drivers.
- The second `简短结论` sentence should state the main limitation / risk / coverage caveat.
- `summary.md` should keep the main decision payload in `Apply now` / `Buy now` / `Stack now`; `各渠道快照` is supportive, not the main body.
- Each source snapshot should describe the strongest recent opportunity plus the main caveat / floor state in one compact sentence. Do not reduce it to labels like `达到 floor` or `仅观察`.
- Each deal should include: deal title, value/discount amount, expiration info, link, and a brief quality assessment.
- JSON should stay compact and structured for later machine reuse.
- Daily source scans should aim for `12-15` verified items and should not stop below `10` unless the source genuinely lacks enough credible candidates that day.
- The `10` item floor applies to high-confidence items.
- If a source cannot produce at least `10` high-confidence items:
  - do not pad the main body with low-quality filler
  - place weaker candidates in `lower_confidence_watchlist`
  - say so explicitly in the summary `Coverage / Confidence` section and in that source's `来源与说明`
- Source windows apply only to the main daily body:
  - `credit-cards/uscardforum/rakuten` mainline items should come from the recent `3` day window
  - `slickdeals/dealmoon` mainline items can use the recent `7` day window
- If an older item still matters but falls outside that source's mainline window:
  - do not put it in `Apply now` / `Buy now` / `Stack now`
  - place it in `Watchlist`
  - mark it as a carryover / 超窗延续项 in `notes` or `carryover=true`
- Core sections should generally carry at least `2-3` items each; do not let one section absorb everything while others stay empty unless the source genuinely lacks coverage.
- The broad daily bundle should always produce the day-level `summary.md|json` in addition to per-source references.
- If a source has no notable deals today, say so explicitly rather than padding with filler.
- Weekly trend claims must be grounded in stored daily report files, not vague memory.
- Deal links should default to concrete deal pages, forum threads, or issuer / merchant product pages. Homepage or list-page links belong only in supporting citations or `lower_confidence_watchlist`.
- `Dealmoon` should stay focused on beauty, electronics, and home. Do not let apparel dominate its daily scan unless it clearly breaks into the overall top picks.
- `Slickdeals` should remain broad across frontpage, tech, home, and other high-signal categories rather than narrowing into a pure tech feed.
- `credit-cards` top items should be cross-checked with issuer official pages or other concrete sources when feasible.
- `Rakuten` should prefer merchant-level entry pages or concrete merchant offers. `allstores` is overview-only and should not be the only link for a top item.
- The helper owns `generated_at`, `report_timezone`, and `report_date`; do not invent placeholder metadata values in the model output.

## Research approach

This skill uses web research (search), not automated scraping. The agent searches for current deals using the strategies defined in `references/source_policy.md`. Do not build or invoke scraping scripts.

## Prompting notes

- When the user asks for a reusable prompt, use `references/prompt_recipes.md`.
- For automation prompts, keep the mode/source/date explicit in the prompt text.
- For broad daily prompts, prefer the bundle workflow (`summary + references`) rather than one giant monolithic Markdown file.
