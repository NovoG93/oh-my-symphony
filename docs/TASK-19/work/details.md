# TASK-19: CopilotBackend (JSONL agent execution) Implementation Notes

## Architecture Overview
`CopilotBackend` implements the `PerTurnCliBackend` contract for the GitHub Copilot CLI (v1.0.80+):
- **Invocation**: One subprocess per turn via `copilot --output-format=json --no-ask-user --allow-all-tools [-p <prompt>]`.
- **Command Construction**: Returns `shlex.join(parts)` string keyword-only with `is_continuation`.
- **Session Lifecycle**: UUID generation on first turn, session reuse across continuation turns when `resume_across_turns` is true, explicit `resume_session(session_id)` with validation.
- **Permission Boundary**: Filesystem containment via repeated `--add-dir <root>` arguments derived from `git_roots_outside(cwd, workspace_root)`. Never `--allow-all` or `--yolo`.
- **JSONL Parsing**: Line-by-line streaming parser extracting `assistant.message.data.content` as the authoritative response, `result` for completion (`exitCode`, `sessionId`), `session.error` for failures, and `assistant.message.data.outputTokens` for token telemetry. Malformed and unknown events are tolerated without crashing.
- **Provider Exhaustion**: `_is_genuine_copilot_exhaustion` distinguishes quota/credit limits from transient RPM/TPM rate limits, emitting `EVENT_PROVIDER_USAGE_EXHAUSTED` and raising `ProviderCapacityError`.

## Acceptance Mapping
| Test Category | Test Function | Verified Behavior |
|---|---|---|
| §23 Command | `test_copilot_prompt_is_passed_with_p_flag` | `-p` flag carries prompt; stdin is `None` |
| §23 Command | `test_copilot_json_output_is_enabled` | `--output-format=json` present in command |
| §23 Command | `test_copilot_model_is_forwarded` | `--model <model>` forwarded |
| §23 Command | `test_copilot_reasoning_effort_is_forwarded` | `--reasoning-effort <effort>` forwarded |
| §23 Command | `test_copilot_no_ask_user_is_enabled` | `--no-ask-user` present |
| §23 Command | `test_copilot_allow_all_tools_is_enabled` | `--allow-all-tools` present; no `--allow-all` |
| §23 Command | `test_writable_roots_become_add_dir_flags` | External git roots become `--add-dir` flags |
| §24 Sessions | `test_copilot_first_session_gets_uuid` | UUID generated on first turn |
| §24 Sessions | `test_consecutive_turns_reuse_session_id` | Same UUID reused on turn 2 |
| §24 Sessions | `test_resume_session_uses_existing_uuid` | Custom UUID preserved and passed |
| §24 Sessions | `test_invalid_resume_session_uuid_is_rejected` | Empty/whitespace/NUL rejected |
| §24 Sessions | `test_resume_across_turns_false_creates_new_session` | New UUID generated per turn |
| §25 JSONL | `test_assistant_message_becomes_final_output` | `assistant.message` content extracted |
| §25 JSONL | `test_malformed_json_line_does_not_crash_worker` | Non-JSON lines ignored safely |
| §25 JSONL | `test_unknown_event_is_tolerated` | Unknown event types ignored |
| §25 JSONL | `test_session_error_fails_turn` | `session.error` raises `TurnFailed` |
| §25 JSONL | `test_final_message_is_not_duplicated_from_deltas` | `message_delta` doesn't duplicate content |
| §27 Capacity | `test_genuine_copilot_credit_exhaustion_emits_provider_usage_exhausted` | Quota triggers `ProviderCapacityError` |
| §27 Capacity | `test_generic_rate_limit_does_not_mark_plan_exhausted` | RPM 429 raises regular `TurnFailed` |

## Rewind Fix (Verify Review Finding)
- Removed unused imports `json`, `shlex`, and `EVENT_SESSION_STARTED` from [test_copilot_backend.py](file:///home/symphony/symphony_workspaces/TASK-19/tests/test_copilot_backend.py#L1-L25).
- Removed unused `commands` local variable in `test_copilot_run_turn_end_to_end` in [test_copilot_backend.py](file:///home/symphony/symphony_workspaces/TASK-19/tests/test_copilot_backend.py#L530-L555).
- Verified `ruff check` passes cleanly on `src/` and `tests/test_copilot_backend.py` (0 errors).
- Verified `pyright` passes with 0 errors on `src/` and `tests/test_copilot_backend.py`.
- Verified 32/32 tests pass in `tests/test_copilot_backend.py` and 2604 tests pass in the full suite.

