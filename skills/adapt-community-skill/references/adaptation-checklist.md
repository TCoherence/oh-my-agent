# Adaptation Checklist

Use this checklist when rewriting a community skill into a local skill.

## Keep

- The core user jobs the original skill solves
- Any helper script that meaningfully reduces repeated manual work
- Small reference files that document non-obvious local constraints

## Rewrite

- `description` so it clearly states what the skill does and when it should trigger
- Commands so they run from the current worktree with local paths
- Validation steps so they reference local validators and representative test commands
- Dependency assumptions so they match the current environment

## Remove

- Extra docs that do not help another agent execute the task
- Machine bootstrap instructions, package-manager install steps, and privileged commands
- Repo-specific assumptions from the upstream project that are irrelevant here
- Dead resources copied from templates or examples

## Before finishing

1. Confirm the skill folder name is normalized and matches the frontmatter `name`.
2. Confirm `SKILL.md` has valid YAML frontmatter with `name` and `description`.
3. Run any added or modified helper scripts once on a representative input.
4. Run `python skills/skill-creator/scripts/quick_validate.py skills/<name>`.
5. Call out any remaining external dependency or missing context explicitly.
