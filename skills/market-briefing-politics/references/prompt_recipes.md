# Prompt Recipes

Use these when the user wants a concrete prompt, an automation prompt, or a scoped variant instead of one generic `market-briefing-politics` invocation.

All prompts below are Chinese-first and assume persisted report outputs under `~/.oh-my-agent/reports/market-briefing/`.

## Politics daily

### Broad politics daily

```text
Use the market-briefing-politics skill in daily_digest mode for politics for the current local date. Produce a Chinese politics daily that covers China central policy signals, US federal policy signals, and China-US / geopolitics. Keep the political and legislative meaning primary, and do not repeat finance-style market-impact prose unless it is necessary as brief context. Read prior stored reports under ~/.oh-my-agent/reports/market-briefing/, persist the new Markdown and JSON outputs, and return a structured Chinese chat summary per the SKILL.md final-answer format (headline + per-section highlights + top picks with inline links + coverage notes + storage paths).
```
