# Implementation Plan: Selective Upstream Backports for `NovoG93/oh-my-symphony`

> **Target repository:** `https://github.com/NovoG93/oh-my-symphony`
>
> **Target branch:** `develop`
>
> **Upstream repository:** `https://github.com/cskwork/oh-my-symphony`
>
> **Reference upstream branch:** `main`
>
> **Planning baseline:** 2026-09-05
>
> **Primary implementer:** Sol-5.6 coding agent

---

## 0. Mission

Implement the following selected upstream changes in the downstream `develop` branch **without merging upstream wholesale** and without regressing downstream-specific functionality:

1. Linear current-relation-schema fix.
2. Per-dispatch environment isolation.
3. Retry-pending touched-file conflict detection.
4. Per-workflow persistent-state namespacing plus atomic JSON writes.
5. Chat **Intent Gate**.
6. Extraction of `orchestrator/attempt.py` with phase-oriented orchestration.

The implementation must be a **downstream-native port**, not a blind cherry-pick. Upstream is a design/reference source. The fork has diverged and contains important custom functionality that must remain authoritative.

The work should be delivered as a sequence of small, reviewable commits with tests after every stage. The final state must pass the full repository test/lint/type-check gates.

---

# 1. Non-negotiable downstream invariants

Before changing code, treat these as invariants. A change that violates one of them is a regression even if it matches upstream exactly.

## 1.1 Named agent profiles remain authoritative

The fork already supports downstream profile-aware dispatch. `BackendInit` currently carries downstream-specific context such as:

- `selection: AgentSelection | None`
- `resolved_backend_config`
- `usage_manager`
- `usage_pool`

Do **not** remove, flatten, bypass, or replace these fields when porting upstream `BackendInit` changes.

Agent profile resolution must continue to determine the actual backend/model/configuration used for a run.

## 1.2 Usage-aware scheduling remains authoritative

The fork already has provider/account usage awareness and `waiting_provider_usage` behavior.

Preserve:

- usage-pool resolution;
- provider usage checks before dispatch;
- pool-specific reset/wait information;
- fail-open/fail-closed behavior currently configured by the fork;
- the rule that an already-running worker is not interrupted merely because a provider limit becomes exhausted;
- downstream telemetry/events/UI around remaining provider capacity;
- stage/profile re-resolution when a ticket changes execution stage.

The selected upstream changes must compose with this system rather than replace it.

## 1.3 Downstream web security remains authoritative

The fork currently has a stronger web authorization boundary than upstream. Preserve it.

In particular:

- supported auth modes remain `token`, `disabled`, and `capabilities`;
- every API route remains explicitly capability-classified / fail-closed;
- exact Host and Origin protections remain active;
- non-loopback bind protections remain active;
- Chat WebSockets continue to use the existing single-use, origin-bound, short-lived WebSocket ticket mechanism;
- the long-lived API bearer must not be put into WebSocket URLs;
- current project-setup confirmation-token semantics remain intact.

Do **not** replace `web_policy.py` with upstream auth logic.

## 1.4 Downstream backend support must remain intact

Audit the actual `SUPPORTED_AGENT_KINDS` on the target branch before implementation. At planning time the fork includes more backends than upstream, including downstream additions such as Copilot support.

Any infrastructure change that affects backend process creation must be implemented for **every supported backend**, not only the backends present upstream.

## 1.5 Existing release, continuation, scheduling, and run-authority behavior must remain semantically unchanged

The `attempt.py` extraction is a refactor. It must not silently change:

- release authority checks;
- durable run authority / leases;
- continuation checkpoints;
- crash continuation;
- retry classification;
- hooks;
- artifact collection;
- auto-commit / auto-merge flow;
- turn budgets;
- token budgets;
- provider-usage waiting;
- phase transitions;
- pause/resume;
- cancellation/shutdown behavior;
- scheduler-visible status codes;
- logging/event names that tests or the UI consume.

## 1.6 No whole-upstream merge

Do not run:

```bash
git merge upstream/main
```

Do not mass-cherry-pick the upstream range.

Use upstream commits/files as references and port the intended behavior deliberately.

---

# 2. Recommended commit sequence

Implement in this order:

1. `fix(linear): support current Linear inverse relation schema`
2. `fix(state): namespace workflow state and write JSON atomically`
3. `fix(scheduler): retain touched-file conflicts for pending retries`
4. `fix(dispatch): isolate per-dispatch subprocess environment`
5. `feat(chat): add server-owned intent approval gate`
6. `refactor(orchestrator): extract phase-oriented attempt runner`

Rationale:

- Features 1–4 are isolated correctness fixes and establish the behavior that the later refactor must preserve.
- The Chat Intent Gate touches Chat/API/UI code but relatively little worker execution code.
- `attempt.py` should be **last**, after the worker path already contains the desired env and scheduler behavior, so the extraction moves the final semantics rather than forcing later edits across two files.

Do not combine all six into one giant commit.

---

# 3. Repository reconnaissance before editing

The implementation agent must first inspect the exact target revision rather than assuming this plan's line numbers still match.

## 3.1 Prepare remotes

```bash
git status --short --branch
git remote -v
```

If no `upstream` remote exists:

```bash
git remote add upstream https://github.com/cskwork/oh-my-symphony.git
```

Then:

```bash
git fetch origin develop
git fetch upstream main
```

Stay on the downstream branch/worktree supplied for the task. Do not reset it to upstream.

Useful comparison commands:

```bash
git merge-base HEAD upstream/main
git log --oneline --decorate --no-merges $(git merge-base HEAD upstream/main)..upstream/main
git diff --stat HEAD...upstream/main
```

## 3.2 Build a downstream feature map

Before edits, run searches such as:

```bash
rg -n "inverseRelations|LinearUnknownPayload" src tests
rg -n "class BackendInit|BackendInit\(" src tests
rg -n "_apply_dispatch_env|SYMPHONY_TOKEN_EMA|SYMPHONY_TOKEN_BUDGET|SYMPHONY_REWIND_SCOPE" src tests
rg -n "class RetryEntry|_conflict_blocker|_schedule_retry|retry_pending|waiting_provider_usage" src tests
rg -n "token_ema\.json|done_count\.json|os\.replace|write_json" src tests
rg -n "ChatSession|project_setup_actions|confirmation_token_hash|ChatProjectAuthorizationError" src tests
rg -n "_run_agent_attempt|_transition_agent_phase|AgentSelection|usage_pool|usage_manager" src/symphony/orchestrator tests
```

Record the actual files involved before making changes.

## 3.3 Capture a baseline

Run the existing targeted suites and then the normal repository gates before modifying anything.

At minimum:

```bash
python -m pytest -q
python -m ruff check src tests
python -m pyright
```

If the repository documents different canonical commands, use those instead/additionally.

If baseline failures exist, record them in the implementation summary and avoid attributing pre-existing failures to this work.

---

# 4. Feature 1 — Linear current-relation-schema fix

## 4.1 Goal

Update the Linear GraphQL adapter so blocker relationships are fetched with the current relation shape and normalized safely.

The important upstream behavior is:

1. Stop filtering `inverseRelations` server-side with the old relation filter shape.
2. Fetch a bounded relation connection:

```graphql
inverseRelations(first: 50) {
  nodes {
    type
    issue {
      id
      identifier
      state { name }
    }
  }
  pageInfo {
    hasNextPage
  }
}
```

3. Filter relation rows whose `type == "blocks"` **locally**.
4. Refuse to treat an incomplete/truncated relation connection as authoritative.

The scheduler must never interpret “we only received the first page of blocker data” as “this issue has no additional blockers.”

## 4.2 Current downstream problem

At the planning baseline, downstream `src/symphony/trackers/linear.py` still contains the old form in both candidate and full lookup queries:

```graphql
inverseRelations(filter: { type: { eq: "blocks" } })
```

The downstream normalizer also treats missing relation data permissively and does not verify `pageInfo.hasNextPage`.

## 4.3 Files expected to change

Primary:

- `src/symphony/trackers/linear.py`

Tests: identify the repository's existing Linear adapter test module(s) with `rg`; extend those rather than creating redundant parallel fixtures unless necessary.

## 4.4 Implementation steps

### Step 4.4.1 — update every issue query that needs blocker data

Audit all GraphQL query constants that populate an `Issue` with blockers.

At minimum update:

- the candidate/polling query;
- the full/by-ID query.

Replace filtered `inverseRelations(...)` with `inverseRelations(first: 50)` and request:

- `nodes.type`;
- `nodes.issue.id`;
- `nodes.issue.identifier`;
- `nodes.issue.state.name`;
- `pageInfo.hasNextPage`.

