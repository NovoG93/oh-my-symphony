# TASK-10 Document details — brief vs reality, round 2 (post-fix reopen)

**What**: Full comparison of the ticket's Plan/Implementation/QA claims against the final branch state, plus the re-verified merge preflight.
**Why**: Document must confirm the change does what the ticket promised and that every cited artefact exists and is accurate before Done.
**As-Is -> To-Be**: Round-1 Document sections written before the merge-gate failure -> Round-2 re-verification after the FIX-TASK-10-1 reopen and branch rewrite.

## Claims re-verified this pass (all pass)

| ticket claim | verification | result |
| --- | --- | --- |
| dispatch log emits profile/model/effort | `core.py:6434-6443` read; fields present | pass (static) |
| reacquire passes the three fields | `core.py:2429-2442` read | pass (static) |
| reroute condition includes profile/model/effort inequality | `core.py:7077-7082` read | pass (static) |
| reroute log carries from/to profile+model and to_reasoning_effort | `core.py:7083-7096` read | pass (static) |
| in-memory entry + `update_stage_agent_profile` on every transition | `core.py:7097-7118` read | pass (static) |
| `update_stage_agent_profile` owner-fenced, parameterized | `run_registry.py:605-644` read | pass (static) |
| 4 new tests exist at cited lines | `test_run_registry.py:2227`, `test_workflow_agent_profiles_runtime.py:658/720/816` | pass |
| pytest cache forensics | `wc -l nodeids` = 2431; 4 new tests at lines 1509/2161/2162/2178; `lastfailed` absent | pass (indirect) |
| README/CHANGELOG/config untouched | `git diff 62a5734 HEAD -- README.md CHANGELOG.md` empty | pass |
| `docs/index.html` badge v0.21.0 (LOW) | diff shows single-line `v0.20.1 -> v0.21.0`; matches `pyproject.toml` 0.21.0 in ancestry | pass |
| wiki TASK-10 section + LOW-2 resolved + 2 decision rows + INDEX row | both wiki files read; row at `INDEX.md:22` | pass |
| host HEAD = develop; target develop | `/home/symphony/git/oh-my-symphony/.git/HEAD` = `refs/heads/develop`; `WORKFLOW.md:301/304` = develop | pass |

## Reality delta: develop moved after Verify

Verify's merge-tree evidence was computed against develop tip `b6b1c48`. This pass:

- `git rev-parse develop` = **`94a532b`** (new: `fix(graphify): untrack self-referential graphify-out symlink`, 2026-08-17 18:20).
- `git merge-base develop HEAD` = `62a5734` — fork point unchanged.
- `git show 94a532b --stat` touches exactly two paths: `.gitignore` (1 line: entry broadened to match symlinks) and `graphify-out` (symlink deleted). The branch touches neither (13-path diff, see below) — **no new conflict possible**.
- The only overlapping path between the two sides remains `docs/llm-wiki/INDEX.md` at disjoint hunks (branch line 22 row vs develop line 12 row) — preflight stays clean.
- `git ls-files graphify-out` shows the branch does track the symlink; `.gitignore` at HEAD has only `graphify-out/` (trailing slash matches directories, not symlinks) — exactly the defect 94a532b fixes. The Done merge therefore delivers a tree without the symlink; the wiki LOW-1 known-gap line was updated to say so.

## Diff inventory (13 paths vs fork point)

Code/tests: `core.py` (+78), `run_registry.py` (+41), `test_run_registry.py` (+46), `test_workflow_agent_profiles_runtime.py` (+259). Docs: `docs/index.html` (1 line), `docs/llm-wiki/INDEX.md` (1 line), `docs/llm-wiki/agent-profile-observability-tooling.md` (+46), `docs/TASK-10/{work/details.md, qa/*.md}`. No resolution-precedence, backend-construction, or routing edits (AC5).

## No Document Defect

Every acceptance criterion maps to code or tests exactly as the brief promises; no evidence contradicts a claim; no shipped doc describes behavior that changed. The only stale statement found (wiki LOW-1 "recommend excluding at merge") was made obsolete by upstream commit 94a532b, not by a TASK-10 defect — fixed in place as wiki maintenance.

## Commands run this pass (all read-only, allowed)

`git rev-parse develop`, `git merge-base develop HEAD`, `git log --oneline HEAD..develop` / `develop..HEAD`, `git show 94a532b --stat`, `git show f15003d --stat`, `git diff 62a5734 HEAD --stat`, `git diff 62a5734 HEAD -- docs/index.html README.md CHANGELOG.md`, `git ls-files graphify-out`, `grep -n graphify-out .gitignore`, `wc -l .pytest_cache/v/cache/nodeids`, `grep -n <4 new tests> nodeids`, plus Read of all cited hunks and qa/wikis files.

## How to re-run

```
cd /home/symphony/symphony_workspaces/TASK-10
./.venv/bin/pytest tests/test_workflow_agent_profiles_runtime.py tests/test_run_registry.py -q
git merge-tree --write-tree develop symphony/TASK-10   # orchestrator runs this at Done
```
