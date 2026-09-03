# TASK-18 Verify — section overflow details

Post-verify note (Document, 2026-08-19): the host gate re-committed the verify tree
as `795e70e` (wip, same tree for src/tests — `git diff be10c9d 795e70e -- src tests`
empty) adding the 5 QA evidence docs; the 17-file change set reviewed below is
byte-identical to what will be merged. Merge facts refreshed in
`qa/merge-preflight.md`; the `## Merge Status` heading was normalized to the exact
gate string the ticket contract requires.

## Review notes (full diff read, `git diff develop...HEAD`, 17 files, +796/−43)

- All 7 ticket items implemented; plan §3–5, §10–14, §19 conformance checked line-by-line.
- Constraint "no registry refactor" honored: `build_backend` remains a plain if/elif chain.
- Constraint "permission flags in the backend, not the command string" honored: `--allow-all-tools`,
  `--no-ask-user`, `--add-dir` live in `CopilotBackend._command_for_turn`; `CopilotConfig.command`
  carries only the executable.
- Removed pi.py imports verified unused (grep for `datetime|timezone|ProviderUsageSnapshot|UsageProbe|
  UsageWindow|USAGE_PROBES` in pi.py → no matches).
- Legacy `source: github-copilot` still works via `USAGE_SOURCE_ALIASES`; the only remaining live
  user (`tests/test_usage_limits.py:97,333`) resolves through the alias to `CopilotUsageProbe`
  (registry test asserts both names resolve to `CopilotUsageProbe`).
- Diff includes two supporting files beyond the 7-item list — `workflow/preflight.py` (dispatch
  validation parity with every other kind) and `workflow/__init__.py` (public re-exports; required
  by the new doctor import). Classified in-scope kind-wiring, not orphan scope.
- `CopilotBackend.__init__` mirrors the opencode/claude `resolved_backend_config` pattern exactly.
- No CRITICAL/HIGH/MEDIUM findings → clean review.

## QA command manifest (full form)

| # | Command | Exit | Evidence | Proves | Does not prove |
|---|---------|------|----------|--------|----------------|
| 1 | `grep -i copilot src/symphony/backends/pi.py` | 1 (no match) | `qa/ac-static.md` | AC1: zero Copilot symbols in committed pi.py | runtime behavior |
| 2 | `grep -n "^class CopilotBackend\|^class CopilotUsageProbe" src/symphony/backends/copilot.py` | 0 | `qa/ac-static.md` | AC2: module exists, both classes + probe registration | runtime behavior |
| 3 | `grep -n 'kind == "copilot"' src/symphony/backends/__init__.py` + constants/profiles/usage greps | 0 | `qa/ac-static.md` | AC3: factory branch, SUPPORTED_AGENT_KINDS, alias normalization | branch executes |
| 4 | `grep -n "copilot: CopilotConfig" src/symphony/workflow/config.py` | 0 | `qa/ac-static.md` | AC4: defaulted ServiceConfig field + backend_timeouts branch | parsing behavior |
| 5 | `.venv/bin/pytest tests/test_copilot_backend.py -q ...` | denied | `qa/runtime-blocked.md` row 1, `qa/pytest-cache.md` | — (see cache analysis) | live green run |
| 6 | `git merge-tree --write-tree develop symphony/TASK-18` | denied | `qa/merge-preflight.md` | — (see topology proof) | textual merge simulation |

Re-runs: each command is re-runnable verbatim from the worktree root once the harness permits
process execution; read-only greps re-run as shown.

## Security Audit rationale (compact)

- secrets: no credential literals in the committed src diff (scanned for ghp_/sk-/AIza/Bearer/
  password=/api key patterns); `check_copilot_auth` prints env-var NAMES and config paths only.
- input-validation: session-id gate, positive-timeout guards, unknown-profile-field rejection
  (3 dedicated tests collected with zero failures).
- injection: `shlex.join` quoting of the prompt and flags (copilot.py:117).
- xss/csrf: no HTML or web-session surface in this change → n/a.
- authz: `--add-dir` roots derived from `git_roots_outside` only; flags not configurable via config.
- rate-limit: rpm/tpm classified transient (not exhaustion); probe fails open.
