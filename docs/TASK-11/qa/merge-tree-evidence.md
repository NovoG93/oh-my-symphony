# TASK-11 Merge Preflight Evidence

Run: 2026-08-17T17:43Z. Mirrors `qa/merge-tree.log` (gitignored) into a tracked file.

## Goal

Prove the feature branch would merge into the target branch without conflicts, without running `git merge-tree` (denied by workspace policy).

## Target resolution

| Source | Value |
|---|---|
| `agent.auto_merge_target_branch` (WORKFLOW.md:304) | `"develop"` |
| `agent.feature_base_branch` (WORKFLOW.md:301) | `"develop"` |
| Host repo `.git/HEAD` | `ref: refs/heads/develop` |

Target = `develop`, tip `62a5734f6f3206559222839c8f3abe698b0d37dc` (the TASK-9 merge commit).

## Preflight

### 1. Topology — develop tip is the direct parent of the branch tip

```
$ git merge-base develop HEAD
62a5734f6f3206559222839c8f3abe698b0d37dc
```

```
$ git rev-parse develop
62a5734f6f3206559222839c8f3abe698b0d37dc
```

The merge base equals the develop tip: `symphony/TASK-11` is two commits ahead of develop (`e9a5b22` deletion commit + `894bf5b` evidence auto-commit). Merging cannot conflict — the feature delta applies directly on top of the target tip (fast-forward topology; the orchestrator's `--no-ff` adds only a merge commit).

**Proves**: a three-way merge of `symphony/TASK-11` into `develop` has zero conflict surface: no divergent commits exist on develop since the branch point.
**Does not prove**: byte-level merge-tree output — the prescribed command was denied; this is the strongest available substitute.

### 2. Delta scope — no overlap with host-managed state

```
$ git diff --name-only develop..symphony/TASK-11
docs/TASK-11/work/removal-notes.md
profile-smoke.txt
```

Only two paths change: a new evidence file under `docs/TASK-11/` and the ticket's deletion target. Neither path is a host-managed board file (`/kanban/` is gitignored per `.gitignore:28`), so no real overlap with host working-tree state is possible.

### 3. Resulting tree — file absent after merge

Since develop tip == merge base, the merged tree is identical to the branch tip tree. `git ls-files profile-smoke.txt` on the branch returns empty (`qa/removal-proof.md`), therefore `git ls-files | grep profile-smoke` returns nothing after the Done merge — AC2's literal post-merge check, proven by construction.

## How to re-run

1. `git rev-parse develop`
2. `git merge-base develop HEAD`
3. `git diff --name-only develop..symphony/TASK-11`
