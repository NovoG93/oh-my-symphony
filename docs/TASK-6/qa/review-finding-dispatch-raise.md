# TASK-6 Verify — Review Finding: unguarded `selection_for_state` in `_dispatch`

## Goal

Record a MEDIUM robustness defect found during the Verify review of the TASK-6
diff (dispatch-selection alignment), with the exact code paths, trigger
conditions, requested fix, and scope. Written so the In Progress rework can
fix it without re-deriving the analysis.

## What the change did

TASK-6 replaced the dispatch-time backend resolution in `_dispatch`
(`src/symphony/orchestrator/core.py:6177-6182`):

```python
# before
agent_kind = cfg.agent.kind_for_state(issue.state, _requested_agent_kind(issue))
# after
dispatch_selection = cfg.selection_for_state(
    issue.state,
    ticket_profile=_requested_agent_profile(issue),
    ticket_kind=_requested_agent_kind(issue),
)
agent_kind = dispatch_selection.kind
```

## The defect

`selection_for_state` (`src/symphony/workflow/config.py:310-399`) is a
*validating* resolver. It raises `ConfigValidationError` (a `SymphonyError`
subclass, `src/symphony/errors.py:148`) when:

1. the ticket frontmatter sets **both** `agent_kind` and `agent_profile`
   ("ambiguous agent override", `config.py:336-341`) — a realistic operator
   migration case: an old `agent_kind`-pinned ticket gets a new
   `agent_profile` without removing the pin; and
2. the ticket or stage names a profile that is not in a **non-empty**
   `agent_profiles` dict ("unknown agent profile", e.g. `config.py:366`,
   `config.py:381`, `config.py:395`).

`kind_for_state` (`config.py:294-308`) never raised, so the raise at
dispatch time is new behaviour introduced by this branch.

The raise is **unguarded**:

- `_dispatch` (`core.py:6147+`) has no try/except around the selection call
  (the only guard wraps `_prepare_release_dispatch` earlier at
  `core.py:6156-6166`).
- Both call sites — the schedule-projection candidate loop inside `_on_tick`
  (`core.py:3940`, loop at `core.py:3802-3956`) and the FIFO fallback
  (`core.py:4036`) — call `_dispatch` with no per-ticket exception handling.

The raise therefore escapes to `_tick_loop`'s broad handler
(`core.py:3568-3589`), which counts a `tick_failed`, applies exponential
backoff, and re-fires the tick. Every subsequent tick re-sorts the same
misconfigured ticket and re-raises at the same position, so:

- every candidate sorted *after* the bad ticket is starved indefinitely
  (the loop aborts mid-iteration);
- the failure log (`tick_failed`, `core.py:3579-3584`) records the error
  string but **not** the ticket identifier, making the culprit hard to find.

## Why this is a regression and not intended behaviour

The plan (§3 precedence) intends the *rejection* of ambiguous/unknown
tickets — but the rejection must be per-ticket, not scheduler-wide. Before
TASK-6 the same ticket flowed through `_dispatch` normally and the identical
`selection_for_state` raise happened inside the supervised worker
(`_run_agent_attempt`, `core.py:6632-6637`, Phase 2 code). There the worker
done-callback logs the failure **with issue id** (`core.py:6497-6513`,
`worker_task_done_without_cleanup`), and the retry/`Blocked` path contains
the failure to that one ticket. TASK-6 moved the same raise one step earlier
into the unguarded scheduler loop.

The codebase's own convention for exactly this class of error is one method
up: `_on_tick` wraps `validate_for_dispatch(cfg)` in
`try/except SymphonyError` and aborts the tick gracefully
(`core.py:3674-3678`). The new selection call needs the same treatment.

## Requested fix (scope: `core.py` `_dispatch` + one regression test)

1. Wrap the `selection_for_state` call in `_dispatch`
   (`core.py:6177-6182`) in `try/except ConfigValidationError` (or
   `SymphonyError`): log an error including `issue_id`/`identifier`, and
   treat it as a per-ticket dispatch refusal — return `False` so the caller
   records `refused_dispatch_authority` (same outcome as an acquisition
   refusal, `core.py:3946-3955`), optionally with a tracker note naming the
   misconfiguration. Do not let it escape to the tick loop.
2. Add a regression test (in `tests/test_workflow_agent_profiles_backend.py`
   or the orchestrator test module): an `Issue` with both `agent_kind` and
   `agent_profile` set (and a second case: unknown profile with non-empty
   `agent_profiles`) routed through `_dispatch` must produce a refusal, not
   an exception.

## Evidence status

- What this proves: the raise path, its unguarded call sites, and the
  regression are established by direct reading of the cited code; the
  finding's trigger conditions match the resolver's own error messages.
- What it does not prove: no runtime reproduction was run this pass —
  live pytest execution is denied by the workspace permission gates, and the
  review gate rewinded the ticket before QA (QA deferred to the re-verify
  pass).

## Branch hygiene (LOW, same review pass)

The branch commit `860c284` added a `graphify-out` **symlink**
(`/home/symphony/git/oh-my-symphony/graphify-out`). `.gitignore` line 39
(`graphify-out/`, trailing slash) only ignores real directories, so the
symlink slipped into the auto-commit. On the host, `graphify-out` is a real
untracked directory (contains `graph.json` etc.); a merge checkout would
replace it with a self-referencing symlink and break the host's graphify
tooling. Verify removed the symlink from the working tree
(`git status` now shows `deleted: graphify-out`); the turn-end auto-commit
stages deletions the same way it staged the addition. Do **not** recreate a
committed `graphify-out` symlink. Confirm before the final merge that
`git diff <main-tip> symphony/TASK-6 -- graphify-out` is empty.
