# Source Policy

## Goal

Produce Seattle metro housing reports that are current, explicit about data freshness, and conservative about fragile public-web signals.

## Source layers

Use sources in this order:

1. `primary market context`
   - NWMLS monthly market snapshot
   - Freddie Mac PMMS / FRED mortgage-rate baseline (plus MBA Weekly / MND / big-lender / local CU spread — see **Mortgage comparison defaults** below)
2. `area trend layer`
   - Redfin city / neighborhood housing market pages when publicly readable
   - Zillow city / local market pages when publicly readable
   - Beyond RE 7-city + King + Snohomish pages (current production primary; less anti-bot friction than Redfin/Zillow)
3. `sample listing layer` — switched 2026-05-21 because Redfin / Zillow detail pages have been returning 403 for ≥5 consecutive weeks
   - **Primary**: Realtor.com listing detail pages (more permissive anti-bot posture; usually returns full listing JSON in the HTML)
   - **Primary**: Local Seattle brokerage listing detail pages — Windermere, John L. Scott, Coldwell Banker Bain, Compass — these are the actual listing agents for most NWMLS inventory and rarely 403
   - **Fallback**: Redfin / Zillow public listing detail pages when reachable
   - **Last resort**: builder-direct pages (TriPointe, Toll Brothers, Lennar, DR Horton) for new construction
   - Never use a city / neighborhood / search-results URL as a "listing" link — see **Listing link discipline** below

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

A single weekly aggregate (Freddie Mac PMMS) is the structural baseline, but **one source is not enough**. The rate block must show a multi-source spread so readers can see where the actual quote distribution sits, not just the headline national average.

Tiered sourcing — all four tiers are required when publicly fetchable:

1. **Headline weekly index** (baseline, always include)
   - `MORTGAGE30US` (Freddie Mac PMMS, weekly Thursday)
   - `MORTGAGE15US` (Freddie Mac PMMS, weekly Thursday)
2. **Industry survey + daily index** (texture layer)
   - MBA Weekly Applications Survey 30Y conforming + jumbo + FHA / 15Y conforming (weekly Wednesday)
   - Mortgage News Daily 30Y / 15Y daily rate index (most recent close before report date)
3. **Big-lender rack rates** (national, ≥2 of)
   - Chase, Bank of America, Wells Fargo, U.S. Bank — published 30Y / 15Y / 5/6 ARM rack rate pages (usually require ZIP — use a Seattle metro ZIP such as 98004 or 98052)
4. **Local credit union rack rates** (Seattle-specific, ≥1 of)
   - BECU, WSECU, Sound Credit Union — published mortgage rate pages

Minimum coverage for the rate block:

- Always: PMMS 30Y + 15Y + at least one Tier 2 source (MBA OR MND)
- Strongly preferred: ≥1 big-lender quote + ≥1 local CU quote, so the report shows national-vs-local spread

The rate block should explicitly say:

- latest value (per source, with snapshot date)
- previous value for the headline PMMS pair (WoW direction)
- multi-source spread for the **current week**, formatted as a small table:
  - source | 30Y | 15Y | snapshot date | notes
- short direction-of-travel note (uses PMMS WoW + MND daily delta for sub-weekly texture)
- what the 30Y vs 15Y spread implies for buyers who can afford shorter duration financing
- a one-line "local vs national" read when CU + big-bank quotes are both present

If a Tier 2/3/4 source is unreachable (paywall, 403, page restructured):

- record the gap in `coverage_gaps` (e.g. `mba_weekly_unavailable_W20`)
- do not silently drop it — the table should still list the source row with `n/a`
- never invent a rate to fill the cell

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

- `source_site` (one of: `realtor`, `windermere`, `johnlscott`, `coldwellbankerbain`, `compass`, `redfin`, `zillow`, `builder`, `none`)
- `property_type`
- `listed_at`
- `days_on_market`
- `original_list_price`
- `price_history_summary`
- buyer-side note on why the sample matters

## Listing link discipline

A listing URL has exactly one job: **point to one specific property**. The reader's mental model when they see `[address](url)` is "I can click this and see the house I'm being told about." Anything else is misleading even if the link technically resolves to a real page.

Hard rules:

- A `sample_listings[].url` MUST resolve to a single-property detail page — the kind of URL that contains the property's MLS ID, listing ID, or unique address slug. Examples that ARE allowed:
  - `realtor.com/realestateandhomes-detail/<address-slug>_M<id>`
  - `windermere.com/listing/...` with a listing ID
  - `redfin.com/WA/<city>/<address>/home/<id>` (if reachable)
  - `zillow.com/homedetails/<address>/<zpid>_zpid/` (if reachable)
  - builder pages with a specific plan + community + lot (e.g. `tripointehomes.com/.../willows-124/lot-12`)
- The following URL shapes are **never** allowed as a sample listing link:
  - `redfin.com/city/<id>/.../<filter>` — city search page
  - `redfin.com/neighborhood/<id>/.../<filter>` — neighborhood search page
  - `redfin.com/zipcode/<zip>` — ZIP search page
  - `zillow.com/<city>-<state>/` — city search page
  - any `?` query-string filter URL on Redfin / Zillow / Realtor.com
  - the bare brokerage homepage or "browse listings" landing page
  - county assessor / parcel-lookup pages used as a substitute for a listing page

If no listing-level URL is reachable for a sample:

- leave `sample_listings[].url` empty (`""`) and set `source_site: "none"`
- in the Markdown body, render the sample as **plain text** — `Sub-market · price band · property type · short take` — with **no markdown link**
- record `listing_links_unavailable` in `coverage_gaps` once per affected area
- do **not** substitute a city / neighborhood / search URL just so the cell renders blue

Rationale: the previous behavior (pasting a Redfin city / neighborhood search URL as `[address](search_url)`) created a false-confidence signal. A reader scanning the table saw clickable per-property links; clicking them landed on a generic city browse page. Plain text is more honest and matches the existing `coverage_gaps` discipline used elsewhere in this skill.

## Claim discipline

- Separate confirmed market facts from interpretation.
- If a claim depends on one thin public source, say so.
- If city/neighborhood pages conflict with broader market context, note the conflict instead of forcing a clean conclusion.
- Do not overstate a weekly directional call from stale monthly data.
