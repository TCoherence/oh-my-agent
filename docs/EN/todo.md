# Todo / Roadmap

## Snapshot (2026-02-27)

- `/search` is implemented.
- SkillSync reverse sync is implemented.
- CLI-first foundations are in place.
- v0.5 is runtime-first.
- Optional LLM router is implemented.
- Runtime observability baseline is implemented.

## v0.5 Runtime Hardening

- [x] Runtime task state machine
- [x] SQLite checkpoints/events/decisions
- [x] Crash recovery baseline
- [x] Per-task worktree isolation
- [x] Step loop execution
- [x] Budget guards
- [x] Allow-all-with-denylist path policy
- [x] Discord buttons + slash fallback
- [x] Merge gate
- [x] External runtime paths + migration
- [x] Janitor cleanup
- [x] Short conversation workspace TTL
- [x] Optional LLM router draft-confirm flow
- [x] `/task_logs` + sampled progress persistence + status message upsert

## Remaining v0.5 Work

- [ ] True runtime interruption for running agent/test subprocesses
- [ ] Message-driven runtime control (`stop`, `pause`, `resume` from normal thread messages)
- [ ] Resume UX refinement
- [ ] Suggestion UX refinement
- [ ] Structured task completion summary
- [ ] Runtime metrics and latency stats
- [ ] Clear paused/interrupted semantics in the state model

## v0.6 - Skill-First Autonomy

- [ ] Promote skill creation to a first-class runtime task type
- [ ] Add skill routing for “turn this workflow into a skill” requests
- [ ] Add skill validation loop before merge
- [ ] Add cross-agent skill abstraction
- [ ] Define Codex-native skill integration strategy
- [ ] Add skill memory / provenance metadata
- [ ] Keep `mission` model and operator surface as enabling architecture for skill autonomy
- [ ] Keep thread-native runtime control as supporting work, not the headline

## v0.7 - Ops-First and Hybrid Autonomy

- [ ] Scheduler-driven operational tasks
- [ ] Event-driven triggers beyond cron
- [ ] Repeated-pattern detection from history
- [ ] Skill recommendation / auto-draft from recurring workflows
- [ ] Hybrid missions combining skill creation and scheduled execution
- [ ] Unified operator surface for active ops and skill-growth workflows

## OpenClaw Gap Summary

Current positioning is still closer to a Discord-native coding runtime than to a general assistant control plane.

Main gaps relative to OpenClaw:

- The strongest gap is not coding runtime alone, but autonomy layering above it.
- The current system is runtime-first; the next needed layer is skill-first autonomy.
- Ops-first and hybrid autonomy should follow later, not compete with v0.6 priorities.
- Skills as platform capability: skill sync/tooling exists, but skill generation and reuse are not yet a first-class task type.
- Operator surface and artifact model still need to mature for sustained autonomous work.

Recommended next architecture step:

- Use runtime-first infrastructure as the base layer.
- Make skill-first autonomy the primary v0.6 product focus.
- Treat `mission`, operator surface, and thread-native control as enabling architecture rather than the headline.
- Defer ops-first and hybrid autonomy to the next phase once skill autonomy is stable.

## Backlog

- [ ] Feishu/Lark adapter
- [ ] Slack adapter
- [ ] File attachment pipeline
- [ ] Markdown-aware chunking
- [ ] Rate limiting / request queue
- [ ] Docker-based agent isolation
- [ ] Semantic memory retrieval
