---
name: market-briefing-ai
description: Produce Chinese-first AI market briefings (frontier labs / paper layer / people pool / 5-layer macro) with persisted Markdown and JSON outputs under ~/.oh-my-agent/reports/market-briefing/. The daily run produces 4 sub-section files plus a final aggregate; sub-sections persist immediately so a timeout mid-run preserves whatever landed. Reads paper-digest's daily JSON directly instead of re-searching arXiv. Use for daily AI digests and bounded historical AI bootstrap dossiers.
metadata:
  timeout_seconds: 1500
  max_turns: 60
---

# Market Briefing — AI

Use this skill for the recurring AI daily briefing and bounded historical AI bootstrap dossiers. This skill is report-centric: it writes durable report files under `~/.oh-my-agent/reports/market-briefing/` so later weekly synthesis (handled by `market-briefing-weekly`) can build on stored report history instead of relying on Discord chat history.

## When to use

- User wants an AI daily report (frontier labs / papers / people / 5-layer / cross-layer).
- User wants a bounded historical backfill to seed future AI reporting.
- User wants automation-ready prompts or templates for recurring AI market briefings.

If the user asks for finance, politics, or cross-domain weekly, prefer the sibling skills (`market-briefing-finance` / `market-briefing-politics` / `market-briefing-weekly`).

## Mode/date discipline

- Always make `mode` and `report date` explicit in the working plan (`domain` is fixed to `ai` for this skill).
- If no report date is specified, default to the current local date of the runtime environment (derived from `OMA_REPORT_TIMEZONE` or `TZ` when set) and state it explicitly in the title and JSON metadata.
- Do not invent a future date unless the user explicitly requests a future-dated planning memo.

## Modes

- `daily_digest`
  - Generate one daily AI report.
- `bootstrap_backfill`
  - Build one bounded historical AI dossier. Default backfill window: **14 days**. Do **not** generate fake historical daily files.

## Required workflow

1. Pick the explicit mode (`daily_digest` is the common case).
2. **Prefetch podcasts** — run the podcast fetch script to get latest episodes from subscribed AI channels:
   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing-ai/scripts/podcast_fetch.py --domain ai
   ```
   The script outputs a JSON array of episodes updated within the last 48 hours. Use this output directly for the `🎙️ 播客动态` section — do not run a separate web search for podcasts.
   If the script returns an empty array or fails, write "今日订阅播客暂无更新" in the podcast section and move on.
3. Load prior stored context with the helper script (see workflow below for the specific invocation).
4. Generate a starter Markdown + JSON scaffold.
5. Do external research for the requested mode.
6. Fill the Markdown + JSON with the researched content (include prefetched podcast data).
7. Persist both files into the canonical report store.
8. Output the report — see **Final answer format** below. (This is mandatory; the user only sees your final assistant message.)

## Final answer format

The full Markdown report is on disk; **do NOT re-paste it verbatim in chat**. Re-streaming a 5–30 KB report as output tokens wastes wall-clock budget late in the run (real incident: weekly `bdcf9908d735` 2026-05-03 — persist succeeded at 18:16, the trailing chat-body re-stream was killed by the 1500s wall at 18:22). The proper systemic fix lives in the runtime backlog under "Long-output final delivery" — until that lands, return a structured chat summary that gives the user enough to act without opening the file.

**Required content in the chat reply:**

1. **Headline conclusion (1–3 sentences)**: today's main AI read — the call/judgment, plus the single most important driver and its main caveat.
2. **Per-section highlights (one short bullet per section in the canonical AI order)**: 1–2 sentences each — frontier_radar / paper_layer / people_pool / 5-layer / cross-layer.
3. **Top picks / signals (3–5 highest-impact items)**: paste the actual entries with their inline citations from the body — these are what the reader needs to see in chat without opening the file.
4. **Coverage notes**: any non-empty `coverage_gaps` / `confidence_flags` / source-mix caveats from the JSON, in 1–2 sentences. Skip if empty.
5. **Storage paths** at the end (the published `.md` / `.json` pair).

Layout:

```
<headline conclusion>

**[AI] 各 section 速览**

- frontier_radar: <1–2 sentences>
- paper_layer: <1–2 sentences>
- people_pool: <1–2 sentences>
- 5-layer: <1–2 sentences>
- cross-layer: <1–2 sentences>

**Top picks**

- <entry 1, with inline link>
- <entry 2, with inline link>
- <entry 3, with inline link>

**Coverage notes** (skip if empty)

- <gap 1>
- <flag 1>

