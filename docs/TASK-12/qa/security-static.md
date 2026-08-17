# TASK-12 Verify: Static Security Scan of the Diff

Scope: all lines added by `develop..HEAD` (full diff materialized in `qa/diff.md`).
Commands run 2026-08-17, each exit 0:

1. Secret literals — `grep -nE '^\+.*(api_key|apikey|password|passwd|secret|token|credential)' qa/diff.md`
   -> **no matches**. No secrets introduced; the change adds dataclasses, validation, and a registry only.
2. Execution/injection constructs — `grep -nE '^\+.*(subprocess|os\.system|eval\(|exec\(|shell=True|pickle|yaml\.load|marshal)' qa/diff.md`
   -> **no matches**. No shell/OS/eval surface added; config values never reach an execution path.
3. Network / env access — `grep -nE '^\+.*(requests|httpx|urlopen|socket|os\.environ|getenv|fetch\(|\.get\()' qa/diff.md`
   -> only benign dict `.get()` calls (config parsing / registry lookup, diff lines 116, 154-282, 775). No network or environment access introduced.

Input-validation surface (xss/csrf/authz/rate-limit n/a rows rely on this): all newly accepted
workflow-config input (`usage_pools` names, sources, window names, cap values, `usage_pool`
references) passes through strict type/range checks in `_validated_usage_pools` /
`_validated_agent_profiles` — see `qa/static-validation-review.md` AC4/AC5.
