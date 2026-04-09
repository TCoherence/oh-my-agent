# Source Policy

Research strategies and source taxonomy for each deals-scanner channel.

## Source taxonomy

The `source` field in reports has two semantic types. This distinction is fixed and must not be blurred.

| Source | Type | Scope |
|--------|------|-------|
| `credit-cards` | **topic bucket** | Aggregates multiple information sources about US credit card deals |
| `uscardforum` | **single site** | uscardforum.com only |
| `rakuten` | **single site** | rakuten.com only |
| `slickdeals` | **single site** | slickdeals.net only |
| `dealmoon` | **single site** | dealmoon.com only |

`credit-cards` is not a single website. It is a curated topic aggregation. The agent searches the enumerated sources below and does not self-expand beyond this list without explicit user instruction.

## Research approach

This skill uses **web search**, not automated scraping. For each source, the agent performs targeted searches using the strategies below, evaluates results, and compiles a structured report.

## Coverage floor

- Daily source scans should target `12-15` verified items.
- High-confidence floor is `10`.
- Do not stop below `10` high-confidence items unless the source genuinely lacks enough credible candidates that day.
- When coverage is thin, keep searching adjacent queries / category pages before giving up.
- If the final usable set is still below `10`:
  - keep the正文 focused on the high-confidence set
  - move weaker candidates into `lower_confidence_watchlist`
  - state that explicitly in the report instead of pretending the scan is complete
- For broad daily bundles, the `summary` report should call out which sources cleared the coverage floor and which did not.

## Daily lookback defaults

- `credit-cards`: recent `3` days
- `uscardforum`: recent `3` days
- `rakuten`: recent `3` days
- `slickdeals`: recent `7` days
- `dealmoon`: recent `7` days
- broad `summary`: recent `7` days

These defaults apply to `daily_scan` only. `weekly_digest` remains a fixed recent `7` day synthesis across all five sources.

## Link and confidence policy

Mainline deal entries should prefer concrete deal-level URLs:

- a specific deal page
- a specific forum thread
- a specific merchant page
- an issuer official offer / product page

Homepage, list pages, and roundup pages are weaker evidence:

- acceptable as supplementary citations
- acceptable in `lower_confidence_watchlist`
- not acceptable as the only link for a mainline high-confidence deal

High-confidence entries should have:

- a concrete deal-level URL
- title / merchant / value mostly clear
- expiration or deal status reasonably inferable
- enough evidence to support a `quality_score` without guesswork

## Summary action-bucket policy

Broad daily `summary.md` should classify cross-source items into:

- `Apply now`
  - card sign-up bonuses
  - expiring registrations
  - targeted enrollment offers
- `Buy now`
  - deals that are ready to purchase today
- `Stack now`
  - portal + coupon + card offer combinations
- `Watchlist`
  - worth tracking, but not yet strong enough for immediate action

Additional summary rules:

- `summary` should read like a decision brief, not a directory page.
- Keep the main payload in `Apply now` / `Buy now` / `Stack now`.
- Aim for roughly `8-12` concrete items across those three buckets unless the day is genuinely thin.
- `各渠道一句话结论` is supportive; it should not replace the main action buckets.
- `Coverage / Confidence` should explicitly state each source's lookback window and whether it cleared the high-confidence floor.
- If an item falls outside that source's mainline daily window but still matters, keep it in `Watchlist` only and mark it as a carryover / 超窗延续项.

## Per-source search strategies

### credit-cards (topic bucket)

**Enumerated information sources:**

1. Doctor of Credit (doctorofcredit.com) — primary for US credit card deals
2. NerdWallet (nerdwallet.com) — sign-up bonuses and card comparisons
3. The Points Guy (thepointsguy.com) — points/miles valuations and deals
4. Frequent Miler (frequentmiler.com) — deal analysis and strategies
5. One Mile at a Time (onemileatatime.com) — premium card deals and travel perks
6. Issuer official pages — Chase, Amex, Citi, Capital One, Bank of America, US Bank, Discover, Wells Fargo activity/offer pages

**Search queries:**

- `site:doctorofcredit.com` (browse recent posts)
- `"credit card" "sign up bonus" site:doctorofcredit.com`
- `"best credit card deals" this week`
- `"new credit card offer" <current month> <current year>`
- `site:nerdwallet.com "best credit cards"`
- `"targeted offer" credit card <current year>`
- `"annual fee waiver" OR "product change" credit card`

**What to capture:**
- New or improved sign-up bonuses
- Limited-time spending bonuses or multipliers
- Annual fee waiver offers
- Product change / upgrade / downgrade opportunities
- Targeted offers (Amex offers, Chase offers, etc.)
- Referral bonus changes
- Expiring offers (within 7 days)

