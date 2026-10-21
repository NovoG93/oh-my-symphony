# Upstream synchronization ledger

## Integration record

- Remote `upstream`: `https://github.com/cskwork/oh-my-symphony` (fetch; push is `DISABLED`).
- Remote `origin`: `https://github.com/NovoG93/oh-my-symphony` (fetch/push).
- Integrated ref: `upstream/dev` at `aaf2b72da3b97fb9d0b71fb881fafea38f59de6d`.
- Base: `8b28dbba8f2b59952acea518d52b68c5a42bc1b0`.
- Observed `upstream/main`: `1f56213aebbab69f8cd73bf0f4f8c63f16307eaf`.
- M1: `b92ba932ea52ec5fe56b467320cb40401c35a1b1`.
- M1 parents: `d97795affee1e800fb57539e799bc8559e61f6ad` and `aaf2b72da3b97fb9d0b71fb881fafea38f59de6d`.
- M2: `bec62af9ec1fe8ce9f0d0aa72f171c7a6417d34a`, subject `merge: integrate upstream functional hardening`.
  - Parents: `d97795affee1e800fb57539e799bc8559e61f6ad` and `da286d22c5ea4cf78c2365d3655ea0fa658e63c9`.
  - The subsequent docs-only commit is necessary because a commit cannot contain its own final hash.

The integration intentionally used `upstream/dev`: it is the requested functional-hardening line and its range is auditable from the recorded base to the integrated head. Upstream/main-only release and documentation commits were excluded because they are outside this functional-dev synchronization scope and are not ancestors in the selected dev range.

## Upstream/dev commit enumeration

Every commit in `git log --reverse 8b28dbba8f2b59952acea518d52b68c5a42bc1b0..aaf2b72da3b97fb9d0b71fb881fafea38f59de6d` is listed individually (21 commits):

1. `91a19c1820aaf53d1f5a81e3b06d2c4eb770d16f` — `docs(skill): tighten symphony-skill per skill-authoring best practices` — **applied**.
2. `306a6d4326d32fb313428234849875e9f7b622bc` — `fix(webapi): add optional SYMPHONY_API_TOKEN bearer gate and loopback-only debug tasks` — **applied**.
3. `cc6e57542d7c1ea71c91b584eaab862eddf6c773` — `feat(tui,web): usability quick wins across board surfaces` — **applied**.
4. `466ed92bcadd45e6cf2fc326b24d32371909274c` — `refactor: layering, backend dedupe, and event-loop hygiene` — **adapted**.
5. `72b9dbfa980b828d2877b42b3f871bf1a01517be` — `fix(win): kill the full process tree when terminating backends` — **applied**.
6. `7636d29cc11fb37356c62235840e320881ac0cb5` — `test(win): capability-probed skips replace environment failures` — **applied**.
7. `bc3acb1255c73debc307775f816228efbaf4913c` — `fix(win): Windows path, junction, locking, and argv bugs` — **applied**.
8. `c6047c30203f86c3fc13ff8ecaad1a801f469f90` — `fix(doctor): keep backslash paths intact in agent CLI preflight on win32` — **applied**.
9. `27893dd2701d790e3244fafc6c356430832cf814` — `chore: refresh uv.lock to 0.20.1 (stale pin caught up by uv sync)` — **applied**.
10. `587a28d888db7d009a3d44b08103f6ca6aadc411` — `fix(win,tests): clear remaining Windows gate failures` — **applied**.
11. `a2888748d3f62b3cac4978833459e31b6b73690e` — `feat(web,auth): SPA token support and stale-board keyboard gating` — **applied**.
12. `c794660debd9887028a85f89264a2d9959447562` — `fix(win): harden kill paths, junctions, and hook capture from adversarial review` — **applied**.
13. `664775cfe71dcf6dc058a6e86163cb67bde68b8a` — `fix(core): off-loop kills and tracker-client close-race safety` — **applied**.
14. `9021e5d48fd2f0fc9d6e923cdf41565b96512cd3` — `docs(contributing): document detached pytest workaround for Windows console Ctrl+C pollution` — **applied**.
15. `6605d42ddc9d4a6a74e5ff4f9ae1875d01ca5598` — `fix(core): complete review follow-ups - off-loop reclaim, identity-gated kills, win32 liveness` — **applied**.
16. `41a07d879df21738e682a7fdfe9b9811bdb5379a` — `test(web-e2e): match stub orchestrator seam methods to the async webapi handlers` — **applied**.
17. `1a5a7cece37032be333f4efb1050fb5561290793` — `docs: session handoff - state, environment notes, and next steps` — **applied**.
18. `7981dc1d21eaf455ec75966991a1cc42719cb38b` — `refactor(runtime): isolate phases and stop safely` — **partially deferred**: the `_AgentPhaseState`/`_AgentPhaseTransition`/`_transition_agent_phase` portion is deliberately deferred and absent; non-phase service/reliability portions were adapted.
19. `4ba47cd3d5d0d8a96ec68614b02acd9eb75a576a` — `fix(core): harden cross-platform checks` — **applied**.
20. `08d731bc2b6d0796ddaf29e46e7be2d468c52b56` — `fix(service): authenticate health probes` — **applied**.
21. `aaf2b72da3b97fb9d0b71fb881fafea38f59de6d` — `fix(oneshot): select executable Python` — **applied**.

## M1 conflict resolutions

- `src/symphony/backends/claude_code.py`, `src/symphony/backends/codex.py`, `src/symphony/backends/per_turn.py`: combine upstream consolidated bounded lifecycle/process termination with fork profiles/sessions/usage/Copilot routing and dynamic version paths.
- `src/symphony/orchestrator/core.py`: combine upstream async/shared-tracker/identity reliability with fork profiles, usage pools/quota, release and RCA; preserve active inline phase transitions.
- `tests/test_doctor.py`: union API-token and fork Copilot checks (25 total).
- `tests/test_run_registry.py`: union Win32/POSIX liveness coverage and fork stage-profile registry coverage.

## Evidence and promotion gate

