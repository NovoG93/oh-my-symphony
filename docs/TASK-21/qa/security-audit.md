# TASK-21 Verify — Security Audit backing evidence (2026-08-19)

**What**: Static security checks over the 16-file branch delta (`git diff --name-only develop..HEAD`).
**Why**: Backs the 7-row `## Security Audit` table on the ticket with durable, re-runnable checks.
**As-Is -> To-Be**: Unaudited Phase 4 delta -> per-category evidence.

Delta scope: 7 doc files (README.md, README.ko.md, docs/index.html, docs/features/agent-profiles.md, pyproject.toml, WORKFLOW.example.md, WORKFLOW.file.example.md, skills/symphony-skill/reference/workflow-config.md), `src/symphony/__init__.py` (docstring), `src/symphony/chat.py` (summarizer), `src/symphony/web/static/app.js` (1 label line), 3 test files.

## Checks

1. **secrets** — the only new secret-adjacent code is test fixtures + `check_copilot_auth` (pre-existing from Phase 3, unchanged in this diff). Verified: `src/symphony/cli/doctor.py:410-433` returns only `f"{env_var} present"` / config-path strings, never token values; `tests/test_copilot_backend.py` asserts `"ghp_secret_token_123" not in result.message`. The new tests introduce no real credentials.
2. **input-validation** — `_summarize_copilot_frame` (chat.py:1885) type-guards every access: `isinstance(kind, str)` gate, `isinstance(data, dict)` before `data.get(...)`, string coercion via `str()`; any malformed frame falls through to `[]`.
3. **injection** — the summarizer performs no exec/shell/subprocess/eval; tool payloads are only `json.dumps`-previewed into transcript tuples (`_preview`, truncated). No format-string or template sink. app.js change is a static label constant.
4. **xss** — app.js:3443 adds a constant string to `CHAT_AGENT_LABELS`; the selector renders it via the existing `el('option', {value: kind}, CHAT_AGENT_LABELS[kind] || kind)` path (app.js:3834) — no user-controlled input in the new code path.
5. **csrf** — no new state-mutating endpoint or fetch call added; delta touches no webapi routes.
6. **authz** — `_summarize_copilot_frame` is an internal transcript helper; no new authorization surface, no privilege change.
7. **rate-limit** — no rate-limit/usage code touched; `tests/test_backend_usage_probes.py` only pins the fail-open probe to a nonexistent binary (`command="nonexistent-copilot-bin"`), exercising the existing fail-open invariant.

## How to re-run

```bash
git diff develop...HEAD --stat                       # 16 files, scope above
sed -n 410,433p src/symphony/cli/doctor.py           # secrets check
sed -n 1885,1947p src/symphony/chat.py               # input-validation / injection check
git diff develop...HEAD -- src/symphony/web/static/app.js src/symphony/webapi.py   # xss/csrf check
```
