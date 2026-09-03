# TASK-19 Verify — indirect pytest evidence from `.pytest_cache`

Live pytest is denied in this worktree (`qa/runtime-blocked.md`), so this is
the strongest available evidence of the last recorded test session.

## Facts observed (2026-08-19)

- `.pytest_cache/v/cache/lastfailed` content: `{}` (2 bytes), mtime
  2026-08-19 16:45 — the most recent pytest session recorded **zero failing
  tests** among the tests it ran.
- `.pytest_cache/v/cache/nodeids` (239673 bytes, mtime 16:45) contains 2618
  nodeids, of which 37 are `tests/test_copilot_backend.py::*`.
- All **32 test names of the current committed file** (HEAD 4b5ba53) are
  present in nodeids, plus 5 stale names from earlier iterations
  (`test_copilot_complete_turn_extracts_assistant_message`,
  `test_copilot_complete_turn_handles_session_error`,
  `test_copilot_invalid_resume_session_rejected`,
  `test_copilot_resume_session`, `test_copilot_stdin_payload_is_none`).
- Installed pytest's cacheprovider
  (`.venv/lib/python3.14/site-packages/_pytest/cacheprovider.py`, NFPlugin)
  initialises `cached_nodeids` from the existing cache and **unions** each
  session's collected items into it (lines 433, 443-453, 469) — nodeids is a
  cumulative union across sessions, while `lastfailed` reflects only the most
  recent session.

## What this proves

- Every one of the 32 current copilot tests was collected (imported and
  listed) by a pytest session on this machine at some point up to 16:45 —
  i.e. the committed test module imports successfully in the venv.
- The most recent recorded session ended with zero failures
  (`lastfailed={}`), consistent with the Implementation claim "32 passed,
  full suite 742 passed".

## What this does NOT prove

- It does not prove the *final* file state (HEAD) was the one collected by
  the 16:45 session — nodeids is a union, and 5 stale names show the file was
  renamed after earlier runs.
- It does not prove the full suite count (742) or that the most recent
  session was a full-suite run rather than a single-file run.
- It says nothing about pyright/ruff (both denied; see runtime-blocked.md).

Verdict for AC 5: **Not proven live; indirect evidence only.**

How to re-run: `.venv/bin/pytest tests/test_copilot_backend.py -q` (see
`qa/runtime-blocked.md`).

## Pass 2 strengthening (2026-08-19, post-fix HEAD c01fc45)

Fix commit time 16:46; the `nodeids` cache file was rewritten at **17:03** —
a pytest session ran AFTER the fix, against the fixed file state (worktree
clean since). Two facts read from the installed
`.venv/lib/python3.14/site-packages/_pytest/cacheprovider.py` make this
decisive:

- `pytest_sessionfinish` returns early on `collectonly` (lines ~465-466), so
  a collect-only run never rewrites nodeids -> the 17:03 rewrite was a REAL
  execution session, not a collection dry-run.
- `lastfailed` is rewritten only when `saved_lastfailed != self.lastfailed`
  (lines ~421-423) -> a 17:03 session with any failure would have rewritten
  `lastfailed` at 17:03. It is still `{}` (mtime 16:45), therefore the 17:03
  session ended with ZERO failing tests.

What this proves: a real pytest session executed the fixed test file after
the fix and recorded zero failures. What it does not prove: the exact
"32 passed / full suite 2604 passed" counts of that session (nodeids is a
cumulative union — 2618 entries; 2604 passed + 14 skipped is consistent but
not verifiable from the cache alone).
