# TASK-22 QA — Acceptance criteria checks (2026-08-19)

**What**: Re-run the three ticket-specified shell checks against `copilot-smoke.txt`.
**Why**: Independent, durable proof each AC holds at Verify time (not just at Implementation time).
**As-Is -> To-Be**: Implementer-reported proofs -> independently re-executed and recorded.

## Commands + results

```bash
ls copilot-smoke.txt
# copilot-smoke.txt   (exit 0)

cmp copilot-smoke.txt docs/TASK-22/work/expected-ok.txt
# (no output, exit 0 — byte-identical)

wc -l -c copilot-smoke.txt
# 1 3 copilot-smoke.txt

od -A x -t x1z copilot-smoke.txt
# 000000 4f 4b 0a                                         >OK.<

sha256sum copilot-smoke.txt
# a12b7cb43c9d9134b5bb1b35e9096b66775d9e92e7611d1cc92b02edd6782a87  copilot-smoke.txt

git status --porcelain
# (empty — clean tree)
```

## What this proves / does not prove

- Proves: `copilot-smoke.txt` exists at repo root, is exactly 3 bytes (`4f 4b 0a`), exactly 1 line, content byte-identical to `OK\n`, and the tree has no stray uncommitted changes.
- Does not prove: post-merge presence on `develop` (see `merge-tree.md`; orchestrator merges at `Done`).
- Re-run: `cmp copilot-smoke.txt docs/TASK-22/work/expected-ok.txt && wc -l -c copilot-smoke.txt && od -A x -t x1z copilot-smoke.txt && sha256sum copilot-smoke.txt`

## Full raw output

See `docs/TASK-22/qa/acceptance-checks-raw.txt` for the unedited terminal transcript.