Do not leave one query on the old shape and one on the new shape.

### Step 4.4.2 — normalize relation payload defensively

In the issue normalizer:

1. Require `inverseRelations` to be an object/dict when blocker data is expected.
2. Require `inverseRelations.nodes` to be a list.
3. Require `inverseRelations.pageInfo` to be an object/dict.
4. Require `pageInfo.hasNextPage` to be a boolean.
5. If `hasNextPage is True`, raise `LinearUnknownPayload` (or the existing downstream equivalent) with a clear reason such as:

```text
issue.inverseRelations incomplete
```

6. Iterate rows and keep only rows with:

```python
relation.get("type") == "blocks"
```

7. Normalize the related `issue` into the fork's blocker representation exactly as before.

The point is to **fail closed** when blocker information is incomplete.

### Step 4.4.3 — preserve unrelated Linear behavior

Do not alter:

- pagination of the top-level issue query unless necessary;
- label normalization;
- priority mapping;
- issue state normalization;
- tracker retry/error semantics;
- downstream fields such as `agent_profile`, `agent_kind`, or request metadata if Linear parsing contains them.

## 4.5 Tests

Add regression tests covering at least:

### Query shape

- candidate query contains `inverseRelations(first: 50)`;
- full/by-ID query contains `inverseRelations(first: 50)`;
- neither query contains the old `inverseRelations(filter: ...)` form;
- `type` and `pageInfo.hasNextPage` are requested.

### Normalization

- `type: "blocks"` is included in `blocked_by`;
- unrelated relation types are ignored;
- zero relation nodes produces zero blockers when `hasNextPage == false`;
- multiple `blocks` rows normalize correctly;
- malformed/missing relation container is rejected where appropriate;
- malformed/missing `nodes` is rejected;
- malformed/missing `pageInfo` is rejected;
- non-boolean `hasNextPage` is rejected;
- `hasNextPage == true` raises `LinearUnknownPayload` and does not return an apparently-unblocked issue.

### Scheduler safety regression

If there is already an adapter-to-scheduler test seam, add one test showing that unknown/truncated Linear blocker data cannot result in dispatch eligibility.

## 4.6 Acceptance criteria

- [ ] Both blocker-bearing queries use the new relation shape.
- [ ] Relation types are filtered locally.
- [ ] Truncated relation data is rejected.
- [ ] Existing Linear tests pass.
- [ ] New malformed/truncated payload tests pass.
- [ ] No changes to non-Linear tracker behavior.

---

# 5. Feature 2 — Per-workflow persistent-state namespacing + atomic JSON

## 5.1 Goal

Prevent sibling workflow files in the same repository from sharing workflow-specific state accidentally, while also making small JSON state writes robust against torn files and transient Windows `os.replace` contention.

At minimum this applies to:

- token EMA state;
- done-count state.

## 5.2 Desired naming behavior

The historical canonical workflow retains backward-compatible filenames:

```text
WORKFLOW.md
  -> .symphony/token_ema.json
  -> .symphony/done_count.json
```

A non-default sibling workflow gets a deterministic suffix derived from its workflow filename:

```text
WORKFLOW.claude.md
  -> .symphony/token_ema.WORKFLOW.claude.json
  -> .symphony/done_count.WORKFLOW.claude.json

WORKFLOW.codex.md
  -> .symphony/token_ema.WORKFLOW.codex.json
  -> .symphony/done_count.WORKFLOW.codex.json
```

Do not automatically copy the old shared state into a non-default workflow: if multiple sibling workflows previously shared `token_ema.json`, there is no reliable way to infer ownership. Starting the newly namespaced state clean is safer than silently cloning ambiguous state.

## 5.3 Add `utils/atomic_json.py`

Create:

```text
src/symphony/utils/atomic_json.py
```

Implement two reusable helpers based on upstream behavior.

### `state_file_name(workflow_path, base_name)`

Semantics:

- if `workflow_path.name == "WORKFLOW.md"`, return `<base_name>.json`;
- otherwise strip the final `.md` from the workflow filename;
- sanitize characters outside a conservative set such as `[A-Za-z0-9._-]` to `-`;
- return `<base_name>.<sanitized-workflow-stem>.json`.

Examples:

```python
state_file_name(Path("/repo/WORKFLOW.md"), "token_ema")
# token_ema.json

state_file_name(Path("/repo/WORKFLOW.claude.md"), "token_ema")
# token_ema.WORKFLOW.claude.json
```

Avoid path traversal: only the filename/stem, never arbitrary parent path text, belongs in the generated filename.

### `write_json_atomic(path, payload, ...)`

Required semantics:

1. Ensure `path.parent` exists.
2. Create a temporary file **in the same directory as the target** so final replacement stays on one filesystem.
3. Serialize valid JSON completely into the temporary file.
4. Flush/close the temp handle before replacement.
5. Replace target with `os.replace(temp, path)`.
6. Retry transient `PermissionError` from `os.replace`, using short bounded backoff (upstream uses approximately 10 ms then 50 ms).
7. Propagate the final failure after the bounded retry count.
8. Always remove any leftover temporary file on error.
9. Do not swallow arbitrary exceptions.

Use deterministic formatting (e.g. `indent=2`, `sort_keys=True`) consistent with upstream/current project conventions.

This helper provides atomic replacement/torn-write protection. It is **not** a transactional multi-process read-modify-write lock; do not claim otherwise.

## 5.4 Wire token EMA state through the helper

Find the existing `_token_ema_path` / load/save logic in `orchestrator/core.py` (or wherever it lives at implementation time).

Change path derivation to:

```python
state_dir / state_file_name(cfg.workflow_path, "token_ema")
```

Use `write_json_atomic()` for writes.

Preserve the current payload schema and load semantics. Do not reset or rename fields inside the JSON.

## 5.5 Wire done-count state through the helper

Likewise change the done-count path to:

```python
state_dir / state_file_name(cfg.workflow_path, "done_count")
```

Use `write_json_atomic()` for persistence.

Preserve its schema and counting semantics.

## 5.6 Audit other `.symphony/*.json` state, but do not scope-creep blindly

Run:

```bash
rg -n '\.symphony.*\.json|json\.dump|json\.dumps|os\.replace' src/symphony
```

For each JSON file, decide whether it is:

- **workflow-instance state** -> potentially should be namespaced;
- **project-wide shared state** -> must remain shared;
- **append-only/SQLite/artifact index state** -> different persistence semantics.

For this task, changing `token_ema` and `done_count` is mandatory. Do not rename project-wide stores such as stats, run registries, artifact stores, release databases, etc. merely because they are JSON-adjacent.

Document any other candidate rather than changing it without a clear ownership argument.

## 5.7 Tests

### `state_file_name`

Cover:

- canonical `WORKFLOW.md` compatibility;
- `WORKFLOW.claude.md`;
- another sibling filename;
- spaces/special characters sanitized deterministically;
- no directory components leak into the output;
- result is a filename, not an absolute path.

### `write_json_atomic`

Cover:

- creates parent directory;
- writes valid JSON;
- replaces an existing file;
- target is never left as partially written JSON in simulated failure scenarios;
- `PermissionError` from `os.replace` is retried;
- success after a transient `PermissionError` works;
- final `PermissionError` propagates after retry limit;
- non-`PermissionError` failure propagates immediately;
- temporary files are cleaned on failure.

Avoid real sleep in tests by injecting/patching sleep or retry timing.

### orchestrator state paths

Create two configs pointing to sibling workflows in the same directory and assert:

```text
WORKFLOW.md            -> token_ema.json / done_count.json
WORKFLOW.claude.md     -> token_ema.WORKFLOW.claude.json / done_count.WORKFLOW.claude.json
```

Verify writes do not overwrite each other.

### backward compatibility

Write a canonical legacy state file and ensure `WORKFLOW.md` still loads it.

## 5.8 Acceptance criteria

- [ ] Canonical workflow retains legacy names.
- [ ] Sibling workflows have independent deterministic state files.
- [ ] Token EMA and done count use atomic writes.
- [ ] No ambiguous automatic migration of sibling workflow state.
- [ ] Atomic writer cleans up temporary files after failure.
- [ ] Tests cover transient Windows-style replacement contention.

---

# 6. Feature 3 — Retry-pending touched-file conflict detection

## 6.1 Goal

A ticket that has failed and is waiting for retry must continue to reserve/conflict with the files it was working on, even if it is no longer present in `_running`.

Without this behavior:

```text
A touches src/auth.py
A fails and enters retry timer
A disappears from _running
B also touches src/auth.py
B incorrectly dispatches before A retries
```

