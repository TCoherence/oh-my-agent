---
name: seattle-metro-housing-watch
description: Produce Chinese-first Seattle metro housing reports with persisted Markdown and JSON outputs under ~/.oh-my-agent/reports/seattle-metro-housing-watch/. Use this skill for Seattle, Bellevue, Redmond, Kirkland, Issaquah, Bothell, and Lynnwood buy-side market pulse reports, area deep dives, and current housing snapshots that should reuse stored report history instead of relying only on chat memory.
metadata:
  timeout_seconds: 900
---

# Seattle Metro Housing Watch

Use this skill for **Seattle metro buy-side housing reporting**. The skill is report-centric and persistence-first: it writes durable Markdown + JSON outputs under `~/.oh-my-agent/reports/seattle-metro-housing-watch/`, then returns a concise chat summary with the storage path.

This is not a generic real-estate chatbot and not a login-backed MLS workflow.

## When to use

- User wants a Seattle housing market weekly pulse.
- User wants Seattle vs Eastside area comparison.
- User wants a Bellevue / Redmond / Kirkland / Issaquah / Bothell / Lynnwood area deep dive.
- User wants a current Seattle metro housing snapshot with rates, inventory, pricing, and buyer observations.
- User wants a repeatable housing-report workflow that can be automated later.

## Locked defaults

- Default mode: `weekly_pulse`
- Default language: Chinese-first
- Default delivery: persisted report + chat summary
- Default geographic scope:
  - `seattle`
  - `bellevue`
  - `redmond`
  - `kirkland`
  - `issaquah`
  - `bothell`
  - `lynnwood`
- Default report style:
  - market core first
  - representative listing samples second
- Public-source only:
  - no MLS login
  - no cookies
  - no brittle scraping as the main workflow

## Modes

### `weekly_pulse`

- Default mode.
- Produce one Seattle metro weekly pulse report.
- Include:
  - mortgage / financing context
  - metro market pulse
  - 7-area scoreboard
  - area-by-area buyer observations
  - 14-18 representative listing samples
- Sample contract:
  - all 7 areas get 2 listings first
  - 4 extra slots go to the strongest areas based on inventory activity and available high-quality listings

### `market_snapshot`

- Shorter on-demand version.
- Use the same source discipline.
- Keep the output lighter, but still include the core metro conclusion and a small sample-listing layer.
- Default listing layer:
  - 1 listing per area
  - at most 7 total

### `area_deep_dive`

- Use when the user asks for one explicit area such as Bellevue or Redmond.
- Focus on that area instead of forcing all metro areas into the body.
- Still use Seattle metro context as background when useful.
- Default listing layer:
  - 4-6 listings for the target area

## Weekly semantics and freshness

`weekly_pulse` means:

- “the latest publicly available Seattle metro housing pulse as of this reporting week”

It does **not** mean:

- every metric is natively updated weekly

Use this discipline:

- NWMLS monthly data may be the structural baseline
- mortgage data may be weekly
- Redfin and Zillow market pages may have their own cadence
- always include a `data_freshness_note`
- if a source lags, say so explicitly

When there are no prior persisted reports:

- treat the run as a first-run report
- use a bounded implicit baseline of the latest 1-2 months of public context
- do not invent fake week-over-week continuity

## Source contract

Read:

- `references/source_policy.md`
- `references/report_schema.md`
- `references/area_scope.md`
- `references/prompt_recipes.md`

Use sources in this order:

1. primary market context
   - NWMLS monthly market snapshot
   - Freddie Mac PMMS / FRED mortgage rate context
2. area trend layer
   - Redfin city / neighborhood housing market pages when publicly readable
   - Zillow city / local market pages when publicly readable
3. sample listing layer
   - Redfin public listings first
   - Zillow listings second

Important:

- Use **web fetch / web search style retrieval** for public market pages.
- Do **not** rely on DOM parsing, JS execution, or brittle page scraping as the default contract.
- Listing samples are **secondary illustration**, not the factual spine.
- If listing samples fail, finish the report anyway and record the gap in `coverage_gaps`.

## Listing contract

- Allowed property types:
  - `single-family`
  - `townhouse`
- Excluded by default:
  - condo
  - apartment
  - multi-family
  - lot
  - manufactured
- Default listing priority:
  1. `active`
  2. `pending / contingent`
  3. `recently sold` only as fallback
- Default price filter uses the **area's own** median baseline.
- Baseline order:
  1. `median_sale_price`
  2. fallback `median_list_price`
  3. fallback Zillow/Redfin publicly readable local price metric
- Default expectation:
  - listing price `>=` the area's own median baseline
- If an area cannot produce 2 high-quality samples above the baseline:
  - allow at most 1 below-baseline exception for that area
  - record the shortfall in `coverage_gaps`

## Required workflow

1. Make the mode explicit.
2. Make the report date explicit.
3. For `area_deep_dive`, lock the area explicitly.
4. Load prior stored context:

```bash
./.venv/bin/python skills/seattle-metro-housing-watch/scripts/report_store.py context   --mode weekly_pulse
```

For an area deep dive:

```bash
./.venv/bin/python skills/seattle-metro-housing-watch/scripts/report_store.py context   --mode area_deep_dive   --area bellevue
```

5. Generate a Markdown + JSON scaffold:

```bash
./.venv/bin/python skills/seattle-metro-housing-watch/scripts/report_store.py scaffold   --mode weekly_pulse   --markdown-file /tmp/seattle_housing_weekly.md   --json-file /tmp/seattle_housing_weekly.json
```

6. Research public sources with the source policy.
7. Fill the Markdown + JSON.
8. Persist the report:

```bash
./.venv/bin/python skills/seattle-metro-housing-watch/scripts/report_store.py persist   --mode weekly_pulse   --markdown-file /tmp/seattle_housing_weekly.md   --json-file /tmp/seattle_housing_weekly.json
```

9. Return the final report summary in chat and mention where the files were stored.

## Output rules

- Chinese-first wording by default.
- Keep inline source links in the body for important claims.
- Always include:
  - `data_freshness_note`
  - `source_mix_note`
  - `verification_note`
  - `coverage_gaps`
  - `confidence_flags`
- Weekly sample default:
  - all 7 areas get 2 listings first
  - no more than 18 total
  - extra 4 slots go to areas with stronger active inventory and better high-price sample availability
- Do not allow one area to dominate the sample list unless the user explicitly asked for that area.

## Canonical storage layout

- `~/.oh-my-agent/reports/seattle-metro-housing-watch/weekly/<date>.md|json`
- `~/.oh-my-agent/reports/seattle-metro-housing-watch/snapshot/<date>/seattle-metro.md|json`
- `~/.oh-my-agent/reports/seattle-metro-housing-watch/areas/<date>/<area>.md|json`

Use `report_store.py` for path generation and persistence. Do not hand-roll storage paths unless you are patching the helper itself.
