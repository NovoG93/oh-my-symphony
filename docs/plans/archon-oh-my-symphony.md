# PRD: Governed Per-Ticket Workflows for `oh-my-symphony`

**Status:** Proposed
**Date:** 2026-08-07
**Target repository:** `cskwork/oh-my-symphony` (`main`, reviewed at version `0.16.1`)
**Reference product:** current `coleam00/Archon` workflow-engine rewrite (`dev`), not the archived RAG/task-management version
**Working feature name:** Governed Workflow Mode
**Primary owner:** TBD
**Validation basis:** official repository documentation and current source review; no implementation or runtime benchmark was performed for this PRD

---

## 1. Executive decision

Add an **opt-in, repository-defined workflow engine inside each Symphony ticket run**.

The engine will execute a validated YAML DAG containing AI-agent, deterministic shell, and human-approval nodes. It will provide node-level history, artifacts, retries, explicit crash recovery, and operator controls in the existing CLI, TUI, and web app.

This must **not** become a second scheduler or an embedded Archon installation:

- Symphony remains the authority for ticket dispatch, concurrency, worktree creation, branch lifecycle, backend processes, budgets, and terminal ticket handling.
- One ticket keeps one Symphony run and one existing ticket workspace/branch lifecycle as configured by Symphony.
- The DAG executes inside that existing ticket run; live node execution reuses Symphony’s lease and process-ownership mechanisms.
- Existing `WORKFLOW.md` stage-prompt behavior remains unchanged unless workflow mode is explicitly enabled.
- Symphony’s seven `AgentBackend` implementations remain the provider abstraction; workflow nodes may choose among them without importing Archon’s provider layer.

The product outcome is **Archon-like determinism and auditability with Symphony’s stronger multi-CLI, file-first, Kanban-oriented operating model**.

### 1.1 Assumptions

- Symphony remains a local, single-host product backed by SQLite for this initiative.
- File-tracker behavior is the first complete mutation target; Linear and Jira retain live runtime overlays until their transition capabilities are verified.
- Repository workflow files are trusted code, while ticket text, prior model output, and external collaboration content are untrusted data.
- Endpoint, command, and package names in this PRD are proposed contracts and should be reconciled with the latest `main` branch at implementation kickoff.
- No active governed run may be automatically migrated to a changed workflow definition.

---

## 2. Why this improvement is needed

### 2.1 Current Symphony strength

`oh-my-symphony` already provides capabilities that should be preserved:

- ticket-level selection among Codex, Claude Code, Gemini, AGY/Antigravity, Kiro, OpenCode, and Pi;
- parallel ticket execution in isolated Git worktrees;
- file, Linear, and Jira tracker adapters;
- live TUI and browser Kanban views;
- configurable stage prompts, hooks, budgets, retries, auto-triage, branch policy, and per-ticket artifacts;
- SQLite-backed dispatch leases and issue safety flags;
- recent run rows through the CLI and `GET /api/v1/runs`;
- operator chat, Git branch/diff/PR controls, health checks, and local service management.

### 2.2 Current limitation

The development process is primarily encoded as **stage-specific prompts plus agent-authored ticket transitions**. For the file tracker, the agent changes the Markdown ticket’s `state` field to move work across the board.

This is simple and flexible, but it makes important controls implicit:

- “plan, then implement, then test, then review” is a prompt convention rather than an executable contract;
- a model can skip or combine stages;
- deterministic commands and AI work are not first-class steps in one run graph;
- current run records are attempt/lease oriented rather than a node-by-node execution ledger;
- recovery uses ticket state as the coarse checkpoint rather than an exact completed-node checkpoint;
- human review is represented by board state and prompt behavior rather than a durable, explicitly resolved gate;
- output from one stage is conventionally written to files, but dependency and artifact provenance are not enforced by the runtime;
- operators can see a live ticket attempt, but not a complete graph/timeline of inputs, node attempts, outputs, retries, approvals, and resume decisions.

### 2.3 What Archon demonstrates

The current Archon rewrite treats AI development as a repo-owned YAML workflow. Its useful concepts include:

- DAG dependencies and parallel branches;
- deterministic shell/script nodes mixed with AI nodes;
- conditional execution and trigger rules;
- explicit retry policy and error classification;
- artifact passing and typed output;
- human approval nodes;
- explicit resume that skips completed nodes;
- reusable workflow blocks and governed child workflows;
- per-node provider/session/tool configuration;
- execution history and a workflow progress UI.

The recommended work is to adopt the **principles**, not clone Archon’s entire architecture or feature surface.

---

## 3. Product goals

### G1. Deterministic development contracts

A repository can require a known sequence such as:

`investigate → plan → approval → implement → test → parallel reviews → final approval → PR`

The model supplies judgment and code; the engine controls ordering, gates, retry boundaries, and completion.

### G2. Preserve backend freedom

Any AI node can inherit the ticket-selected backend or explicitly use a different supported Symphony backend. A workflow may plan with one agent, implement with another, and review with a third.

### G3. Exact, durable execution state

Operators can determine:

- which workflow definition was used;
- which nodes ran, in what order, with which backend and attempt number;
- which outputs and repository changes each node produced;
- why a node failed or retried;
- what is safe to resume;
- who approved or rejected a gate.

### G4. Crash-safe, explicit recovery

After a hard process failure, Symphony must not silently start the ticket again from the beginning. It must preserve the run fence, classify the interrupted node, and require an explicit `resume`, `abandon`, or `start fresh` action.

### G5. Backward compatibility

A repository without workflow-engine configuration behaves exactly as it does today. Existing tickets, stage prompts, boards, hooks, backend adapters, and commands continue to work.

### G6. Consistent operator experience

The same run and gate state must be visible and actionable from:

- CLI;
- TUI;
- web admin UI;
- API.

### G7. Bounded, safe parallelism

Independent nodes may run in parallel only when their read-only access can be enforced by the selected backend or process sandbox. Otherwise they degrade to exclusive execution. Workspace-mutating nodes are serialized within a ticket, and global ticket concurrency remains controlled by Symphony’s existing scheduler.

---

## 4. Non-goals

The first implementation will not:

1. replace Symphony’s ticket scheduler, tracker adapters, worktree manager, or `AgentBackend` protocol;
2. run Archon as a subprocess or import Archon as a runtime dependency;
3. introduce a cloud control plane, multi-tenant SaaS, or remote SSH workers;
4. replace Markdown tickets or make SQLite the human-editable issue source of truth;
5. create one worktree per node;
6. permit concurrent write-capable nodes in the same ticket workspace;
7. treat a natural-language chat message, ticket movement, or comment as approval;
8. add the archived Archon RAG/knowledge-management product;
9. ship a drag-and-drop visual workflow builder in the first release;
10. support arbitrary cross-repository child workflows in the first release;
11. duplicate a separate collaboration system’s chat, shared-context, or team-coordination authority;
12. change current auto-merge or branch-protection policy without an explicit workflow node and existing policy checks.

---

## 5. Users and primary use cases

### 5.1 Solo developer

Runs several unattended tickets overnight but requires deterministic tests and review before a PR is opened.

