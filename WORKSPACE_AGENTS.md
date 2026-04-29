# Workspace agent guide

You are running inside an oh-my-agent task workspace. This file (`AGENTS.md` /
`CLAUDE.md` / `GEMINI.md` are all the same content) lives in your **cwd** and
tells you where things are.

## Your cwd is already wired up

The runtime symlinks the following into your cwd before invoking you. **Use
them as-is — do not search the filesystem.**

| Path                          | What's there                                  |
| ----------------------------- | --------------------------------------------- |
| `./.venv/`                    | Python virtualenv with all script deps        |
| `./.claude/skills/<name>/`    | Skill bundle for the `claude` agent           |
| `./.gemini/skills/<name>/`    | Same skill, for the `gemini` agent            |
| `./.agents/skills/<name>/`    | Same skill, for the `codex` agent             |

You will only ever read from your own agent's skill dir. The runtime exposes
this dir as the `OMA_AGENT_HOME` environment variable so SKILL.md instructions
stay portable across the three agents:

- `claude` → `OMA_AGENT_HOME=.claude`
- `gemini` → `OMA_AGENT_HOME=.gemini`
- `codex`  → `OMA_AGENT_HOME=.agents`

## How to invoke a skill

Read the SKILL.md first, then run scripts via the venv:

```bash
./.venv/bin/python "$OMA_AGENT_HOME/skills/<skill-name>/scripts/<script>.py" <args>
```

Where reports persist (so they survive task workspace cleanup):

```
/home/.oh-my-agent/reports/<skill-name>/...
```

Use the skill's own `report_store.py persist` / `deal_store.py persist` to
write there — do not hand-roll absolute paths.

## What NOT to do

- **Don't `find / -name SKILL.md`.** SKILL.md is at
  `$OMA_AGENT_HOME/skills/<name>/SKILL.md` in your cwd.
- **Don't `cd /home/.oh-my-agent/agent-workspace`.** That's a shared dir
  outside your task's isolation. Stay in your cwd; everything you need is
  symlinked here.
- **Don't run scripts via system `python3`.** It lacks `yt-dlp`, `PyYAML`,
  etc. Always use `./.venv/bin/python`.
- **Don't `ls /repo` or probe for the source repo.** You don't have it.
