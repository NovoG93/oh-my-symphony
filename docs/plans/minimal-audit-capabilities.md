# Minimal capability audit — chat → DAG → quality-gated delivery

**Date:** 2026-02-14 · **Scope:** what already exists in this repo toward "user chats
'make a to-do app' → agent decomposes into a DAG of dev-cycle tickets → automatic
git handling and quality gates". Audited from source, not docs claims.

---

## 1. `src/symphony/chat.py` — operator chat

**What it is.** A `ChatManager` (~1200 lines) that runs operator chat sessions
against the *host repo* by reusing the agent backend adapters (`build_backend`)
directly — the same claude/codex/gemini/opencode/pi CLI adapters workers use. So
yes, **chat already calls a real LLM agent CLI**.

**Entry points.** There is **no `symphony chat` CLI subcommand**. Chat is exposed
only through the web layer:
- REST: `/api/v1/chat/session[s]` (POST/PATCH/DELETE), `/chat/.../message` in
  `webapi.py` (`_register_chat_routes`, line ~1840).
- One-way WebSocket stream for transcript + ephemeral `agent_delta` token frames.
- Frontend: `src/symphony/web/static/{index.html,app.js}` chat panel.
- Max 3 concurrent sessions; JSONL transcripts under `.symphony/chat/`;
  advisory turn/token budgets (warn, never block).

**Two modes:**
- `qa` — read-only (claude `--permission-mode plan`, codex read-only sandbox).
- `edit` — the agent may modify the host working tree.

