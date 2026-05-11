# Automation Templates

This template targets the current file-driven scheduler model under `~/.oh-my-agent/automations/*.yaml`.

## Daily AI

```yaml
name: daily-ai-intel
enabled: false
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
delivery: channel
skill_name: market-briefing-ai
prompt: "Use the market-briefing-ai skill in daily_digest mode for ai. Start with a Frontier Labs / Frontier Model Radar section covering OpenAI, Anthropic, Google DeepMind, Meta, xAI, Mistral, Qwen, and DeepSeek. Then structure the report around people/community signals plus energy, chips, infra, model, and application. Keep unverified rumors in watchlist form unless cross-checked. Read prior stored reports under ~/.oh-my-agent/reports/market-briefing/, persist Markdown + JSON, then return a structured Chinese chat summary per the SKILL.md final-answer format."
agent: codex
cron: "0 9 * * *"
author: scheduler
```
