# Automation Templates

This template targets the current file-driven scheduler model under `~/.oh-my-agent/automations/*.yaml`.

## Weekly cross-domain

```yaml
name: weekly-market-intel
enabled: false
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
delivery: channel
skill_name: market-briefing-weekly
prompt: "Use the market-briefing-weekly skill in weekly_synthesis mode for cross-domain. Read the last 7 days of stored politics, finance, and ai daily reports plus the latest bootstrap dossiers and a small number of previous weekly reports under ~/.oh-my-agent/reports/market-briefing/. Ensure the finance weekly mainline absorbs US volatility, China/Hong Kong market pulse, China property-policy and financing signals, tracked-holdings developments, and broad-market implications. Ensure the AI weekly mainline absorbs frontier-lab radar, people/community signals, and five-layer developments. Persist the new Markdown and JSON weekly report, then return a structured Chinese chat summary per the SKILL.md final-answer format."
agent: codex
cron: "0 10 * * 0"
author: scheduler
```
