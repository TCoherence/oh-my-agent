# Prompt Recipes

Use these when the user wants a concrete prompt, an automation prompt later, or a scoped variant instead of one generic invocation.

All prompts below are Chinese-first and assume persisted report outputs under `~/.oh-my-agent/reports/seattle-metro-housing-watch/`.

## Weekly pulse

```text
Use the seattle-metro-housing-watch skill in weekly_pulse mode for the current local report date. Produce a Chinese Seattle metro buy-side housing report focused on Seattle, Bellevue, Redmond, Kirkland, and Issaquah. Use public sources only, include a clear data_freshness_note, build a 5-area scoreboard, include 3-5 representative sample listings as secondary evidence, persist Markdown and JSON under ~/.oh-my-agent/reports/seattle-metro-housing-watch/, and return a concise chat summary plus storage path.
```

## Market snapshot

```text
Use the seattle-metro-housing-watch skill in market_snapshot mode for the current local report date. Produce a shorter Chinese Seattle metro housing snapshot with mortgage context, metro market pulse, a compact area scoreboard, and a small representative listing layer. Use public sources only, persist Markdown and JSON, and return the snapshot summary plus storage path.
```

## Area deep dive

```text
Use the seattle-metro-housing-watch skill in area_deep_dive mode for Bellevue for the current local report date. Keep the report Chinese-first and focused on Bellevue, but still explain how the area sits relative to the broader Seattle metro market. Use public sources only, include a data_freshness_note, include representative Bellevue listing samples as supporting evidence, persist Markdown and JSON, and return the summary plus storage path.
```