### 5.2 Technical lead

Commits team workflows to the repository so every agent follows the same planning, implementation, validation, and review process.

### 5.3 Operator

Uses the TUI or web app to see why a ticket is waiting, inspect evidence, approve a gate, resume an interrupted run, or abandon a bad run.

### 5.4 Maintainer comparing agents

Uses different backends at different nodes or runs the same read-only review branches with multiple agents, while retaining a single audit trail.

### 5.5 Team using external collaboration tooling

Receives workflow events and supplies approved context snapshots through a narrow adapter, while Symphony remains execution authority. Collaboration messages may inform the next safe node boundary but cannot directly schedule nodes or resolve gates without an explicit authenticated approval action.

---

## 6. Product principles and binding constraints

### 6.1 One authority per concern

| Concern | Authority |
|---|---|
| Ticket eligibility and dispatch | Symphony orchestrator |
| Worktree, branch, process, concurrency, and budget lifecycle | Symphony orchestrator |
| Node order, dependency readiness, retries, and gate state | Symphony workflow executor inside the run |
| Human-readable issue content and coarse board state | Tracker, including Markdown for the file tracker |
| Durable runtime execution state | `.symphony/state.db` |
| AI process implementation | Existing `AgentBackend` adapters |
| Optional shared/team context | External provider through a narrow interface; never the scheduler |

### 6.2 One ticket, one workspace

All nodes for a ticket use the same Symphony-managed ticket workspace—normally the existing isolated Git worktree, or the workspace produced by the configured hook. This preserves the current branch/workspace model and avoids nested lifecycle conflicts.

### 6.3 Coarse ticket state, fine workflow state

Kanban columns remain a human-scale lifecycle. Nodes do not become columns. The card displays the active node and progress within its current column.

### 6.4 Explicit gates

Only a dedicated approval command or API mutation containing the run and approval identifiers can resolve a gate. Ordinary chat, issue comments, ticket edits, and state moves are untrusted context.

### 6.5 Definition snapshotting

A run executes an immutable normalized snapshot and hash of the workflow definition selected at dispatch. Editing the YAML affects future runs only.

### 6.6 Deterministic operations where possible

Tests, formatting, type checks, Git inspection, artifact copying, and policy checks should be deterministic nodes. AI should be used for investigation, planning, implementation, synthesis, and review judgment.

### 6.7 Fail closed

Invalid workflow definitions, missing backends, unsupported capabilities, unresolved variables, cycles, unsafe paths, and ambiguous resume states block dispatch. Symphony must not silently fall back to another workflow or legacy mode after a DAG run has started.

---

## 7. Proposed user experience

### 7.1 Repository layout

```text
WORKFLOW.md                         # existing service/tracker/backend configuration
.symphony/
  workflows/
    ticket-default.yaml
    quick-fix.yaml
    review-only.yaml
  artifacts/                       # runtime artifacts, gitignored by default
    <run-id>/
      <node-id>/
        output.txt
        events.jsonl
        ...
kanban/
  TASK-123.md                       # existing file-tracker ticket
```

The existing committed artifact convention under `docs/<TICKET-ID>/<stage>/` remains available for artifacts intended to become repository content. Runtime logs and internal outputs go under `.symphony/artifacts/` and are not committed unless an explicit node copies selected content into the worktree.

### 7.2 `WORKFLOW.md` opt-in

```yaml
workflow_engine:
  enabled: true
  directory: ./.symphony/workflows
  default: ticket-default
  max_parallel_nodes: 2
  require_explicit_resume: true
  artifact_retention_days: 30

  ticket_state_mapping:
    running: "In Progress"
    waiting_approval: "Human Review"
    succeeded: Done
    rejected: Blocked
    abandoned: Blocked
```

If `workflow_engine` is absent or `enabled: false`, Symphony uses current stage-loop behavior.

### 7.3 Per-ticket selection

File ticket frontmatter may override the default:

```yaml
---
id: TASK-123
state: Todo
agent:
  kind: codex
workflow: quick-fix
---
```

Selection precedence:

1. explicit ticket workflow;
2. deterministic configured route, when that feature is enabled later;
3. `workflow_engine.default`;
4. legacy stage-loop mode only when the workflow engine itself is disabled.

An unknown or invalid explicitly selected workflow blocks the ticket and surfaces a preflight error. It never silently selects another workflow.

### 7.4 Board card

A running card adds compact workflow information:

```text
TASK-123  ●  [4/8]
Fix pagination race
node=test  agent=codex
18,430 tokens
```

A gate displays:

```text
TASK-123  ◇  [7/8]
Awaiting release approval
```

### 7.5 Run detail

The web and TUI detail views show:

- workflow name and definition hash;
- run status and current node;
- ordered node graph/list;
- node attempts, duration, backend, token use, and error class;
- output preview and artifact links;
- before/after Git revision and diff summary;
- retry, cancellation, interruption, and approval events;
- explicit actions allowed in the current state.

---

## 8. Workflow definition

### 8.1 Version 1 schema

The first schema should be explicit and typed rather than using mutually exclusive magic keys.

```yaml
version: 1
name: ticket-default
description: Plan, implement, validate, review, and prepare a PR.

defaults:
  backend: inherit
  context: fresh
  timeout_seconds: 1800
  max_parallel_nodes: 2

nodes:
  - id: plan
    type: agent
    workspace_access: read
    prompt_file: docs/symphony-prompts/workflows/plan.md
    output_type: plan

  - id: approve-plan
    type: approval
    depends_on: [plan]
    title: Approve implementation plan
    evidence: [plan]

  - id: implement
    type: agent
    depends_on: [approve-plan]
    workspace_access: write
    prompt: |
      Implement the approved plan for ${ticket.identifier}.
      Approved plan: ${nodes.plan.output}

  - id: test
    type: shell
    depends_on: [implement]
    workspace_access: write
    run: python -m pytest -q
    timeout_seconds: 1800

  - id: correctness-review
    type: agent
    depends_on: [test]
    backend: claude
    workspace_access: read
    prompt_file: docs/symphony-prompts/workflows/review-correctness.md
    output_type: review

  - id: maintainability-review
    type: agent
    depends_on: [test]
    backend: codex
    workspace_access: read
    prompt_file: docs/symphony-prompts/workflows/review-maintainability.md
    output_type: review

  - id: synthesize
    type: agent
    depends_on: [correctness-review, maintainability-review]
    workspace_access: read
    prompt: |
      Synthesize the two reviews. Report blocking findings only.
      Correctness: ${nodes.correctness-review.output}
      Maintainability: ${nodes.maintainability-review.output}
    output_type: review-summary

  - id: release-approval
    type: approval
    depends_on: [synthesize]
    title: Approve PR preparation
    evidence: [test, synthesize]

  - id: finalize
    type: agent
    depends_on: [release-approval]
    workspace_access: write
    external_side_effects: true
    prompt_file: docs/symphony-prompts/workflows/finalize-pr.md
```

### 8.2 Common fields

