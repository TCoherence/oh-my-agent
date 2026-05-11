# Prompt Recipes

Use this when the user wants a concrete prompt, an automation prompt, or a scoped variant instead of one generic `market-briefing-weekly` invocation.

All prompts below are Chinese-first and assume persisted report outputs under `~/.oh-my-agent/reports/market-briefing/`.

## Weekly synthesis

### Cross-domain weekly

```text
Use the market-briefing-weekly skill in weekly_synthesis mode for cross-domain for the current ISO week. Read the previous 7 days of stored politics, finance, and ai daily reports, plus the latest bootstrap dossiers and a bounded number of previous weekly reports under ~/.oh-my-agent/reports/market-briefing/. The finance weekly mainline must absorb US market volatility, China/Hong Kong market pulse, China property policy and financing signals, tracked-holdings developments over the last 7 days, and broad-market implications. The AI weekly mainline must absorb frontier-lab radar, people/community signals, and five-layer developments. Produce a detailed Chinese cross-domain weekly report, persist Markdown + JSON, explicitly explain structural trends rather than just repeating headlines, and return a structured Chinese chat summary per the SKILL.md final-answer format.
```
