# Automation Templates

This template targets the current file-driven scheduler model under `~/.oh-my-agent/automations/*.yaml`.

## Daily finance

```yaml
name: daily-finance-intel
enabled: false
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
delivery: channel
skill_name: market-briefing-finance
prompt: "Use the market-briefing-finance skill in daily_digest mode for finance. Cover China macro and policy, US macro and policy, US market volatility and risk appetite, China/Hong Kong market pulse, China property policy and financing signals, tracked holdings over the last 7 days, and the market / index implications for broad positioning. Read prior stored reports under ~/.oh-my-agent/reports/market-briefing/, persist Markdown + JSON, then return a structured Chinese chat summary per the SKILL.md final-answer format."
agent: codex
cron: "30 8 * * *"
author: scheduler
```