| Field | Required | Description |
|---|---:|---|
| `id` | Yes | Unique stable identifier matching `^[a-z][a-z0-9-]{0,62}$`. |
| `type` | Yes | `agent`, `shell`, or `approval` in v1. |
| `depends_on` | No | Node IDs that must succeed before this node is ready. Empty means root node. |
| `workspace_access` | For executable nodes | `read`, `write`, or `none`; used by the scheduler’s per-workspace read/write lock. `read` permits parallel execution only when enforcement is available. |
| `timeout_seconds` | No | Node timeout, bounded by global limits. |
| `retry` | No | Explicit retry configuration. |
| `output_type` | No | Stable semantic artifact type such as `plan`, `test-report`, or `review`. |
| `backend` | Agent only | `inherit` or a supported Symphony backend. |
| `context` | Agent only | `fresh` by default; `continue` only when backend capability validation passes. |
| `external_side_effects` | No | Declares writes outside the worktree, such as PR creation or deployment. |

### 8.3 Agent node

An agent node supports exactly one of `prompt` or `prompt_file`.

It reuses the selected `AgentBackend`, normalized event stream, usage collection, rate-limit handling, cancellation behavior, skill injection, and ticket budget enforcement.

Backend precedence:

1. node `backend` when not `inherit`;
2. ticket backend override;
3. current auto-triage result;
4. service default backend.

A node-level override does not mutate the ticket’s default backend.

### 8.4 Shell node

A shell node executes in the ticket workspace through the existing subprocess control boundary.

Requirements:

- no AI is involved;
- stdout and stderr are captured separately;
- preview is capped at 32 KiB each; full output is written to the artifact store subject to a configurable size cap;
- default timeout is 120 seconds unless overridden;
- default attempts: one;
- retry is allowed only when explicitly configured;
- cancellation terminates the process group;
- ticket data and prior outputs are passed through environment variables or files, not interpolated unescaped into the shell command;
- working-directory and artifact paths must remain inside approved roots.

### 8.5 Approval node

An approval node has no backend process and no workspace access. It atomically:

1. completes all prerequisite evidence;
2. creates a pending approval record;
3. marks the node `waiting_approval`;
4. marks the run `waiting_approval`;
5. retains a durable ticket-run fence;
6. releases any worker process slot;
7. exposes approve/reject actions through CLI, TUI, web, and API.

V1 decisions are `approve` or `reject`. “Request changes and loop back” is deferred until controlled loop semantics are implemented.

### 8.6 Variables

V1 supports read-only substitutions in agent prompts:

- `${ticket.id}`
- `${ticket.identifier}`
- `${ticket.title}`
- `${ticket.description}`
- `${ticket.labels}`
- `${run.id}`
- `${run.workspace}`
- `${nodes.<node-id>.output}`
- `${nodes.<node-id>.artifact_dir}`

Rules:

- references must point to a transitive dependency;
- unknown variables are validation errors;
- output preview substitution is size bounded;
- large output is referenced by artifact path, not injected into full prompt context;
- ticket content is delimited and identified as untrusted input in generated prompts;
- shell command text does not support raw ticket/output substitution.

### 8.7 Validation and compilation

Before dispatch, Symphony compiles YAML into a normalized immutable execution plan.

Validation must reject:

- invalid schema version;
- duplicate IDs;
- cycles or self-dependencies;
- missing dependencies;
- unreachable nodes when not explicitly allowed;
- unknown fields in v1;
- unsupported node/backend combinations;
- missing prompt files;
- references to non-ancestor output;
- unsafe artifact or working-directory paths;
- `context: continue` on a backend that cannot resume sessions;
- external-side-effect nodes that violate configured policy;
- more than 100 nodes, more than 20 dependencies per node, or YAML larger than 1 MiB;
- effective parallelism above the configured service maximum.

Compilation produces:

- normalized JSON;
- SHA-256 definition hash;
- topological layers;
- transitive dependency map;
- variable/reference map;
- required backend capability set;
- risk summary;
- source file and line diagnostics.

The hash is stored on the run, and the normalized definition is stored once in the content-addressed `workflow_snapshots` table.

---

## 9. Execution semantics

### 9.1 Run lifecycle

```text
created
  └─> running
        ├─> waiting_approval ──explicit approve──> running
        │                    └─explicit reject───> rejected
        ├─> needs_attention ──explicit resume────> running
        │    reason: node_failed | interrupted | integrity_failed
        │              ├──────explicit abandon───> abandoned
        │              └──────start fresh────────> abandoned + new run
        ├─> cancelled
        └─> succeeded
```

`cancelled`, `rejected`, `abandoned`, and `succeeded` are terminal. `needs_attention` is nonterminal but inactive and fenced; ordinary ticket polling never resumes it.

### 9.2 Node lifecycle

```text
pending → ready → running → succeeded
                    ├──────> failed
                    ├──────> interrupted
                    └──────> cancelled

pending/ready → skipped       # later condition support
approval: pending → waiting_approval → succeeded | rejected
```

Every transition and its corresponding event append occur in one SQLite transaction.

### 9.3 Read/write scheduling

Within one ticket workspace:

- one `write` node may run at a time;
- no `read` node may run while a `write` node is active;
- multiple dependency-ready `read` nodes may run concurrently up to `max_parallel_nodes` only when every participating backend/process has enforceable read-only workspace access;
- `none` nodes, such as approval, do not acquire the workspace lock;
- when read-only access cannot be enforced, the executor safely degrades that node to the exclusive `write` lock and records a capability warning; a declaration alone is never considered sufficient isolation;
- default for agent and shell nodes is `write` if the field is omitted during an early compatibility period; strict mode later requires it explicitly.

Global ticket concurrency continues to use the existing Symphony setting. Node concurrency does not create an independent global scheduler.

### 9.4 Completion rule

A run succeeds when every required terminal node succeeds and no required node failed or was rejected.

V1 uses “all dependencies must succeed.” Conditional branches and alternative trigger rules are Phase 3 features.

### 9.5 Error classification and retries

Errors are classified as:

| Class | Examples | Default action |
|---|---|---|
| `fatal` | authentication failure, permission denial, invalid config, exhausted budget | fail immediately; never automatic retry |
| `transient` | rate limit, backend subprocess crash, temporary network timeout | agent node may retry |
| `unknown` | unclassified failure | fail unless retry explicitly allows it |
| `validation` | invalid structured output, missing artifact, contract violation | fail; optional explicit retry later |
| `cancelled` | operator cancellation | no retry |

Defaults:

- agent nodes: two retries after the first attempt, exponential backoff from three seconds, only for `transient` errors;
- shell nodes: no retry unless `retry` is declared;
- external-side-effect nodes: no automatic retry unless an idempotency strategy is declared;
- budget exhaustion is fatal and must not be hidden as a transient provider failure.

The runtime must avoid double-counting backend-internal reconnect attempts as node attempts. Adapter-level recovery is internal; a new node attempt begins only after the adapter returns a terminal failure.

Example:

```yaml
retry:
  max_attempts: 3       # total attempts, including the first
  backoff_seconds: 3
  on: [transient]
```

### 9.6 External side effects

A node declaring `external_side_effects: true` may create a PR, post a comment, deploy, or mutate another service.

Policy:

