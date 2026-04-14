# Automation Templates

These templates target the current file-driven scheduler model under `~/.oh-my-agent/automations/*.yaml`.

## Daily politics

```yaml
name: daily-politics-intel
enabled: false
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
delivery: channel
skill_name: market-briefing
prompt: "Use the market-briefing skill in daily_digest mode for politics. Research today's China central policy signals, US federal policy signals, and China-US or geopolitical moves. Keep the output politically focused rather than finance-style market commentary. Read prior stored reports under ~/.oh-my-agent/reports/market-briefing/, persist the new Markdown and JSON report, then post the finished Chinese report with the saved location."
agent: codex
cron: "0 8 * * *"
author: scheduler
```

## Daily finance

```yaml
name: daily-finance-intel
enabled: false
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
delivery: channel
skill_name: market-briefing
prompt: "Use the market-briefing skill in daily_digest mode for finance. Cover China macro and policy, US macro and policy, US market volatility and risk appetite, China/Hong Kong market pulse, China property policy and financing signals, tracked holdings over the last 7 days, and the market / index implications for broad positioning. Read prior stored reports under ~/.oh-my-agent/reports/market-briefing/, persist Markdown + JSON, and post the finished Chinese report with the saved location."
agent: codex
cron: "30 8 * * *"
author: scheduler
```

## Daily AI

```yaml
name: daily-ai-intel
enabled: false
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
delivery: channel
skill_name: market-briefing
prompt: "Use the market-briefing skill in daily_digest mode for ai. Start with a Frontier Labs / Frontier Model Radar section covering OpenAI, Anthropic, Google DeepMind, Meta, xAI, Mistral, Qwen, and DeepSeek. Then structure the report around people/community signals plus energy, chips, infra, model, and application. Keep unverified rumors in watchlist form unless cross-checked. Read prior stored reports under ~/.oh-my-agent/reports/market-briefing/, persist Markdown + JSON, and post the finished Chinese report with the saved location."
agent: codex
cron: "0 9 * * *"
author: scheduler
```

## Weekly cross-domain

```yaml
name: weekly-market-intel
enabled: false
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
delivery: channel
skill_name: market-briefing
prompt: "Use the market-briefing skill in weekly_synthesis mode for cross-domain. Read the last 7 days of stored politics, finance, and ai daily reports plus the latest bootstrap dossiers and a small number of previous weekly reports under ~/.oh-my-agent/reports/market-briefing/. Ensure the finance weekly mainline absorbs US volatility, China/Hong Kong market pulse, China property-policy and financing signals, tracked-holdings developments, and broad-market implications. Ensure the AI weekly mainline absorbs frontier-lab radar, people/community signals, and five-layer developments. Persist the new Markdown and JSON weekly report, then post the finished Chinese cross-domain synthesis with the saved location."
agent: codex
cron: "0 10 * * 0"
author: scheduler
```