Desired:

```text
A running            -> owns src/auth.py
A retry-pending      -> STILL owns src/auth.py
A retry/restarts     -> owns src/auth.py
B waits throughout
```

## 6.2 Extend `RetryEntry`

In `src/symphony/orchestrator/entries.py`, extend the existing dataclass with an immutable snapshot:

```python
touched_files: frozenset[str] = frozenset()
```

Use an immutable type because this represents the dispatch/retry ownership snapshot, not a mutable cache.

Do not remove existing fields such as `kind` or `holds_slot`.

## 6.3 Capture the file set when retry ownership is created

Locate every path that creates/replaces a `RetryEntry`:

```bash
rg -n "RetryEntry\(" src/symphony/orchestrator
```

At the point where a running ticket becomes retry-pending, capture its touched files **before the running entry is discarded**.

Use the same canonical touched-file calculation already used for running conflict detection. Do not invent a second parser with subtly different semantics.

Conceptually:

```python
touched = frozenset(self._touched_files_for(issue_or_entry))
RetryEntry(..., touched_files=touched)
```

If a retry entry is rescheduled/reparked, preserve the existing snapshot unless there is an authoritative newer live issue/body from which the file set is intentionally recomputed.

Do not accidentally reset `touched_files` to the default during timer churn.

## 6.4 Update `_conflict_blocker`

Preserve the current running-worker check, then also inspect retry-pending entries.

Expected logic:

1. Compute candidate touched files.
2. If none, no file conflict.
3. Check current `_running` entries exactly as today.
4. Check `_retry.items()` / equivalent retry registry.
5. Skip the candidate's own issue ID.
6. If the retrying issue is simultaneously present in `_running`, prefer the live running issue's current touched-file body.
7. Otherwise use `retry_entry.touched_files`.
8. Return a blocker if intersection is non-empty.

Avoid reparsing a ticket file that may have moved/changed/disappeared while retry ownership is pending; the snapshot is specifically there to avoid losing the ownership fact.

## 6.5 Interaction with `waiting_provider_usage`

This fork has downstream provider-capacity waiting states. Audit whether these are represented through the same retry registry.

Default rule for this backport:

> If a retry/wait entry represents an in-flight ticket that will resume the same work, its touched-file ownership remains active while pending.

Do **not** use `holds_slot` as an accidental proxy for touched-file ownership unless the existing scheduler explicitly defines that equivalence.

If `waiting_provider_usage` can last for a long time, keep upstream-equivalent conflict safety unless there is already a deliberate downstream rule that releases file ownership. If such a rule exists, preserve it and add an explicit test/documentation comment.

## 6.6 Tests

Add regression scenarios:

### Core race

- A touches `src/auth.py`.
- A enters retry pending and is removed from `_running`.
- B touches `src/auth.py`.
- `_conflict_blocker(B)` reports A / refuses B.

### Non-overlap

- A retry-pending touches `src/auth.py`.
- B touches `src/payments.py`.
- B is not blocked by A.

### Same issue

The retry snapshot for the candidate itself must not self-block.

### Repark/reschedule

A retry entry that is rescheduled keeps its touched-file snapshot.

### Running-preferred behavior

If an issue is represented both live and in retry bookkeeping during a transition window, conflict calculation uses the authoritative live representation without double-counting or contradictory behavior.

### Provider-usage wait

Add a targeted downstream test showing the intentional ownership semantics for `waiting_provider_usage`.

## 6.7 Acceptance criteria

- [ ] Retry entries retain a frozen touched-file snapshot.
- [ ] Pending retry work participates in conflict checks.
- [ ] Rescheduling does not erase ownership.
- [ ] Non-overlapping tickets remain dispatchable.
- [ ] Provider-usage waiting behavior is explicitly tested.

---

# 7. Feature 4 — Per-dispatch environment isolation

## 7.1 Goal

Remove worker-specific environment mutation from process-global `os.environ`.

At the planning baseline, downstream code still has an `_apply_dispatch_env`-style path that exports values such as:

```text
SYMPHONY_TOKEN_EMA
SYMPHONY_TOKEN_BUDGET
SYMPHONY_REWIND_SCOPE
```

into global process state before backend spawn.

That is unsafe when multiple tickets/profiles/backends dispatch concurrently: one dispatch can overwrite another's values or leak rewind scope into a forward run.

Desired invariant:

> Every backend process receives a dispatch-local env overlay, and creating/spawning one worker never mutates the parent Symphony process environment for dispatch-local values.

## 7.2 Extend downstream `BackendInit`; do not replace it

In the downstream backend base/init dataclass, add:

```python
from dataclasses import field

env: dict[str, str] = field(default_factory=dict)
```

Keep all downstream fields intact, including:

- profile selection;
- resolved backend configuration;
- usage manager;
- usage pool;
- existing `__post_init__` behavior.

Use a defensive copy if callers could retain and mutate the input mapping.

## 7.3 Replace `_apply_dispatch_env` with a pure builder

Create/port a helper conceptually like:

```python
def _dispatch_env_for(
    self,
    issue: Issue,
    cfg: ServiceConfig,
    *,
    is_rewind: bool,
) -> dict[str, str]:
    ...
```

It returns an overlay and **must not mutate `os.environ`**.

Expected values:

- always include the current token EMA value expected by hooks;
- always include the token budget value expected by hooks;
- for rewind dispatches, include the serialized rewind scope;
- for normal/forward dispatches, omit `SYMPHONY_REWIND_SCOPE` entirely.

Omitting the rewind key is important: do not emit an empty value merely to “clear” a process-global variable. There should no longer be process-global dispatch state to clear.

Search for every call to `_apply_dispatch_env` and eliminate it once all callers use the returned mapping.

## 7.4 Thread env into every `BackendInit` construction used for workers

Audit all sites:

```bash
rg -n "BackendInit\(" src/symphony
```

At planning time, important downstream sites include:

- initial worker dispatch;
- in-run phase transition / backend rebuild;
- continuous-improvement/improvement worker paths;
- any retry/recovery backend construction;
- Chat backend construction, if it shares `BackendInit` (Chat should normally get no worker dispatch overlay unless explicitly intended).

For worker dispatch:

```python
BackendInit(
    ...downstream profile/usage fields...,
    env=self._dispatch_env_for(issue, cfg, is_rewind=...),
)
```

At phase transitions, recompute the correct overlay for the new phase/run context rather than blindly reusing stale environment state.

## 7.5 Make every backend subprocess honor `BackendInit.env`

This is the most important downstream-specific part of the port.

Enumerate every supported backend and every subprocess spawn path:

```bash
rg -n "create_subprocess|Popen|subprocess\.|env=" src/symphony/backends
```

For each backend, ensure the child environment is constructed from inherited process env plus the dispatch-local overlay.

Conceptually:

```python
child_env = os.environ.copy()
# preserve any existing backend-specific env augmentation here
child_env.update(init.env)
```

Then pass:

```python
env=child_env
```

to the subprocess.

### Merge precedence

Preserve existing backend-specific env semantics. The safe rule is:

1. inherited `os.environ`;
2. existing backend-specific/config-derived environment modifications exactly as before;
3. dispatch-local `BackendInit.env` for Symphony dispatch metadata.

This ensures stale inherited `SYMPHONY_*` dispatch values cannot override the current run.

If there is a common subprocess helper that can enforce this correctly for all backends, centralize there. Do not introduce a large unrelated backend refactor solely for this feature.

## 7.6 Downstream backends must all be covered

Do not stop after matching upstream's Codex/Claude/Pi implementation.

Enumerate `SUPPORTED_AGENT_KINDS` and establish a test/checklist for every backend in this fork, e.g. as applicable:

- Codex;
- Claude Code;
- Gemini;
- AGY/Antigravity;
- Kiro;
- OpenCode;
- Pi;
- Prime Agent;
- Copilot;
- any additional kind present when the task is executed.

A backend that ignores `BackendInit.env` is a bug.

## 7.7 Chat must not inherit worker dispatch-local state accidentally

After removing global mutation, Chat subprocesses should naturally stop seeing worker-specific token/rewind values.

Add a regression test if practical:

- worker A gets a rewind scope;
- Chat or worker B starts later/concurrently;
- B's child env has no rewind scope unless B itself is a rewind dispatch.

Update stale comments/docstrings in `chat.py` that claim Chat snapshots env because dispatch mutates process-global state.

## 7.8 Concurrency tests

Tests must prove isolation, not merely inspect helper return values.

At minimum:

### Parent env unchanged

