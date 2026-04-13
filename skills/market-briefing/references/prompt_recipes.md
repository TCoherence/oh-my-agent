# Prompt Recipes

Use these when the user wants a concrete prompt, an automation prompt, or a scoped variant instead of one generic `market-briefing` invocation.

All prompts below are Chinese-first and assume persisted report outputs under `~/.oh-my-agent/reports/market-briefing/`.

## Politics daily

### Broad politics daily

```text
Use the market-briefing skill in daily_digest mode for politics for the current local date. Produce a Chinese politics daily that covers China central policy signals, US federal policy signals, and China-US / geopolitics. Keep the political and legislative meaning primary, and do not repeat finance-style market-impact prose unless it is necessary as brief context. Read prior stored reports under ~/.oh-my-agent/reports/market-briefing/, persist the new Markdown and JSON outputs, and post the finished report with inline source links in the body plus a final source/verification section.
```

## Finance daily

### Broad finance daily

```text
Use the market-briefing skill in daily_digest mode for finance for the current local date. Cover China macro and policy, US macro and policy, US market volatility and risk appetite, China/Hong Kong market pulse, China property policy and financing signals, tracked holdings over the last 7 days, and the market / index-fund implications for broad positioning. The tracked universe defaults to NVDA, MSFT, AAPL, AMZN, GOOG, TSLA, META, VOO, SPY, and S&P 500. Treat housing policy as a financing/market issue here, not as a politics-style policy recap. Read prior stored reports under ~/.oh-my-agent/reports/market-briefing/, persist Markdown + JSON, and keep the report detailed with inline source links in the body.
```

### Holdings-and-China-focused finance daily

```text
Use the market-briefing skill in daily_digest mode for finance for the current local date, with explicit focus on China macro / policy, China property finance signals, China/Hong Kong market pulse, US macro / policy, US market volatility, and the tracked holdings over the last 7 days: NVDA, MSFT, AAPL, AMZN, GOOG, TSLA, META, VOO, SPY, and S&P 500. Cover market impact explicitly, persist Markdown + JSON, and keep inline source links on all key claims.
```

## AI daily

### Broad AI daily with frontier radar

```text
Use the market-briefing skill in daily_digest mode for ai for the current local date. Read prior stored reports and the current AI people pool, then begin with a Frontier Labs / Frontier Model Radar section covering OpenAI, Anthropic, Google DeepMind, Meta, xAI, Mistral, Qwen, and DeepSeek. After that, structure the report around tracked people/community signals plus the five layers: energy, chips, infra, model, application. Consult tracked people/groups first, do a bounded discovery sweep for new relevant people, and keep rumors in unverified frontier signals unless they are cross-checked by stronger sources. Persist Markdown + JSON under ~/.oh-my-agent/reports/market-briefing/ and keep inline source links in the body.
```

### Frontier-focused AI daily

```text
Use the market-briefing skill in daily_digest mode for ai for the current local date, with special emphasis on frontier-lab and frontier-model signals such as GPT-6-class or Anthropic Mythos-class developments. Start with a Frontier Labs / Frontier Model Radar section, then map the implications into people/community, energy, chips, infra, model, and application. Official sources and high-quality media take priority; unverified social signals stay in watchlist or unverified frontier signals rather than the main thesis. Persist Markdown + JSON and use inline source links throughout.
```

## Weekly synthesis

### Cross-domain weekly

```text
Use the market-briefing skill in weekly_synthesis mode for cross-domain for the current ISO week. Read the previous 7 days of stored politics, finance, and ai daily reports, plus the latest bootstrap dossiers and a bounded number of previous weekly reports under ~/.oh-my-agent/reports/market-briefing/. The finance weekly mainline must absorb US market volatility, China/Hong Kong market pulse, China property policy and financing signals, tracked-holdings developments over the last 7 days, and broad-market implications. The AI weekly mainline must absorb frontier-lab radar, people/community signals, and five-layer developments. Produce a detailed Chinese cross-domain weekly report with inline source links in the body, persist Markdown + JSON, and explicitly explain structural trends rather than just repeating headlines.
```
