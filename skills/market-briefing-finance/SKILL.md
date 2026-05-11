---
name: market-briefing-finance
description: Produce Chinese-first finance market briefings (China macro / US macro / US volatility / China-HK market / property policy / tracked holdings / podcasts) with persisted Markdown and JSON outputs under ~/.oh-my-agent/reports/market-briefing/. Includes podcast prefetch from subscribed finance channels. Use for daily finance digests and bounded historical finance bootstrap dossiers.
metadata:
  timeout_seconds: 1500
  max_turns: 60
---

# Market Briefing — Finance

Use this skill for the recurring finance daily briefing and bounded historical finance bootstrap dossiers. This skill is report-centric: it writes durable report files under `~/.oh-my-agent/reports/market-briefing/` so later weekly synthesis (handled by `market-briefing-weekly`) can build on stored report history instead of relying on Discord chat history.

## When to use

- User wants a finance daily report (China macro / US macro / US volatility / China-HK market / property policy / tracked holdings).
- User wants a bounded historical backfill to seed future finance reporting.
- User wants automation-ready prompts or templates for recurring finance market briefings.

If the user asks for AI, politics, or cross-domain weekly, prefer the sibling skills (`market-briefing-ai` / `market-briefing-politics` / `market-briefing-weekly`).

## Mode/date discipline

- Always make `mode` and `report date` explicit in the working plan (`domain` is fixed to `finance` for this skill).
- If no report date is specified, default to the current local date of the runtime environment (derived from `OMA_REPORT_TIMEZONE` or `TZ` when set) and state it explicitly in the title and JSON metadata.
- Do not invent a future date unless the user explicitly requests a future-dated planning memo.

## Modes

- `daily_digest`
  - Generate one daily finance report.
- `bootstrap_backfill`
  - Build one bounded historical finance dossier. Default backfill window: **30 days**. Do **not** generate fake historical daily files.

## Required workflow

1. Pick the explicit mode (`daily_digest` is the common case).
2. **Prefetch podcasts** — run the podcast fetch script to get latest episodes from subscribed finance channels:
   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing-finance/scripts/podcast_fetch.py --domain finance
   ```
   The script outputs a JSON array of episodes updated within the last 48 hours. Use this output directly for the `🎙️ 播客动态` section — do not run a separate web search for podcasts.
   If the script returns an empty array or fails, write "今日订阅播客暂无更新" in the podcast section and move on.
3. Load prior stored context with the helper script:
   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing-finance/scripts/report_store.py context --mode daily_digest --domain finance
   ```
4. Generate a starter Markdown + JSON scaffold.
5. Do external research for the requested mode.
6. Fill the Markdown + JSON with the researched content (include prefetched podcast data).
7. Persist both files into the canonical report store:
   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing-finance/scripts/report_store.py persist \
       --mode daily_digest --domain finance \
       --markdown-file /tmp/finance.md --json-file /tmp/finance.json
   ```
8. Output the report — see **Final answer format** below. (This is mandatory; the user only sees your final assistant message.)

## Final answer format

The full Markdown report is on disk; **do NOT re-paste it verbatim in chat**. Re-streaming a 5–30 KB report as output tokens wastes wall-clock budget late in the run (real incident: weekly `bdcf9908d735` 2026-05-03 — persist succeeded at 18:16, the trailing chat-body re-stream was killed by the 1500s wall at 18:22). The proper systemic fix lives in the runtime backlog under "Long-output final delivery" — until that lands, return a structured chat summary that gives the user enough to act without opening the file.

**Required content in the chat reply:**

1. **Headline conclusion (1–3 sentences)**: today's main finance read — the call/judgment, plus the single most important driver and its main caveat.
2. **Per-section highlights (one short bullet per section in the canonical finance order)**: 1–2 sentences each — 中国宏观 / 美国宏观 / 美国波动 / 中港市场 / 持仓动态 / 播客.
3. **Top picks / signals (3–5 highest-impact items)**: paste the actual entries with their inline citations from the body — these are what the reader needs to see in chat without opening the file.
4. **Coverage notes**: any non-empty `coverage_gaps` / `confidence_flags` / source-mix caveats from the JSON, in 1–2 sentences. Skip if empty.
5. **Storage paths** at the end (the published `.md` / `.json` pair).

Layout:

```
<headline conclusion>

**[Finance] 各 section 速览**

- 中国宏观: <1–2 sentences>
- 美国宏观: <1–2 sentences>
- 美国波动: <1–2 sentences>
- 中港市场: <1–2 sentences>
- 持仓动态: <1–2 sentences>
- 播客: <1–2 sentences>

**Top picks**

- <entry 1, with inline link>
- <entry 2, with inline link>
- <entry 3, with inline link>

**Coverage notes** (skip if empty)

- <gap 1>
- <flag 1>

📁 Stored at:
- ~/.oh-my-agent/reports/market-briefing/daily/<date>/finance.md
- ~/.oh-my-agent/reports/market-briefing/daily/<date>/finance.json
```

❌ Don't end the turn with "Done.", "Report saved.", or a short progress summary — those are status notes, not the answer.
❌ Don't reply with ONLY the storage path — the user can't open files in Discord; they need the summary above.
❌ Don't paste the full Markdown body verbatim — that's wasted output tokens and wall-clock; the file is the canonical artifact.
❌ Don't drop the per-section block in favor of a vague "今日整体平稳" — the reader wants the section-by-section read.
✅ The summary above gives the reader enough to make a decision without opening the file; the file is for full detail + citations.

## Storage layout

Canonical storage paths:

- `~/.oh-my-agent/reports/market-briefing/bootstrap/finance/<date>.md|json`
- `~/.oh-my-agent/reports/market-briefing/daily/<date>/finance.md|json`

Use the helper script for path generation and persistence. Do not hand-roll paths unless you are patching the helper itself.

## Report structure

Read the relevant references before drafting:

- `references/report_schema.md`
- `references/source_policy.md`
- `references/finance_watchlist.md`
- `references/podcast_feeds.yaml`
- `references/automation_templates.md`
- `references/prompt_recipes.md`

### Daily report structure

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

### Politics vs finance boundary

- `finance`
  - 关注政策对市场、融资、住房、信用、风险偏好的影响
- `politics` (sibling skill `market-briefing-politics`)
  - 关注政策文本本身、立法/行政背景、地缘/安全/供应链政治含义
- 同一政策如果两边都提：
  - finance 写市场影响
  - politics 写政策与地缘背景
  - 不允许两边写成重复摘要

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

## Podcast section rules

The `🎙️ 播客动态` section is part of the finance daily report.

- Data comes exclusively from `podcast_fetch.py` output — do not web-search for additional podcasts.
- Each item: bold linked `[频道名 — 集名](episode_url)`，followed by 1–2 sentence Chinese summary distilled from the shownotes.
- If prefetch returned zero episodes, write `今日订阅播客暂无更新` and move on.
- Do not fabricate episode content. Only summarize what the shownotes contain.
- Subscribed channels are configured in `references/podcast_feeds.yaml` (this skill only carries the `finance` group). To add/remove channels, edit the YAML — no code changes needed.
