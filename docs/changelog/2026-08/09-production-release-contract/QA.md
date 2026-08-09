# QA - Production application release contract

All testing results as succinct plain-language checklist sentences. Evidence lives in `qa/`.

- Verdict: PARTIAL

## Before

- [x] The real Jira release is a false green: JIRA-003's implementation branch is not an ancestor of local `main`, but JIRA-010 is Done and claims production readiness. - evidence: `qa/before-jira-release-integrity.txt`
- [x] Existing release evidence bypasses real drag, omits visible controls and full form requirements, and explicitly substitutes static analysis for Safari execution. - evidence: `qa/before-jira-evidence-audit.md`
- [x] Deep/OneShot delivery gates accept historical ledger greps and do not bind evidence to the target commit. - evidence: `qa/before-symphony-gates.txt`

## Results

- [x] Builder release validator, file-board lifecycle, CLI, doctor, prompt, and OneShot focused gates pass locally.
- [x] Builder lint, type, and whitespace gates pass locally.
- [x] Independent adversarial review closed stale-lease, crash/concurrency identity, cleanup, exact-target, replay, and ticket-version races with executable regressions.
- [x] The complete Symphony suite passes outside the managed semaphore restriction: `2011 passed, 8 skipped`.
- [ ] Independent QA, real Jira lifecycle, and independent browser acceptance remain pending.

Backward-trace: pending

## Commands

| Command | Source | Proves |
|---|---|---|
| `git -C /Users/danny/Documents/PARA/Resource/jira-dev-factory merge-base --is-ancestor symphony/JIRA-003 main` | evaluator_owned | The required implementation branch did not reach the declared release target. |
| `pytest -q tests/test_release_contracts.py tests/test_orchestrator_release_contract_integration.py tests/test_orchestrator_contract_integration.py tests/test_workflow_pipeline_prompt.py tests/test_workflow_presets.py tests/skills/test_symphony_oneshot_bootstrap.py tests/test_doctor.py tests/test_workflow.py` | frozen_repo | Release validator/lifecycle/prompt compatibility and existing contract regressions. |
| `ruff check src tests` | frozen_repo | Static lint gate. |
| `symphony-pyright` | frozen_repo | Repository type gate using the active interpreter. |
| `pytest -q tests/test_release_contracts.py tests/test_orchestrator_release_contract_integration.py` | frozen_repo | Builder result: `62 passed in 24.34s`; exact-target/runner/evidence authority and durable file-board release lifecycle, including adversarial repair cases. |
| `pytest -q tests/test_release_contracts.py tests/test_orchestrator_release_contract_integration.py tests/test_orchestrator_contract_integration.py tests/test_workflow_pipeline_prompt.py tests/test_workflow_presets.py tests/skills/test_symphony_oneshot_bootstrap.py tests/test_doctor.py tests/test_workflow.py` | frozen_repo | Builder result: `308 passed, 2 skipped in 25.58s`; focused aggregate contract, lifecycle, prompt, doctor, workflow, and OneShot compatibility. Skips require an installed `symphony` CLI and are covered statically. |
| `pytest -q tests/test_release_cli.py tests/test_cli_main_routing.py` | frozen_repo | Builder result: `16 passed in 1.33s`; release command behavior and CLI routing. |
| `ruff check src tests` | frozen_repo | Builder result: all checks passed. |
| `symphony-pyright` | frozen_repo | Builder result: 0 errors, 0 warnings, 0 informations. |
| `git diff --check` | frozen_repo | Builder result: clean. |
| `pytest -q tests/test_orchestrator_release_contract_integration.py tests/test_release_verifier_lease_fencing.py tests/test_run_registry.py` | frozen_repo | Stable authority/lifecycle result: `94 passed in 45.99s`, including stale-peer lease refusal, deterministic pre-create reservations, crash recovery, concurrent reconciliation, and non-replayable finalizer completion. |
| `pytest -q` | frozen_repo | Stable complete result outside the managed semaphore restriction: `2011 passed, 8 skipped in 224.83s`; no product failures. |
| `pytest -q tests/test_projects.py::test_concurrent_processes_serialize_registry_and_port_allocation` | evaluator_owned | The sole sandbox-only failure passes outside the sandbox: `1 passed in 1.20s`. |
| `ruff check --no-cache src tests && symphony-pyright && git diff --check` | frozen_repo | Stable result: Ruff passed; Pyright `0 errors, 0 warnings, 0 informations`; whitespace clean. |

## QA

Tool: ego-browser
UI-tier: Expressive + dense/admin completeness overlay
- Browser proof pending after the real OpenCode run.

## Reproduction Fidelity

- Fidelity level: exact
- Residual risk from data gap: No production server/data exists; the app is a localStorage-only static client. Exact repository history, source, stored browser behavior, and release artifacts are available.
- Post-deploy confirmation plan: Re-run the machine release gate and Ego Lite task space on the integrated real local `main` SHA; no remote deployment is in scope.

## Residual Risk

- Not proven: autonomous Jira lifecycle, final browser behavior, and integration into the user's Jira `main`. A real GLM-5.2 provider/model probe is proven, but the full Symphony repair run remains pending.
- Follow-up: rebase the green source candidate, seal the Jira fixture, run the autonomous release loop, then perform independent Ego Lite acceptance.
