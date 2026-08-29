# Upstream synchronization ledger

## Integration record

- Remote `upstream`: `https://github.com/cskwork/oh-my-symphony` (fetch; push is `DISABLED`).
- Remote `origin`: `https://github.com/NovoG93/oh-my-symphony` (fetch/push).
- Integrated ref: `upstream/dev` at `aaf2b72da3b97fb9d0b71fb881fafea38f59de6d`.
- Base: `8b28dbba8f2b59952acea518d52b68c5a42bc1b0`.
- Observed `upstream/main`: `1f56213aebbab69f8cd73bf0f4f8c63f16307eaf`.
- M1: `b92ba932ea52ec5fe56b467320cb40401c35a1b1`.
- M1 parents: `d97795affee1e800fb57539e799bc8559e61f6ad` and `aaf2b72da3b97fb9d0b71fb881fafea38f59de6d`.
- M2: **pending**. No M2 hash is invented here: the hash must be finalized after M2 because the promotion and its ledger evidence are self-referential in timing.

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
