# Copilot backend (`copilot` kind)

First-class GitHub Copilot CLI backend, extracted from `pi.py` by TASK-18
(Phase 1 of `docs/plans/copilot-cli-backend-implementation-plan.md`). See
[[usage-aware-agent-profiles]] for the probe registry context.

## Module layout

- `src/symphony/backends/copilot.py` — `CopilotBackend(PerTurnCliBackend)`
  (copilot.py:34) and `CopilotUsageProbe(UsageProbe)` (copilot.py:242);
  eager `USAGE_PROBES["copilot"] = CopilotUsageProbe` at module import
  (copilot.py:259).
- `pi.py` holds zero Copilot symbols — the probe class, its registration,
  and the now-unused `datetime`/`timezone` and usage imports were removed.
- Usage source canonicalized: `USAGE_SOURCE_ALIASES = {"github-copilot":
  "copilot"}` normalized at the top of `get_usage_probe`
  (usage.py:41/51) — legacy `source: github-copilot` configs and profile
  bindings keep resolving to `CopilotUsageProbe` unchanged.

## CLI contract

- One subprocess per turn: `copilot --output-format=json --no-ask-user
  --allow-all-tools [-p <prompt>]`; `--model`/`--reasoning-effort` added
  when configured; prompt and flags joined with `shlex.join`.
- Permission flags (`--allow-all-tools`, `--no-ask-user`) and `--add-dir`
  writable-git-roots are hardcoded in `_command_for_turn` — never in
  `CopilotConfig.command`, which carries only the executable.
- JSONL events consumed: `assistant.message` (content + `outputTokens`),
  `result` (`sessionId` + `exitCode`), `session.error`; non-JSON or blank
  lines ignored.
- Sessions: `--session-id` threaded across turns; `resume_session` rejects
  empty/whitespace/NUL ids; a recovered session id different from the
  expected one raises `TurnFailed`.
- Exhaustion: `_is_genuine_copilot_exhaustion` — rpm/tpm transients are
  NOT exhaustion (normal retry); quota/usage-limit/credit keywords are
  (capacity wait).

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

## Test coverage (TASK-18)

- `tests/test_copilot_backend.py` (12 tests): factory returns
  `CopilotBackend`, pi.py zero-symbol invariant, command flags, session
  lifecycle/resume/validation, JSONL completion + session.error,
  config parsing + profile resolution + unknown-field rejection.
- Contract suite: `TestCopilotBackendContract` in
  `tests/test_backend_contract.py`; `copilot` added to
  `_SPAWN_MODULES` git-grant parametrization.
- Registry test asserts both `copilot` and legacy `github-copilot` resolve
  to `CopilotUsageProbe`.

Not proven (Done Signal): live Copilot CLI execution with a real token —
the suite uses `_FakeSubprocess` doubles.
