# Contributing to Oh My Agent

Thanks for your interest! This is a self-hosted, single-user project, so PRs are reviewed with that scope in mind: we prioritize reliability and clear operator UX over feature breadth.

For AI-agent contributors (Claude Code, Gemini CLI, Codex), see [AGENT.md (CLAUDE.md)](./CLAUDE.md) — it has the architecture brief and coding conventions. This file is for humans.

## Dev environment

```bash
git clone https://github.com/TCoherence/oh-my-agent.git
cd oh-my-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the full test suite before sending a PR:

```bash
.venv/bin/python -m pytest            # full suite
.venv/bin/python -m pytest tests/test_scheduler.py   # one file
.venv/bin/python -m pytest -k "watchdog"             # by substring
```

The project targets **Python 3.11+**. Tests must pass locally; the CI pipeline matches.

## Running the bot locally

```bash
cp .env.example .env                   # fill in DISCORD_TOKEN, etc.
cp config.yaml.example config.yaml     # tune channels and agents
oh-my-agent
```

The runtime state lives in `~/.oh-my-agent/` by default. Delete that directory to reset state.

## Pull requests

### What makes a good PR

- **Scope**: one coherent change per PR. Don't mix refactors with features.
- **Tests**: new behavior needs test coverage. Bug fixes should add a regression test that would have caught the bug.
- **No speculative abstractions**: three concrete call sites before we extract a helper. Don't add config flags for things nobody asked for.
- **Surface-area changes**: anything that changes a Discord slash command, config schema, automation YAML schema, or CLI output format should also update `docs/EN/` and `docs/CN/`.
- **Follow the existing style**: match the surrounding code. We don't enforce a formatter yet, but extreme inconsistency will get flagged in review.

### Commits

- Each commit should build and test green. We may squash on merge, but keep the intermediate history readable.
- **Do not include `Co-Authored-By` lines** in commits.
- Commit messages follow a lightweight convention: `scope: imperative summary`. Examples: `scheduler: add watchdog supervisor`, `docs: fix stale test count`. When in doubt, copy the style from `git log`.

### Changelog

Anything user-visible goes into `CHANGELOG.md` under the **Unreleased** section. Format: `- short description (#PR)`.

Release cuts move the Unreleased items into a numbered version block. See [docs/EN/release-process.md](./docs/EN/release-process.md) for the full release playbook.

### PR template

Include:

- **Summary** — 1-3 sentences: what changed and why.
- **Test plan** — what you ran to verify (`pytest`, manual `/doctor`, etc.).
- **Risk** — anything a reviewer should pay extra attention to (shared state, schema change, rate limit).

## What not to submit without discussion

These need a design discussion before implementation (open a GitHub issue first):

- A second platform adapter (Slack / Feishu / WeChat) — the `BaseChannel` contract is actively evolving for 1.0.
- Schema migrations for the SQLite store or automation YAML.
- Changes to the autonomous runtime state machine (`DRAFT → RUNNING → …`).
- Removing a bundled skill.
- Adding a new top-level config section.

For scheduler / memory / runtime internals, it's fine to open a PR directly with a clearly scoped change — just note the rationale in the description.

## Reporting security issues

**Do not** open a public issue. See [SECURITY.md](./SECURITY.md) for the disclosure process.

## Reporting bugs and requesting features

- **Bugs**: open a GitHub issue with reproduction steps, expected vs actual behavior, version (`oh-my-agent --version`), and environment (local / Docker).
- **Feature requests**: open a GitHub issue describing the use case first. Implementation discussion happens on the issue thread; PRs are welcome once the design is agreed.

## Code of conduct

Be kind. Critique the code, not the person. This project is a hobby for most contributors; assume good faith and keep review comments focused on making the change better.