Set sentinel parent values, build two dispatch envs, construct/spawn mocked backends, and verify the relevant parent `os.environ` keys remain unchanged.

### Two concurrent dispatches

Create A and B with different:

- EMA;
- budget;
- rewind scope.

Assert each fake backend/subprocess receives only its own values.

### Forward dispatch after rewind

A rewind dispatch has `SYMPHONY_REWIND_SCOPE`.

A subsequent forward dispatch does **not** have that key.

### Backend contract

Prefer a parameterized contract test across every supported backend/factory. Patch actual process creation and assert the `env=` mapping contains a unique dispatch sentinel.

This test should fail automatically when someone adds a future backend but forgets the env overlay, if the backend registry can be enumerated.

### Phase transition

If stage/profile transition rebuilds the backend, assert the replacement backend receives a fresh correctly resolved env overlay while retaining profile/usage context.

## 7.9 Acceptance criteria

- [ ] No worker dispatch path mutates global `os.environ` for dispatch-local values.
- [ ] `BackendInit.env` coexists with all downstream profile/usage fields.
- [ ] Every backend process receives its own overlay.
- [ ] Rewind scope cannot leak to another dispatch.
- [ ] Concurrent dispatch tests demonstrate isolation.
- [ ] Chat no longer depends on serialized spawn assumptions.

---

# 8. Feature 5 — Chat Intent Gate

## 8.1 Goal

Introduce one explicit human approval boundary between a Chat agent understanding a software request and Symphony creating actionable board work.

The agent may investigate, clarify, and propose an intent. It may **not** file the root request ticket directly through the Chat flow.

Desired control flow:

```text
User request
    |
    v
Chat agent investigates / clarifies
    |
    v
Structured Intent proposal
    |
    v
Server parses + owns proposal state
    |
    v
Human explicitly approves
    |
    v
Server files request ticket + intent artifact
    |
    v
Normal downstream scheduler/profile/usage machinery takes over
```

This feature is a control-plane safety boundary. It is not a new scheduling system.

## 8.2 Scope limits

Implement these upstream Intent Gate components:

- strict intent marker/parser;
- in-memory server-owned action state;
- human confirmation token requirement;
- expiry;
- superseding stale proposals;
- idempotent/concurrency-safe approval;
- server-owned file-board ticket creation;
- `.sdlc/work/<slug>/intent.md` artifact;
- Chat snapshot and WebSocket events;
- REST approval endpoint;
- bare `approve` / `approve <slug>` convenience behavior;
- UI intent card/status/Approve control;
- protocol prompt instructing the Chat agent how/when to propose.

Explicitly **out of scope** for this task:

- upstream deep-project creation;
- automatic deep-preset project bootstrapping;
- replacing downstream web auth;
- automatic fallback from one provider/profile to another;
- Linear/Jira request creation through the Intent Gate unless deliberately added in a separate follow-up;
- persistent recovery of approvable Intent actions across server restart.

## 8.3 Add `src/symphony/intent.py`

Port/adapt the upstream intent model and strict parser.

### `IntentAction`

Model fields equivalent to:

- `action_id`;
- `slug`;
- `title`;
- `track`;
- `intent` markdown;
- `status`;
- filed `ticket` metadata;
- `error`;
- `expires_at`;
- in-flight `task` (not serialized to clients).

Recommended statuses:

```text
pending
running
approved
failed
expired
superseded
```

Terminal-for-pruning statuses should include at least approved/expired/superseded.

Use a bounded action count per Chat session (upstream uses 20) so hostile/broken model output cannot grow server memory indefinitely.

### strict marker

Use exactly one machine-readable wrapper, e.g. upstream's:

```text
<symphony-intent>
{...JSON...}
</symphony-intent>
```

The JSON object should contain only the allowed members:

```json
{
  "slug": "...",
  "title": "...",
  "track": "micro|full",
  "intent": "...markdown..."
}
```

Parser requirements:

- exactly one opening and closing marker;
- reject duplicate JSON keys;
- reject unknown keys;
- require all required keys;
- validate slug with a conservative filesystem-safe regex;
- cap title length;
- cap intent body size (upstream uses ~32 KiB);
- `track` only `micro` or `full`;
- require well-formed intent markdown contract;
- remove the hidden marker from the human-visible Chat message;
- malformed markers must not create an approvable server action.

### intent markdown contract

At minimum require sections:

```markdown
## Problem
...

## Success criteria
- [ ] ...

## Out of scope
...
```

The upstream prompt also encourages:

- Evidence;
- Constraints;
- Open questions.

Keep those as expected/protocol sections, while retaining the strict minimum validation upstream uses.

Require at least one open checkbox in success criteria so approval produces an actionable request rather than prose-only context.

### expiration

Use a bounded TTL (upstream: approximately 30 minutes) from proposal creation.

Expiration is checked again at confirmation time, not only when rendering the card.

## 8.4 Server-owned filing for file tracker

Implement/adapt an `IntentFiler`/`file_intent_request` service.

For this task, approval is supported when:

```text
tracker.kind == file
```

and a file-board root exists.

The filer must:

1. Re-read current config at approval time.
2. Validate that the tracker is still a file tracker.
3. Determine the correct target/request intake state from current workflow configuration.
4. Create the root request ticket through the existing validated `FileBoardTracker` APIs, not by raw ad-hoc file writes.
5. Bind request metadata/slug in the same way upstream expects so downstream DAG/request grouping can operate normally.
6. Write the approved intent artifact to:

```text
.sdlc/work/<slug>/intent.md
```

7. Include useful host-owned provenance such as approval time/session ID if upstream's renderer does so.
8. Return a bounded ticket metadata dict suitable for the UI.

The root ticket becomes normal board input after creation. It must then be subject to the fork's existing:

- profile resolution;
- usage-pool capacity gate;
- dependency scheduling;
- conflict detection;
- run authority.

### File-system safety

The validated slug must be the only user/model-derived path component used below `.sdlc/work`.

Resolve/validate paths so `../`, absolute paths, separators, and Unicode/path tricks cannot escape the project-owned artifact root.

## 8.5 Add Chat errors

In `errors.py`, add downstream equivalents of:

```python
class ChatIntentActionError(SymphonyError):
    code = "chat_intent_action"

class ChatIntentAuthorizationError(SymphonyError):
    code = "chat_intent_confirmation_forbidden"
```

Naming/code should match upstream unless the downstream error taxonomy requires a small adaptation.

## 8.6 Extend `ChatSession`

Add process-local fields:

```python
intent_actions: dict[str, IntentAction]
pending_notices: ...  # if not already present / if needed by upstream flow
```

Important trust rule:

> Do not reconstruct approvable intent authority from the persisted transcript or index after restart.

The transcript shares an editable project tree and is display/history data, not a trusted authority store.

A reattached session may show old messages, but old intent cards must not magically regain approval authority unless the architecture later adds a durable host-owned store.

## 8.7 Add the Chat protocol prompt

Inject a protocol instruction equivalent to upstream's Intent Gate when the configured tracker is a supported file board.

The protocol should tell the Chat agent:

1. Software build/fix/feature/refactor requests must pass through one human gate.
2. Investigate enough to state the problem and evidence.
3. Ask no more than a small bounded number of clarification rounds (upstream uses at most two) before proposing when enough information exists.
4. Explain the proposal in normal human-visible prose first.
5. Then emit exactly one strict intent marker.
6. Do not create the request ticket directly.
7. Use `track: micro` for tightly-scoped work and `track: full` for larger work.
8. Success criteria must be verifiable.

Do not expose server secrets, confirmation tokens, or internal authority mechanics in the model prompt.

For non-file trackers, do not instruct the model to emit an approval mechanism the server cannot file.

## 8.8 Parse model output into server-owned actions

Extend `ChatManager._record_agent_message` (or current equivalent) after existing project-setup parsing.

Flow:

1. Start with raw model text.
2. Preserve existing project setup parsing behavior.
3. If current tracker supports Intent Gate, call `parse_intent_marker()`.
4. Strip the marker from visible model text.
5. If no browser confirmation capability exists for the session, do not retain an approvable action; append a safe human-visible “approval unavailable” notice.
6. A fresh valid proposal supersedes older pending (not running) proposals.
7. Prune only terminal/stale actions when action limit is reached.
8. Do not evict a live approvable/running action merely to make room.
9. Broadcast visible explanation before the action card to preserve reading order/accessibility.
10. Broadcast events for:
   - new intent action;
   - status changes;
   - removed/pruned actions;
   - superseded actions.

Add intent actions to `snapshot()` / session list detail where upstream does so.

## 8.9 Human approval semantics

