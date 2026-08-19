# Copilot backend (`copilot` kind)

First-class GitHub Copilot CLI backend. Phase 1 (TASK-18) extracted it from
`pi.py`; Phase 2 (TASK-19) implemented the working JSONL agent backend;
Phase 3 (TASK-20) added authoritative quota probing via the CLI's internal
JSON-RPC and runtime exhaustion classification
(`docs/plans/copilot-cli-backend-implementation-plan.md` §6–9, §16–18). See
[[usage-aware-agent-profiles]] for the probe registry context.

## Module layout

- `src/symphony/backends/copilot.py` — `CopilotBackend(PerTurnCliBackend)`
  (copilot.py:44) and `CopilotUsageProbe(UsageProbe)` (copilot.py:423);
  eager `USAGE_PROBES["copilot"] = CopilotUsageProbe` at module import
  (copilot.py:556).
- `pi.py` holds zero Copilot symbols — the probe class, its registration,
  and the now-unused `datetime`/`timezone` and usage imports were removed.
- Usage source canonicalized: `USAGE_SOURCE_ALIASES = {"github-copilot":
  "copilot"}` normalized at the top of `get_usage_probe`
  (usage.py:41/51) — legacy `source: github-copilot` configs and profile
  bindings keep resolving to `CopilotUsageProbe` unchanged.

## CLI contract (TASK-19 Phase 2, verified)

- One subprocess per turn; `_command_for_turn(*, prompt, is_continuation)`
  returns a single `shlex.join(parts)` string (keyword-only). Base flags
  `copilot --output-format=json --no-ask-user --allow-all-tools`, then
  `--model`/`--reasoning-effort` when configured, always `--session-id`,
  `--add-dir <root>` per `git_roots_outside(cwd, workspace_root)`, and
  `-p <prompt>` last. `_stdin_payload` returns `None` (prompt travels via
  `-p`, never stdin).
- Permission flags are hardcoded in `_command_for_turn` — never in
  `CopilotConfig.command` (which carries only the executable). Writable
  roots become repeated `--add-dir`; `--allow-all`/`--yolo` never used.
- JSONL parsing (`_decode_events` + `_complete_turn`): line-by-line
  `json.loads`, non-JSON lines and non-dict objects skipped, unknown event
  types ignored — the parser never raises. `assistant.message.data.content`
  is the authoritative final response; `assistant.message.data.outputTokens`
  is accumulated into `_latest_usage` (no synthesis from
  `premiumRequests`/`totalNanoAiu`); `result.sessionId`/`exitCode` is the
  authoritative completion signal; `session.error` fails the turn.
- Sessions: `--session-id` is ALWAYS sent — fresh `str(uuid.uuid4())` on
  first turn, reused across turns when `resume_across_turns` is true, fresh
  per turn when false (superset of plan §6, which gated it on resume).
  `resume_session(session_id)` validates via the shared
  `_is_valid_session_id` house helper (str, non-empty, printable, ≤512 —
  "safe to forward", not strict UUID syntax; same pattern as
  claude_code/opencode/pi). A `result.sessionId` that mismatches the
  expected recovered id, or a recovered session the CLI never confirms,
  raises `TurnFailed`.
- Exhaustion: `_is_genuine_copilot_exhaustion` (copilot.py:247-273) — rpm/tpm
  transients (`requests per minute`, `tokens per minute`, `rpm`, `tpm`) and
  generic 429/`rate limit exceeded` are NOT exhaustion (plain `TurnFailed`,
  retryable; mirrors `_is_genuine_pi_exhaustion`); genuine quota/credit
  keywords (`quota exceeded`, `usage limit reached/exceeded`, `insufficient
  credits`, `out of credits`, `ai credits exhausted`, `premium requests
  exhausted`, `provider_usage_exhausted`, `plan limit reached/exceeded`) emit
  `EVENT_PROVIDER_USAGE_EXHAUSTED` and raise `ProviderCapacityError`
  (capacity wait that bypasses the retry budget). Hooked via
  `CopilotBackend._check_provider_exhaustion` (copilot.py:67-70) and the
  `_complete_turn` error path (copilot.py:198-205).

## Authoritative quota probe (TASK-20 Phase 3)

