# Prompt Recipes

Use these when the user wants a concrete prompt, an automation prompt later, or a scoped variant instead of one generic invocation.

All prompts below are Chinese-first and assume persisted report outputs under `~/.oh-my-agent/reports/seattle-metro-housing-watch/`.

## Weekly pulse

```text
Use the seattle-metro-housing-watch skill in weekly_pulse mode for the current local report date. Produce a Chinese Seattle metro buy-side housing report focused on Seattle, Bellevue, Redmond, Kirkland, Issaquah, Bothell, and Lynnwood. Use public sources only, compare both 30Y and 15Y fixed mortgage context, include a clear data_freshness_note, build a 7-area scoreboard, and include 14-18 representative listing samples as secondary evidence. Every area gets 2 listings first, with 4 extra slots going to areas that have stronger active inventory and better above-median listing availability. Only use single-family and townhouse samples. Persist Markdown and JSON under ~/.oh-my-agent/reports/seattle-metro-housing-watch/, and return a concise chat summary plus storage path.
```

## Market snapshot

```text
Use the seattle-metro-housing-watch skill in market_snapshot mode for the current local report date. Produce a shorter Chinese Seattle metro housing snapshot with 30Y and 15Y mortgage context, metro market pulse, a compact 7-area scoreboard, and at most 7 representative listings total. Use public sources only, persist Markdown and JSON, and return the snapshot summary plus storage path.
```

## Area deep dive

```text
Use the seattle-metro-housing-watch skill in area_deep_dive mode for Bellevue for the current local report date. Keep the report Chinese-first and focused on Bellevue, but still explain how the area sits relative to the broader Seattle metro market. Use public sources only, compare 30Y and 15Y mortgage context when useful, include a data_freshness_note, include 4-6 representative Bellevue listing samples as supporting evidence, and use only single-family or townhouse listings that are at or above Bellevue's own median price baseline unless coverage gaps require a limited exception. Persist Markdown and JSON, and return the summary plus storage path.
```
