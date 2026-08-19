# TASK-18 QA — Merge preflight (topology proof)

`git merge-tree --write-tree develop symphony/TASK-18` was denied by the permission
harness (`qa/runtime-blocked.md` row 4). A stronger proof is available from
read-only git topology:

## Facts (all read-only git verbs, 2026-08-19)

- Target branch: `develop` (WORKFLOW.md:304 `feature_base_branch`, :307
  `auto_merge_target_branch` — both `"develop"`; host repo HEAD ref:
  `refs/heads/develop`, read from `/home/symphony/git/oh-my-symphony/.git/HEAD`).
- `git rev-parse develop` → `c829339fca9616de6b2e4918f687e82ab62438b7`
- `git rev-parse HEAD`  → `795e70e58c6e585661bb086f3e5a65e8bb59be47` (host wip commit; the original verify tip `be10c9d41fe7c94c1c193665135f7345bc5c2e40` was re-committed with the QA evidence docs — `git diff be10c9d 795e70e -- src tests` is empty, so the reviewed source is byte-identical)
- `git rev-parse HEAD^` → `c829339fca9616de6b2e4918f687e82ab62438b7` (HEAD's parent IS develop tip)
- `git merge-base develop HEAD` → `c829339` (= develop tip)
- `git rev-list --count develop..HEAD` → `1`; `git rev-list --count HEAD..develop` → `0`

## Conclusion

`symphony/TASK-18` is exactly develop + 1 commit. Merging it into `develop` is a
pure fast-forward: the resulting tree is byte-identical to the reviewed `be10c9d`
source tree, so a textual merge conflict is impossible by topology. The single
commit (795e70e) carries the 17-file source/test change set plus the 5 QA evidence
docs — no orphan change rides the merge.

Not checked: host-worktree dirty files (the host `git status --porcelain` read was
denied, `qa/runtime-blocked.md` row 5). That affects only the orchestrator's
checkout step at Done, not the merge result, and the orchestrator performs the
actual merge.

How to re-run (when allowed):
```bash
git merge-tree --write-tree develop symphony/TASK-18
git rev-list --count develop..HEAD && git rev-list --count HEAD..develop
```