Batch-focused and full evidence recorded for M1: **2722 passed / 14 skipped / 83.81%**; affected tests **566 passed**; security **159 passed**; live API **10/10**; Ruff, Pyright, i18n, uv-lock, MCP, CLI, service, Node, and diff checks passed. The orphan-isolated check passed. Doctor has the expected protected-repository and missing-Copilot FAILs described above.

Windows-native tests were unavailable and are explicitly **not claimed**. On **2026-08-29**, the user explicitly waived the Windows-native proof and authorized proceeding with M2. This waiver does not convert unrun checks into evidence: residual risk remains for backend process-tree termination, Win32 path/junction/locking/argv behavior, doctor path handling, capability-probed skips, run-registry liveness, hook capture, and cross-platform service/core checks. These scenarios remain required follow-up validation.

Post-M2 verification passed: **2722 passed / 14 skipped / 83.81%**; Ruff, Pyright, uv-lock, and i18n checks passed; focused evidence passed (**574/159**); MCP and service smokes passed; ancestry checks passed. Windows-native proof remains explicitly waived, not claimed.

## Future upstream audit commands

Use these commands to discover and audit future upstream work:

```bash
git fetch upstream
git log --reverse develop..upstream/dev
git log --left-right --cherry-pick develop...upstream/dev
git range-diff develop...upstream/dev
git log --first-parent develop..upstream/dev
git merge-base develop upstream/dev
git merge-base --is-ancestor develop upstream/dev
```

## Integration record (v0.25.0 / dev synchronization)

- Remote `upstream`: `https://github.com/cskwork/oh-my-symphony` (fetch; push is `DISABLED`).
- Remote `origin`: `https://github.com/NovoG93/oh-my-symphony` (fetch/push).
- Base: `aaf2b72da3b97fb9d0b71fb881fafea38f59de6d`.
- Integrated ref: `upstream/dev` at `f69e7cd7540296aedac856c9a7338653a47e6bb2` (`v0.25.0`).
- Branch: `feat/sync-upstream-v0.25-hardening`.
- Total upstream commits in range: 73 commits.
- Integration strategy: Selective Adaptation over blind merge.
  - Fork invariants preserved: Named Profiles & Tiered Routing (`TIER-001`/`TIER-002`), Usage Pools & Quotas (`ProviderUsageManager`), Copilot backend, and inline phase transitions.
  - Upstream's monolithic decomposition of `core.py` (which depends on the deferred `_AgentPhaseState` machine) was intentionally deferred.

### Enumeration of adapted upstream commits:
1. `86d8b73` / `4286425`: CSRF gate on body-less mutations in `web_policy.py`, service startup zombie detection with `waitpid(WNOHANG)`, stale lock reclamation, `killpg` fallback to `os.kill`. (Committed: `edcda42`)
2. `0126a2a`: Codex v2 token subset double-counting fix. (Committed: `edcda42`)
3. `3f71d30`: Release binder target branch resolution. (Committed: `edcda42`)
4. `d67428e`: Symlink loop protection on Python 3.13+. (Committed: `edcda42`)
5. `56ff023`: Done->reopen budget cap with `agent.max_reopens` and `## Reopen Approved`. (Committed: `4b44e86`)
6. `f46a026` / `ed389a3`: Mechanical Done contract enforcement, deep preset artifact validation, and inline heading regex support. (Committed: `4b44e86`)
7. `babeba4`: Dynamic lane rewind detection, gate metrics fold, and autonomous learning loop via `board_health`. (Committed: `4b44e86`)
8. `58a9537`: Release dispatch slots on terminal worker cancellation. (Committed: `4b44e86`)
9. `5e97ba5`: Jira structured ADF notes and idempotent `append_note_once`. (Committed: `4760449`)
10. `c025fa0`: Issue budget snapshots (`/api/v1/issues/{id}` budgets/scheduling) and Codex cache token telemetry. (Committed: `5618e87`)
11. `34fe676`: Doctor agent binary and profile probing with `--version`. (Committed: `58d1128`)
12. `e828324`: Intent gate trip-wire heuristics scan and track downgrade. (Committed: `311250e`)
13. `fd5a7f6`: Kahn's algorithm topological order, iterative cycle DFS, and robust `except BaseException` per-turn process reap. (Committed: `180b634`)
14. `6512e5a`: TUI grouped `HelpScreen` modal on `?`, CLI subcommand list in `--help`, and fast reject for mistyped subcommands. (Committed: `d2923bb`)
15. `d51bfa2`: `JsonlStreamBackend` shared streaming skeleton for Claude Code and Pi while preserving all fork usage probes and model injection. (Committed: `e7a9f2c`)

### Full Evidence & Quality Gates:
- Full test suite: **3004 passed, 15 skipped in 202.24s (0:03:22)**.
- Type check: `pyright` **0 errors, 0 warnings, 0 informations**.
- Code quality & linter: `ruff check src tests` **All checks passed!** (0 errors).
- Doctor preflight: `symphony doctor ./WORKFLOW.md` **PASS** (expected protected source repository warning only).
- Pre-commit hook: smart pytest selector running single, targeted, or complete pytest depending on staged changes.

## Integration record (v0.28.0 reliability and Human Review)

