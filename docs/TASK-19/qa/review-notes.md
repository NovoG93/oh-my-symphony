# TASK-19 Verify — diff review notes (2026-08-19)

Branch `symphony/TASK-19` = develop (54002aa) + 1 commit (4b5ba53). Changed
files: `src/symphony/backends/copilot.py` (+40), `tests/test_copilot_backend.py`
(+334/-26), `docs/TASK-19/work/details.md` (+33). All in ticket scope.

## Conformance vs ticket / plan §6–9

- `_command_for_turn(self, *, prompt, is_continuation) -> str` — keyword-only,
  returns `shlex.join(parts)`, exactly plan §6's signature. Base flags
  `--output-format=json --no-ask-user --allow-all-tools`, conditional
  `--model`/`--reasoning-effort`, always `--session-id <uuid>` (generated on
  first turn, reused when `resume_across_turns` is true; fresh per turn when
  false), `--add-dir` per `git_roots_outside` root, then `-p <prompt>`.
  `copilot.py:100-130`.
- `_stdin_payload` returns `None` (prompt travels via `-p`). `copilot.py:96-98`.
- JSONL: `_decode_events` parses line-by-line, skips non-JSON lines and
  non-dict objects without raising (`copilot.py:224-236`); `_complete_turn`
  treats `assistant.message.data.content` as the authoritative final message,
  `result.sessionId`/`result.exitCode` as the completion signal,
  `session.error` as failure; unknown event types are ignored
  (`copilot.py:132-222`). `message_delta` is not used for the final text, so
  its `deltaContent` is never double-counted (covered by
  `test_final_message_is_not_duplicated_from_deltas`).
- Session: `resume_session` validates via shared `_is_valid_session_id`
  (str, non-empty printable, ≤512 chars — `backends/__init__.py:87-94`) and
  raises `TurnFailed` if the CLI's `result.sessionId` mismatches or never
  confirms the recovered session (`copilot.py:85-94,166-188`). Note: the
  shared helper validates "safe to forward" rather than strict UUID syntax —
  deliberate house pattern shared with claude_code/opencode/pi; plan §8 says
  "validate UUID format", tests pin empty/whitespace/NUL/oversized rejection.
- Permissions: `--add-dir` per writable root; no `--allow-all`/`--yolo`
  anywhere (`test_copilot_allow_all_tools_is_enabled` asserts absence).
- Telemetry: `assistant.message.data.outputTokens` accumulated into
  `_latest_usage["output_tokens"]/["total_tokens"]`; no `premiumRequests`/
  `totalNanoAiu` synthesis (`copilot.py:148-151`); genuine quota exhaustion
  (excl. RPM/TPM) emits `EVENT_PROVIDER_USAGE_EXHAUSTED` and raises
  `ProviderCapacityError` (`copilot.py:190-197,239-263`).

Plan §23–25 test list: every named test exists in the committed file
(`tests/test_copilot_backend.py`), 32 tests total, plus the §27 capacity pair.
`TestCopilotBackendContract` is wired into `tests/test_backend_contract.py:396`
and `ALL_KINDS` includes `copilot`.

## Minor observations (no action required)

- `_resume_on_next_turn` is written but never read in copilot.py — dead state
  mirroring sibling backends; harmless.
- `is_continuation` is accepted but unused in `_command_for_turn` — same
  interface-wide pattern as gemini.py (which `del`s it); not flagged by the
  configured lint rules (no ARG rules selected).
- `response = final_message or stdout_text` falls back to raw JSONL when no
  `assistant.message` arrives — robustness fallback, not a defect.

## Defect found (MEDIUM)

`tests/test_copilot_backend.py` imports three names it never uses, all added
by commit 4b5ba53 (diff `@@ -1,20 +1,29 @@`):

- line 5 `import json` — no `json.` reference anywhere in the file
- line 7 `import shlex` — no `shlex.` reference anywhere in the file
- line 15 `EVENT_SESSION_STARTED` — only occurrence is the import line

Ruff's selected rules (pyproject.toml: `select = ["E4","E7","E9","F"]`; only
`**/__init__.py` ignores F401) flag all three as **F401 unused imports**.
The repo's canonical lint gate includes tests —
`python -m ruff check src tests` (CONTRIBUTING.md:30, CI `.github/workflows/tests.yml:40`)
— so the branch is red on the CI lint job until the three lines are removed.
`src/symphony/backends/copilot.py` itself is import-clean (every imported
name referenced), so `ruff check src` alone passes — which is why the
Implementation stage's narrower claim did not catch this.

Requested fix (scope: only the import block of
`tests/test_copilot_backend.py`): delete `import json` (line 5), `import shlex`
(line 7), and `EVENT_SESSION_STARTED,` (line 15); re-run
`.venv/bin/python -m ruff check src tests` to confirm 0 errors.

## Re-review — pass 2 (2026-08-19, post-fix HEAD c01fc45)

Fix delta confirmed: `git diff 4b5ba53..HEAD` touches only
`tests/test_copilot_backend.py` (-4 lines: the 3 unused imports plus the
unused `commands` local in `test_copilot_run_turn_end_to_end`) and evidence
docs. Static walk of the final file state:

- `grep -nE "json\.|shlex\." tests/test_copilot_backend.py` -> empty;
  `grep -n EVENT_SESSION_STARTED tests/test_copilot_backend.py` -> empty.
- Every remaining import in both changed files checked name-by-name as used
  (no F401 candidates); the deleted `commands` local removes the F841
  candidate. The lint finding is statically closed.
- `src/symphony/backends/copilot.py` is byte-identical to the commit audited
  in pass 1 (`git diff 4b5ba53..HEAD -- src/` empty) — pass-1 conformance
  and security analysis carries over unchanged.
- The extra `commands` removal is same-root-cause (F841, same lint gate),
  same file, and was declared in the fix-pass Plan section — in scope.
- Minor observations from pass 1 unchanged, non-blocking.

Verdict: MEDIUM finding resolved; no CRITICAL/HIGH/MEDIUM issues remain.
Live `ruff check` re-run is still denied (`qa/runtime-blocked.md`); the
F401/F841 walk above is the static substitute.
