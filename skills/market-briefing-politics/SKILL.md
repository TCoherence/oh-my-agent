---
name: market-briefing-politics
description: Produce Chinese-first politics market briefings (China central policy / US federal policy / China-US geopolitics / impact assessment) with persisted Markdown and JSON outputs under ~/.oh-my-agent/reports/market-briefing/. Use for daily politics digests and bounded historical politics bootstrap dossiers.
metadata:
  timeout_seconds: 1500
  max_turns: 60
---

# Market Briefing — Politics

Use this skill for the recurring politics daily briefing and bounded historical politics bootstrap dossiers. This skill is report-centric: it writes durable report files under `~/.oh-my-agent/reports/market-briefing/` so later weekly synthesis (handled by `market-briefing-weekly`) can build on stored report history instead of relying on Discord chat history.

## When to use

- User wants a politics daily report (China central policy / US federal policy / China-US geopolitics).
- User wants a bounded historical backfill to seed future politics reporting.
- User wants automation-ready prompts or templates for recurring politics market briefings.

If the user asks for AI, finance, or cross-domain weekly, prefer the sibling skills (`market-briefing-ai` / `market-briefing-finance` / `market-briefing-weekly`).

## Mode/date discipline

- Always make `mode` and `report date` explicit in the working plan (`domain` is fixed to `politics` for this skill).
- If no report date is specified, default to the current local date of the runtime environment (derived from `OMA_REPORT_TIMEZONE` or `TZ` when set) and state it explicitly in the title and JSON metadata.
- Do not invent a future date unless the user explicitly requests a future-dated planning memo.

## Modes

- `daily_digest`
  - Generate one daily politics report.
- `bootstrap_backfill`
  - Build one bounded historical politics dossier. Default backfill window: **30 days**. Do **not** generate fake historical daily files.

## Required workflow

1. Pick the explicit mode (`daily_digest` is the common case).
2. Load prior stored context with the helper script:
   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing-politics/scripts/report_store.py context --mode daily_digest --domain politics
   ```
3. Generate a starter Markdown + JSON scaffold.
4. Do external research for the requested mode.
5. Fill the Markdown + JSON with the researched content.
6. Persist both files into the canonical report store:
   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing-politics/scripts/report_store.py persist \
       --mode daily_digest --domain politics \
       --markdown-file /tmp/politics.md --json-file /tmp/politics.json
   ```
7. Output the report — see **Final answer format** below. (This is mandatory; the user only sees your final assistant message.)

## Final answer format

The full Markdown report is on disk; **do NOT re-paste it verbatim in chat**. Re-streaming a 5–30 KB report as output tokens wastes wall-clock budget late in the run (real incident: weekly `bdcf9908d735` 2026-05-03 — persist succeeded at 18:16, the trailing chat-body re-stream was killed by the 1500s wall at 18:22). The proper systemic fix lives in the runtime backlog under "Long-output final delivery" — until that lands, return a structured chat summary that gives the user enough to act without opening the file.

**Required content in the chat reply:**

1. **Headline conclusion (1–3 sentences)**: today's main politics read — the call/judgment, plus the single most important driver and its main caveat.
2. **Per-section highlights (one short bullet per section in the canonical politics order)**: 1–2 sentences each — 中国中央政策 / 美国联邦政策 / 中美地缘 / 影响判断.
3. **Top picks / signals (3–5 highest-impact items)**: paste the actual entries with their inline citations from the body — these are what the reader needs to see in chat without opening the file.
4. **Coverage notes**: any non-empty `coverage_gaps` / `confidence_flags` / source-mix caveats from the JSON, in 1–2 sentences. Skip if empty.
5. **Storage paths** at the end (the published `.md` / `.json` pair).

Layout:

```
<headline conclusion>

**[Politics] 各 section 速览**

- 中国中央政策: <1–2 sentences>
- 美国联邦政策: <1–2 sentences>
- 中美地缘: <1–2 sentences>
- 影响判断: <1–2 sentences>

**Top picks**

- <entry 1, with inline link>
- <entry 2, with inline link>
- <entry 3, with inline link>

**Coverage notes** (skip if empty)

- <gap 1>
- <flag 1>

📁 Stored at:
- ~/.oh-my-agent/reports/market-briefing/daily/<date>/politics.md
- ~/.oh-my-agent/reports/market-briefing/daily/<date>/politics.json
```

❌ Don't end the turn with "Done.", "Report saved.", or a short progress summary — those are status notes, not the answer.
❌ Don't reply with ONLY the storage path — the user can't open files in Discord; they need the summary above.
❌ Don't paste the full Markdown body verbatim — that's wasted output tokens and wall-clock; the file is the canonical artifact.
❌ Don't drop the per-section block in favor of a vague "今日整体平稳" — the reader wants the section-by-section read.
✅ The summary above gives the reader enough to make a decision without opening the file; the file is for full detail + citations.

## Storage layout

Canonical storage paths:

- `~/.oh-my-agent/reports/market-briefing/bootstrap/politics/<date>.md|json`
- `~/.oh-my-agent/reports/market-briefing/daily/<date>/politics.md|json`

Use the helper script for path generation and persistence. Do not hand-roll paths unless you are patching the helper itself.

## Report structure

Read the relevant references before drafting:

- `references/report_schema.md`
- `references/source_policy.md`
- `references/automation_templates.md`
- `references/prompt_recipes.md`

### Daily report structure

- 中国中央政策 / 决策信号
- 美国联邦政策 / 决策信号
- 中美 / 地缘政治动态
- 影响判断与后续观察点

### Politics vs finance boundary

- `politics`
  - 关注政策文本本身、立法/行政背景、地缘/安全/供应链政治含义
- `finance` (sibling skill `market-briefing-finance`)
  - 关注政策对市场、融资、住房、信用、风险偏好的影响
- 同一政策如果两边都提：
  - politics 写政策与地缘背景
  - finance 写市场影响
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
