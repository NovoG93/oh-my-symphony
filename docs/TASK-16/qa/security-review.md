# TASK-16 Verify — Security Audit Detail

**What**: Seven-row security review of the Stage 5 delta (API projection + UI card).
**Why**: The change moves telemetry into API payloads and DOM rendering — check each classic attack surface.
**As-Is -> To-Be**: Unaudited projection/card code -> Each surface checked with anchors, all clear.

## 1. Secrets — pass
The delta adds no credentials, tokens, or keys. `provider_usage` carries pool ids, source names, percentages, ISO timestamps, and booleans only (core.py:2891-2969). `usage_pools` serializes `source` + `caps` floats from workflow config (webapi.py:748-754), which is the same config already exposed by the workflow payload. Nothing new crosses the trust boundary.

## 2. Input validation — pass
No new user input is parsed. Pool ids come from validated workflow config (`_validated_usage_pools` in `src/symphony/workflow/builder.py`, Stage 1) or from `ProviderUsageManager` snapshots populated by probes. The projection guards every nullable field: `cfg is None`, `snap is None`, `used_percent is None`, `resets_at` non-datetime pass-through (core.py:2899-2936). Values that reach JSON are native types.

## 3. Injection — pass
No SQL, shell, or template construction anywhere in the delta. The only string interpolation is JS `t()` (i18n.js:1210-1215) and a computed CSS `width: ${pct}%` where `pct = Math.min(100, Math.max(0, used))` (app.js:2891-2892) — a non-numeric value coerces to `NaN%`, which browsers reject as an invalid declaration rather than executing anything.

## 4. XSS — pass
All card content is built with `el()`, which creates text nodes for string children (`document.createTextNode`, app.js:252). No `innerHTML`/`insertAdjacentHTML` in the delta. User-influenceable strings (pool id, source, resets_at) flow into text nodes or `className` from a fixed vocabulary — no attribute injection vector.

## 5. CSRF — n/a
The delta adds no state-changing endpoints; `usage_pools` (GET /api/v1/workflow) and `provider_usage` (GET board payload) are read-only additions to existing GET routes.

## 6. Authz — n/a
No new routes and no new permission checks. The new fields extend payloads of endpoints already gated by the webapi auth model; they expose only quota telemetry and configured caps, not capabilities.

## 7. Rate-limit — pass
`snapshot()` → `_provider_usage_projection()` performs no network I/O per request: it reads the manager's cached snapshots (refresh is TTL-gated inside `ProviderUsageManager`, Phase 2) and `evaluate()` is a pure decision over those caches. Per-pool work per board request is O(pools × windows) dictionary math.

## What this does not cover
Runtime pen-testing of the running server was not possible in this pass (exec denied by the worktree permission policy — see `qa/runtime-blocked.md`); these rows are static review of the delta, which is the appropriate depth for a projection/DOM change with no new endpoints.