Add `ChatManager.confirm_intent(...)` with the same authority model already used for downstream project-setup confirmation.

Required checks/order:

1. Resolve live session.
2. Resolve action ID.
3. Hash incoming browser-held confirmation token with the existing helper.
4. Constant-time compare with `session.confirmation_token_hash`.
5. If token missing/mismatched, raise `ChatIntentAuthorizationError`.
6. If already approved, return existing action (idempotent success).
7. If an approval task is already in flight, await/shield the same task rather than filing twice.
8. Reject superseded action.
9. Re-check expiration.
10. Set status `running` and create one async approval task.
11. Shield it from individual HTTP-client disconnect/cancellation.
12. Server-side filer executes in a thread if filesystem/tracker work is blocking.
13. On failure:
    - status `failed`;
    - store bounded/sanitized error;
    - broadcast failure;
    - allow explicit retry while still within TTL.
14. On success:
    - status `approved`;
    - store ticket metadata;
    - broadcast success;
    - request an orchestrator refresh so the ticket can enter normal scheduling promptly.

### Concurrency requirement

Two simultaneous approval requests for the same action must create **one** ticket, not two.

Write a concurrency test for this.

## 8.10 Bare approval reply convenience

Port/adapt `intent_for_reply()` behavior:

- `approve` selects the action only if exactly one live approvable intent exists;
- `approve <slug>` selects one matching live action;
- any other text remains ordinary Chat input;
- ambiguous `approve` remains ordinary/no-op rather than guessing.

In the message POST handler, check project-setup numeric selection first as today, then Intent approval, then ordinary `send_message()`.

## 8.11 REST/API integration with downstream authorization

### Add action-id validation

In `webapi.py`, add a strict route-ID regex such as:

```python
_INTENT_ACTION_RE = re.compile(r"^intent-[0-9a-f]{32}$")
```

and `_check_intent_action_id()`.

### Add explicit approval endpoint

Port an endpoint shaped like:

```text
POST /api/v1/chat/sessions/{session_id}/intent/{action_id}/approve
```

No request fields are required/allowed.

The browser confirmation capability is supplied in the existing header:

```text
X-Symphony-Chat-Confirmation
```

### Preserve downstream web policy

Do not use upstream's old API-token/origin machinery.

The downstream route policy currently classifies mutating `/api/v1/chat/...` routes as at least:

```text
chat + board
```

That is appropriate for filing a board ticket. Verify the new route is classified automatically or explicitly via the current policy-registration mechanism and add a fail-closed policy test.

The Intent approval route should **not** require `projects` merely because project setup does; it writes the current board and `.sdlc/work`, not the global project registry.

The approval still requires the browser-held Chat confirmation token in addition to route authorization.

### Dynamic `approve` through `/message`

Because the message route can turn a bare `approve` into a board mutation, make sure its existing route capability already requires `board`. If policy changes during implementation, retain this property explicitly.

## 8.12 WebSocket integration

Keep the fork's existing single-use WebSocket ticket security unchanged.

New intent events are server-to-client Chat events sent through the existing authenticated/ticketed WebSocket channel.

Do not add a second WebSocket or put a bearer/confirmation token in query parameters.

On initial `hello`/snapshot, include current process-local intent actions for the focused session.

## 8.13 UI integration

Inspect upstream current static assets and port only the Intent Gate UI pieces into the downstream UI structure.

The UI needs to render:

- title;
- track (`micro` / `full`);
- readable intent markdown/summary as supported by current UI;
- status;
- Approve button when approvable;
- disabled/running state while approval is in flight;
- approved ticket identifier/state after success;
- failed status/error and retry affordance when still valid;
- expired/superseded state without an active approval button.

The approval request must include the browser-held confirmation header already used for project setup.

Do not persist that token in server transcript data.

Add accessible labels and avoid relying on color alone for status.

## 8.14 Intent Gate tests

### Parser unit tests

Cover:

- valid marker;
- visible prose is preserved and marker removed;
- duplicate JSON keys rejected;
- unknown JSON keys rejected;
- missing required fields rejected;
- invalid slug rejected;
- oversized title/body rejected;
- invalid track rejected;
- missing required markdown headings rejected;
- no success checkbox rejected;
- duplicate/multiple markers rejected;
- malformed JSON rejected;
- path traversal-like slug rejected.

### Chat manager tests

Cover:

- valid model output creates one server-owned action;
- session with no confirmation capability cannot create approvable authority;
- new pending proposal supersedes previous pending proposal;
- running action is not silently superseded/evicted;
- action pruning remains bounded;
- expiration works;
- stale transcript/reattach does not restore approval authority;
- `approve` unambiguous selection;
- `approve <slug>` selection;
- ambiguous approve does not choose randomly.

### Approval tests

Cover:

- missing token -> forbidden;
- wrong token -> forbidden;
- correct token -> ticket filed once;
- two concurrent approvals -> one filer invocation / one ticket;
- second approval after success -> idempotent same result;
- expired -> conflict/error;
- superseded -> conflict/error;
- filing failure -> `failed`, error surfaced safely;
- retry after transient failure within TTL -> can succeed;
- request refresh called after success.

### API security tests

Cover both auth and capability modes:

- direct approval route lacks `chat` -> denied;
- lacks `board` -> denied;
- has `chat+board` but lacks Chat confirmation token -> forbidden;
- correct API capability + correct browser confirmation -> accepted;
- message endpoint bare `approve` cannot bypass board capability;
- new route is present in route-policy validation;
- existing project-setup route still requires `projects` and behaves unchanged;
- WebSocket ticket behavior unchanged.

### UI/browser tests

If browser E2E infrastructure exists:

- model/event inserts intent card;
- approve button sends confirmation and reaches approved status;
- double-click does not create duplicate ticket;
- expired/superseded cards are not actionable;
- reconnection renders current live action snapshot.

## 8.15 Acceptance criteria

- [ ] Chat agent can propose but cannot self-file a request through this flow.
- [ ] Valid proposal becomes server-owned process-local action state.
- [ ] Approval requires both downstream route authorization and originating-browser confirmation capability.
- [ ] Concurrent approvals are idempotent.
- [ ] Approved intent files exactly one validated file-board request.
- [ ] `.sdlc/work/<slug>/intent.md` cannot escape the project.
- [ ] Approval causes orchestrator refresh.
- [ ] New ticket then uses normal profile/usage/scheduler behavior.
- [ ] Intent authority is not restored from untrusted transcript/index files.
- [ ] Existing project-setup and WebSocket security behavior passes unchanged.

---

# 9. Feature 6 — Extract `attempt.py` / phase-oriented orchestration

## 9.1 Goal

Reduce `orchestrator/core.py` complexity by extracting the body of one worker attempt into:

```text
src/symphony/orchestrator/attempt.py
```

This is primarily a **semantic-preserving extraction**.

Do not use it as an opportunity to redesign downstream profile routing, usage pools, release authority, or worker exit behavior.

Upstream's module should be treated as a structural template, not copied verbatim.

## 9.2 Why verbatim upstream copy is unsafe

The downstream `_run_agent_attempt` currently contains logic that upstream may not have, including custom behavior around:

- named profiles / `AgentSelection`;
- per-stage profile resolution;
- downstream backend kinds;
- usage manager / usage pool;
- provider capacity exhaustion;
- `waiting_provider_usage`;
- downstream API/UI telemetry;
- downstream release/run continuation changes.

Copying upstream `attempt.py` wholesale can silently delete these semantics.

The safe strategy is:

> First characterize the downstream behavior with tests; then move existing downstream code into the upstream phase shape in small mechanical steps.

## 9.3 Desired module boundary

`core.py` remains responsible for broad orchestration/scheduler ownership.

`attempt.py` owns the lifecycle of **one already-dispatched run attempt**.

Target shape:

```text
Orchestrator scheduler/core
        |
        v
_run_agent_attempt(...)
        |
        v
attempt.run_agent_attempt(...)
        |
        +--> startup / authority / workspace context
        +--> backend construction
        +--> backend start + initialize
        +--> session open / prompt setup
        +--> turn loop
        |      +--> pause gate
        |      +--> phase transition
        |      +--> build prompt
        |      +--> run turn
        |      +--> hooks/artifacts/checkpoint
        |      +--> refresh/evaluate result
        |
        +--> deterministic cleanup in finally
        |
        v
existing worker-exit / scheduler handling
```

Do **not** extract `worker_exit.py` as part of this task unless the current target branch has already done so. The requested scope is `attempt.py`.

## 9.4 Characterization before movement