- preflight highlights these nodes;
- auto-retry is disabled by default;
- the external operation idempotency key is stable across retries: `${run.id}:${node.id}`; the attempt ID is recorded separately for diagnostics;
- integrations should query for an existing result before creating a duplicate;
- repositories may require an approval ancestor for every external-side-effect node;
- merges remain subject to existing Symphony branch and merge policy.

---

## 10. Durable resume and crash recovery

### 10.1 Required behavior

A resumed run must:

- use the stored workflow snapshot, not the current YAML file;
- skip succeeded nodes whose required artifacts still exist and match stored hashes;
- preserve prior node outputs and approval decisions;
- mark the node that was `running` during a crash as `interrupted`;
- rerun only the interrupted/failed node and downstream incomplete nodes;
- never rerun a completed external-side-effect node unless the operator explicitly resets it;
- revalidate backend availability and workspace integrity before continuing;
- produce a new node attempt row rather than overwriting history.

### 10.2 Ticket-run fence

The current lease protects active dispatch, but workflow gates and interrupted runs must continue blocking redispatch when no backend process is alive.

Add a durable `run_fences` record keyed by issue ID:

- created atomically with a DAG run;
- retained while the run is running, waiting for approval, or in `needs_attention`;
- removed only after terminalization or explicit abandonment;
- mirrored into the existing `issue_flags.paused` state while waiting or in `needs_attention` so rollback to an older Symphony binary remains fail-safe; the mirror uses a run-owned reason prefix and is cleared only when that same run resolves or terminates;
- checked before normal ticket dispatch.

This separates “this process owns a live worker lease” from “this issue already has a nonterminal governed run.”

### 10.3 Startup reconciliation

On service startup:

1. reclaim dead process leases using the existing owner PID/boot-ID mechanism;
2. find nonterminal governed runs;
3. convert stale `running` node attempts to `interrupted` and set the run to `needs_attention`;
4. verify the ticket worktree and branch still exist;
5. verify succeeded-node artifact hashes;
6. restore `waiting_approval` runs without starting a worker;
7. restore other nonterminal failures as `needs_attention` with a precise reason;
8. do not auto-dispatch their tickets.

### 10.4 Explicit actions

- **Resume:** continue the same run from its stored snapshot.
- **Abandon:** terminalize the run, preserve history/artifacts, and release its fence.
- **Start fresh:** abandon the existing run and create a new run from the current workflow definition.
- **Reset node:** advanced, destructive operator action; deferred from the first release.

A definition edit never mutates a run in place. Ticket title, description, label, or external-context edits also do not enter a running node implicitly; governed runs use their dispatch snapshot unless a future explicit, provenance-recorded context refresh is performed at a safe node boundary.

---

## 11. Human approval requirements

### 11.1 Approval record

Each gate stores:

- approval ID;
- run ID and node ID;
- node attempt;
- title and instructions;
- referenced evidence/artifacts;
- status and optimistic-lock version;
- requested timestamp;
- resolution timestamp;
- decision;
- actor identifier when available;
- source (`cli`, `tui`, `web`, or authenticated external adapter);
- optional comment;
- immutable resolution event.

### 11.2 Resolution safety

- resolution requires the approval ID and expected version;
- repeated identical requests return the existing result;
- conflicting second decisions return HTTP `409` / CLI error;
- only a pending gate can be resolved;
- approval does not imply merge approval unless the workflow explicitly defines that policy;
- moving a ticket to or from `Human Review` does not resolve the gate;
- a chat message containing “approved” does not resolve the gate;
- an external collaboration tool can resolve a gate only through a narrow authenticated `ApprovalResolver` interface.

### 11.3 Evidence view

The approval screen must show, at minimum:

- workflow and run ID;
- gate title;
- plan or review output referenced by the node;
- latest deterministic validation results;
- Git diff summary and changed files;
- external side effects that will become eligible after approval;
- approve and reject actions with confirmation.

---

## 12. Artifacts and provenance

### 12.1 Two artifact scopes

| Scope | Location | Default Git behavior | Purpose |
|---|---|---|---|
| Runtime | `.symphony/artifacts/<run>/<node>/` | gitignored | logs, full output, normalized data, internal evidence |
| Repository | existing `docs/<ticket>/<stage>/` or another explicit worktree path | ordinary Git change | plans, reports, specs, or documentation intended to be committed |

### 12.2 Artifact metadata

Every indexed artifact stores:

- artifact ID;
- run and producer node;
- semantic type;
- scope;
- relative path;
- media type;
- byte size;
- SHA-256;
- creation timestamp;
- optional schema/version;
- provenance links to input artifacts.

### 12.3 Atomicity

Files are written to a temporary path and atomically renamed before metadata is committed. Startup reconciliation removes stale temporary files and detects metadata pointing to missing or modified content.

### 12.4 Output handling

- a small, redacted preview is stored in SQLite for fast UI display;
- full output remains in the artifact store;
- downstream prompts receive bounded output or an artifact reference;
- secrets and credential-like values are redacted before event/preview persistence;
- raw provider transcripts are off by default and require explicit local configuration.

### 12.5 Retention and garbage collection

- never delete artifacts belonging to a nonterminal or fenced run;
- never delete evidence referenced by a pending approval;
- terminal-run cleanup follows `artifact_retention_days`;
- metadata is retained after payload cleanup and records `payload_expired_at`;
- a run cannot be resumed when a required payload has expired or failed integrity verification;
- garbage collection is idempotent and uses the artifact ID/path confinement rules.

### 12.6 Git provenance

For executable nodes, record:

- `HEAD` before and after;
- changed paths;
- diffstat;
- whether the node created commits;
- current branch;
- dirty-worktree state.

The first release does not require one commit per node, but the history must identify which node window introduced repository changes.

---

## 13. Ticket and tracker integration

### 13.1 Ownership change in governed mode

Current file-tracker mode intentionally lets the agent write ticket state. In governed mode, that is too nondeterministic.

For governed runs:

- the orchestrator owns configured coarse state transitions through the tracker adapter;
- prompts instruct agents not to edit ticket state;
- the file tracker performs locked, atomic Markdown frontmatter mutation;
- node progress remains in SQLite and the live overlay, not in frontmatter;
- terminal summaries are written idempotently by Symphony, not free-form by the agent.

Legacy mode retains current agent-owned transitions.

### 13.2 State mapping

Recommended mapping:

| Runtime condition | File-ticket state |
|---|---|
| run starts | `In Progress` |
| approval pending | `Human Review` |
| run succeeds | `Done` |
| run rejects or is explicitly abandoned | `Blocked` |
| run needs attention | keep current state; add attention/pause overlay |

Repositories may configure names. A missing target state is a preflight error when automatic state mutation is enabled.

### 13.3 Linear and Jira

MVP behavior:

- run/node state appears through Symphony’s live overlay regardless of external tracker state;
- workflow selection may use the service default;
- no new direct tracker mutation is assumed unless the existing adapter exposes a verified, idempotent transition capability;
- file-tracker parity ships first;
- external tracker transitions and a `symphony/workflow:<name>` label convention are a follow-up slice.

