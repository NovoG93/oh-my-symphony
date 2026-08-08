# Continuous improvement rubric

This document is the contract for the continuous-improvement heartbeat: a
default-off scheduler that periodically re-verifies the integrated baseline
and turns defects into normal Kanban tickets. It never edits product code.
See `docs/architecture.md` ("Continuous improvement heartbeat") for the
runtime surfaces and `docs/continuous-improvement/ticket-template.md` for the
ticket body format.

## Default configuration

```yaml
continuous_improvement:
  enabled: false
  interval_ms: 1800000   # minimum 60000
  max_turns: 48          # 0 = unlimited
  ticket_prefix: CI
  max_tickets_per_run: 5
  require_idle_board: true
  agent_kind: ""         # "" = workflow default agent
  modes: []              # [] = readiness only (see "Improvement modes")
  mode_interval_hours: {}
  max_improvement_tickets_per_run: 3
```

- `enabled` defaults to `false`.
- `interval_ms` defaults to `1800000` (30 minutes). It accepts only
  positive integers, with a lower bound of `60000` ms (1 minute); values
  below the floor, non-integers, and booleans are rejected.
- `max_turns` defaults to `48` (24 hours at the default interval). `0`
  means unlimited.
- `ticket_prefix` defaults to `CI` and must be identifier-safe (used as
  the tracker's ticket-ID prefix, e.g. `CI-1`).
- `max_tickets_per_run` defaults to `5`.
- `require_idle_board` defaults to `true` (see "Idle-board requirement"
  below).
- `agent_kind` defaults to `""`, which inherits the workflow's `agent.kind`;
  otherwise it must be one of the supported agent kinds (agy, codex, claude,
  gemini, kiro, opencode, pi) and selects which backend runs the tickets this
  heartbeat creates.
- `modes` defaults to `[]`. Unknown mode names are a config error; order and
  duplicates do not matter (the parser canonicalizes both). See "Improvement
  modes" below.
- `mode_interval_hours` defaults to `{}` (each mode keeps its shipped
  cadence); values must be non-negative numbers, `0` meaning "every
  heartbeat".
- `max_improvement_tickets_per_run` defaults to `3` and caps the *proposal*
  tickets (triage + agent modes) a single run may file, separately from
  `max_tickets_per_run` (readiness/security findings).


## Improvement modes (experimental, opt-in)

The heartbeat's original job — product-readiness inspection — is now one
*mode* among several. `continuous_improvement.modes` selects them; the list
is empty by default, and an `enabled: true` block with no `modes:` resolves
to `[readiness]`, which is exactly the pre-modes behaviour. A disabled block
resolves to no modes at all: nothing runs.

```yaml
continuous_improvement:
  enabled: true
  modes: [readiness, blocked_fixes, security, market_research,
          feature_improvements]
  mode_interval_hours:                  # per-mode cadence floor, in hours
    market_research: 168                # weekly (the default)
  max_improvement_tickets_per_run: 3    # cap on proposal tickets per run
```

| Mode | Kind | Default cadence | Output |
| --- | --- | --- | --- |
| `readiness` | fixed argv checks | every heartbeat | bug tickets from failed checks |
| `blocked_fixes` | board triage | every heartbeat | one linked fix ticket per stuck ticket |
| `security` | fixed argv checks | 24 h | patch tickets from scan findings |
| `market_research` | agent turn | 168 h | improvement proposals with evidence links |
| `feature_improvements` | agent turn | 72 h | improvement proposals from a UX / code-health review |

Cadence is enforced by a durable `{mode: last-run epoch}` file at
`.symphony/continuous-improvement/mode-state.json`, so a weekly mode stays
weekly across restarts. When no enabled mode is due, the scheduler re-arms
without consuming a turn (`skipped_reason: no_modes_due`).

### Agent freedom is preserved

Every mode's only board write path is a **normal Kanban ticket**. There is no
parallel execution path: proposals land in the board's first active state and
flow through whatever pipeline the board is configured with (a single ticket
or a stage DAG), dispatched by the normal scheduler. The heartbeat never
plans, edits or merges the work it proposes.

### blocked_fixes

Scans tickets in `Blocked` and `Human Review`, extracts the last blocker-ish
section (`## Blocker`, `## Blocked RCA`, `## QA Failure`, `## Review
Findings`, `## Budget Exceeded`) as a root-cause note, and files one fix
ticket per stuck ticket. The source ticket then gains a `blocked_by` edge to
the fix ticket — an ordinary DAG edge, always acyclic because the fix ticket
is brand new. A source ticket that is already blocked by an open ticket is
skipped: something is already tracking its unblock.

### security

Ecosystem-detected, optional-by-construction scans run against the same
proven baseline as `readiness`:

| Scan | Runs when | Command |
| --- | --- | --- |
| `pip_audit` | `pyproject.toml` or `requirements.txt` exists | `python -m pip_audit --progress-spinner off` |
| `npm_audit` | `package.json` exists | `npm audit --audit-level=high` |

A missing scanner is `not_available`, never `failed` — an uninstalled tool
must not manufacture a security ticket.

### Agent-driven modes

`market_research` and `feature_improvements` need a real agent turn. The
continuous-improvement module never imports the orchestrator; instead the
orchestrator injects an `AgentRunner` capability, and the module hands it an
`AgentTask` (mode, prompt, cwd, output path). The turn:

- runs against the host repo (`cwd == workspace_root == workflow dir`), like
  a chat turn, and outside the dispatch slot accounting;
- receives a succinct prompt built from `docs/symphony-prompts/ci/
  <mode>.md` when present, else the module's built-in default, with
  `{app_context}` (README head, wiki index, open ticket titles),
  `{output_path}` and `{max_proposals}` substituted;
- may write exactly one file — its JSON proposal file under
  `.symphony/continuous-improvement/proposals/` — and files no tickets
  itself. The heartbeat validates, caps, de-duplicates and files them.

A missing runner reports `not_available`; an exploding turn reports
`not_proven` for that mode and never aborts the run.

### Proposal ticket quality

Every proposal ticket carries:

- a `## Goal` / `## Scope` / `## Acceptance criteria` / `## Evidence` body,
  matching the chat-intake description format;
- an evidence block pointing at the report section and the agent's or scan's
  own evidence;
- the `continuous-improvement`, `ci` and per-mode labels;
- a `REQ-CI-<YYYYMMDD>-<n>` request group shared by one run's tickets;
- a priority (1-3), and the configured `agent_kind` override.

De-duplication is two-layered: the `CI Proposal: <mode>/<slug>` marker (an
exact re-proposal) and the normalized title of any open ticket (a human
already filed the same thing). Anything past
`max_improvement_tickets_per_run` waits for the next run.


## Result semantics

Every rubric item resolves to exactly one of these states:

- `passed` — the check ran and succeeded. No ticket is created.
- `failed` — the check ran and revealed a product-readiness defect. A ticket
  is created (subject to de-duplication and `max_tickets_per_run`).
- `not_available` — an optional check is not configured, or a required tool
  is not installed. This is **not** a failure and never creates a ticket.
- `not_proven` — the baseline itself cannot be trusted: dirty worktree,
  missing target branch, unreachable upstream, or an infrastructure failure
  in the check runner (timeout, crash, unexpected exit before completion).

`not_proven` is stronger than `failed`. If the baseline proof step is
`not_proven`, the run stops after recording the report; no downstream check
runs and no tickets are created from that run. A heartbeat that cannot prove
what it tested must not manufacture findings about it.

## Baseline proof (always runs first)

The heartbeat never runs `git checkout`, `git switch`, `git reset`,
`git stash`, or any command that mutates the working tree or HEAD in the
host worktree. When `agent.auto_merge_target_branch` is configured and the
host checkout is on another branch, the heartbeat creates a temporary
detached worktree for the target branch, runs checks there, and removes it
before finishing. It proves:

- current branch name
- current commit SHA
- worktree dirty status (`git status --porcelain`)
- upstream alignment, when an upstream is configured (ahead/behind counts)

If the checked baseline is dirty, the target branch cannot be resolved, the
temporary worktree cannot be created, or the upstream is configured but
unreachable, the baseline proof is `not_proven` and the run ends there.

## Default checks

| Check | Command | Rubric role |
| --- | --- | --- |
| Unit / integration tests | `python -m pytest -q` | `failed` on non-zero exit; `not_proven` on timeout/crash |
| Lint | `python -m ruff check src tests` | `failed` on any reported violation |
| Type check | `python -m pyright` | `failed` on any reported error |
| Browser QA | project-specific, optional | `not_available` unless dependencies and required environment flags are present |
| Read-only DB probes | project-specific, optional | `not_available` unless explicit read-only configuration exists |

Browser and DB checks are opt-in and read-only by construction: they must
never run destructive DB migrations, resets, or seed commands, and a missing
or unconfigured optional check is always `not_available`, never `failed`.

## No-code-edit invariant

The heartbeat **never edits product code**. Its only write surfaces are:

1. `docs/continuous-improvement/latest.md` (machine-owned sections only, see
   below).
2. New Kanban tickets through the tracker's normal creation path.

Any defect it finds becomes a ticket for a normal worker to pick up. The
heartbeat does not attempt fixes, does not open pull requests, and does not
touch files under `src/` or `tests/`.

## Default-off behavior and command safety

- The feature ships disabled (`continuous_improvement.enabled: false`).
  Enabling it is an explicit operator action from the web settings card or
  `WORKFLOW.md`.
- Only `enabled`, `interval_ms`, `max_turns`, `modes`, and `agent_kind` are
  browser-editable. The check list, ticket template, environment variables,
  and file paths are trusted workflow configuration, not remotely
  configurable.
- Every check runs as a predefined `argv` array with `shell=False` — no
  shell string interpolation, no user-supplied command text.
- Every subprocess has an explicit timeout. A timeout is recorded as
  `not_proven` (or `not_available` for optional checks), never silently
  dropped.
- Captured output is capped in size and scanned for obvious secret patterns
  (tokens, keys, credentials) before it is written to the report or a ticket
  body; matches are redacted.
- The heartbeat never runs destructive DB migrations, resets, or seed
  commands. DB checks are limited to read-only probes from explicit
  configuration.

## Cross-process lease

Multiple orchestrator processes can point at the same workflow directory
(for example, two terminals running the same board). Concurrent heartbeat
runs against the same baseline would double-report findings and race on
`docs/continuous-improvement/latest.md`. A durable, fakeable lease (the same
family as `RunRegistry.acquire_run` in
`src/symphony/orchestrator/run_registry.py`) guards each run: a process must
acquire the lease before starting a heartbeat run and release it when the
run finishes or the process exits. A process that cannot acquire the lease
skips its scheduled run rather than blocking.

## Turn budget

Each completed run (any terminal outcome, including `not_proven`) consumes
one turn. `max_turns` defaults to 48 (24 hours at the default 30-minute
interval); `0` means unlimited. When `turns_used >= max_turns`, the
scheduler stops scheduling new runs and reports
`skipped_reason: max_turns_reached` until an operator resets the counter
(`POST /api/v1/workflow/continuous-improvement/reset-turns`) or restarts the
orchestrator. The counter is in-memory only.

## Idle-board requirement

`require_idle_board: true` (the only supported value in the first
implementation) postpones a due run while normal workers are running or
retrying. The heartbeat never competes with normal ticket dispatch for
`max_concurrent_agents` slots; it runs as a bounded background task outside
the tick loop and only when the board would otherwise be idle.

## Tracker support matrix

| Tracker | Ticket creation | Notes |
| --- | --- | --- |
| File board (`FileBoardTracker`) | Supported | Uses `create_with_next_identifier(prefix="CI")`; de-duplicated by `CI Fingerprint` |
| Jira / other remote trackers | Not supported (first implementation) | Registrar reports `skipped_reason: unsupported_tracker`; the run still completes and writes its report |

A tracker without a safe, idempotent creation contract must report
`unsupported_tracker` rather than crash the run or fall back to an unsafe
write path.

## De-duplication

Each finding is fingerprinted from a stable subset of its content (rubric
item, check name, normalized failure summary). Before creating a ticket, the
registrar searches active tickets for an existing `CI Fingerprint: <hash>`
line:

- match found → append an observation, or skip if nothing new to add; no new
  ticket is created.
- no match → create a new ticket, up to `max_tickets_per_run` (default 5) new
  tickets per run. Additional findings beyond the cap wait for the next run.

Ticket writes always go through tracker APIs (lock / compare-and-swap
identifier allocation), never direct Markdown rewrites.
