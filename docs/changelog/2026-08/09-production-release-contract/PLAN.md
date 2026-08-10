# PLAN - Production application release contract

Frozen plan. A fresh-context implementer reads ONLY this file (plus the latest `R-LOOP.md` section on
re-entry) and builds it - the plan must be self-sufficient. Frozen after approval; changes append a
dated `## Amendment`.

## Approval

- Status: approved-by-user
- Record: 2026-08-08T22:40:35Z; user confirmed the Jira clone target, all app-delivery scope, and autonomous fix-ticket creation: "1 was made /Users/danny/Documents/PARA/Resource/jira-dev-factory/jira-clone 2 yes all 3 symphony can issue new tickets to fix the low quality untill all pass (done autnomously)"

## Intent

- Goal / constraints / tradeoffs / rejected approaches: Add a machine release gate rather than another prose-only QA checklist. Preserve all existing non-app workflows and the v0.19.0 base. Do not edit the user's active checkout, lower existing gates, accept historical `verdict: GREEN` text, or merge the stale `feat/autonomous-dev-factory` branch. Use standard paths (`release-contract.yaml`, `docs/<verifier>/qa/release-evidence.json`) and the `app-release` / `app-release-finalizer` labels so no broad config migration is needed. Host-computed Git ancestry, hashes, exact coverage, and artifact checks prevent accidental false greens; independent browser QA remains necessary because an intentionally dishonest worker can fabricate files.
- Completion promise: deliver the gate, autonomous repair/fresh-verifier lifecycle, documentation, and a real Jira OpenCode GLM-5.2 red-to-green E2E with independent Ego Lite proof. Stop only when every criterion is proven or after `max_iterations=3`, at which point record the exact blocker and smallest next action without claiming completion.

## Steps

1. Domain contract and ADR
   - Update `CONTEXT.md` with concise definitions for Application release contract, Release verifier, Release evidence cycle, Repair group, Release finalizer, and Target commit.
   - Add `docs/adr/0002-machine-enforced-app-release-contracts.md`: label-scoped gate, exact target/ancestry/hash semantics, fresh-verifier rule, compatibility, and deliberate-worker trust limitation.

2. Pure release validation in `src/symphony/orchestrator/release_contracts.py`
   - Load raw `release-contract.yaml` with PyYAML while retaining raw bytes for SHA-256. Schema v1 fields: `schema_version`, `target_branch`, `finalizer_ticket`, non-empty `implementation_tickets`, `launch`, `viewports`, and `checks`.
   - Every check has a unique stable `id`, `kind`, `description`, `repair_group`, and `required_viewports`. Require at least one check of each kind: `feature`, `control`, `visual`, `responsive`, `accessibility`, and `reliability`. Reject unknown viewport references and unsafe/duplicate identifiers.
   - Load only `docs/<verifier>/qa/release-evidence.json`. Schema v1 fields: `schema_version`, `verifier_ticket`, `contract_sha256`, `target_branch`, full `target_sha`, `runner` (`name`, exact `command`, `exit_code`, native `results_path`, native `results_sha256`), `checks`, `console_errors`, and `failed_requests`. Do not accept a global or Markdown verdict as truth.
   - Validate the current verifier ID; raw contract hash; configured target branch and host-computed current target commit; each `symphony/<implementation-ticket>` exists and is an ancestor of target; runner exit 0 and hashed native result; exact one-to-one check coverage; every status `PASS`; required viewport coverage; and every cited artifact path contained below `docs/<verifier>/`, existing, regular, non-empty, and SHA-256 matching. Console/page failures and unexpected failed requests are repairable release failures.
   - Return structured immutable results separating `evidence_errors` (malformed, missing, stale, unsafe: rerun same verifier without product tickets) from `repairable_failures` (behavior/runtime/ancestry: grouped repairs), plus target/contract/fingerprint metadata and concise Markdown note text.
   - Add a small read-only commit resolver to `src/symphony/utils/git_inspect.py` only if the existing helpers cannot return a full commit SHA.