### 13.4 Terminal summary

For file tickets, Symphony writes or updates a bounded section using stable markers:

```markdown
<!-- symphony-run:RUN_ID:start -->
## Symphony Run

- Workflow: `ticket-default`
- Run: `RUN_ID`
- Result: succeeded
- Validation: passed
- Artifacts: `.symphony/artifacts/RUN_ID/`
- Branch: `symphony/TASK-123`
<!-- symphony-run:RUN_ID:end -->
```

Reconciliation can safely repeat this write without duplicating content. Terminalization order is: atomically write the file-ticket state/summary, then commit the run terminal state and release its fence. If the process dies between those operations, the retained fence prevents redispatch and startup reconciliation completes the database transition.

### 13.5 Mid-run ticket drift

- The run uses the bounded ticket snapshot captured at dispatch.
- A title, description, or label edit is displayed as drift but is not silently injected into an active agent session.
- A direct manual move to a conflicting terminal state places the run in `needs_attention` with reason `ticket_state_conflict`; it does not count as approval or successful completion.
- A deliberate cancellation performed through Symphony uses the normal cancel action and audit event.
- Future context refresh must occur only at a node boundary, store provenance, and invalidate any downstream input hashes affected by the refresh.

---

## 14. Persistence model

All changes are additive to `.symphony/state.db` and must use transactional, versioned migrations.

### 14.1 Existing `runs` table additions

| Column | Purpose |
|---|---|
| `execution_mode` | `legacy_stage_loop` or `governed_workflow` |
| `execution_status` | `created`, `running`, `waiting_approval`, `needs_attention`, or a terminal status; separate from current lease/status field |
| `attention_reason` | `node_failed`, `interrupted`, `integrity_failed`, `ticket_state_conflict`, or another stable reason while attention is required |
| `workflow_name` | selected workflow |
| `workflow_version` | schema version |
| `workflow_hash` | normalized definition hash |
| `ticket_snapshot_json` | bounded dispatch-time ticket input |
| `terminal_reason` | stable machine-readable outcome |
| `input_tokens`, `output_tokens`, `cost_usd` | run aggregate when available |

Keep the current `status` column’s lease/history semantics compatible. Do not overload it with approval or DAG state.

### 14.2 `workflow_snapshots`

```text
workflow_hash PRIMARY KEY
workflow_name
schema_version
normalized_json
source_path
created_at
```

A run references an immutable, content-addressed snapshot by hash. Identical definitions are stored once rather than copying up to 1 MiB of JSON into every run row.

### 14.3 `run_fences`

```text
issue_id PRIMARY KEY
run_id UNIQUE
reason
created_at
updated_at
```

`reason` is one of `running`, `waiting_approval`, or `needs_attention`. The fence and its compatibility pause mirror are updated in the same logical transition.

### 14.4 `node_runs`

```text
node_run_id PRIMARY KEY
run_id
node_id
attempt
node_type
status
backend_kind
workspace_access
started_at
updated_at
completed_at
error_class
error_code
error_message_redacted
output_preview
output_sha256
session_id
input_tokens
output_tokens
cost_usd
head_before
head_after
diffstat_json
external_operation_key
UNIQUE(run_id, node_id, attempt)
```

### 14.5 `run_events`

```text
run_id
seq
node_id NULLABLE
type
created_at
payload_json_redacted
PRIMARY KEY(run_id, seq)
```

Sequence allocation and the state transition that emits the event occur in the same transaction.

### 14.6 `artifacts`

```text
artifact_id PRIMARY KEY
run_id
node_id
artifact_type
scope
relative_path
media_type
size_bytes
sha256
created_at
metadata_json
```

### 14.7 `approvals`

```text
approval_id PRIMARY KEY
run_id
node_id
node_attempt
status
version
title
instructions
requested_at
resolved_at
decision
actor
source
comment
UNIQUE(run_id, node_id, node_attempt)
```

### 14.8 Migration policy

- maintain a schema-version table;
- run each migration under `BEGIN IMMEDIATE`;
- create a timestamped backup of `state.db` before the first workflow-engine migration;
- retain compatibility with old legacy rows through defaults;
- never rewrite or delete historical run rows during migration;
- test upgrade from representative older schemas already handled by `run_registry.py`;
- older binaries may ignore additive tables, but operational rollback requires all governed runs to be terminal or explicitly paused because older code cannot interpret the new run graph.

---

## 15. Backend capability model

Add explicit capability metadata to `AgentBackend` rather than inferring behavior from backend name.

```python
@dataclass(frozen=True)
class BackendCapabilities:
    session_resume: bool
    process_cancel: bool
    streaming_usage: bool
    structured_output: bool
    node_skills: bool
    tool_policy: bool
    enforce_read_only_workspace: bool
```

Requirements:

- preflight validates required capabilities;
- unsupported optional fields fail with a precise path and backend name;
- `context: fresh` works for every backend;
- a node declared `workspace_access: read` runs in parallel only when `enforce_read_only_workspace` is true; otherwise it is scheduled exclusively;
- `context: continue` requires `session_resume` and stores the session identifier per node;
- a resumed node may reuse a session only when the same backend and explicit context policy are preserved;
- switching backends always starts fresh context;
- normalized events remain the sole contract consumed by the workflow executor.

Provider-specific options are deferred until a portable base is stable. They can later live under a namespaced `backend_options` field.

---

## 16. API requirements

Keep existing endpoints compatible. Add:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/v1/workflows` | list discovered workflows and validation state |
| `GET` | `/api/v1/workflows/{name}` | normalized definition and source diagnostics |
| `POST` | `/api/v1/workflows/validate` | validate supplied YAML without saving |
| `GET` | `/api/v1/runs/{run_id}` | full run, graph, nodes, usage, and actions |
| `GET` | `/api/v1/runs/{run_id}/events?after_seq=N` | ordered incremental events |
| `GET` | `/api/v1/runs/{run_id}/artifacts` | artifact metadata |
| `GET` | `/api/v1/artifacts/{artifact_id}` | stream local artifact with path confinement |
| `POST` | `/api/v1/runs/{run_id}/resume` | explicit resume |
| `POST` | `/api/v1/runs/{run_id}/abandon` | abandon and release fence |
| `POST` | `/api/v1/runs/{run_id}/cancel` | cancel an active run |
| `GET` | `/api/v1/approvals?status=pending` | list gates |
| `POST` | `/api/v1/approvals/{approval_id}/resolve` | approve or reject with expected version |

Mutation responses return the updated authoritative state. Errors use stable codes such as:

- `workflow_invalid`;
- `workflow_not_found`;
- `run_not_resumable`;
- `run_fenced`;
- `approval_already_resolved`;
- `approval_version_conflict`;
- `backend_capability_missing`;
- `artifact_not_found`;
- `workspace_integrity_failed`.

Real-time delivery may begin with event polling using `after_seq`; a run-event WebSocket/SSE stream may follow. Do not overload the existing operator-chat WebSocket with workflow state mutations.

---

## 17. CLI requirements

Preserve `symphony runs WORKFLOW.md --limit N`. Add focused commands:

```bash
symphony workflow list ./WORKFLOW.md
symphony workflow show ticket-default ./WORKFLOW.md
symphony workflow validate .symphony/workflows/ticket-default.yaml

