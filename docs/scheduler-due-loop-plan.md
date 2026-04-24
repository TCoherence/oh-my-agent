# Scheduler: replace per-job sleepers with a central due-loop scanner

## Context

`Scheduler` today creates one long-lived asyncio task per scheduled job. Each task does `await asyncio.sleep(delay)` to wait for its next fire. `asyncio.sleep` is monotonic-clock based; on Docker-on-Mac when the host laptop suspends, the Linux VM pauses, monotonic time freezes, and the in-flight sleep can't complete on schedule. After host wake, the wall clock has passed `next_fire_at` but the sleep still has time on its monotonic budget — the supervisor flags `missed_fire`, kills the task, recomputes for *tomorrow*. **The missed run is silently dropped.**

Today (2026-04-24) we lost 5 fires this way. The diagnostic case was `deals-scanner-daily-1000`: host already awake (Discord RESUMED at 16:22, janitor at 16:37) when the 17:00 fire was missed at 17:02 — proof the *old* in-flight sleep stays stuck even after wake.

The structural fix: stop using per-job long sleepers. Maintain `next_fire_at` per job in state, and let one global *due loop* tick on a bounded interval, dispatch jobs whose `next_fire_at <= now`, and start a short-lived fire coroutine each time. Wall-clock awareness becomes a property of the whole scheduler, not a helper bolted onto each runner. After host suspend, recovery is bounded by one scanner tick.

Same-job overlap is still forbidden (existing semantics). No backfill — exactly one fire per missed window, on the first scanner tick after `next_fire_at` (already current behavior since cron's "post-fire next" is computed from the fire's completion time, not from the missed wall-clock target).

## Approach

### 1. Replace per-job sleepers with a central due loop

**Removed:** `_run_cron_job` (lines 927-958), `_run_interval_job` (lines 898-925).

**Kept (with new role):** `_start_job` no longer creates an asyncio task. It only initializes `JobRuntimeState` with the initial `next_fire_at` and `phase="sleeping"`. Same first-fire computation as today:
- cron: `_next_cron_fire(spec, now)` via `compute_next_run_at`
- interval: `now + timedelta(seconds=job.initial_delay_seconds)` (or `now` if zero — fires on the very next tick)

**New:** `_due_loop(on_fire)` — single asyncio task, started in `Scheduler.run()` alongside `_reload_loop`. Uses an internal **wakeup event** so schedule changes don't have to wait out the bounded tick:

```python
_DEFAULT_DUE_LOOP_MAX_TICK_SECONDS = 30.0

# In __init__:
self._due_loop_wakeup = asyncio.Event()

async def _due_loop(self, on_fire):
    while True:
        try:
            now = self._now()
            due_names = self._collect_due_jobs(now)
            for name in due_names:
                self._dispatch_due_job(name, on_fire)
            self._touch_due_loop_progress()  # liveness signal for supervisor
            # IMPORTANT: clear() BEFORE computing sleep_for. If we cleared
            # after compute, a wakeup that arrives in the window between
            # compute and clear would be lost — leading to a missed
            # short-interval fire.
            self._due_loop_wakeup.clear()
            sleep_for = self._compute_due_loop_sleep(self._now())
            try:
                await asyncio.wait_for(
                    self._due_loop_wakeup.wait(), timeout=sleep_for
                )
            except asyncio.TimeoutError:
                pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("due_loop iteration failed")
            await asyncio.sleep(self._due_loop_max_tick_seconds)
```

Where:
- `_collect_due_jobs(now)`: returns sorted list of job names where `state.phase == "sleeping"` and `state.next_fire_at <= now`. Skips jobs already in `phase=="firing"` (overlap guard, see §2).
- `_dispatch_due_job(name, on_fire, *, preserve_next_fire=False)`: marks the job firing and creates the short-lived fire task — see §2.
- `_compute_due_loop_sleep(now)`: `min(max_tick, time_until_earliest_next_fire)`. **Only considers `phase=="sleeping"` jobs** — firing jobs may carry a stale `next_fire_at` from before, and including them would make the loop spin at the lower bound until the fire completes. Bounded by `due_loop_max_tick_seconds` (default 30s, exposed as instance attribute `due_loop_max_tick_seconds` for tests). Lower bound `0.05s` to avoid busy loop on edge cases. If no sleeping jobs exist (or all are firing), sleep the full `max_tick`.

