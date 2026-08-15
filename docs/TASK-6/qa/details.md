# TASK-6 Verify — QA Details (re-verify pass)

Overflow for the ticket's `## QA Evidence` and `## AC Scorecard` sections.
Written 2026-08-15. Note: `qa/merge-tree.log` matches `.gitignore` (`*.log`)
and would not ride the Done merge, so its full content is mirrored here.

## Command manifest (full)

| # | Command | Exit | Evidence | Proves | Does not prove | Re-run |
|---|---|---|---|---|---|---|
| 1 | `.venv/bin/pytest tests/test_workflow_agent_profiles_backend.py -q` | refused, none | `qa/runtime-blocked.md` | live run blocked by permission gate | anything runtime | same command |
| 2 | `env -u SYMPHONY_GIT_WRITABLE_ROOTS .venv/bin/pytest tests/test_workflow_agent_profiles_backend.py -q` | refused, none | `qa/runtime-blocked.md` | live run blocked by permission gate | anything runtime | same command |
| 3 | indirect: final-tree full-suite session | completed normally (session-finish artifact; exit code not recorded) | `qa/test-run-evidence.md` | 2354 tests collected incl. all 9 new; zero failures recorded; tree at run time == HEAD 59e34e8 | exact pass counts (2344 vs 2354 gap), suite exit code | `env -u SYMPHONY_GIT_WRITABLE_ROOTS .venv/bin/pytest -q` |
| 4 | `git merge-tree --write-tree main symphony/TASK-6` | refused, none | `qa/runtime-blocked.md`, below | live preflight blocked by permission gate | conflicts | same command |
| 5 | `git rev-parse main HEAD` + `git merge-base main HEAD` | 0 | below | merge-base == main tip 7b70e09 => main fully contained in branch => merge cannot conflict (fast-forward topology) | merge-tree output format | `git merge-base main HEAD` |
| 6 | `git diff --name-only main...HEAD` + `git ls-files graphify-out` | 0 | below, `qa/static-review.md` | branch delta is 7 in-scope paths; graphify-out absent | host index dirty state (unreadable here) | `git diff --name-only main...HEAD` |

## merge-tree.log mirror (gitignored .log file, kept durable here)

Target resolution: `agent.auto_merge_target_branch` (WORKFLOW.md:304) and
`agent.feature_base_branch` (WORKFLOW.md:301) both pin `"main"`; host HEAD
(`/home/symphony/git/oh-my-symphony/.git/HEAD`) is `refs/heads/main`.
Effective target: **main**.

- `git merge-tree --write-tree main symphony/TASK-6` — DENIED by permission
  gate (recorded in `qa/runtime-blocked.md`).
- `git rev-parse main HEAD` — main = `7b70e09ad5c38dcc73652032a62308a08b38ab9b`,
  HEAD = `59e34e80cefe86a5b45bb71e865e0a83ede94034` (exit 0).
- `git merge-base main HEAD` = `7b70e09…` == main tip (exit 0). Main is
  fully contained in `symphony/TASK-6`; the merge is a tree-level
  fast-forward of the branch delta, and a merge whose base equals one side
  cannot conflict.
- Branch delta (7 paths): `docs/TASK-6/qa/review-finding-dispatch-raise.md`,
  `docs/TASK-6/qa/static-review.md`, `docs/TASK-6/work/backend_profile_support.md`,
  `docs/TASK-6/work/details.md`, `src/symphony/backends/claude_code.py`,
  `src/symphony/orchestrator/core.py`,
  `tests/test_workflow_agent_profiles_backend.py`.
- `git ls-files graphify-out` — empty; `git diff main...HEAD -- graphify-out`
  — empty: the LOW symlink finding is resolved.
- Overlap: branch touches only those 7 paths; host checkout sits at main tip
  (no commits on main since the fork) and kanban/ is gitignored, so no host
  dirty tracked file can overlap the merge.

Conclusion: preflight clean by topology proof; the orchestrator creates the
single `--no-ff` merge commit at Done.

## AC scorecard detail

| AC | Signal | Source | Result | Evidence |
|---|---|---|---|---|
| AC1 Codex model + reasoning_effort reach CLI invocation | `_build_turn_params` sets `params["model"]` / `params["effort"]` from the resolved profile CodexConfig (codex.py:523-526); test asserts both from a profile resolution | `tests/test_workflow_agent_profiles_backend.py::test_codex_profile_model_and_reasoning_effort_in_turn_params` | pass | `qa/test-run-evidence.md`, `qa/static-review.md` |
| AC2 inherited command intact; profile command override works (Codex + Claude) | `_prepare_command_and_env` returns `self._codex.command`; Claude keeps `self._claude.command`; override profiles supply their own command via `resolve_agent_config` | `…::test_codex_inherited_command_and_profile_command_override`, `…::test_claude_inherited_command_and_profile_override` | pass | `qa/test-run-evidence.md` |
| AC3 ClaudeConfig first-class `model`; `--model` injected when profile sets model | `ClaudeConfig.model: str = ""` (config.py:429); `_inject_model` inserted at `run_turn` entry (claude_code.py:75-91, 223-224); helper + end-to-end run_turn tests | `…::test_claude_inject_model_helper_cases`, `…::test_claude_profile_model_injected_in_run_turn` | pass | `qa/test-run-evidence.md`, `qa/static-review.md` |
| AC4 Claude resume + other settings inherit from global config | overlay resolution: inheriting profile keeps `resume_across_turns=True` / `turn_timeout_ms=3_600_000`, overriding profile flips them | `…::test_claude_resume_and_timeout_inheritance` | pass | `qa/test-run-evidence.md` |
| AC5 sessions scoped by ticket + backend kind + profile; no cross-profile resume | `_run_agent_attempt` rebuilds the backend per stage selection and clears session/thread state; tests assert distinct created sessions per profile and empty `resumed_sessions` | `…::test_session_scoping_different_profiles_same_backend`, `…::test_session_scoping_claude_models_distinct_sessions` | pass | `qa/test-run-evidence.md`, `work/backend_profile_support.md` |
| AC6 new backend tests pass; pre-existing suite green | full-suite session on final tree (2354 collected, lastfailed empty) — see `qa/test-run-evidence.md` | `.pytest_cache/v/cache/nodeids` + `lastfailed`, host log ts 20:40:50 | pass (indirect; live rerun denied) | `qa/test-run-evidence.md`, `qa/runtime-blocked.md` |

## Post-rewind note (Document lane, 2026-08-15)

The Contract-Failure rewind (missing exact-header `## Merge Status`) rewrote
commit topology: the Verify-pass tip `59e34e8` is orphaned and current HEAD
is `8f679e2` (`wip: turn 2026-08-15T20:40:54Z`). `git diff 59e34e8 HEAD
--stat` shows only the 3 qa evidence files added (`qa/details.md`,
`qa/runtime-blocked.md`, `qa/test-run-evidence.md`), so the recorded suite
evidence still describes the current `src/`+`tests/` tree. Re-verified this
pass (all exit 0): `git merge-base main HEAD` = `7b70e09` == main tip;
`git diff --name-only main...HEAD` = 10 paths (3 code/test + 7
`docs/TASK-6/` evidence), all ticket-scoped; `git ls-files graphify-out`
empty; host HEAD `refs/heads/main`; WORKFLOW.md pins target `"main"`.
