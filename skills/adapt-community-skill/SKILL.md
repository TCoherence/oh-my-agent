---
name: adapt-community-skill
description: Adapt an imported or community-provided skill so it works in this workspace. Use when Codex needs to copy a skill from another repo or source and rewrite its SKILL.md, scripts, configuration, commands, or dependencies to match local repo rules, available tools, validation flow, and runtime constraints.
---

# Adapt Community Skill

Adapt an existing skill instead of porting it blindly. Keep the useful workflow, then rewrite the parts that assume a different repo layout, toolchain, validator, or operating environment.

## Quick Start

1. Locate the source skill folder or pasted skill contents.
2. Inventory the source with:

```bash
python skills/adapt-community-skill/scripts/scan_skill.py <source-skill-path>
```

3. Create or update the target skill under `skills/<normalized-name>/`.
4. Rewrite `SKILL.md` so the frontmatter only contains `name` and `description`, and make the description explicit about triggers.
5. Keep only the resource folders the adapted skill actually needs: `scripts/`, `references/`, `assets/`, `agents/`.
6. Validate with:

```bash
python skills/skill-creator/scripts/quick_validate.py skills/<normalized-name>
```

## Adaptation Workflow

### 1. Understand the source skill

Extract the concrete jobs the source skill is meant to perform:

- What user requests should trigger it
- Which files or commands it expects
- Which parts are reusable instructions versus source-repo assumptions

If the source skill is vague, infer 2-3 likely trigger phrases and rewrite the description around them.

### 2. Normalize the target shape

Make the target skill look like a first-class local skill:

- Folder name: lowercase hyphen-case under `skills/`
- Required file: `skills/<name>/SKILL.md`
- Required frontmatter keys: `name`, `description`
- Optional folders only when needed: `scripts/`, `references/`, `assets/`, `agents/`

Do not carry over extra docs such as `README.md`, `CHANGELOG.md`, migration notes, or install guides unless they are truly required for execution and fit better as `references/`.

### 3. Rewrite environment assumptions

Community skills often assume a different machine or repo. Replace those assumptions:

- Convert absolute or home-directory paths into workspace-relative paths.
- Replace destructive git commands with safe inspection or patch-based editing.
- Remove `sudo`, bootstrap installers, and machine-wide package-manager steps unless the current task explicitly allows them.
- Prefer existing project tooling and the repo's virtualenv over ad hoc global installs.
- Rewrite commands so they run from the current worktree.
- If the skill changes a user-facing default in `config.yaml.example`, update `config.yaml` in the same change when that file exists.

### 4. Tighten instructions

Rewrite the body for another agent, not for a human reader:

- Use imperative instructions.
- Put trigger conditions in the frontmatter description, not in a "when to use" body section.
- Keep `SKILL.md` short and move detailed checklists or schemas into `references/`.
- Add scripts only when they remove repeated manual work or reduce error-prone adaptation steps.

### 5. Verify dependencies and validation

For every imported script or command:

- Check whether the interpreter or CLI is already available.
- Prefer standard library or existing repo dependencies when the original skill used niche packages.
- If a helper script is added or changed, run it on a representative input.
- Finish with `quick_validate.py` on the final skill folder.

If the skill still depends on something not present locally, surface that gap explicitly instead of hiding it in prose.

## Common Rewrite Targets

- Frontmatter that includes unsupported metadata or unclear descriptions
- Commands that assume another repo root or another shell environment
- References to unavailable services, API keys, or network fetch steps
- Scripts that depend on external packages when a simpler local implementation is enough
- Documentation-heavy community skills that should be reduced to one lean `SKILL.md` plus a small `references/` file

## Resources

- Use `scripts/scan_skill.py` to flag common portability issues before editing.
- Read `references/adaptation-checklist.md` when you need a compact checklist of what to keep, rewrite, or remove.