Before creating the module, identify the current `_run_agent_attempt` range and write/strengthen tests for the fragile downstream behaviors.

At minimum characterize:

1. named profile selected for initial stage;
2. profile can change on stage transition;
3. usage pool associated with selected profile reaches `BackendInit`;
4. provider exhaustion sets the expected run/scheduler state;
5. phase transition builds a new backend when required;
6. per-dispatch env reaches initial backend;
7. per-dispatch env reaches phase-transition backend;
8. pause gate behavior;
9. cancellation vs shutdown outcome;
10. token/turn budget exit;
11. issue refresh failure outcome;
12. hooks and artifact collection ordering;
13. continuation checkpoint only after valid completed boundary;
14. final cleanup stops/closes the active backend exactly once;
15. release authority refusal remains unchanged.

Prefer existing tests; add only missing characterization.

## 9.5 Avoid circular imports and preserve monkeypatch seams

Upstream uses a late-bound helper:

```python
def _core():
    from . import core
    return core
```

and `TYPE_CHECKING` for the `Orchestrator` type.

Use the same pattern where necessary because:

```text
core.py -> imports attempt.py
attempt.py -> needs selected core helpers/types
```

More importantly, preserve the repository's documented monkeypatch convention.

If tests currently patch:

```python
symphony.orchestrator.core.some_helper
```

and extracted attempt code starts calling a directly imported original helper instead, those tests/seams may break and runtime injection behavior changes.

For shared helpers intentionally kept in `core.py`, resolve through `_core().helper` or through `orch.method(...)` rather than binding an incompatible direct import.

Do not create circular import side effects at module import time.

## 9.6 Introduce explicit attempt state

Port/adapt upstream's small internal state objects to reduce a huge local-variable list.

A downstream `_AttemptState` will likely need to carry at least:

- `running_issue_id`;
- current `issue`;
- **unrouted/base** workflow `cfg`;
- currently resolved/routed config if distinct;
- current `AgentSelection` / profile identity;
- current usage pool/context as needed;
- workspace/cwd;
- active backend client;
- first/current prompt;
- current kanban state;
- turn number / total turn counters;
- token-budget state;
- debug/event context;
- skills/prompt context;
- any release/continuation context currently local to `_run_agent_attempt`.

Do not over-generalize this into a public API. It is an internal attempt-runner implementation detail.

### Critical routing rule

Keep the **unrouted base config** available for the whole run.

When state changes, re-resolve the downstream stage/profile from the base config. Do not permanently replace the base config with the first stage's resolved config.

Otherwise a run can get stuck on the initial agent profile and ignore later `stage_profiles`/routing changes.

## 9.7 Public entry point and exit normalization

Implement:

```python
async def run_agent_attempt(
    orch: Orchestrator,
    issue: Issue,
    attempt: int | None,
    cfg: ServiceConfig,
) -> None:
    ...
```

`core.Orchestrator._run_agent_attempt()` becomes a tiny delegate:

```python
async def _run_agent_attempt(self, issue, attempt, cfg):
    return await agent_attempt.run_agent_attempt(self, issue, attempt, cfg)
```

Use a small internal `_AttemptExit` value to normalize early-exit outcome + optional error if that matches upstream structure and simplifies the code.

Preserve the exact downstream mapping of exceptions/cancellations to worker outcome.

## 9.8 Phase A — `_start_attempt`

Move startup/authority preparation first.

This phase should preserve the current ordering of operations such as:

- running issue identity capture;
- run/release authority resolution;
- workspace validity/paths;
- continuation eligibility/checkpoint acquisition;
- current issue/config resolution;
- initial profile selection;
- usage-pool context;
- initial backend creation;
- event/debug initialization that must precede spawn.

Do not reorder authority checks after side effects.

### Backend construction requirement

When building `BackendInit`, preserve all downstream fields:

```python
BackendInit(
    cfg=...,
    cwd=...,
    workspace_root=...,
    on_event=...,
    on_process_started=...,
    client_tools=...,
    selection=...,
    resolved_backend_config=...,
    usage_manager=...,
    usage_pool=...,
    env=orch._dispatch_env_for(...),
)
```

The exact field names should match the live branch.

## 9.9 Phase B — `_start_backend`

Extract process lifecycle startup without semantic change:

- synchronize child/agent PID before/after start as currently done;
- `await client.start()`;
- always refresh PID bookkeeping even if start raises;
- `await client.initialize()`;
- preserve error conversion/classification.

Do not move initialization outside the attempt cleanup boundary.

## 9.10 Phase C — `_open_session`

Move session-open preparation:

- initialize turn counters;
- build/render initial prompts;
- resolve language;
- load skills off-loop if current code does so;
- resume/continuation session if eligible;
- set current state/profile metadata;
- log/broadcast session started;
- establish token counters/budgets;
- write any run-registry attempt events in the same order as today.

Do not change prompt content as part of this refactor.

## 9.11 Phase D — `_run_turn_loop`

Move the main loop with explicit helper boundaries.

A useful target based on upstream is:

```text
_run_turn_loop
  -> _honour_pause_gate
  -> _transition_phase (when state changed)
  -> _build_turn_prompt
  -> _run_turn
  -> _after_turn_hooks
  -> _evaluate_turn_result
```

The helper names may vary, but keep the responsibilities similarly narrow.

### `_honour_pause_gate`

Preserve:

- pause is honored at safe turn boundary, not by tearing down an active model turn;
- state is refreshed after resume because operator may have moved the ticket;
- cancellation/shutdown semantics.

### `_build_turn_prompt`

Preserve:

- first-turn vs continuation prompt selection;
- state/stage prompts;
- downstream profile-neutral prompt behavior;
- rewind prompt/scope behavior;
- continuation context;
- token/budget instructions.

### `_run_turn`

Preserve ordering:

- turn-start bookkeeping;
- `before_run` hook on the same turns as today;
- run-registry attempt event;
- backend `run_turn()`;
- usage accumulation;
- provider exhaustion detection;
- outcome classification.

Provider exhaustion must remain distinguishable from generic backend failure.

### `_after_turn_hooks`

Preserve ordering and failure policy for:

- completion log/event;
- `after_run`;
- artifact collection;
- commit/checkpoint updates;
- any downstream audit fields.

### `_evaluate_turn_result`

Preserve:

- authoritative issue refresh;
- release guards;
- terminal state handling;
- state transition detection;
- max turns / total turns;
- token budget;
- empty-response protections;
- continuation/next-turn decision.

## 9.12 Phase transitions are the highest-risk downstream integration

The fork's profile and usage design means a state change may imply a different:

- named profile;
- backend kind;
- model;
- backend command/config;
- usage pool.

On transition:

1. Refresh authoritative issue/current state.
2. Resolve the destination stage/profile from the **base config**.
3. Re-evaluate the destination profile's usage pool according to existing downstream semantics.
4. If the backend/config/profile must change, close the previous backend as current code does.
5. Build a new `BackendInit` carrying the new:
   - `selection`;
   - resolved backend config;
   - usage manager;
   - usage pool;
   - per-dispatch env.
6. Start/initialize/resume according to current backend semantics.
7. Update running-entry profile/backend telemetry.
8. Do not mutate the ticket's explicit pin unless current downstream behavior already does so.

Write a dedicated test in which state A routes to profile X/pool X and state B routes to profile Y/pool Y, then assert the in-run transition actually uses Y rather than reusing X.

## 9.13 Provider usage exhaustion through the extracted runner

Identify every current place `_run_agent_attempt` handles provider capacity or backend-reported quota exhaustion.

Preserve all associated data, including as applicable:

- usage pool identifier;
- reset time;
- provider reason;
- `hit_provider_usage_exhausted` flag/event;
- running-entry fields;
- scheduler exit outcome used to park as `waiting_provider_usage`;
- no destructive retry classification as an ordinary crash.

Add a regression test that fails if quota exhaustion becomes generic `worker_failed` after extraction.

## 9.14 Cleanup must remain deterministic

Use a `try/finally` boundary encompassing backend start/session/turns so cleanup runs on:

- normal completion;
- early return;
- backend exception;
- cancellation;
- shutdown;
- phase-transition half-failure.

Cleanup should:

- close/stop only the currently active backend;
- update PID bookkeeping;
- avoid double-closing already transitioned clients;
- preserve current exception suppression/logging policy for cleanup errors;
- not overwrite the primary attempt outcome with a secondary cleanup error unless that is current behavior.

Add tests for cancellation and backend-start/transition failure.

## 9.15 Keep selected shared helpers in `core.py` initially

Do not chase maximal file-size reduction in one PR.

If a helper is:

