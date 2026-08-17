# Security Audit Artifact (Verify, 2026-08-17)

**What**: Security review of the TASK-17 delta (7 files: 1 test module + 6 docs, `git diff 115223c..HEAD`).
**Why**: Every Verify pass must record a 7-row security audit; each pass/fail row needs a durable artifact.
**As-Is -> To-Be**: Unaudited delta -> 7-row audit on record.

Scope: this ticket's changes are tests (`tests/test_usage_limits.py`, +105 lines) and documentation (README.md, WORKFLOW.example.md, WORKFLOW.file.example.md, docs/features/agent-profiles.md, docs/TASK-17/work/*). No `src/` changes. Prior tickets' code was audited in their own Verify passes.

## Commands run (read-only, allowed)

1. `git diff 115223c..HEAD | grep -inE "api[_-]?key|token|secret|password|BEGIN [A-Z ]*PRIVATE"` — matches only prose mentions of the `claude` CLI token flag and a test-name reference to API-key auth behavior; **no literal credentials**.
2. Doc-symbol cross-check: `EVENT_PROVIDER_USAGE_EXHAUSTED` exists (`src/symphony/backends/__init__.py:50`); `waiting_provider_usage` reason exists (`src/symphony/orchestrator/usage.py:31`, used in `core.py:5649`); pool-defaulting claim `profile.usage_pool or selection.kind` matches `core.py:5635-5637`.
3. Test-behavior cross-check: `ProviderUsageManager.evaluate` (`src/symphony/orchestrator/usage.py:150-190`) fails open on missing/stale/non-authoritative snapshots — the semantics the new tests assert.

## 7-row audit

| Row | Result | Evidence / reason |
|---|---|---|
| secrets | pass | diff scan (cmd 1) found zero literal credentials; this artifact |
| input-validation | n/a | no runtime input paths changed; new tests *exercise* existing validation (cap %, unknown pools) without adding code |
| injection | n/a | test + markdown only; no executable/query/command construction added |
| xss | n/a | no HTML/JS source changed; docs text is not interpolated into web UI markup |
| csrf | n/a | no runtime handlers changed |
| authz | n/a | no runtime code changed |
| rate-limit | pass | the ticket's domain: new generic pool tests assert cap blocking/fail-open semantics; recorded run green in `qa/test-cache-evidence.md` |

## Findings

None CRITICAL/HIGH/MEDIUM/LOW. Clean review.
