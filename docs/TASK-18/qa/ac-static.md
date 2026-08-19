# TASK-18 QA — Static acceptance checks (AC 1–4)

Run 2026-08-19 against worktree HEAD `be10c9d` (parent = develop tip `c829339`).
Live pytest/ruff denied by the permission harness — see `qa/runtime-blocked.md`.

## AC 1 — pi.py contains zero Copilot symbols

Command: `grep -i copilot src/symphony/backends/pi.py` → **no output** (exit 1, no matches).

Also verified the removed imports left no dangling usage:

- `grep -n "datetime\|timezone\|ProviderUsageSnapshot\|UsageProbe\|UsageWindow\|USAGE_PROBES" src/symphony/backends/pi.py` → no matches.
- `grep -rn "GithubCopilotUsageProbe" src tests` → no matches (only historical docs/plans text).

What it proves: zero Copilot-specific symbols in the committed `pi.py` (case-insensitive,
any occurrence). What it does not prove: runtime behavior — this is a static content check.

Re-run: `grep -i copilot src/symphony/backends/pi.py`

## AC 2 — copilot.py exists with both classes

- `src/symphony/backends/copilot.py` exists (259 lines).
- `class CopilotBackend(PerTurnCliBackend)` at line 34.
- `class CopilotUsageProbe(UsageProbe)` at line 242.
- `USAGE_PROBES["copilot"] = CopilotUsageProbe` at line 259.
- `_is_genuine_copilot_exhaustion` at line 215 (RPM/TPM excluded; quota keywords detected).

What it proves: module present with the required symbols and eager probe registration,
matching plan §3/§16. What it does not prove: the classes behave correctly at runtime.

Re-run: `grep -n "^class CopilotBackend\|^class CopilotUsageProbe" src/symphony/backends/copilot.py`

## AC 3 — kind & factory wiring

- `src/symphony/workflow/constants.py:95` — `SUPPORTED_AGENT_KINDS` includes `"copilot"`.
- `src/symphony/workflow/constants.py:165` — `PROFILE_FIELDS_BY_KIND["copilot"]` = pi field set
  (command, resume_across_turns, turn/read/stall timeouts, usage_pool) + model + reasoning_effort.
- `src/symphony/workflow/constants.py:176` — `DEFAULT_COPILOT_COMMAND = "copilot"`.
- `src/symphony/backends/__init__.py:292` — `if kind == "copilot": from .copilot import CopilotBackend; return cast(AgentBackend, CopilotBackend(init))`; unsupported-kind error string updated to list `copilot`.
- `src/symphony/workflow/profiles.py:48` — `copilot: CopilotConfig | None = None` on
  `ResolvedAgentConfig`; `_get_backend_config` branch at line 73 (`cfg.copilot or _default_copilot_config()`).
- `src/symphony/backends/usage.py:39` — `USAGE_SOURCE_ALIASES = {"github-copilot": "copilot"}` normalized at the top of `get_usage_probe` (line 51), before the lazy if/elif chain; `elif source == "copilot"` branch imports `CopilotUsageProbe` from `.copilot` (lines 78–81). Legacy `source: github-copilot` (still used in `tests/test_usage_limits.py`) resolves through the alias.

What it proves: the factory has a `copilot` branch returning `CopilotBackend`, the kind is
supported, and the legacy alias resolves. What it does not prove: the branch executes
(static read of the return path only).

Re-run: `grep -n 'kind == "copilot"' src/symphony/backends/__init__.py src/symphony/workflow/profiles.py`

## AC 4 — CopilotConfig defaulted on ServiceConfig

- `src/symphony/workflow/config.py:559` — `@dataclass(frozen=True) class CopilotConfig` with
  `command: str = DEFAULT_COPILOT_COMMAND`, defaulted timeouts, `resume_across_turns: bool = True`,
  `model: str = ""`, `reasoning_effort: str = ""`; `_default_copilot_config()` at line 572.
- `src/symphony/workflow/config.py:804` — `copilot: CopilotConfig | None = None` on `ServiceConfig`
  (defaulted, so existing configs without a `copilot:` block keep constructing).
- `backend_timeouts()` extended: `copilot` branch at line 842 (falls back to defaults when None);
  error string updated at line 874.
- `src/symphony/workflow/builder.py:608` — parses optional `copilot:` YAML block with
  `_validated_positive_or_default` guards; wired into `build_service_config` at line 912.
- `src/symphony/workflow/preflight.py:97` — rejects empty `copilot.command` when configured.
- `src/symphony/workflow/__init__.py` — exports `CopilotConfig` and `DEFAULT_COPILOT_COMMAND`.

What it proves: the config surface matches plan §12/§13 and the field is optional/defaulted.
What it does not prove: parsing behavior (covered by tests — see `qa/pytest-cache.md`).

Re-run: `grep -n "copilot: CopilotConfig" src/symphony/workflow/config.py`

## Security static scan (supports Security Audit rows)

- Secrets: `git show be10c9d -- src | grep -nE "ghp_|sk-|AIza|Bearer [A-Za-z0-9]{20}|password=|api[_-]?key="`
  → only two docstring/flag matches ("--no-ask-user"), no credential literals.
- Input validation: `_is_valid_session_id` gates `resume_session` (rejects empty/whitespace/NUL —
  `tests/test_copilot_backend.py::test_copilot_invalid_resume_session_rejected`); builder uses
  `_validated_positive_or_default` for all three timeouts; unknown profile fields rejected
  (`test_invalid_copilot_profile_rejected`).
- Injection: prompt and flags assembled via `shlex.join` (copilot.py:117) — shell metacharacters
  in the prompt are quoted, matching other backends.
- Authz: permission flags (`--allow-all-tools`, `--no-ask-user`) and `--add-dir` git roots are
  hardcoded in `CopilotBackend._command_for_turn`; roots come from `git_roots_outside(init.cwd,
  init.workspace_root)` (copilot.py:47) — writable-root boundary enforced in the backend.
- Rate-limit: `_is_genuine_copilot_exhaustion` returns False for rpm/tpm transients, True for
  quota keywords; probe `fetch_usage` fails open (returns cached snapshot or None).

## Constraints honored (static)

- No registry refactor: `build_backend` remains the plain if/elif factory.
- Permission flags (`--allow-all-tools`, `--no-ask-user`, `--add-dir`) live hardcoded in
  `CopilotBackend._command_for_turn` (copilot.py:91–117), never in `CopilotConfig.command`.
- `check_copilot_auth()` is a standalone function in `src/symphony/cli/doctor.py:410`,
  separate from `check_pi_auth` / `check_prime_agent_auth`; it reports env-var names and
  config-file paths only, never token values.
