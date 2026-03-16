# Automation Templates

These templates target the current file-driven scheduler model under `~/.oh-my-agent/automations/*.yaml`.

## Daily politics

```yaml
name: daily-politics-intel
enabled: false
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
delivery: channel
prompt: "Use the market-intel-report skill in daily_digest mode for politics. Research today's China central policy signals, US federal policy signals, and China-US or geopolitical moves. Read prior stored reports under ~/.oh-my-agent/reports/market-intel/, persist the new Markdown and JSON report, then post the finished Chinese report with the saved location."
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
prompt: "Use the market-intel-report skill in daily_digest mode for finance. Cover major-company earnings or guidance, macro and policy adjustments, market/economic implications, and next signals to watch. Read prior stored reports under ~/.oh-my-agent/reports/market-intel/, persist the new Markdown and JSON report, then post the finished Chinese report with the saved location."
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
prompt: "Use the market-intel-report skill in daily_digest mode for ai. Structure the report in five layers: energy, chips, infra, model, application. Read prior stored reports under ~/.oh-my-agent/reports/market-intel/, persist the new Markdown and JSON report, then post the finished Chinese report with the saved location."
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
prompt: "Use the market-intel-report skill in weekly_synthesis mode for cross-domain. Read the last 7 days of stored politics, finance, and ai daily reports plus the latest bootstrap dossiers and a small number of previous weekly reports under ~/.oh-my-agent/reports/market-intel/. Persist the new Markdown and JSON weekly report, then post the finished Chinese cross-domain synthesis with the saved location."
agent: codex
cron: "0 10 * * 0"
author: scheduler
```

## Bootstrap examples

Bootstrap is intentionally manual or one-shot. Do not bury it inside recurring daily automations.

Example one-off prompts:

- politics: `Use the market-intel-report skill in bootstrap_backfill mode for politics with a 30-day lookback.`
- finance: `Use the market-intel-report skill in bootstrap_backfill mode for finance with a 30-day lookback.`
- ai: `Use the market-intel-report skill in bootstrap_backfill mode for ai with a 14-day lookback.`