symphony run show RUN_ID ./WORKFLOW.md
symphony run events RUN_ID ./WORKFLOW.md
symphony run resume RUN_ID ./WORKFLOW.md
symphony run abandon RUN_ID ./WORKFLOW.md
symphony run cancel RUN_ID ./WORKFLOW.md

symphony approval list ./WORKFLOW.md
symphony approval resolve APPROVAL_ID --approve --version 1 ./WORKFLOW.md
symphony approval resolve APPROVAL_ID --reject --comment "Missing migration test" --version 1 ./WORKFLOW.md
```

Requirements:

- human-readable output by default;
- `--json` for automation;
- nonzero exit codes for invalid workflow, conflict, rejected gate, or failed action;
- no approval from free-form chat text;
- show the exact stored workflow hash on resume;
- require confirmation for abandon unless `--yes` is supplied.

---

## 18. Web UI requirements

### 18.1 MVP

- board cards show workflow, active node, `completed/total`, gate/attention badge, and aggregate usage;
- clicking a run opens an execution panel with a topological list rather than a visual editor;
- node rows expand to show attempts, output preview, artifacts, errors, usage, and Git summary;
- approval panel presents evidence and dedicated approve/reject buttons;
- `needs_attention` runs expose resume, abandon, and start-fresh choices with the failure/interruption reason;
- workflow settings page lists files, validation state, and read-only normalized preview;
- existing YAML/prompt editor can open a workflow file and run validation before save;
- invalid workflow edits are saved only as drafts or rejected with source diagnostics; they do not affect an existing run snapshot.

### 18.2 Later

- graphical DAG visualization;
- drag-and-drop workflow builder;
- structured node forms;
- templates and reusable blocks;
- workflow-diff view before activation;
- live event WebSocket/SSE.

The run-execution view is higher priority than the visual builder.

---

## 19. TUI requirements

- card shows node progress and attention state without increasing card height excessively;
- detail modal adds workflow name/hash, current node, node timeline, and pending gate evidence;
- a dedicated gate action opens a confirmation modal; it must not reuse archive or generic state-move behavior;
- resume and abandon are explicit actions with confirmation;
- interrupted/node-failed attention, waiting-approval, and budget-exhausted states are visually distinct in text and symbol, not color alone;
- event/output previews are bounded to protect terminal responsiveness;
- existing keyboard-first ticket actions continue to work in legacy mode.

Exact key bindings should be chosen after checking current bindings; no existing binding may change silently.

---

## 20. Budgets and usage

Reuse current ticket-level limits and add optional node limits:

```yaml
budget:
  max_turns: 8
  max_tokens: 120000
```

Rules:

- node usage contributes to the ticket/run aggregate;
- run budget is authoritative even when node budgets are higher;
- all provider-reported usage is normalized but may be incomplete for backends that do not expose it;
- missing cost data is `null`, not zero;
- budget exhaustion is a terminal policy event unless the operator explicitly starts a new run with changed configuration;
- retries count toward all budgets;
- usage is shown per attempt, node, backend, and run.

No external telemetry is required. Metrics remain local unless a user explicitly configures export later.

---

## 21. Security and safety

### 21.1 Workflow files are executable code

Repository workflows can run agents and shell commands. Treat workflow changes like CI configuration changes:

- load from the checked-out trusted repository;
- display external-side-effect and shell nodes during review;
- allow repository policy to require code-owner review for `.symphony/workflows/`;
- never download and execute a remote workflow by name in v1.

### 21.2 Prompt injection containment

- delimit ticket descriptions and external context as untrusted data;
- never let untrusted ticket text alter node dependencies or runtime policy;
- do not interpolate ticket text directly into shell commands;
- approval evidence clearly distinguishes generated claims from deterministic test results;
- node tool restrictions may be added only through verified backend capabilities.

### 21.3 Artifact and path safety

- normalize and confine all paths;
- reject `..`, absolute paths outside approved roots, and symlink escapes;
- set output and total-artifact size limits;
- sanitize filenames derived from ticket/node values;
- redact common token, key, password, cookie, and authorization-header patterns before persistence;
- never expose arbitrary filesystem reads through the artifact endpoint.

### 21.4 Web mutations

- default binding remains loopback;
- verify `Origin` for browser state-changing requests and WebSockets;
- use CSRF protection or bearer authentication when exposed beyond loopback;
- approval resolution uses optimistic locking and an audit event;
- artifact downloads use a generated ID, not a user-supplied path.

### 21.5 Process safety

- cancel process groups, not only parent PIDs;
- retain current dead-owner cleanup and lease fencing;
- place time and output limits on every node;
- never automatically retry an unknown external mutation;
- preserve the host working tree through existing worktree isolation.

---

## 22. Architecture and code changes

### 22.1 Keep `orchestrator/core.py` as coordinator, not DAG implementation

Introduce a narrow execution strategy seam:

```python
class TicketExecutor(Protocol):
    async def execute(self, context: TicketRunContext) -> TicketRunResult: ...

class LegacyStageExecutor(TicketExecutor): ...
class GovernedWorkflowExecutor(TicketExecutor): ...
```

The orchestrator continues to:

- poll trackers;
- determine ticket eligibility;
- acquire dispatch ownership;
- create/clean workspaces;
- choose execution mode;
- enforce global concurrency;
- publish live snapshots;
- apply terminal policy.

The governed executor owns only the nodes within that ticket run.

### 22.2 Proposed package

```text
src/symphony/flow/
  __init__.py
  model.py          # immutable workflow/node/run types
  loader.py         # recursive repo workflow discovery
  schema.py         # YAML decoding and field validation
  compiler.py       # DAG, references, capabilities, definition hash
  executor.py       # node readiness and lifecycle
  scheduler.py      # bounded per-workspace read/write scheduling
  retries.py        # classification and backoff policy
  events.py         # append-only event model
  artifacts.py      # safe storage and provenance
  approvals.py      # durable gate transitions
  recovery.py       # startup reconciliation and resume plan
  prompts.py        # bounded variable rendering and trust delimiters
```

Keep existing `src/symphony/workflow/` for `WORKFLOW.md` service configuration to avoid mixing two meanings.

### 22.3 Existing files affected

| Area | Expected change |
|---|---|
| `workflow/config.py`, `builder.py`, `preflight.py` | add `workflow_engine` config and validation |
| `issue.py` and tracker mapping | optional workflow selection metadata |
| `orchestrator/core.py` | select executor and expose run actions; avoid embedding node logic |
| `orchestrator/run_registry.py` | versioned additive migrations, fences, nodes, events, artifacts, approvals |
| `backends/__init__.py` | capability metadata and normalized cancellation/session contract |
| `trackers/file.py` | governed state transitions and idempotent terminal summary |
| `webapi.py` | route registration only; split new flow routes into a separate module if needed |
| CLI modules | workflow/run/approval commands |
| `tui.py` or TUI package | progress, details, gate, resume, abandon |
| web static app | execution panel and actions |
| `doctor` | validate workflow directory, backends, artifact path, and database migration |
| stats/progress mirror | workflow/node/gate summaries |

No unrelated backend or tracker refactoring is required.

### 22.4 Extension boundaries

Optional integrations must use narrow interfaces:

```python
class WorkflowEventSink(Protocol):
    async def publish(self, event: WorkflowEvent) -> None: ...

