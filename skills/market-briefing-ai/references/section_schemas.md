# AI daily sub-section schemas

Stage 2.2 (per `plans/market-briefing-daily-ai-0900-fail-patt-mutable-nest`)
introduces sub-section files alongside the legacy `ai.md/.json` daily
report. Each sub-section is persisted **immediately after the agent
finishes writing it**, not batched at the end — so a max_turns or
timeout failure mid-run preserves whatever sections have already
landed, and the re-run with a bumped budget can skip them via
`report_store.py section-status`.

The legacy single-file `ai.md/.json` is **still produced** at the end of
the run as a final aggregation. Downstream consumers (weekly synthesis,
human readers in Discord) keep using it. The sub-section files are
additive: they enable future composition (e.g., a hypothetical
`tech-weekly` skill reading `frontier_radar.json` × 7 days) without
breaking today's contract.

## Layout

```
~/.oh-my-agent/reports/market-briefing/daily/<date>/
├── ai.md              ← legacy single-file output (preserved)
├── ai.json            ← legacy
└── ai_sections/       ← Stage 2.2: per-section sub-reports
    ├── frontier_radar.md / .json
    ├── paper_layer.md / .json
    ├── people_pool.md / .json
    └── macro_news.md / .json
```

`report_store.py persist-section` writes to `ai_sections/<section>.{md,json}`.

`report_store.py section-status` queries which sub-sections are complete
(both `.md` and `.json` exist + JSON parses + JSON has the required keys
listed below).

## Section names + role

| name | covers in legacy ai.md | independent? |
| --- | --- | --- |
| `frontier_radar` | `## Frontier Labs / Frontier Model Radar` | yes — 8-lab signals don't cross-reference each other |
| `paper_layer` | technical signals folded into `## Model` / `## Application` | yes — reads paper-digest JSON, no other sub-section dependency |
| `people_pool` | `## 关键人物与社区信号` + `## 候选池变化与后续关注` | yes — reads `references/ai_people_seed.yaml` + tracked-pool state |
| `macro_news` | `## Energy` / `## Chips` / `## Infra` / `## Model` / `## Application` + `## 层间联动影响` | partly — `Model` / `Application` overlap with `paper_layer` (acceptable; aggregator dedups) |

The summary aggregator (final step that writes `ai.md/.json`) reads
the 4 sub-section JSONs from disk and stitches them, plus prefetched
podcast data, into the legacy output shape. Cross-section dedupe (e.g.,
a paper appearing in both `paper_layer` and `macro_news.model`) happens
in the aggregator, not in any sub-section's own context — sub-sections
are deliberately isolated so their drafting can fan out.

## Required keys per sub-section

Every sub-section JSON shares this header:

```json
{
  "version": 1,
  "section": "<name>",
  "domain": "ai",
  "report_date": "YYYY-MM-DD",
  "generated_at": "<ISO timestamp>",
  "report_timezone": "<TZ name>",
  "coverage_gaps": [],
  "confidence_flags": []
}
```

`section-status` checks the header keys (`version`, `section`,
`domain`, `report_date`) plus one section-specific "body anchor" key
listed below.

### `frontier_radar`

Body anchor: `labs` (array, may be empty).

```json
{
  "frontier_signal_summary": "<2-4 sentence overview>",
  "labs": [
    {
      "lab": "OpenAI",
      "signals": [
        {
          "kind": "product_release | research_paper | leadership_signal | safety_disclosure | other",
          "summary": "<1-2 sentence>",
          "evidence_urls": ["https://..."],
          "verified": true,
          "ts": "YYYY-MM-DD"
        }
      ]
    }
  ],
  "unverified_frontier_signals": []
}
```

### `paper_layer`

Body anchor: `papers_consumed_from_paper_digest` (array, may be empty).

```json
{
  "paper_digest_status": "consumed | missing | stale",
  "paper_digest_path": "/home/.oh-my-agent/reports/paper-digest/daily/YYYY-MM-DD.json",
  "papers_consumed_from_paper_digest": [
    {
      "arxiv_id": "2501.XXXXX",
      "title": "<paper title>",
      "tldr_cn": "<one-sentence Chinese summary>",
      "tldr_en": "<optional EN tldr from S2>",
      "arxiv_url": "https://arxiv.org/abs/...",
      "why_in_market_briefing": "<1-2 sentence: what makes this paper market-relevant beyond paper-digest's own framing>"
    }
  ],
  "technical_signals": [
    {
      "summary": "<technical signal NOT covered by paper-digest>",
      "evidence_urls": ["https://..."]
    }
  ]
}
```

When `paper_digest_status != "consumed"`, leave
`papers_consumed_from_paper_digest: []` and explain in
`coverage_gaps` (e.g., `paper_digest_unavailable`,
`paper_digest_stale`). Do NOT WebSearch arXiv to backfill — that's
paper-digest's job, surface the gap honestly.

### `people_pool`

Body anchor: `people_signal_summary` (string, may be empty).

```json
{
  "people_signal_summary": "<2-4 sentence overview>",
  "tracked_people_signals": [
    {
      "person_id": "lowercase-hyphenated",
      "name": "Full Name",
      "signal_summary": "<1-2 sentence>",
      "evidence_urls": ["https://..."]
    }
  ],
  "new_candidate_people": [
    {
      "person_id": "lowercase-hyphenated",
      "name": "Full Name",
      "group": "claude-code-builders | openai-builders | oss-ai-builders | ai-generalists",
      "reason": "<1 sentence>",
      "evidence_urls": ["https://..."]
    }
  ],
  "promoted_people": [],
  "candidate_queue_summary": ""
}
```

Same field shape as the legacy `ai.json`'s `new_candidate_people` /
`promoted_people` so the aggregator can copy directly.

### `macro_news`

Body anchor: `five_layer_signals` (object with the 5 layer keys).

```json
{
  "five_layer_signals": {
    "energy": [],
    "chips": [],
    "infra": [],
    "model": [],
    "application": []
  },
  "cross_layer_links": [
    {
      "summary": "<2-3 sentence: how a signal in layer X drives layer Y>",
      "evidence_urls": ["https://..."]
    }
  ]
}
```

Each layer entry is an object with at minimum `summary` (string) and
`evidence_urls` (array of strings). Specific entries can carry domain-
specific fields (e.g., `chips` may carry `vendor`, `model` may carry
`model_name`) — extras are accepted, only `summary` + `evidence_urls`
are required for `section-status`.

## What `section-status` does NOT validate

- Per-element field presence inside lists (e.g., does each lab signal
  have an `evidence_urls`). The aggregator decides whether a sparse
  section is acceptable for the day.
- Cross-section consistency (e.g., a paper appearing in both
  `paper_layer` and `macro_news.model`).
- Markdown content shape — the agent owns the human-readable layout;
  validation is JSON-only.

These remain the agent's responsibility, not the script's gate.

## Why these 4 and not more

Trying to slice further (e.g., per-lab files for `frontier_radar`)
loses the aggregation-level signal summary. Trying to slice fewer
(e.g., merging `paper_layer` into `macro_news.model`) re-couples the
paper-digest dependency to the rest of `macro_news`, losing the win
where `paper_layer` finishes fast on a thin paper day.

The 4 chosen here track the natural fault lines in the AI daily
workflow.
