# Human Review completion

PR #56 originally changed the TUI and the retired standalone board viewer.
Current Symphony serves its board from the orchestrator. The remaining defect
was that both the TUI confirmation and the service board's state edit could
write `Done` without running the host completion lifecycle.

`Orchestrator.confirm_done` now owns manual confirmation. The TUI `c` action,
`POST /api/v1/{identifier}/confirm-done`, and a service-board PATCH from
`Human Review` to `Done` all call it. Other issue edits retain their existing
response shape and behavior.

Confirmation requires a retained workspace and no active worker. A host run
lease serializes delivery across service instances and is renewed while Git
and hooks run. The ticket and release identity are re-read after acquisition;
ownership is checked before each delivery stage. Repeated confirmation of an
already-Done ticket returns `changed: false` without rerunning effects.

The host records delivery history and runs the existing merge gate before
writing Done. History failure preserves Human Review. Merge failure uses the
existing Blocked fallback and preserves the workspace. Both return an explicit
failure to the caller. A successful transition records stats and notifications,
runs `after_done`, applies the configured workspace cleanup policy, and awaits
wiki counting/sweeping. The existing `after_done_failure_policy` is unchanged:
`warn` removes the workspace; `block` preserves it and records an error while
the ticket remains Done.

Application-release verifiers, finalizers, and registered release-evidence
tickets cannot be manually confirmed. Their approval belongs to an authorized
run; removing a label does not remove the registry identity. Resume their
normal verification/finalization flow instead of bypassing its authority.

Main already preserved Human Review workspaces at startup and preferred Blocked
for exhausted retry budgets. Those implementations and regression tests are
retained. The deleted standalone board viewer is not restored.

Regression coverage lives in `tests/test_confirm_done.py`, alongside the
existing TUI, server, startup-cleanup, and retry tests. It includes concurrent
requests, short-lived leases with a competing registry, lost ownership,
stale snapshots, missing workspaces, failed history/merge, stripped release
labels, and both hook-failure policies.