class ContextProvider(Protocol):
    async def snapshot_for_node(self, run_id: str, node_id: str) -> ContextSnapshot: ...

class ApprovalResolver(Protocol):
    async def verify_resolution(self, request: ApprovalResolution) -> VerifiedActor: ...
```

Event sink failures cannot block the local state transition. Context is consumed only at safe node boundaries, is stored with provenance, and cannot rewrite the workflow. Approval resolution remains an explicit authenticated mutation.

---

## 23. Delivery plan

### Phase 0 — Architecture seam and durable event ledger

**Goal:** improve observability without changing execution behavior.

Deliver:

- schema migration framework;
- additive run metadata and append-only events;
- run detail API/CLI;
- `TicketExecutor` seam with existing behavior moved behind `LegacyStageExecutor` only as necessary;
- local artifact helper and redaction;
- no DAG dispatch yet.

Exit criteria:

- all current tests pass unchanged;
- current CLI/TUI/web behavior is preserved;
- every legacy run emits start/progress/terminal events;
- old database upgrades are covered.

### Phase 1 — Sequential governed workflow vertical slice

**Goal:** prove the complete contract with minimal node semantics.

Deliver:

- workflow discovery, schema, compiler, hash, and snapshot;
- `agent`, `shell`, and `approval` nodes;
- dependency graph executed sequentially;
- per-node backend selection;
- output/artifact references;
- explicit approve/reject;
- file-tracker orchestrator-owned state transitions;
- CLI and basic web run detail;
- ticket-run fence and explicit resume after a simulated crash.

Exit criteria:

- an example `plan → approval → implement → test → approval` workflow completes end to end;
- kill/restart at every node boundary produces one unambiguous resumable state;
- legacy mode has zero behavioral regression.

### Phase 2 — Bounded DAG parallelism and operator parity

**Goal:** support independent review branches and full local operation.

Deliver:

- topological ready queue;
- per-workspace read/write lock;
- parallel read-only nodes;
- richer TUI run/gate actions;
- web execution timeline and artifact view;
- per-node/run budgets and usage;
- event polling or streaming;
- startup reconciliation and integrity checks.

Exit criteria:

- two read-only reviewers run concurrently after validation when both backends enforce read-only workspace access, and otherwise degrade to safe sequential execution;
- no write node overlaps any other workspace-using node;
- all operator actions have CLI, TUI, web, and API coverage;
- duplicate dispatch and duplicate gate resolution tests pass.

### Phase 3 — Controlled composition

Deliver only after production evidence from Phase 2:

- restricted conditions based on validated structured output;
- trigger rules;
- structured JSON output schemas;
- loop node with hard iteration/budget limits;
- reusable `include` blocks with cycle/depth checks;
- deterministic workflow routing by labels or ticket metadata;
- node-level skills/tool policies where capabilities permit;
- external tracker transition parity;
- visual DAG display and, later, a builder.

### Phase 4 — Governed child workflows, optional

Consider only when a real use case needs separately audited sub-runs. A child workflow would receive its own run record, artifacts, budget, approvals, and resume state while remaining under the parent ticket’s Symphony authority. Do not implement merely for feature parity with Archon.

---

## 24. Acceptance criteria

### 24.1 Backward compatibility

- [ ] With no `workflow_engine` config, the same ticket produces the same stage prompt, backend selection, worktree, state behavior, and visible status as before.
- [ ] Existing `symphony`, `symphony tui`, `symphony service`, `symphony runs`, board, doctor, and smoke commands remain compatible.
- [ ] Existing databases migrate without losing run or issue-flag rows.
- [ ] New tables and columns do not alter legacy query results unexpectedly.

### 24.2 Definition and dispatch

- [ ] Invalid YAML or DAG prevents dispatch with file/line diagnostics.
- [ ] The selected workflow, normalized snapshot, and hash are stored before the first node starts.
- [ ] Editing YAML during a run does not alter that run.
- [ ] An unknown ticket override never silently falls back.
- [ ] A governed ticket has at most one nonterminal run fence.

### 24.3 Execution

- [ ] Nodes run only after all required dependencies succeed.
- [ ] Independent read nodes may run concurrently within the configured limit.
- [ ] Write nodes never overlap another workspace-using node.
- [ ] Node backend precedence is deterministic and visible.
- [ ] Shell nodes run exactly once unless retry is explicit.
- [ ] Every node attempt has start and terminal events.
- [ ] Full output is bounded and stored safely; UI preview is redacted and capped.

### 24.4 Approval

- [ ] A pending gate survives service restart.
- [ ] Only the dedicated approval mutation can resolve it.
- [ ] Duplicate identical resolutions are idempotent.
- [ ] Conflicting resolution returns a conflict and does not change history.
- [ ] Ticket state movement and chat text do not approve a gate.
- [ ] Evidence includes referenced node output, tests, and diff summary.

### 24.5 Recovery

- [ ] SIGKILL during each supported node type leaves no duplicate active backend process after reconciliation.
- [ ] Runs in `needs_attention` are fenced and not auto-dispatched.
- [ ] Resume uses the content-addressed stored workflow snapshot.
- [ ] Succeeded nodes with valid artifacts are not rerun.
- [ ] A missing or hash-mismatched required artifact blocks resume with a precise error.
- [ ] Abandon releases the fence but preserves history and artifacts.
- [ ] Start fresh creates a new run ID only after the old run is abandoned.

### 24.6 Operator surfaces

- [ ] CLI, TUI, web, and API show the same authoritative status.
- [ ] A card shows current node and progress.
- [ ] Operators can inspect attempts, artifacts, errors, usage, and approval history.
- [ ] Resume, cancel, abandon, approve, and reject actions are available where applicable.
- [ ] UI actions return and render conflict/error states rather than assuming success.

### 24.7 Safety

- [ ] Ticket/output values cannot be raw-injected into shell command text.
- [ ] Artifact endpoint cannot traverse outside the artifact root.
- [ ] Common credential patterns are redacted from persisted previews/events.
- [ ] External-side-effect nodes are visible and not automatically retried by default.
- [ ] Non-loopback state-changing web access requires configured authentication and request-origin protection.

---

## 25. Verification strategy

### 25.1 Unit tests

- schema decoding and diagnostics;
- cycle detection and topological ordering;
- variable ancestry and rendering bounds;
- backend capability validation;
- read/write scheduler invariants;
- retry classification and backoff;
- redaction and path confinement;
- state-machine transition legality;
- approval optimistic locking;
- artifact hashing and atomic rename;
- definition normalization and stable hash.

### 25.2 Property and concurrency tests

Generate random acyclic graphs and verify:

- every node runs after dependencies;
- no node runs twice per attempt;
- terminal status is deterministic for the same outcomes;
- write-lock invariants always hold;
- event sequence is strictly increasing per run;
- concurrent resolution/acquire requests produce one winner.

### 25.3 Fault-injection tests

Terminate the process:

- after run/fence creation;
- after node `running` commit but before process spawn;
- while the backend is running;
- after artifact rename but before DB metadata commit;
- after node success but before downstream scheduling;
- after approval creation;
- during terminal ticket mutation;
- before fence release.

Each state must reconcile deterministically and idempotently.

### 25.4 Integration tests

Use fake backends first, then one representative resumable and one one-shot backend:

- full sequential workflow;
- mixed-backend workflow;
- shell failure and explicit retry;
- approval across restart;
- parallel read-only review;
- cancellation and process-group cleanup;
- budget exhaustion;
- file-ticket state and terminal summary;
- API conflict semantics;
- web smoke and TUI snapshot behavior.

### 25.5 Repository gates

Use the project’s existing gates:

```bash
python -m ruff check src tests
python -m pyright
python -m pytest -q --cov=src/symphony --cov-report=term --cov-fail-under=80
python scripts/smoke_web_api.py --base-url http://127.0.0.1:9999
```

Add a focused workflow suite, but the full suite remains the release gate.

---

## 26. Success metrics

All metrics are local by default.

### Reliability

- zero duplicate ticket dispatches attributable to workflow mode;
- zero duplicate approval resolutions;
- successful deterministic reconciliation in all supported fault-injection checkpoints;
- resume success rate by failure class;
- node retry success rate and repeated-failure rate;
- missing/corrupt artifact rate.

### Quality and governance

- percentage of governed runs that execute deterministic validation before completion;
- percentage requiring human gate intervention;
- review findings caught before PR creation;
- workflow validation failure rate;
- number of external-side-effect nodes executed without a preceding configured gate.

### Efficiency

- median and P95 node duration;
- time waiting for approval;
- time from interruption to operator resolution;
- tokens/cost by node type and backend;
- parallel review wall-clock reduction;
- event DB and UI update latency.

### Adoption

- governed runs versus legacy runs;
- workflows used by name;
- tickets using per-node multiple backends;
- abandonment and start-fresh rates.

No success metric justifies weakening explicit approval or resume safety.

---

## 27. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Building a second orchestrator | Competing ownership, duplicate dispatch, difficult recovery | DAG executor is subordinate to one Symphony ticket run; no independent daemon or worktree lifecycle. |
| Overloading `orchestrator/core.py` | Fragile monolith and difficult testing | Introduce `TicketExecutor`; place workflow logic in `symphony.flow`. |
| Ticket state and run state diverge | Misleading board and accidental redispatch | Runtime overlay is authoritative for execution; use locked orchestrator-owned coarse transitions and durable fence. |
| Parallel agents corrupt workspace | Conflicting edits or nondeterministic tests | Read/write scheduler plus capability-gated read-only enforcement; unsupported backends degrade to exclusive execution. |
| Agent lies about tests or completion | False success | Deterministic shell nodes and artifact contracts; engine controls completion. |
| Retry duplicates PR/deploy/comment | External damage | No default retry for side effects; idempotency key and preflight policy. |
| Crash after side effect but before success commit | Ambiguous replay | Store pre-call idempotency key; integration queries existing result; require operator decision when unverifiable. |
| Workflow language grows too quickly | Maintenance burden and weak semantics | Ship three node types first; add conditions/loops/composition only from demonstrated use cases. |
| Inconsistent backend capabilities | Workflow works only on one provider | Explicit capability matrix and preflight; fresh context portable default. |
| Secrets in logs/artifacts | Credential exposure | bounded persistence, redaction, raw transcript opt-in, safe artifact endpoint. |
| Old binary ignores a governed run | Duplicate work after rollback | mirror suspended state to existing pause flag; require terminal/paused governed runs before rollback. |
| UI builder consumes effort before runtime is stable | Attractive but unreliable product | execution detail and YAML validation precede visual authoring. |
| Natural-language approval ambiguity | Unsafe continuation | dedicated versioned approval mutation only. |

---

## 28. Decisions intentionally deferred

1. **Conditions:** add a restricted expression language only after structured output exists; never evaluate arbitrary Python or shell.
2. **Loops:** require hard iteration, time, and token limits plus explicit artifact/checkpoint semantics.
3. **Reusable includes:** load-time expansion with cycle/depth limits is preferred before child workflows.
4. **Child workflows:** justified only when separate governance, artifacts, cost, or approvals are needed.
5. **Visual builder:** follow a stable versioned schema; the YAML remains the portable source of truth.
6. **Remote workers:** separate project because it changes lease, artifact, process, and trust boundaries.
7. **PostgreSQL:** not needed for the current single-host product; preserve a storage interface only if it does not complicate SQLite correctness.
8. **Automatic model-based workflow routing:** avoid in early versions; deterministic ticket metadata is safer.
9. **Automatic “request changes” loop:** defer until loop semantics are auditable and bounded.
10. **Per-node sandboxing:** scheduler access declarations ship first; stronger OS/container isolation may follow.

---

## 29. Recommended first production workflow

Start with a conservative workflow that proves governance without advanced branching:

```text
plan (agent/read)
  → plan approval
  → implement (agent/write)
  → tests (shell/write)
  → review (agent/read)
  → release approval
  → finalize PR (agent/write + external side effect)
