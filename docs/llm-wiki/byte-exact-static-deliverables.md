# byte-exact-static-deliverables

How to create and prove a static text deliverable ("file contains exactly X")
without touching code, and where acceptance fixtures must live when the
harness blocks `/tmp` writes.

## The pattern (SMOKE-003, 2026-08-15)

1. **Write deterministically.** `printf 'OK\n' > smoke.txt` — `echo OK` varies
   with shell settings; `printf` produces the same bytes everywhere.
2. **Prove existence** — `ls smoke.txt` -> exit 0.
3. **Prove content** — `cmp smoke.txt <fixture>` -> exit 0, no output =
   byte-identical.
4. **Prove exactness** — `wc -l -c` -> `1 3` (line/byte count) and
   `od -A x -t x1z` -> `4f 4b 0a >OK.<` (hex, no trailing bytes);
   `sha256sum` gives a re-checkable fingerprint (`a12b7cb4...` for `OK\n`).
5. **Prove it ships** — `git ls-files smoke.txt` -> tracked, so the file
   rides the Done merge.

No test code is needed for a static text deliverable: the shell acceptance
checks are the proof (difficulty `trivial`).

## Fixtures live in the workspace, not /tmp

The harness blocks writes outside session working dirs
(`printf 'OK\n' > /tmp/expected` -> refused). Put reference fixtures inside
the ticket evidence root (`docs/<id>/qa/expected-ok.txt`) and substitute that
path in re-run commands. The fixture then rides the merge with the evidence.

## Inverse: removing a static artifact (TASK-11, 2026-08-17)

The TASK-9 artifact `profile-smoke.txt` was removed without rewriting history:

1. **Remove with a normal commit** — `git rm profile-smoke.txt`; the harness
   auto-commit then carries the staged deletion (`profile-smoke.txt | 1 -`).
   No `git revert` and no `git reset` — the TASK-9 merge commit `62a5734`
   stays an ancestor (`git merge-base --is-ancestor 62a5734 HEAD` -> exit 0).
2. **Prove absence on disk** — `test ! -e profile-smoke.txt` -> exit 0.
3. **Prove absence from the index** — `git ls-files profile-smoke.txt` ->
   empty; the literal `git ls-files | grep profile-smoke` check is denied by
   the pipe policy, so use the pathspec form instead.
4. **Prove the merged tree** — when `git merge-tree` is denied and the branch
   is a direct descendant of the target tip, `git merge-base develop HEAD` ==
   develop tip proves zero conflict surface: the merged tree is exactly the
   branch tree, so the post-merge `ls-files` check holds by construction. If
   the target has advanced past the merge base, prove per-path instead: diff
   the merge base against the target for each shared path and check the hunks
   are disjoint (TASK-11 pass 2, `docs/TASK-11/qa/merge-tree-evidence.md`).

## Decision log

| date | decision | why |
|------|----------|-----|
| 2026-08-15 (SMOKE-003) | `printf 'OK\n'` over `echo OK` | deterministic bytes across shells |
| 2026-08-15 (SMOKE-003) | Fixture at `qa/expected-ok.txt` instead of `/tmp/expected` | harness blocks `/tmp` writes; fixture must ride the merge too |
| 2026-08-15 (SMOKE-003) | No test code for a static text deliverable | shell AC checks are the proof |
| 2026-08-15 (SMOKE-003) | `git ls-files` in the proof chain | proves the deliverable is tracked and survives the merge |
| 2026-08-17 (TASK-9) | Re-applied the pattern unchanged for `profile-smoke.txt` | second end-to-end confirmation; same bytes (`4f 4b 0a`) and same sha256 `a12b7cb4…` for `OK\n` |
| 2026-08-17 (TASK-9) | Fixture at `work/expected-ok.txt` instead of `qa/expected-ok.txt` | both live inside the ticket evidence root and ride the merge; only the `docs/<id>/` location matters |
| 2026-08-17 (TASK-11) | Removed `profile-smoke.txt` with plain `git rm`, no revert/reset | smoke artifact must not ship on develop; history stays intact — proof chain is `test ! -e`, empty `git ls-files`, deletion commit stat, `62a5734` ancestor check |
| 2026-08-17 (TASK-11) | `git ls-files <path>` pathspec instead of `git ls-files | grep` | workspace pipe policy denies `|`; the pathspec form is equivalent and single-command |
| 2026-08-17 (TASK-11) | Step-4 topology proof qualified: merge-base == target tip only when the branch is a direct descendant; otherwise per-path disjoint analysis | develop advanced past the merge base after pass 1; the simple topology claim no longer applied, and the fallback proved conflict-free |
| 2026-08-19 (TASK-22) | Re-applied the pattern unchanged for `copilot-smoke.txt` | third end-to-end confirmation; same bytes (`4f 4b 0a`) and sha256 `a12b7cb4…` for `OK\n`; fixture at `work/expected-ok.txt`; committed content re-proven via `git show HEAD:<path>` when re-run commands are gated |
| 2026-08-29 (TASK-23) | Re-applied the pattern for `verify-upstream-smoke.txt` (`all systems go\n`) | fourth end-to-end confirmation; `printf 'all systems go\n'` -> 15 bytes `61 6c 6c … 6f 0a`, sha256 `5b0128ca…`; fixture at `work/expected-upstream-smoke.txt` |
| 2026-08-29 (TASK-23) | Ticket contract headings must match exactly — `## Merge Status: <subtitle>` failed the gate as "missing ## Merge Status" | the gate matches heading strings verbatim (extends TASK-22's "gate reads the kanban body, not the vault" lesson); subtitles belong in the body, never in the heading |
| 2026-08-29 (TASK-24) | Byte-exact discipline applied to a program's *stdout*: AC is "prints exactly `hello from symphony`", proved by a subprocess test (`result.stdout == "hello from symphony\n"`) plus `od` hexdump (20 bytes `68 65 6c … 79 0a`) | extends the static-file pattern to code deliverables — the output, not a file, is the byte-exact deliverable; the same `od`/`wc` proof chain applies to stdout |
| 2026-08-29 (TASK-24) | Exact-heading rule recurred: verify wrote `## Merge Status: <subtitle>` again despite the TASK-23 row; Document renamed it to bare `## Merge Status` | the gate matches headings verbatim — the subtitle variant still fails the contract as "missing ## Merge Status"; producing stages must use the bare heading, subtitle moves into the body |

## Not covered

- Encodings beyond ASCII and files with no trailing newline (the pattern
  assumes `OK\n`; adapt the `od` expectations for other byte layouts).
- Post-merge state on the target branch: only proven by topology (merge base
  == target tip) until the orchestrator's Done merge lands.
