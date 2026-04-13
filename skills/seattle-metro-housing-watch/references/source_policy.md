# Source Policy

## Goal

Produce Seattle metro housing reports that are current, explicit about data freshness, and conservative about fragile public-web signals.

## Source layers

Use sources in this order:

1. `primary market context`
   - NWMLS monthly market snapshot
   - Freddie Mac PMMS / FRED mortgage-rate context
2. `area trend layer`
   - Redfin city / neighborhood housing market pages when publicly readable
   - Zillow city / local market pages when publicly readable
3. `sample listing layer`
   - Redfin public listings first
   - Zillow public listings second

## Retrieval method expectations

- Use **web fetch / web search style retrieval** for public market pages.
- Do **not** make DOM parsing or JS evaluation the default contract.
- Do **not** rely on brittle page-structure scraping as the main workflow.
- If a page is partially inaccessible, thin, or ambiguous, degrade to a broader city-level or metro-level source and record that in `coverage_gaps`.

## Weekly freshness discipline

The report cadence is weekly, but the source refresh cadence is mixed.

- NWMLS may be monthly
- mortgage data may be weekly
- Redfin / Zillow market pages may have their own update cadence

Every report should explicitly include:

- `data_freshness_note`
- `source_mix_note`
- `verification_note`

If a source is stale relative to report date, say so plainly.

## Mortgage comparison defaults

The default rate block must compare:

- `MORTGAGE30US`
- `MORTGAGE15US`

The report should explicitly say:

- latest value
- previous value
- short direction-of-travel note
- what the 30Y vs 15Y spread implies for buyers who can afford shorter duration financing

## Listing samples

Representative listing samples are **secondary illustration**, not the factual spine.

Rules:

- `weekly_pulse`
  - 7 areas each get 2 listings first
  - hard cap `18`
  - 4 extra slots go to areas with better high-price, high-quality sample availability
- `market_snapshot`
  - target 1 listing per area
  - at most `7`
- `area_deep_dive`
  - target `4-6` listings for the chosen area
- allowed property types:
  - `single-family`
  - `townhouse`
- excluded by default:
  - condo
  - apartment
  - multi-family
  - lot
  - manufactured
- sample selection priority:
  1. `active`
  2. `pending / contingent`
  3. `recently sold` only as fallback
- default price filter is based on the area's own median baseline:
  1. `median_sale_price`
  2. fallback `median_list_price`
  3. fallback Zillow/Redfin public local price metric
- default expectation is `>=` the area's own median baseline
- if an area cannot supply 2 above-baseline listings, allow at most 1 below-baseline exception and record it in `coverage_gaps`

Each sample should include, when publicly visible:

- `source_site`
- `property_type`
- `listed_at`
- `days_on_market`
- `original_list_price`
- `price_history_summary`
- buyer-side note on why the sample matters

## Claim discipline

- Separate confirmed market facts from interpretation.
- If a claim depends on one thin public source, say so.
- If city/neighborhood pages conflict with broader market context, note the conflict instead of forcing a clean conclusion.
- Do not overstate a weekly directional call from stale monthly data.