📁 Stored at:
- ~/.oh-my-agent/reports/market-briefing/daily/<date>/ai.md
- ~/.oh-my-agent/reports/market-briefing/daily/<date>/ai.json
```

❌ Don't end the turn with "Done.", "Report saved.", or a short progress summary — those are status notes, not the answer.
❌ Don't reply with ONLY the storage path — the user can't open files in Discord; they need the summary above.
❌ Don't paste the full Markdown body verbatim — that's wasted output tokens and wall-clock; the file is the canonical artifact.
❌ Don't drop the per-section block in favor of a vague "今日整体平稳" — the reader wants the section-by-section read.
✅ The summary above gives the reader enough to make a decision without opening the file; the file is for full detail + citations.

## Storage layout

Canonical storage paths:

- `~/.oh-my-agent/reports/market-briefing/bootstrap/ai/<date>.md|json`
- `~/.oh-my-agent/reports/market-briefing/daily/<date>/ai.md|json`
- `~/.oh-my-agent/reports/market-briefing/daily/<date>/ai_sections/<section>.md|json` (4 sub-sections)

Use the helper script for path generation and persistence. Do not hand-roll paths unless you are patching the helper itself.

## Report structure

Read the relevant references before drafting:

- `references/report_schema.md`
- `references/source_policy.md`
- `references/ai_frontier_watchlist.md`
- `references/ai_people_seed.yaml`
- `references/section_schemas.md`
- `references/podcast_feeds.yaml`
- `references/automation_templates.md`
- `references/prompt_recipes.md`

### Daily report structure

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

In every command below, omit `--report-date` so the script uses today's local date (resolved via `OMA_REPORT_TIMEZONE` / `TZ` / system). Pass `--report-date YYYY-MM-DD` only when you need to override (e.g., a backfill run for a specific past day).

0. **(Re-run path) Check for already-complete sub-sections.** Before drafting anything, run:

   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing-ai/scripts/report_store.py section-status \
       --domain ai
   ```

   Read the `sections` map in the JSON output. For any section with `complete: true`, skip the corresponding research + draft + persist steps below — the file pair is already on disk and will be picked up by the aggregator. Only redo sections marked `complete: false`. (On a fresh first-run all four are missing; this is a no-op.)

1. **Prefetch podcasts** (always run, fast):

   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing-ai/scripts/podcast_fetch.py --domain ai
   ```

   Save the output for the `🎙️ 播客动态` section in the final aggregate. If the script returns an empty array or fails, plan to write `今日订阅播客暂无更新` in the aggregate and move on. Podcasts are not a sub-section — they live only in the final aggregate.

2. **Load historical context + people pool**:

   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing-ai/scripts/report_store.py context --mode daily_digest --domain ai
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing-ai/scripts/ai_people_pool.py context
   ```

3. **Draft + persist each sub-section, immediately**. The 4 sub-sections are largely independent; do NOT batch their persists. Each one is `write Markdown` → `write JSON matching schema` → `persist-section` → move to next.

   For each section in `[frontier_radar, paper_layer, people_pool, macro_news]` (skip if step 0 marked it complete):

   ```bash
   # 1. Write the section's draft to /tmp scratch files
   #    (use the schema in references/section_schemas.md)
   # 2. Persist immediately:
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing-ai/scripts/report_store.py persist-section \
       --domain ai --section <name> \
       --markdown-file /tmp/ai_<name>.md --json-file /tmp/ai_<name>.json
   ```

   Section-specific guidance:

   - **`frontier_radar`** — 8 labs (`OpenAI`, `Anthropic`, `Google DeepMind`, `Meta`, `xAI`, `Mistral`, `Qwen`, `DeepSeek`). For each, do at most **1 grouped WebSearch** for the day's signals (you may search multiple labs at once). Body anchor: `labs[]`. Verified signals → `signals[]` with `verified: true`; unverified rumours → top-level `unverified_frontier_signals`.
   - **`paper_layer`** — **Read paper-digest's daily JSON first**: `~/.oh-my-agent/reports/paper-digest/daily/<TODAY>.json` (use the same `<TODAY>` ISO date as your `section-status` call resolved to). Use `top_picks[].{arxiv_id, title, tldr_cn, arxiv_url}` directly to populate `papers_consumed_from_paper_digest[]`. **Do not WebSearch arXiv papers** — that's paper-digest's job. If the file is missing or older than today, set `paper_digest_status` accordingly, leave `papers_consumed_from_paper_digest: []`, and add `paper_digest_unavailable` (or `paper_digest_stale`) to `coverage_gaps`. Add at most 2-3 entries to `technical_signals[]` for technical news that paper-digest legitimately would not cover (e.g., a tooling release, an open-weights drop without a paper).
   - **`people_pool`** — Load `references/ai_people_seed.yaml` and `ai_people_pool.py context` output. Research tracked people + community signals → `tracked_people_signals[]`. Run the bounded discovery sweep (see "People discovery rules" below) → `new_candidate_people[]`. Body anchor: `people_signal_summary` (string).
   - **`macro_news`** — 5-layer signals across `energy`, `chips`, `infra`, `model`, `application`, plus `cross_layer_links[]`. **Do not duplicate** what `paper_layer` already covered (the aggregator will dedup, but writing the same paper twice wastes turns). Body anchor: `five_layer_signals` (object with the 5 layer keys).

