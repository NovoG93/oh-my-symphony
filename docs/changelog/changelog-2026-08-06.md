# Changelog - 2026-08-06

## Chat page: talk to the connected agent about the host repo, co-work in it

### Problem theory

Symphony only ran agents ticket-at-a-time. Asking the configured agent "explain this repo's
backend abstraction" or pairing with it on an ad-hoc change meant leaving the dashboard for a
terminal session that knew nothing about the workflow's configuration. The backend adapters were
already orchestrator-independent (`build_backend(BackendInit(...))`), but nothing exposed them to
the operator.

### Decisions

- A `ChatManager` (`symphony/chat.py`) drives the configured backend directly with
  `cwd == workspace_root == workflow_dir`, so `validate_agent_cwd` passes without touching
  `backends/*` and the conversation happens in the operator's own working tree. Chat lives outside
  `DispatchState` slot accounting: one session, single-flight turns, never starving ticket workers.
- Two permission modes. `qa` is read-only where enforceable — claude gets `--permission-mode plan`
  via strip-then-append on the configured command (last flag wins), codex gets a read-only
  sandbox; `edit` restores the configured behaviour. Claude mode switches rebuild the backend and
  carry `--resume <session>` so context survives; codex restarts its thread and the UI warns.
  Backends without an enforcement knob report `mode_enforced: false` and rely on the preamble.
- Streaming is a one-way WebSocket (`/api/v1/chat/ws`): hello snapshot on connect, then every
  ChatMessage (user echo, agent markdown, tool activity, turn lifecycle). All mutations stay REST
  (`/api/v1/chat/{session,message}`) so the JSON content-type guard keeps covering CSRF; the WS
  handler additionally rejects cross-origin upgrades on loopback binds because browsers skip CORS
  for WebSockets. Slow subscribers drop oldest frames rather than stalling the turn.
- The first-turn preamble teaches the agent the file board: where `board_root` lives, the ticket
  front-matter shape, and that filing an issue means creating `<board_root>/<ID>.md` with
  `state: Todo`. In edit mode the agent can therefore register tickets the orchestrator will
  dispatch; every turn end calls `request_refresh()` so pickup does not wait out the poll
  interval. (File boards only — hand edits and agent edits are already interchangeable there.)
- Transcripts append to `.symphony/chat/<session>.jsonl` (non-blocking, StatsStore pattern);
  restarts do not restore the conversation — the file is a record.
- `app.on_shutdown` closes live sockets first (an open WS otherwise holds `runner.cleanup()`)
  and then stops the chat backend. No changes to `server.py`, `cli/main.py` or the orchestrator.

### Alternatives rejected

- Running chat through the orchestrator's dispatch path: dispatch is ticket-shaped (workspace
  provisioning, hooks, budgets, board transitions) — all wrong for a conversation, and a chat
  would occupy one of `max_concurrent_agents` (often 1), starving the board.
- A chat-only worktree: safe but defeats "co-working" — the operator wants the agent's edits in
  the tree they are looking at. Read-only qa mode is the default posture instead.
- Client-to-server WebSocket messages: would need its own CSRF story; REST mutations already
  have one.

### Verification

- `tests/test_chat.py`: session lifecycle, turn locking, continuation sequencing, mode-derived
  command/sandbox variants, failure broadcast, fan-out, JSONL transcript — against a fake backend.
- `tests/test_webapi_chat.py`: REST contract (201/202/404/409/415), WS hello → turn event stream,
  cross-origin rejection, shutdown stops the backend.
- Browser-verified against a scratch repo with the real claude CLI: qa question streams tool
  activity and a markdown answer; edit mode modifies the working tree.

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
