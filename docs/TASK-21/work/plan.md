# TASK-21 Work Plan: Copilot Backend Phase 4 (UI, Docs, Tests)

## Goal
Complete Phase 4 of the GitHub Copilot backend implementation:
1. Expose `copilot` everywhere `SUPPORTED_AGENT_KINDS` is consumed, including the web UI chat agent selector labels.
2. Add `_summarize_copilot_frame` in `src/symphony/chat.py` normalizing `assistant.message` (authoritative), `assistant.message_delta`, `tool.*`, `session.error`, and ignoring ephemeral/lifecycle frames.
3. Update repo-wide documentation (`README.md`, `README.ko.md`, `pyproject.toml`, `docs/features/agent-profiles.md`, `WORKFLOW.file.example.md`, `WORKFLOW.example.md`, etc.) to replace hardcoded agent counts with "multiple coding-agent backends" and include `copilot`.
4. Complete the test suite with §29 Doctor/API/UI tests, fix `test_copilot_usage_probe_fails_open` in `tests/test_backend_usage_probes.py`, and ensure the full `pytest` suite passes cleanly.

## Concrete Steps
1. **Chat Frame Summarizer**:
   - In `src/symphony/chat.py`:
     - Implement `_summarize_copilot_frame(payload)`.
     - Route `agent_kind == "copilot"` in `_summarize_frame`.
   - In `src/symphony/web/static/app.js`:
     - Add `copilot: "GitHub Copilot"` to `CHAT_AGENT_LABELS`.
2. **Docs & Examples**:
   - `pyproject.toml`: Update project description to include GitHub Copilot CLI without hardcoded counts.
   - `README.md` & `README.ko.md`: Update header taglines, agent lists, install tables, lifecycle design notes, and fail-open invariant lists.
   - `docs/features/agent-profiles.md`: Add `copilot` to backend kinds, profile fields table (`model`, `reasoning_effort`, `command`, `resume_across_turns`, timeouts, `usage_pool`), and fail-open invariant notes.
   - `WORKFLOW.file.example.md` & `WORKFLOW.example.md`: Add `copilot:` configuration blocks and update `agent.kind` comment hints.
3. **Tests**:
   - `tests/test_backend_usage_probes.py`: Ensure `test_copilot_usage_probe_fails_open` uses a non-existent command to properly test fail-open without relying on host binary execution.
   - `tests/test_copilot_backend.py`:
     - Add §29 Doctor/API/UI tests (`test_doctor_detects_copilot_binary`, `test_doctor_handles_copilot_auth_independently_from_pi`, `test_workflow_api_exposes_copilot_supported_kind`, `test_chat_agent_selector_contains_copilot`).
     - Add chat frame summarization tests (`_summarize_copilot_frame` for message, message_delta, tool calls, session.error, ephemeral events).
   - `tests/test_chat.py`: Add test verifying Copilot frame dispatching and streaming in ChatManager.
4. **Verification**:
   - Run `pytest` on all modified test modules and full test suite.
   - Run `ruff` and `pyright` checks.
