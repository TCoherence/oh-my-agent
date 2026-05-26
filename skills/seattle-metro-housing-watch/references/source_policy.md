# Source Policy

## Goal

Produce Seattle metro housing reports that are current, explicit about data freshness, and conservative about fragile public-web signals.

## Source layers

Use sources in this order:

1. `primary market context`
   - NWMLS monthly market snapshot
   - Freddie Mac PMMS / FRED mortgage-rate baseline (plus MBA Weekly / MND national texture, WA-state aggregate + local CU spread — see **Mortgage comparison defaults** below)
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

Tiered sourcing. **Tiers 1, 2 are national reference; Tiers 3, 4 are the Seattle-local read** (the report's priority). Tier 5 is best-effort only.

> **Root-cause note (2026-05-25).** Lender / CU mortgage-rate **pages are client-side-rendered SPAs** — the numbers are injected by a JSON/XHR call, so a plain web-fetch of the HTML returns the shell with zero rate cells. Big-lender APIs are additionally **auth-gated** (e.g. Chase's `apix.chase.com/.../v1/rates` returns `Missing authorization header`). Several previously-listed CU URLs were also just **stale (404)**. Retrieval priority is therefore: **static PDF rate sheet > server-rendered WA-state aggregate page > HTML page with an inline rate table > SPA shell**. Do not reverse-engineer auth-gated widget tokens.

1. **Headline weekly index** (Tier 1 — national baseline, always include)
   - `MORTGAGE30US` (Freddie Mac PMMS, weekly Thursday)
   - `MORTGAGE15US` (Freddie Mac PMMS, weekly Thursday)
2. **Industry survey + daily index** (Tier 2 — national texture)
   - MBA Weekly Applications Survey 30Y conforming + jumbo + FHA / 15Y conforming (weekly Wednesday)
   - Mortgage News Daily 30Y / 15Y daily rate index (most recent close before report date)
3. **WA-state aggregate** (Tier 3 — the stable "Seattle-local" reference band; both server-rendered, verified 2026-05-25)
   - Bankrate WA — `https://www.bankrate.com/mortgages/mortgage-rates/washington/` (30Y/15Y note rate in raw HTML)
   - NerdWallet WA — `https://www.nerdwallet.com/mortgages/mortgage-rates/washington` (30Y APR in raw HTML)
4. **Local credit-union rack rates** (Tier 4 — Seattle-specific actual quotes, ≥1 of; corrected URLs + retrieval)
   - **BECU** (preferred local anchor) — static PDF rate sheet `https://www.becu.org/-/media/Files/PDF/MortgageExternalRateSheet.pdf` → parse the PDF (note rate + APR + discount points + effective datetime; no JS, no auth — most stable source available)
   - **WSECU** — `https://wsecu.org/loans/mortgage-purchase` (rate table is in the raw HTML; the old `/rates*` URLs 404)
   - **SoundCU** (optional) — `https://www.soundcu.com/rates/personal/home-loans/` rates come from an Optimal Blue auth-gated widget (`quickquote-consumer.optimalblue.com`); best-effort only
5. **Big-lender national rack rates** (Tier 5 — best-effort, optional)
   - Chase, Bank of America, Wells Fargo, U.S. Bank (Seattle ZIP 98004 / 98052). These are **auth-gated SPAs**; if unreachable, mark `n/a` and **do NOT count as a coverage gap** (it is a structural API barrier, not a fetch miss). Freddie Mac PMMS already covers the national headline.

Minimum coverage for the rate block:

- Always: PMMS 30Y + 15Y + at least one Tier 2 source (MBA OR MND)
- Local read (required when fetchable): ≥1 Tier 3 (WA aggregate) + ≥1 Tier 4 (local CU) — BECU PDF is the preferred local anchor. This pair supplies the national-vs-local spread that the (now best-effort) big-lender tier used to be relied on for.
- Big-lender Tier 5: best-effort; absence is **not** a coverage gap.

The rate block should explicitly say:

- latest value (per source, with snapshot date)
- previous value for the headline PMMS pair (WoW direction)
- multi-source spread for the **current week**, formatted as a small table:
  - source | 30Y | 15Y | snapshot date | notes
- short direction-of-travel note (uses PMMS WoW + MND daily delta for sub-weekly texture)
- what the 30Y vs 15Y spread implies for buyers who can afford shorter duration financing
- a one-line **national-vs-local** read whenever a Tier 1 (PMMS) row and ≥1 Tier 3/4 (WA aggregate or local CU) row are both present; distinguish **note rate vs APR** when comparing across sources

If a Tier 2/3/4 source is unreachable (paywall, 403, page restructured):

- record the gap in `coverage_gaps` (e.g. `mba_weekly_unavailable_W20`)
- do not silently drop it — the table should still list the source row with `n/a`
- never invent a rate to fill the cell
- **exception**: Tier 5 big-lender auth-gated SPAs are best-effort and do NOT generate a coverage gap when unreachable

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
