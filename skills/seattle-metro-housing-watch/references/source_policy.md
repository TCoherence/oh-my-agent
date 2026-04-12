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
3. `sample listing layer`
   - Redfin public listings first
   - Zillow only as a fallback

## Retrieval method expectations

- Use **web fetch / web search style retrieval** for public market pages.
- Do **not** make DOM parsing or JS evaluation the default contract.
- Do **not** rely on brittle page-structure scraping as the main workflow.
- If a page is partially inaccessible, thin, or ambiguous, degrade to a broader city-level or metro-level source and record that in `coverage_gaps`.

## Weekly freshness discipline

The report cadence is weekly, but the source refresh cadence is mixed.

- NWMLS may be monthly
- mortgage data may be weekly
- Redfin market pages may have their own update cadence

Every report should explicitly include:

- `data_freshness_note`
- `source_mix_note`
- `verification_note`

If a source is stale relative to report date, say so plainly.

## Listing samples

Representative listing samples are **secondary illustration**, not the factual spine.

Rules:

- default 3-5 samples
- do not let sample listings dominate the report
- if listing pages fail or are too thin, finish the report anyway
- record sample-layer weakness in `coverage_gaps`
- explain why each sample matters from a buyer perspective

## Claim discipline

- Separate confirmed market facts from interpretation.
- If a claim depends on one thin public source, say so.
- If city/neighborhood pages conflict with broader market context, note the conflict instead of forcing a clean conclusion.
- Do not overstate a weekly directional call from stale monthly data.
