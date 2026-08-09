# GOAL - Production application release contract

Single source of "done". Only the verifier ticks a box; unticking needs regression evidence.
Never delete or reword an unmet criterion - append. Mid-run discovered musts are APPENDED as new
unchecked criteria tagged `(surfaced: ...)`. Ambiguous/product-changing candidates go to
`## Decision Gates` as `ask-user`, not into criteria.

## Original Request

> app was made using oh my symphony [Pasted Content 2328 chars] but quality is bad some menus dont work ui ux is not up to my expectations features missing. from these learning what. what improvements can be made to enhance the app to produce high quality production software autonoumously with oh-my-symphony implement the improvement make fully functional and do a full e2e run using agents opencode glm-5.2 as model
>
> 1 was made /Users/danny/Documents/PARA/Resource/jira-dev-factory/jira-clone 2 yes all 3 symphony can issue new tickets to fix the low quality untill all pass (done autnomously)

## Spec

Add a machine-enforced release contract for browser application delivery. A dedicated `app-release`
verifier must prove the exact configured target commit, every declared feature and visible control,
desktop/tablet/mobile behavior, keyboard/focus/accessibility behavior, visual review, storage/runtime
failure behavior, and clean console/network results. Evidence is structured, bound to the raw contract
hash and current target SHA, and validated against real non-empty hashed artifacts. Historical green
text is never authoritative.

When current evidence is malformed or stale, fail closed and rerun the verifier. When real behavior or
branch ancestry fails, create idempotent repair tickets grouped by the contract, create a fresh verifier
blocked by those repairs, and relink the release finalizer before closing the failed verifier. This gate
must run independently of the default four-stage contract switch so deep/custom application pipelines
cannot silently disable it. Existing non-application tickets and workflows without `app-release` labels
remain compatible.

The real acceptance run uses OpenCode `zai-coding-plan/glm-5.2` against an isolated clone of the Jira
factory. It must demonstrate a red release, autonomous repair-ticket creation, merged repairs, a fresh
target-SHA-bound green verifier, and independent Ego Lite desktop/mobile verification. Proven Jira
changes are then integrated into the user's clean local target without pushing.

Design Read: dense Atlassian/Jira-style project tracker using one coherent system; `DESIGN_VARIANCE=3`,
`MOTION_INTENSITY=2`, `VISUAL_DENSITY=8`; Expressive baseline plus dense/admin completeness overlay.

## Success Criteria

- [x] A strict release-contract schema rejects missing/duplicate check IDs, missing required quality kinds, malformed evidence, stale verifier IDs, stale target SHAs, stale contract hashes, failed runner/native results, console/network errors, unsafe/missing/empty/hash-mismatched artifacts, and unmerged implementation branches. - verify: `pytest -q tests/test_release_contracts.py`
- [x] A valid latest-cycle evidence set for the current target SHA and fully merged implementation branches passes without reading Markdown verdict text. - verify: `pytest -q tests/test_release_contracts.py::test_current_target_complete_evidence_passes`
- [x] The release gate runs for `app-release` Verify transitions even when ordinary stage contracts are disabled on a deep/custom board. - verify: `pytest -q tests/test_orchestrator_release_contract_integration.py`
- [x] Repairable failures create one idempotent repair ticket per repair group, a fresh verifier blocked by those repairs, and a durable finalizer dependency before the failed verifier may close. - verify: `pytest -q tests/test_orchestrator_release_contract_integration.py`
- [x] Malformed/stale evidence fails closed without inventing a product repair, and partial tracker writes neither duplicate work nor allow delivery. - verify: `pytest -q tests/test_orchestrator_release_contract_integration.py`
- [x] Shipped default/deep/OneShot app-delivery guidance requires target-SHA evidence, full control inventory, responsive/keyboard/a11y/visual/runtime proof, latest-cycle semantics, and autonomous repair routing without ledger-wide stale-green grep gates. - verify: `pytest -q tests/test_workflow_pipeline_prompt.py tests/test_workflow_presets.py tests/skills/test_symphony_oneshot_bootstrap.py`
- [x] The public operator contract documents the new domain terms, schema, CLI preflight, limitations, and configuration/adoption path. - verify: `git diff --check && rg -n "app-release|release-contract" CONTEXT.md docs README.md skills/symphony-skill`
- [x] The relevant Symphony regression, lint, and type gates pass with no unnamed drift. - verify: `pytest -q tests/test_release_contracts.py tests/test_orchestrator_release_contract_integration.py tests/test_orchestrator_contract_integration.py tests/test_workflow_pipeline_prompt.py tests/test_workflow_presets.py tests/skills/test_symphony_oneshot_bootstrap.py tests/test_doctor.py tests/test_workflow.py && ruff check src tests && symphony-pyright`
- [ ] A real OpenCode GLM-5.2 Symphony run against the Jira clone proves red -> repair tickets -> merged fixes -> fresh green release on the exact target SHA, with no running/retrying workers or orphaned agent processes. - verify: saved `/api/v1/state`, `/api/v1/board`, `/api/v1/runs`, Symphony log, and provider/model/session evidence under the E2E run artifacts
- [ ] Independent Ego Lite QA proves every declared Jira control and critical flow at desktop and mobile, including full issue creation, real pointer/keyboard status change, menus, focus, persistence failure, responsive layout, and zero unexpected console/network errors. - verify: Ego Lite task-space evidence plus final merged-target release evidence
- [ ] A deliberate broken-menu negative control is rejected before repair and the same check passes only after the repair lands on the target branch. - verify: E2E release-cycle evidence for both target SHAs

## QA Cases (web apps only)

- [ ] Jira desktop 1280px: exercise every nav/header/menu/control, full issue/project/detail flows, real pointer movement, and persistence after reload with no unexpected errors. - evidence: `qa/jira-desktop.md`
- [ ] Jira tablet 768px and mobile 375px: all actions remain reachable, no document overflow/clipping, sidebar/dialog behavior works, and screenshots show coherent hierarchy. - evidence: `qa/jira-responsive.md`
- [ ] Keyboard/a11y: tab order, accessible names, visible focus, modal trap/return, shortcut behavior, and keyboard status change work. - evidence: `qa/jira-a11y.md`
- [ ] Negative control: one broken visible menu yields RED and repair tickets; repaired target yields PASS under a fresh verifier/target SHA. - evidence: `qa/jira-negative-control.md`

## Decision Gates

| ID | Action | Status | Finding | Decision | Recheck |
|---|---|---|---|---|---|
| d1 | no-op | resolved | The user's actual Jira repository has no remote. | Integrate only into local `main`; do not push or claim remote publication. | final Git history proof |
| d2 | no-op | resolved | Current source checkout had concurrent release work. | Implement only in isolated `codex/production-release-contract-20260809` worktree based on released `v0.19.0`. | `git status` and ref check |
| d3 | auto-fix | resolved | JIRA-003 implementation never reached `main`, while JIRA-010 still passed. | Treat implementation-branch ancestry and exact target SHA as hard release facts. | negative-control E2E |
