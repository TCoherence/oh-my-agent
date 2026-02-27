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

## v0.6 Direction

- [ ] Smart agent routing by task profile
- [ ] Multi-agent collaboration
- [ ] Intent-based agent selection
- [ ] Skill-oriented task type
- [ ] Skill routing for “turn this workflow into a skill” requests
- [ ] Skill validation loop before merge

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
