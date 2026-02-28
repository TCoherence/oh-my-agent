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

- [x] True runtime interruption for running agent/test subprocesses
- [x] Message-driven runtime control (`stop`, `pause`, `resume` from normal thread messages)
- [x] Resume UX refinement
- [x] Suggestion UX refinement
- [x] Structured task completion summary
- [x] Runtime metrics and latency stats
- [x] Clear paused/interrupted semantics in the state model

## v0.6 Direction

- [ ] Introduce a first-class `mission` model above threads and runtime tasks
- [ ] Add thread-native runtime control (`pause`, `resume`, `stop`, `summarize`) from normal messages
- [ ] Support true interruption of running agent/test subprocesses and resumable checkpoints
- [ ] Promote skill creation to a first-class runtime task type
- [ ] Add skill routing for “turn this workflow into a skill” requests
- [ ] Add an operator surface for active/blocked missions, approvals, and artifacts
- [ ] Add first-class task artifacts (diff, test summary, generated files, commit, screenshots)
- [ ] Smart agent routing by task profile
- [ ] Multi-agent collaboration
- [ ] Intent-based agent selection
- [ ] Skill-oriented task type
- [ ] Skill routing for “turn this workflow into a skill” requests
- [ ] Skill validation loop before merge

## OpenClaw Gap Summary

Current positioning is still closer to a Discord-native coding runtime than to a general assistant control plane.

Main gaps relative to OpenClaw:

- Mission model: OpenClaw presents a stronger assistant/session/control-plane abstraction, while this repo still composes `thread + router + runtime task`.
- Runtime control: autonomous execution exists, but operator controls are still command-heavy and do not yet provide true interruption and natural recovery from normal thread messages.
- Skills as platform capability: skill sync/tooling exists, but skill generation and reuse are not yet a first-class task type.
- Operator surface: task logs and status messages exist, but there is no unified operator view for active missions, blocked reasons, pending approvals, and artifacts.
- Artifact model: merge review is still mostly text-summary driven; task outputs should become first-class artifacts.

Recommended next architecture step:

- Treat `mission` as the durable unit of work.
- Let threads be one interaction surface for a mission.
- Let runtime tasks become execution attempts under that mission.
- Make human control and skill creation part of the same mission lifecycle.

## Backlog

- [ ] Feishu/Lark adapter
- [ ] Slack adapter
- [ ] File attachment pipeline
- [ ] Markdown-aware chunking
- [ ] Rate limiting / request queue
- [ ] Docker-based agent isolation
- [ ] Semantic memory retrieval
- [ ] Cross-agent skill abstraction
- [ ] Codex-native skill integration strategy
