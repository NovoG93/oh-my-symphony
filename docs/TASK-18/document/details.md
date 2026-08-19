# TASK-18 Document — stage notes

## Brief vs reality comparison (2026-08-19, rewind turn)

- Ticket arrived in state `Document` with a host-appended `## Contract Failure`:
  the gate could not find an exact `## Merge Status` section (verify had written
  `## Merge Status: preflight clean, ...`). Fix: normalized the heading and
  refreshed the section's facts to the current branch tip.
- Branch topology re-verified (read-only git): develop = `c829339`, HEAD =
  `795e70e` (wip commit, parent IS develop tip), `develop..HEAD` = 1,
  `HEAD..develop` = 0 -> pure fast-forward. `git diff be10c9d 795e70e -- src
  tests` is empty: the reviewed source is byte-identical; the host re-committed
  the verify tree adding only the 5 QA evidence docs.
- All 7 ticket items confirmed in the committed tree; Document re-ran the AC1-4
  greps (see `qa/ac-static.md`):
  - AC1: `grep -i copilot src/symphony/backends/pi.py` -> no output.
  - AC2: `CopilotBackend` at copilot.py:34, `CopilotUsageProbe` at :242,
    eager `USAGE_PROBES["copilot"]` at :259.
  - AC3: factory branch `backends/__init__.py:292`, profiles branch
    profiles.py:73, `SUPPORTED_AGENT_KINDS`/`PROFILE_FIELDS_BY_KIND`/
    `DEFAULT_COPILOT_COMMAND` in constants.py, alias normalization
    usage.py:41/51.
  - AC4: `copilot: CopilotConfig | None = None` at config.py:804;
    `backend_timeouts()` branch; builder + preflight wiring.
- AC5 stays indirect: live pytest denied; `.pytest_cache` shows 2595 nodeids,
  `lastfailed={}`, 12/12 `test_copilot_backend.py` nodeids collected.
- No Document Defect: no ticket claim contradicts reality after the hash
  refresh; the two stale user-facing docs found were updated in this lane.

## Stale docs fixed

- `docs/llm-wiki/usage-aware-agent-profiles.md` — three spots still taught
  `GithubCopilotUsageProbe` inside pi.py and `github-copilot` as canonical:
  probe list (Pi/Prime bullet), delegation example, registry list.
- `docs/features/agent-profiles.md` — delegation example `pi-copilot ->
  github-copilot` -> canonical `pi-copilot -> copilot` with alias note.
- `qa/merge-preflight.md` + `qa/pytest-cache.md` — commit-hash facts refreshed
  to the current branch tip so evidence matches what the orchestrator merges.
- `verify/details.md` — post-verify note added (diff-count context).

## Created

- `docs/llm-wiki/copilot-backend.md` — new topic page (module layout, CLI
  contract, config surface, constraints, test coverage, not-proven boundary).
- `docs/TASK-18/document/details.md` — this file.

## Not covered

- Live pytest re-run (harness-denied; cache-indirect evidence only).
- Live Copilot CLI execution with a real token (Done Signal, Not proven).
