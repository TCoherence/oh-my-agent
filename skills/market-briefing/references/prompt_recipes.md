# Prompt Recipes

Use these when the user wants a concrete prompt, an automation prompt, or a scoped variant instead of one generic `market-briefing` invocation.

All prompts below are Chinese-first and assume persisted report outputs under `~/.oh-my-agent/reports/market-briefing/`.

## Politics daily

### Broad politics daily

```text
Use the market-briefing skill in daily_digest mode for politics for the current local date. Produce a Chinese politics daily that covers China central policy signals, US federal policy signals, and China-US / geopolitics. Read prior stored reports under ~/.oh-my-agent/reports/market-briefing/, persist the new Markdown and JSON outputs, and post the finished report with inline source links in the body plus a final source/verification section.
```

### China-policy-focused daily

```text
Use the market-briefing skill in daily_digest mode for politics for the current local date, with scope concentrated on China central-government policy, ministry-level signals, and top-level strategic direction. Keep US and geopolitics only as context when they materially affect China policy interpretation. Read prior stored reports, persist Markdown + JSON, and ensure key claims in the body carry inline source links.
```

### US-policy-focused daily

```text
Use the market-briefing skill in daily_digest mode for politics for the current local date, with scope concentrated on White House, Treasury, Commerce, Federal Reserve, Congress, and other federal policy signals relevant to China, trade, technology, and markets. Read prior stored reports, persist Markdown + JSON, and use inline source links for key claims.
```

### Geopolitics-focused daily

```text
Use the market-briefing skill in daily_digest mode for politics for the current local date, with scope concentrated on China-US relations and broader geopolitics that can affect policy, markets, supply chains, sanctions, or defense posture. Read prior stored reports, persist Markdown + JSON, and keep inline source links throughout the body.
```

## Finance daily

### Broad finance daily

```text
Use the market-briefing skill in daily_digest mode for finance for the current local date. Cover China macro and policy, US macro and policy, tracked holdings over the last 7 days, and the market / index-fund implications for broad positioning. The tracked universe defaults to NVDA, MSFT, AAPL, AMZN, GOOG, TSLA, META, VOO, SPY, and S&P 500. Read prior stored reports under ~/.oh-my-agent/reports/market-briefing/, persist Markdown + JSON, and keep the report detailed with inline source links in the body.
```

### Earnings-focused daily

```text
Use the market-briefing skill in daily_digest mode for finance for the current local date, with scope concentrated on China macro / policy shifts and tracked-holdings developments over the last 7 days, including earnings releases, investor presentations, management commentary, and CEO / CFO / IR public remarks. Keep the market / index implications explicit, persist Markdown + JSON, and cite company releases, filings, transcripts, and supporting media inline.
```

### Macro-policy-focused daily

```text
Use the market-briefing skill in daily_digest mode for finance for the current local date, with scope concentrated on macro prints, central-bank signals, fiscal or industrial policy, and the market/economic implications across both China and the US. Read prior stored reports, persist Markdown + JSON, and keep inline source links near all key claims.

### Holdings-and-China-focused finance daily

```text
Use the market-briefing skill in daily_digest mode for finance for the current local date, with explicit focus on China macro / policy and the tracked holdings over the last 7 days: NVDA, MSFT, AAPL, AMZN, GOOG, TSLA, META, VOO, SPY, and S&P 500. Cover China and US macro/policy separately, then analyze tracked-holdings earnings, filings, investor presentations, conference appearances, or CEO/CFO/IR public remarks, and finish with market / index-fund implications. Persist Markdown + JSON and keep inline source links on all key claims.
```
```

## AI daily

### Broad AI five-layer daily

```text
Use the market-briefing skill in daily_digest mode for ai for the current local date. Structure the report in five layers: energy, chips, infra, model, application. For each layer, cover important movement, implications, and cross-layer spillover. If a layer has no high-confidence incremental signal, say so explicitly instead of filling the section with generic prose. Read prior stored reports under ~/.oh-my-agent/reports/market-briefing/, persist Markdown + JSON, and use inline source links in the body rather than only at the end.
```

### Chips-and-infra-focused AI daily

```text
Use the market-briefing skill in daily_digest mode for ai for the current local date, but prioritize chips and infra, and only cover energy, model, and application when they materially change the interpretation. If chips or infra have no high-confidence incremental signal, say so explicitly and move the section into watchlist-style follow-up rather than generic filler. Read prior stored reports, persist Markdown + JSON, and use inline source links for all major claims.
```

### Model-and-application-focused AI daily

```text
Use the market-briefing skill in daily_digest mode for ai for the current local date, but prioritize model and application while still noting major changes in energy, chips, and infra that constrain deployment. Read prior stored reports, persist Markdown + JSON, and use inline source links in the body.
```

## Bootstrap backfill

### Politics bootstrap

```text
Use the market-briefing skill in bootstrap_backfill mode for politics with a 30-day lookback ending on the current local date. Build one bounded historical dossier rather than fake daily files. Focus on structural policy shifts, recurring themes, and what future daily reports should track. Persist Markdown + JSON and use inline source links for important claims.
```

### Finance bootstrap

```text
Use the market-briefing skill in bootstrap_backfill mode for finance with a 30-day lookback ending on the current local date. Build one bounded historical dossier covering China macro / policy, US macro / policy, tracked holdings, sector leadership, and what future daily reports should track. Persist Markdown + JSON and use inline source links in the body.
```

### AI bootstrap

```text
Use the market-briefing skill in bootstrap_backfill mode for ai with a 14-day lookback ending on the current local date. Build one bounded historical dossier using the five-layer structure: energy, chips, infra, model, application. Persist Markdown + JSON and use inline source links for key developments.
```

## Weekly synthesis

### Cross-domain weekly

```text
Use the market-briefing skill in weekly_synthesis mode for cross-domain for the current ISO week. Read the previous 7 days of stored politics, finance, and ai daily reports, plus the latest bootstrap dossiers and a bounded number of previous weekly reports under ~/.oh-my-agent/reports/market-briefing/. The finance weekly mainline must absorb China macro / policy, tracked-holdings developments over the last 7 days, and broad-market implications. Produce a detailed Chinese cross-domain weekly report with inline source links in the body, persist Markdown + JSON, and explicitly explain structural trends rather than just repeating headlines.
```
