# TASK-21 Work Notes: Phase 4 Copilot Backend Integration

## Overview
Phase 4 finishes the Copilot CLI backend integration by connecting UI/API/chat presentation layers, removing hardcoded backend count references across documentation, and completing test coverage for §29 Doctor/API/UI and event summarization.

## Key Changes
1. `src/symphony/chat.py`:
   - Added `_summarize_copilot_frame`:
     - `assistant.message`: extracts `data.content` as authoritative `agent_message`.
     - `assistant.message_delta`: extracts `data.deltaContent` as streaming `agent_delta`.
     - `tool.*`: extracts tool name and input/arguments/result details as `tool_activity`.
     - `session.error`: extracts `data.message` or error payload as `tool_activity` error.
     - Ephemeral/setup/turn lifecycle frames: safely ignored (returning empty list).
   - Hooked `agent_kind == "copilot"` into `_summarize_frame`.
2. `src/symphony/web/static/app.js`:
   - Added `copilot: "GitHub Copilot"` to `CHAT_AGENT_LABELS`.
3. `tests/test_backend_usage_probes.py`:
   - Updated `test_copilot_usage_probe_fails_open` to use `command="nonexistent-copilot-bin"` to ensure test does not invoke host binary.
4. Repo-wide Documentation:
   - `README.md`, `README.ko.md`, `pyproject.toml`, `docs/features/agent-profiles.md`, `WORKFLOW.file.example.md`, `WORKFLOW.example.md`, `WORKFLOW.md`, `skills/symphony-skill/reference/workflow-config.md`.
5. Test Suite:
   - Added §29 Doctor, API, UI, and Chat frame summarization tests in `tests/test_copilot_backend.py`.
