# TASK-16 Verify — Runtime Blocked Record

**What**: Which live commands the harness permission policy refused during Verify, and what replaces them.
**Why**: Keep the record honest: refused commands are "Not proven live", and each refusal gets an exact re-run command.
**As-Is -> To-Be**: Silent gap in live evidence -> Every refusal logged with its re-run command and its indirect substitute.

Refusals observed 2026-08-17 in the TASK-16 worktree (same policy shape as TASK-14 Verify):

| # | Command attempted | Result | Re-run command (outside worktree) |
|---|---|---|---|
| 1 | `.venv/bin/py.test tests/test_web_static_contract.py tests/test_webapi.py -k "usage_pools or provider_usage or waiting_provider_usage or usage_unknown or estimated_usage or remaining_percent_is_100 or open_project_starts_only_destination" -q` | "This command requires approval" | same command; exit code 0 expected |
| 2 | `git merge-tree --write-tree develop symphony/TASK-16` | "This command requires approval" | run from the host repo; exit code 0 expected |
| 3 | `ls ~/.cache/ms-playwright` (browser-binaries inventory) | blocked path (outside allowed dirs) | `ls ~/.cache/ms-playwright` |

## Indirect substitutes used

- Live pytest → `.pytest_cache/v/cache/` from the implementation turn's final full-suite run: `nodeids` (2557 entries, mtime 21:34) contains all 8 new Stage 6.12 tests; `lastfailed` = `{}` (mtime 21:31) records zero failures. This proves the suite ran green in this worktree after the implementation commit; it does not prove a fresh run in this Verify pass. See `qa/static-contract-verification.md`.
- merge-tree → topology proof: `git merge-base develop symphony/TASK-16` = `01f3a410e20ee97f95401909327ea08fe41b537f` = `git rev-parse develop`, i.e. the target tip is a strict ancestor of the feature branch, so the merge cannot conflict. See `qa/merge-tree.log`.
- Browser check → the repo's own Playwright suite (`tests/test_web_browser_e2e.py`, chromium via `TestServer` serving the real webapi app) ran in that same green suite run; its nodeids are present and absent from `lastfailed`. `playwright` is installed in `.venv`; had the chromium binary been missing, `p.chromium.launch()` would have failed the run — so browsers were present and the suite passed. The specific new card view was not re-driven live in this Verify pass.

## Not proven

- Live re-run of the 8 Stage 6.12 tests in this Verify pass.
- Live Playwright navigation to the workflow/settings views rendering `#provider-usage-card` (the e2e suite exercises the board view; the card mounts in workflow editor + settings).
- Live external provider rate-limit polling (out of scope — probes run offline/mocked per Done Signals).
