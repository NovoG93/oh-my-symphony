# TASK-4 Verify — QA Evidence (2026-08-15T18:50Z, retry attempt 1)

Branch `symphony/TASK-4` @ 050d96f; merge-base with main = 4c9e7b1.
Live pytest / merge-tree / host-git execution is denied by the workspace
permission policy (attempts #4-#7 in `qa/runtime-blocked.md`); the evidence
below is what the policy allows plus the durable `.pytest_cache` record.

| # | Command (exact) | Exit | Evidence | Proves | Does not prove |
|---|---|---|---|---|---|
| 1 | `git diff 4c9e7b1 HEAD --stat` | 0 | 10 files, +950/-0, all ticket-scoped (this file's "Scope" line) | No orphan scope; no deletions | Correctness of any change |
| 2 | `grep -rn "agent_profiles\|stage_profiles\|default_profile" src/` | 0 | output in `review-notes.md` | No runtime dispatch/backend reads the new keys (Phase 2/3 untouched) | That future phases don't exist elsewhere |
| 3 | `git show HEAD:graphify-out` | 128 | `fatal: path 'graphify-out' does not exist in 'HEAD'` | Symlink removed from branch | Working-tree cleanliness on host |
| 4 | `git diff 4c9e7b1 HEAD -- src/symphony/chat.py src/symphony/projects.py tests/test_chat.py` | 0 | empty output | Branch does not touch the code of the single recorded suite failure | — |
| 5 | `git merge-base main HEAD` | 0 | `4c9e7b1` | Fork point; topology for merge analysis | Clean merge (see `merge-preflight.md`) |
| 6 | `git diff --name-only main..symphony/TASK-4` / `git diff 4c9e7b1 main --name-only` | 0 | branch set = 10 files; main set = 3 files (.gitignore, WORKFLOW.md, scripts/symphony-setup-worktree.sh); disjoint | No file-level overlap between branch and main since the fork | Textual-level 3-way merge result |
| 7 | `.pytest_cache/v/cache/{nodeids,lastfailed}` (mtime 18:42) | — | 2320 collected; 15/16 profile tests present; lastfailed = 1 entry: `tests/test_chat.py::test_project_setup_marker_with_symlink_loop_stays_plain_text` | The 18:42Z full-suite run on this branch executed 15 profile tests with zero profile failures | The 16th test (duplicate rejection) — never collected in any recorded run; static review only |
| 8 | `pytest tests/test_workflow_agent_profiles.py -q` (both `pytest` and `python3 -m` forms) | denied | `qa/runtime-blocked.md` #4/#5 | Policy denial persisted this pass | Any live test result this pass |
| 9 | `git merge-tree --write-tree main symphony/TASK-4` | denied | `qa/runtime-blocked.md` #6 | — | Clean-merge proof (fallback: `merge-preflight.md`) |
| 10 | `git -C /home/symphony/git/oh-my-symphony status --porcelain` | denied | `qa/runtime-blocked.md` #7 | — | Host worktree dirty state |

## Recorded suite failure — triage

`tests/test_chat.py::test_project_setup_marker_with_symlink_loop_stays_plain_text`
was the only failure in the 18:42Z full-suite run. Branch-independence:
the diff of `chat.py`, `projects.py`, `test_chat.py` vs the fork point is
empty (row 4), so the failure exists identically at the fork point. Static
trace: on Python 3.14/Linux, `Path.exists()` returns False for a
self-referential symlink (ELOOP swallowed), so the guard in
`chat.py:444-447` does not fire and a proposal is produced instead of plain
text — a pre-existing main-side/environment issue outside this ticket's
scope. No profile-related test failed.

How to re-run (in an execution-allowed environment):
`pytest tests/test_workflow_agent_profiles.py -q` (expect 16 passed),
`pytest -q` (full suite; the chat symlink test is expected to fail on
Python 3.14 for the pre-existing reason above), then
`git merge-tree --write-tree main symphony/TASK-4` (expect clean).
