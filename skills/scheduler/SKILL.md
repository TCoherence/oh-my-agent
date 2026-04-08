---
name: scheduler
description: "Create, update, and validate recurring automation jobs for oh-my-agent. Use this skill when users ask for scheduled tasks, cron-like behavior, periodic reports, or autonomous recurring actions."
---

# Scheduler Skill

Use this skill to manage file-driven automation definitions under `~/.oh-my-agent/automations/*.yaml`.

## When to use

- User asks for recurring jobs or periodic reports.
- User wants the bot to run tasks automatically without manual prompts.
- User needs owner-only safety together with automation.

## Workflow

1. Create or update one YAML file per automation under `~/.oh-my-agent/automations/`.
2. Prefer `cron` for normal wall-clock schedules.
3. Use `interval_seconds` only for high-frequency local testing or very short loops.
4. Validate the file or directory:

```bash
./.venv/bin/python skills/scheduler/scripts/validate_automations.py ~/.oh-my-agent/automations
```

5. Tell the user to run `/automation_reload` or wait for the next polling cycle.

## Automation schema

Required fields:

- `name`
- `platform`
- `channel_id`
- `prompt`
- exactly one of:
  - `cron`
  - `interval_seconds`

Optional fields:

- `enabled` (default `true`)
- `delivery` (`channel` or `dm`, default `channel`)
- `thread_id`
- `target_user_id`
- `agent`
- `author`
- `initial_delay_seconds`

## Scheduling rules

- `cron` and `interval_seconds` are mutually exclusive.
- `initial_delay_seconds` is only valid with `interval_seconds`.
- `delivery=dm` should set `target_user_id` explicitly when possible.
- File names are not identifiers; `name` is the logical key.
- Duplicate `name` values across files are conflicts and should be fixed.

## Example: daily politics intelligence

```yaml
name: daily-politics-intel
enabled: false
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
delivery: channel
prompt: "Use the market-briefing skill in daily_digest mode for politics. Research today's China central policy signals, US federal policy signals, and China-US or geopolitical moves. Read prior stored reports under ~/.oh-my-agent/reports/market-briefing/, persist the new Markdown and JSON report, then post the finished Chinese report with the saved location."
agent: codex
cron: "0 8 * * *"
author: scheduler
```

## Example: local high-frequency test

```yaml
name: hello-from-codex
enabled: false
platform: discord
channel_id: "${DISCORD_CHANNEL_ID}"
delivery: dm
target_user_id: "123456789012345678"
prompt: "Hello from the other side! Just checking in."
agent: codex
interval_seconds: 20
initial_delay_seconds: 10
author: scheduler
```

## Validation notes

- The validator targets the file-driven scheduler model, not `config.yaml`.
- Invalid or conflicting files are skipped by the runtime and remain log-visible only.
- `/automation_status` shows valid active + disabled entries, not parse errors.
