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
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing/scripts/podcast_fetch.py --domain ai
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing/scripts/podcast_fetch.py --domain finance
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

## AI daily workflow

For `daily_digest` with `domain=ai`. The AI daily run produces **4 sub-section files plus a final aggregate**, persisted into the canonical store. Sub-sections are persisted **immediately as each one is written** so a max_turns or timeout failure mid-run preserves whatever has already landed; the bumped re-run skips already-complete sections via `section-status`. See [`references/section_schemas.md`](references/section_schemas.md) for the schema contract.

### Steps

0. **(Re-run path) Check for already-complete sub-sections.** Before drafting anything, run:

   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing/scripts/report_store.py section-status \
       --domain ai --report-date <today>
   ```

   Read the `sections` map in the JSON output. For any section with `complete: true`, skip the corresponding research + draft + persist steps below — the file pair is already on disk and will be picked up by the aggregator. Only redo sections marked `complete: false`. (On a fresh first-run all four are missing; this is a no-op.)

1. **Prefetch podcasts** (always run, fast):

   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing/scripts/podcast_fetch.py --domain ai
   ```

   Save the output for the `🎙️ 播客动态` section in the final aggregate. If the script returns an empty array or fails, plan to write `今日订阅播客暂无更新` in the aggregate and move on. Podcasts are not a sub-section — they live only in the final aggregate.

2. **Load historical context + people pool**:

   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing/scripts/report_store.py context --mode daily_digest --domain ai
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing/scripts/ai_people_pool.py context
   ```

3. **Draft + persist each sub-section, immediately**. The 4 sub-sections are largely independent; do NOT batch their persists. Each one is `write Markdown` → `write JSON matching schema` → `persist-section` → move to next.

   For each section in `[frontier_radar, paper_layer, people_pool, macro_news]` (skip if step 0 marked it complete):

   ```bash
   # 1. Write the section's draft to /tmp scratch files
   #    (use the schema in references/section_schemas.md)
   # 2. Persist immediately:
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing/scripts/report_store.py persist-section \
       --domain ai --report-date <today> --section <name> \
       --markdown-file /tmp/ai_<name>.md --json-file /tmp/ai_<name>.json
   ```

   Section-specific guidance:

   - **`frontier_radar`** — 8 labs (`OpenAI`, `Anthropic`, `Google DeepMind`, `Meta`, `xAI`, `Mistral`, `Qwen`, `DeepSeek`). For each, do at most **1 grouped WebSearch** for the day's signals (you may search multiple labs at once). Body anchor: `labs[]`. Verified signals → `signals[]` with `verified: true`; unverified rumours → top-level `unverified_frontier_signals`.
   - **`paper_layer`** — **Read paper-digest's daily JSON first**: `~/.oh-my-agent/reports/paper-digest/daily/<today>.json`. Use `top_picks[].{arxiv_id, title, tldr_cn, arxiv_url}` directly to populate `papers_consumed_from_paper_digest[]`. **Do not WebSearch arXiv papers** — that's paper-digest's job. If the file is missing or older than today, set `paper_digest_status` accordingly, leave `papers_consumed_from_paper_digest: []`, and add `paper_digest_unavailable` (or `paper_digest_stale`) to `coverage_gaps`. Add at most 2-3 entries to `technical_signals[]` for technical news that paper-digest legitimately would not cover (e.g., a tooling release, an open-weights drop without a paper).
   - **`people_pool`** — Load `references/ai_people_seed.yaml` and `ai_people_pool.py context` output. Research tracked people + community signals → `tracked_people_signals[]`. Run the bounded discovery sweep (see "People discovery rules" below) → `new_candidate_people[]`. Body anchor: `people_signal_summary` (string).
   - **`macro_news`** — 5-layer signals across `energy`, `chips`, `infra`, `model`, `application`, plus `cross_layer_links[]`. **Do not duplicate** what `paper_layer` already covered (the aggregator will dedup, but writing the same paper twice wastes turns). Body anchor: `five_layer_signals` (object with the 5 layer keys).

4. **Final aggregation step** — read the 4 sub-section JSONs back from disk and write the legacy `ai.md` + `ai.json` (the user-facing single-file output preserved for backward compat + weekly synthesis). The aggregate Markdown follows the existing AI daily structure in [`references/report_schema.md`](references/report_schema.md). Persist via:

   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing/scripts/report_store.py persist \
       --mode daily_digest --domain ai --report-date <today> \
       --markdown-file /tmp/ai.md --json-file /tmp/ai.json
   ```

   The legacy `persist` command **still auto-records the AI people pool** — no separate manual step needed.

5. **Output** — see "Final answer format" above.

### Execution strategy (parallel preferred)

The 4 sub-sections in step 3 are largely independent — `paper_layer` depends only on paper-digest's prior JSON; the others touch separate sources (frontier-lab announcements / tracked-people activity / 5-layer macro news). If your runtime exposes a sub-agent / Task / Agent tool (Claude Code's `Task` tool, Gemini's `@agent_name`, or equivalent), prefer fanning out the 4 section drafts as parallel sub-agent calls so each gets its own context window. Each sub-agent should:

- be told its single `section` (one of the 4 names above)
- own the full per-section workflow end-to-end (research → JSON + Markdown matching the schema → `persist-section`)
- return only a short success/failure summary + the persisted file paths

The cross-section dedupe (e.g., a paper appearing in both `paper_layer` and `macro_news.model`) happens in the **final aggregation step (step 4)**, which reads the 4 per-section JSONs back from disk — so isolated sub-agent contexts do **not** weaken cross-section coverage.

If sub-agent tooling is not available, fall back to running the 4 section drafts sequentially in the parent context — same outputs, same filesystem layout, just slower. Even sequential, the per-section persist preserves the checkpoint-recovery property: a max_turns or timeout failure mid-run leaves complete sections on disk for the bumped re-run to skip.

Use `references/sync-repo` for explicit curated people-pool maintenance only. Do not rewrite the repo seed file during a normal daily run.

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
