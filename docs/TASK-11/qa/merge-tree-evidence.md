# TASK-11 Merge Preflight Evidence (Verify pass 2)

Run: 2026-08-17T18:50Z. Rewrites pass 1: develop has moved past `62a5734`, so the old "develop tip is the direct parent" argument no longer applies. This file replaces it with a three-way merge analysis.

## Goal

Prove `symphony/TASK-11` merges into `develop` without conflicts, without running `git merge-tree` (denied by workspace policy in both forms).

## Target resolution

| Source | Value |
|---|---|
| `agent.auto_merge_target_branch` (WORKFLOW.md:304) | `"develop"` |
| `agent.feature_base_branch` (WORKFLOW.md:301) | `"develop"` |
| Host repo `.git/HEAD` | `ref: refs/heads/develop` |

Target = `develop`, tip `bfb232fa4d4b87caf23db64b55416cc59415fe88`; feature tip `e9c248a7716882e0bf265036f0362456c28abb4a`.

## Preflight

### 1. Prescribed command attempted, denied

```
$ git merge-tree --write-tree develop symphony/TASK-11        # worktree form
This command requires approval
$ cd /home/symphony/git/oh-my-symphony && git merge-tree ...  # host form
This command requires approval
```

Recorded as denied, not as success. The fallback chain below is the substitute.

### 2. Recorded clean merge-tree at the prior develop tip

`docs/FIX-TASK-11-1/qa/preflight-evidence.md` (durable artifact now on develop) records:

```
$ git -C /home/symphony/git/oh-my-symphony merge-tree --write-tree develop symphony/TASK-11
Result: Exited 0 with tree 798121874e8c7ef257fe09d620c89368021921ef.
```

That run predates only the FIX-TASK-11-1 merge itself: `git diff --stat 44344d6..develop` shows the 2 commits since touch 8 paths — `docs/FIX-TASK-11-1/*`, `docs/llm-wiki/INDEX.md` (1 line), `docs/llm-wiki/worktree-git-sandbox.md` (1 line).

### 3. Three-way analysis at the current tip (base `62a5734`)

- Branch side (`62a5734..HEAD`, 1 commit): 6 paths — 3 new `docs/TASK-11/*` evidence files, `docs/llm-wiki/INDEX.md`, `docs/llm-wiki/byte-exact-static-deliverables.md`, `profile-smoke.txt` deleted.
- Develop side (`62a5734..develop`, 8 commits): 31 paths — source/tests, TASK-10 and fix-ticket docs, wiki files.
- **Only shared path: `docs/llm-wiki/INDEX.md`.** Develop's hunks edit table rows 12 (worktree-git-sandbox) and 22 (agent-profile-observability-tooling); the branch's hunk edits row 18 (byte-exact-static-deliverables). Disjoint regions, >3 lines apart — git's default 3-line-context merge auto-merges cleanly.
- `git diff 62a5734 develop -- profile-smoke.txt` is empty: develop never touched the file, so the branch's deletion applies cleanly and the merged tree has no `profile-smoke` path.
- `git diff 62a5734 develop -- docs/llm-wiki/byte-exact-static-deliverables.md` is empty: the branch's version applies verbatim.
- `docs/TASK-11/*` exist only on the branch: pure additions.

### 4. Host dirty-tracked overlap

```
$ git status --porcelain          # host repo
 M WORKFLOW.md
?? WORKFLOW.md.bak-20260817-163800
```

```
$ git diff --name-only develop..symphony/TASK-11 -- WORKFLOW.md
(empty)
```

Host dirty paths are `WORKFLOW.md` (+ untracked `.bak`); neither is in the branch delta — no real overlap, matching the fix ticket's `--literal-pathspecs diff --quiet` check (exit 0, `docs/FIX-TASK-11-1/qa/preflight-evidence.md`).

## Verdict

Conflict-free: a recorded clean merge-tree at the prior develop tip plus per-path analysis showing every subsequent develop change is disjoint from the branch delta. The orchestrator's `--no-ff` merge at Done will produce a tree with `profile-smoke.txt` absent.

**Proves**: zero conflict surface between `symphony/TASK-11` and develop tip `bfb232f`; post-merge `git ls-files | grep profile-smoke` returns nothing by construction.
**Does not prove**: literal merge-tree byte output for the current tip (command denied in both forms).

## How to re-run

1. `git rev-parse develop`
2. `git merge-base develop HEAD`
3. `git diff 62a5734 develop -- profile-smoke.txt`
4. `git diff --stat 44344d6..develop`
5. Host: `git status --porcelain`, then `git diff --name-only develop..symphony/TASK-11 -- WORKFLOW.md`
