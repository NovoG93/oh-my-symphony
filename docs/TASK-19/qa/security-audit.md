# TASK-19 Verify — Security Audit analysis (2026-08-19)

Scope: diff of commit 4b5ba53 (`src/symphony/backends/copilot.py`,
`tests/test_copilot_backend.py`, `docs/TASK-19/work/details.md`). Analysis is
static (code read); live tools were denied (`qa/runtime-blocked.md`).

| Row | Result | Rationale |
|---|---|---|
| secrets | pass | No credentials/tokens in the diff. Session UUIDs travel on the CLI only; event payloads pass through the base `_emit`, which runs `redact_session_id` over nested payloads (per_turn.py:347-363). |
| input-validation | pass | `resume_session` gates ids through `_is_valid_session_id` (str, non-empty, printable, ≤512; backends/__init__.py:87-94). JSONL decoder rejects non-JSON lines and non-dict objects; every event field is type-checked (`isinstance`) before use in `_complete_turn`/`is_progress_event`. |
| injection | pass | The shell command is assembled as a `parts` list and emitted via `shlex.join(parts)` (copilot.py:130) — prompt, model, and `--add-dir` roots are all shell-quoted; nothing is interpolated into a shell template. |
| xss | n/a | No HTML/UI output surface; backend emits structured events only. |
| csrf | n/a | No HTTP endpoints touched by this change. |
| authz | pass | Filesystem boundary preserved: writable roots become repeated `--add-dir` flags only; never `--allow-all`/`--yolo` (asserted by `test_copilot_allow_all_tools_is_enabled` and `test_writable_roots_become_add_dir_flags`). `--allow-all-tools` grants tool execution, not directory access. |
| rate-limit | pass | `_is_genuine_copilot_exhaustion` separates genuine quota/credit exhaustion (→ `EVENT_PROVIDER_USAGE_EXHAUSTED` + `ProviderCapacityError`, blocking the pool) from transient RPM/TPM throttling (→ plain `TurnFailed`, retryable); covered by §27 tests. |

No secrets, injection vectors, or authz regressions found in the reviewed diff.

## Re-verification — pass 2 (2026-08-19, HEAD c01fc45)

Post-fix delta (`git diff 4b5ba53..HEAD`) touches only
`tests/test_copilot_backend.py` (-4 lines of imports/local variable) and
evidence docs — no security-relevant content in the delta, and the audited
source file is byte-identical. All seven rows above stand unchanged for HEAD.
