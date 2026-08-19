# TASK-21 Verify — static acceptance checks (2026-08-19)

**What**: Command outputs for AC1 (lists/selector), AC2 (summarizer), AC3 (no hardcoded counts), run against the committed tree (`HEAD` = `e8dc7b6`).
**Why**: Static checks are the authoritative proof for these ACs; the matching tests also ran green in the cached session (`qa/pytest-cache-evidence.md`).
**As-Is -> To-Be**: Unchecked ACs -> command-backed pass evidence.

## AC1 — Copilot in supported backend lists and chat agent selector

- `grep -n "copilot: 'GitHub Copilot'" src/symphony/web/static/app.js` → `3443: copilot: 'GitHub Copilot',`
- `grep -n "SUPPORTED_AGENT_KINDS" src/symphony/workflow/constants.py` → line 95: `SUPPORTED_AGENT_KINDS = {"agy", "codex", "claude", "copilot", "gemini", "kiro", "opencode", "pi", "prime-agent"}`
- Consumers all draw from the shared constant (no per-site lists to patch): `src/symphony/webapi.py:747` (`agent_kinds` API), `tui/app.py:697,772`, `workflow/builder.py:333,997,1159`, `workflow/profiles.py:76`, `workflow/mutate.py:475`, `cli/board.py:522,568`, `chat.py:711,772,1036`. The web UI selector resolves options from `listing.supported_agent_kinds` with `CHAT_AGENT_LABELS` (`app.js:3832-3834`), so the label added at 3443 renders for Copilot.
- Runtime tests covering this AC were collected and zero-failure in the cached session: `test_chat_agent_selector_contains_copilot`, `test_workflow_api_exposes_copilot_supported_kind`.

## AC2 — `_summarize_copilot_frame` normalizes copilot events

- `grep -n "_summarize_copilot_frame\|agent_kind == \"copilot\"" src/symphony/chat.py` →
  - `1781-1782`: dispatcher branch `if agent_kind == "copilot": return _summarize_copilot_frame(payload)`
  - `1885`: `def _summarize_copilot_frame(`
- Function body handles: `assistant.message` (authoritative, `data.content` → `agent_message`), `assistant.message_delta` (`data.deltaContent` → `agent_delta`), `tool.*`/`tool_`/`tool` (→ `tool_activity` with previewed detail), `session.error` (→ `tool_activity` error), everything else → `[]`. Type-guards `isinstance(kind, str)` / `isinstance(data, dict)` before key access.
- Tests: 6 `test_summarize_copilot_frame*` + `test_summarize_frame_dispatches_copilot` in the cached nodeids (zero failures).

## AC3 — docs no longer hardcode an agent count

- `git grep -in "eight ai" -- README.md docs/features/agent-profiles.md pyproject.toml` → **0 matches**
- Broad sweep `grep -rn "여덟\|eight agents\|eight different\|Eight AI\|8 backend" README.md README.ko.md docs/index.html docs/features/agent-profiles.md pyproject.toml WORKFLOW.example.md WORKFLOW.file.example.md skills/symphony-skill/reference/workflow-config.md` → **0 matches**
- Sweep for any `eight`/`Eight`/`EIGHT`/`여덟` in the five primary docs → 1 hit only: "lightweight" (false positive) at `docs/features/agent-profiles.md:188`.
- Copilot present in every backend list: `pyproject.toml` description, `README.md`/`README.ko.md` tagline+lists+tables+lifecycle notes, `docs/index.html` hero/og/tables/i18n (en+ko), `docs/features/agent-profiles.md:15,89,151,175`, `WORKFLOW.example.md`/`WORKFLOW.file.example.md` `copilot:` blocks + kind comments, `skills/symphony-skill/reference/workflow-config.md:35`, `src/symphony/__init__.py` docstring.

## AC4 — full suite

Covered by `qa/pytest-cache-evidence.md` (2636 collected, zero failures) + `qa/runtime-blocked.md` (live re-run refused).

## How to re-run

```bash
grep -n "copilot: 'GitHub Copilot'" src/symphony/web/static/app.js
grep -n "_summarize_copilot_frame" src/symphony/chat.py
git grep -in "eight ai" -- README.md docs/features/agent-profiles.md pyproject.toml   # expect 0
.venv/bin/pytest -q   # unrestricted environments only; expect 2627 passed, 9 skipped
```
