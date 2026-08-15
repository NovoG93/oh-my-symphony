# SMOKE-003 Verify Details (overflow for ticket sections)

Run: 2026-08-15T12:07:26Z.

## QA Evidence — full command manifest

| Command | Exit | Evidence path | Proves | Does not prove |
| --- | --- | --- | --- | --- |
| `ls smoke.txt` | 0 | `qa/ac-checks.md` | File exists at repo root (AC1) | Nothing about content |
| `printf 'OK\n' > docs/SMOKE-003/qa/expected-ok.txt` then `cmp smoke.txt docs/SMOKE-003/qa/expected-ok.txt` | 0 (no output = identical) | `qa/ac-checks.md` + fixture `qa/expected-ok.txt` | Byte-identical to `OK\n` reference (AC2) | Nothing about how the file was created |
| `sha256sum smoke.txt` | 0 | `qa/ac-checks.md` | Immutable fingerprint `a12b7cb43c9d9134b5bb1b35e9096b66775d9e92e7611d1cc92b02edd6782a87` (AC2, re-checkable) | Nothing beyond hash match |
| `wc -l -c smoke.txt` | 0 | `qa/ac-checks.md` | 1 line, 3 bytes (AC3) | Nothing about byte values |
| `od -A x -t x1z smoke.txt` | 0 | `qa/ac-checks.md` | Hex `4f 4b 0a`, no trailing/extraneous bytes (AC3) | Nothing about post-merge state |
| `cmp smoke.txt docs/SMOKE-003/qa/expected-ok.txt && od -A x -t x1z smoke.txt` (ticket re-run form) | 0 | `qa/ac-checks.md` | The ticket's own re-run passes with fixture path substituted for `/tmp/expected` (harness blocks `/tmp` writes) | Same as AC2+AC3 |
| `git ls-files smoke.txt` | 0 | `qa/ac-checks.md` | File is tracked, so it rides the Done merge | Post-merge state on `main` |
| `git diff 501a4c0..HEAD --stat` | 0 | this file | Branch delta = 2 files, 37 insertions, both in ticket scope | — |
| `grep -cE '<secret patterns>' smoke.txt docs/SMOKE-003/work/implementation-notes.md` | 0 matches each | `qa/security-audit.md` | No secret-pattern hits in the added files | Patterns not caught by the regex list |

## How to re-run (full suite)

```bash
printf 'OK\n' > docs/SMOKE-003/qa/expected-ok.txt
cmp smoke.txt docs/SMOKE-003/qa/expected-ok.txt
wc -l -c smoke.txt
od -A x -t x1z smoke.txt
sha256sum smoke.txt
git diff 501a4c0..HEAD --stat
```

## Review details (why clean)

- Diff vs main: exactly `A smoke.txt` (1 line) and `A docs/SMOKE-003/work/implementation-notes.md` (36 lines). No other repo file touched — no drive-by refactors.
- Ticket requirements: `smoke.txt` at repo root with exactly one line `OK`; work notes recorded. Both present and byte-verified.
- Done Signals: all three observable signals met (`smoke.txt` 3 bytes `4f 4b 0a`; work notes contain execution proofs; "Not proven" item — post-merge state — correctly left to the orchestrator).
- Difficulty `trivial` and "no test code" rationale in `## Implementation` agree with a static text deliverable whose proof is the shell AC checks themselves.
- No CRITICAL/HIGH/MEDIUM findings.

## Merge preflight details

See `qa/merge-preflight.md` (mirror of gitignored `qa/merge-tree.log`).
