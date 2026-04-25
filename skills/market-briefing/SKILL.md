---
name: market-briefing
description: Produce Chinese-first politics, finance, and AI market briefings with persisted Markdown and JSON outputs under ~/.oh-my-agent/reports/market-briefing/. Use this skill for bounded historical bootstrap dossiers, domain daily digests, and cross-domain weekly synthesis that should reuse prior stored reports rather than relying on Discord history.
metadata:
  timeout_seconds: 1500
  max_turns: 60
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
2. **(AI / finance daily) Prefetch podcasts** — run the podcast fetch script to get latest episodes from subscribed channels:
   ```bash
   ./.venv/bin/python skills/market-briefing/scripts/podcast_fetch.py --domain ai
   ./.venv/bin/python skills/market-briefing/scripts/podcast_fetch.py --domain finance
   ```
   Use `--domain ai` for AI daily, `--domain finance` for finance daily. The script outputs a JSON array of episodes updated within the last 48 hours. Use this output directly for the `🎙️ 播客动态` section — do not run a separate web search for podcasts.
   If the script returns an empty array or fails, write "今日订阅播客暂无更新" in the podcast section and move on.
3. Load prior stored context with the helper script.
4. Generate a starter Markdown + JSON scaffold.
5. Do external research for the requested mode/domain.
6. Fill the Markdown + JSON with the researched content (include prefetched podcast data for AI / finance daily).
7. Persist both files into the canonical report store.
8. Output the report — see **Final answer format** below. (This is mandatory; the user only sees your final assistant message.)

## Final answer format

**You MUST end your turn with the full Markdown report body in your reply** — the same Markdown content you persisted in step 6/7. The Discord user receives only your final assistant message; they cannot see file contents. If you skip this they only see your progress narration ("loading scripts..." / "fetching feeds...") and have no way to read the report you produced.

Layout:

```
<full Markdown report — every section, every bullet, verbatim from the .md you persisted>

📁 Stored at:
- ~/.oh-my-agent/reports/market-briefing/daily/<date>/<domain>.md
- ~/.oh-my-agent/reports/market-briefing/daily/<date>/<domain>.json
```

❌ Don't end the turn with "Done.", "Report saved.", or a short progress summary — those are status notes, not the answer.
❌ Don't reply with only the storage path — the user cannot open files in Discord.
❌ Don't truncate, paraphrase, or "summarize for chat" because the report is long — the gateway auto-chunks messages > 2000 chars across multiple Discord posts, so paste the full body anyway.
✅ The exact Markdown body you wrote to the daily store goes into your reply, verbatim, followed by the storage paths.

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
- `references/podcast_feeds.yaml`
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
  - 🎙️ 播客动态（from prefetch, 48h freshness window）
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
  - 🎙️ 播客动态（from prefetch, 48h freshness window）
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
2. Load the current people pool with `ai_people_pool.py context`.
3. Research:
   - frontier-lab radar
   - five-layer AI developments
   - tracked people / community / X.com signals
   - **people discovery sweep** (see rules below)
4. Fill the AI Markdown + JSON — include `new_candidate_people` and `promoted_people` arrays.
5. Persist the report with `report_store.py persist`. The persist command **automatically calls `ai_people_pool.py record`** for AI daily reports — no separate manual step needed.

Only use `sync-repo` for explicit curated maintenance. Do not rewrite the repo seed file during a normal daily run.

### People discovery rules

Each AI daily report must include a **bounded discovery sweep** for new people beyond the current tracked pool. Aim for **1–3 candidates per report** when signal exists.

**Where to look:**

- X/Twitter threads with high engagement from AI practitioners (especially replies/quotes by existing tracked people)
- Notable podcast guests from the `podcast_fetch.py` output
- Authors of significant papers, tools, or open-source releases cited in the report
- People making verifiable frontier-AI claims or predictions that day
- GitHub trending AI repo authors
- Conference keynote speakers or panelists mentioned in news

**What qualifies as a candidate (`new_candidate_people` entry):**

- Published a concrete artifact (tool, paper, blog post, significant thread) in the last 48h, OR
- Made a verifiable claim about frontier AI backed by evidence, OR
- Was independently mentioned by 2+ tracked people or sources in the same day

**What does NOT qualify:**

- Historical references ("Karpathy once said…")
- Passing mentions without context
- People already in the seed file or tracked pool
- Celebrities/executives mentioned only in market-cap headlines

**Minimum fields per candidate:**

```json
{
  "person_id": "lowercase-hyphenated",
  "name": "Full Name",
  "group": "one of the four groups",
  "reason": "one sentence: what they did and why it matters",
  "evidence_urls": ["at least one URL proving the signal"]
}
```

Optional but valuable: `x_handle`, `role`, `search_terms`, `cross_checked`, `promote_recommended`.

**If no candidates found:** write `本日发现扫描未发现达标候选人` in `candidate_queue_summary` — do NOT force nominations to fill a quota.

## Podcast section rules

The `🎙️ 播客动态` section appears in AI and finance daily reports.

- Data comes exclusively from `podcast_fetch.py` output — do not web-search for additional podcasts.
- Each item: bold linked `[频道名 — 集名](episode_url)`，followed by 1–2 sentence Chinese summary distilled from the shownotes.
- If prefetch returned zero episodes, write `今日订阅播客暂无更新` and move on.
- Do not fabricate episode content. Only summarize what the shownotes contain.
- Subscribed channels are configured in `references/podcast_feeds.yaml`, grouped by domain (`ai`, `finance`). AI daily pulls the `ai` group; finance daily pulls the `finance` group. To add/remove channels, edit the YAML — no code changes needed.
