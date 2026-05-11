# Automation Templates

This template targets the current file-driven scheduler model under `~/.oh-my-agent/automations/*.yaml`.

## Daily politics

```yaml
name: daily-politics-intel
enabled: false
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
delivery: channel
skill_name: market-briefing-politics
prompt: "Use the market-briefing-politics skill in daily_digest mode for politics. Research today's China central policy signals, US federal policy signals, and China-US or geopolitical moves. Keep the output politically focused rather than finance-style market commentary. Read prior stored reports under ~/.oh-my-agent/reports/market-briefing/, persist the new Markdown and JSON report, then return a structured Chinese chat summary per the SKILL.md final-answer format."
agent: codex
cron: "0 8 * * *"
author: scheduler
```