- `CopilotUsageProbe(UsageProbe)` (copilot.py:423) spawns
  `copilot --server --stdio --no-auto-update --log-level error` (plan §17;
  `--server` guard appends the flags when a custom `command=` lacks them) and
  sends one LSP-framed JSON-RPC request — `Content-Length` header + JSON body
  `{"jsonrpc":"2.0","id":1,"method":"account.getQuota","params":{}}`. No
  `initialize` handshake needed (the CLI answers `-32601 Unhandled method
  initialize`; `account.getQuota` works directly). `_read_lsp_message`
  (copilot.py:513-553) parses the header (int `Content-Length`, rejects
  `<= 0`) and body (non-dict JSON → None), bounded by 5s `wait_for` per read.
- `normalize_copilot_quota` (copilot.py:307-420): the meaningful bucket is
  `result.quotaSnapshots.premium_interactions` (`chat`/`completions` are
  unlimited); `used_percent = 100.0 - remainingPercentage`, clamped 0–100,
  keyed as a generic `monthly` window (NOT Codex five-hour/weekly semantics).
  `hard_limit_reached` when `hasQuota` is false or remaining <= 0; `resetDate`
  parsed into `resets_at` (ISO or epoch s/ms), falling back to
  `next_month_first_day_utc()` only when absent/unparseable (plan §18 — the
  provider value is a short rolling window, do not assume a calendar month).
- Fail open: `fetch_usage()` (copilot.py:439-447) wraps everything in
  try/except → None on spawn failure, timeout, disconnect, or malformed
  frames; a `cached_snapshot` constructor arg short-circuits (test hook).
  All RPC handling stays inside the probe — scheduler
  (`orchestrator/usage.py`) remains provider-agnostic.

## Config surface

- `CopilotConfig` (frozen dataclass): `command`, `turn/read/stall_timeout_ms`,
  `resume_across_turns`, `model`, `reasoning_effort` — config.py:559.
- `ServiceConfig.copilot: CopilotConfig | None = None` (defaulted,
  config.py:804); `backend_timeouts()` copilot branch falls back to
  defaults; builder parses the optional `copilot:` YAML block;
  `preflight.py` rejects empty `copilot.command` when configured.
- `SUPPORTED_AGENT_KINDS` includes `copilot`; `PROFILE_FIELDS_BY_KIND
  ["copilot"]` = pi field set + `model`/`reasoning_effort`
  (constants.py:95/165); `DEFAULT_COPILOT_COMMAND = "copilot"`.
- Factory branch `kind == "copilot"` in `build_backend`
  (backends/__init__.py:292) — plain if/elif chain, no registry refactor.
- Doctor: `check_copilot_auth` (doctor.py:410) — env vars
  `COPILOT_GITHUB_TOKEN`/`GH_TOKEN`/`GITHUB_TOKEN`, then
  `~/.config/github-copilot/hosts.json` and `~/.config/gh/hosts.yml`; warns
  when agent.kind=copilot and no auth found. Doctor check count 23 -> 24.

## Test coverage (TASK-18 + TASK-19 + TASK-20)

- `tests/test_copilot_backend.py` (43 tests): factory returns
  `CopilotBackend`, pi.py zero-symbol invariant, command flags (§23),
  session lifecycle/resume/validation (§24), JSONL completion +
  session.error + outputTokens telemetry + non-zero `result.exitCode` +
  recovered-session mismatch/unconfirmed + malformed/unknown-line
  tolerance (§25), usage tests (§26: probe-in-module, failure-fails-open,
  remaining→used conversion, monthly reset) + capacity tests (§27:
  genuine exhaustion emits `EVENT_PROVIDER_USAGE_EXHAUSTED` ->
  `ProviderCapacityError`, generic 429/RPM/TPM NOT exhaustion, exhausted
  pool blocks all Copilot profiles, configured cap blocks new dispatch,
  running worker never cancelled), two standalone LSP-RPC tests
  (happy path asserts `account.getQuota` + `Content-Length` on stdin;
  malformed frame fails open), config parsing + profile resolution +
  unknown-field rejection, and `test_copilot_run_turn_end_to_end` (full
  `run_turn` via `_FakeSubprocess`).
- Contract suite: `TestCopilotBackendContract` in
  `tests/test_backend_contract.py:396`; `copilot` in `_SPAWN_MODULES`
  git-grant parametrization.
- Registry test asserts both `copilot` and legacy `github-copilot` resolve
  to `CopilotUsageProbe`.
- Global fail-open invariant: `copilot` added to the
  `test_usage_probe_failure_never_prevents_dispatch` parametrize in both
  `tests/test_backend_usage_probes.py` and
  `tests/test_orchestrator_usage_limits.py` (28 tests in the latter).

Not proven (Done Signal): live Copilot CLI execution with a real token —
the suite uses `_FakeSubprocess` doubles.
