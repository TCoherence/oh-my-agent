---
name: scheduler
description: "Create, update, and validate recurring automation jobs for oh-my-agent. Use this skill when users ask for scheduled tasks, cron-like behavior, periodic reports, or autonomous recurring actions."
---

# Scheduler Skill

Use this skill to manage `automations` in `config.yaml`.

## When to use

- User asks for recurring jobs or periodic reports
- User wants the bot to run tasks automatically without manual prompts
- User needs owner-only safety together with automation

## Workflow

1. Open `config.yaml`.
2. Ensure `access.owner_user_ids` contains trusted user IDs.
3. Add or update `automations.jobs`.
4. Validate with:

```bash
./.venv/bin/python skills/scheduler/scripts/validate_automations.py config.yaml
```

5. If valid, remind user to restart `oh-my-agent`.

## Job schema (MVP)

Each job supports:

- `name` (string)
- `enabled` (optional bool, default `true`)
- `platform` (string, e.g. `discord`)
- `channel_id` (string)
- `delivery` (optional string, `channel` or `dm`, default `channel`)
- `prompt` (string)
- `interval_seconds` (int, must be > 0)
- `thread_id` (optional string, for `delivery=channel`)
  Omit to post directly in the parent channel.
- `target_user_id` (optional string, for `delivery=dm`)
  If omitted, scheduler falls back to the first `access.owner_user_ids`.
- `agent` (optional string)
- `initial_delay_seconds` (optional int, >= 0)
- `author` (optional string, defaults to `scheduler`)

## Example

```yaml
access:
  owner_user_ids: ["123456789012345678"]

automations:
  enabled: true
  jobs:
    - name: daily-refactor
      enabled: true
      platform: discord
      channel_id: "767174280856600621"
      delivery: dm
      target_user_id: "123456789012345678"
      prompt: "Review TODOs, pick one coding task, implement and summarize changes."
      agent: codex
      interval_seconds: 86400
      initial_delay_seconds: 10
```
