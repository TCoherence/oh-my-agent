# Prompt Recipes

Use these when the user wants a concrete prompt, an automation prompt, or a scoped variant instead of one generic `market-briefing-finance` invocation.

All prompts below are Chinese-first and assume persisted report outputs under `~/.oh-my-agent/reports/market-briefing/`.

## Finance daily

### Broad finance daily

```text
Use the market-briefing-finance skill in daily_digest mode for finance for the current local date. Cover China macro and policy, US macro and policy, US market volatility and risk appetite, China/Hong Kong market pulse, China property policy and financing signals, tracked holdings over the last 7 days, and the market / index-fund implications for broad positioning. The tracked universe defaults to NVDA, MSFT, AAPL, AMZN, GOOG, TSLA, META, VOO, SPY, and S&P 500. Treat housing policy as a financing/market issue here, not as a politics-style policy recap. Read prior stored reports under ~/.oh-my-agent/reports/market-briefing/, persist Markdown + JSON, and return a structured Chinese chat summary per the SKILL.md final-answer format.
```

### Holdings-and-China-focused finance daily

```text
Use the market-briefing-finance skill in daily_digest mode for finance for the current local date, with explicit focus on China macro / policy, China property finance signals, China/Hong Kong market pulse, US macro / policy, US market volatility, and the tracked holdings over the last 7 days: NVDA, MSFT, AAPL, AMZN, GOOG, TSLA, META, VOO, SPY, and S&P 500. Cover market impact explicitly, persist Markdown + JSON, and return a structured Chinese chat summary per the SKILL.md final-answer format.
```
