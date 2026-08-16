# SMOKE-003 Acceptance Checks (Verify)

Run: 2026-08-15T12:07:26Z. Worktree `symphony/SMOKE-003` @ `07fabe6`, working tree clean (`git status`).
Reference fixture: `qa/expected-ok.txt` (written via `printf 'OK\n' > docs/SMOKE-003/qa/expected-ok.txt` because the harness blocks writes to `/tmp`).

## AC1 — file exists

- Command: `ls smoke.txt`
- Exit code: 0
- Output: `smoke.txt`

## AC2 — exactly `OK`

- Command: `cmp smoke.txt docs/SMOKE-003/qa/expected-ok.txt`
- Exit code: 0, no output -> byte-identical to `OK\n`
- Command: `sha256sum smoke.txt`
- Exit code: 0
- Output: `a12b7cb43c9d9134b5bb1b35e9096b66775d9e92e7611d1cc92b02edd6782a87  smoke.txt`

## AC3 — nothing else

- Command: `wc -l -c smoke.txt`
- Exit code: 0
- Output: `1 3 smoke.txt` (1 line, 3 bytes)
- Command: `od -A x -t x1z smoke.txt`
- Exit code: 0
- Output: `000000 4f 4b 0a  >OK.<` — 3 bytes, hex `4f 4b 0a`, exactly one line, nothing else

## Ticket re-run form

- Command: `cmp smoke.txt docs/SMOKE-003/qa/expected-ok.txt && od -A x -t x1z smoke.txt`
- Exit code: 0
- Output: `000000 4f 4b 0a  >OK.<` — the ticket's own re-run passes when the fixture path is substituted for `/tmp/expected` (harness blocks `/tmp` writes).

## What this proves / does not prove

- Proves: the working tree `smoke.txt` is 3 bytes, hex `4f 4b 0a`, one line `OK\n`, byte-identical to the `OK\n` reference.
- Proves: `smoke.txt` is tracked (`git ls-files smoke.txt` -> `smoke.txt`), so it rides the Done merge.
- Does not prove: post-merge state on `main` (orchestrator creates the merge at Done).

## How to re-run

```bash
printf 'OK\n' > docs/SMOKE-003/qa/expected-ok.txt
cmp smoke.txt docs/SMOKE-003/qa/expected-ok.txt
wc -l -c smoke.txt
od -A x -t x1z smoke.txt
sha256sum smoke.txt
```
Expected: cmp exit 0 with no output; `1 3`; `4f 4b 0a >OK.<`; sha256 `a12b7cb43c9d9134b5bb1b35e9096b66775d9e92e7611d1cc92b02edd6782a87`.