**Extra rules:**
- Do not let aggregator/blog roundup links be the only evidence for top items when an issuer page exists.
- For mainline `Apply now` items, try to pair a blog/forum explainer with an issuer official page or more concrete landing page.
- Mainline daily items should come from the recent `3` day window. Older but still notable items belong in `Watchlist` only.

### uscardforum (single site)

**Search queries:**

- `site:uscardforum.com` (browse recent hot threads)
- `美卡论坛 今日热帖`
- `uscardforum 开卡 数据点`
- `uscardforum 积分兑换`
- `uscardforum 银行政策`

**What to capture:**
- Hot discussion threads with high reply counts
- Approval/denial data points for specific cards
- Points/miles redemption strategy discussions
- Bank policy changes or warnings (shutdown risks, etc.)
- Community-reported targeted offers

### rakuten (single site)

**Search queries:**

- `site:rakuten.com/stores` (browse merchant list)
- `rakuten cashback today`
- `rakuten double cashback <current month>`
- `rakuten flash sale`
- `rakuten new stores`
- `"rakuten" "cashback" best deals`

**What to capture:**
- Merchants with unusually high cashback rates (above their normal rate)
- Flash/limited-time promotions
- New merchant additions
- Stacking opportunities (Rakuten + credit card portal + coupon code)
- In-store cashback offers
- Seasonal event cashback boosts

**Extra rules:**
- Prefer merchant-level Rakuten entry pages when available.
- Treat `allstores` / broad listing pages as overview-only; do not use them as the only link for a top deal.
- Mainline daily items should come from the recent `3` day window. Older but still notable items belong in `Watchlist` only.

### slickdeals (single site)

**Search queries:**

- `site:slickdeals.net/deals` (browse frontpage)
- `slickdeals frontpage today`
- `slickdeals popular deals`
- `slickdeals "thumbs up"` (highly voted deals)
- `site:slickdeals.net <specific category>`

**What to capture:**
- Frontpage deals (editor-curated, highest signal)
- Highly-voted community deals (many thumbs up)
- Tech and electronics deals
- Home, kitchen, and daily essentials
- Apparel, outdoor, travel, auto, and other broad-category deals that materially stand out
- Notable coupon codes or stacking opportunities
- Price error / clearance deals

**Positioning:**
- Keep Slickdeals broad. It is the wide deal radar across categories, not just a tech feed.
- The mainline daily window is recent `7` days.

### dealmoon (single site)

**Search queries:**

- `site:dealmoon.com` (browse featured deals)
- `北美省钱快报 今日折扣`
- `dealmoon exclusive code`
- `dealmoon 独家折扣`
- `北美省钱快报 美妆` / `电子` / `家居`
- `dealmoon hot deals today`

**What to capture:**
- Featured/editor-picked deals of the day
- Exclusive discount codes (Dealmoon-only)
- Beauty and skincare deals
- Electronics and gadget deals
- Home, kitchen, and household deals
- Chinese community-oriented deals (Asian grocery, shipping to China, etc.)

**Positioning:**
- Dealmoon daily coverage should stay centered on beauty, electronics, and home.
- Apparel/fashion can appear only when it clearly makes the day-level top picks or watchlist; it should not dominate the source report.
- High-confidence mainline entries should prefer concrete deal or merchant pages rather than homepage/roundup links.
- The mainline daily window is recent `7` days.

## Deal quality evaluation criteria

When assigning `quality_score` (1-5), consider:

1. **Discount depth** — How much below normal price? For credit cards, how does the bonus compare to historical offers?
2. **Historical context** — Is this actually a good deal, or does this price appear regularly?
3. **Expiration urgency** — Deals ending within 48 hours score higher.
4. **Community validation** — High upvotes, many positive comments, multiple sources reporting the same deal.
5. **Exclusivity** — Targeted offers, limited availability, or new-customer-only deals.
6. **Stackability** — Can this deal combine with other discounts (portal + card + coupon)?
7. **Broad appeal** — Deals on widely-needed items score higher than ultra-niche products.

## Cross-checking

- For important claims (e.g., "all-time low" or "highest-ever bonus"), cross-check with at least one additional source.
- Note if a deal requires specific conditions (membership tier, new customer only, specific card).
- If a deal's validity is uncertain, say so explicitly in the `notes` field.

## What NOT to search

- Do not use `/search` — that is internal conversation-history search, not an external news source.
- Do not fabricate deals or prices. If evidence is incomplete, mark the deal with a lower quality_score and note the uncertainty.
