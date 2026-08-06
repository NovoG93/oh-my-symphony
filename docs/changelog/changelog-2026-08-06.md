# Changelog - 2026-08-06

## Git page: host-repo history and operator-driven merges in the web UI

### Problem theory

Symphony creates a `symphony/<ID>` branch per ticket and merges it at the Verify gate, but the web
UI showed none of that. When a merge gate failed and parked a ticket in Blocked, recovery meant
leaving the dashboard for a terminal: inspect the branch, compare it against the target, run the
merge by hand, and hope the manual invocation matched the gate's exclude-path policy. The board
managed tickets; the repository state those tickets produce was invisible and unmanageable.

### Decisions

- A read-only query module (`utils/git_inspect`) wraps git subprocesses and degrades to empty
  results on any failure, matching the web API's read-degradation principle. Ref names are
  validated by the caller before they reach git; the leading-alphanumeric rule also blocks
  option-injection via refs shaped like flags.
- New routes under `/api/v1/git/`: `log` (commit history, whole repo or one ref),
  `task-branches` (`symphony/*` branches enriched with board tickets, merged/ahead/behind, and the
  running-worker flag), `compare` (merge preview: commit list plus three-dot numstat), and the one
  mutation, `POST merge`. `GET branches` moved into the section with path and shape unchanged.
- Manual merge reuses `auto_merge_on_done_best_effort` verbatim, so an operator merge and the
  automatic Verify gate are the same operation: same exclude paths, `--no-ff` shape, conflict
  preflight, dirty-host guard, and `AutoMergeResult` vocabulary. Guards before it runs: task-branch
  whitelist (`SYMPHONY_BRANCH_PREFIX` + identifier check), running-worker 409, and a single-flight
  lock. Failures map to `409 merge_<status>`.
- Merge success appends a best-effort `Manual Merge` note to the ticket and requests a board
  refresh; ticket state never changes here. Unblocking stays with the existing recover-blocked
  flow, keeping state transitions an explicit operator action.
- The SPA gains a `#/git` sidebar route with three on-demand cards (task branches, history,
  compare) and a target-select merge modal. No polling; a Refresh button re-loads.
- The branch-name prefix moved to `workflow/constants.SYMPHONY_BRANCH_PREFIX`; orchestrator
  call-sites derive from it, and the worktree setup script documents the mirror it cannot import.

### Alternatives rejected

- A bespoke `git merge` path for the endpoint: shorter, but it would bypass exclude paths (today:
  `kanban`), skip the dirty-host and conflict preflights, and let manual and automatic merges
  diverge — the divergence itself being the bug surface.
- Auto-unblocking tickets after a successful manual merge: state transitions have dispatch and
  stats side effects and belong to the recover flow the operator already knows.
- Polling the git cards like the board: git subprocess fan-out per refresh buys nothing on a
  local operator tool; on-demand loading keeps the server quiet.

### Verification

- `tests/test_git_inspect.py`: log parsing, task-branch merge states, compare numstat (binary
  included), non-repo degradation against throwaway repos.
- `tests/test_webapi.py`: endpoint contracts on a real temp repo — ticket mapping, unknown-ref and
  injection rejections, a real end-to-end merge with note append, failure-status mapping, and
  concurrent-merge rejection.
- `tests/test_web_static_contract.py`: SPA route, api client methods, and CSS class contract.
- Browser-verified against a scratch board: merge modal → merged badge, merge commit in history,
  `Manual Merge` note on the ticket.
