# TASK-20 QA — Security audit analysis (7 rows)

**What**: Security review of the TASK-20 diff (src/symphony/backends/copilot.py +289, tests/test_copilot_backend.py +399, tests/test_orchestrator_usage_limits.py +1).
**Why**: Verify the new JSON-RPC probe and quota normalization introduce no new attack surface.
**As-Is -> To-Be**:
- As-Is: Placeholder probe, no I/O.
- To-Be: Probe spawns the Copilot CLI, reads quota JSON, normalizes it. Reviewed against the 7 standard dimensions.

## Rows

| # | Area | Result | Basis |
|---|---|---|---|
| 1 | secrets | pass | No credential handling added. The probe sends a parameterless `account.getQuota` request over stdin; the response contains quota counters only. `stderr` is captured via PIPE and never logged. No tokens/passwords in the diff (`git diff 3a436bb..HEAD`). |
| 2 | input-validation | pass | `_read_lsp_message` validates the `Content-Length` header (int parse, `<= 0` rejected) and JSON body (non-dict rejected → None). `normalize_copilot_quota` guards every field: `isinstance` checks on `result`/`quotaSnapshots`/`premium`, `float()` conversions wrapped in try/except, percentages clamped to 0–100. |
| 3 | injection | pass | The CLI is spawned with a fixed default command (`copilot --server --stdio --no-auto-update --log-level error`); the `command=` constructor arg is test-only — `ProviderUsageManager` instantiates probes with no args (src/symphony/orchestrator/usage.py:78 `probe_cls()`). No external string reaches `bash -lc`. Quota response data is never interpolated into commands or paths. |
| 4 | xss | n/a | No HTML/UI rendering of quota data in this diff (backend + tests only). |
| 5 | csrf | n/a | No web endpoints touched. |
| 6 | authz | n/a | No authorization decisions; the probe reuses the CLI's own authenticated session. |
| 7 | rate-limit | pass | Stage A explicitly distinguishes generic 429/RPM/TPM transients from account exhaustion: the RPM/TPM guard short-circuits, `429 Too Many Requests` and `rate limit exceeded` assert False in `test_generic_rate_limit_does_not_mark_plan_exhausted`, while genuine signals (`quota exceeded`, `insufficient credits`, …) assert True and the pool-block path is covered by `test_exhausted_copilot_pool_blocks_all_copilot_profiles`. |

## How to re-run

```bash
cd /home/symphony/symphony_workspaces/TASK-20
git diff 3a436bb..HEAD -- src/symphony/backends/copilot.py
grep -n 'probe_cls()' src/symphony/orchestrator/usage.py
```

## What this proves / does not prove

- Proves: static review found no secrets, injection, or validation gaps in the new probe; runtime rate-limit classification is covered by the collected tests (zero failures per `qa/pytest-cache-evidence.md`).
- Does not prove: behavior of a future Copilot CLI whose server mode changes — mitigated by fail-open (`fetch_usage` returns None on any error, verified by `test_copilot_quota_probe_failure_fails_open`).
