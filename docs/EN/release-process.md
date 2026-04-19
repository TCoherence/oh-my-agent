# Release Process

This is the playbook for cutting a new Oh My Agent release. It's intentionally short — we're a single-maintainer project, so the goal is a checklist that catches regressions, not a corporate release gate.

## Versioning

We follow [Semantic Versioning](https://semver.org/):

- **MAJOR** — contract-breaking change (config schema, YAML automation schema, slash command removal, runtime state machine change).
- **MINOR** — user-visible additive changes (new skill, new slash command, new subsystem).
- **PATCH** — bug fixes, doc fixes, internal refactors with no surface change.

Version source of truth: [`src/oh_my_agent/_version.py`](../../src/oh_my_agent/_version.py). The git tag uses `vX.Y.Z`.

## Release cadence

- No fixed cadence — cut a release when there's user-visible change worth shipping.
- Cut a patch when you land a bug fix that affects the current release line.
- Cut a minor when the Unreleased section has accumulated ~3+ items, or when a single large feature is ready.
- Major bumps are planned work; they get a tracking issue and a dedicated plan doc (see [v1.0-plan.md](v1.0-plan.md) for the shape of that).

## Pre-release checklist

Run these in order. Do **not** skip — they've each caught a real bug at least once.

### 1. Code is on main, tree is clean

```bash
git status             # clean, on main
git pull               # up to date
git log --oneline -10  # eyeball recent commits — anything surprising?
```

### 2. Tests pass locally in a clean venv

```bash
source .venv/bin/activate
pytest                 # full suite — all green
pytest -q              # check for warnings worth fixing
```

No skipped tests that should be running. No new warnings that drown the signal.

### 3. `/doctor` is green against a real bot

Start the bot against your staging Discord, run `/doctor`, and read every section:

- **Scheduler health** — no stale jobs; `reload_last_progress_at` is within the last few minutes.
- **Runtime health** — no stuck tasks; no HITL prompts older than you expect.
- **Memory** — entry count looks sane; no `parse_failure` spam in the last hour.
- **Auth** — every configured provider is `ok` (or intentionally `cleared`).

If any section is red, fix before tagging.

### 4. Manual automation smoke test

Trigger one of the bundled automations via `/automation_run` and confirm:

- The task enters `DRAFT` (or skips to `RUNNING` if `auto_approve: true`).
- Completion posts to Discord with the summary card.
- `/automation_status` shows the updated `last_run_at` and `next_run_at`.

### 5. Restart survives

```bash
# Kill the bot (SIGINT or Ctrl-C), wait ~5s, restart.
# Confirm in Discord:
#   - Any WAITING_USER_INPUT / WAITING_MERGE tasks are still waiting.
#   - Any DRAFT tasks are still DRAFT.
#   - The scheduler picked up where it left off.
#   - `/doctor` Scheduler health shows no stale jobs.
```

### 6. Changelog is current

`CHANGELOG.md` has an `## Unreleased` section with every user-visible change. Each item ends with a PR reference `(#123)` or a short SHA `(abc1234)`.

### 7. Docs match the code

Quick grep for version-dependent stale lines:

```bash
grep -rn 'v0\.[0-9]\|^504 tests' README.md CLAUDE.md docs/EN docs/CN
```

Anything mentioning an old version number should either still be accurate (migration notes in `upgrade-guide.md` legitimately reference `v0.8.x`) or needs updating.

## Cutting the release

### Step 1 — Bump the version

Edit [`src/oh_my_agent/_version.py`](../../src/oh_my_agent/_version.py):

```python
__version__ = "X.Y.Z"
```

### Step 2 — Close the changelog section

In `CHANGELOG.md`:

- Rename `## Unreleased` to `## vX.Y.Z - YYYY-MM-DD`.
- Add a fresh empty `## Unreleased` block at the top.
- If there are breaking changes, call them out in a **Breaking** subsection.

### Step 3 — Commit + tag

```bash
git add src/oh_my_agent/_version.py CHANGELOG.md
git commit -m "chore(release): cut vX.Y.Z"
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main vX.Y.Z
```

### Step 4 — Create the GitHub release

```bash
gh release create vX.Y.Z \
  --title "vX.Y.Z" \
  --notes-file <(awk '/## vX.Y.Z/,/## v/' CHANGELOG.md | head -n -1)
```

Or paste the changelog section manually via the GitHub UI.

## Post-release

- Announce in whatever channels you use. Keep it factual: what changed, what broke (if anything), how to upgrade.
- Watch `/doctor` and `service.log` for a day or two. Patch releases are cheap; ship a `X.Y.Z+1` the same day if you find a regression.
- Update any deployed instances. If the release includes schema migrations, confirm the startup-time migration log lines appear.

## Hotfix flow

For a critical fix (data loss, security, or bot-down bug) on a released version:

1. Branch from the release tag: `git checkout -b hotfix/vX.Y.Z+1 vX.Y.Z`.
2. Land the fix with a regression test.
3. Bump `_version.py` to `X.Y.Z+1`, add a `## vX.Y.Z+1 - DATE` block to the changelog.
4. Tag, push, release — same as above.
5. Merge the hotfix branch back into `main` (or cherry-pick if `main` has drifted).

## What not to do

- Do **not** publish a release from a dirty working tree.
- Do **not** cut a release with failing tests "because CI will catch it" — there is no CI gate on tags.
- Do **not** skip the `/doctor` check. It exists precisely to catch the things unit tests miss.
- Do **not** retag an existing version. If the release is wrong, cut a new patch.