**Wakeup event sites** — call `self._due_loop_wakeup.set()` whenever the set of `next_fire_at` values can change:

- end of `_start_job()` (new job becomes schedulable)
- end of `_stop_job()` (one fewer job; recompute earliest)
- inside `_fire_job_once.finally` *after* updating `next_fire_at` (so a 50ms fire on an interval=10s job triggers the next re-evaluation immediately rather than waiting 30s)
- end of `_reconcile_running_jobs(...)` after applying added/removed/updated
- inside `restart_due_loop` after the new task is created (kicks the new loop into immediate iteration)

`set_automation_enabled()` and `reload_now()` indirectly hit reconcile, so they're covered transitively.

The bounded tick is the core of the suspend fix: after host wake, `_now()` reads the new wall clock; any job whose `next_fire_at <= now` fires within the same tick. Recovery latency ≤ `due_loop_max_tick_seconds`. The wakeup event closes the latency gap for normal short-interval scheduling.

### 2. Fire task semantics

**Rename `_job_tasks: dict[str, asyncio.Task]` → `_fire_tasks: dict[str, asyncio.Task]`** to reflect the new role. Under this model the dict only contains **in-flight fire tasks**, never long-sleepers. Sleeping jobs live in `_job_state` only. This rename is mechanical and clarifies every reader/writer site.

```python
def _dispatch_due_job(self, name, on_fire, *, preserve_next_fire=False):
    state = self._job_state.get(name)
    job = self._jobs_by_name.get(name)
    if state is None or job is None or state.phase == "firing":
        return  # overlap guard or stale
    # Capture the previously-scheduled next_fire so manual fires (see §6)
    # can restore it instead of advancing the regular schedule.
    pinned_next_fire = state.next_fire_at if preserve_next_fire else None
    self._mark_job_firing(name)  # phase="firing", fire_started_at=now
    task = asyncio.create_task(
        self._fire_job_once(job, on_fire, pinned_next_fire=pinned_next_fire),
        name=f"scheduler:fire:{name}",
    )
    self._fire_tasks[name] = task

async def _fire_job_once(self, job, on_fire, *, pinned_next_fire=None):
    try:
        logger.info("Scheduler firing job=%s ...", job.name, ...)
        await on_fire(job)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Scheduler job %r failed: %s", job.name, exc)
    finally:
        # Race guard: only touch state if this fire task is still the
        # current entry. A reload/update may have replaced state and task
        # while we were running; in that case the new task owns the slot.
        current = asyncio.current_task()
        if self._fire_tasks.get(job.name) is current:
            self._fire_tasks.pop(job.name, None)
            if self._job_state.get(job.name) is not None:
                post_now = self._now()
                if pinned_next_fire is not None and pinned_next_fire > post_now:
                    # Manual fire that didn't displace a future scheduled fire.
                    next_fire = pinned_next_fire
                else:
                    next_fire = self._compute_next_fire_after_completion(job, post_now)
                self._mark_job_sleeping(job.name, next_fire_at=next_fire)
                self._due_loop_wakeup.set()

def _compute_next_fire_after_completion(self, job, post_now):
    if job.cron:
        return _next_cron_fire(_parse_cron_expression(job.cron), post_now)
    interval = job.interval_seconds or 0
    return post_now + timedelta(seconds=interval)
```

Preserves today's semantics:
- cron next-fire = next matching moment **after fire completion** — missed intermediate cron beats stay skipped (no backfill).
- interval next-fire = `completion_time + interval`.
- initial interval delay still honored via the initial `next_fire_at` set in `_start_job`.
- `phase=="firing"` blocks duplicate dispatch from a subsequent tick if a fire runs longer than `max_tick`.

`_mark_job_firing` and `_mark_job_sleeping` (lines 677-695) keep their current contracts; only the callsites change.