- used by scheduler + attempt;
- heavily monkeypatched in tests;
- tied to `Orchestrator` internal state;

leave it in `core.py` and call through `orch` or `_core()`.

The goal is a clear attempt lifecycle boundary, not zero coupling on the first extraction.

## 9.16 Delete the old inline body only after parity tests pass

Suggested mechanical sequence:

1. Create `attempt.py` with state/exit types and wrapper.
2. Move startup block; call it from old method; tests.
3. Move backend start/session-open blocks; tests.
4. Move turn loop; tests.
5. Move phase transition helper; tests.
6. Move hook/result helpers; tests.
7. Make `core._run_agent_attempt` one-line delegate.
8. Remove dead imports/local helper duplicates.
9. Run full suite.

Avoid a single copy/paste of 1,000+ lines followed by debugging dozens of semantic differences.

## 9.17 Refactor-specific tests

In addition to existing suite, require explicit tests for:

- `_run_agent_attempt` delegates to `attempt.run_agent_attempt`;
- import cycle does not occur;
- existing monkeypatch paths still intercept collaborators where promised by project architecture;
- initial named profile preserved;
- stage transition changes named profile correctly;
- usage pool follows profile transition;
- `BackendInit.env` present on initial and transitioned clients;
- provider exhaustion classification preserved;
- pause/resume preserved;
- release-authority early exit preserved;
- continuation checkpoint semantics preserved;
- cancellation maps to same outcome as before;
- shutdown interruption maps to same outcome as before;
- cleanup called once;
- full worker lifecycle integration test emits the same key event sequence before/after refactor.

## 9.18 Acceptance criteria

- [ ] `src/symphony/orchestrator/attempt.py` exists and owns the one-attempt lifecycle.
- [ ] `core._run_agent_attempt` is a thin delegate.
- [ ] Named profile and usage-pool semantics survive extraction.
- [ ] Stage transitions re-resolve from base config.
- [ ] Dispatch env is passed on initial and transition backend construction.
- [ ] Provider-quota exhaustion still reaches `waiting_provider_usage` path correctly.
- [ ] Release/run authority, continuation, hooks, artifacts, budgets, pause, cancellation, and cleanup are unchanged.
- [ ] Existing monkeypatch/test seams are intentionally preserved.
- [ ] No unrelated worker-exit refactor is bundled into this task.

---

# 10. Cross-feature integration tests

After all six stages, add/run integration cases that prove the changes compose.

## 10.1 Concurrent profile dispatch + env isolation

Scenario:

- Ticket A resolves profile `codex-X` and is a rewind dispatch.
- Ticket B resolves another profile/backend and is forward dispatch.
- Both spawn concurrently.

Assert:

- A receives its own EMA/budget/rewind env;
- B receives its own EMA/budget and no rewind env;
- A/B retain their own `usage_pool`;
- parent env is unchanged.

## 10.2 Retry conflict + usage waiting

Scenario:

- A owns `src/common.py` and becomes retry/provider-usage pending.
- B also touches `src/common.py`.

Assert intentional downstream behavior: B remains blocked while A owns pending work.

Then resolve/remove A's retry ownership and assert B can become eligible.

## 10.3 Sibling workflows

Start/test config for two workflow files in one repo.

Assert:

- independent token EMA;
- independent done count;
- no state-file corruption;
- shared project-level stores remain shared where intended.

## 10.4 Intent approval -> normal downstream dispatch

Scenario:

1. Chat emits valid intent.
2. Browser approves.
3. Server files root request.
4. Orchestrator refresh sees it.
5. Normal scheduler evaluates it.

Assert that the resulting ticket is **not special-cased past downstream policy**:

- dependencies apply;
- named profile routing applies;
- usage-pool capacity applies;
- file-conflict gates apply.

## 10.5 Attempt refactor + phase profile transition

Exercise one attempt that transitions between two stages with different profiles and usage pools.

Assert:

- one run attempt identity is preserved according to current fork semantics;
- backend is rebuilt when required;
- profile telemetry updates;
- env overlay remains dispatch-local;
- hooks/checkpoints are still emitted in the expected sequence.

---

# 11. Test and verification command matrix

Use repository-documented commands where they differ, but the implementation should finish with at least:

```bash
python -m ruff check src tests
python -m pyright
python -m pytest -q
```

Run targeted tests after each commit rather than waiting for the end.

Suggested search-driven targeting:

```bash
# Linear
python -m pytest -q $(rg -l "LinearTracker|inverseRelations|LinearUnknownPayload" tests)

# State persistence
python -m pytest -q $(rg -l "token_ema|done_count|atomic_json|state_file_name" tests)

# Retry conflicts
python -m pytest -q $(rg -l "RetryEntry|conflict_blocker|touched_files|retry" tests)

# Dispatch env/backend contracts
python -m pytest -q $(rg -l "BackendInit|dispatch_env|REWIND_SCOPE|TOKEN_EMA|usage_pool" tests)

# Chat Intent Gate
python -m pytest -q $(rg -l "ChatManager|chat.*project|web_policy|chat/sessions" tests)

# Attempt lifecycle
python -m pytest -q $(rg -l "run_agent_attempt|transition_agent_phase|continuation|provider_usage" tests)
```

Be aware shell command substitution may produce no files or too many arguments depending on the test layout; adapt intelligently.

If browser tests are available, run the Chat-related E2E subset with the repository's documented environment flag after unit/API tests pass.

Also run:

```bash
symphony doctor ./WORKFLOW.md
```

if the repository's own workflow/config is expected to remain valid.

---

# 12. Commit-by-commit definition of done

## Commit 1 — Linear

- new query shape in every relevant query;
- local relation type filtering;
- incomplete relation fail-closed;
- Linear regression tests green.

## Commit 2 — state

- `utils/atomic_json.py`;
- canonical state name compatibility;
- sibling workflow namespacing;
- token EMA + done-count writers converted;
- state tests green.

## Commit 3 — retry conflicts

- `RetryEntry.touched_files`;
- capture/preservation across retry scheduling;
- retry registry participates in conflict checks;
- usage-wait behavior tested;
- scheduler tests green.

## Commit 4 — dispatch env

- `BackendInit.env` added without removing downstream fields;
- no `_apply_dispatch_env` global mutation remains;
- every backend subprocess honors overlay;
- initial/transition/improvement paths supply env;
- concurrency/backend contract tests green.

## Commit 5 — Intent Gate

- `intent.py` + strict parser/model;
- Chat protocol/action lifecycle;
- confirmation/idempotent filing;
- API route + downstream policy tests;
- WebSocket/snapshot events;
- UI card/action;
- file tracker only;
- no deep-project/auth scope creep;
- Chat/API/browser tests green.

## Commit 6 — attempt extraction

- characterization tests added first;
- `attempt.py` owns lifecycle;
- `core` delegates;
- downstream profile/usage/env/release/continuation semantics preserved;
- full test/lint/type suite green.

---

# 13. Review checklist for downstream regressions

Before declaring completion, inspect the final diff specifically for these red flags.

## 13.1 Profile regressions

- [ ] No removal of `selection` from `BackendInit`.
- [ ] No removal of `resolved_backend_config`.
- [ ] No removal of `usage_manager` / `usage_pool`.
- [ ] No path bypasses profile resolver and falls back blindly to `cfg.agent.kind` where a profile is expected.
- [ ] Phase transition does not pin the first profile forever.

## 13.2 Usage regressions

- [ ] `waiting_provider_usage` still exists and is scheduler-visible.
- [ ] Provider exhaustion remains distinct from crash/failure.
- [ ] No running worker is forcibly killed just because usage becomes exhausted unless pre-existing semantics explicitly do that.
- [ ] Usage-pool reset/remaining telemetry still propagates.

## 13.3 Backend regressions

- [ ] All supported backends still instantiate.
- [ ] Copilot/downstream-only backends receive `BackendInit.env`.
- [ ] Existing resume-across-turns differences remain intact.
- [ ] Backend command/model/profile resolution unchanged except env isolation.

## 13.4 Web/auth regressions

- [ ] `web_policy` modes unchanged.
- [ ] No bearer token added to Chat WS URL.
- [ ] New Intent route has explicit/fail-closed policy metadata.
- [ ] Bare `approve` cannot bypass `board` capability.
- [ ] Browser confirmation token still required.
- [ ] Project setup still requires its stronger `projects` authorization where it did before.

## 13.5 Persistence regressions

- [ ] `WORKFLOW.md` continues reading legacy token/done files.
- [ ] Sibling workflow state no longer collides.
- [ ] Project-wide stores were not accidentally namespaced.

