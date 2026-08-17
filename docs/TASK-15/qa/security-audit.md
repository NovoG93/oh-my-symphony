# Security Audit: Stage 2.2-2.8 backend usage probes (Verify, 2026-08-17)

**What**: 7-area security review of the branch delta (probes, normalizers, exhaustion hooks, registry).
**Why**: New subprocess execution and provider-output parsing are the security-relevant surfaces.
**As-Is -> To-Be**: Unreviewed new code -> Audited in full; no CRITICAL/HIGH/MEDIUM security findings.

| Area | Result | Analysis (full diff read) |
|---|---|---|
| secrets | pass | No secrets read, written, logged, or transmitted. `AgyUsageProbe` parses stdout JSON of a provider CLI and discards stderr; claude/gemini/kiro/opencode/pi probes are passive (cached snapshot or `None`). Error text placed into events is provider stderr already visible to the operator; no credential keys are referenced anywhere in the diff. |
| input-validation | pass | All four normalizers (`normalize_agy_usage`, `normalize_claude_usage`, `normalize_gemini_usage`, `normalize_kiro_usage`, `normalize_opencode_local_usage`) defensively parse untrusted provider output: `isinstance(dict)` guards, `try/except` float coercion, unknown keys skipped via `non_window_keys`, malformed JSON -> `None` fail-open (`agy.py` `fetch_usage`). `_parse_gemini_exhaustion` regexes operate on bounded text with safe numeric conversion. |
| injection | pass | The only shell execution added is `asyncio.create_subprocess_shell` in `AgyUsageProbe` with a fixed operator-config constant (`"agy -p /quota --output-format json"`); no user, issue, or provider-controlled data is interpolated into the command. All other probes execute nothing. Claude's existing `resolve_bash() -lc` path is untouched by this diff. |
| xss | n/a | No HTML/UI rendering in scope; this is a CLI orchestrator backend layer. |
| csrf | n/a | No state-changing web endpoints in scope. |
| authz | n/a | Local single-user CLI; no authorization decisions added. Snapshots written on ProviderCapacityError are pool-scoped telemetry, not privilege state. |
| rate-limit | pass | The change *implements* rate-limit handling: exhaustion detection excludes transient rpm/tpm/429 so those still flow into standard backoff retries, while genuine plan/credit exhaustion pauses dispatch. Probe polling remains TTL-bounded by the pre-existing `ProviderUsageManager` cache (`orchestrator/usage.py:37`); fail-open guarantees a broken probe can never stall scheduling. |

**How to re-run**: read `git diff develop...HEAD` (the delta reviewed above); no runtime path added that a sandbox would exercise differently.