- Source: `upstream/dev` at `c6a39473373f42393396e47114e2de0cb4cc8c8c` (v0.28.0).
- Rewritten-history boundary: old integrated head `aaf2b72da3b97fb9d0b71fb881fafea38f59de6d` maps to `3e8402080e48ac169a4d21cc63da0e04717f63fa` with identical tree and patch ID; the 21-commit prior range matches under `git range-diff`. The offered range is therefore `3e840208..c6a3947` (191 commits: 141 non-merge, 50 merge; 29 first-parent), avoiding a misleading comparison across the rewrite.
- Upstream `main` at `796e9de45c777399b02c4ec71fa0e4db4659350b` is three merge commits ahead of `dev` with zero tree diff; `dev` is the selected functional source.
- Baseline preservation: `8da35d641fb103b84767c31622b240b0d44caa77` is the no-ff merge of the existing 11-commit fork branch into `develop` (parents `7917524b4007ba49afd95ecdaa68275a9ee3e0be` and `5cb758d14f745623a05eb935c4fb541786419906`).
- Rewritten boundary bridge: `df64c1b` records the tree-equivalent rewritten upstream boundary without changing the fork tree. Integration branch: `feat/sync-upstream-v0.28-review`.
- M1: `9a9ea969d9eb12918cf65d30a35e92ec209feb7c`, subject `merge: adapt upstream v0.28 reliability and review`. Parents: integration pre-merge `ad31537f325fd1f5585cead0910101f4abb81c67` and `upstream/dev` `c6a39473373f42393396e47114e2de0cb4cc8c8c`. Focused/full tests and the repository commit hook passed. A docs-only follow-up records M1 because a commit cannot contain its own final hash.
- M2: `aa4d5b0c6270042757a0d7abf977b9b9bf8978b0`, subject `merge: promote upstream v0.28 reliability and review`. Parents: pre-promotion `develop` `df64c1b6f8dadf799eb7b6d06a0ff94bb0406db4` and reviewed integration tip `6c563b6383fc5ca857997b524d8c2dbb1f5820b4`. This is the visible no-ff promotion. The docs-only follow-up records its final hash and parents.
- Final ancestry checks: the preserved fork tip `5cb758d14f745623a05eb935c4fb541786419906` and upstream head `c6a39473373f42393396e47114e2de0cb4cc8c8c` are ancestors of `develop`; `git merge-base develop upstream/dev` is `c6a3947`, and `git merge-base develop origin/develop` is `7917524`. Local `develop` is ahead of fetched `origin/develop`; it has not been pushed.
- Integration method: selective adaptation inside the no-commit M1 merge. Existing fork tree and behavior were preserved, then only the accepted reliability and Human Review gaps were adapted. The index contains no unrelated upstream replacement.

### M1 conflict inventory

The open merge recorded 73 conflict paths. Resolution started by restoring the fork tree across the open merge, then applying only the selected reliability and Human Review adaptations. Within the conflict set, `src/symphony/orchestrator/core.py` and `src/symphony/webapi.py` received the reviewed adaptations; `tests/test_orchestrator_dispatch.py`, `tests/test_tui.py`, and `tests/test_webapi.py` received focused regression/contract updates. All other conflict paths below retain the fork implementation under the explicit deferrals and invariants above. New lifecycle coverage is in `tests/test_confirm_done.py`; the smoke stub adjustment is in `tests/test_web_api_smoke_script.py`.

Complete conflict-path inventory (73):

`.gitignore`, `AGENTS.md`, `CHANGELOG.md`, `README.ko.md`, `README.md`, `WORKFLOW.example.md`, `WORKFLOW.file.example.md`, `WORKFLOW.md`, `docs/index.html`, `examples/WORKFLOW.jira.example.md`, `pyproject.toml`, `src/symphony/__init__.py`, `src/symphony/_shell.py`, `src/symphony/backends/__init__.py`, `src/symphony/backends/agy.py`, `src/symphony/backends/claude_code.py`, `src/symphony/backends/codex.py`, `src/symphony/backends/kiro.py`, `src/symphony/backends/per_turn.py`, `src/symphony/backends/pi.py`, `src/symphony/backends/prime_agent.py`, `src/symphony/chat.py`, `src/symphony/cli/board.py`, `src/symphony/cli/doctor.py`, `src/symphony/cli/main.py`, `src/symphony/errors.py`, `src/symphony/intent.py`, `src/symphony/orchestrator/attempt.py`, `src/symphony/orchestrator/contracts.py`, `src/symphony/orchestrator/core.py`, `src/symphony/orchestrator/entries.py`, `src/symphony/orchestrator/helpers.py`, `src/symphony/orchestrator/migrations.py`, `src/symphony/orchestrator/release_contracts.py`, `src/symphony/orchestrator/run_registry.py`, `src/symphony/projects.py`, `src/symphony/server.py`, `src/symphony/service.py`, `src/symphony/stats.py`, `src/symphony/trackers/file.py`, `src/symphony/trackers/linear.py`, `src/symphony/utils/atomic_json.py`, `src/symphony/web/static/app.js`, `src/symphony/web/static/i18n.js`, `src/symphony/web/static/style.css`, `src/symphony/webapi.py`, `src/symphony/workflow/__init__.py`, `src/symphony/workflow/builder.py`, `src/symphony/workflow/config.py`, `src/symphony/workflow/constants.py`, `src/symphony/workflow/presets.py`, `tests/test_backend_contract.py`, `tests/test_backends.py`, `tests/test_backends_lifecycle.py`, `tests/test_chat.py`, `tests/test_chat_intent.py`, `tests/test_cli_main_routing.py`, `tests/test_deep_preset_e2e.py`, `tests/test_doctor.py`, `tests/test_migrations.py`, `tests/test_orchestrator_dispatch.py`, `tests/test_orchestrator_phase_transition.py`, `tests/test_release_contracts.py`, `tests/test_run_registry.py`, `tests/test_service.py`, `tests/test_stats_skills.py`, `tests/test_tracker_linear_full.py`, `tests/test_tui.py`, `tests/test_web_static_contract.py`, `tests/test_webapi.py`, `tests/test_webapi_auth.py`, `tests/test_webapi_chat.py`, `uv.lock`

### Adaptations and boundaries

- AGY transient 503 output now retries for `service is currently unavailable` and `unavailable (code 503)`.
- Human Review confirmation now flows through `Orchestrator.confirm_done` from the existing `POST /api/v1/issues/{identifier}/confirm-review`, generic Human Review-to-Done PATCH, and TUI hotkey. The current response and UI contract are preserved. The lifecycle validates worker/workspace/release authority, acquires and renews the run lease, finalizes host delivery history, applies the existing merge and after-Done policies, and refreshes observers. It rechecks issue identity, state, release authority, and lease around awaited delivery work; FileBoardTracker uses an atomic conditional final transition.
- Dependencies, cancellation cleanup, doctor probing, release contracts, and delivery behavior listed as already adapted below are preserved without duplicate implementation.
- Explicit deferrals: Factory and PM Workspace features; plain-text profile expansion; lean-core/module and phase-state refactors; unrelated documentation/launcher changes; and Windows-only upstream changes. Existing fork behavior remains. No native Windows validation is claimed.
- Known limitations: non-file tracker adapters use a fresh read followed by an update rather than a backend atomic compare-and-transition; generic PATCH can persist non-state fields before confirmation subsequently rejects. File-board release identity is checked immediately before final lane CAS, but label and state changes are not one atomic operation.