## 13.6 Scheduler regressions

- [ ] Running conflicts still work.
- [ ] Retry-pending conflicts now work.
- [ ] Dependency, capacity, starvation, lease, and provider-usage gates keep their established order/meaning.

## 13.7 Attempt lifecycle regressions

- [ ] No helper moved in a way that bypasses monkeypatch seams unintentionally.
- [ ] cleanup remains in `finally`.
- [ ] cancellation outcome unchanged.
- [ ] hooks order unchanged.
- [ ] checkpoints only after completed boundaries.
- [ ] release authority remains checked before unsafe side effects.

---

# 14. Implementation-agent working rules

The Sol-5.6 agent executing this plan should follow these rules.

## 14.1 Prefer behavior-preserving ports over source-level similarity

If upstream and downstream structures differ, implement the **upstream invariant** using downstream architecture.

Example:

- upstream has a simpler `BackendInit`;
- downstream has profile/usage fields;
- correct port = add `env` to downstream object, not replace downstream object with upstream's dataclass.

## 14.2 Read the complete local function before editing

Do not patch a snippet from this plan based only on a line match. The fork contains downstream logic around the same sections.

For high-risk functions such as `_run_agent_attempt`, `_transition_agent_phase`, and Chat message processing, read the full function and its callers first.

## 14.3 Use upstream as a diff reference

Useful commands after fetching upstream:

```bash
git show upstream/main:src/symphony/trackers/linear.py
git show upstream/main:src/symphony/utils/atomic_json.py
git show upstream/main:src/symphony/intent.py
git show upstream/main:src/symphony/orchestrator/attempt.py

git diff HEAD..upstream/main -- src/symphony/trackers/linear.py
git diff HEAD..upstream/main -- src/symphony/chat.py
git diff HEAD..upstream/main -- src/symphony/webapi.py
git diff HEAD..upstream/main -- src/symphony/orchestrator/core.py
```

Do not interpret unrelated diff hunks as part of this task.

## 14.4 Test before and after each semantic move

Especially during `attempt.py` extraction:

```text
move one coherent phase -> targeted tests -> inspect diff -> next phase
```

Do not defer all validation until the module has been fully rewritten.

## 14.5 Avoid speculative cleanup

Do not in this task:

- rename public API endpoints unrelated to Intent;
- redesign all backend classes;
- replace retry registry;
- change workflow YAML schema;
- add automatic profile fallback;
- port upstream deep-preset project creation;
- port unrelated release fixes;
- extract `worker_exit.py` unless already present and required by the live branch;
- upgrade dependencies solely because upstream did.

Create follow-up notes for attractive unrelated work.

## 14.6 Leave a completion report

At the end, produce a concise implementation report containing:

1. commits created;
2. files changed per feature;
3. important downstream adaptations vs upstream;
4. tests added;
5. exact verification commands and results;
6. any pre-existing failing tests;
7. follow-up items intentionally left out of scope.

---

# 15. Source/reference map

These are the principal upstream/downstream reference files as of the planning baseline. Re-fetch `upstream/main` before implementation because exact line numbers may move.

## Downstream

- Repository: <https://github.com/NovoG93/oh-my-symphony/tree/develop>
- Linear adapter: <https://raw.githubusercontent.com/NovoG93/oh-my-symphony/refs/heads/develop/src/symphony/trackers/linear.py>
- Backend base/init: <https://raw.githubusercontent.com/NovoG93/oh-my-symphony/refs/heads/develop/src/symphony/backends/base.py>
- Orchestrator core: <https://raw.githubusercontent.com/NovoG93/oh-my-symphony/refs/heads/develop/src/symphony/orchestrator/core.py>
- Orchestrator entries: <https://raw.githubusercontent.com/NovoG93/oh-my-symphony/refs/heads/develop/src/symphony/orchestrator/entries.py>
- Chat: <https://raw.githubusercontent.com/NovoG93/oh-my-symphony/refs/heads/develop/src/symphony/chat.py>
- Web API: <https://raw.githubusercontent.com/NovoG93/oh-my-symphony/refs/heads/develop/src/symphony/webapi.py>
- Web policy: <https://raw.githubusercontent.com/NovoG93/oh-my-symphony/refs/heads/develop/src/symphony/web_policy.py>
- Errors: <https://raw.githubusercontent.com/NovoG93/oh-my-symphony/refs/heads/develop/src/symphony/errors.py>

## Upstream references to port selectively

- Repository: <https://github.com/cskwork/oh-my-symphony/tree/main>
- Linear adapter: <https://raw.githubusercontent.com/cskwork/oh-my-symphony/refs/heads/main/src/symphony/trackers/linear.py>
- Backend base/init: <https://raw.githubusercontent.com/cskwork/oh-my-symphony/refs/heads/main/src/symphony/backends/base.py>
- Atomic JSON helper: <https://raw.githubusercontent.com/cskwork/oh-my-symphony/refs/heads/main/src/symphony/utils/atomic_json.py>
- Intent model/parser/filer: <https://raw.githubusercontent.com/cskwork/oh-my-symphony/refs/heads/main/src/symphony/intent.py>
- Chat Intent implementation: <https://raw.githubusercontent.com/cskwork/oh-my-symphony/refs/heads/main/src/symphony/chat.py>
- Web API Intent routes: <https://raw.githubusercontent.com/cskwork/oh-my-symphony/refs/heads/main/src/symphony/webapi.py>
- Orchestrator attempt module: <https://raw.githubusercontent.com/cskwork/oh-my-symphony/refs/heads/main/src/symphony/orchestrator/attempt.py>
- Orchestrator core: <https://raw.githubusercontent.com/cskwork/oh-my-symphony/refs/heads/main/src/symphony/orchestrator/core.py>

Use `git log upstream/main -- <file>` and `git blame upstream/main -- <file>` to locate the precise upstream commits if commit-level provenance is useful during implementation.

---

# 16. Final Definition of Done

The complete task is done only when all of the following are true:

### Linear

- [ ] Current relation schema used.
- [ ] Local `blocks` filtering used.
- [ ] Truncated blocker data fails closed.

### Persistence

- [ ] Workflow-specific token EMA/done-count files are namespaced.
- [ ] Canonical `WORKFLOW.md` filenames are backward compatible.
- [ ] Writes are atomic with bounded `PermissionError` retry and temp cleanup.

### Retry conflicts

- [ ] Retry entries retain touched-file ownership.
- [ ] Conflict scheduler considers pending retries.
- [ ] Downstream usage-wait interaction is tested.

### Dispatch env

- [ ] Parent env no longer carries dispatch-local worker state.
- [ ] Every supported backend receives its own env overlay.
- [ ] Profile/usage fields survive `BackendInit` changes.
- [ ] Rewind scope cannot leak.

### Intent Gate

- [ ] Strict proposal parser exists.
- [ ] Human-visible response excludes hidden marker.
- [ ] Server owns approvable state.
- [ ] Browser confirmation token required.
- [ ] Downstream `chat+board` authorization still applies.
- [ ] Concurrent approvals file once.
- [ ] File-board ticket + intent artifact created server-side.
- [ ] UI and WebSocket states work.
- [ ] No upstream auth/deep-project scope creep.

### Attempt extraction

- [ ] `attempt.py` owns one-attempt lifecycle.
- [ ] `core._run_agent_attempt` delegates.
- [ ] Initial and transition profile resolution works.
- [ ] Usage-pool/provider exhaustion semantics preserved.
- [ ] Per-dispatch env works through transitions.
- [ ] release/continuation/hooks/artifacts/pause/cancel/cleanup semantics preserved.

### Quality gate

- [ ] Targeted tests pass after each commit.
- [ ] Full `pytest` passes, aside from explicitly documented pre-existing failures.
- [ ] Ruff passes.
- [ ] Pyright passes.
- [ ] Browser Chat tests pass when the repository's browser test environment is available.
- [ ] `symphony doctor ./WORKFLOW.md` remains clean where applicable.
- [ ] Final diff contains no unrelated upstream merge noise.

---

# 17. Short instruction to the implementing Sol-5.6 agent

Implement this plan sequentially on the downstream `develop` codebase. Fetch upstream `main` and use it as a reference, but do not merge it. Preserve downstream named-agent-profile, usage-pool, backend, authorization, continuation, and release semantics. Write regression tests before/high-risk refactors, especially before extracting `_run_agent_attempt`. Commit each of the six stages independently, run targeted tests after each stage, then run the complete lint/type/test suite. If upstream code conflicts with downstream architecture, preserve the behavior described in this plan using downstream abstractions rather than copying upstream source literally.
