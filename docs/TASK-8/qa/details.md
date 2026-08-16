# TASK-8 Verify — QA Manifest

| Command / artifact | Exit | Evidence | Proves | Does not prove | Re-run |
|---|---|---|---|---|---|
| `.venv/bin/pytest tests/test_workflow_agent_profiles_e2e.py -v` | refused | `qa/runtime-blocked.md` | gate refusal only | live pass | `.venv/bin/pytest tests/test_workflow_agent_profiles_e2e.py -v` |
| `.venv/bin/pytest -q` (full suite) | refused | `qa/runtime-blocked.md` | gate refusal only | live pass | `.venv/bin/pytest -q` |
| `symphony doctor WORKFLOW.md --workspace .` | refused | `qa/runtime-blocked.md` | gate refusal only | live doctor output | `symphony doctor WORKFLOW.md` |
| recorded pytest session (cache forensics) | n/a | `qa/test-run-evidence.md` | final test file imported (pyc 05:07:07.134 > .py 05:07:04.725), session finished 05:07:07.699Z with `lastfailed` `{}`; 2,380 collected ids incl. all 8 new E2E tests | per-test outcomes of all 2,380 in one run; exact pass/skip counts; exit code | see re-run lines in `qa/test-run-evidence.md` |
| `git diff main...HEAD` review + greps | 0 | `qa/static-review.md` | 8 paths all in ticket scope; no secrets/shell/HTML in diff; docs cross-checked against `constants.py`/`builder.py`/`config.py` | runtime behavior of the code | `git diff main...HEAD` + greps listed in file |
| `git merge-base main HEAD` == `git rev-parse main` (`4231989`) | 0 | `qa/merge-preflight.md` | linear descendant of main ⇒ merge cannot conflict | real `merge-tree` output (gate-refused) | `git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree main symphony/TASK-8` |

Two LOW doc defects found in review were fixed in place (docs-only):
`file://` absolute links → relative (`README.md:214`, `README.ko.md:204`);
gemini `resume_across_turns` claim corrected to "accepted but inert" in
README.md / README.ko.md / `docs/features/agent-profiles.md` /
`docs/TASK-8/work/details.md`. Details in `qa/static-review.md` LOW-1/LOW-2.
No source or test files changed by those fixes.
