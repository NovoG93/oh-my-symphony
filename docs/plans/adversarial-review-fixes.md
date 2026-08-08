# Adversarial review — fix map

Every finding in [`adversarial-review.md`](./adversarial-review.md) (F-01 … F-32)
with the commit that resolved it and a one-line resolution. Three commits, each
verified green on its own.

| commit | scope |
|---|---|
| `ec638c4` | production blockers (F-01 … F-06) + review §4.3 retry markers + §4.4/F-10 single-merge rule |
| `50c390e` | first-patch tier (F-07, F-08, F-09, F-11, F-12, F-13, F-18, F-19) |
| `d423f9f` | backlog tier (F-14 … F-32), docs truth §5, coverage §6 |

Baseline before the work: **1614 passed, 7 skipped**.
After `ec638c4`: 1675 · after `50c390e`: 1697 · after `d423f9f`: **1718 passed,
7 skipped**, `ruff check src tests` clean, `node --check` clean on the SPA,
`scripts/check_i18n.py` OK (315 keys).

## Findings

| id | sev | commit | resolution |
|---|---|---|---|
| F-01 | HIGH | `ec638c4` | `_run_agent_attempt` keeps the unrouted `base_cfg` and re-resolves `_config_for_issue_agent` before every `_rebuild_backend_for_phase`; logs `stage_backend_rerouted`. The probe test now asserts `instances[1]`'s kind and fails without the fix. |
| F-02 | HIGH | `ec638c4` | `_stall_timeout_ms_for_entry` resolves the stall budget per running entry (pin > stage route > default) plus the new `agent.stall_timeout_ms_by_state` map (parsed, rename-aware, documented in both WORKFLOW examples and README "Heavy stages"). |
| F-03 | HIGH | `ec638c4` | Shared `trackers/validate.validate_identifier()` enforced in `symphony board new`, `validate_ticket_dependencies`, `FileBoardTracker.create` and `.update_fields`; the web API reuses the same regex. `board new "../../evil"` exits 1 and writes nothing. |
| F-04 | HIGH | `ec638c4` | `SYMPHONY_BOARD_ROOT` / `SYMPHONY_BOARD_ROOT_NAME` in the hook env; the setup script and both WORKFLOW hooks link `${SYMPHONY_BOARD_ROOT_NAME:-kanban}`; `WorkspaceManager` raises `WorkspaceBoardUnreachable` on a resolve() mismatch; new `board.reachable` doctor row. |
| F-05 | HIGH | `ec638c4` | **Fixed, not labelled experimental.** The merge contract is now stated (presets.py, `deep/base.md`, `deep/build.md`, `deep/verify.md`, README), enforced by the `board.deep_merge_contract` doctor row, and covered by the first orchestrator-level deep tests (preset round-trip + preflight, a request ticket walking Intake→Done with its spawned DAG, blocker gating). Chosen over labelling because the contract was already implied by the defaults (`feature_base_branch == auto_merge_target_branch == current branch`) — bounded work, no feature retraction. |
| F-06 | HIGH | `ec638c4` | `agent.stage_contracts: auto\|on\|off`; auto-disable logs `stage_contracts_disabled` with the offending lanes at every config load, gets a doctor row, `agent.stage_contracts_enabled` on `GET /api/v1/workflow`, and a Settings-page hint. |
| F-07 | MED | `50c390e` | P1: "do NOT run git history commands" replaced with "Read-only inspection of history is expected (`git log`, `git show`, `git diff`)". The commit/branch/push/merge boundary stays. |
| F-08 | MED | `50c390e` | Document lane gets a positive write scope (llm-wiki, the user-facing docs the change touched, the vault, ticket comments) plus a step mirroring `deep/document.md`. Behaviour changes stay forbidden. |
| F-09 | MED | `50c390e` | `symphony board update <id> [--state] [--blocked-by] [--add-blocked-by] [--request]` routed through the identifier + dependency validators; `verify.md`, `deep/verify.md`, `deep/qa.md` name it instead of hand-editing frontmatter. |
| F-10 | MED | `ec638c4` | Verify is prove-only: `git merge-tree --write-tree` preflight + `## Merge Status: preflight clean, orchestrator will merge at Done`. Canonical rule documented in `base.md`, `docs/PIPELINE.md`, README. |
| F-11 | MED | `50c390e` | **Minimal variant** (offered by the review): `apply_lane_preset` refuses before touching WORKFLOW.md, naming the missing prompt files and the `cp -R docs/symphony-prompts` command, instead of writing placeholder stubs. The preferred fix (ship prompt bodies as package data) needs the prompt tree moved inside the package and every reference updated — deferred as its own change. The test now asserts distinctive lines from the shipped prompts. |
| F-12 | MED | `50c390e` | `_run_improvement_agent` snapshots `git status --porcelain -uall` around the turn; a write outside the proposals dir discards the proposals, logs `ci_agent_wrote_outside_contract`, and the mode records `not_proven`. (The review's "better: run in the throwaway worktree" is not done; the snapshot gate is the enforcement it asked for.) |
| F-13 | MED | `50c390e` | Card attention says "blocker X is not on the board" at error severity with the fixing command; new `board.dependencies` doctor row reports dangling blockers and cycles. The optional auto-move-to-Blocked is not implemented (review marked it optional). |
| F-14 | MED | `d423f9f` | `⛓ blocked by …` and request chips on cards; `blocked_by` / `request` inputs in the create modal and the drawer, posting through the validating endpoints. |
| F-15 | MED | `d423f9f` | `FileBoardTracker.create_validated(...)` runs snapshot + validation + id allocation + write under the board-level lock; CLI and web API share it. |
| F-16 | MED | `d423f9f` | Cadence stamped only for `passed`/`failed`; `not_available` / `not_proven` retry on the next heartbeat. |
| F-17 | MED | `d423f9f` | `reopen_resolved_blocked_sources` returns a `Blocked` source to the first Todo-ish lane once every blocker is resolved and one of them is a CI fix, with an `## Unblocked` note. |
| F-18 | MED | `50c390e` | `WORKFLOW.example.md` and the oneshot template get `--permission-mode acceptEdits --add-dir`; `bootstrapping.md` branches on tracker kind; a test pins the flags across all four shipped workflows. |
| F-19 | MED | `50c390e` | `SYMPHONY_CLI` exported into the dispatch env; prompts and the chat preamble use `${SYMPHONY_CLI:-symphony}`; new `board.cli` doctor row; permission-mode note in bootstrapping. |
| F-20 | MED | `d423f9f` | Audit-only `last_agent_kind` frontmatter (never read as a pin) written on `stage_kinds`-routed boards, exposed on the API and as a muted card chip. |
| F-21 | MED | `d423f9f` | The `docs/changelog/…` line is gone from both base prompts. |
| F-22 | MED | `d423f9f` | The `tools/board-viewer` launch block and `BV_PORT` are deleted from `tui-open.sh`. |
| F-23 | MED | `d423f9f` (+ `ec638c4`) | Orchestrator-level deep tests landed with F-05; the preset-apply API now returns a `warning` when `agent.max_turns` cannot cover the new lane count. |
| F-24 | MED | `d423f9f` | The checker understands template-literal key prefixes (it was red on a clean tree) and runs in `tests.yml` **and** in the suite so it cannot rot again. |
| F-25 | LOW | `d423f9f` | Suite count corrected to the measured value; the two missing operator-visible removals (doctor rows, progress URL) and every hardening change are now in the Unreleased section. |
| F-26 | LOW | `d423f9f` | Renamed `_migrate_002_legacy_flow_tables` with an "inert" docstring; recorded migration name and version unchanged so existing databases keep a valid upgrade chain. Dropping the tables is left to a future migration, as the review suggested. |
| F-27 | LOW | `d423f9f` | `document.md` (file + linear) gains the `## Document Defect` → `In Progress` rewind step the base prompt and PIPELINE.md already assumed. |
| F-28 | LOW | `d423f9f` | `deep/base.md` draws the real shape (Review's PASS releases the spawned chain) and states that `Done` is intentionally unprompted; `presets.py` says the same. |
| F-29 | LOW | `d423f9f` | `next_request_id` is fed `scan_all()`, so a closed group's id is never reused. |
| F-30 | LOW | `d423f9f` | Dedupe key is `(slug, normalized title length)`; every dedupe logs `ci_proposal_deduped` with its reason. |
| F-31 | LOW | `d423f9f` | `symphony board` prints `exc.message` + context without the internal error code. |
| F-32 | LOW | `d423f9f` | `_human_review_target_state` / `_rewind_budget_target_state` resolve through the board's terminal lanes; `skip_document` refuses cleanly when none exists. |

## Review sections beyond the findings table

| item | commit | resolution |
|---|---|---|
| §4.1 workspace→board reachability | `ec638c4` | all three layers (hook env, dispatch-time assert, doctor check). |
| §4.2 stall budget + per-stage knob | `ec638c4` | both halves: per-entry resolution and `agent.stall_timeout_ms_by_state`. |
| §4.3 retryable stream errors | `ec638c4` | `stream unreadable` / `no result event` markers, bounded by `max_retries`, with a drift-guard test pinning the markers against the backend sources. |
| §4.4 canonical wiki write-back | `ec638c4` | single-merge ordering + the "host-repo tracked path, written inside the worktree" rule in `docs/PIPELINE.md`, `base.md`, `document.md`, README. |
| §5 docs truth | `ec638c4`, `50c390e`, `d423f9f` | stage_kinds wording, README merge sentence, bootstrapping branch, CHANGELOG, plan deviation, `docs/spec/**` + llm-wiki banners. |
| §6 coverage holes | all three | `instances[1]` routing, deep-preset e2e, malformed identifiers, concurrency, preset prompt content, CI outcome matrix + blocked_fixes fate, stall backend, retry-marker drift. |

## Consciously not done

* **F-11 package-data prompts** — the refusal path ships instead; moving
  `docs/symphony-prompts/**` inside the package is a separate change.
* **F-12 throwaway worktree for the CI agent turn** — the write-contract
  snapshot is in; relocating the turn is a larger change to the heartbeat.
* **F-13(c) auto-move a dangling-blocked ticket to `Blocked`** — the review
  marked it optional; the card text and the doctor row carry the signal.
* **F-26 migration that drops the inert tables** — deliberately after the
  release, so every deployed database crosses the current line first.
* **Deep-preset acceptance run C** (review §8) — still needs a live run with
  real agent CLIs; the e2e tests use a mock backend.