### 3. Reload semantics

**Critical change**: today `_reconcile_running_jobs` infers active jobs from `_job_tasks.keys()`. Under the new model `_fire_tasks` only holds in-flight fires, so that diff is wrong (it would treat a sleeping job as "removed"). **Pass the diff explicitly** from `_apply_snapshot`:

```python
# _apply_snapshot — already computes these sets
added, removed, updated = self._diff_snapshot(prior, snapshot)
await self._reconcile_running_jobs(added, removed, updated, on_fire)
```

`_reconcile_running_jobs(added, removed, updated, on_fire)` then operates on the explicit sets:

- **removed**: `_stop_job(name)` — see ordering below.
- **added**: `_start_job(job, on_fire)` — initializes `JobRuntimeState` only. Due loop picks it up on next tick.
- **updated**: `_stop_job(name)` (cancel in-flight if any, drop state) then `_start_job(job, on_fire)` (re-init with new schedule). `next_fire_at` is recomputed against the new spec.

**`_stop_job(name)` ordering** (matters because the in-flight `_fire_job_once.finally` would otherwise mark the removed job back to sleeping):

```python
async def _stop_job(self, name):
    task = self._fire_tasks.pop(name, None)  # 1. remove our slot first
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    self._job_state.pop(name, None)  # 2. drop state after the task is gone
```

The race guard inside `_fire_job_once.finally` (see §2) belt-and-suspenders this: even if ordering is violated, the `if self._fire_tasks.get(job.name) is current` check ensures the cancelled task doesn't write into a slot that was reassigned. The `if self._job_state.get(job.name) is not None` check ensures `_mark_job_sleeping` is a no-op for removed jobs.

`_apply_snapshot` (lines 566-606) keeps its current diff logic but now hands the sets to reconcile instead of relying on `_fire_tasks` introspection.

### 4. Supervisor changes

In `src/oh_my_agent/gateway/manager.py` `_run_scheduler_supervisor` (lines 757-792):

- **Drop** the per-job `restart_job(name, reason="missed_fire")` path. There is no long-sleeper to restart; the due loop handles overdue jobs natively.
- **Keep** the reload-loop restart path (`restart_reload_loop`) **and its rate-limit** (`_DEFAULT_MIN_RESTART_INTERVAL_SECONDS = 120s`). The reload loop can still legitimately go stale if filesystem scan hangs, and the rate limit prevents thrash.
- **Add** due-loop liveness: if `_due_loop_task.done()` while not stopped (or no progress for `due_loop_max_tick_seconds * factor`), call new `scheduler.restart_due_loop(reason=...)`. Mirror the reload-loop pattern, **including the same rate limit** (reuse `_DEFAULT_MIN_RESTART_INTERVAL_SECONDS`).

In `scheduler.py`:

- **Keep** `_DEFAULT_MIN_RESTART_INTERVAL_SECONDS = 120.0` and the `restart_in_progress` re-entry guard machinery — both are reused by `restart_reload_loop` and the new `restart_due_loop`.
- **Remove** the per-job `restart_job(name, reason)` method (only consumer was the supervisor's missed_fire path, which goes away).
- **Keep** the `JobRuntimeState.restart_in_progress` / `last_restart_at` / `last_restart_reason` fields untouched this round — they will simply stay `None` after the per-job restart goes away. Removing them would churn `/doctor` rendering and snapshot tests for no functional gain. Mark for follow-up cleanup.
- **Add** new public APIs:
  - `restart_due_loop(*, reason: str) -> bool` — mirror of `restart_reload_loop`. Steps in order:
    1. If `self._on_fire is None` → return `False` (scheduler not running).
    2. Check `_due_loop_state.restart_in_progress` re-entry guard → return `False` if set.
    3. Check rate limit (`_DEFAULT_MIN_RESTART_INTERVAL_SECONDS`) → return `False` if too soon.
    4. Set `restart_in_progress = True`.
    5. Cancel old `_due_loop_task` (if any) and `await asyncio.gather(task, return_exceptions=True)` — must complete before creating the new one to avoid two loops dispatching simultaneously.
    6. `self._due_loop_task = asyncio.create_task(self._due_loop(self._on_fire), name="scheduler:due")`
    7. Update `last_restart_at`, `last_restart_reason`, `last_progress_at`; clear `restart_in_progress` in `finally`.
  - `get_due_loop_runtime_state() -> DueLoopRuntimeState | None` — snapshot copy of `last_progress_at`, `last_restart_at`, `last_restart_reason`, `restart_in_progress`. Mirror of `get_reload_runtime_state`. Used by `/doctor`.
- **`evaluate_job_health()` scopes** — explicit and distinct so the manager's dispatch is unambiguous:
  - `scope == "reload"` → `restart_reload_loop(reason=...)`
  - `scope == "due_loop"` → `restart_due_loop(reason=...)` (new — fires when `_due_loop_task.done()` while not stopped, or `last_progress_at` is older than `due_loop_max_tick_seconds * factor`)
  - `scope == "job"` + `reason == "missed_fire"` → **informational only**, log + render in `/doctor`, no restart
- Add `_due_loop_state: DueLoopRuntimeState` with `last_progress_at` (touched once per due-loop iteration via `_touch_due_loop_progress`) plus the rate-limit fields (`last_restart_at`, `last_restart_reason`, `restart_in_progress`).
- Update `evaluate_job_health` docstring (lines 749-758) to describe the new `due_loop` scope and the demoted `missed_fire`.

### 5. `next_run_at` persistence (out of scope — follow-up)

Today persisted `automation_runtime_state.next_run_at` is **display-only**: `Scheduler.run()` always recomputes from cron/interval and never seeds `JobRuntimeState.next_fire_at` from disk. This patch preserves that — the due loop reads in-memory `next_fire_at` only.

Process-restart catch-up (firing a job that became overdue while the bot was down) is therefore **not** addressed here. Add a `// FOLLOW-UP` note in the PR description so it's not forgotten. The host-suspend bug — the actual reported issue — is fully addressed because the bot process stays alive across host suspend; only its event loop is paused.

`_sync_automation_runtime_state` (line 794-809) keeps consulting `compute_*_next_run_at` for the bulk-reload startup path — fine, no in-memory state exists yet at that moment.

**`_refresh_automation_next_run_at(name)` (line 811-829) needs one new branch** to stay consistent with `preserve_next_fire`. Today it always recomputes via `compute_job_next_run_at(name)` (cron/interval-spec-only) and writes the result to `automation_runtime_state.next_run_at`. After a manual fire of an interval job, the in-memory `next_fire_at` is the *pinned* future time, but the persisted display would still show `completion_time + interval` — UI lies about the next run.

Fix:

```python
def _refresh_automation_next_run_at(self, name):
    state = self._scheduler.get_job_runtime_state(name)
    now = self._scheduler._now() if state else None  # or datetime.now(tz)
    if state is not None and state.phase == "firing" and state.next_fire_at and state.next_fire_at > now:
        # Manual fire pinned a future schedule — preserve it.
        next_run_at = state.next_fire_at
    else:
        next_run_at = self._scheduler.compute_job_next_run_at(name)
    self._store.upsert_automation_state(name, next_run_at=next_run_at.isoformat() if next_run_at else None)
```

(Logic only — keep current code's exact upsert signature and error handling.)

For non-pinned cases (regular scheduled fires, cron jobs whose `compute_job_next_run_at` already matches the in-memory state) behavior is unchanged.

**Implementation note**: reaching into `scheduler._now()` from `_refresh_automation_next_run_at` is acceptable for this patch since the scope is tiny and well-contained, but it leaks scheduler internals. **Follow-up**: add a public `Scheduler.compute_display_next_run_at(name) -> datetime | None` that encapsulates the `phase=="firing"` + pinned-future logic, so display callers don't reach into `_now()` or branch on phase themselves.

### 6. `fire_job_now` (manual `/automation_run`)

Currently (lines 329-340) `fire_job_now` `await`s `on_fire(job)` synchronously and returns after the fire completes. Under the new model fires run in dispatched short-lived tasks, decoupled from the request lifecycle.

**Two decisions:**

**(A) `fire_job_now` returns *after dispatch*, not after `on_fire` completes.** Rationale:

- Consistency with the due-loop path: every fire goes through `_dispatch_due_job` → fire-and-forget task. Manual fires using a different (synchronous) path would re-introduce the duplicate-execution model the refactor is trying to remove.
- `/automation_run` is currently used to trigger automations whose runs end up enqueued into `runtime_tasks` anyway via `_dispatch_scheduled_job`. The synchronous wait today is misleading — it only awaits the enqueue, not the actual runtime task work.
- Discord interaction tokens have a 15-minute timeout; long automations would time out the slash response anyway.

**(B) Manual fire does NOT displace the next regular schedule.** Old behavior: `fire_job_now` called `on_fire` directly and never touched `_job_state`, so the long-sleeper kept its original wakeup time. Naively routing manual fires through `_dispatch_due_job` would make `_fire_job_once.finally` recompute `next_fire_at` as `completion_time + interval` — meaning a manual run at 10:00 of a 1h-interval job would push the next regular fire from 11:00 to 11:00:N (N = manual-fire duration), silently shifting the cadence. Fix via the `preserve_next_fire=True` plumbing in §2:

- Capture `pinned_next_fire = state.next_fire_at` *before* `_mark_job_firing`.
- After fire completes, if `pinned_next_fire > now`: restore it (manual fire didn't displace anything in the future).
- Else (pinned was already overdue — e.g. user manually fired a job that was about to fire anyway): use the normal post-completion computation. This avoids an immediate double-fire on the next due-loop tick.

**Return shape**: a small string-typed result code so the service layer can render an actionable message instead of a vague "false":

```python
FireJobResult = Literal["ok", "not_found", "scheduler_down", "already_firing"]

async def fire_job_now(self, name) -> FireJobResult:
    job = self._jobs_by_name.get(name)
    state = self._job_state.get(name)
    if job is None or state is None:
        return "not_found"
    on_fire = self._on_fire
    if on_fire is None:
        return "scheduler_down"
    if state.phase == "firing":
        logger.info("fire_job_now: %r already firing — skipping manual fire", name)
        return "already_firing"
    self._dispatch_due_job(name, on_fire, preserve_next_fire=True)
    return "ok"
```

**`/automation_run` user-facing message must change**:

- Slash-command response lives in `gateway/services/automation_service.py` (recon: line 66 calls `fire_automation`) and the discord wiring (`gateway/platforms/discord.py:888` passes the callback). Update both touch points.
- Map result codes:
  - `"ok"` → `"Dispatched {name} — fire queued, watch the channel for the result."`
  - `"not_found"` → `"Automation {name} not found."`
  - `"scheduler_down"` → `"Scheduler is not running."`
  - `"already_firing"` → `"Automation {name} is already firing — manual run skipped."`
- All callers of `fire_automation` / `fire_job_now` must be updated to handle the new return shape; old callers expecting `bool` will break at type-check time. Known callsites:
  - `gateway/manager.py:835` (`GatewayManager.fire_automation` public wrapper)
  - `gateway/services/automation_service.py:66` (slash command path)
  - `gateway/platforms/discord.py:888` (callback wiring)
  - `gateway/services/task_service.py` — `TaskService` injects `self._scheduler.fire_job_now` and uses its bool return inside `replace_draft_task()` to render "refired / returned False" messages. Map to result code there too.

  Confirm by greping `fire_job_now` and `fire_automation` during implementation — fix every site so type checking passes cleanly.

Behavior changes to call out in CHANGELOG: manual fires while a scheduled fire is in-flight are now **refused** (was: ran concurrently); manual fires return immediately on dispatch (was: waited for the fire's `on_fire` enqueue step to finish).

### 7. Consistent `_now()` threading + shutdown ordering

**`_now()` must replace every `datetime.now(self._timezone)` inside the Scheduler instance** — not just inside the due loop. Otherwise fake-clock tests stay half-true: the due loop sees the simulated time but `_mark_job_firing`, `_compute_next_fire_after_completion`, `compute_next_run_at`, etc. still read real wall clock and produce inconsistent state.

12 sites confirmed via grep, all in `scheduler.py`:

- 239 `compute_next_run_at`
- 303 `run()` init (initial `_reload_state.last_progress_at`)
- 361 `_touch_reload_progress`
- 640 `_start_job` (initial `last_progress_at`)
- 681 `_mark_job_firing`
- 690 `_mark_job_sleeping`
- 761 `evaluate_job_health(now=None)` default
- 812 `restart_job` timestamp checks (going away)
- 857 `restart_reload_loop` timestamp checks
- 923 `_run_interval_job` next_fire (going away)
- 935 `_run_cron_job` loop top (going away)
- 956 `_run_cron_job` post_fire_now (going away)

After the per-job runners are removed, the surviving sites all become `self._now()`. New sites added by this patch (`_due_loop`, `_fire_job_once`, `_dispatch_due_job`, `_compute_next_fire_after_completion`, `_compute_due_loop_sleep`, `restart_due_loop`, `_touch_due_loop_progress`) all use `self._now()` from day one.

Module-level helpers at lines 1020 / 1028 (`build_scheduler_from_config` etc.) stay as-is — they run outside any Scheduler instance.

**Shutdown ordering in `Scheduler.run()` finally block** (or wherever `stop()` happens):

```python
try:
    ...  # main wait
finally:
    self._stop_event.set()
    # 1. Cancel due loop first so no new fires get dispatched.
    if self._due_loop_task is not None:
        self._due_loop_task.cancel()
        await asyncio.gather(self._due_loop_task, return_exceptions=True)
    # 2. Cancel reload loop.
    if self._reload_task is not None:
        self._reload_task.cancel()
        await asyncio.gather(self._reload_task, return_exceptions=True)
    # 3. Cancel any in-flight fire tasks (drain).
    fire_tasks = list(self._fire_tasks.values())
    for t in fire_tasks:
        t.cancel()
    if fire_tasks:
        await asyncio.gather(*fire_tasks, return_exceptions=True)
    # 4. Clear state.
    self._fire_tasks.clear()
    self._job_state.clear()
    self._due_loop_task = None
    self._reload_task = None
```

Order matters: cancelling fire tasks before the due loop would let the loop dispatch a new fire on its next iteration. Clearing state before tasks finish would lose the race-guard's identity check.

## Out of scope (explicit)

- Persisted `next_run_at` becoming startup-authoritative for catch-up. Follow-up.
- `IdleTracker` (`src/oh_my_agent/memory/idle_trigger.py`) has the same monotonic vulnerability — separate change.
- Backfill / replay of historically-missed cron beats. Semantics unchanged: one fire per overdue window.

## Critical files

| File | Change |
|---|---|
| `src/oh_my_agent/automation/scheduler.py` | Add `_now()` + replace 12 `datetime.now(self._timezone)` sites. Add `_due_loop`, `_dispatch_due_job` (with `preserve_next_fire` kwarg), `_fire_job_once` (with race guard + wakeup-set), `_compute_next_fire_after_completion`, `_compute_due_loop_sleep` (sleeping-only), `_collect_due_jobs`, `_touch_due_loop_progress`, `DueLoopRuntimeState`, `_due_loop_state`, `_due_loop_wakeup` event, `due_loop_max_tick_seconds` attr + `_DEFAULT_DUE_LOOP_MAX_TICK_SECONDS`. Add public `restart_due_loop(*, reason)` and `get_due_loop_runtime_state()`. Rename `_job_tasks` → `_fire_tasks` everywhere. Remove `_run_cron_job`, `_run_interval_job`, `restart_job`. **Keep** `_DEFAULT_MIN_RESTART_INTERVAL_SECONDS`, `JobRuntimeState.restart_*` fields. Change `_reconcile_running_jobs` signature to `(added, removed, updated, on_fire)`. Adapt `_start_job` (state-only init + wakeup-set), `_stop_job` (cancel in-flight fire, ordered, + wakeup-set), `fire_job_now` (`Literal` result + dispatch with `preserve_next_fire=True`). Update `evaluate_job_health` docstring + soften `missed_fire` to informational + add `due_loop` scope. Add explicit shutdown ordering in `run()` finally. |
| `src/oh_my_agent/gateway/manager.py` | In `_run_scheduler_supervisor` (757-792): remove `missed_fire` per-job restart; add due-loop liveness check + `restart_due_loop` call (rate-limited). Update `fire_automation` (line 835) to map `Literal` result code. |
| `src/oh_my_agent/gateway/services/automation_service.py` | Update `/automation_run` response (around line 66) to map `Literal` result code → user-facing message (4 cases). |
| `src/oh_my_agent/gateway/services/task_service.py` | Update `replace_draft_task()` (and any other site invoking `fire_job_now`) to handle `Literal` result code instead of `bool`. |
| `src/oh_my_agent/gateway/platforms/discord.py` | If the slash-command response message lives here (around line 888), update wording. |
| `tests/test_scheduler_watchdog.py` | Rewrite per-job-task tests as due-loop + restart_due_loop tests (see §Verification). |
| `tests/test_scheduler.py` | Update any test that asserts `_fire_tasks[name]` (formerly `_job_tasks[name]`) exists for sleeping jobs (it won't anymore). Mechanical rename otherwise. |
| `CHANGELOG.md` | Short entry under `## Unreleased` → `### Changed`. |

## Verification

### Tests (rewrite `tests/test_scheduler_watchdog.py`, add to `tests/test_scheduler.py`)

Use existing idiom: build a real `Scheduler`, `await scheduler.run(on_fire_mock)`, manipulate `_job_state` and shrink `due_loop_max_tick_seconds` for fast loops. No freezegun.

1. **`test_due_loop_fires_cron_job_when_due`** — cron job, manually set `state.next_fire_at = now - 1s`, set `due_loop_max_tick_seconds = 0.05`. Assert `on_fire` called within ~0.2s.

2. **`test_due_loop_fires_interval_job_with_initial_delay`** — interval job with `initial_delay_seconds=0.2`. Assert `on_fire` called between 0.2s and ~0.5s (loose upper bound).

3. **`test_due_loop_does_not_double_fire_in_flight_job`** — job's `_fire_job_once` blocks on an `asyncio.Event`. Force two consecutive due-loop ticks. Assert `on_fire` called exactly once until the event is set.

4. **`test_due_loop_advances_next_fire_after_completion`** — interval=10s job. Fire once, assert post-fire `state.next_fire_at ≈ completion_time + 10s` (not start_time + 10s).

5. **`test_due_loop_recovers_after_simulated_host_suspend`** — regression. Use a **mutable fake clock** (e.g. `clock = {"now": real_now}`, `monkeypatch.setattr(scheduler, "_now", lambda: clock["now"])`), **not** an "Nth-call" monkeypatch — the due loop reads `_now` multiple times during init, so call-count fragility would mask bugs. Job with `next_fire_at = clock["now"] + 60s`, `due_loop_max_tick_seconds = 0.05`. Sequence:
   1. Start scheduler, wait for `_due_loop_task` to enter its first `asyncio.sleep` (verify via `state.phase == "sleeping"` for the test job).
   2. Set `clock["now"] = next_fire_at + 1s` (simulates host wake skipping wall clock forward).
   3. Wait on an `asyncio.Event` set inside the `on_fire` mock.
   4. Assert event fires within `~max_tick * 3` real seconds, **without** monotonic time advancing 60s.

6. **`test_reload_remove_cancels_in_flight_fire`** — job is firing (blocked on event). Trigger reload that removes the job. Assert in-flight fire task is cancelled and `_job_state[name]` gone.

7. **`test_reload_update_recomputes_next_fire`** — change a cron job's expression on disk. Trigger reload. Assert `state.next_fire_at` reflects new spec.

8. **`test_fire_job_now_returns_already_firing_when_in_flight`** — job is firing. Call `fire_job_now(name)`. Assert returns `"already_firing"` and no second fire occurs.

9. **`test_fire_job_now_returns_ok_and_dispatches`** — sleeping job. Call `fire_job_now(name)`. Assert returns `"ok"`, `_mark_job_firing` was called, and `next_fire_at` updates after completion. Also cover `"not_found"` (unknown name) and `"scheduler_down"` (scheduler not running) in separate small tests.

10. **`test_supervisor_restarts_due_loop_when_dead`** — kill `scheduler._due_loop_task` externally. Run one supervisor tick. Assert `restart_due_loop` invoked and a fresh task is running.

11. **`test_restart_due_loop_is_rate_limited`** — call `restart_due_loop(reason="test")` twice rapidly, assert second returns `False` due to `_DEFAULT_MIN_RESTART_INTERVAL_SECONDS` guard.

12. **`test_supervisor_no_longer_restarts_per_job_on_missed_fire`** — set up a job with `state.next_fire_at = past`, `phase=sleeping`, `last_progress_at = past - 5min`. Run one supervisor tick. Assert no per-job restart call happened — the due loop is the recovery path. (`scheduler.restart_job` should not exist as an attribute.)

13. **`test_get_due_loop_runtime_state_snapshot`** — assert the snapshot contains `last_progress_at`, `last_restart_at`, `last_restart_reason`, `restart_in_progress` and is a copy (mutating it doesn't affect internal state).

14. **`test_due_loop_wakeup_after_short_interval_fire`** — interval=1s job (parser uses `int()`, so 1 is the smallest positive value), `due_loop_max_tick_seconds=30.0` (intentionally large to prove the wakeup event matters, not the tick). After first fire completes, assert second fire happens within ~1.3s real time, not ~30s. Without the wakeup-set inside `_fire_job_once.finally`, this test would hang ~30s.

15. **`test_manual_fire_preserves_future_scheduled_next_fire`** — interval=3600s (1h) job. Sleeping with `next_fire_at = now + 1800s`. Call `fire_job_now(name)`. After the dispatched fire completes, assert `state.next_fire_at` is still ≈ original `now + 1800s` (within tolerance), **not** `completion_time + 3600s`.

16. **`test_manual_fire_advances_when_pinned_already_overdue`** — same interval=10s job, but artificially set `next_fire_at = now - 5s` before manual fire. After fire completes, assert `state.next_fire_at ≈ completion_time + 10s` (no immediate double-fire).

17. **`test_refresh_next_run_at_preserves_pinned_during_manual_fire`** — interval=3600s job with future `next_fire_at`. Trigger manual fire that blocks on event. While `phase=="firing"`, call `_refresh_automation_next_run_at(name)`. Assert persisted `next_run_at` matches the pinned future time, not `now + 3600s`. After the fire completes (event released), persisted `next_run_at` matches the pinned time again.

**Delete:** existing `test_scheduler_watchdog.py` tests for `restart_job` per-job rate-limiting and per-job `task_done_unexpectedly` restart — both pertain to the removed `restart_job` method. Reload-loop restart rate-limiting tests **stay** (still applicable).

### Manual / regression

- `.venv/bin/pytest` — full suite passes.
- Restart bot, watch logs for one daily cycle, confirm cron jobs fire on time and no `missed_fire` warnings under healthy operation.
- Mac suspend path: not directly unit-testable without real suspension; test 5 simulates the mechanism deterministically.

### CHANGELOG entry

Under `## Unreleased`:

```markdown
### Changed

- **Scheduler now uses a central wall-clock due scanner instead of per-job 
  long sleepers**. Replaces the "one asyncio task per job, each doing 
  `asyncio.sleep(delay)`" model with a single due loop that ticks every 
  ≤30s, reads wall clock, and dispatches any job whose `next_fire_at` has 
  passed. After host suspend (e.g. Docker-on-Mac laptop sleep), scheduled 
  jobs now recover within one scanner tick instead of being silently 
  skipped by the watchdog. No-backfill semantics unchanged: one fire per 
  overdue window. Behavior changes for `/automation_run`: (a) returns 
  immediately after dispatch (was: waited for the fire's enqueue step); 
  (b) refuses if the job is already firing (was: ran concurrently).
```
