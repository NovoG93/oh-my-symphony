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

## Decision log

| date | decision | why |
|------|----------|-----|
| 2026-08-15 (SMOKE-003) | `printf 'OK\n'` over `echo OK` | deterministic bytes across shells |
| 2026-08-15 (SMOKE-003) | Fixture at `qa/expected-ok.txt` instead of `/tmp/expected` | harness blocks `/tmp` writes; fixture must ride the merge too |
| 2026-08-15 (SMOKE-003) | No test code for a static text deliverable | shell AC checks are the proof |
| 2026-08-15 (SMOKE-003) | `git ls-files` in the proof chain | proves the deliverable is tracked and survives the merge |

## Not covered

- Encodings beyond ASCII and files with no trailing newline (the pattern
  assumes `OK\n`; adapt the `od` expectations for other byte layouts).
