# Copilot backend (`copilot` kind)

First-class GitHub Copilot CLI backend. Phase 1 (TASK-18) extracted it from
`pi.py`; Phase 2 (TASK-19) implemented the working JSONL agent backend
(`docs/plans/copilot-cli-backend-implementation-plan.md` §6–9). See
[[usage-aware-agent-profiles]] for the probe registry context.

## Module layout

- `src/symphony/backends/copilot.py` — `CopilotBackend(PerTurnCliBackend)`
  (copilot.py:36) and `CopilotUsageProbe(UsageProbe)` (copilot.py:266);
  eager `USAGE_PROBES["copilot"] = CopilotUsageProbe` at module import
  (copilot.py:283).
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
- Exhaustion: `_is_genuine_copilot_exhaustion` — rpm/tpm transients are
  NOT exhaustion (plain `TurnFailed`, retryable); quota/usage-limit/credit
  keywords emit `EVENT_PROVIDER_USAGE_EXHAUSTED` and raise
  `ProviderCapacityError` (capacity wait that bypasses the retry budget).

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

## Test coverage (TASK-18 + TASK-19)

- `tests/test_copilot_backend.py` (32 tests): factory returns
  `CopilotBackend`, pi.py zero-symbol invariant, command flags (§23),
  session lifecycle/resume/validation (§24), JSONL completion +
  session.error + outputTokens telemetry + non-zero `result.exitCode` +
  recovered-session mismatch/unconfirmed + malformed/unknown-line
  tolerance (§25), genuine-vs-rate-limit exhaustion (§27), config parsing
  + profile resolution + unknown-field rejection, and
  `test_copilot_run_turn_end_to_end` (full `run_turn` via
  `_FakeSubprocess`).
- Contract suite: `TestCopilotBackendContract` in
  `tests/test_backend_contract.py:396`; `copilot` in `_SPAWN_MODULES`
  git-grant parametrization.
- Registry test asserts both `copilot` and legacy `github-copilot` resolve
  to `CopilotUsageProbe`.

Not proven (Done Signal): live Copilot CLI execution with a real token —
the suite uses `_FakeSubprocess` doubles.
