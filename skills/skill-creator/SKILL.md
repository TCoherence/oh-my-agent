---
name: skill-creator
description: "Meta-skill: teaches the agent how to create new skills in the correct format so they are automatically detected, synced, and validated by the system."
---

# Skill Creator

Use this skill when the user asks you to create a new capability, tool, or reusable workflow.
A **skill** is a directory under `skills/` containing a `SKILL.md` file and optional `scripts/`.

## Directory Structure

```
skills/
└── my-skill/
    ├── SKILL.md        ← required: description + usage instructions
    └── scripts/        ← optional: executable helper scripts
        ├── run.sh
        └── helper.py
```

## SKILL.md Format

Every `SKILL.md` **must** begin with a YAML frontmatter block:

```markdown
---
name: my-skill
description: "One sentence: when to use this skill and what it does."
---

# My Skill

Explain what the skill does, when to use it, and any requirements.

## Usage

Describe how to invoke the skill.

## Examples

Show example inputs and outputs.
```

### Frontmatter Rules

| Field | Required | Notes |
|-------|----------|-------|
| `name` | **yes** | Must match the directory name (lowercase, hyphens) |
| `description` | **yes** | Used by agents to decide when to apply the skill |

## Script Conventions

- Place helper scripts in `scripts/` inside the skill directory.
- Shell scripts (`.sh`): must pass `bash -n` syntax check.
- Python scripts (`.py`): must pass `python -m py_compile`.
- All scripts **must be executable** (`chmod +x scripts/run.sh`).
- Use shebangs: `#!/bin/bash` or `#!/usr/bin/env python3`.

## Step-by-Step: Creating a Skill

1. **Create the skill directory** under `.claude/skills/` (from your workspace cwd):
   ```bash
   mkdir -p .claude/skills/my-skill/scripts
   ```

2. **Write `SKILL.md`** with valid frontmatter and clear usage instructions.

3. **Add scripts** (optional). Remember to make them executable:
   ```bash
   chmod +x .claude/skills/my-skill/scripts/run.sh
   ```

4. **Done.** The system automatically detects new non-symlink skill directories containing
   `SKILL.md` after each agent response and sends a Discord notification with validation results.
   You do not need to do anything else.

## Automatic Detection

After you create a skill, the bot will:
1. Detect the new skill directory (non-symlink, contains `SKILL.md`).
2. Copy it to the canonical `skills/` directory (reverse sync).
3. Symlink it into all CLI agent skill directories (forward sync).
4. Validate it and report any errors or warnings in the Discord thread.

If there are validation errors (e.g. missing frontmatter), fix them and the next agent response
will trigger another detection pass. Or use `/reload-skills` to trigger manually.