```

Do not begin with five parallel agents, loops, sub-workflows, or automatic merge. Add parallel read-only review after the event ledger, gates, and crash recovery are proven.

---

## 30. Research basis

The PRD is based on the repositories and documentation reviewed on 2026-08-07:

1. Archon current README and architecture:
   https://github.com/coleam00/Archon/blob/dev/README.md
2. Archon workflow authoring guide, including DAGs, dependencies, retries, resume, artifacts, includes, and child workflows:
   https://archon.diy/guides/authoring-workflows/
3. `oh-my-symphony` current README and documented product behavior:
   https://github.com/cskwork/oh-my-symphony/blob/main/README.md
4. Current Symphony run registry:
   https://github.com/cskwork/oh-my-symphony/blob/main/src/symphony/orchestrator/run_registry.py
5. Current Symphony web/API surface:
   https://github.com/cskwork/oh-my-symphony/blob/main/src/symphony/webapi.py
6. Current project configuration and verification gates:
   https://github.com/cskwork/oh-my-symphony/blob/main/pyproject.toml
   https://github.com/cskwork/oh-my-symphony/blob/main/.github/workflows/tests.yml

---

## 31. Final recommendation

Approve development through **Phase 0 and Phase 1 as one architecture initiative**, with separate mergeable slices:

1. event ledger and executor seam;
2. workflow schema/compiler;
3. sequential node executor;
4. durable approval and run fence;
5. explicit resume/reconciliation;
6. file-tracker state ownership in governed mode;
7. CLI and basic web execution detail.

Gate Phase 2 on fault-injection evidence, not feature count. The critical product improvement is not “more agents”; it is making a multi-agent ticket **repeatable, inspectable, resumable, and explicitly governed without weakening Symphony’s existing simplicity or execution authority**.
