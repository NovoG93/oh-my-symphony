# TASK-14 QA — Security audit of the diff (8353534..b849565)

**What**: Seven-dimension security review of the Codex usage probe and provider-exhaustion change.
**Why**: The change adds a new subprocess-based probe, parses provider-supplied payloads, and alters retry accounting — each a place where mistakes would be security-relevant.
**As-Is -> To-Be**: As-Is: no security review on record. To-Be: each dimension has a verdict and an evidence anchor.

Method: full diff of the 8 changed files read (see `git diff 8353534..b849565`), plus the exercised code paths read in context.

## Rows

1. **secrets — pass.** No secrets are read, logged, or persisted. The probe reads `account/read` auth-mode metadata only; no token materialization anywhere in the diff. Error messages raised via `ProviderCapacityError` carry provider-supplied text but never credentials (`src/symphony/backends/codex.py` `_raise_for_terminal_status`).
2. **input-validation — pass.** All provider-supplied fields are defensively coerced: `_parse_resets_at` accepts int/float/str/datetime with try/except and returns `None` on unparseable input; `normalize_codex_rate_limits` guards float conversion and unknown `windowDurationMins` values degrade to a safe `<N>_minutes` key or the raw key name (`src/symphony/backends/codex.py`).
3. **injection — pass.** The standalone probe spawns `resolve_bash() -lc <command>` where the command is the operator-configured probe command (default `codex app-server`), never agent- or provider-controlled content; JSON-RPC writes are `json.dumps` of fixed structures (`src/symphony/backends/codex.py` `CodexUsageProbe._probe_standalone`).
4. **xss — n/a.** No HTML, web, or UI surface in this change; events flow to board text and structured logs.
5. **csrf — n/a.** No browser/web session state; the change is backend stdio JSON-RPC and orchestrator bookkeeping only.
6. **authz — pass.** API-key vs subscription authentication is distinguished: `apiKey` auth marks the snapshot `authoritative=False` so ChatGPT subscription caps never block API-key dispatch, while subscription-auth snapshots remain authoritative; no privilege escalation path is added (`normalize_codex_rate_limits` auth handling, asserted by `test_codex_api_key_auth_does_not_apply_chatgpt_cap`).
7. **rate-limit — pass.** Genuine plan/credit exhaustion is classified separately from transient RPM/TPM 429s (`_is_genuine_provider_exhaustion`); the exhaustion path cancels the worker, updates the shared snapshot, and clears retry trackers without consuming the retry budget, so scheduler re-dispatch is gated on `waiting_provider_usage` eligibility — preventing retry-storm churn against an exhausted provider (`src/symphony/orchestrator/core.py` `_on_codex_event`, `_on_worker_exit_impl`; asserted by `test_provider_exhaustion_does_not_consume_retry_budget`).

## Not covered by this audit

Live runtime behaviour (all execution denied this session — `qa/runtime-blocked.md`); static review only.
