# Prompt Recipes

Reusable prompt variants for deals-scanner. Use these when the user asks for a specific prompt or when constructing automation prompts.

## Daily scan — credit-cards (broad)

```text
Use the deals-scanner skill in daily_scan mode for credit-cards.
Research today's US credit card deals from Doctor of Credit, NerdWallet,
The Points Guy, and issuer sites. Cover sign-up bonuses, cashback/rewards
promotions, annual fee waivers, and expiring offers.
Read prior stored reports under ~/.oh-my-agent/reports/deals-scanner/,
persist the new Markdown and JSON report, then post the finished Chinese
report with the saved location.
```

## Daily scan — credit-cards (sign-up bonus focus)

```text
Use the deals-scanner skill in daily_scan mode for credit-cards.
Focus specifically on new or improved sign-up bonuses and targeted card
offers. Check Doctor of Credit and issuer sites for any changes in the
last 24 hours. Read prior stored reports, persist, and post in Chinese.
```

## Daily scan — uscardforum

```text
Use the deals-scanner skill in daily_scan mode for uscardforum.
Scan uscardforum.com for today's hot discussions, new approval/denial
data points, redemption strategy threads, and any bank policy change
reports. Read prior stored reports, persist the new Markdown and JSON
report, then post the finished Chinese report with the saved location.
```

## Daily scan — rakuten

```text
Use the deals-scanner skill in daily_scan mode for rakuten.
Research today's Rakuten cashback deals, flash promotions, new merchant
additions, and notable high-cashback merchants. Identify stacking
opportunities with credit card portals. Read prior stored reports,
persist, and post in Chinese.
```

## Daily scan — rakuten (flash deal focus)

```text
Use the deals-scanner skill in daily_scan mode for rakuten.
Focus on limited-time flash cashback events and seasonal promotions
only. Skip merchants at their normal cashback rate. Persist and post
in Chinese.
```

## Daily scan — slickdeals

```text
Use the deals-scanner skill in daily_scan mode for slickdeals.
Research today's Slickdeals frontpage deals and popular community-voted
deals across tech, home, and other categories. Prioritize highly-voted
deals and notable price drops. Read prior stored reports, persist, and
post in Chinese.
```

## Daily scan — slickdeals (tech only)

```text
Use the deals-scanner skill in daily_scan mode for slickdeals.
Focus only on electronics and tech deals from Slickdeals frontpage
and popular listings. Include laptops, phones, TVs, headphones, and
smart home devices. Persist and post in Chinese.
```

## Daily scan — dealmoon

```text
Use the deals-scanner skill in daily_scan mode for dealmoon.
Research today's Dealmoon (北美省钱快报) featured deals, exclusive
discount codes, and popular categories including beauty, fashion, and
food/lifestyle. Read prior stored reports, persist, and post in Chinese.
```

## Daily scan — dealmoon (beauty focus)

```text
Use the deals-scanner skill in daily_scan mode for dealmoon.
Focus on beauty, skincare, and personal care deals only from Dealmoon.
Include exclusive codes and notable brand sales. Persist and post
in Chinese.
```

## Weekly cross-source digest

```text
Use the deals-scanner skill in weekly_digest mode for all-sources.
Read the last 7 days of stored credit-cards, uscardforum, rakuten,
slickdeals, and dealmoon daily reports under
~/.oh-my-agent/reports/deals-scanner/. Highlight the best deals,
notable trends, and actionable cross-source strategies. Persist the
new Markdown and JSON weekly digest, then post the finished Chinese
cross-source synthesis with the saved location.
```