4. **Final aggregation step** — read the 4 sub-section JSONs back from disk and write the legacy `ai.md` + `ai.json` (the user-facing single-file output preserved for backward compat + weekly synthesis). The legacy AI daily Markdown structure is fixed at 11 H2 sections in [`references/report_schema.md`](references/report_schema.md); thread the 4 sub-section payloads into them as follows:

   | Legacy `ai.md` H2 section | Source sub-section + JSON path |
   | --- | --- |
   | `## 摘要` | Synthesise from all 4 sub-section JSONs (1-2 sentences each layer's headline, plus your overall day-frame) |
   | `## Frontier Labs / Frontier Model Radar` | `frontier_radar.json` — render `frontier_signal_summary` as a lead paragraph, then per-lab `signals[]`. Append `unverified_frontier_signals[]` as a clearly-labelled tail block. |
   | `## 关键人物与社区信号` | `people_pool.json` `people_signal_summary` + `tracked_people_signals[]` |
   | `## Energy` / `## Chips` / `## Infra` / `## Model` / `## Application` | `macro_news.json` `five_layer_signals.{energy, chips, infra, model, application}` |
   | (folded INTO `## Model`) | `paper_layer.json` `papers_consumed_from_paper_digest[]` — render as a sub-list at the **end** of `## Model`, each entry `- [<arxiv_id>](arxiv_url) <title> — <tldr_cn>`. **No new H2 section.** |
   | (folded INTO `## Application`) | `paper_layer.json` `technical_signals[]` — render as a sub-list at the **end** of `## Application`, each entry `- <summary>` with evidence-url citations. **No new H2 section.** |
   | `## 层间联动影响` | `macro_news.json` `cross_layer_links[]` |
   | `## 🎙️ 播客动态` | `podcast_fetch.py` output from step 1 (NOT a sub-section JSON) |
   | `## 候选池变化与后续关注` | `people_pool.json` `new_candidate_people[]` + `promoted_people[]` + `candidate_queue_summary` |
   | `## 来源与交叉验证说明` | Synthesise from all 4 sub-sections' `coverage_gaps[]` + `confidence_flags[]` + the bibliography of cited URLs |

   The aggregate `ai.json` should re-flatten the same content into the legacy AI daily JSON shape (`frontier_lab_watch`, `tracked_people`, `new_candidate_people`, etc. per `references/report_schema.md`) — pull from the 4 sub-section JSONs without re-deriving from web sources.

   Persist via the existing `persist` command (no `--section` flag — this is the final aggregate, not a sub-section):

   ```bash
   ./.venv/bin/python ${OMA_AGENT_HOME}/skills/market-briefing-ai/scripts/report_store.py persist \
       --mode daily_digest --domain ai \
       --markdown-file /tmp/ai.md --json-file /tmp/ai.json
   ```

   The legacy `persist` command **still auto-records the AI people pool** from the aggregate `ai.json` — no separate manual step needed. The 4 sub-section files under `ai_sections/` are NOT consumed by `ai_people_pool.py record`; they exist for checkpoint recovery + downstream composition only.

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

The `🎙️ 播客动态` section is part of the AI daily report.

- Data comes exclusively from `podcast_fetch.py` output — do not web-search for additional podcasts.
- Each item: bold linked `[频道名 — 集名](episode_url)`，followed by 1–2 sentence Chinese summary distilled from the shownotes.
- If prefetch returned zero episodes, write `今日订阅播客暂无更新` and move on.
- Do not fabricate episode content. Only summarize what the shownotes contain.
- Subscribed channels are configured in `references/podcast_feeds.yaml` (this skill only carries the `ai` group). To add/remove channels, edit the YAML — no code changes needed.
