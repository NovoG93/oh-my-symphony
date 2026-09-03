# Security static review — TASK-13 Verify (2026-08-17)

Scope: the 5 code files changed by d250c37
(src/symphony/orchestrator/usage.py, src/symphony/orchestrator/__init__.py,
src/symphony/orchestrator/core.py, tests/test_orchestrator_usage_limits.py,
tests/test_usage_limits.py).

## Commands and results

| # | Command | Result |
|---|---|---|
| 1 | `grep -n -E "sk-[A-Za-z0-9]\|api[_-]?key\|password\|secret\|token\|Bearer" <5 files>` | Only pre-existing matches: `release_ticket_version_token` (completion-token protocol, not a secret) and token-accounting fields (`input_tokens`, `token_ema`, …) all outside the added hunks. No key/token/secret material added. |
| 2 | `grep -n -E "os\.system\|subprocess\|shell=True\|eval\(\|exec\(\|__import__\|pickle\|yaml\.load" <files>` | Zero matches (exit 1). |
| 3 | `git show d250c37` full read | No new network, file, or process APIs introduced; the only IO is in-memory dict caching and structured logging. |

## Findings

- **secrets**: pass — no credentials introduced; new code handles only
  percentages/timestamps.
- **input-validation**: pass — every value `evaluate()` compares comes from
  `UsagePoolConfig.caps`, validated at workflow load since TASK-12 to
  `0 < v <= 100` numeric values (`src/symphony/workflow/builder.py`,
  TASK-12 scope); `used_percent` is float-compared with `>=`.
- **injection**: pass — no subprocess/shell/URL/SQL/pickle/eval surface in
  the added code; probe calls are typed protocol methods, and a failing or
  missing probe cannot inject anything (result is a frozen dataclass).
- **xss**: n/a — no web/UI rendering of pool data in this change; wait
  reasons go to logs and scheduler entries, not HTML.
- **csrf**: n/a — no state-changing HTTP endpoints in scope.
- **authz**: n/a — no auth layer in scope; manager is in-process only.
- **rate-limit**: pass — this IS the rate-limit feature: caps are enforced
  only at dispatch eligibility (`core.py:5528-5560`), never against running
  workers (`core.py:3893-3903`), and fail open on missing/stale/non-
  authoritative telemetry (`usage.py:154-161`). Covered by tests
  `tests/test_orchestrator_usage_limits.py` (cap blocking, fail-open set,
  running-worker non-cancellation) and `qa/pytest-cache-evidence.md`.

## What this does not prove

Runtime behavior under a live provider (no probes registered in Stage 3 —
backend probes are Stages 4/5); no dynamic analysis was run (execution
denied, `qa/runtime-blocked.md`).
