# Adversarial Review — `minimal` branch (pre-minimal @ 89e2de9 → 47fe8f2)

**Reviewer stance:** hostile senior reviewer, fresh context, production-release gate.
**Scope:** 18 commits, 607 files, +6,366 / −143,940 lines.
**Baseline verified locally at HEAD 47fe8f2 (clean tree):**

```
.venv/bin/python -m pytest -q -p no:cacheprovider   → 1614 passed, 7 skipped in 78.55s
.venv/bin/python -m ruff check src tests            → All checks passed!
.venv/bin/python -m pyright                         → 16 errors (14 = `textual` missing in this venv,
                                                       2 = pre-existing ruamel stub complaints in
                                                       mutate.py, identical at pre-minimal) — env limit,
                                                       not a branch regression
.venv/bin/python scripts/check_i18n.py              → FAIL: 5 problem(s)   ← see F-24
```

Review axes are the ones briefed: agent freedom (P1), removal correctness, new-feature
coherence, acceptance findings 1–4, docs truth, test honesty.

---

## Executive verdict

**DO NOT SHIP AS-IS. Conditional GO after the six blockers below are fixed.**

The deletion work (flow engine, board-viewer, debris) is genuinely clean: I hunted for
dangling references across `src/`, `tests/`, docs, skills and CI, and found only three
real leftovers (F-22, F-25, F-26), none of them load-bearing. The suite is green, ruff is
clean, and the Learn→Document rename is thorough and correctly aliased in every code path
I could find (contracts, rewinds, prompt-context, skip endpoint, TUI, web UI, tracker).

The problem is the **new** code. Three of the four headline features shipped in this branch
have a defect that makes the feature either inoperative or unsafe in the exact configuration
the README advertises:

* `agent.stage_kinds` (per-stage backend routing) **does not route on in-run stage
  transitions** — and the in-run transition is how the default 4-lane board actually works.
  I proved this with a probe test: three backend builds across an `In Progress → Verify`
  transition, all `claude`, with `stage_kinds["verify"] = "gemini"` configured (F-01).
* The **deep (8-lane) preset** has never been executed by an orchestrator — not in a test,
  not in acceptance run A or B. Reading it against the merge/worktree machinery, its Build
  lane ends at `Done`, which either merges unverified slices into the target branch or
  leaves the Verify/QA tickets looking at a base branch without the code they must re-prove
  (F-05). It is shipped as a first-class, UI-switchable option.
* The **board tool** that every prompt and the chat preamble now mandate (`NEVER hand-write
  ticket markdown files`) validates less than the web API it is supposed to mirror: I wrote
  a ticket to `/private/tmp/evil.md` from a board rooted at `/private/tmp/bt/board` with a
  one-line command (F-03). It also has no `update` verb, while `verify.md` instructs agents
  to add ids to an existing ticket's `blocked_by` (F-09).

Against **P1 (agent freedom)** the branch is in good shape overall — the stage prompts are
ownership boundaries (a lane says what it writes), not capability cages, and I judge almost
all of them legitimate gates. Two exceptions are real defects (F-07, F-08), and one is a
capability *loss* the operator never asked for: the bootstrap-recommended `WORKFLOW.example.md`
gives the claude worker neither `--permission-mode` nor `--add-dir`, which is precisely the
configuration that produced acceptance finding 1 (F-18).

Against **P2 (minimal but powerful, boards customizable)** there is one silent trap: renaming
any default lane silently disables the entire stage-contract evidence floor, with no log line,
no doctor warning, no UI hint (F-06).

Against **P3 (production-ready)** the branch is close but the docs oversell in a few precise
places, and acceptance findings 1–4 all have identifiable root causes in shipped code, not
just "hardening ideas" (see §4).

**Counts:** 6 HIGH · 18 MED · 8 LOW = 32 findings.

---

## Findings table

