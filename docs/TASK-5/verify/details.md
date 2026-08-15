# TASK-5 Verify — full command manifest and review details (2026-08-15T20:02Z re-pass)

## Command manifest

| # | Command | Exit | Evidence path | Proves | Does not prove |
| - | ------- | ---- | ------------- | ------ | -------------- |
| 1 | `.venv/bin/pytest tests/test_workflow_agent_profiles_runtime.py -q` | blocked (permission policy, pre-execution) | `qa/runtime-blocked.md` | The gate refuses live pytest in this worktree | Test results — the TASK-5 file never executed this session |
| 2 | `.venv/bin/pytest -q` | blocked (same) | `qa/runtime-blocked.md` | Same gate behaviour | Full-suite results this session |
| 3 | `git merge-tree --write-tree main symphony/TASK-5` | blocked (same) | `qa/merge-preflight.md` | Same gate behaviour | Byte-level merge-tree output |
| 4 | `git show HEAD:graphify-out` | 128 (fatal) | `qa/merge-preflight.md` | Rewind fix: symlink no longer tracked at branch tip 390edf2 | Anything about older commits (7a703d8 still recorded the add — that is history) |
| 5 | `git merge-base main symphony/TASK-5` | 0 -> `6d75be5` | `qa/merge-preflight.md` | Merge base for the three-way analysis | Conflict-free outcome by itself |
| 6 | `git diff 6d75be5 HEAD --stat` | 0 -> 25 files, no `graphify-out` | `qa/merge-preflight.md` | Branch-side change set | Main-side changes |
| 7 | `git diff 6d75be5 main --stat` | 0 -> 2 files | `qa/merge-preflight.md` | Main-side change set; disjoint from the branch set | Host-repo dirty tracked files (unreadable from this session) |
| 8 | `cat .pytest_cache/v/cache/lastfailed` | 0 -> `{}` (mtime 19:51Z) | `qa/pytest-cache-evidence.md` | A pytest session completed at 19:51Z with zero failures (lastfailed is written only at session finish) | The exact command line / subset of that run; skipped tests |
| 9 | `grep -c "::" .pytest_cache/v/cache/nodeids` | 0 -> 2344 | `qa/pytest-cache-evidence.md` | Latest collection contains 2344 tests = 2335 passed + 9 skipped (worker record, `work/details.md`) | The 19:54Z session's outcome (no lastfailed update followed — incomplete/collect-only) |
| 10 | `grep -c "test_workflow_agent_profiles_runtime" …/nodeids` | 0 -> 23 (lines 2137-2159) | `qa/pytest-cache-evidence.md` | All 23 TASK-5 tests were collected, incl. the orchestrator lifecycle test | That they all executed in the completed run |
| 11 | `grep -n "symlink_loop" …/nodeids` | 0 -> line 479 | `qa/pytest-cache-evidence.md` | The previously-failing Python-3.14 chat test is in the collected set | Its pass/fail in the 19:54Z session (in the 19:51Z completed run lastfailed={} shows nothing failed) |
| 12 | `grep -nE "password|secret|api_key|token|shell=True|os.system|eval\(|exec\(" <changed files>` | 0 -> pre-existing hits only | `qa/security-review.md` | No new credential/injection pattern in the TASK-5 diff | Non-greppable injection classes (none plausible: no new subprocess code) |
| 13 | `grep -n "auto_merge_target_branch\|feature_base_branch" WORKFLOW.md` + `git show main:WORKFLOW.md \| same` | 0 -> main/main (lines 301/304) | `qa/merge-preflight.md` | Merge target = main on both sides | Host repo checkout (Read tool on `.git/HEAD` -> `ref: refs/heads/main` confirms main) |
| 14 | `git status` / `git rev-parse HEAD` | 0 -> clean / 390edf2 | `qa/pytest-cache-evidence.md` | The cache runs exercised exactly the reviewed tree | — |

## Review notes (diff review)

- Scope: full diff `6d75be5..390edf2`, 25 files. All hunks map to ticket scope:
  resolver (config.py, profiles.py), BackendInit + 8 backend drivers
  (backends/*), orchestrator wiring (core.py two BackendInit sites + F-01
  re-route at core.py:6989, helpers.py ticket extraction/ambiguity guard),
  ticket plumbing (issue.py, trackers/file.py), exports (workflow/__init__.py),
  claude `model` field + parsing (builder.py — required so the claude
  allowlisted `model` overlay has a field to land on), chat.py symlink-loop
  guard (adjudicated last pass: kept, AC6-enabling for the pre-existing
  Python 3.14 suite failure), docs/TASK-5 evidence.
- Orphan-scope check: no `graphify-out` (fixed), no UI changes, no CLI
  injection (Phase 3) — codex/claude drivers consume `resolved_backend_config`
  but flag construction is unchanged.
- Adjudicated carry-overs: stall reconciler (`_stall_timeout_ms_for_entry`,
  core.py:10110) reads the kind's base timeouts, not profile-overlaid
  `stall_timeout_ms` — out of AC scope, follow-up ticket material.
- Plan file `/home/symphony/agent-profiles-plan.md` sits outside this
  session's allowed directories; the ticket AC list transcribes the §3
  precedence verbatim and the implementation matches it tier-for-tier.
- Backward compat: for profile-less configs, tiers 4/6/8 ≡ legacy
  `kind_for_state` (pin > stage_kinds > kind); `_config_for_issue_agent`
  returns the identical cfg for the identical kind. No `Issue(...)` call site
  constructs positionally, so the inserted `agent_profile` field breaks
  nothing.

## Security Audit re-validation

The 7-row table on the card stands: this pass re-ran the greps and the hunk
review against the final tip; the only tree delta since the first pass is the
symlink removal, so every verdict is unchanged (3 pass / 4 n/a). Full detail:
`qa/security-review.md`.
