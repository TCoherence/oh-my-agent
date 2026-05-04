# Changelog

All notable changes to this project are documented in this file.

The format is intentionally lightweight and release-oriented rather than exhaustive.

## Unreleased

### Added

- **Dashboard launcher for the `scripts/docker-*.sh` path** — the prior dashboard rollout (PR #33) only wired `compose.yaml`, but production users running via `scripts/docker-run.sh` / `scripts/docker-start.sh` had no way to start the dashboard alongside the bot. This PR extends the script suite without breaking existing flows. `scripts/docker-common.sh` grows a parallel set of dashboard helpers (`oma_dashboard_container_exists`, `oma_dashboard_remove_existing_container`, `oma_dashboard_build_run_args`, `oma_port_is_free`, `oma_dashboard_pick_port`, `oma_dashboard_start_detached`) sharing the same env / mount / cap-drop conventions as the bot. `oma_port_is_free` tries `lsof` first, falls back to `nc -z`, and (if neither tool exists) optimistically returns success — the `docker run -p` itself fails fast if the bind is taken. `oma_dashboard_pick_port` walks `OMA_DASHBOARD_PORTS` (default `8080 8081 8088 8888 9090`) and returns the first available; missing all of them is non-fatal — bot keeps starting, dashboard is skipped with a clear message pointing at `OMA_DASHBOARD_PORTS=...` override. `scripts/docker-start.sh` now starts both: bot first, then `oma_dashboard_start_detached` after. `scripts/docker-stop.sh` stops the dashboard before the bot. `scripts/docker-status.sh` walks both containers and shows port bindings (so the operator can confirm which host port the dashboard ended up on without grepping logs). `scripts/docker-logs.sh dashboard` (or `--dashboard`) tails the dashboard's stdout; default still tails the bot. Successful dashboard launch prints `[oma] dashboard at http://127.0.0.1:<port>` to stdout — clickable in most terminals. Set `OMA_DASHBOARD_ENABLED=0` to skip the dashboard entirely; set `OMA_DASHBOARD_PORTS='9091 9092'` to override the candidate list; set `OMA_DASHBOARD_CONTAINER_NAME='...'` to rename the side container. monitoring.md §6.3 (EN + CN) split into §6.3.a (scripts path, listed first since that matches the `docker-run.sh`/`docker-start.sh` users) and §6.3.b (compose path, unchanged from PR #33).
- **Dashboard Docker deployment — second compose service + image preinstall**. Completes the `oma-dashboard` rollout (PR #32 shipped only the host-side dev workflow). `Dockerfile` now preinstalls `fastapi>=0.110,<1` + `uvicorn[standard]>=0.27,<1` + `jinja2>=3.1,<4` after the base requirements step (versions kept in lockstep with `pyproject.toml`'s `[project.optional-dependencies] dashboard`). `compose.yaml` adds a second service `oh-my-agent-dashboard` reusing the same image, mounting the same `oma-runtime:/home` volume, binding `0.0.0.0:8080` inside the container, and publishing `127.0.0.1:8080:8080` on the host. The `127.0.0.1:` prefix on the publish syntax is what makes loopback-only safe-by-default — Docker only listens on host loopback, not all interfaces. `OMA_FAIL_FAST_CLI=0` is set on the dashboard service so the entrypoint's CLI binary check (which the dashboard doesn't need) doesn't block startup. `depends_on: [oh-my-agent]` orders startup, but the dashboard's data layer already self-handles missing SQLite gracefully — first-load before the bot writes its DB shows placeholder cards, not 500s. **First-time setup requires `docker compose build` to bake the new deps into the image**; subsequent `docker compose up -d` starts both services. `docker compose up -d oh-my-agent` skips the dashboard entirely. monitoring.md §6.3 (EN + CN) rewritten with the operator recipe + the loopback-boundary security caveat (changing the publish to `0.0.0.0:8080:8080` for LAN access requires wiring auth first; the dashboard itself has none).
- **Read-only monitoring dashboard (`oma-dashboard`)** — opt-in standalone HTTP service that aggregates SQLite + log + memory state into a single HTML page, refreshed every 60s via `<meta refresh>`. Five sections: automation health (per-automation 7-day success rate, last run / next run, truncated last error), task / runtime health (current status distribution + 7-day terminal counts + recent 10 failures), cost & usage (7-day daily token/cost trend with inline SVG sparkline + today by source + today top-5 by skill), memory & skill (active vs superseded counts, by-category and by-scope breakdowns, 7-day new entries, per-skill 30-day invocation + success rate), and system layer (ERROR/WARNING rate from BOTH `service.log` AND `oh-my-agent.log` with 5-minute buckets over the last hour, disk usage for runtime tree, bot uptime parsed from the most-recent `Runtime started` line). Loopback-only by deployment convention (default bind `127.0.0.1:8080`); no auth; exposing on `0.0.0.0` without first wiring auth is unsafe and called out in `docs/EN/monitoring.md` §6.1. Lives at `src/oh_my_agent/dashboard/{cli,app,data}.py` plus `templates/dashboard.html`. Optional dep group `dashboard` adds `fastapi>=0.110,<1` + `uvicorn[standard]>=0.27,<1` + `jinja2>=3.1,<4`; base install unaffected. New entry point `oma-dashboard` in `[project.scripts]`. Reuses `oh_my_agent.paths` (PR #31 / Stage 0) for all path resolution — zero duplication with `boot.py` / `runtime/service.py`. Read-only SQLite via `sqlite3.connect("file:...?mode=ro", uri=True)`; concurrent-safe under WAL (verified by dedicated test in `tests/test_dashboard_data_concurrent.py` — writer thread + reader thread, 200 inserts, asserts no errors and monotonic count growth). All `fetch_*` helpers self-contain error handling: missing DB / missing log / unparseable YAML returns a placeholder dict with an `error` key; the template renders the placeholder verbatim instead of 500-ing. Severity strings match Python `record.levelname` (`ERROR` / `WARNING`) — Codex review caught and pinned that filtering on `WARN` would silently miss every warning line; tests fixture-assert this. Covered by 28 new tests across 4 files (`test_dashboard_data.py` 19 tests + `test_dashboard_data_concurrent.py` 2 tests + `test_dashboard_app.py` 5 tests + `test_dashboard_cli.py` 2 tests). Tests `pytest.importorskip("fastapi")` so the suite stays green on installs that didn't opt in. **This PR ships only the host-side dev workflow + tests**; the Docker deployment piece (compose.yaml second service + Dockerfile dashboard deps preinstall + monitoring.md §6.3 instructions) lands in the follow-up PR. Bind-mount users can already use `pip install -e '.[dashboard]'` + `oma-dashboard --config ./config.yaml` against the host-visible SQLite path; named-volume users wait for the next PR.
- **Weekly memory reflection — multi-scale "dream" pass on top of daily**. Where `DiaryReflector` (line 226-294 in `src/oh_my_agent/memory/diary_reflector.py`) reads a single day, the new `WeeklyReflector` (`src/oh_my_agent/memory/weekly_reflector.py`) reads the **trailing 7 complete days ending yesterday** — concretely `end_date = today - 1`, window `[end_date - 6, end_date]` inclusive. Targets the patterns daily can't see: recurring preferences across multiple days, evolving workflows, "the third time the user has asked about X" signals. Reuses the `JudgeStore.apply_actions` pipeline so dedup / supersede / confidence machinery is shared with the existing single-thread Judge. Strict-than-daily prompt rules: `add` requires evidence from **≥ 2 distinct dated diary sections** (single-day evidence forces `no_op` or `strengthen`); each evidence snippet must carry a `[YYYY-MM-DD]` citation; PR / issue / commit numbers and library versions explicitly banned alongside daily's existing blocklist; missing days render as `## --- YYYY-MM-DD --- (no diary)` placeholders so the prompt knows whether evidence is continuous. `WeeklyReflectionLoop` mirrors `DiaryReflectionLoop` with `(fire_dow_local, fire_hour_local)` instead of just hour — defaults to Tuesday 03:00 local (Monday activity lands in the window, offset from daily's 02:00 to avoid the same-clock-tick concurrency case). Naive local-time arithmetic shares daily's DST gotcha, scheduled to be fixed together when daily moves to timezone-aware. Wired into `boot.py` next to the existing diary-reflection block (gated on `judge_store`, `diary_dir`, and `memory.weekly_reflection.enabled=true`); `_shutdown` accepts and stops the new loop. Covered by 21 new tests in `tests/test_weekly_reflector.py`: window-boundary lock-down (`test_reflect_last_week_window_is_yesterday_minus_six_through_yesterday` — Codex-flagged most important test), missing-day placeholder, oversized-day truncation, total-length cap, fire-time math for four `(dow, hour)` corners, dow=7/-1 validation, loop lifecycle.
- **Daily memory reflection now defaults to enabled** in `config.yaml.example`. The `DiaryReflector` code has been in tree but gated behind `memory.diary_reflection.enabled=false` since v0.8 — meaning cross-thread memory consolidation never landed for any operator who hadn't explicitly flipped the bit. Default flipped to `true`; comment expanded to explain why. `boot.py` now logs an INFO hint when daily is detected disabled but `judge_store` and `diary_dir` are otherwise ready, surfacing the migration path in the console for operators upgrading from older configs.
- **AI daily Stage 2.2 — section-level checkpoint storage + paper-digest JSON reuse**. Follow-up to PR #23 (Stage 1.2: timeout no-retry surface), implementing the structural piece of `plans/market-briefing-daily-ai-0900-fail-patt-mutable-nest`'s Stage 2 inside the existing `skills/market-briefing/` (no skill split — that's deferred Stage 3 with explicit trigger criteria). Three coordinated changes: (1) New `references/section_schemas.md` formalises 4 AI-daily sub-section schemas — `frontier_radar` (8-lab signals), `paper_layer` (consumes paper-digest's `top_picks` JSON contract), `people_pool` (tracked + new candidates), `macro_news` (5-layer signals + cross-layer links). Each carries shared meta keys plus a section-specific body anchor used as the `section-status` validation gate. (2) `scripts/report_store.py` gains two new subcommands: `persist-section --domain ai --section <name> --markdown-file ... --json-file ...` writes to `~/.oh-my-agent/reports/market-briefing/daily/<date>/ai_sections/<name>.{md,json}` (mirrors deals-scanner's `references/<source>.{md,json}` layout); `section-status --domain ai --report-date <date>` returns a `{section: {complete, md_path, json_path, reason}}` map where `complete: true` requires both files exist + JSON parses + meta keys present + body anchor present (half-written or schema-invalid sections surface the actual reason — `md_missing` / `json_missing` / `json_parse_failed: <exc>` / `json_schema_invalid: <exc>` / `json_not_object`). Backed by helpers `build_section_paths`, `_validate_section_payload`, `persist_section`, `section_status`. AI domain-only by design (other domains raise `ValueError` if attempted); `VALID_AI_SECTIONS` constant exported. (3) `SKILL.md` AI daily workflow rewritten to enforce: step 0 calls `section-status` first to skip already-complete sections on a re-run; step 3 drafts each of the 4 sub-sections sequentially with `persist-section` called immediately after each one (no batching); `paper_layer` reads `~/.oh-my-agent/reports/paper-digest/daily/<TODAY>.json` directly instead of WebSearching arXiv (eliminates 5-15 search turns); step 4 aggregates the 4 sub-sections into the legacy `ai.md/.json` (preserved for backward compat + weekly synthesis). Adds an "Execution strategy (parallel preferred)" hint mirroring deals-scanner's [SKILL.md:149](skills/deals-scanner/SKILL.md:149) — telling the agent to fan out the 4 section drafts via Claude Code's `Task` tool when available, sequential fallback otherwise. **Whether the hint actually triggers Task is verified post-rollout, not assumed**: 3 recent deals-scanner runs had Task in their init `tools` list but 0 `tool_use name=Task` invocations, so the hint is documented-but-unverified across the codebase today. Stage 2.2's storage wins (checkpoint recovery + downstream composition for a hypothetical `tech-weekly` reading `frontier_radar.json` × 7 days) deliver regardless of whether parallelism actually happens. Covered by 8 new tests in `tests/test_market_briefing_report_store.py`: path layout, non-AI domain rejection, unknown-section rejection, `persist_section` meta normalization + body anchor validation, `section-status` 4-way completeness matrix (complete / json_missing / json_schema_invalid / md_missing), unparseable-JSON degenerate case, end-to-end round-trip with all 4 sections complete. Full suite: 1019 passed, ruff clean, mypy clean.

### Migration

Two `config.yaml` changes need manual application to existing local configs (template-only edits do **not** propagate):

1. **Daily reflection default flip**: in your `config.yaml`, set `memory.diary_reflection.enabled: true`. Without this, daily memory consolidation stays off — the loop never starts. The new boot-time INFO log will surface the same hint.
2. **Weekly reflection section**: copy the new `memory.weekly_reflection` block from `config.yaml.example` into your local `config.yaml`. Default `enabled: false` is intentional — opt in once daily has been running for at least a week so weekly has 7 days of diary to chew on. Defaults: `fire_dow_local: 1` (Tuesday), `fire_hour_local: 3` (03:00 local).

No automatic migration is performed. Settings layout is otherwise unchanged.

### Changed

- **Aggregation skills now return a structured chat summary instead of re-pasting the full Markdown body**. Affects `skills/{market-briefing,deals-scanner,paper-digest,youtube-podcast-digest}/SKILL.md`. The previous "you MUST end your turn with the full Markdown report body in your reply, verbatim" rule across these 4 skills had two compounding problems: (1) re-streaming a 5–30 KB persisted report as output tokens late in the run consumed real wall-clock, contributing to budget pressure (real incident: weekly `bdcf9908d735` 2026-05-03 — persist succeeded at 18:16, the trailing chat-body re-stream got killed by the 1500s wall at 18:22); (2) operator observation that compliance was already poor — most runs didn't faithfully paste the full body anyway, so the rule was simultaneously expensive and ineffective. Replaced with the same "structured chat summary + storage paths" pattern that `skills/seattle-metro-housing-watch/SKILL.md` (line 188+) and `skills/jensen-huang-speech-research/SKILL.md` (line 213+) have used since their introduction: each daily/weekly skill now spells out 4–6 required content blocks in its `## Final answer format` section (headline conclusion / per-section highlights / top picks with inline links / coverage notes / storage paths) and explicitly forbids both "Done." status notes AND verbatim full-body paste. The proper systemic fix — runtime serves the persisted file path directly so the agent doesn't re-stream content that's already on disk — remains tracked under `docs/EN/todo.md` and `docs/CN/todo.md` "Backlog (no version commitment)" as **"Long-output final delivery"**; this change is the interim relaxation while that lands. Per-skill required-content lists differ by report shape (deals-scanner: 简短结论 + Top picks + per-source snapshot + coverage; paper-digest: 一句话结论 + Top 3 picks + Watchlist + coverage gaps; youtube-podcast-digest: 本周一句话结论 + Top 3 集 + 5 group 速览 + 跨集观察 + coverage; market-briefing: headline + per-section highlights tied to each domain's section order + top picks + coverage), but all share the same anti-patterns in their `❌ Don't ...` lists. No code change, no schema change — pure SKILL.md prose update across 4 files.

### Fixed

- **Subprocess timeout no longer auto-retries; surfaced as operator-mediated "Re-run +30 min timeout" button**. Recent `market-briefing-daily-ai-0900` failure `c267ba34a29e` (5/1) showed the existing retry-on-timeout policy compounding cost without helping: claude-cli's subprocess was killed at the configured 1500s wall-clock, the runtime saw `error_kind=timeout` (in `_RETRY_BACKOFF_SECONDS`) and relaunched, the second phase hit the same 1500s wall, total 3410s wall-clock + ~2× spend for zero useful output. Diagnosis: when timeout fires mid-tool-use, the agent has no terminal `result` event to report progress, so the next run starts from scratch with the same bounded-budget shape — almost always the same outcome. Fix: removed `"timeout": (0,)` from `_RETRY_BACKOFF_SECONDS` ([service.py:144](src/oh_my_agent/runtime/service.py:144)) so timeout is now terminal-on-first-occurrence (parallel to how `max_turns` was already handled). The failure path in `_fail` ([service.py:3961](src/oh_my_agent/runtime/service.py:3961)) gains a parallel `error_kind == "timeout"` branch that calls a new `_surface_rerun_bump_timeout_button`, mirroring `_surface_rerun_bump_turns_button` line-for-line: mints a decision nonce, posts a Discord prompt with mention + budget-bump preview ("hit wall-clock `timeout` (1500s). Re-run with `timeout_seconds=3300` (+30 min)?"), surfaces a single button. Click flows through the existing decision pipeline as the new `rerun_bump_timeout` `DecisionAction` ([types.py:84](src/oh_my_agent/runtime/types.py:84)) — same nonce-consume + status-validation guards as `rerun_bump_turns`. Backing rerun handler `_rerun_task_with_bumped_timeout` mints a sibling task with `agent_timeout_seconds = parent.agent_timeout_seconds + _RERUN_BUMP_TIMEOUT_SECONDS_DEFAULT` (default `+1800` = 30 min); falls back to `_RERUN_FALLBACK_BASE_TIMEOUT_SECONDS = 600` when the parent had no override. Discord renders the button via `action_meta` at [discord.py:749](src/oh_my_agent/gateway/platforms/discord.py:749) ("Re-run +30 min timeout", primary style); `TaskService.disable_actions` for FAILED tasks now exposes both `rerun_bump_turns` and `rerun_bump_timeout` ([task_service.py:335](src/oh_my_agent/gateway/services/task_service.py:335)) — the runtime decides which one to surface based on `error_kind` (max_turns → turns button, timeout → timeout button), but the action whitelist for the FAILED state covers both. Existing test `test_timeout_retries_once_then_gives_up` flipped to `test_timeout_does_not_auto_retry_anymore` (asserts `_invoke_agent.await_count == 1` and zero retry events). Added 4 new tests in `tests/test_runtime_rerun_bump_turns.py` covering the timeout sibling lifecycle (fallback / explicit override / lineage events / notify+signal), 1 expanded test for the `_fail` routing matrix (`test_fail_surfaces_rerun_button_only_on_max_turns_or_timeout` — pins that max_turns and timeout each route to their own button, cli_error / no-response route to neither), and 2 new Discord-render tests in `tests/test_discord_channel.py` pinning the labels and primary-button styles for both rerun buttons.

## v0.9.4 - 2026-04-29

### Added

- **Workspace agent hint files + `$OMA_AGENT_HOME` SKILL.md placeholder** (follow-up to PR #15's symlink fix). The artifact-task symlink fix landed in #15 was necessary but insufficient: post-fix automation logs (`46a8623d`, `35bd5de279f3`, etc.) showed the agent still spending its first 3–6 turns on `find / -name SKILL.md`, `cd /home/.oh-my-agent/agent-workspace`, `ls .venv/bin/python` etc. before reading SKILL.md, because nothing in the bare task cwd told the agent that everything it needed was already symlinked there. Two coordinated changes close that gap: (1) New `WORKSPACE_AGENTS.md` source file at the repo root contains workspace-specific agent guidance ("don't `find /`, don't `cd /home/.oh-my-agent/agent-workspace`, use `./.venv/bin/python`, skills are at `./.claude/skills/<name>/SKILL.md`"). `_setup_workspace` (`boot.py`) now copies it as **three identical files** — `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` — into the agent workspace, so each CLI agent reads its own preferred system-context filename (Claude reads `CLAUDE.md`, Gemini reads `GEMINI.md`, codex reads `AGENTS.md`). The copy overwrites SkillSync's previously-generated AGENTS.md (which mixed dev-time repo rules with workspace skill listings — the wrong content for runtime agents). `RuntimeService._link_agent_workspace_into` (`runtime/service.py`) symlinks all three hint files into the task cwd alongside `.venv` / `.claude/skills/` / `.gemini/skills/` / `.agents/skills/`. Falls back to legacy SkillSync-generated AGENTS.md when `WORKSPACE_AGENTS.md` is missing. (2) New `OMA_AGENT_HOME` env var (`.claude` / `.gemini` / `.agents`) exported from each `BaseCLIAgent` subclass via a class attribute (`ClaudeAgent._oma_agent_home = ".claude"`, etc.) and threaded through `_build_env()`. The 8 automation skills (`bilibili-video-summary`, `deals-scanner`, `market-briefing`, `paper-digest`, `scheduler`, `seattle-metro-housing-watch`, `youtube-podcast-digest`, `youtube-video-summary`) had their script invocations rewritten from `./.venv/bin/python skills/<name>/scripts/...py` to `./.venv/bin/python ${OMA_AGENT_HOME}/skills/<name>/scripts/...py` so Bash subprocess expansion routes to the correct per-agent dir at runtime. The dev-side skills (`adapt-community-skill`, `skill-creator`) keep bare `skills/<name>/...` because they run in merge-flow git worktrees where `skills/` is the real source dir, not in `_artifacts/<task_id>/`. Covered by 4 new tests in `tests/test_skill_sync.py` (hint files copied, content overwrites SkillSync output, idempotent on re-run, no-op when `WORKSPACE_AGENTS.md` missing), 1 new test in `tests/test_runtime_service.py` (`_prepare_task_workspace` symlinks the three hint files), and 3 new tests in `tests/test_claude_agent.py` (each of `ClaudeAgent` / `GeminiCLIAgent` / `CodexCLIAgent` exports the right `OMA_AGENT_HOME` value).

- **Streaming reply with Discord rate-limit throttle** (opt-in, off by default). When `gateway.streaming.enabled: true`, the gateway posts one anchor message at the start of each agent run (placeholder `⏳ *thinking…*`) and edits it in place as partial text arrives. A new `StreamingRelay` helper (`src/oh_my_agent/gateway/stream_relay.py`) collapses edits inside `min_edit_interval_ms` (default 1000 ms) into a single trailing flush so the Discord edit bucket (~1/s per message) never runs dry; on finalize it renders the full reply (with usage audit) onto the anchor and spills any overflow past the 2000-char cap to follow-up messages via `chunk_message`. Implemented by threading a new `on_partial: PartialTextHook` kwarg from `GatewayManager._run_registry_with_progress_logging` → `AgentRegistry.run` → `_run_single_agent` → each CLI agent's `run`. `BaseCLIAgent._run_streamed` consumes `agent.stream()` events, accumulates `TextEvent.text`, and fires `on_partial(accumulated)` on each tick. Both fresh and `--resume` turns now go through the streaming path (images still fall back to block mode — argv augmentation hasn't been merged with streaming yet). Channels gate the relay via a new `BaseChannel.supports_streaming_edit` class flag (Discord overrides to `True`); unsupported channels ignore the config knob. Covered by `tests/test_stream_relay.py` (11), `tests/test_manager_streaming.py` (6), and `tests/test_cli_resume_streaming.py` (4 — pins Claude/Codex/Gemini resume turns stream via `_run_streamed(command=...)` and that resume+image still block).
- **`/task_suggest` accepts `max_turns` / `timeout_seconds` budget overrides** (slash + Discord modal). The optional kwargs are threaded through `TaskService.decide()` → `TaskDecisionEvent` → `handle_decision_event`'s suggest / request_changes branches, which write `agent_max_turns` / `agent_timeout_seconds` onto the task row before re-queueing. The next run honors the new budget via `AgentRegistry._temporary_max_turns` / `_temporary_timeout`. The `task.suggested` event payload records `max_turns_override` / `timeout_seconds_override`; a `Budget override: max_turns → … · timeout → …s` line is appended to the surfaced suggestion text. Slash validation uses `app_commands.Range[int, 1, 500]` / `app_commands.Range[int, 1, 86400]` for Discord-native enforcement.
- **Discord Modal on the `Suggest` button**. Clicking the button now opens a Discord modal with three fields — suggestion (required, up to 2000 chars), max_turns (optional, positive integer), timeout_seconds (optional, positive integer). Strict integer validation via `_parse_optional_positive_int`: non-integer or `≤ 0` inputs surface an ephemeral error and the decision is not applied. Previously the button called `decide()` without a suggestion and with no way to pass budget overrides; users had to fall back to `/task_suggest`.
- **Streaming heartbeat + tool-activity surfacing**. Two follow-ups to the streaming MVP so users don't stare at a frozen `⏳ *thinking…*` while Claude chews on a long tool chain: (1) `StreamingRelay.start()` now spawns a heartbeat coroutine that rewrites the placeholder every `heartbeat_interval` seconds (default 3 s, well above the 0.5 s `min_edit_interval_ms` floor) with an `(Ns)` elapsed-time suffix and, if a tool has been seen, a `using <tool>` cue. The heartbeat auto-cancels on the first `update()`, `finalize()`, or `error()`. (2) New `ToolUseHook = Callable[[str], Awaitable[None]]` type alias (`agents/base.py`) plus `StreamingRelay.note_tool_use(name)` counter. Wired through `BaseCLIAgent._run_streamed` (emits the hook on every `ToolUseEvent`), `ClaudeAgent.run` / `CodexCLIAgent.run` / `GeminiCLIAgent.run`, `AgentRegistry.run` (`inspect.signature` gate — legacy agents without `on_tool_use` silently pass through), and `GatewayManager._run_registry_with_progress_logging`. On `finalize()` the attribution line gets a `· 🔧 N tool(s)` suffix (pluralized), so the final message carries a cheap summary of tool activity without leaking any arguments or output. Gemini emits a single final JSON blob today, so the hook is a no-op there — accepted for API symmetry. Covered by `tests/test_stream_relay.py` (8 new tests: `note_tool_use` counter, singular/plural/none attribution, heartbeat elapsed time, heartbeat tool-name injection, heartbeat cancellation on `update` + `finalize`), `tests/test_cli_resume_streaming.py` (3 new: Claude multi-tool stream, `on_tool_use` alone activates streaming, Codex command_execution → Bash hook), `tests/test_manager_streaming.py` (2 new: hook threads through registry, streaming-off doesn't forward it), and `tests/test_agent_registry.py` (2 new: hook forwarded when signature accepts it, legacy agents without the kwarg are not broken).
- **Streaming tool-trail moved to its own `-#` subtext line**. Follow-up to the heartbeat/tool surfacing entry above — two visible problems in the first cut: (a) the heartbeat wrote `⏳ thinking… · (12s) · using Edit` into the message *body*, so tool status visually fought the model's text output; (b) any tool event after the first `TextEvent` was invisible until `finalize` because the heartbeat had already been cancelled, so on a long Skill run users saw the first chunk and then a frozen screen. `StreamingRelay` now renders up to two attribution subtext lines: line 1 is the existing `-# via **claude**` (plus `⏳ (Ns)` while the heartbeat is still alive), line 2 is a new `-# 🔧 A · B · C (+N)` live trail driven by `_tool_trail: list[str]`. Tool events during the streaming phase trigger a throttled anchor edit (same pending-flush window `update` uses) so the trail keeps refreshing after the first text chunk lands. Consecutive duplicate tool names dedup (5 sequential `Bash` calls render as one `Bash` entry); overflow past `_TOOL_TRAIL_VISIBLE = 3` collapses into a `(+N)` marker. On `finalize` the trail line drops and the attribution collapses back to the single-line `🔧 N tools` summary. Covered by 4 new tests in `tests/test_stream_relay.py`: trail appears during the streaming phase, dedup of consecutive duplicates, `(+N)` overflow past 3 distinct names, finalize drops the live trail. `test_heartbeat_includes_tool_name_when_known` is renamed to `test_heartbeat_surfaces_tool_trail_on_second_subtext_line` with updated assertions (body stays a calm `*thinking…*`, tool name lives on its own `-#` line).
- **CI pipeline (GitHub Actions) with three gates: `ruff` → `mypy` → `pytest`**. New `.github/workflows/ci.yml` runs on every push-to-main and every PR on Python 3.12 (Ubuntu). `ruff check src tests` uses the minimal ruleset `E4/E7/E9/F/I` (pycodestyle errors + pyflakes + isort); `mypy src` runs with `ignore_missing_imports`, `no_implicit_optional`, `warn_unused_ignores` on all 68 source files with zero errors and **no per-module overrides**. `pytest -q` finishes in ~1 min. `dev` extras now include `ruff>=0.13`, `mypy>=1.13`, and `types-PyYAML`. Introduced in PR #4 (pytest baseline), PR #5 (ruff + mypy introduction + 88 → 0 mypy fixes), and the three follow-ups #6/#7/#8 that cleared the mypy `ignore_errors` overrides on `automation/scheduler.py`, `runtime/service.py`, and `gateway/platforms/discord.py` respectively.
- **External push notification provider (Bark)** (#14). New top-level `notifications` config + `PushDispatcher` wrapper, distinct from the in-Discord `NotificationManager`. Six event kinds (`mention_owner`, `task_draft`, `task_waiting_merge`, `ask_user`, `automation_complete`, `automation_failed`) with per-kind allow-list and per-kind Bark `level` (`passive` / `active` / `timeSensitive` / `critical`) so OS focus / DND modes can no longer bury time-critical events. `BarkPushProvider` POSTs JSON via stdlib `urllib.request` in `asyncio.to_thread` (no new dependency); device key is read from an env var so the secret never lives in YAML. Default disabled (`enabled: false`); only fires when `task.automation_name` is set so manual `/task_start` runs never ring. `_check_notifications` config validator errors on unknown providers / missing `device_key_env` / invalid level enum and warns when the named env var is unset.
- **In-flight tasks listed in `/automation_status`** (#13). The status table now expands each automation row with any active runs in `DRAFT` / `PENDING` / `RUNNING` / `VALIDATING` / `BLOCKED` / `PAUSED` / `WAITING_USER_INPUT` / `WAITING_MERGE` so the operator can see whether a long-running automation (e.g. `market-briefing` with `timeout_seconds=1200`) is mid-flight without falling back to `/task_list`. Dispatch path stays unchanged.
- **Worktree dev config template + setup guide** (#12). Closes the "every worktree needs a manual `config.yaml` symlink, prod config pollution risk" backlog item. New `~/.oh-my-agent/dev-config.yaml` template re-points runtime paths under `~/.oh-my-agent-dev/` and switches Discord token / channel to `DEV_*` env vars; `docs/EN/dev-environment.md` walks through the SessionStart hook that auto-symlinks `config.yaml` → dev-config when `$PWD` matches `*/oh-my-agent/.claude/worktrees/*`. Pytest path unaffected (no config read); only real-bot end-to-end testing benefits.

### Changed

- **Scheduler now uses a central wall-clock due scanner instead of per-job long sleepers**. Replaces the "one asyncio task per job, each doing `asyncio.sleep(delay)`" model with a single due loop that ticks every ≤30s, reads wall clock, and dispatches any job whose `next_fire_at` has passed. After host suspend (e.g. Docker-on-Mac laptop sleep), scheduled jobs now recover within one scanner tick instead of being silently skipped by the watchdog. No-backfill semantics unchanged: one fire per overdue window. Behavior changes for `/automation_run`: (a) returns immediately after dispatch (was: waited for the fire's enqueue step); (b) refuses if the job is already firing (was: ran concurrently).
- **COMPLETED watermark now lags channel notify** (reply / artifact path). The completion branch in `_execute_task_loop` used to write `status=COMPLETED` before calling `_notify`, so a poller reading the DB could see `COMPLETED` before the channel message landed (and if `_notify` raised, the message would never land — the task still reported COMPLETED). The reorder runs `_notify` first, wraps it in `try/except`, and only flips the DB row to `COMPLETED` after a successful send. On failure the task is marked `FAILED` with `error=notification_failure: …`, a `task.notification_failed` event is recorded, and `summary` / `output_summary` / `artifact_manifest` are persisted so a future resend can rebuild the completion text without new schema. Scope: reply / artifact path only — merge flow (`WAITING_MERGE`) and `_fail` still have the same race by design and are out of scope for this change. New regression test `test_notify_failure_marks_failed_without_false_complete` pins the four invariants (status, error prefix, persisted rebuild fields, absence of `task.completed` event).
- **Single published artifact path** (`runtime.reports_dir/...`). `_archive_artifact_files` is replaced by `_publish_artifact_files` with four rules: (a) files already under `reports_dir/` are reused in place (no copy); (b) workspace files under `reports/<sub-tree>/…` are mirrored to `reports_dir/<sub-tree>/…` preserving structure — canonical collisions overwrite in place, no suffix; (c) other workspace files fall back to `reports_dir/artifacts/<basename>` with a `-<task_id[:8]>` suffix on basename collisions; (d) absolute paths outside both `workspace_path` and `reports_dir` follow the same flat fallback as (c). User-facing "Archived to:" → "Published to:" — the absolute published path is now the primary answer to "where is my artifact?", with `Delivered via: <mode>` / `Scratch (ephemeral): _artifacts/<task_id>/` demoted to subordinate transport detail. Follow-up thread seeders and `automation_posts.artifact_paths` already use the published paths (durable; `reports_dir/` is not janitor-pruned).
- **Canonical 5-intent router + unified completion rendering** (#19). Router intents collapsed from 6 into a 5-intent canonical set: `chat_reply`, `invoke_skill`, `oneoff_artifact`, `propose_repo_change`, `update_skill`. Legacy names (`reply_once`, `invoke_existing_skill`, `propose_artifact_task`, `propose_repo_task`, `propose_task`, `create_skill`, `repair_skill`) keep working as aliases — a new `normalize_intent()` helper maps them to canonical at parse time, so cached router prompts and older models keep working. `update_skill` collapses the prior `create_skill` + `repair_skill` split; the dispatcher decides create-vs-repair by checking `skill_name` against `_known_skill_names()` (registered → repair branch with `_build_skill_repair_request`, unknown → create branch). On the rendering side, `_completed_text` is now a `completion_mode`-keyed dispatcher: `reply` → new `_artifact_reply_text` (shared by both manual artifacts and scheduler runs, `**Output**` header + body verbatim + `-#` subordinate notes for `Published to:` / scratch / transport), `merge` → existing `Task <id> completed.` layout. Retired `_automation_completed_text` along with the path-eating heuristic helpers (`_is_automation_note_paragraph`, `_is_automation_prep_paragraph`, `_split_automation_output`, `_normalize_automation_note`, `_automation_artifact_preview`); the agent's `output_summary` now renders verbatim with only `TASK_STATE` / `BLOCK_REASON` markers stripped. Single-file artifact preview kept as a fallback inside the unified renderer (when the agent reply is empty and the workspace contains exactly one small text artifact).
- **Skill invocation paths unified through `create_artifact_task`** (#20). The three skill entry points — explicit `/skill_name`, router-resolved `invoke_skill`, scheduler cron — now flow through `RuntimeService.create_artifact_task(skill_name=...)`. Explicit and router-resolved invocations carry `auto_approve=True` (the user already pointed at a specific skill, no second-step approval needed), with SKILL.md `metadata.timeout_seconds` / `max_turns` forwarded as `agent_timeout_seconds` / `agent_max_turns` so a long-running skill like `market-briefing` (1200s) keeps its budget. Unresolved router invocations (model said `invoke_skill` but `skill_name` does not match a registered skill) fall back to inline `AgentRegistry.run` so the agent can still answer with whatever context exists. Side benefit: every skill run now gets a workspace, an artifact manifest, the unified `**Output**` reply layout, and the `Published to:` block for files written under `reports_dir`. Long terminal text now chunks via `chunk_message` instead of hard-truncating at 1900 chars; short text keeps the single-`await` fast path so existing `_wait_for_status` test fixtures aren't perturbed (the underlying race between DB-status writes and channel side-effects on the WAITING_MERGE path is documented in `docs/EN/todo.md` Backlog).
- **Strengthened final-answer guidance for 6 file-producing skills** (#10). `bilibili-video-summary`, `deals-scanner`, `market-briefing`, `paper-digest`, `seattle-metro-housing-watch`, `youtube-video-summary` — Step 5 / final-reply guidance now explicitly tells the agent to paste the full markdown report into the Discord reply (Gateway auto-chunks past 2000 chars), not just a `Saved: <path>` line, because users cannot open files from Discord. Closes a recurring failure mode where automation messages rendered "Run completed" with the report locked behind a published path.

### Fixed

- **Streaming reply: Gemini no longer leaks raw JSON to users + `min_edit_interval_ms` floor**. Two follow-ups to the streaming MVP: (1) `GeminiCLIAgent` now overrides `_parse_stream_line` so the single `{"response":"…","session_id":"…","stats":{…}}` line that `--output-format json` emits is decoded into `SystemInitEvent` + `TextEvent` + `UsageEvent` — previously the base fallback wrapped the whole JSON line as a plaintext `TextEvent`, meaning every resume/streamed Gemini turn dumped a raw JSON literal onto Discord. Plaintext lines still fall through to the default `TextEvent` path so a future token-streamed Gemini CLI keeps working. (2) `GatewayManager.__init__` now floors `gateway.streaming.min_edit_interval_ms` at 500 ms (Discord allows ~5 edits / 5 s per message, so anything lower trips 429s under a busy stream); an explicit `0` is preserved as an opt-out so tests and ops experiments can keep bypassing the throttle. Covered by `tests/test_cli_resume_streaming.py` (new `test_gemini_resume_runs_through_streaming_when_on_partial` uses realistic JSON payload + new `test_gemini_plaintext_stream_lines_still_forwarded` pins the plaintext fallback) and `tests/test_manager_streaming.py::test_streaming_min_edit_interval_ms_is_floored`.
- **`_artifact_paths_for_task` now accepts absolute manifest entries explicitly** (Rule 4 reachable end-to-end). Prior to this fix the collector did `workspace / rel_path` blindly and early-returned `[]` for tasks without `workspace_path`; absolute `artifact_manifest` entries only survived by a Python pathlib quirk (`Path("/ws") / "/abs" → Path("/abs")`), so `_publish_artifact_files` Rule 4 was effectively unreachable from the real task flow — any future path-normalization refactor (`candidate = workspace / rel_path.lstrip("/")`, for example) would silently drop absolute entries. The rewrite branches explicitly: absolute entries pass through, relative entries join with `workspace_path` only when one exists. Also tightens automation completion prose to the single-string contract — `_automation_completed_text` now emits Title-case `Published to:` everywhere (no lowercase `published:` drift) and labels workspace-relative fallbacks as `Workspace artifact (scratch):` / `Workspace artifacts (scratch):` so they can't be mistaken for durable published paths. Covered by new regression tests `test_publish_rule_4_end_to_end_via_task_flow` (pipeline-level Rule 4 pin via `_deliver_artifacts`), `test_decide_suggest_promoted_to_request_changes_applies_budget` (pins the `suggest → request_changes` promotion path that forwards `max_turns` / `timeout_seconds` budget overrides into the `request_changes` handler on `WAITING_MERGE` tasks), and `test_automation_completion_text_uses_title_case_published_label`.
- **Latent NameErrors and missing None-guards surfaced by the new mypy/ruff gate**. Cleanup fallout from introducing static analysis (PR #5): (a) `boot.py` referenced an undefined local `agents` in the diary-reflection gate — simplified to rely on `channel_pairs` alone, which already implies a registry was built. (b) `runtime/service.py` auth-suspend cancel path used `with suppress(asyncio.CancelledError)` without importing `suppress` from `contextlib` — would `NameError` on the exact path it was defending. (c) `runtime/worktree.py::run_shell` returned `proc.returncode` (typed `int | None`) from a signature promising `int` — coerced to `-1` when a SIGKILL race leaves it unset. (d) `gateway/manager.py` accessed `auth_challenge.provider` / `auth_challenge.reason` at six sites after an early-return that only excluded both-None, not single-None; added an explicit `assert auth_challenge is not None` matching the real control-flow invariant.
- **Artifact tasks no longer burn 8–15 turns probing for SKILL.md / `.venv` from a bare cwd** (caused recent `deals-scanner` / `market-briefing` / `youtube-podcast-digest` automations to hit `terminal_reason: max_turns` on 60–90-turn budgets). `_prepare_task_workspace` used to create `_artifacts/<task_id>/` as an empty directory: SKILL.md's literal `./.venv/bin/python` couldn't resolve and Claude CLI's parent-walk skill discovery had no `.claude/skills/` to find, so the agent re-searched the filesystem each run via `find / -name SKILL.md`, `ls /repo/.venv`, etc. — JSONL agent logs showed 5 of 5 recent failures spending their first 8–15 turns on this dead-weight discovery, leaving too few turns for the actual aggregation. `RuntimeService.__init__` now accepts an `agent_workspace: Path | None = None` kwarg (threaded from `boot.py:_setup_workspace`'s return value), and `_prepare_task_workspace` calls a new `_link_agent_workspace_into(workspace)` helper that idempotently symlinks `.venv`, `.claude/skills`, `.gemini/skills`, `.agents/skills` from `agent_workspace` into the task cwd when their targets exist. The agent now resolves `./.venv/bin/python` and discovers skills on turn 1; cleanup is unaffected because `shutil.rmtree(..., ignore_errors=True)` does not follow symlinks (it unlinks them and leaves the agent-workspace targets intact). Companion fix in `_list_workspace_files`: switched from `rglob("*")` (which followed symlinks and would have inflated `_collect_changed_files` results with the entire 50 MB+ linked venv site-packages tree) to an explicit stack walk that skips any symlink. `agent_workspace=None` keeps the legacy bare-directory behavior for backward compat. Covered by three new tests in `tests/test_runtime_service.py`: `test_prepare_task_workspace_links_agent_workspace_dirs` (symlinks created + idempotent on re-prepare), `test_prepare_task_workspace_no_agent_workspace_skips_linking` (legacy path), `test_list_workspace_files_skips_symlinks` (50 MB venv-noise inflation guard).
- **Automation markdown body restored in completion messages** (#18). Two stacked issues had been silently swallowing paper-digest / market-briefing reports in the automation-dump channel — users saw a literal `**Output**\nAutomation run completed.` followed by a scratch path, with the actual markdown nowhere. (a) `_is_automation_note_paragraph` treated *any* paragraph containing both backtick and slash as status chatter, so any markdown section mentioning a path or inline-coded token (very common in report bodies) got demoted into a `-#` note and dropped from the rendered Output. (b) `_collect_changed_files` only listed files inside the task workspace, but skills like paper-digest persist via `report_store.py persist` to absolute paths under `reports_dir`. The empty `artifact_manifest` short-circuited both `_publish_artifact_files` (no `Published to:` block) and `_automation_artifact_preview` (no markdown preview), and the renderer fell through to the literal-string fallback. Drop the broad backtick+slash classifier (verb-prefixed matches like `Saved/Wrote/Persisted to/...` still catch real chatter) and add a `_scan_reports_dir_writes` hook that surfaces files newer than `task.started_at` from `reports_dir` as absolute manifest entries; the existing publish Rule 1 reuses them in place. Subsumed by PR #19's broader render unification, which retired the heuristic helpers entirely.
- **Cooperative shutdown + WAL checkpoint on close** (#16). `GatewayManager.stop()` now signals the Discord gateway to drain, cooperatively shuts down in-flight runtime workers (with subprocess cancellation deadlines), and runs an explicit SQLite WAL checkpoint before exit; `main.py` hooks `SIGINT` / `SIGTERM` into this path. The previous shutdown sequence could leave SQLite in a non-checkpointed state if the process was SIGTERMed mid-write, which on slow disks pushed WAL recovery time on the next start past acceptable thresholds. Eliminates a recurring "first start after restart is slow" symptom on the Docker-on-Mac deployment.

## v0.9.3 - 2026-04-19

### Added

- **Runtime retry by `error_kind` + "Re-run +30 turns" button** (Phase 3 of runtime agent-invocation UX, plan `3-diverge-refactor-snuggly-lerdorf`).
  - New `_invoke_agent_with_retry` wrapper in `RuntimeService`: transient kinds `rate_limit` / `api_5xx` / `timeout` retry with per-kind backoff (10s→30s / 5s→15s / one 0s retry); total retries across kinds capped at 3 per call (`_MAX_TOTAL_RETRIES`). Terminal kinds (`max_turns` / `auth` / `cli_error`) never retry and still propagate to the `AgentRegistry` fallback loop. Each retry logs `Runtime task=<id> retry=<n>/<max> kind=<k>` and writes a `task.agent_retry` event.
  - When a runtime task fails with `error_kind=max_turns`, `_fail` now posts an interactive decision surface ("Re-run +30 turns"). Clicking it creates a sibling task with `agent_max_turns = parent + 30` (fallback base 25 if parent left it unset), emits `task.rerun_sibling_created` on the parent and `task.created` on the sibling. Surface text @-mentions configured owners and advertises its TTL (from `runtime.decision_ttl_minutes`, default 24 h).
  - New `DecisionAction` value `rerun_bump_turns`; Discord button label "Re-run +30 turns" (primary); `TaskService.disable_actions` exposes this single action on `FAILED` status.
  - Scope: runtime task path only (`/task_start`, automations). Chat-path max_turns UX (`AgentRegistry.run()` from `/ask` / slash skills) stays out of scope per the original plan — users retry manually.
  - Tests: `tests/test_runtime_agent_retry.py` (10) covers retry dispatch per kind, per-kind + global caps, retry-then-success path; `tests/test_runtime_rerun_bump_turns.py` (9) covers sibling task creation, lineage events, surface mentions/TTL, and that `_fail` only surfaces the button on `max_turns`.
- **Automation follow-up via Discord reply** (MVP). Replying to any automation-posted channel message now spawns a Discord thread rooted on that message, seeded with a system turn that lists the original run's archived artifact paths. The agent continues in that thread as a normal conversation (CLI session is **not** resumed — artifact paths are injected as context instead; see [docs/EN/todo.md](docs/EN/todo.md) backlog for the optional `--resume` upgrade).
  - New `automation_posts` SQLite table: `(platform, channel_id, message_id)` primary key with `automation_name`, `fired_at`, `artifact_paths` (JSON), `agent_name`, `skill_name`, `task_id`, `follow_up_thread_id`. `CREATE TABLE IF NOT EXISTS` so existing DBs upgrade in place.
  - Runtime `_send_automation_terminal_message` now captures the first outbound message id and records an `automation_posts` row with the run's archived (or delivered) paths; `_notify` threads the paths through a new `automation_artifact_paths` kwarg.
  - `BaseChannel.create_followup_thread(anchor_message_id, name) -> str | None`: new default-None helper; Discord implementation uses `Message.create_thread()` on the target channel.
  - `IncomingMessage.reply_to_message_id` carries Discord's `message.reference.message_id` into the manager. `GatewayManager._handle_message_impl` looks up the anchor post; on hit, creates the follow-up thread, seeds history with `[Follow-up on automation 'X'. Artifacts: ...]`, and persists `follow_up_thread_id`.
  - 7-day TTL enforced by the runtime janitor (`AUTOMATION_POST_TTL_DAYS`, one tick per `_cleanup_interval_minutes`); expired rows are silently dropped.
  - Tests: `tests/test_automation_posts.py` (9) covers CRUD, upsert, follow-up-thread persistence, list order, TTL purge (including 0/negative ttl no-op), and manager-level reply routing with and without a matching post.
- **Artifact archive directory** (`runtime.reports_dir`, default `~/.oh-my-agent/reports`). Every `completion_mode=reply` artifact is now also copied flat into `<reports_dir>/artifacts/<filename>` so users can find reports without digging through `_artifacts/<task_id>/`. Filename collisions get a `-<task_id[:8]>` suffix. The completion message surfaces a new `Archived to:` block with the absolute archive path. The isolated task workspace is still cleaned by the janitor per retention policy; the archive copy is not auto-pruned. Set `runtime.reports_dir: ""` (or `false`) to disable archiving and keep pre-v0.9.3 behavior.
- `ArtifactDeliveryResult.archived_paths` (defaults to `[]`) threaded through `RuntimeService._deliver_artifacts` / `deliver_files()` for future non-task callers.
- **Task-model catalog doc** (`docs/EN/task-model.md` + `docs/CN/task-model.md`): single source of truth for the 3 task types, 3 completion modes, 6 router intents, 17 statuses, message→task flow, artifact delivery path, and known sharp edges (router threshold 0.55, artifact workspace lacking bundled skills, budget defaults, silent attachment-upload fallback, archive retention). Linked from both README TOCs + `CLAUDE.md` Runtime layer.
- `config-reference.md` (EN + CN): `runtime.reports_dir` row.
- Tests:
  - `test_artifact_task_completes_without_merge` now asserts the archived file exists under `<reports_dir>/artifacts/` and appears in the completion message's `Archived to:` block.
  - `test_artifact_task_archive_suffixes_conflicting_filename`: two tasks producing the same filename → second file gets `-<task_id[:8]>` suffix.
  - `test_artifact_archive_disabled_when_reports_dir_empty`: `reports_dir=None` short-circuits `_archive_artifact_files()`.

### Changed

- `config.yaml.example` + local `config.yaml`: added `runtime.reports_dir` key.
- **Skill turn budgets**: `deals-scanner`, `market-briefing`, `youtube-podcast-digest` now set `metadata.max_turns: 60` — previously only `timeout_seconds` was set, so claude fell back to its default 25-turn budget on multi-source fetch-then-aggregate workflows and hit `error_max_turns`. Same trap that was closed for `paper-digest` (a43b903) and `seattle-metro-housing-watch` (369affe).

### Fixed

- **Claude CLI failure path: JSONL-aware parsing**. `ClaudeAgent.run()`'s error branch used `json.loads(stdout)` which silently failed on multi-line NDJSON output (`system.init` → `assistant` → `user` → `result`), leaving `error_kind` as `cli_error` instead of classifying `error_max_turns` → `max_turns`. Reuse `_parse_claude_stream_json` so the last `type=result` frame is always read; when no result frame exists (CLI killed mid-stream), fall back to `classify_cli_error_kind` on stderr. Regression tests: `tests/test_claude_agent.py::test_claude_error_max_turns_with_ndjson_stdout` + `test_claude_ndjson_without_result_frame_falls_back_to_cli_error`. Observed in prod 2026-04-19: seattle-metro-housing-watch weekly hit max_turns but `AgentRegistry.run()` fell back to codex instead of short-circuiting, because the final frame's `subtype=error_max_turns` never reached the classifier.

## v0.9.2 - 2026-04-18

### Added

- **Scheduler liveness watchdog + `next_run_at` authority fix** (closes backlog item [docs/EN/todo.md:243](docs/EN/todo.md), addresses the 2026-04-18 08:30 PDT paper-digest-daily-0830 10-hour stall incident).
  - `Scheduler` now tracks per-job runtime state (`phase`, `next_fire_at`, `fire_started_at`, `last_progress_at`, `last_restart_at`, `last_restart_reason`) and exposes a read-only health API (`list_job_runtime_state()`, `get_job_runtime_state()`, `get_reload_runtime_state()`, `evaluate_job_health(now)`).
  - `evaluate_job_health(now)` is the single source of truth for stale detection. Two rules: (A) task completed unexpectedly while `_stop_event` not set, and (B) `phase=="sleeping"` past `next_fire_at + grace` without progress. In-flight `firing` jobs are never flagged, so long-running automations like `market-briefing` (`timeout_seconds=1200`) do not trigger false positives.
  - `GatewayManager._run_scheduler_supervisor()` polls health every 60s and restarts stale jobs or the reload loop via `restart_job(reason)` / `restart_reload_loop(reason)`. Restart calls are rate-limited (`min_restart_interval_seconds=120`, plus `restart_in_progress` re-entry guard) so repeated supervisor ticks do not thrash.
  - `/doctor` gained a **Scheduler liveness** section: stale findings expanded with reason + `next_fire_at` + `last_progress_at`; when nothing is stale, shows the first 8 active jobs with `phase` + `next_fire_at`, plus `reload_last_progress_at`, plus any supervisor restart history from the last 24h. No catch-up/backfill — missed runs are covered by `/automation_run`.
  - `GatewayManager._dispatch_scheduled_job` now refreshes `next_run_at` via a shared `_refresh_automation_next_run_at` helper on **every** exit path (normal completion, failure, no-live-session, DM missing target, DM unsupported) via outer try/finally. Refresh helper catches its own exceptions without re-raising so it never masks main-path errors.
- **Test coverage expansion: 561 → 620 tests** (+59):
  - `tests/test_scheduler_watchdog.py` (10): health evaluation rules A/B/reverse, restart rate-limiting, restart reload loop, runtime-state snapshot APIs, `compute_job_next_run_at`, reload staleness.
  - `tests/test_automation_state_refresh.py` (6): all five `_dispatch_scheduled_job` exit branches assert `next_run_at` refresh; refresh helper's own-exception path verified silent.
  - `tests/test_runtime_worktree.py` (14): `WorktreeManager` against a real git repo fixture — ensure/changed_files/run_shell timeout+heartbeat/create_patch/apply_check/list_changes/remove/prune.
  - `tests/test_runtime_notifications.py` (9): `NotificationManager.emit` + `resolve` with real SQLite + fake channel — no-owners / success / dedup / missing session / both-fail / no-DM-support / resolve / body content / reason_label.
  - `tests/test_auth_providers_bilibili.py` (20): mocked OAuth state machine for all QR codes (86101 / 86090 / 86038), header-based cookie extraction, cross-domain URL fallback, persist / validate / invalidate.
- **Governance files**:
  - `SECURITY.md` — vulnerability disclosure (tcoherence@gmail.com, 72h ack, 7d substantive follow-up), scope table, supported-versions table, credential handling note, safe-harbor clause.
  - `CONTRIBUTING.md` — dev setup, test invocation, PR conventions, "discuss first" list for schema/adapter/state-machine changes.
  - `docs/EN/README.md` — English mirror of `docs/CN/README.md` as the EN docs index.
  - `docs/EN/release-process.md` + `docs/CN/release-process.md` — release playbook (pre-release checklist, version bump / tag / GitHub-release steps, hotfix flow).

### Changed

- `memory/store.py`: added `logger.warning` / `logger.exception` on two silent except sites (`get_schema_version` swallow, `claim_pending_runtime_task` rollback wrapper). Legacy-compat JSON-parse fallbacks in other sites intentionally left silent to avoid log noise.
- `docs/{EN,CN}/architecture.md`: removed stale "automation runtime state is not yet persisted" line (persisted since v0.7.3); updated missed-job wording from "not yet finalized" to "policy is fixed to `skip`; manual catch-up via `/automation_run`".
- `docs/{EN,CN}/todo.md`: flipped v0.9 RC / Contract Freeze items to `[x] shipped in v0.9.1`; flipped post-1.0 scheduler-watchdog backlog item to `[x]`.
- `docs/CN/README.md`: removed Slack-stub references (stub was removed in v0.9.1); added missing doc-index entries (upgrade-guide, monitoring, troubleshooting, config-reference, release-process) + SECURITY/CONTRIBUTING pointers.
- `AGENT.md` (`CLAUDE.md`): updated test-count reference from "504 tests" to "full test suite".

### Deferred to post-1.0

- P1.4 "CLI auth silent-401 safety net" — not reproducible locally; remains in [docs/EN/todo.md:244](docs/EN/todo.md) backlog. Main path (`OMA_CONTROL auth_required` control frame) is well-covered.

## v0.9.1 - 2026-04-18

### Added

- **v0.9 Contract-Freeze Phase A — restart/recovery + upgrade path tests** (toward v1.0 acceptance criteria #3 / #7 / #9 / #10)
  - `tests/test_restart_recovery.py` (11 tests): in-process close/reopen round-trips for chat thread history, CLI session resume bridge, runtime tasks (DRAFT / RUNNING / WAITING_USER_INPUT) with `event_log`, HITL prompts (DB + Discord `_rehydrate_hitl_prompt_views` view rehydration), auth-wait (runtime task path + direct-chat `suspended_agent_run` path), automation `last_run_at` / `next_run_at`, and `schema_version` idempotence.
  - `tests/test_upgrade_paths.py` (12 tests): end-to-end `scripts/migrate_memory_to_judge.py` exercise (dry-run + real run + backup dir assertions); `memory.adaptive` → `memory.judge` alias lock-in (compat fallback preserved, not a regression); deprecation warnings for `memory.adaptive` config and for legacy `curated.yaml` / `daily/` layouts; builder-mode runtime.db schema-migration test (no binary fixture — `aiosqlite` constructs a v0.7-era `runtime_tasks` missing the modern columns + `task_type='code'` enum, then asserts `_ensure_column` backfill + enum normalisation to `'repo_change'`); `schema_version` lands at `CURRENT_SCHEMA_VERSION` after `init()` even on legacy DBs lacking the `schema_version` table.
  - `_warn_if_legacy_memory_config()` and `_warn_if_legacy_memory_layout()` helpers in `main.py` emit deprecation warnings at startup (visible in `service.log`) so `/doctor` and operators notice stale v0.8 state without breaking the boot.
  - Total tests: 504 → 527.
- **v0.9 Contract-Freeze Phase B — service-layer extraction: `MemoryService` + `SkillEvalService`** (toward v1.0 acceptance criterion #11 — adapters do not own business logic).
  - New `MemoryService` (`gateway/services/memory_service.py`) owns `/memories` listing, `/forget` supersede, `/memorize` judge invocation, and the best-effort `MEMORY.md` resynthesis trigger that previously lived inline in `discord.py`.
  - New `SkillEvalService` (`gateway/services/skill_eval_service.py`) owns `/skill_stats` aggregation, single-skill detail with attached evaluations, `/skill_enable` toggle, and the thumbs-up/down reaction → `upsert_skill_feedback` / `delete_skill_feedback` plumbing.
  - New result dataclasses in `gateway/services/types.py`: `MemoryListResult`, `MemoryActionResult`, `MemoryEntrySummary`, `SkillStatsResult`, `SkillStatRow`, `SkillToggleResult`. Discord adapter now consumes these via two new render helpers (`_render_memory_list_result`, `_render_skill_stats_result`).
  - `discord.py` slash handlers slimmed (1992 → 1887 lines, -105). Each remaining handler is owner-check + service call + render. No business logic touches `JudgeStore` or `MemoryStore.{upsert,delete,get}_skill_*` directly anymore — the only remaining `_judge_store` / `_memory_store` references are the setters in `__init__` and the constructor arguments passed into the services in `_refresh_services()`.
  - `tests/test_memory_service.py` (15 tests) and `tests/test_skill_eval_service.py` (18 tests) cover success / store-missing / unknown-id / synth-failure-swallowed / score-validation paths.
  - Total tests: 527 → 560.
- **v0.9 Contract-Freeze Phase C — operator documentation + experimental-surface trim** (toward v1.0 acceptance criterion #5 — `/doctor` + docs sufficient to localise faults; and the explicit-rejection half of criterion #11 — no surfaces that "validate but do nothing").
  - **4 new operator-grade docs** (bilingual, EN + CN):
    - `docs/{EN,CN}/troubleshooting.md` — 15 fault patterns (bot silent, task stuck in DRAFT, RUNNING hang, agent fallback loop, automation never fires, memory not injected, HITL buttons dead, merge gate blocked, skill auto-disabled, rate-limiter saturation, config validation failure, CLI session no-resume, image attachment ignored, skill changes invisible, `/doctor` red status), each with **Symptom → Diagnose → Resolve** + escalation guidance.
    - `docs/{EN,CN}/monitoring.md` — service-log location/format, P0 alert table (5 patterns), P1 threshold table (8 patterns), `/doctor` section-by-section glossary, disk usage paths, cost signals.
    - `docs/{EN,CN}/config-reference.md` — every `config.yaml.example` field documented, plus a cross-field cheatsheet for the three timeouts (`agents.<name>.timeout` vs `runtime.default_max_minutes` vs `skills.evaluation.<skill>.timeout`).
    - `docs/{EN,CN}/upgrade-guide.md` — general SOP + per-version sections (v0.7.x→v0.8.0 through v0.9.x→v1.0), with the **v0.8.2→v0.9.0 memory rewrite** and **v0.9.x→v1.0 Slack removal** marked as breaking.
  - **Stale-reference cleanup**: `daily/curated`, `MemoryExtractor`, `AdaptiveMemoryStore`, `DateBasedMemoryStore`, `/promote` removed from `README.md`, `AGENT.md`, `docs/{EN,CN}/{architecture,development,todo,README,v1.0-plan}.md` (the upgrade-guide retains the literal terms in their proper "this is what was removed" context). Verified by `grep -rn` against the same target set.
  - **Slack stub removed (BREAKING)** — explicit `1.0` contract: single supported platform.
    - `src/oh_my_agent/gateway/platforms/slack.py` deleted.
    - `_build_channel()` in `main.py` no longer accepts `platform: slack`.
    - `config_validator` now emits a specific error pointing to the upgrade guide when `platform: slack` is configured (no more silent skip at startup, no more generic "expected one of" message). New `UNSUPPORTED_PLATFORMS` map carries per-platform rejection text.
    - `config.yaml.example` slack stub block removed.
    - New `tests/test_v08_session1.py::TestConfigValidator::test_slack_rejected_with_specific_message` locks the rejection contract.
  - Total tests: 560 → 561.

## v0.9.0 - 2026-04-17

### Changed (BREAKING)

- **Memory subsystem rewritten as a Judge model**: the legacy daily/curated tier system, post-turn `MemoryExtractor`, and explicit `/promote` workflow are removed and replaced by a single-tier `JudgeStore` plus an event-driven `Judge` agent.
  - **New on-disk schema**: `~/.oh-my-agent/memory/memories.yaml` (flat list with `id`, `summary`, `category`, `scope`, `confidence`, `observation_count`, `evidence_log[]`, `status`/`superseded_by`); `MEMORY.md` is synthesized from active entries on the same path.
  - **Triggers**: thread idle (default 15 min), explicit `/memorize` slash command (optionally with literal `summary` + `scope` to short-circuit the LLM), and natural-language keywords (`记一下` / `remember this` / etc.) — extraction no longer runs on every assistant turn.
  - **Action-based judgment**: the Judge emits `add` / `strengthen` / `supersede` / `no_op` actions against the existing memory list (passed in as context), eliminating the paraphrase-driven duplication that left the old store stuck at `obs=1` for every entry.
  - **Removed modules**: `oh_my_agent.memory.adaptive`, `oh_my_agent.memory.date_based`, `oh_my_agent.memory.extractor` and their tests are deleted.
  - **Removed Discord command**: `/promote` (no two-tier system to promote between); `/memories` and `/forget` continue to work against the new store, and `/memorize` is added.
  - **Config**: `memory.adaptive` is replaced by `memory.judge` (`enabled`, `memory_dir`, `inject_limit`, `idle_seconds`, `idle_poll_seconds`, `synthesize_after_seconds`, `max_evidence_per_entry`, `keyword_patterns`).
  - **Migration**: run `python scripts/migrate_memory_to_judge.py <memory_dir>` to back up the old layout and write a fresh `memories.yaml` from `curated.yaml` (pass `--include-daily` to also import daily entries). Subsequent startup will rebuild `MEMORY.md` automatically.

## v0.8.2 - 2026-04-17

### Added

- **`paper-digest` skill**: daily research-paper radar covering arXiv, HuggingFace Daily, and Semantic Scholar; workflow slimmed to keep within agent `max_turns` budget on large days
- **`youtube-podcast-digest` skill**: weekly digest of YouTube podcast subscriptions; filters out Shorts and supports configurable catch-up windows so missed runs are picked up on the next fire
- **Claude agent `--output-format stream-json --verbose`**: per-turn output visibility during long Claude CLI runs — intermediate reasoning and tool calls surface in real time rather than only at completion
- **Scheduler DRAFT-skip reminder + `/task_replace`**: when a scheduler-fired task is still in DRAFT at the next cron tick, the system reminds the operator; `/task_replace` discards a DRAFT and immediately refires its automation cron
- **`market-briefing` podcast — finance daily coverage**: `🎙️ 播客动态` section extended from AI-only to both AI and finance daily reports; finance group seeded with 6 channels (锦供参考, 起朱楼宴宾客, 小Lin说, 商业就是这样, 知行小酒馆, 面基); AI group expanded to 8 channels (+42章经, AI局内人, 科技早知道); `general` feed group removed — shared channels merged into their primary domain group
- **AI people-pool discovery rules**: SKILL.md now specifies concrete discovery sources (X/Twitter, GitHub trending, podcast guests, paper authors), candidate qualification criteria, minimum JSON fields, and a target range of 1–3 candidates per AI daily report; `report_store.py persist` auto-calls `ai_people_pool.py record` for AI daily reports so the state file stays in sync without relying on the agent to remember a manual step
- **Per-automation `auto_approve` flag**: automation YAML files now accept `auto_approve: true` to skip the runtime risk-evaluation gate (DRAFT → PENDING without manual approval); default is `false` (safe); all existing automations updated to `auto_approve: true`
- **`/automation_run` slash command**: manually fire any enabled automation job on demand — useful for retrying failed cron jobs without waiting for the next schedule
- **Human-readable risk reasons**: DRAFT task cards and DM notifications now show descriptive reason text (e.g. "estimated runtime 26 min exceeds 20 min threshold") instead of raw labels like `minutes_over_20` or `draft`
- **`automation.yaml.example`**: annotated reference template for all automation YAML fields including the new `auto_approve` flag
- **README progressive-disclosure redesign**: top-level README rewritten to lead with a 60-second quickstart and progressively reveal advanced configuration

### Fixed

- **Automation YAML `skill_name` fix**: all 7 automation YAML files now carry explicit `skill_name`, enabling correct `timeout_seconds` inheritance from skill metadata (e.g. deals-scanner 900s, market-briefing 1500s instead of falling back to agent default 300s); automation prompts rewritten to reference SKILL.md workflows and helper scripts instead of hardcoding output paths
- **Scheduler tasks stuck in DRAFT**: scheduler-fired tasks with `timeout_seconds > 1200` (market-briefing, deals-scanner) were blocked by `evaluate_strict_risk()` producing `minutes_over_20`; scheduler tasks now respect per-automation `auto_approve` flag to bypass the risk gate

### Removed

- **`agents/api/` deleted**: deprecated since v0.4.0 and no longer used in any shipping config — `AnthropicAPIAgent`, `OpenAIAPIAgent`, `BaseAPIAgent`, and the `type: api` branch in `main._build_agent()` are removed; CLI agents (Claude / Gemini / Codex) are the only supported path. `skills/market-intel-report/` (consolidated into `market-briefing` earlier) also removed.

## v0.8.1 - 2026-04-15

### Added

- **Memory extraction hygiene** (Slice A): extraction window rewritten to use the most recent 6 turns (≤800 chars per assistant turn) instead of silently truncating from the front of the full history; ensures recent user evidence always reaches the extractor regardless of thread length
- **Extraction trigger optimization**: per-thread `last_extracted_user_turn_count` + `last_extraction_empty` in-memory state; extraction is skipped when no new user turns have occurred since the last pass *and* that pass returned empty — eliminates redundant agent calls on idle threads
- **Extraction prompt hardening**: explicit negative rules block one-off task details, temporary plans, slash command habits, file paths, implementation steps, and future speculation (`"the user may…"`); `[user]` / `[assistant]` role tags guide the model to treat assistant content as context only
- **Parse-failure retry**: on JSON parse failure the extractor retries once with a simplified schema; on second failure it returns empty and logs `parse_failure=true` with the skip reason — no raw LLM output leaks to the user
- **`MemoryEntry` schema expansion — Batch 1**: added `explicitness` (`explicit`/`inferred`), `status` (`active`/`superseded`), `evidence` (≤140 char user-side snippet), and `last_observed_at`; old YAML files migrate lazily on load with safe defaults
- **`MemoryEntry` schema expansion — Batch 2**: added `scope` (`global_user`/`workspace`/`skill`/`thread`), `durability` (`ephemeral`/`medium`/`long`), `source_skills`, and `source_workspace`; `scope_matches()`, `scope_score_multiplier()`, `broadened_scope()`, and `stronger_durability()` helpers added to `adaptive.py`
- **Two-stage deduplication**: Stage 1 normalises (lowercase, strip punctuation, stopword filter) and classifies pairs as `same_memory` (normalised equality or Jaccard ≥ 0.75), `candidate` (Jaccard 0.35–0.75), or `distinct` (< 0.35); Stage 2 batches all candidates in a single agent call that returns `same_memory` / `related_but_distinct` / `contradictory`; contradictory hits mark the old entry `status=superseded` with a timestamped `last_observed_at`
- **Fast-path / slow-path promotion**: explicit, high-confidence memories (`explicitness=explicit`, `confidence ≥ 0.85`, `observation_count ≥ 2`, category in preference/workflow/project_knowledge) fast-promote to curated in 1–2 observations; inferred memories slow-promote only after `confidence ≥ 0.80` plus either `observation_count ≥ 3` or ≥ 2 distinct source threads; `fact` category never takes the fast path; `superseded` entries are ineligible for promotion
- **Scope-aware bucketed retrieval**: `get_relevant()` now accepts `skill_name`, `thread_id`, and `workspace` context and routes memories into four buckets (`skill_scoped`, `workspace_project`, `global_preference`, `recent_daily`) each with a configurable per-bucket limit; scope-match multipliers boost contextually relevant memories without hard-excluding unmatched global preferences
- **Injection filter**: `superseded` entries are permanently excluded from prompt injection and `MEMORY.md` synthesis; `thread`-scoped and `ephemeral` entries respect their scope boundaries
- **Structured memory trace logs**: four new log event types — `memory_extract` (thread_id, turn_count, extracted/rejected counts, retry_used, skip_reason, parse_failure), `memory_merge` (candidate/same/distinct/contradictory counts), `memory_promote` (fast/slow path counts, skipped_reason), `memory_inject` (selected/filtered-superseded counts, per-bucket breakdown)
- **`/memories` view enhanced**: Discord display now shows `explicitness`, `status`, `observation_count`, and `last_observed_at` alongside existing tier and confidence bar
- **`seattle-metro-housing-watch` skill — coverage and contract update**: Bothell and Lynnwood promoted from optional expansion to default 7-area contract; Zillow added as formal area-trend second source alongside Redfin (no longer listing-only fallback); rate section now explicitly compares 30Y and 15Y fixed (MORTGAGE30US + MORTGAGE15US with direction and relative meaning); listing contract: single-family/townhouse only, per-area baseline 2 + priority-allocated 4 surplus slots (by high-quality sample availability → inventory activity → core-area preference), hard cap 18, price filter vs area-own median; `sample_listings[]` extended with `source_site`, `property_type`, `listed_at`, `original_list_price`, `price_history_summary`; `market_snapshot` stays lightweight (1/area, max 7); `area_deep_dive` gets 4–6 samples
- **`market-briefing` skill — coverage and structure update**: finance daily expanded to 8 fixed sections (adds 中国/香港市场脉搏, 美国市场波动与风险偏好, 中国房地产政策与融资信号); AI daily expanded to 9 sections with a new Frontier Labs / Frontier Model Radar section before the five-layer stack; frontier watchlist fixed to 8 labs (OpenAI, Anthropic, Google DeepMind, Meta, xAI, Mistral, Qwen, DeepSeek); rumor discipline codified (official source > quality media > social/leak only in `unverified_frontier_signals`); finance/politics boundary rule written into reference docs; `timeout_seconds` raised to 1500; weekly synthesis absorbs new finance and AI sections structurally; new `references/finance_watchlist.md` and `references/ai_frontier_watchlist.md` reference files
- **Usage audit attribution unified**: direct chat / explicit skill replies, runtime thread-agent replies, and automation terminal messages now share the same usage audit suffix contract (`in/out`, cache read/write, cost) while preserving each path's own prefix metadata such as automation name, run ID, and agent attribution
- **`market-briefing` podcast integration**: AI daily reports now include a `🎙️ 播客动态` section sourced from xiaoyuzhoufm.com subscriptions; new `scripts/podcast_fetch.py` prefetches latest episodes (48h freshness window, parallel fetch); subscription list externalized to `references/podcast_feeds.yaml` grouped by domain — editable without code changes; `timeout_seconds` raised from 1200 to 1500 to accommodate the prefetch step

### Fixed

- `DateBasedMemoryStore.max_memories` now applies correctly across both daily and curated tiers, respecting status-aware eviction order
- Merge hits on non-today daily files now write the updated entry back to the originating daily file rather than silently dropping the update
- `promote_memory()` now deduplicates against the curated tier before writing, preventing identical entries accumulating in `curated.yaml`
- `last_observed_at` is kept consistent across merge, promotion, and contradiction-supersede paths

## v0.8.0 - 2026-04-12

### Added

- Service-layer package (`gateway/services/`) with shared result types (`ServiceResult`, `TaskActionResult`, `MemoryResult`, etc.) and a `BaseService` ABC that wires logger, config, channel, and optional registry
- `TaskService`: extracted all task-control business logic (start, stop, pause, resume, approve, reject, suggest, discard, merge, changes, logs, cleanup, list, status) from `discord.py` into a platform-agnostic service; Discord slash commands are now thin call-through wrappers
- `AskService`: extracted ask / agent-select / thread-reset / history / search logic from `discord.py`; session state managed cleanly in one place
- `DoctorService` and `AutomationService`: doctor diagnostics and automation operator commands (status, reload, enable, disable) separated from the Discord adapter
- `HITLService`: interactive HITL surface (button callbacks, approval/rejection/suggestion/request-changes flows) extracted from the Discord `View` lifecycle into a testable, platform-agnostic layer
- `BaseChannel` extensions: `edit_message()`, `upload_file()`, and `interactive_prompt()` added to the contract with Discord implementations
- `--validate-config` CLI flag: startup config schema validation with fail-fast reporting of missing fields and invalid values
- Schema version tracking in `SQLiteScopedStore`: `schema_versions` table stores per-scope schema version; `migrate_runtime_schema()` applies forward-only migrations without data loss
- Markdown-aware message chunker (`utils/chunker.py`): fenced code blocks (`` ``` `` / `~~~`) are never split mid-block; oversized blocks split by line with fence close/re-open; plain text chunks respect word boundaries
- Structured logging (`logging_setup.py`): `KeyValueFormatter` emits `key=value` pairs for machine-parseable log lines; `setup_logging()` wires `TimedRotatingFileHandler` (daily rotation, 7-day retention) alongside console output; startup cleanup removes stale rotated `service.log.YYYY-MM-DD` files older than the retention window
- Graceful shutdown contract: `GatewayManager.stop()` signals the Discord gateway, drains in-flight runtime workers, cancels agent subprocesses, and flushes SQLite WAL before exit; `main.py` hooks into `SIGINT`/`SIGTERM`
- User-visible error contract: unhandled exceptions in command handlers and skill dispatch now surface a short, readable message to the user instead of a raw traceback; full traceback is preserved in the log
- Rate-limit / request queue: per-channel async semaphore limits concurrent agent calls; overflow messages receive a "busy" reply instead of silently queuing indefinitely
- Concurrent thread/task isolation tests: `tests/test_concurrent_isolation.py` covers simultaneous task creation, per-channel semaphore enforcement, and cross-thread memory isolation
- `compose.yaml`: first-class Docker Compose config with named `oma-runtime` volume, environment forwarding for API keys and agent overrides, health-check, and restart policy
- Operator guide (`docs/EN/operator-guide.md` and `docs/CN/operator-guide.md`): covers local install, Docker/Compose install, restart, diagnostics, automation, backup, and upgrade/migration SOPs
- `seattle-metro-housing-watch` skill: tri-weekly housing market analysis for the Greater Seattle area across five domains (affordability/inventory, luxury/condo, rental, interest-rate/macro, regional/suburb); supports `bootstrap_backfill` and `weekly_digest` modes with persisted report storage

### Fixed

- Existing `runtime.db` databases missing newly added tables (e.g., `automation_runtime_state`) on startup: `SQLiteScopedStore.init()` now re-runs the full `SCHEMA_SQL` DDL (`CREATE TABLE IF NOT EXISTS`) on both new and existing databases before committing
- Stale rotated log files not cleaned up when the process never ran through midnight: `_cleanup_old_logs()` now runs at startup and removes `service.log.YYYY-MM-DD` files beyond the 7-day retention window

### Changed

- Discord slash command handlers in `discord.py` are now thin wrappers that delegate entirely to the corresponding service; no business logic remains in the adapter
- `_setup_logging()` in `main.py` delegates to `logging_setup.setup_logging()` and passes runtime config (log level, retention days)
- `v0.8` items in `docs/EN/todo.md` and `docs/CN/todo.md` marked complete; snapshot date updated to 2026-04-12

## v0.7.3 - 2026-04-10

### Added

- Discord-first owner notifications for `auth_required`, `ask_user`, `DRAFT`, and `WAITING_MERGE`, with a separate ping message in the same thread plus best-effort owner DMs
- Persistent `notification_events` rows in SQLite for notification dedupe and future escalation/reminder support
- Split SQLite runtime layout with dedicated conversation, runtime-state, and skills-telemetry databases
- Automatic startup migration from legacy monolithic `memory.db` into `memory.db`, `runtime.db`, and `skills.db`, with preserved `.monolith.bak` backup bundles
- `market-briefing` AI people-pool helper, curated seed file, runtime candidate queue, and X.com/community signal workflow
- Attachment-first artifact delivery with local absolute-path fallback and delivery-aware completion summaries
- Thread-scoped unified logs under `~/.oh-my-agent/runtime/logs/threads/` across direct chat, explicit skill invocation, runtime tasks, and HITL resume flows
- Structured HITL answer payloads carried through task/thread resume context alongside backward-compatible `[HITL Answer]` prompt blocks
- Discord `/doctor` operator diagnostics for gateway/runtime/HITL/auth/log health snapshots
- Persisted automation runtime state (`last_run_at`, `last_success_at`, `last_error`, `last_task_id`, `next_run_at`) surfaced through `/automation_status` and `/doctor`

### Changed

- Human-input states now fan out through an internal notification layer instead of each flow hand-rolling Discord reminders
- `auth_required`, `ask_user`, `DRAFT`, and `WAITING_MERGE` notifications now resolve explicitly when the underlying waiting state is cleared, while routine runtime progress still stays notification-free
- `market-intel-report` has been renamed to `market-briefing`, and persisted report storage now lives under `~/.oh-my-agent/reports/market-briefing/`
- `market-briefing` finance daily now defaults to China macro/policy, US macro/policy, tracked holdings over the last 7 days, and a market/index-fund lens, and all daily domains now carry stricter no-signal / low-confidence guidance in schema and prompts
- `market-briefing` AI daily now adds tracked people/community signals, a bounded X.com discovery sweep, candidate/promotion state under `~/.oh-my-agent/reports/market-briefing/state/`, and explicit no-signal fallbacks for thin layers
- Report-store helpers for `market-briefing` and `deals-scanner` now derive their default local report date from `OMA_REPORT_TIMEZONE` / `TZ` instead of implicitly inheriting UTC-like container defaults, and Docker helper scripts now pass an explicit report timezone into the container
- `deals-scanner` daily scans now use source-specific default lookback windows (`3` days for credit-cards/uscardforum/rakuten, `7` days for slickdeals/dealmoon/summary), expose `lookback_window_days` in daily JSON, and treat older carryover items as `Watchlist`-only instead of mixing them into the main summary buckets
- `memory.path` now refers to the conversation store only; runtime task/auth/HITL/notification/session state moves to `runtime.state_path`, and skill provenance/telemetry moves to `skills.telemetry_path`
- Runtime task claiming no longer opens a nested `BEGIN IMMEDIATE` transaction on a shared SQLite connection
- `artifact`, `repo_change`, and `skill_change` runtime flows are now fully closed out for v0.7.3, with delivery/logging/HITL behavior aligned to task type instead of merge-only assumptions
- `/automation_status` and `/doctor` now read persisted automation runtime state instead of relying on in-memory scheduler snapshots alone
- Automation-backed executions now honor skill `metadata.timeout_seconds` the same way direct skill invocations do
- Live task status, merge/discard/request changes, and answered/cancelled HITL prompts now settle into stable final Discord views instead of lingering in a loading state

## v0.7.2 - 2026-03-16

### Added

- File-driven automation scheduling under `~/.oh-my-agent/automations/*.yaml`
- Polling-based automation hot reload for file add/update/delete and per-file `enabled` toggles
- Cron-based automation schedules with `interval_seconds` retained for high-frequency local testing
- Discord operator automation commands: `/automation_status`, `/automation_reload`, `/automation_enable`, `/automation_disable`
- Temporary `docs/archive/next_up.md` note for near-term execution focus
- Long-running Docker helper scripts: `docker-start.sh`, `docker-logs.sh`, `docker-stop.sh`, `docker-status.sh`
- `market-briefing` skill for persisted politics / finance / AI bootstrap, daily, and weekly reports
- Report-store helper for canonical Markdown + JSON outputs under `~/.oh-my-agent/reports/market-briefing/`
- Generic Discord-first HITL `ask_user` control path for owner-only single-choice questions across direct chat and runtime tasks
- Skill-specific `metadata.timeout_seconds` frontmatter override for slow direct-chat skill invocations

### Changed

- `config.yaml` no longer embeds `automations.jobs`; automation config now only carries global scheduler settings
- Scheduler startup now watches the automation storage directory even when it initially contains 0 jobs
- Scheduler now keeps a visible in-memory snapshot of valid active + disabled automations for operator commands, while invalid/conflicting files remain log-only
- Scheduler-triggered automations now use reply/artifact runtime tasks with a single-step `true` validation path instead of repo-change loops
- Duplicate fires of the same automation name are now skipped while an earlier run is still in flight
- Automation runs now post the final artifact/result directly in Discord instead of runtime task status/update spam
- Requeued in-flight runtime tasks now roll back one step before retry, avoiding immediate `TIMEOUT max_steps=1` failures after restart for single-step automation runs
- Runtime cleanup now uses a 7-day default retention window and prunes stale agent logs along with old task workspaces
- README and Chinese README now document the external automation directory, hot-reload semantics, and current in-memory-only runtime state behavior
- Docker docs now distinguish attached development runs from detached long-running service runs, including postmortem debugging expectations around `docker logs` and persistent application log files
- Scheduler skill and validator now target file-driven automation YAML under `~/.oh-my-agent/automations/` instead of the old `config.yaml` job model
- README and Chinese README now document the market-briefing skill, report storage layout, and bounded bootstrap workflow
- `OMA_CONTROL` now supports generic `ask_user` challenges alongside `auth_required`, with persisted `hitl_prompts`, visible Discord choice prompts, auto-resume behavior, and persistent-view recovery after restart
- `WAITING_USER_INPUT` now covers both QR auth pauses and generic owner-choice pauses for runtime tasks and automations
- Direct-chat skill invocations can now temporarily override the normal per-agent CLI timeout from `SKILL.md` frontmatter `metadata.timeout_seconds` without changing default chat timeouts for the rest of the system

## v0.7.1 - 2026-03-08

### Added

- Auth-first QR login and structured agent control-flow integration:
  - owner-only Discord auth commands: `/auth_login`, `/auth_status`, `/auth_clear`
  - Bilibili QR login provider with local credential persistence
  - runtime `WAITING_USER_INPUT` state for auth-blocked task execution
  - `OMA_CONTROL` challenge envelope parsing for direct chat and runtime task paths
  - suspended direct-chat agent runs that can resume after auth succeeds
  - resumable auth-triggered agent runs with session-first resume and fresh-run fallback
- Outbound Discord attachment support for auth QR delivery
- Docker runtime workflow:
  - image build/run helper scripts
  - repo-mounted `/repo` source of truth for config and source code
  - host-mounted `/home` runtime state root
  - startup-time editable install from `/repo` so normal source edits only require restart
  - image now carries runtime dependencies only instead of relying on a separate in-image source snapshot for execution
  - preinstalled `claude`, `gemini`, and `codex` CLIs in the Docker image
  - fail-fast startup validation for configured CLI binaries
- Transcript-first video skills:
  - `youtube-video-summary` using `yt-dlp` for subtitle and metadata extraction
  - `bilibili-video-summary` using `yt-dlp`, explicit `auth_required` signaling, and transcript-backed article-style summaries

### Changed

- Docker startup now reads config from `OMA_CONFIG_PATH` (default `/repo/config.yaml`) and loads `.env` from the config directory rather than relying on process `cwd`
- Skill sync now anchors repo-native `.claude/`, `.gemini/`, and `.agents/skills/` paths to the resolved project root instead of the current working directory
- Docker entrypoint was simplified to require prepared repo config instead of seeding runtime copies of `config.yaml`, `.env`, `skills/`, or root `AGENTS.md`
- Docker docs now describe the mounted-repo workflow, restart-vs-rebuild expectations, and CLI/login requirements
- Date-based memory now keeps promoting eligible daily observations into `curated.yaml` during normal runtime operation instead of only on startup
- `MEMORY.md` synthesis is now wired into startup, memory extraction, and manual `/promote` flows when curated memory changes
- Auth challenge UX now surfaces the agent's progress/update message before sending the QR prompt, so resume no longer appears to jump in abruptly

### Fixed

- Runtime shutdown order now stops runtime workers/janitor before closing SQLite, preventing `no active connection` janitor crashes during process teardown
- Runtime janitor now backs off after exceptions instead of spinning in a tight error loop
- Auth QR PNG files are now cleaned up when flows reach terminal states (`approved`, `expired`, `failed`, `cancelled`)
- Resumed CLI sessions are now documented as potentially stale with respect to newly added skills, matching actual router-vs-session behavior

## v0.7.0 - 2026-03-01

### Added

- Skill evaluation end-to-end loop:
  - chat-path skill invocation telemetry stored in SQLite
  - per-invocation Discord reaction feedback (`👍` / `👎`)
  - `/skill_stats [skill]` operator surface
  - `/skill_enable <skill>` manual recovery for auto-disabled skills
  - auto-disable guard that removes unhealthy skills from automatic routing while preserving explicit `/skill-name`
  - duplicate-skill overlap review before skill auto-merge
  - source-grounded review for external repo/tool/reference skill adaptations
- Image attachment support for Discord messages:
  - `Attachment` dataclass with `is_image` property in gateway base
  - Discord `on_message` downloads `image/*` attachments (≤10 MB) to temp dir
  - `IncomingMessage.attachments` field carries attachments through the pipeline
  - Image-only messages (no text) get a default analysis prompt
  - Claude/Gemini: copy images to `workspace/_attachments/` and augment prompt with file-reference instructions
  - Codex: native `--image` flag support
  - Session history stores attachment metadata (filename, content_type)
  - Temp files cleaned up after agent response
- Date-based two-tier memory system (`DateBasedMemoryStore`):
  - Daily logs (`memory/daily/YYYY-MM-DD.yaml`) with exponential time decay
  - Curated long-term memories (`memory/curated.yaml`) — no decay
  - Auto-promotion: daily entries meeting observation count + confidence + age thresholds are promoted to curated on load
  - `MEMORY.md` synthesis: agent-generated natural-language view of curated memories
  - Pre-compaction flush: memory extraction now runs before history compression
- Discord `/promote` slash command for manual daily-to-curated promotion
- Discord `/memories` now shows tier tags (`[C]` curated / `[D]` daily)
- Module-level utility functions: `word_set`, `jaccard_similarity`, `eviction_score`, `find_duplicate`
- `MemoryEntry.tier` field (`"daily"` | `"curated"`)
- `oh-my-agent --version`
- Single-source package versioning via `src/oh_my_agent/_version.py`
- Generated workspace `AGENTS.md` metadata with source path, source hash, and generation timestamp
- Codex repo/workspace skill delivery via official `.agents/skills/`
- Request-scoped observability labels for gateway agent runs and background memory/compression follow-up work

### Fixed

- Runtime skill mutation flow no longer auto-merges likely duplicate skills or weak external-source adaptations without review
- Router-visible skill list now excludes auto-disabled skills, while explicit skill invocation remains available
- Strict risk detection no longer misclassifies `adapt ...` requests as `apt ...` package-management operations
- Hardened persisted CLI session resume for Claude, Codex, and Gemini:
  - invalid/stale resumed sessions are now cleared more selectively
  - stale persisted sessions are deleted even when fallback succeeds through another agent
- Added tests to keep package version and changelog state aligned
- Base workspace now refreshes synced skills and generated `AGENTS.md` together when repo `AGENTS.md` or canonical `skills/` change
- Legacy workspace `.codex/` compatibility directories are removed during workspace refresh and session sync
- Generated workspace `AGENTS.md` no longer enumerates workspace skill extensions now that Codex uses official `.agents/skills/`
- Background memory extraction and history compression logs now carry the originating request ID, and gateway reply logs now expose `purpose=...` across start/success/error lines

## v0.6.1 - 2026-03-01

### Added

- Codex CLI session resume via `codex exec resume`
- Gemini CLI session resume via `gemini --resume`
- Session persistence and restore for Codex/Gemini through the existing gateway session store

### Fixed

- Resume state handling now survives normal non-session CLI failures without dropping valid sessions
- Gateway session sync now clears stale persisted session IDs after fallback

## v0.6.0 - 2026-02-28

### Added

- Adaptive memory with YAML-backed storage
- Memory extraction from conversation history
- Memory injection into agent prompts
- Discord `/memories` and `/forget` commands
- Skill auto-approve and auto-merge for skill tasks

## v0.5.3 - 2026-02-27

### Added

- True subprocess interruption for running runtime agent/test subprocesses
- Message-driven runtime control for `stop`, `pause`, and `resume`
- PAUSED state with workspace preservation and resumable instructions
- Structured task completion summaries and runtime timing metrics

### Improved

- Suggestion UX for blocked tasks and approval flows

## v0.5.2 - 2026-02-26

### Added

- Durable runtime state machine
- Merge gate for repository and skill changes
- External runtime workspace layout and migration
- Runtime janitor cleanup
- Discord task commands and approval flow

## v0.4.2 - 2026-02-26

### Added

- Owner-only access gate
- Scheduler MVP
- Delivery mode selection per scheduled job
- Scheduler skill

## v0.4.1 - 2026-02-25

### Added

- `@agent` targeting in thread messages
- `/ask` agent override
- Persisted CLI session IDs and resume-across-restart support
- CLI error observability improvements

## v0.4.0 - 2026-02-25

### Added

- CLI-first cleanup of the agent stack
- Codex CLI agent
- SkillSync reverse sync
- Discord slash commands
- Claude CLI session resume
- Memory export/import