| id | sev | file:line | finding | proposed fix |
|---|---|---|---|---|
| F-01 | HIGH | `src/symphony/orchestrator/core.py:3904`, `:4226` | `agent.stage_kinds` is resolved once per *dispatch* (`_config_for_issue_agent`) and `_rebuild_backend_for_phase` reuses that already-routed `cfg`. A ticket that walks Todo→In Progress→Verify→Document in one dispatch (the normal 4-lane path) keeps the **first** lane's backend for every later lane. Proven: probe test with `stage_kinds={"in progress":"claude","verify":"gemini"}` logged `worker_phase_transition to_state=verify` and still produced `[claude, claude, claude]` factory calls. The README's own example (`Document: gemini`) therefore never fires. | Pass the *unrouted* workflow cfg into the turn loop and re-resolve at every phase transition: `phase_cfg = _config_for_issue_agent(base_cfg, issue)` immediately before `_rebuild_backend_for_phase`, and use `phase_cfg` for the backend build, `entry.agent_kind`, and the token/EMA bookkeeping. Add an integration test asserting the second factory call's `agent_kind` after a transition. |
| F-02 | HIGH | `src/symphony/orchestrator/core.py:6149` | `_reconcile_running` computes the stall budget from `cfg.backend_timeouts()`, which keys off `cfg.agent.kind` — the *workflow default* backend. A ticket pinned to (or stage-routed to) another backend is stall-cancelled on the wrong backend's `stall_timeout_ms`. With `agent.kind: codex` (300 s) and a claude-pinned ticket configured at 900 s, the reconciler kills the claude worker at 300 s. This is the mechanism behind acceptance finding 2. | Resolve per entry: `_, _, stall_ms = _config_for_issue_agent(cfg, entry.issue).backend_timeouts()` inside the per-entry loop (or from `entry.agent_kind`, which is already tracked). Add a regression test with two backends and different stall budgets. |
| F-03 | HIGH | `src/symphony/cli/board.py:175` vs `src/symphony/webapi.py:73,332` | `symphony board new` never validates the identifier; `FileBoardTracker.create` joins it straight into a path. Verified: `symphony board new "../../evil" "escape" --root board` → `created /private/tmp/bt/board/../../evil.md` (file lands outside the board root, invisible to the tracker). `symphony board new "A 5; echo pwned" …` also succeeds. The web API applies `_IDENTIFIER_RE` to the same operation. Agents are now *instructed* to call this CLI with model-generated ids. | Move `_IDENTIFIER_RE` into `trackers/validate.py` as `validate_identifier()`; call it from `cmd_new` **and** from `FileBoardTracker.create`/`update_fields` (defence in depth, since `create_with_next_identifier` and CI registrars also flow through it). Test: traversal, spaces, empty, 64+ chars. |
| F-04 | HIGH | `scripts/symphony-setup-worktree.sh:152`, `src/symphony/orchestrator/helpers.py:53`, `src/symphony/workflow/constants.py:33` | The after_create hook links only a directory literally named `kanban` (`for dir in kanban; do`), but `board_root` is free-form and the CLI's own fallback is `DEFAULT_BOARD_ROOT_NAME = "board"`. On any other board root the worker gets **no** link, silently creates a local board copy, and the orchestrator re-dispatches forever. No env var carries the board root to hooks (`_branch_hook_env` exports only branch names), and no doctor check exists. This is acceptance finding 1, verbatim. | (a) `_branch_hook_env` also exports `SYMPHONY_BOARD_ROOT` (absolute) and `SYMPHONY_BOARD_ROOT_NAME` (path relative to the workflow dir). (b) Script: `for dir in ${SYMPHONY_BOARD_ROOT_NAME:-kanban}; do`. (c) `WorkspaceManager.create_or_reuse`: after hooks, assert `(workspace/board_name).resolve() == board_root.resolve()` when the host board lives inside the workflow dir; on mismatch fail the dispatch with a named error instead of running a doomed turn. (d) doctor: new `check_board_reachable_from_workspace`. Full design in §4.1. |
| F-05 | HIGH | `src/symphony/workflow/presets.py:70`, `docs/symphony-prompts/file/deep/build.md:21`, `docs/symphony-prompts/file/deep/verify.md` | The deep preset has no merge/branch story. Each lane is a separate ticket → separate worktree → separate `symphony/<ID>` branch. `build.md` ends at `Done`, so with `auto_merge_on_done: true` an **unverified** slice merges into the target before QA-1 and VERIFY-1 exist; with it `false`, VERIFY-1/QA-1 get worktrees cut from the base branch that do not contain BUILD-1's code, so "re-prove every claim" re-proves nothing. Neither constraint is documented anywhere. No orchestrator-level test and no live acceptance run covers the deep preset (runs A and B are both 4-lane). | Decide and document the contract. Minimum for release: (a) state in `README#lane-presets`, `presets.py` docstring and `deep/base.md` that the deep preset requires `auto_merge_on_done: true` with `feature_base_branch == auto_merge_target_branch`, and that Build merges are gated by the Review lane's PASS, not by Verify; (b) add `verdict:`-gated merge language to `deep/verify.md` so a RED verdict blocks further merges; (c) add one orchestrator e2e test (mock backend) walking Intake→…→Document across spawned tickets. Otherwise mark the preset **experimental** in UI + README for this release. |
| F-06 | HIGH | `src/symphony/orchestrator/contracts.py:165`, `core.py:4083` | `board_uses_default_contracts` gates the whole contract validator on lane *names*. Rename `Document` → `Docs` (a first-class P2 operation, offered in the UI) and every mechanical evidence gate silently disappears — no log, no doctor line, no UI badge. Conversely a 2-lane board named `Todo`/`In Progress` with fully rewritten prompts still gets the shipped section list enforced and will rewind on sections its prompts never requested. | Make it explicit and observable: add `agent.stage_contracts: auto|on|off` (default `auto` = today's heuristic); log once per config load when contracts are auto-disabled (`stage_contracts_disabled` with the offending lanes); surface it in `symphony doctor` and in `GET /api/v1/workflow`. Keep the name heuristic only as the `auto` default. |
| F-07 | MED | `docs/symphony-prompts/file/stages/document.md:3` (+ `linear/` mirror) | **P1 freedom violation.** "Do NOT edit source, **do NOT run git history commands**, and do NOT run the Merge Gate here". Banning read-only `git log/show/diff` serves no gate — the lane's job is literally "compare brief vs reality", which needs the diff. The merge/commit ban is a legitimate ownership boundary; the history-read ban is a cage. | Rewrite as: "Do not create commits, tags, branches, or pushes — the host gate owns delivery history. Read-only inspection (`git log`, `git show`, `git diff`) is expected." |
| F-08 | MED | `docs/symphony-prompts/file/stages/document.md:3` vs `presets.py:53`, plan §Document | The 4-lane Document prompt says "Write wiki files and ticket comments **only**. Do NOT edit source." — but the lane's advertised charter (preset description "Docs + wiki write-back", plan item 5, `deep/document.md:12`) is to update README / CHANGELOG / policies touched by the change. As written the default board can never update user-facing docs. | Replace with a positive write scope: "Write: `docs/llm-wiki/`, the user-facing docs the change touched (README/CHANGELOG/config docs), and ticket comments. Do not change behaviour (no source/test edits)." Add a step 1.5 mirroring `deep/document.md:12`. |
| F-09 | MED | `src/symphony/cli/board.py` (no `update` subcommand) vs `docs/symphony-prompts/file/stages/verify.md:14`, `chat.py:122` | `verify.md` tells the agent to "register new bug tickets … **add their IDs to `blocked_by`**" of the current ticket; `deep/verify.md`/`qa.md` tell agents to set another ticket back to `Build`. There is no `symphony board update/mv --blocked-by`, so the only path is hand-editing frontmatter — which the chat preamble explicitly forbids ("NEVER hand-write ticket markdown files") and which bypasses cycle validation entirely. | Add `symphony board update <id> [--state] [--blocked-by ID ...] [--add-blocked-by ID] [--request]` routed through `validate_ticket_dependencies(new_ticket=False)` + `FileBoardTracker.update_fields`. Update `verify.md` / `deep/*.md` to name it. |
| F-10 | MED | `src/symphony/orchestrator/core.py:5571,6342` + `docs/symphony-prompts/file/stages/verify.md:19` | Two merge paths. When `agent.auto_merge_on_done` is true the Verify prompt tells the agent to create the `--no-ff` merge commit **and** the orchestrator merges the same branch again at Done. Plan v2 decided the opposite ("single merge story = orchestrator `auto_merge` on transition; Verify prompt no longer merges by hand"). Practical effects: two merge commits per ticket (or a confusing `nothing_to_apply`), and code reaching the target branch *before* the Document lane runs — which is also why the wiki write-back lands inconsistently (acceptance finding 4). | Make Verify prove-only: replace step 8 with the `git merge-tree --write-tree` preflight + `## Merge Status: preflight clean, orchestrator will merge at Done`, and drop the hand-merge. Keep the `{% if agent.auto_merge_on_done %}` branch text describing who merges. This also fixes F-27/§4.4 for free. |
| F-11 | MED | `src/symphony/workflow/mutate.py:612` (`_ensure_preset_prompt_files`) | Applying a preset on a board that does not carry the shipped prompt files writes **placeholder stubs** ("Do the work this column stands for…") and reports success. That is the normal state of any bootstrapped repo that predates this branch (no `docs/symphony-prompts/file/deep/`, no `stages/document.md`). The board silently degrades from gated pipeline to "do something". The test (`test_apply_lane_preset_creates_missing_prompt_files`) asserts only `is_file()`, so it locks the defect in. | Ship the preset prompt bodies as package data (`[tool.setuptools.package-data] symphony = [... "prompts/**"]`) and copy the real text; or, if that is too big for this release, refuse the apply with an actionable `WorkflowMutationError` naming the missing files and the `cp -R docs/symphony-prompts` command. Strengthen the test to assert a distinctive line from the shipped prompt. |
| F-12 | MED | `src/symphony/orchestrator/core.py:3253` (`_run_improvement_agent`) | Agent-driven CI modes run a **full edit-mode backend in the host repo root** (`cwd == workspace_root == workflow_dir`) with the read-only contract enforced by prompt text only (the docstring admits it). The readiness checks go to lengths to use a clean temp worktree; the agent turn does not. `require_idle_board` is checked at schedule time only, so a worker dispatched during the (possibly long) run can collide with the agent in the host tree. | Snapshot `git status --porcelain` (+ untracked list) before and after the turn; if anything outside `.symphony/continuous-improvement/proposals/` changed, discard the proposals for that mode, log `ci_agent_wrote_outside_contract` with the paths, and record `status="not_proven"`. Better: run the turn in the same throwaway worktree `_prepare_baseline` already builds. |
| F-13 | MED | `src/symphony/orchestrator/core.py:3057`, `src/symphony/trackers/file.py:672` | A **dangling** `blocked_by` id (typo by an agent, deleted ticket) hydrates to `state=None`, `_blocker_dependency_is_resolved(None)` returns False, and the ticket is never dispatched again. The only signal is a `dangling_blocked_by` WARN in the log and a generic "waiting on unresolved dependency" card badge that does not say the blocker does not exist. Permanent silent deadlock on a board whose ids are now written by models. | (a) Card attention text: "blocker `X` is not on the board" when the id is unknown. (b) New doctor check `board.dependencies` reporting dangling blockers and cycles (reuse `dangling_blockers` / `find_cycle`). (c) Optional: after N consecutive polls with a dangling blocker, append `## Blocker` and move to `Blocked` so a human/`blocked_fixes` sees it. |
| F-14 | MED | `src/symphony/web/static/app.js:1050-1075,1108-1145` | Plan Phase 3 items 2 and 5 ("per-request progress on the web board", "dep list rendering in web board + TUI card badge") are **not delivered on the web side**: cards render no `blocked_by` and no `request`, and the create/edit forms cannot set either. The API already returns both fields (`webapi.py:214-217`), so they are dead payload. The TUI half exists (`tui/widgets.py:237`). | Render a `⛓ blocked by X, Y` chip and a `REQ-n` chip on the card; add `blocked_by` (comma list) + `request` inputs to the create modal and drawer, posting through the already-validating endpoints. Extend `test_web_static_contract.py` accordingly. |
| F-15 | MED | `src/symphony/cli/board.py:173-186`, `src/symphony/webapi.py:609,705` | Validate-then-write is not atomic. `validate_ticket_dependencies(scan_all())` runs outside the board lock; `create` takes only a *per-ticket* lock. Two concurrent `symphony board new` calls (the deep Plan lane spawns several in a row, and multiple workers may spawn sub-tickets in parallel — an explicitly designed capability, plan appendix #4) can each observe an acyclic board and jointly create a cycle, or race the `create_with_next_identifier` allocator against an explicit id. | Take the existing board-level allocator lock (`_exclusive_lock(self._allocator_lock_path())`) around validate+create; expose it as `FileBoardTracker.create_validated(..., issues_snapshot_fn)` so CLI and web API share one atomic path. |
| F-16 | MED | `src/symphony/continuous_improvement.py:1562-1565` | `for mode in due: mode_state[mode] = now_epoch` stamps the cadence for **every** due mode regardless of outcome, including `not_available` (no agent runner injected) and `not_proven` (baseline dirty, exception). A weekly `market_research` that could not run waits another week. | Only stamp modes whose outcome is `passed` (or `failed`, which is a real result); leave `not_available` / `not_proven` unstamped so the next heartbeat retries. Add a test with an outcome matrix. |
| F-17 | MED | `src/symphony/continuous_improvement.py:935` (`_link_blocker`) | `blocked_fixes` adds `fix → blocks source` and stops. The source stays in `Blocked`/`Human Review` forever: nothing moves it back when the fix ticket reaches Done. The mode's own proposal text promises "then hand it back to the pipeline" and its acceptance criterion is "`X` can leave its stuck state" — which the machinery cannot do. | Either (a) have the readiness/blocked_fixes pass reopen a `Blocked` source whose every blocker is now resolved (move to `_blocked_source_reopen_state(cfg)` with an audit note — the helper already exists at `core.py:339`), or (b) reword the proposal so the fix ticket owns the reopen step explicitly and add it to the ticket body's acceptance criteria. |
| F-18 | MED | `WORKFLOW.example.md:268` vs `WORKFLOW.file.example.md:264`, `skills/…/bootstrapping.md:13` | Bootstrap tells operators to `cp WORKFLOW.example.md "$TARGET/WORKFLOW.md"` — the **linear** example, whose claude command is bare `claude -p --output-format stream-json --verbose`. Every configuration that actually works (`WORKFLOW.md`, `WORKFLOW.file.example.md`, `examples/WORKFLOW.demo.claude.md`, `examples/WORKFLOW.smoke.md`) adds `--permission-mode acceptEdits --add-dir "$SYMPHONY_WORKFLOW_DIR/…"`. A fresh bootstrap therefore ships a claude worker that cannot accept edits and cannot write through the board junction — the same class of failure as acceptance finding 1. `skills/…/oneshot/templates/WORKFLOW.oneshot.md:51` has the same gap. | Align the claude command in `WORKFLOW.example.md` and the oneshot template with the file example; make `bootstrapping.md` branch on tracker kind (`file` → `WORKFLOW.file.example.md`, `linear` → `WORKFLOW.example.md`). |
| F-19 | MED | `src/symphony/chat.py:118-124`, `docs/symphony-prompts/file/deep/plan.md:14` | Prompts and the chat preamble now *require* `symphony board new` to be on the agent's `PATH`, with no check anywhere. Symphony is typically installed in a venv and launched by absolute path (`.venv/bin/symphony`, or via `symphony service start`, which spawns with `sys.executable -m`); the spawned agent inherits the orchestrator's `PATH`, which need not contain the venv `bin`. If it does not, the intake protocol fails with the one fallback the preamble forbids. Additionally, claude under `--permission-mode acceptEdits` still gates Bash calls, so the Plan lane's `symphony board new` may be denied outright. | (a) Export `SYMPHONY_CLI` (resolved `sys.argv[0]` / `shutil.which("symphony")`) into the dispatch env and reference it in the preamble/prompts (`"${SYMPHONY_CLI:-symphony} board new …"`). (b) New doctor check: `symphony` resolves on PATH from a `bash -lc` subshell. (c) Note in README/skills that boards using the CLI-driven lanes need a permission mode that allows Bash. |
| F-20 | MED | `src/symphony/orchestrator/core.py:3783` | When `agent.stage_kinds` is non-empty the orchestrator stops stamping `agent.kind` onto **every** ticket, so on a routed board no ticket ever records which backend ran it (board UI, audits, Done history all lose the field). The comment explains why the *pin* must not be written; it throws away the audit value with it. | Write the resolved kind to a non-pin field (e.g. frontmatter `last_agent_kind`, or a `## Dispatch` note line) that `_requested_agent_kind` never reads, and surface that in `_issue_card`. |
| F-21 | MED | `docs/symphony-prompts/file/base.md:65` | Base prompt orders every worker to "Record non-trivial decisions in `docs/changelog/changelog-YYYY-MM-DD.md`" — a convention this very branch deleted from the repo (`docs/changelog/` archives removed in 2e1ff6a) and that no other doc, gate, or contract mentions. Workers burn turns creating an unread directory in the user's repo. | Delete the line, or fold it into the Document lane's wiki decision-log step which already covers it (`document.md:6` / `deep/document.md:12`). |
| F-22 | MED | `tui-open.sh:106-119` | The shipped launcher still tries to start `tools/board-viewer/server.py`, writes `log/board-viewer.log`, and prints "board-viewer starting at http://127.0.0.1:8765/". Guarded by `-f`, so it is a silent no-op — but it is a live reference to a deleted subsystem in an operator-facing script and it contradicts `skills/…/operations.md:95` ("there is no separate board-viewer process"). | Delete the block and the `BV_PORT` plumbing; if the intent is to open a browser, point it at the orchestrator port. |
| F-23 | MED | `docs/symphony-prompts/file/deep/*.md` (all), `tests/test_workflow_presets.py:85` | The deep preset's only tests are prompt-text greps. There is no test that a deep board loads, dispatches, spawns its DAG, or that `apply_lane_preset('deep')` produces a board `build_service_config` accepts (e.g. `agent.max_turns >= len(active_states)` preflight with 8 lanes — `preflight` rejects `max_turns < active states`, and the shipped examples use `max_turns: 100`, but nothing checks after a preset switch). | Add: (1) a preflight/round-trip test loading the workflow *after* `apply_lane_preset('deep')`; (2) a mock-backend orchestrator test for one full deep request. Consider validating `max_turns` inside `apply_lane_preset` and warning in the API response. |
| F-24 | MED | `scripts/check_i18n.py` (fails at HEAD), `.github/workflows/tests.yml` | The repo's own i18n checker exits non-zero at HEAD: 5 `workflow.ciMode.*` keys are "defined but never used" because `app.js:2700` builds them dynamically (`t(\`workflow.ciMode.${mode}\`)`). It is also not wired into CI, so nobody noticed. A shipped checker that fails on a clean tree is worse than no checker. | Teach the checker about template-literal prefixes (treat `t(\`prefix.${…}\`)` as consuming `prefix.*`), then add it to `tests.yml` next to ruff. |
| F-25 | LOW | `CHANGELOG.md:81` | "Suite: 1582 passed, 7 skipped" — actual at HEAD is **1614 passed, 7 skipped**. Small, but this line is the release's own evidence claim. | Update at release cut; better, generate it from the run instead of hand-editing. |
| F-26 | LOW | `src/symphony/orchestrator/migrations.py:117-283` | `_migrate_002_governed_workflow` still creates `workflow_snapshots`, `node_runs`, `approvals`, `artifacts` and their indexes, plus three `runs` columns, for the deleted flow engine. Nothing in `src/` reads them. Every fresh `.symphony/state.db` gets dead schema, and `FIRST_GOVERNED_WORKFLOW_VERSION` still drives backup logic. | Keep the migration (existing DBs must stay at the same version) but rename it `_migrate_002_legacy_flow_tables` with a comment saying the engine is gone and the tables are inert; consider a migration 00N that drops them once the release is out. |
| F-27 | LOW | `docs/symphony-prompts/file/base.md:3`, `src/symphony/prompt_context.py:64` | `## Document Defect` is consumed as a rewind/failure section by base.md and `prompt_context`, but **no stage prompt ever tells an agent to write it** (`document.md` has no rewind path at all — the Document lane can only go to Done or Human Review). Dead contract vocabulary. | Either add a "real defect found → append `## Document Defect`, set state to `In Progress`, stop" step to `document.md` (the plan and `docs/PIPELINE.md:53` both assume it exists), or remove the references. |
| F-28 | LOW | `src/symphony/workflow/presets.py:88-104` | The deep preset ships no `Done` stage prompt while the default preset does (`stages/done.md`), and `apply_lane_preset` actively pops the `Done` entry when switching to deep (`mutate.py:588-596`). Switching default→deep→default is lossy in spirit if the operator had customized `Done`. Also `deep/base.md:23` draws `Review -> Build` while the request ticket actually goes `Review -> Done` (`deep/review.md`), which reads as a contradiction. | Add a `deep/done.md` (or state in the preset docstring that Done is intentionally unprompted), and fix the deep diagram to show that Review's PASS *releases* the spawned Build tickets. |
| F-29 | LOW | `src/symphony/continuous_improvement.py:821-828` | `next_request_id` scans only **open** issues, so once a day's CI tickets close, a later run the same day reuses `REQ-CI-<date>-1` for a completely different batch. Request groups stop being unique keys. | Scan `tracker.scan_all()` (all states) when picking the next free index. |
| F-30 | LOW | `src/symphony/continuous_improvement.py:898,904` | Proposal de-duplication uses `_slug(title)` truncated to 60 chars against open tickets; two genuinely different proposals sharing a 60-char prefix collapse into one, silently counted as a duplicate. | Compare on the full normalized title (or keep the slug but also require a length match), and log the dedupe reason per proposal. |
| F-31 | LOW | `src/symphony/cli/board.py:186` | CLI errors print the internal code: `error: board_dependency_error: ticket already exists (identifier='A-2')`. `WorkflowMutationError` deliberately strips the code prefix for operator-facing surfaces (`test_mutation_error_message_has_no_code_prefix`), so the two surfaces disagree. Agents read this text. | Print `exc.message` (+ context) without the code in the CLI, keeping the code in the JSON API. |
| F-32 | LOW | `src/symphony/orchestrator/core.py:1779` (`skip_document`), `:4204` | `"Human Review"` and `"Blocked"` are hardcoded target states. On a fully customized board without those lanes (explicitly allowed by P2) the transition writes a state the tracker does not know. Pre-existing, but the branch's "boards stay fully customizable" claim raises its profile. | Resolve through helpers like the existing `_max_turns_exhausted_target_state` / `_blocked_rca_work_state` (first terminal state matching `human`/`block`, else first terminal), and refuse with a clear message when none exists. |

---

## 1. Agent-freedom audit (P1)

I grepped every shipped prompt (`docs/symphony-prompts/**`, 23 files), the chat preambles
(`chat.py:100-190`), the CI mode prompts, the WORKFLOW example hooks, and the validation code
for prohibitive language. Verdicts:

**Legitimate gates (keep):**

* `base.md:61-63` — "Never skip Verify / never mark Done without evidence / never silence
  failing tests". This *is* the product. Evidence gate, not a cage.
* `in-progress.md:3` "Do NOT push, merge, or open PRs; Verify owns the Merge Gate" and
  `deep/build.md:4` "Do NOT push, merge, or touch other slices' scope" — single-writer
  ownership so two lanes don't race the same branch. Legitimate.
* Every `deep/*.md` "Do NOT implement / do NOT plan" line — lane contracts in a pipeline
  where another *agent* owns that step. That is delegation, not restriction; the agent is
  still free to spawn sub-tickets (plan appendix #4) and to use any tool inside its lane.
* `todo.md:3` / `done.md:3` "write ticket comments only" — these lanes exist to produce a
  judgement, and both have an explicit escape (`Blocked`, `## Merge Missing`).
* `ci/*.md` "Read only. Do NOT modify any file except the output file" — the heartbeat runs
  in the *host* repo outside the dispatch model. This one is right in intent, but it is only
  a prompt: see F-12 for the missing enforcement.
* `document.md:17` "agents must not simulate the operator skip" — protects an operator-only
  control from self-authorization. Legitimate.
* Codex `thread_sandbox: workspace-write` in the examples, with a documented escape hatch and
  a comment telling operators when to widen it. Legitimate default.

**Defects (fix):**

* **F-07** `document.md:3` — "do NOT run git history commands" bans read-only inspection with
  no gate behind it, in the one lane whose job needs the diff. Straight P1 violation.
* **F-08** `document.md:3` — "write wiki files and ticket comments **only**" is narrower than
  the lane's own charter, so the branch *removes* a capability the plan required.
* **F-21** `base.md:65` — mandatory busywork (`docs/changelog/…`) against a deleted convention.
* **F-18/F-19** — the inverse of a cage but the same class of harm: the recommended bootstrap
  config denies the claude worker edit acceptance and board-directory access, and the new
  CLI-mandated intake protocol assumes a Bash capability and a `PATH` entry nobody verifies.
  An agent that is *told* it must use a tool it cannot invoke is the most expensive kind of
  fence.

**Not a violation but worth stating:** `base.md:64` "Touch only what the ticket requires; no
drive-by refactors" is scope discipline with an explicit override elsewhere
(`## Scope Expansion`, `[scope-expand]` marker). Keep.

**Net:** P1 is respected in design. The gates are evidence gates. Three prompt lines and two
config/PATH gaps are the exceptions.

---

## 2. Removal correctness (flow engine + board-viewer)

I grepped the whole tree (excluding `.git`, `.venv`, `docs/plans`) for `governed`, `flow`,
`workflow_engine`, `flow_store`, `board-viewer`, `board_viewer`, `viewer_port`, `Learn`,
`skip-learn`, and cross-checked the web API route table against every `apiRequest()` path in
`app.js` (30 client paths → all present in `webapi.py`/`server.py`; zero orphans).

**Clean:**

* `src/symphony/flow/**`, `cli/flow.py`, `orchestrator/flow_store.py` and every seam
  (`core.py`, `run_registry.py:governed`, `main.py` subcommands, `doctor.check_workflow_engine`,
  `workflow/config.workflow_engine`, `workflow/preflight`) are gone with no residue in `src/`.
* `service.py` viewer plumbing (`viewer_port`, `viewer_pid`, `viewer_command`,
  `build_viewer_command`, `board_viewer_script_for`) fully removed, `ServiceRecord` JSON
  round-trip updated on both sides, `--viewer-port` flag gone from the parser and from
  README/skills/CHANGELOG.
* `.symphony/workflows/ticket-default.yaml` deleted and the `.gitignore` carve-out dropped
  (1ea0739) — consistent.
* Learn→Document rename is thorough: contracts (`document`+`learn` alias), rewind detection,
  `prompt_context` section maps, `skip_document` + `skip_learn` method alias + both HTTP
  routes on both servers, TUI binding, `app.js:isDocumentState`, i18n (en+ko), presets,
  examples, README/README.ko/PIPELINE/skills. I could not find a rename hole.

**Leftovers:** F-22 (`tui-open.sh` still launches the deleted viewer), F-26 (dead SQLite
migration), and stale historical spec docs under `docs/spec/**` +
`docs/llm-wiki/INDEX.md:15` that still describe `tools/board-viewer` as present. The spec
docs are archived design records, so I rate them LOW/no-action — but if the release claims
"no dead references", `docs/spec/**` should carry a one-line "superseded" banner.

Also note `.pytest_cache/v/cache/nodeids` still lists the deleted `test_board_viewer.py`
tests — untracked local cache, no action.

---

## 3. New-feature coherence

### 3.1 Board tool validation (`trackers/validate.py`)

I read the algorithms and probed the CLI. The graph logic is sound:

* self-block on **update** → caught (`_find_cycle_through` returns `[A, A]`); on **create**
  → caught earlier as "unknown blocked_by target" (the new id is not on the board yet).
  Verified both.
* pre-existing unrelated cycles do not block an unrelated write — deliberate and correct
  (`_find_cycle_through` only reports cycles through the edited node).
* `find_cycle` skips dangling targets; `topological_order` appends cyclic leftovers instead
  of dropping tickets. Good.
* `_find_cycle_through`'s global `visited` set is correct for reachability (a fully explored
  node that did not reach `node` never will) and prevents infinite recursion on a
  pre-existing cycle.

Gaps: **F-03** (no identifier validation — the big one), **F-15** (validate/create is not
atomic), **F-09** (no update verb), and case sensitivity: `--blocked-by a-1` against ticket
`A-1` is rejected as "unknown target" (verified). That is defensible (ids are exact keys) but
it is asymmetric with `board ls --state` and `board new --state`, which are case-insensitive,
and models will get it wrong. Suggest a hint in the error: "unknown blocked_by target 'a-1'
— did you mean 'A-1'?" (cheap case-insensitive lookup for the message only).

### 3.2 Presets round-trip (`workflow/mutate.py`)

`apply_lane_preset` is careful work: comment-preserving ruamel round-trip, atomic write,
extra terminal lanes preserved, per-state maps (`stage_kinds`,
`max_concurrent_agents_by_state`, `max_state_turns_by_state`, `max_total_tokens_by_state`)
pruned for removed lanes, stale stage entries from the other preset dropped, case-variant
keys de-duplicated, path-escape guard. Round-trip default→deep→default is tested.

Two real issues: **F-11** (placeholder prompts on boards without the shipped files — the
common upgrade case) and **F-23** (no post-apply load/preflight validation, no orchestrator
test). One design note that is *correct* and worth keeping: because `Verify` and `Document`
exist in both presets, switching default→deep only migrates `Todo`/`In Progress` tickets, so
the destructive surface is smaller than it looks. The web handler's running-worker 409 guard
and per-ticket `skipped_running` reporting are good.

### 3.3 `stage_kinds` resolution and the pin stamp

`kind_for_state` precedence (pin > stage > default) is right and unit-tested; the builder
hard-fails an unknown kind and only warns on an unknown state key (correct asymmetry —
states are UI-editable). The interaction with the ticket stamp is deliberately handled
(`core.py:3783`). But the feature is defeated in practice by **F-01** (no re-resolution at
phase transitions) and made dangerous by **F-02** (stall clock keyed to the wrong backend),
and it costs the audit stamp for the whole board (**F-20**). The existing test
(`test_stage_kinds_route_backend_kind_into_backend_factory`) asserts only `instances[0]` —
exactly the call that works. That is the coverage hole that let F-01 through.

### 3.4 Chat intake protocol vs the CLI

Preamble → CLI parity checked flag by flag: `--state`, `--request`, `--blocked-by`,
`--description-file -` all exist with those exact spellings; the preamble renders the board's
real active states and switches routing on the presence of an `Intake` lane. Good.

Mismatches: the preamble says "use the next free `REQ-<n>`" but nothing lists existing request
ids (`board ls` doesn't show `request`; only `board graph --request X` filters) — add
`request` to `board ls` output or a `board requests` listing. Plus F-19 (PATH/Bash capability)
and F-14 (the filed DAG is invisible on the board the operator is pointed at).

### 3.5 Learn→Document rename + legacy alias

Correct everywhere I looked (see §2). One asymmetry: `skip_document` on a legacy `Learn`
board appends a section titled `## Document Skipped` while the lane is still called Learn;
`prompt_context` accepts both names so nothing breaks, but the audit note names a lane that
does not exist on that board. Cosmetic — fold into F-27's cleanup if touched.

### 3.6 Contracts gating (03d40d8)

`board_uses_default_contracts` handles the rename + legacy alias correctly
(`{todo, in progress, verify, document, learn}`, case-insensitive). Answering the brief's
question directly: **a board with only *some* default lanes still gets contracts enforced**
(e.g. `["Todo","In Progress"]` → True), because the predicate is "all lanes ∈ set", not
"set ⊆ lanes". For a 2-lane board that means the In Progress contract (Plan/Acceptance
Tests/Done Signals/Implementation/Self-Critique + a real `docs/<ID>/work/` artefact) is
enforced even if the operator rewrote the prompt to ask for none of it. And renaming one lane
turns the whole validator off silently. Both directions are wrong for P2; **F-06** proposes
the explicit switch.

### 3.7 CI modes (73f7323)

Solid structure: modes are opt-in, `resolved_modes()` preserves pre-modes behaviour, the
only board write path is a normal ticket, proposals are capped/deduped/request-grouped,
per-mode cadence is durable (`.symphony/continuous-improvement/mode-state.json`, atomic
replace, unknown keys filtered on load), one mode's exception cannot kill the run,
`report_phase` gives the UI live progress, and the runner stays orchestrator-free with the
agent capability injected. `security` correctly treats a missing scanner as `not_available`
rather than manufacturing a finding.

Defects: **F-12** (agent write boundary is prompt-only, in the host tree), **F-16** (cadence
stamped for modes that could not run), **F-17** (blocked_fixes never closes its loop),
**F-29/F-30** (request-id reuse, slug collisions). Orchestrator binding is correct
(`partial(default_improvement_runner, agent_runner=self._run_improvement_agent)` keeps the
3-positional `ImprovementRunner` signature the test fakes implement) and the lease/turn
accounting (`consumed` on completion, not on a lease-held postpone) is right.

---

## 4. Acceptance findings 1–4 — root causes and smallest correct fixes

These are not just "hardening ideas"; each traces to a specific line.

### 4.1 Workspace→board reachability (finding 1) — root cause F-04

The hook links a hardcoded `kanban` directory; the board root is configurable and the CLI's
own default is `board`. Smallest correct fix, in three layers:

1. **Config → hook.** `orchestrator/helpers.py:53`:
   ```python
   def _branch_hook_env(cfg):
       env = {"SYMPHONY_FEATURE_BASE_BRANCH": ..., "SYMPHONY_MERGE_TARGET_BRANCH": ...}
       root = cfg.tracker.board_root
       if root is not None:
           env["SYMPHONY_BOARD_ROOT"] = str(root)
           wf = cfg.workflow_path.parent.resolve()
           if root.resolve().is_relative_to(wf):
               env["SYMPHONY_BOARD_ROOT_NAME"] = root.resolve().relative_to(wf).as_posix()
       return env
   ```
   `scripts/symphony-setup-worktree.sh:152`: `for dir in ${SYMPHONY_BOARD_ROOT_NAME:-kanban}; do`.
2. **Mechanical detection at dispatch** (the part acceptance run B actually asked for).
   In `WorkspaceManager.create_or_reuse`, after `after_create`, when the board root lives
   inside the workflow dir:
   ```python
   linked = (workspace / board_name)
   if linked.exists() and linked.resolve() != board_root.resolve():
       raise SymphonyError("workspace board is not the host board",
                           workspace=str(linked), host_board=str(board_root))
   ```
   This catches symlink-copied-instead-of-linked (Windows `ln -s` fallback), a stale real
   directory, and a wrong `board_root` — before the first turn is spent, and it fails loudly
   instead of looping. Prefer this over "ticket file modified in workspace but board
   unchanged" heuristics: it is deterministic, cheap, and has no false positives.
3. **doctor.** `check_board_reachable_from_workspace(cfg)`: WARN when `tracker.kind == file`
   and neither `hooks.after_create` nor the referenced setup script mentions the board root
   name (static grep), FAIL when `board_root` does not exist. Register it in `run_checks`.

### 4.2 Stall budget vs heavy stages (finding 2) — root cause F-02 + a missing per-stage knob

Two independent fixes, both small:

1. **Correctness (do this):** F-02 — resolve `backend_timeouts()` per running entry so a
   pinned/routed backend gets *its own* configured budget. Today raising
   `claude.stall_timeout_ms` does nothing unless claude is also `agent.kind`.
2. **Ergonomics (do this too):** add `agent.stall_timeout_ms_by_state: {Verify: 900000}`,
   parsed exactly like the existing `max_total_tokens_by_state` / `max_state_turns_by_state`
   maps (same validator shape, same rename handling in `mutate._rename_state_keyed_map`,
   which already enumerates those keys), consulted in `_reconcile_stall_state` with fallback
   to the backend value. Ship `Verify: 900000` commented-out in both WORKFLOW examples and
   document it under "heavy stages" in README.
   *Rejected alternative:* resetting the stall clock on workspace file activity — it silently
   defeats the stall detector for any agent that writes logs while hung, which is the exact
   failure mode `stalled_session` exists to catch (see the OLV-002 comment at
   `backends/claude_code.py:157`).

### 4.3 Bounded auto-retry for transient stream errors (finding 3)

The machinery already exists and is bounded — `_is_retryable_worker_error` →
`worker_error_retry_scheduled` with exponential backoff, capped by `agent.max_retries` with
`_retry_cap_exceeded`/`_escalate_max_retries` (`core.py:5766`, `:5859`). The claude stream errors simply are not in
the marker list (`core.py:155-169`). Smallest correct fix:

```python
_RETRYABLE_WORKER_ERROR_MARKERS = (
    ..., "try again later",
    "stream unreadable",          # claude_code.py:247 / codex.py:653 / pi.py:232
    "no result event",            # claude_code.py:259 (rc=1 with no result frame)
)
```
Plus a test asserting (a) such an exit schedules a retry instead of pausing, and (b) the
`max_retries` cap still escalates to `Blocked`/`Human Review` with the `## Escalation` note.
`_is_retryable_auto_pause_reason` already releases matching *persisted* pauses on restart,
so previously-paused tickets self-heal after the upgrade — call that out in the CHANGELOG.
Keep the pause path for everything unmatched: a blanket retry-all would mask real crashes.

### 4.4 Canonical wiki write-back location (finding 4)

Root cause is ordering, not ambiguity: **Verify merges (F-10), then Document writes.** The
wiki files land on the ticket branch *after* the merge that was supposed to deliver them, so
whether they reach the target tree depends entirely on whether the orchestrator's Done
auto-merge finds new commits — which is exactly why runs A and B differed. Canonical rule to
adopt and document in one place (`README#pipeline`, `docs/PIPELINE.md`, `base.md`,
`document.md`):

> The wiki is a **host-repo, tracked** path: `<workflow-dir>/docs/llm-wiki/` (configurable via
> `wiki.root`, already parsed). Workers write it **inside their worktree** at the same
> relative path; the per-turn wip commit carries it and the Done merge delivers it. The wiki
> is never symlinked into the workspace, and never written directly to the host tree.

Concrete changes: (1) apply F-10 so the only merge happens at Done, after Document;
(2) `document.md` step 2 names `docs/llm-wiki/` *relative to the workspace* explicitly;
(3) if an operator does symlink the wiki (some setups do, to share it across tickets), tell
them to add it to `agent.auto_merge_capture_untracked` — that flag exists for precisely this
case (`utils/auto_merge.py:75-78`) and should be documented next to the rule;
(4) `symphony doctor` WARN when `wiki.root` resolves outside the workflow dir.

---

## 5. Docs truth

**Accurate:** the viewer/governed removals are reflected everywhere in README, README.ko,
skills, PIPELINE.md, CHANGELOG; the board-tool section documents the real flags and the real
validation; the CI-modes table matches `SUPPORTED_CI_MODES` and the actual behaviour
including "zero proposals is a valid answer"; the lane-preset section matches the code;
the API table matches the router (including the deprecated `skip-learn` alias); README.ko is
in sync with README on every section I sampled.

**Overselling / wrong:**

* README:200-204 + `skills/…/workflow-config.md:106` + CHANGELOG:58 — `stage_kinds` "A ticket
  that changes state gets the new stage's backend on its next dispatch" is *literally* true
  and *practically* misleading, because the default flow has no next dispatch (F-01). After
  fixing F-01 the sentence becomes simply true; until then it should say "…on its next
  dispatch; a ticket that walks several lanes in one dispatch keeps the backend it started
  with."
* README.md:833 "where Document merges land" — merges happen in Verify (prompt) and at Done
  (orchestrator), never in Document (F-10).
* `skills/…/bootstrapping.md:13` — copies the linear example for every bootstrap (F-18).
* CHANGELOG:81 suite count is stale (F-25).
* CHANGELOG Unreleased does not mention two operator-visible removals: the `viewer.board-viewer`
  and `workflow_engine` **doctor checks** are gone (anyone parsing doctor output loses rows),
  and `WORKFLOW-PROGRESS.md`'s default board URL changed from `:8765` to the service port
  (`test_progress_md.py`). Add both to "Removed"/"Changed".
* The plan's CUT list includes `WORKFLOW.file.example.md`; it is still present (and still
  referenced by README:345, PIPELINE.md:134, two tests, and the llm-wiki). Keeping it is the
  *right* call — but the plan and the file should agree; note the deviation in the plan doc.
* `docs/spec/**` and `docs/llm-wiki/INDEX.md:15` still describe `tools/board-viewer` in the
  present tense.

---

## 6. Test-suite honesty

I diffed every modified test file looking for weakened assertions. **I found no assertion
weakened to accommodate a behaviour change.** The renames are mechanical (`Learn`→`Document`
in fixtures and docstrings), and several changes *strengthen* coverage:

* `test_orchestrator_contracts.py` deliberately keeps one case on the legacy `Learn` name with
  a comment — good alias regression.
* `test_orchestrator_phase_transition.py` / `test_supergoal_hardening_loop.py` add
  `document → in progress` rewind assertions **and** retain the `learn` ones.
* `test_progress_md.py` replaces the deleted viewer-URL assertion with two stronger ones
  (default port and `server.port` override) instead of dropping it.
* `test_web_static_contract.py` deletes five governed-UI contract tests (correct — the UI is
  gone) and adds a lane-preset contract test. Net loss of ~80 assertions is honest deletion,
  not dilution.
* `test_orchestrator_contract_integration.py` adds a genuinely good test asserting
  `stage_kinds` reaches `BackendInit.cfg.agent.kind` rather than just bookkeeping fields.

**Coverage holes for the new features (ranked):**

1. **F-01's blind spot** — the stage_kinds test asserts only the *first* factory call. One
   extra assertion on `instances[1]` after a phase transition would have caught the defect.
2. **Deep preset**: zero orchestrator-level tests; only prompt-text greps (F-23).
3. **Identifier validation**: `test_board_cli_subcommands.py` covers unknown state, unknown
   blocker, duplicate id, cycle — but never a malformed id (F-03).
4. **Concurrency**: no test creates two tickets concurrently against one board (F-15).
5. **Preset prompt content**: `test_apply_lane_preset_creates_missing_prompt_files` asserts
   existence only, which locks in the placeholder defect (F-11).
6. **CI cadence outcomes**: no test asserts what `mode_state` records when a mode is
   `not_available` or raises (F-16); no test asserts the source ticket's fate after a
   `blocked_fixes` fix completes (F-17).
7. **Stall budget**: no test asserts which backend's `stall_timeout_ms` the reconciler uses
   (F-02).
8. **Retryable stream errors**: no test pins the marker list against the strings the backends
   actually emit — the backends and the marker list can drift silently (see §4.3).

---

## 7. Prioritized fix plan

### Blocks production (must fix before tagging)

| # | id | why it blocks |
|---|---|---|
| 1 | **F-03** | Agent-driven writes escape the board root; CLI and API disagree on validation. One-line class of bug, security-adjacent, trivially fixed. |
| 2 | **F-04** | Reproduced live (acceptance B): silent infinite re-dispatch on any non-`kanban` board root. Burns money and looks like a hang. |
| 3 | **F-01** | The branch ships a headline feature that does not work in the documented configuration, with docs that imply it does. Either fix or retract the feature. |
| 4 | **F-02** | Wrong stall budget for pinned/routed backends kills healthy workers mid-stage; also the real fix for acceptance finding 2. |
| 5 | **F-05** | A UI-switchable preset with no merge story, no e2e test, and no live run must not ship as a peer of the default. Fix or label experimental. |
| 6 | **F-06** | Renaming a lane silently removes the product's evidence floor. Needs at minimum a log line + doctor row before release. |

### Ship-with, fix in the first patch release

F-07, F-08, F-09, F-10, F-11, F-12, F-13, F-18, F-19 — these are correctness and
truthfulness issues that degrade the experience but do not silently corrupt a board.
F-18/F-19 climb to blocker status if the release is aimed at *new* users bootstrapping from
scratch (they are the first-run path).

Acceptance-finding fixes 4.2(2) (`stall_timeout_ms_by_state`) and 4.3 (retry markers) are
cheap and high-value; I would pull both into the release if the schedule allows — the retry
marker change is four lines plus a test.

### Backlog

F-14, F-15, F-16, F-17, F-20, F-21, F-22, F-23, F-24, F-25 … F-32, plus the docs corrections
in §5 and the coverage holes in §6.

---

## 8. What I could not verify (honesty section)

* **Runtime behaviour of the deep preset** — no live run exists and I did not start one
  (read-only task, real agent CLIs). F-05 is derived from reading the preset, the prompts,
  `symphony-setup-worktree.sh`, and `utils/auto_merge.py` together; it should be confirmed by
  an acceptance run C before or immediately after release.
* **Claude Code's exact headless permission semantics** under `--permission-mode acceptEdits`
  for Bash tool calls (F-19's second half). The configuration *inconsistency* across shipped
  examples (F-18) is verified fact; the consequence for `symphony board new` inside a worker
  is inference from the CLI's documented model and should be confirmed empirically.
* **pyright in CI** — locally 14 of 16 errors are "textual not installed" in this venv and 2
  are pre-existing ruamel stub complaints present at `pre-minimal`. I did not reproduce CI's
  environment; I assume CI is green because `tests.yml` gates on it and the branch merged.
* **Windows paths** — the junction fallback in the setup script and `_check_identifier`'s
  backslash rationale were read, not executed.
* I did **not** modify, stage, or commit anything. This file is written untracked at
  `docs/plans/adversarial-review.md`; `git status` should show exactly one untracked file.
