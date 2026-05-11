---
name: market-briefing-weekly
description: Produce Chinese-first cross-domain weekly synthesis briefings that absorb the last 7 days of stored politics / finance / ai daily reports plus latest bootstrap dossiers, persisted as Markdown + JSON under ~/.oh-my-agent/reports/market-briefing/weekly/. Reads stored daily reports — does NOT do real-time podcast or news fetch. Use when the user asks for a weekly cross-domain synthesis or roll-up.
metadata:
  timeout_seconds: 1500
  max_turns: 60
---

# Market Briefing — Weekly cross-domain synthesis

Use this skill for the recurring cross-domain weekly synthesis briefing. This skill is report-centric: it reads durable daily report files under `~/.oh-my-agent/reports/market-briefing/` written by the sibling skills (`market-briefing-ai` / `market-briefing-finance` / `market-briefing-politics`) and synthesises one cross-domain weekly report.

## When to use

- User wants a weekly cross-domain synthesis across politics, finance, and AI.
- User wants automation-ready prompts or templates for a recurring weekly market briefing.

If the user asks for a single-domain daily, prefer the sibling skills (`market-briefing-ai` / `market-briefing-finance` / `market-briefing-politics`).

## Mode/date discipline

- Always make `mode` and `report date` (ISO week) explicit in the working plan (`domain` is fixed to `cross-domain` for this skill).
- If no report date is specified, default to the current local ISO week of the runtime environment (derived from `OMA_REPORT_TIMEZONE` or `TZ` when set) and state it explicitly in the title and JSON metadata.
- Do not invent a future ISO week unless the user explicitly requests a future-dated planning memo.

## Modes

- `weekly_synthesis`
  - Generate one cross-domain weekly report using recent stored daily reports plus bootstrap context.

## Required workflow

1. Lock the explicit ISO week.
2. Load prior stored context with the helper script — this pulls last 7 days of daily reports plus latest bootstrap dossiers for each domain:
   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing-weekly/scripts/report_store.py context --mode weekly_synthesis --domain cross-domain
   ```
3. Generate a starter Markdown + JSON scaffold.
4. Synthesise cross-domain narrative from the stored daily reports — focus on structure, trend, and continuity rather than repeating raw headlines. **Read stored daily reports; do NOT re-fetch real-time podcasts or news.** Real-time freshness is the daily skills' responsibility.
5. Fill the Markdown + JSON with the synthesised content.
6. Persist both files into the canonical report store:
   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing-weekly/scripts/report_store.py persist \
       --mode weekly_synthesis --domain cross-domain \
       --markdown-file /tmp/cross-domain.md --json-file /tmp/cross-domain.json
   ```
7. Output the report — see **Final answer format** below. (This is mandatory; the user only sees your final assistant message.)

## Final answer format

The full Markdown report is on disk; **do NOT re-paste it verbatim in chat**. Re-streaming a 5–30 KB report as output tokens wastes wall-clock budget late in the run (real incident: weekly `bdcf9908d735` 2026-05-03 — persist succeeded at 18:16, the trailing chat-body re-stream was killed by the 1500s wall at 18:22). The proper systemic fix lives in the runtime backlog under "Long-output final delivery" — until that lands, return a structured chat summary that gives the user enough to act without opening the file.

**Required content in the chat reply:**

1. **Headline conclusion (1–3 sentences)**: this week's main cross-domain read — the call/judgment, plus the single most important driver and its main caveat.
2. **Per-section highlights**: cross-domain trend summary + the 3–5 strongest cross-references between domains.
3. **Top picks / signals (3–5 highest-impact items)**: paste the actual entries with their inline citations from the body — these are what the reader needs to see in chat without opening the file.
4. **Coverage notes**: any non-empty `coverage_gaps` / `confidence_flags` / source-mix caveats from the JSON, in 1–2 sentences. Skip if empty.
5. **Storage paths** at the end (the published `.md` / `.json` pair).

Layout:

```
<headline conclusion>

**[Weekly cross-domain] 速览**

- 跨域趋势: <1–2 sentences>
- 跨域链接: <1–2 sentences per strongest cross-reference>
- ... (3–5 cross-references total)

**Top picks**

- <entry 1, with inline link>
- <entry 2, with inline link>
- <entry 3, with inline link>

**Coverage notes** (skip if empty)

- <gap 1>
- <flag 1>

📁 Stored at:
- ~/.oh-my-agent/reports/market-briefing/weekly/<iso-week>/cross-domain.md
- ~/.oh-my-agent/reports/market-briefing/weekly/<iso-week>/cross-domain.json
```

❌ Don't end the turn with "Done.", "Report saved.", or a short progress summary — those are status notes, not the answer.
❌ Don't reply with ONLY the storage path — the user can't open files in Discord; they need the summary above.
❌ Don't paste the full Markdown body verbatim — that's wasted output tokens and wall-clock; the file is the canonical artifact.
❌ Don't drop the per-section block in favor of a vague "本周整体平稳" — the reader wants the cross-domain reads.
✅ The summary above gives the reader enough to make a decision without opening the file; the file is for full detail + citations.

## Storage layout

Canonical storage paths:

- `~/.oh-my-agent/reports/market-briefing/weekly/<iso-week>/cross-domain.md|json`

Reads from (written by sibling daily skills):

- `~/.oh-my-agent/reports/market-briefing/daily/<date>/{ai,finance,politics}.md|json` for the past 7 days
- `~/.oh-my-agent/reports/market-briefing/bootstrap/{ai,finance,politics}/<date>.md|json` (latest dossier per domain)

Use the helper script for path generation and persistence. Do not hand-roll paths unless you are patching the helper itself.

## Report structure

Read the relevant references before drafting:

- `references/report_schema.md`
- `references/source_policy.md`
- `references/automation_templates.md`
- `references/prompt_recipes.md`

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