3. Host lifecycle wiring in `src/symphony/orchestrator/core.py`
   - On every forward transition produced by Verify, independently of `agent.stage_contracts`, detect `app-release` in normalized labels, refresh the full ticket, and evaluate standard paths from the ticket workspace and workflow repository.
   - Green permits the existing transition. Evidence errors append `## App Release Gate Failure`, restore the producing Verify state, refresh the body, and count as a normal bounded rewind; create no code ticket.
   - Repairable failures use existing tracker APIs to create exactly one idempotent `QUALITY-*` ticket per `repair_group`, starting in `Build` when available, otherwise `In Progress`/the first active lane. Each ticket must include failed check IDs, expected/actual, repro, artifact/ancestry evidence, contract hash, target SHA, source verifier, request grouping, `quality-fix` labels, and the source agent kind.
   - Create exactly one fresh `RELEASE-VERIFY-*` ticket labeled `app-release`, blocked by every repair ticket, carrying the same request and a self-contained instruction to test the new target SHA. Before the failed verifier may close as historical evidence, update the contract's `app-release-finalizer` ticket to depend on that fresh verifier. Use a stable fingerprint from source verifier + contract hash + target SHA + sorted failed check IDs in durable labels/body; retries reconcile partial writes and never duplicate repairs/verifiers.
   - If the tracker lacks atomic create/update support or any durable link fails, fail closed by rewinding the source with exact evidence. Never allow partial repair creation to unblock delivery.

4. Worker/operator surfaces
   - Add `src/symphony/cli/release.py` and route `symphony release check [WORKFLOW] --ticket <ID> --workspace <path> [--json]` in `src/symphony/cli/main.py`, sharing the pure validator and returning non-zero on every non-green result.
   - Add a doctor check in `src/symphony/cli/doctor.py` that reports missing/invalid `release-contract.yaml` when any board ticket is labeled `app-release`, without breaking boards that do not use app delivery.
   - Update `docs/symphony-prompts/file/stages/verify.md` and deep `intake.md`, `plan.md`, `qa.md`, `verify.md`, `document.md` so app delivery inventories every visible control and requirement, generates native evidence on the exact target SHA at desktop/tablet/mobile, runs the CLI gate, creates/consumes repair cycles, and never treats any historical ledger grep as current approval.
   - Mirror the release contract/fresh-cycle rules in `skills/symphony-skill/oneshot/templates/WORKFLOW.oneshot.md`, `templates/SYSTEM.md`, and the directly relevant OneShot references. Keep non-browser OneShot behavior compatible; mechanically reject EDIT-ME/placeholder browser specs and the no-`package.json` false assumption. Do not introduce Playwright as the operator's independent browser proof; shipped worker instructions may remain tool-agnostic/native-runner based.
   - Document adoption and schema examples in `README.md` and the routed Symphony skill reference used for app-delivery/OneShot operation.

5. Tests, red first
   - Add `tests/test_release_contracts.py`: current target pass; stale green/new red cannot pass; wrong verifier; target/contract mismatch; JIRA-003-style unmerged branch; missing/duplicate/failed checks; missing required kind/viewports; runner nonzero; console/network errors; traversal, missing, empty, and hash-mismatched artifacts.
   - Add `tests/test_orchestrator_release_contract_integration.py`: gate active on deep/custom while default stage contracts are off; evidence error rewinds without repairs; red groups repairs and makes a fresh verifier; finalizer link precedes historical close; idempotent retry; partial tracker failure fail-closed; green only with all ancestry/current-SHA facts.
   - Extend `tests/test_workflow_pipeline_prompt.py`, `tests/test_workflow_presets.py`, `tests/skills/test_symphony_oneshot_bootstrap.py`, `tests/test_doctor.py`, and CLI tests. Preserve existing contract/preset regressions.
   - Run targeted tests while building, then the trusted aggregate, Ruff, Pyright, `git diff --check`, and a fresh-context audit. No test/gate weakening.

6. Real acceptance after the Symphony code is independently green
   - Use a disposable clone of `/Users/danny/Documents/PARA/Resource/jira-dev-factory`, preserving its real domain/tickets but never contaminating the source during the run. Configure one serialized OpenCode worker with command `/Users/danny/.opencode/bin/opencode run --format json --auto --model zai-coding-plan/glm-5.2` and a separate service port/workspace root.
   - Seed a grounded Jira `release-contract.yaml` covering all visible controls and audited missing/broken behavior. Create an `app-release-finalizer` and an `app-release` negative-control verifier on a target SHA with one deliberately broken real menu. Prove RED, programmatic repair tickets, GLM-5.2 repair execution/merge, fresh verifier, and GREEN on the new exact target SHA. Continue bounded repair cycles until all checks pass.
   - Capture authoritative API, board, run-registry, log, provider/model/session, target history, artifact, and process-cleanup evidence. Run independent Ego Lite desktop/mobile/control/a11y QA on the final merged clone. After all green, integrate the proven commits into the clean real Jira `main` with a recoverable backup ref; do not push.

## Acceptance checklist