**Can it create tickets?** Yes, but only *incidentally*: `_BOARD_PREAMBLE`
(chat.py ~line 100) tells the agent the board format ("create
`<board_root>/<IDENTIFIER>.md` with `state: Todo` … Symphony picks it up
automatically") and edit mode permits the file write. `ChatManager` is
constructed with `request_refresh=orchestrator.request_refresh` so a filed
ticket is polled promptly.

**Gap vs conversational decomposition:**
- No decomposition prompting at all — the preamble describes the *file format*,
  not `blocked_by`, not the dev-cycle lanes, not the decomposition checklist
  that lives in the oneshot skill.
- No structured tool for ticket creation (freehand markdown authoring by the
  agent; no validation that IDs are unique, deps acyclic, states legal).
- No plan-preview/confirm loop ("here is the ticket DAG I propose — approve?").
- No awareness of the governed workflow engine (`workflow:` frontmatter key) or
  of per-lane quality gates.
- QA mode explicitly refuses to file tickets and tells the user to switch modes.

## 2. `src/symphony/flow/` + `cli/flow.py` + `orchestrator/flow_store.py` — the governed DAG engine

**A real DAG execution engine already exists**, but its unit of execution is
**nodes *within one ticket run***, not tickets. It is opt-in per repo via
`workflow_engine.enabled` in WORKFLOW.md (default off; this repo's WORKFLOW.md
does **not** enable it — the checked-in `.symphony/workflows/ticket-default.yaml`
exists but the stage-prompt legacy loop is what actually runs today).

**Spec format** (`schema.py`, `model.py`): YAML file in a reviewed
`workflow_engine.directory`, e.g. `.symphony/workflows/ticket-default.yaml`:
- `version: 1`, `name`, `defaults {backend, context, timeout_seconds, max_parallel_nodes}`.
- `nodes:` list; three types: `agent` (one backend turn, `prompt`/`prompt_file`),
  `shell` (`run:` command), `approval` (human gate with `title`, `instructions`,
  `evidence: [node-ids]`).
- Dependencies: `depends_on: [node-ids]`. Compiler (`compiler.py`) builds
  topological layers, transitive ancestry, content hash; rejects cycles,
  dangling deps, unknown fields (fail-closed). Limits: ≤100 nodes, ≤20 deps/node.
- Prompt interpolation (`prompts.py`): closed `${...}` grammar — `ticket.*`,
  `run.*`, `${nodes.X.output}` only if X is a transitive ancestor; untrusted
  values wrapped in trust delimiters.

**Ticket→workflow mapping:** a ticket picks a workflow via `workflow:` YAML
frontmatter (`issue.workflow`, parsed in `trackers/file.py`), else
`workflow_engine.default`. Executor selection is in
`core.py::_executor_for` → `GovernedWorkflowExecutor` vs `LegacyStageExecutor`
(`orchestrator/executors.py`).

**Execution** (`executor.py`): subordinate to the orchestrator (which owns
scheduling, leases, workspaces). v1 runs **one node at a time** (topological
order *is* the schedule). Run states: terminal / **suspended** (approval gate
open — no process, fence retained so polling can't redispatch) / interrupted
(crash → `needs_attention`, never auto-restarted).

**Gates & evidence:**
- `approval` nodes = human quality gates, resolved only via
  `symphony approval resolve --approve/--reject` or the web panel.
- `artifacts.py`: content-addressed node outputs (sha256, size, integrity
  `verify()`), ≤32 MiB, path-hostile-input sanitized.
- `gitprov.py`: per-node git provenance (before/after HEAD, committed?, paths
  touched) — evidence, never a precondition (nothing raises).
- `flow_store.py` (SQLite in `.symphony/state.db`): event ledger — every state
  change + its event committed in one transaction; **fences** block redispatch
  of an issue with a nonterminal governed run; `RiskSummary` surfaces shell
  nodes / external-side-effect nodes / ungated external nodes for review.
- `retries.py`: explicit per-node `retry {max_attempts, backoff_seconds, on:[error-classes]}`.

**CLI** (`cli/flow.py`): `symphony workflow list|show|validate`,
`symphony run show|events|resume|abandon|cancel`, `symphony approval
list|resolve`. Writes are ledger-level; actually resuming requires the running
service (`POST /api/v1/runs/{id}/resume`).

**Bundled default workflow** (`ticket-default.yaml`) is already a dev-cycle DAG:
`plan → approve-plan(human) → implement → test(pytest) → review(agent, read-only)
→ release-approval(human) → finalize(PR prep)`. The `review.md` prompt is a
genuine reviewer brief (correctness/scope/safety/maintainability, failure-
scenario-or-it's-noise). **Deliberately linear; no parallel branches yet.**

## 3. `skills/symphony-skill/oneshot/` — prompt → evidence-gated board

This is the closest existing thing to the target experience, but it is an
**operator-side Claude Code skill (prompts + bash), not product code**.

- `templates/bootstrap.sh "<prompt>"` initializes a `.oneshot/` vault
  (prompt.md, brief.md, plan.md, architecture.md, contracts.md, append-only
  claims.md / verification.md / decisions.log), writes a dedicated
  `WORKFLOW.oneshot.md` with 7 lanes, creates the intake ticket, and starts
  Symphony.
- **Lanes:** `Brief → Plan → Build → Verify → QA → Polish → Deliver`
  (+ terminal `Delivered`). Verify and Polish can reopen Build.
- **Decomposition** happens in the **Plan lane** (a worker agent), guided by
  `reference/decomposition.md`: classify request type (bugfix / feature / app
  delivery / release / docs / research-spike), run a checklist (independently
  testable, self-contained spec, fits one context window ≤5 files/≤500 lines,
  owns one contract), write ticket descriptions as full worker prompts
  (Goal/Scope/Dependencies/AC/Verification/Done evidence), express deps as
  `blocked_by`, and number tickets in task order. Includes worked slice
  patterns (CRUD app, CLI tool …) that already look like the target DAG
  (DISCOVERY → BUILD-1..N with deps → VERIFY → QA → DELIVER).
- **Adversarial review: yes, explicitly.** The Verify lane prompt says "You are
  an ADVERSARY to `claims.md` — re-prove every entry." QA lane produces
  qa-report.md + PDF with sha256 logging (browser apps get Playwright:
  `templates/playwright-qa.spec.ts`, `qa-pdf.sh`).
- **Delivery gate is literal bash** (in `lanes.md`/template): brief/plan/
  verification non-empty, `verdict: GREEN` grep, qa-report `APPROVED FOR
  DELIVERY` for browser apps. Defect-registration loop: Verify files new bug
  tickets with repro evidence and adds them as `blocked_by`.
- `templates/SYSTEM.md` constitution: orchestrator never implements; vault is
  the only persistent state; loop terminates only on delivery proof.

**Gap:** the entry point is "run bootstrap.sh with a prompt", i.e. the
*operator's* Claude Code session is the conversational layer. Nothing in
Symphony-the-product hosts this; the lane prompts are inline Liquid in a
template, duplicating (in different vocabulary) what `docs/symphony-prompts/`
does for the main board.

## 4. WORKFLOW.md `prompts.base`+`prompts.stages` and git automation

**Stage prompts** (`docs/symphony-prompts/file/`, wired via `prompts:` in
WORKFLOW.md; assembled per turn as base + current-state stage):
- `todo.md` — triage gate: actionable → In Progress, else Blocked; bug label →
  reproduction-first (failing repro under `docs/<ID>/reproduce/`).
- `in-progress.md` — **plan + TDD + self-critique in one lane**: `## Plan`,
  `## Acceptance Tests`, `## Done Signals`, TDD loop ("no production code
  without a test"), `## Implementation`, `## Self-Critique`, `## Pipeline
  Route`; honors rewind scope (`$SYMPHONY_REWIND_SCOPE`).
- `verify.md` — review + QA + merge gate: diff-vs-ticket review, fixed 7-row
  `## Security Audit`, `## Review Findings` severity table rewinds to In
  Progress, real acceptance commands with durable evidence under
  `docs/<ID>/qa/`, `## AC Scorecard` (evidence cells must cite artifacts),
  `## QA Failure` rewind, **Merge Gate** (merge-tree preflight → `--no-ff`
  merge, `## Merge Status`), Playwright required for browser UI.
- `learn.md` — wiki write-back (`docs/llm-wiki/`), `## Learnings`,
  `## As-Is → To-Be Report`, Human Review branch, then Done.
- Board states: `Todo → In Progress → Verify → Learn → Done`, with
  Verify/Learn→In Progress rewinds capped by `agent.max_attempts` (soft cap 3
  → Blocked).

**Git automation (already substantial):**
- `hooks.after_create` → `scripts/symphony-setup-worktree.sh`: per-ticket git
  worktree on `symphony/<ID>` branch; kanban/ symlinked to host, docs/ stays
  branch-local.
- `hooks.after_run` (inline in WORKFLOW.md): per-turn commit-or-amend `wip:`
  snapshot, with machine-readable markers `[no-test]` (prod change without
  paired test) and `[scope-expand]` (rewind touched out-of-scope files) that
  the review prompt promotes to findings.
- `auto_commit_on_done` (config default true): orchestrator squashes turns into
  one `<ID>: <title>` commit on exit.
- `utils/auto_merge.py`: on Done, merge `symphony/<ID>` into the target branch
  `--no-ff`, with exclude-path blocking, dirty-host-overlap skip, upstream
  verification; never raises — reports gate satisfaction so dependents don't
  trust an unmerged blocker.
- Optional `after_done` hook (commented out) pushes the branch and opens a PR
  via `gh`.

## 5. Ticket dependencies and scheduling today

- Frontmatter key: **`blocked_by`** (list; `trackers/file.py` parses id /
  identifier / mapping forms and hydrates each blocker's current state).
  `issue.py::BlockerRef{id, identifier, state}`. There is no `deps:` key —
  `blocked_by` *is* the cross-ticket dependency edge, so a board of tickets
  with `blocked_by` **is already a scheduled DAG**.
- Scheduling (`orchestrator/core.py::_eligibility_contention_decision`, ~3585):
  a ticket waits (`WAIT_NON_SLOT`) while any blocker is in flight or its state
  is not a *successful* terminal state (`_blocker_dependency_is_resolved` —
  Done counts, Blocked/Cancelled do not). Plus per-state concurrency caps
  (`max_concurrent_agents_by_state`), global slots, per-state token budgets,
  priority ordering, auto-triage of actionable Todos.
- No cycle detection across tickets, no DAG visualization of the board, no
  validation at ticket-creation time that a `blocked_by` target exists.

## 6. Gap list for chat → DAG → quality-gated delivery

**What already exists (do not rebuild):**
- Cross-ticket DAG semantics: `blocked_by` + orchestrator eligibility. ✔
- Within-ticket dev-cycle DAG with human gates, evidence ledger, artifacts,
  git provenance: governed workflow engine (`flow/`). ✔
- Dev-cycle stage prompts with plan/TDD/self-critique/review/security-audit/
  QA/merge-gate/learn: `docs/symphony-prompts/file/`. ✔
- Adversarial verification + literal-bash delivery gate + decomposition
  heuristics: oneshot skill (prose form). ✔
- Git automation: worktrees, wip snapshots with quality markers, squash on
  Done, auto-merge with preflight, optional PR hook. ✔
- Chat that talks to a real agent CLI over the host repo and *can* file
  tickets in edit mode. ✔

**Concretely missing for the target experience:**
1. **A decomposition brain reachable from chat.** Chat's preamble teaches the
   file format only. Nothing injects the oneshot `decomposition.md` heuristics,
   lane taxonomy, or `blocked_by` conventions into a chat turn; no
   "propose ticket DAG → operator approves → tickets materialize" loop.
2. **Structured ticket-creation API for agents.** Ticket filing is freehand
   markdown. No validated create-tickets-with-deps endpoint/tool (unique IDs,
   acyclic `blocked_by`, existing targets, legal states) that a chat agent or a
   Plan-lane agent could call instead of hand-writing YAML frontmatter.
3. **Research/discovery/adversarial-plan-review lanes on the main board.** The
   default board compresses the cycle into 4 lanes; research and discovery
   exist only as oneshot's Brief lane and as decomposition prose. Adversarial
   *plan* review exists nowhere on the main board (Verify reviews *code*;
   `approve-plan` in ticket-default.yaml is a *human* gate, not an adversarial
   agent). The target's "adversarial plan review" step needs either a new stage
   prompt, a governed-workflow agent node, or a plan-review ticket type.
4. **Ticket-DAG-aware planning artifacts.** No machine-readable plan object
   linking a user goal to the set of tickets it spawned (oneshot's `plan.md`
   ticket table is prose in a vault; nothing on the main board tracks
   "these N tickets = one user request", progress %, or completion of the
   *request* as opposed to individual tickets).
5. **Cross-ticket DAG validation & visibility.** No cycle check, no dep-graph
   view in TUI/web, no warning for `blocked_by` pointing at nonexistent or
   Cancelled tickets.
6. **Chat CLI/TUI entry.** Chat is web-only; "just chat" from the terminal
   (`symphony chat "make a to-do app"`) does not exist.
7. **Governed engine ↔ board-DAG integration.** The two DAG layers don't
   compose: a governed workflow cannot spawn tickets, and ticket decomposition
   cannot emit a governed workflow. The dev-cycle-per-ticket (flow YAML) and
   dev-cycle-as-lanes (stage prompts) are parallel universes; the target needs
   one of them designated primary.

**Overlaps / duplication to reconcile before building:**
- **Three dev-cycle encodings:** (a) stage prompts `Todo/In Progress/Verify/
  Learn`, (b) governed `ticket-default.yaml` `plan→approve→implement→test→
  review→approve→finalize`, (c) oneshot lanes `Brief→Plan→Build→Verify→QA→
  Polish→Deliver`. Same intent, three vocabularies, three gate mechanisms
  (prompt-discipline + rewinds vs approval nodes + ledger vs vault files +
  bash gates).
- **Two decomposition documents:** oneshot `reference/decomposition.md` vs the
  ticket-description conventions embedded in stage prompts — not shared with
  chat at all.
- **Two QA/evidence conventions:** `docs/<ID>/{work,qa}/` + ticket sections
  (main board) vs `.oneshot/vault/` + claims/verification (oneshot) vs
  content-addressed `ArtifactStore` (governed). Any "quality gate" feature
  must pick one evidence substrate.
- **Two merge stories:** Verify-prompt merge gate (agent runs merge-tree and
  merges) vs `auto_merge.py` on Done (orchestrator merges). Both are active in
  this repo's WORKFLOW.md (`auto_merge_on_done: true` *and* the Verify prompt
  instructs the merge) — the prompt does the merge at Verify and the util is
  the backstop; a redesign should make one canonical.

**Shortest credible path (observation, not a plan):** the target is roughly
"oneshot's Plan-lane decomposition, promoted from skill prose into product
code, fronted by the existing chat (plus a structured ticket-creation tool),
emitting `blocked_by`-linked tickets whose per-ticket dev cycle is either the
existing 4-lane stage prompts or the governed DAG" — every load-bearing piece
exists; what's missing is the connective tissue and one authoritative
dev-cycle encoding.