### Offered upstream commits and dispositions

The table lists every commit in the rewritten source range, including merge commits. Adapted and already-adapted changes are named explicitly; the remaining commits are individually dispositioned as deferred or history-only.

| Commit | Subject | Disposition |
|---|---|---|
| `f843493f87aabfd8defc968f22d1916f2b129a73` | fix(workspace): exclude prior wip paths from final squash | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `f202eaa2183fa87aa0d5b52fa09a61b18efd0be1` | fix(orchestrator): honor human review done handoff | superseded by `89eaf07`; current host-delivery confirmation adapted below |
| `6104d2d89ab342c30c9723edbc89929fe05fab20` | test(orchestrator): isolate confirm done side effects | upstream test setup superseded by fork-specific lifecycle tests in `tests/test_confirm_done.py` |
| `256b57d441fa021b36884de73305f7531a20669d` | feat: add autonomous development factory | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `0f1d18eeef1ebc9d5dfa7d60da245f88ad4eba07` | docs: record autonomous factory delivery | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `b87d4df9b8c1335eb0e7e9fc1c23624defdd815f` | test: make factory CLI checks hermetic | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `d2c8032986bd4cdde89955f3861393b272467ad8` | wip: bundle factory skills and add handoff | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `eeaf99274ece10962991f43cea56298a0a070911` | docs: record factory checkpoint blocker | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `a634d49eb64a4490d6d0854a53cd701f2d7a68e1` | chore(release): merge dev for 0.21.0 | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `131ee8818286d9d4f7a76d21775486c28ada403b` | chore(release): prepare 0.21.0 | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `27f771f2f07b4e5a35661e5468a2bfac5770cf4a` | docs(readme): cut AI tells from README and README.ko | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `1955f906fca68c4e10025e1f14592f323e1bf89b` | Merge branch 'docs/unslop-readme' | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `53049ef559b4cf8b4aa2bc6874ded9e24ad64c49` | docs(readme): loosen the release-gate prose | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `f48cad01debde787fbfe53698cf0be1e363b4923` | Merge branch 'docs/unslop-release-gate' | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `03a2c26229c701ebb44cc85eed32e83d7457a9d9` | docs: cut AI tells from architecture.md and CONTRIBUTING.md | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `b548d08a31794cb17e4b2ba22f6d3340d0aaddbc` | Merge branch 'docs/unslop-docs' | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `420c9ec5f92b230d6bec1e500a46be473f633ccb` | docs: cut em dashes from the agent-facing entry points | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `59a0c15ce22f499d4187160202b281ea9ea3a3bb` | Merge branch 'docs/unslop-agent-files' | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `b4c0b4627c56b2fc25d4fa93d98278bff637edf3` | fix(linear): restore polling with current relation schema | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `1df6ed0f2be84b1bd787b9b1e683d517364c8a86` | chore(sdlc): record Linear polling fix as shipped | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `9801d4bb6afb7b283b54ab2a93e987109824a05d` | docs(plans): dark-factory cycle design and implementation plan | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `9b93e3712b2a9a4057c3e1cf14e9cdb828b4c5c3` | fix(orchestrator): retry state renames and namespace state per workflow (#30, #32) | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `a0b92eeec656b11eefd8c1386bcc97467262eafa` | fix(dispatch): isolate per-dispatch env in BackendInit and include retry-pending tickets in the conflict pre-check (#28, #29) | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `873387c437677c9fa4b399430db36e88e8df83f5` | feat(chat): intent proposals with server-owned approval as the single human gate | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `abddd30b53db30d5fa720b5d6d2aeffcb629f873` | feat(pipeline): intake consumes the approved intent; chat can create deep-preset projects | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `fbe408220f4d7221fede2d696035cfcce1097e33` | feat(web): intent approval card in chat | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `814527f52b74e0bc2b296d363f7428d6c8d5161a` | docs: describe the chat intent gate and the dark-factory cycle | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `740d42768e4273ca4b3040e880282ba617bd6fe3` | docs: retire the board-intake wording for the intent gate | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `7201c77e710282218fb8e8e4d81da4f3a8bc38af` | docs: fix chat intake anchors | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `13242b40331960980a649f900bc96caaed068599` | refactor(orchestrator): move the worker-exit state machine to worker_exit.py | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `766b1c97b46b8189a212c9a814967425b0dbf6b9` | refactor(backends): claude and pi share the per-turn skeleton via JsonlStreamBackend | already adapted by fork commit `e7a9f2c`; existing usage probes/model injection are preserved |
| `d58b763108caef77363018eebb6b3f66c73c002d` | refactor(orchestrator): split _run_agent_attempt into phases in attempt.py | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `eebd92542f4f4b3f17cb1dfca874f989eab570ea` | docs: refresh handoff and changelog for the dark-factory branch | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `50541e85807e19a50ef6697cdd6859085a86fd47` | docs(handoff): record the pytest-xdist trial | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `998d6a8461e6cbc7695783aa9489942451a18e6b` | chore(release): prepare 0.22.0 | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `905c8fb9c060e83199e63eb91e7a59352c7ffed3` | Merge branch 'feat/dark-factory-cycle' into dev | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `7265e375b1987aa62c29205b14022263b5741227` | Merge branch 'dev' for 0.22.0 | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `3f3e7661c282dddc2732dd23513a6238b44a81ca` | chore(release): update the landing badge to 0.22.0 | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `bbead62052b60ad372c8727a123e5bed8ebd7c5e` | Merge branch 'dev' (landing badge 0.22.0) | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `5338d6f8e14d5c0a77ba6fd296827681f2663513` | chore(sdlc): set lazymode 3 (intent is the only human gate) | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `30875f3da23f1a204c2856385e37e0ebaadecc24` | docs(handoff): record the live dark-factory end-to-end run | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `3ce1c013f2f71c65b34b536b14fef3011c532bbb` | Merge branch 'dev' (lazymode 3, e2e handoff) | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `ae445a8abe660e616ea0ed98dc9b44df14ef2743` | docs(handoff): record the empty target_branch release-binder defect found in the E2E | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `ee4d25fefa8a326fbfde309cfbddd0f88384fcea` | Merge branch 'dev' (handoff: release-binder finding) | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `dcd5316f2ef37aaf9b091edd5ad02f573469a2aa` | feat(tui,cli): grouped help modal, subcommand list in --help, reject mistyped commands | already adapted by fork commit `d2923bb` |
| `c1d06c9e278aa9cecacc8b6dea7e6bf0e64dc413` | fix: close 9 HIGH defects in web auth, service lifecycle, and TUI edit/refresh paths | already adapted: service and web hardening represented in existing fork code |
| `03c32d306533908a2c2aa0284f108a0fd9db571c` | docs: printable bilingual manual on Pages, PDFs rendered at deploy | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `fed32bfa19c4f287daabff4fc3ec26c5b35ce807` | chore(release): prepare 0.23.0 — browser-scoped JSON guard, build/deploy split for Pages | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `470fd7ecc2fa242049fd9789084936afc525328b` | fix(orchestrator): make contract rewinds real and accept inline Merge Status headings | already adapted: real lane rewinds overlap fork acceptance work |
| `62d45cbdad7598c4101d0caa662002421d0eb2f9` | Merge branch 'dev' (release 0.23.0: manual, hardened web auth, real contract rewinds) | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `76c31704e99682ac245e954daee6e233a31e20aa` | chore(skills): checkpoint Astra DRY/KISS improvement pass | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `88fb6737edb58d8d6166d7b252d34308269da386` | merge: skill-improvement-2026-09-08 into main | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `4cbe96e2ed5d58dbca06435b15f83decce37eb1e` | feat(jira): 구조화 ADF 노트와 멱등 댓글 전송, 의도 분석 lane 예시 | already adapted by fork commit `4760449`; upstream example/docs are deferred |
| `2d8b51cbfd12695c5f6d795eb308989cb40a2902` | fix: 심링크 루프 프로젝트 경로 거부, 느린 호스트에서 release reconcile 테스트 안정화 | symlink-loop guard already adapted by `edcda42`; test-timing adjustment deferred |
| `e6f314d7f9c3970b71c9eb818058057106867558` | Merge branch 'dev' | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `6f800532ceed0e07c02a8cc0e85d1a7a319e16fc` | fix(release): resolve empty auto_merge_target_branch to the current branch in the release binder | already adapted: release target resolution represented in existing fork code |
| `f4a56f2825990861f9110837c1cdb85b9ff2ad5a` | feat(contracts): enforce the deep preset's lane gates and the move into Done mechanically | already adapted in fork form: mechanical contracts preserve release gates and profile routing |
| `78124d6bdd042eebda0cd8cee60d44f9691f4cc6` | feat(orchestrator): cap Done->reopen cycles with agent.max_reopens | already adapted: reopen limits and approval evidence overlap fork contract work |
| `65e9e83a259b12fd64dd76bb0b04394237df8e89` | feat(intent): trip-wire scan on the chat intent gate | already adapted by fork commit `311250e`; UI and documentation differences remain deferred |
| `8cf06ffedea0b5e8a656aeaaf62f8a79e3f95db8` | feat(doctor): probe the agent binary with --version after the PATH lookup | already adapted: doctor binary version probe is present |
| `64145271f3359411573550c8e194a46aef06d8c8` | feat(orchestrator): agent.fallback_kinds switches backends on quota errors | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `d748498c648ece4fb95ad52fdb2a5a0c1197038b` | feat(stats): close the learning loop with gate events and a board-health prompt input | already adapted: board health and learning metrics overlap fork acceptance work |
| `c0f4e7b23441110b4ae50e3f2d0d8b56c87811bf` | chore(sdlc): re-seed with sdlc-kit v0.8.0, archive the closed feature, untrack evidence | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `34d2ea2dc72ac99a89a064059af927d395ea1523` | refactor(orchestrator): extract the Verify-exit release gate to release_transition.py; make the parallel suite load-tolerant | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `f0a5cffa3a5c42613a8238fc1312913b59532947` | chore(release): prepare 0.24.0 — mechanical gates on every preset, reopen/quota budgets, learning loop | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `8f89d6a45aa24f565f8072335c747ffbed7ef119` | merge: integrate SDLC cycle hardening for quality verification | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `e052507098d84f2714b492a6b756dff35648f0f9` | fix: harden dependency graphs, backend cleanup and SDLC integration | already adapted: dependency validation and backend cleanup are present; no duplicate process cleanup |
| `e5fc5ed2d25de14ccf5635eda12ad637bd91f402` | Merge pull request #60 from cskwork/fix/performance-quality-20260912 | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `f4336c1df8f75f0532e5577ebd508b35273e10e9` | docs(handoff): record the 2026-09-12 live end-to-end run and two pre-existing findings | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `f26f9bcd8f64627722fc180455b2845e8aa3bae2` | docs(handoff): record the 2026-09-12 live end-to-end run and two pre-existing findings | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `705b0d175ef05d2fd805b873a42788059af0b33a` | Merge branch 'dev' (release 0.24.0: mechanical gates on every preset, reopen/quota budgets, learning loop) | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `559ba4f0eda9277b3b6a1d9c25802ef6e76bf61c` | fix: correct Codex usage and refresh verified workflow manuals | Codex usage fix already adapted by fork commit `edcda42`; manual refresh deferred |
| `d64229379350bdb1a02094ff01c7929280465240` | merge: preserve concurrent live E2E handoff | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `9f05da612e6003cab86f2b842b5156bee5dc9f9f` | fix: release dispatch slots for cancelled terminal workers | already covered: terminal cancellation releases dispatch capacity; existing lifecycle tests retained |
| `cc89aa452b72d9ef9aafef8da8190480145970cf` | Merge pull request #61 from cskwork/test/release-e2e-manual-20260912 | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `f0385d6f2cc06d70ca0a4d32ba0fb4b70f06b871` | Merge pull request #62 from cskwork/dev | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `4b9d9fd66015a48db47572cf5a7437d55686f7c3` | docs: clarify Done merge target in both cheatsheets | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `f0d944731728c5a66d5abd835494a410b1861e4c` | feat: harden dispatch guards and explain execution status | already adapted in fork form: dispatch guards and issue budgets coexist with fork telemetry and usage pools |
| `0e2cbe69e7780ddec3025cd640e6a4c8448b8d18` | Merge pull request #63 from cskwork/improve/post-release-reliability-20260912 | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `49c7fa10b4c55326068a24ff5710c04bc6f1d1fd` | chore(release): prepare v0.25.0 | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `56396ab29f43040507b2066aff623b2608a5e6fd` | Merge pull request #64 from cskwork/release/v0.25.0 | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `95781ffc89fa0e5e29d9d5cc183ff089cbe26d97` | fix: first-run experience — keep generated workflow, project-facing agent files, clean git status | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `6290f296e744955caee6e94fe8778c25f492f766` | Merge pull request #66 from cskwork/improve/first-run-experience-20260922 | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `9c84a25357aac07b5702e47f8da8a053afe22cfe` | chore(release): prepare v0.26.0 | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `3de1ea9109cd318f98f9faf6dc2de48ce120cd60` | Merge pull request #67 from cskwork/release/v0.26.0 | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `9fc79f3b0ed76152b8890181d77def529e93c333` | fix(orchestrator): retry agy transient 503 instead of pausing | adapted: added AGY transient 503 retry markers and regression coverage |
| `557cf6f2a671f60289bfd51bcbf16dc267f3aea5` | Apply reviewed security remediation | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `769cb9788785ecb4677bf6d951b79f2c3c85b842` | wip(pm): PM Workspace checkpoint before R25 fixes | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `bb51fd3ed41686df1e2cab8fce632d6c4a3c81f2` | Merge origin/main (v0.26.0) into feat/pm-feature | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `fe09e4cc9060fe9d7d1ccce0217d08fb1c9a7bbf` | fix(pm): close R24 review findings | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `c1aa5b1b47be57534a1987f4eb8256f7aa87960f` | test(pm): run acceptance event-loop heartbeat under the timing marker | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `ca956402456c5867ea0bab2d4af1103156426500` | fix(pm): keep event loop free during xlsx export and import preview | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `bbdfb019203c908d4e07e0e95976fb3fbfab8f25` | fix(pm): run journal recovery under the change lock | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `6f315c8596cc988002d20041528710951c4b394c` | fix(pm): read change snapshots for exchange under the change lock | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `cbbf373f2fb46e9e00d6c4863903c6b2633ef8ad` | test(pm): assert exchange reads stay off the event loop | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `c89fb837c2621ede38eb22a50630fd7b2d21f9c0` | fix(pm): attach WBS tree keyboard handling once | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `de5277bba7d8194351063ee93650f440f92fa45e` | fix(shell): still signal our own unreaped worker group when ps cannot answer | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `fbd6bdb3bde4723fa134c307ca0f90a4f8eb0bce` | fix(webapi): limit the new cross-origin refusal to PM routes | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `19b9a7f1a49f2964f0a049d8939287542ef9311b` | fix(pm): drop unread planning config keys | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `42605e0d533a4238faca3e7fd5d7f9d9b87babb5` | refactor(pm): remove unused exchange wrappers | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `0631ce8579dd6f9bb0f5bdf2715c5a99754e3f8f` | test(shell): pin the ps-missing cleanup contract, not the reaper race | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `4595d33504f2be74e0781a0ebc3d48de0decc41e` | feat(pm): ship a reduced OpenSpec 1.14.0 bundle with Symphony | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `1a2594ea7257a871da20d5cbbf9a694c5d06d9cb` | feat(pm): resolve bundled OpenSpec with supported host Node | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `f6a77e16ec5c57762487133660c04902bf6cf9d8` | feat(pm): report host tool readiness and demo capabilities | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `8260e969cda655e9f6e511ca30b2c08cdac7d86c` | feat(pm): add English and Korean demo planning content | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `1c697608967178e99dbe319f1592a469483a3495` | feat(pm): create isolated validated planning demos | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `6d0d7385bf3843ba27b98deb4597104547688b77` | feat(pm): serve guarded loopback planning demos | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `0248519740e3087afeb539d8a07de1c90ad898b8` | feat(cli): add disposable PM demo command | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `ce436efdef28c0ce9501f73a976e57023b1dd650` | feat(pm): explain workspace buttons and open demo guides | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `b1dd481c2bf18c4f4b092ce324bc41526d32cc4d` | docs(pm): document bundled OpenSpec, Node floor and pm demo | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `a2b439c4cd57c0d2410c5a4a9452aa9a1eceb8bf` | test(pm): prove bundled OpenSpec matches an independent 1.14.0 install | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `e382c7b54ed66373461639ca1d2e4697a69a55c8` | test(pm): drive the installed demo in Chrome and the no-Node server | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `8bd8f3676163841b88517f13c2e1806f8b9ff09d` | fix(pm): align approval, workbook exports and GPT-6.1 planning | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `1ca50e64721cc223dbf0169c6c8584f1c05972c0` | fix(pm): preserve drafts and use compatible installed Codex | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `89f836b6025a5147862a063e23eaa46f6555cb66` | test(pm): isolate PATH probe state and clean failed fixtures | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `2f1d53e78af5ee29f39ed37880950e0e15fe62d4` | fix(web): preserve token entry and ignore stale authentication failures | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `5b718fd2d8d3f3489b33846898fc4a334ae40393` | fix(pm): improve mobile planning readability and error feedback | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `2a25a91a2a72c40489512750dbdd6c11483cc135` | fix(pm): complete planning cancellation and dialog keyboard access | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `c03dd58d8d92e88747ea1247526bdc0c1d2b5536` | fix(pm): refresh planning status and clarify available actions | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `ca9ab5602aad12ce3b8828f46f69cb73670ea21b` | fix(pm): preserve hidden fields in portaled dialogs | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `a75a551a497b847bf6f0b461abae726adf2bb35f` | fix(pm): improve readable plans and sibling approvals | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `4329183be59d86b5a3e5689887dda4bb24680323` | fix(pm): serialize WBS commands and reveal active artifact tabs | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `0a71971c876b2a76377a1eb3ec0613bd28bfb3e0` | feat(pm): harden workspace and bundle OpenSpec 1.14.2 | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `f8980567a2191a6bc12e942513eaa1bb9721430c` | Merge PM Workspace and demo for v0.27.0 | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `45cb205317108a8b1c823e852e0af90af1c33af9` | ci: install locked frontend dependencies before tests | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `0f4c103912027fed3b713e482271e2618444503f` | Merge pull request #68 from cgracia/fix/retry-503-service-unavailable | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `ce03c50a52986d3c24773ecd8e171c241aacb64e` | Merge PR #39 with current main and preserve scoped autocommit exclusions | deferred merge-resolution behavior: the extra index reset for scoped squash staging is outside the accepted lifecycle/retry adaptation; fork staging behavior retained |
| `18ebe451d65e66fcf65b35af4c69b2892460b92a` | Merge pull request #39 from cskwork/cursor/critical-correctness-bugs-4128 | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `365d9185d31ba151ca6de316a7656ede9a0a8156` | test(pm): build demo wheel from a per-worker source copy | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `3745d70bd41e023079ee53a63b8600933105820d` | Merge pull request #71 from cskwork/release/v0.27.0 | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `f7bd9339dc81059091ac846990ea80d3840fe011` | test(tui): count sent error toasts, not toasts still on screen | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `da6d32b6954e23b9f4afebf546f25d71d54499c9` | test(pm): pin OpenSpec refusal of stack-crashing brace globs | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `577233f65b5a43af4ea5ae5ed414800ef5766cbd` | fix(pm): guard bundled braces against stack-crashing nesting | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `8da3e364ed3d60a2b384e3a2b5f8d03d2300e3d0` | test(pm): open collapsed PM tool disclosures in the installed journey | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `8cc3834886712994f5f0fb3002ea225ac7b16334` | fix(pm): run PM data work on a small dedicated thread pool | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `667ed860cb59103c6c5ac32cbeffa1a49fbfb24f` | docs(changelog): record the PM thread pool RSS fix | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `244c48653fc83c4c8f8f94a303728d7b05880755` | Merge pull request #72 from cskwork/fix/pre-release-v0.27.0 | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `0d718a76257a4f2c7d6900ea69e569d06f88b645` | Merge branch 'main' into dev | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `69b5fde1c25c7dee79dabf112667fa06ed693c04` | fix(factory): integrate dev delivery safeguards | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `2e16e9947db54dc89315d62a56ed516967a11f1e` | fix(factory): preserve current dev and concurrent board updates | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `c074446e2b2d45b907019264682c2338f6bc27b6` | fix(factory): support Windows Codex grants and clarify CLI recovery | deferred: Windows-specific change; preserve fork behavior and make no native-Windows validation claim |
| `24b46a9b1c0d8387bdfca52d88140cb598ca479f` | fix(factory): preserve work after Git snapshot failures | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `3ae2d733487392b6bd12b3ffacd7fcbae5d9e90c` | test(factory): model durable snapshots in reconcile fixtures | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `49eb561c601152ca55f6133bb641f0ac8303fae6` | fix(factory): derive browser proof from explicit requirements | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `51baf875f8275da6f45613516cc68d8d8a407a97` | fix(factory): report the effective endpoint and delivery stage | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `4cf8d47e19f022e5136b59addb13587f56739c33` | fix(factory): retain blocked work after token cancellation | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `f0e3c5a91508e72604986a55cb4aa84d153a3466` | docs(factory): record PR 57 verification and quality gaps | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `a94dad0e03b1b191b94580f59e66ee0e17646d7b` | Merge pull request #57 from cskwork/feat/autonomous-dev-factory | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `89eaf07cf43a0c340c28d2edbfcc5c9ed0d3296f` | fix(orchestrator): complete Human Review through host delivery | adapted: centralized Human Review delivery gates; preserved confirm-review API and UI contract |
| `41b8ecad414e3c612c6fbd3f1dffb8ee0f19b01c` | fix(codex): backport Windows linked-board sandbox grants | deferred: Windows-specific change; preserve fork behavior and make no native-Windows validation claim |
| `726e571def04d3eb69f87c99a122a80c358f98af` | test(orchestrator): run lease timing regression serially | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `6dd3558ff3e066e1de7bcb1164b1f37ff8302ced` | Merge pull request #73 from cskwork/fix/issue31-symlink | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `4edd98aa123d404c23c36e5111cd6a550e9c1a71` | Merge pull request #56 from cskwork/cursor/critical-correctness-bugs-3dcd | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `f1ee582d8cf50505944ae9eff192e4b57d74f7b9` | fix(factory): preserve work through Windows recovery and cleanup | deferred: Windows-specific change; preserve fork behavior and make no native-Windows validation claim |
| `3d65b528d475b5ab3e55fccfb6910c99bbc3b87d` | fix(workspace): preserve shared boards during setup recovery | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `abdbe1e279f8a369f5970061d69b953b27156ba8` | test(windows): make Unicode pipe fixture independent of text encoding | deferred: Windows-specific change; preserve fork behavior and make no native-Windows validation claim |
| `aa3c80dace883150ed2bdd48e076e8f177cad853` | docs(quality): publish independent factory recovery assessment | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `ce5b7ce36c6f493d84a2d965e9ed73f0f8ddf771` | Merge pull request #74 from cskwork/feat/factory-quality-recovery | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `7ec1bf1a591a0b6ea4d9ef70a27c4a6c33dbad8b` | chore(release): integrate verified changes for v0.27.0 | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `ebfc703b0fa67e7c64caf08d901fa6a478b905b5` | Merge pull request #75 from cskwork/release/v0.27.0-final | history-only merge boundary; no independent tree change beyond individually dispositioned commits |
| `bfd913845e16f3285209b2bb9a3cae1d1108171b` | fix(cli): clarify factory recovery and delivery evidence | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `f25e5f087f2abc1e5c642037528fd161086d6f1e` | Merge pull request #77 from cskwork/fix/factory-recovery-guidance | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `f5cfdd32f1f4b434b2890c635bdab0b99b14b6c1` | chore: lean core phase 1 — remove repo bloat, keep shipped features | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `7e23472db38be222285aab94954c00b7971e26db` | fix(docs): keep superdesign prerequisites in READMEs; check the demo workflow in docs/reference.md | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `56e7dc691b4f684699fa88ab00888e4327c51799` | Merge pull request #79 from cskwork/refactor/lean-core-phase1 | deferred: broad core/module refactor; preserve fork architecture and phase routing |
| `c77b56f26cb87d6f5badefdafc1fc8f996d68868` | refactor(orchestrator): split core.py into method modules (LR-3) | deferred: broad core/module refactor; preserve fork architecture and phase routing |
| `e0dda3c901783a42e48e7690a508fc6161d1aec4` | refactor(webapi): split webapi.py into a symphony.webapi package (LR-3) | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `177bae333f78d67ba6bba6b6895b70decbe98db8` | chore(lean-core): remove verified dead code (LR-3) | deferred: broad core/module refactor; preserve fork architecture and phase routing |
| `fb8b7c9c8c0824942bc64f1c303e3dc3561d3736` | feat(backends): declarative plain-CLI agent profiles (LR-2) | deferred: profile/CLI behavior conflicts with fork named-profile and stage-routing contract |
| `661ac2a3af9442238f8107dfe3d0fcfb36392fa0` | refactor(registry): split run_registry into records, base, release, and PM modules | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `9e75bb913704e3e9ddd13b914fac540fbe8dc577` | Merge branch 'p2/deadcode' into refactor/lean-core-phase2 | deferred: broad core/module refactor; preserve fork architecture and phase routing |
| `83fbdce303f6183afa09c39e290c607b9467201b` | Merge branch 'p2/profiles' into refactor/lean-core-phase2 | deferred: broad core/module refactor; preserve fork architecture and phase routing |
| `73e672227c61c7792746665aa7ffb7f561cf9d96` | Merge branch 'p2/registry' into refactor/lean-core-phase2 | deferred: broad core/module refactor; preserve fork architecture and phase routing |
| `8004ed3f38f42472f04249fe7f31a50c13c62136` | Merge branch 'p2/webapi' into refactor/lean-core-phase2 | deferred: broad core/module refactor; preserve fork architecture and phase routing |
| `e52f0ea24fba49f7fe8bcd0decceeb87c613f211` | Merge branch 'p2/core' into refactor/lean-core-phase2 | deferred: broad core/module refactor; preserve fork architecture and phase routing |
| `d0b14eeaf8919688dde66bb3d55f076b9f0c48a4` | feat(agents): accept agent profiles wherever an agent kind is selectable | deferred: profile/CLI behavior conflicts with fork named-profile and stage-routing contract |
| `f3f4a55618a14034847a91edcc141772261b895d` | docs(changelog): record phase 2 splits and profile selection | deferred: profile/CLI behavior conflicts with fork named-profile and stage-routing contract |
| `6bf7013266be597e3f81b269fa368c43293c43a2` | Merge pull request #80 from cskwork/refactor/lean-core-phase2 | deferred: broad core/module refactor; preserve fork architecture and phase routing |
| `75aefe70ed54821288d855d1ef972167dd189a51` | docs(pm): drop the link to an uncommitted local evidence file | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `86c266426ad4801e201748f0b7c2da8695f84d45` | fix(factory): use the shipped agy command | deferred: Factory/PM Workspace feature outside agreed integration scope |
| `ad305c6a2652e73075c7489800f6068509aef80a` | fix(agents): include workflow profiles in ticket pickers | deferred: profile/CLI behavior conflicts with fork named-profile and stage-routing contract |
| `e07e005c1551dde3564f550289cda0bc253cf5bd` | refactor(core): remove production-dead helpers | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `1bbd8944fb4da6c9a326a2348e8589b82b043a1f` | test(workspace): pin stub tools inside preserve hooks | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `9c0c2d2c7a67cf727045a1bbaab6a1ad6202f166` | docs(changelog): record lean core follow-ups | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `520ab0183447d3403c0ecf8dd673fc8051e7824b` | fix(pm): confirm absence after process group snapshot races | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `d874c0eeec187707318e56278a9a72e5f1bdbd35` | docs(changelog): record PM cleanup race fix | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `ceb6f58fd19a9e6d7f45db9b7f5bc7d360da86ca` | Merge pull request #81 from cskwork/chore/lean-core-followups | deferred: broad core/module refactor; preserve fork architecture and phase routing |
| `c8484b07384c5bdfd35b089dc203ab0c73592e90` | chore(release): v0.28.0 | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `f908fe0c8b80c7ab7a86f0dab8322d9455692ac9` | docs(changelog): cut 0.28.0 | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `60f3a7f6b949ca22f565c7dc7781ca4540ff2b09` | docs(pages): show v0.28.0 on the landing badge | deferred: reviewed; unrelated to the agreed reliability and Human Review scope |
| `c6a39473373f42393396e47114e2de0cb4cc8c8c` | Merge pull request #82 from cskwork/release/v0.28.0 | history-only merge boundary; no independent tree change beyond individually dispositioned commits |


### Verification and promotion state

- Full suite after the final race and release-authority checks: `3023 passed, 16 skipped in 202.52s`.
- Focused lifecycle/API/TUI/dispatcher/backend-contract suite after the race fixes: `550 passed, 3 skipped`.
- Ruff passed; Pyright passed with 0 errors/warnings/informations; i18n passed (625 keys); `uv lock --check` passed; `git diff --check` passed.
- `symphony doctor ./WORKFLOW.md` was attempted. It refused the protected source-repository workflow and the sandbox blocked local bind/state/workspace writes; this is recorded as an environment limitation, not a doctor pass. No guard was weakened.
- No push or deployment was performed. M2 promotion and final ancestry checks are complete; this commit only records the final M2 evidence.