- [ ] Strict schema/target/ancestry/hash/coverage/artifact/latest-verifier validation and valid-pass behavior from GOAL criteria 1-2.
- [ ] Preset-independent app-release transition enforcement from GOAL criterion 3.
- [ ] Idempotent repair groups, fresh verifier, and finalizer dependency from GOAL criterion 4.
- [ ] Malformed/stale and partial-write fail-closed behavior from GOAL criterion 5.
- [ ] Default/deep/OneShot app quality guidance and no stale-green ledger gate from GOAL criterion 6.
- [ ] Glossary, ADR, operator/schema/adoption documentation from GOAL criterion 7.
- [ ] Targeted regression, Ruff, Pyright, and diff checks from GOAL criterion 8.
- [ ] Real OpenCode GLM-5.2 target-SHA red-to-green lifecycle proof from GOAL criterion 9.
- [ ] Independent Ego Lite desktop/mobile/keyboard/a11y/runtime proof from GOAL criterion 10.
- [ ] Broken-menu negative control fails before and passes after merged repair from GOAL criterion 11.

## Tools & Skills

- `symphony-skill` routes: app workflow configuration, operation, deep/OneShot delivery.
- `domain-modeling`: glossary plus ADR for stable lifecycle terms.
- `supergoal`: isolated builder -> evidence-only tester -> independent auditor loop, max 3.
- UI authority for the Jira repair run: Expressive baseline plus dense/admin completeness overlay, Atlassian/Jira design-system direction, dials `3/2/8`.
- Browser: Ego Lite (`ego-browser`) for independent final proof; worker-native browser runner is evidence input, not operator authority.
- Tests: `/Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/pytest`, `.venv/bin/ruff`, `.venv/bin/symphony-pyright`; run with `PYTHONPATH=/private/tmp/symphony-release-contract-LuIfCN/src` when using the original venv.

## Verification strategy

- Before proof: current JIRA-010 claims production readiness while `git merge-base --is-ancestor symphony/JIRA-003 main` is non-zero; `main` lacks JIRA-003 form files; visible controls are inert; current deep and OneShot gates accept prompt/ledger evidence without target-SHA binding.
- Step -> GOAL.md criterion: 1-2 -> 1,2; 3 -> 3,4,5; 4 -> 6,7; 5 -> 8; 6 -> 9,10,11.
- Trusted commands: `PYTHONPATH=/private/tmp/symphony-release-contract-LuIfCN/src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/pytest -q tests/test_release_contracts.py tests/test_orchestrator_release_contract_integration.py tests/test_orchestrator_contract_integration.py tests/test_workflow_pipeline_prompt.py tests/test_workflow_presets.py tests/skills/test_symphony_oneshot_bootstrap.py tests/test_doctor.py tests/test_workflow.py` (frozen_repo)
- Trusted commands: `PYTHONPATH=/private/tmp/symphony-release-contract-LuIfCN/src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/ruff check src tests` and `PATH=/Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin:$PATH PYTHONPATH=/private/tmp/symphony-release-contract-LuIfCN/src /Users/danny/Documents/PARA/Resource/symphony-multi-agent/.venv/bin/symphony-pyright` (frozen_repo)
- Trusted commands: evaluator-owned negative-control script and final Ego Lite task-space execution against the exact merged target SHA (evaluator_owned)

## Grounding ledger

- Which app/target? -> `/Users/danny/Documents/PARA/Resource/jira-dev-factory/jira-clone`, clean local `main` at audit time -> use a disposable clone for the destructive red-to-green run, integrate only after proof.
- Why target ancestry? -> JIRA-003 implementation branch exists but is not an ancestor of main; JIRA-010 passed anyway -> ancestry is a hard host fact.
- What is broken? -> Backlog/Issues fall through to Board; header search/Filters inert; initial theme icon blank; three-field form omits requested fields; mouse-only cards; silent storage failure; clipped/overlapping UI -> contract inventory and repair groups are grounded, not speculative.
- How autonomous? -> user explicitly authorized new fix tickets until all pass -> core creates idempotent grouped repairs plus a fresh verifier; bounded at three conductor iterations.
- Which model? -> installed OpenCode 1.18.14 resolves `zai-coding-plan/glm-5.2`; model selection must be embedded in command -> perform a real provider/model/session-correlated probe and run.

## Amendment - 2026-08-09 host authority hardening

Fresh-context adversarial review showed that diagnostic labels and a board
create followed by a registry write could not satisfy the promised crash and
peer-service guarantees. The host lifecycle therefore uses durable SQLite
cycle generations, exact verifier/finalizer run bindings, append-only evidence
identity, and pre-create deterministic reservations for every repair and fresh
verifier ticket. RED lifecycle mutation is fenced to the exact live verifier
lease. Finalizer completion is bound to the exact terminal ticket bytes and
file-replacement generation observed before and after persistence; any rewrite
invalidates approval and requires a fresh cycle. These changes strengthen the
approved fail-closed contract without expanding it beyond local file boards.
