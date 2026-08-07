# Symphony Bloat Audit — Minimalization Baseline

**Date:** 2026-02 (audit) · **Repo:** `symphony-multi-agent` @ v0.16.1 · **src total: 40,505 LOC** (Python, excl. `__pycache__`)

**Target end-state:** chat request → agent decomposes into a DAG of dev-cycle tickets
(research → discovery → plan → adversarial review → implement → test/QA → verify) →
tickets on the Markdown kanban → workers dispatched with automatic git handling → production-quality output.
Everything not serving that loop is candidate bloat.

Classification: **CORE** (the loop), **SUPPORT** (indirect need), **OPTIONAL** (nice-to-have),
**BLOAT** (remove). Import evidence is from `grep` over `src/` (who imports whom), not guesses.

---

## 1. `src/symphony/` inventory

### CORE — ~28,700 LOC (keep)

| Module | LOC | Justification |
|---|---:|---|
| `orchestrator/` | 11,143 | Poll/dispatch/lease loop, run registry, flow store (SQLite), contracts. Heart of the loop. `core.py` alone is 7,054 LOC and *contains* embedded bloat hooks (CI scheduler `_maybe_schedule_continuous_improvement`, `_maybe_run_wiki_sweep`, ~300–400 LOC excisable). |
| `flow/` | 4,490 | The governed **DAG engine inside a ticket** (schema, compiler, executor, agent/shell nodes, retries, artifacts, crash recovery). This *is* the DAG half of the target. |
| `workflow/` | 2,747 | `WORKFLOW.md` parser/config/builder — board states, per-state prompts, backends. Config backbone. |
| `backends/` (core subset) | 2,284 | `__init__.py` (326, `build_backend` + governed contract), `codex.py` (1,032), `claude_code.py` (479), `per_turn.py` (320, shared per-turn CLI runner), `approval_policy.py` (67), `plain_cli.py` (60). Keep the 1–2 backends actually used. |
| `trackers/file.py` + `__init__` + `_retry` | 1,375 | The Markdown kanban tracker — the board itself. |
| `chat.py` | 1,205 | Operator chat sessions against backends. **This is the chat entry point of the target loop.** Note: currently only reachable through `webapi.py` (sole importer) — the minimal build must keep a chat surface (thin API or CLI). |
| `workspace.py` | 1,059 | Per-ticket workspace/worktree manager + lifecycle hooks — how workers get isolated git checkouts. |
| `cli/` (main/flow/board) | 1,506 | Entry points: `symphony run/board/workflow` (`main.py` 448, `flow.py` 782, `board.py` 243). |
| `utils/` git subset | 1,172 | `git_ops` (202), `git_inspect` (277), `git_sandbox` (271), `auto_merge` (422) — the "automatic git handling" requirement. |
| domain + infra singles | 2,805 | `issue.py` (144, Issue model), `ticket_markdown.py` (65), `prompt.py` (594, strict template renderer), `prompt_context.py` (260), `errors.py` (220), `logging.py` (124), `_shell.py` (240, cross-platform bash resolution), `agent.py` (38), `__init__/__main__` (61). |

### SUPPORT — ~1,165 LOC (keep, shrinkable)

| Module | LOC | Justification |
|---|---:|---|
| `service.py` | 986 | Run-state persistence for `symphony service` (pid records, liveness). Needed if service mode stays; imports `cli.doctor` at line 627 (preflight) — that call can be dropped with doctor. |
| `skills.py` | 122 | Skill-body prompt injection for skill-attached issues (used by core + file tracker). Small; keep or fold into prompt assembly. |
| `utils/archive.py` | 57 | Done-ticket archival, used by `orchestrator/core`. |

### OPTIONAL — ~8,170 LOC (removable without touching the loop; small edits at import sites)

| Module | LOC | Justification |
|---|---:|---|
| `tui/` | 2,566 | Textual kanban TUI. Pure view layer; only importer is a lazy import in `cli/main.py`. Removing also drops the `textual` dependency. |
| `webapi.py` | 2,135 | aiohttp REST + web board + git-merge UI + workflow-editing + CI endpoints. **Mixed**: the chat/issue/board endpoint slice (~400–600 LOC) becomes CORE if web chat is the entry surface; the git-merge UI, workflow PUT editors, and CI endpoints are removable. |
| `cli/doctor.py` | 847 | Preflight diagnostics. Useful, not in the loop. Imported lazily by `cli/main` and once by `service.py`. |
| extra backends | 1,042 | `pi.py` (509), `opencode.py` (350), `gemini.py` (113), `kiro.py` (48), `agy.py` (22). Lazy-loaded per `kind` in `build_backend` — deleting = removing `if kind ==` branches. |
| `notifications/` | 409 | Slack notifier. Referenced by `workflow/config`, `workflow/builder`, `orchestrator/helpers` — three small edit sites. |
| `progress_md.py` | 345 | `WORKFLOW-PROGRESS.md` mirror observer; only importer `cli/main.py`. |
| `stats.py` | 317 | JSONL run-stats store. Imported by `core.py`, `tui`, `webapi` — decouple with a no-op or delete call sites. |
| `server.py` | 224 | aiohttp app runner for webapi. Keep a slim version only if web chat survives. |
| `i18n.py` | 173 | TUI label localization + doc-language preamble. `prompt.py` uses only `normalize_language`/`doc_language_preamble` (3 lazy imports); TUI/builder are the real consumers. Shrinks to ~30 LOC or inline. |
| `utils/keep_awake.py` | 114 | macOS caffeinate wrapper; only importer `cli/main.py`. |
| `web/static/` | (HTML/JS, non-py) | Web board frontend (`app.js`, `i18n.js`, `style.css`, `index.html`). Removable with webapi UI; keep only if web chat is the surface. |

### BLOAT — ~2,435 LOC in src (delete)

| Module | LOC | Justification |
|---|---:|---|
| `continuous_improvement.py` | 920 | Idle-board "product readiness heartbeat" — a second, competing improvement loop. Importers: `orchestrator/core.py` (scheduler hook), `webapi.py` (3 CI endpoints), `workflow/config` field. Not part of the ticket dev-cycle. |
| `trackers/linear.py` | 449 | Linear GraphQL tracker. No static importer; only reachable via `kind == "linear"` in `trackers/__init__`. Target uses the file board only. |
| `trackers/jira.py` | 391 | Jira REST tracker. Same dynamic-only reachability. |
| `utils/wiki_sweep.py` | 378 | `docs/llm-wiki` integrity sweep run from `core.py` — repo-documentation hygiene, not the dev loop. |
| `mock_codex.py` | 297 | Mock Codex app-server. **Zero importers in src** (doctor only mentions it in a comment); used by 2 test files and doc examples. Move under `tests/` or delete with its tests. |
| `factory/`, `web/` (py) | 0 | `factory/` is an **empty package dir** (no `.py` files) — dead, delete. `web/` holds only static assets. |

---

## 2. `scripts/`, `tools/`, `docs/`, root files

### `scripts/` (1,064 LOC py + 189 sh)

| File | LOC | Class | Justification |
|---|---:|---|---|
| `git_quality_gate.py` | 150 | SUPPORT | Called by `.githooks/pre-commit` & `pre-push` — repo dev gate, keep. |
| `symphony-setup-worktree.sh` | 189 | SUPPORT | Worktree bootstrap used by the workflow. |
| `smoke_web_api.py` | 208 | OPTIONAL | Web API smoke; drop with webapi (test `test_web_api_smoke_script.py` depends). |
| `check_i18n.py` | 93 | BLOAT | i18n string checker; drop with `i18n.py`. |
| `capture_tui_screenshot.py` | 255 | BLOAT | README screenshot tooling; drop with TUI. |
| `ui_shots.py` | 65 | BLOAT | Same. |
| `static_todo_browser_acceptance.py` | 293 | BLOAT | One-off demo acceptance script (has a mirror test). |
| `scripts/__pycache__/` | — | BLOAT | Committed?—cache dir on disk; delete. |

### `tools/` (~5,900 LOC incl. JS/CSS)

`tools/board-viewer/` — a **second, standalone web board** (server.py 1,566 + 1,741 JS + 1,411 CSS + screenshots + own README). Duplicates `web/static` board. **BLOAT**; delete whole dir (drop `tests/test_board_viewer.py`).

### `docs/` (5.7 MB, 482 tracked files)

| Item | Size | Class |
|---|---|---|
| `docs/SMA-18..SMA-26/`, `REL-066/` | ~3.6 MB | BLOAT — per-ticket work archives of this repo's own development. |
| `docs/changelog/` | 892 KB | BLOAT (history lives in git + CHANGELOG.md). |
| `docs/plans/`, `docs/improvements/`, `docs/qa/`, `docs/dispatch-stability/`, `docs/continuous-improvement/`, `docs/superpowers/`, `docs/llm-wiki/` | ~700 KB | BLOAT/OPTIONAL — internal process artifacts; llm-wiki only matters to `wiki_sweep` (bloat). |
| `docs/symphony-prompts/` (file/linear/workflows) | 76 KB | **CORE** — the per-state worker prompts `WORKFLOW.md` renders. Keep `file/` + `workflows/`; drop `linear/` with the Linear tracker. |
| `docs/spec/`, `architecture.md` | ~80 KB | SUPPORT — keep. |
| `docs/index.html`, screenshots (`tui-screenshot.svg` 228K, `admin-ui-screenshot.png` 84K), `PIPELINE*.md`, `PRD-*.md`, `handoff-*.md` | ~400 KB | BLOAT — marketing/history assets. |

### Root files & dirs

| Item | Class | Note |
|---|---|---|
| `WORKFLOW.md` | CORE | Live config (gitignored). |
| `WORKFLOW.example.md`, `WORKFLOW.file.example.md` | SUPPORT | Keep **one** canonical example; the file-variant duplicate is bloat. |
| `examples/` (demo workflows + demo kanbans, `WORKFLOW.jira.example.md`, `WORKFLOW.smoke.md`) | BLOAT | Demo artifacts; jira example dies with jira tracker. |
| `kanban/` (SMA-*, DEMO-*, QA-*) | — | Gitignored live board of this repo's own dev; wipe for a fresh start. |
| `PLAN.md`, `WORKFLOW-PROGRESS.md` | BLOAT | Old plan; generated mirror (dies with `progress_md.py`). |
| `tui-open.sh`, `tui-open.bat`, `.tui-launcher.command` | BLOAT | TUI launchers; die with TUI. |
| `.bkit/`, `.serena/` (0 B), `.omc/` (76K, `RELEASE_RULE.md` is git-tracked), `.omx/` (1.7M), `.domain-agent/` (8K), `.playwright-mcp/` (148K) | BLOAT | Other agent-harness droppings; only `.omc/RELEASE_RULE.md` is tracked — untrack+delete. |
| `tmp_workspaces/` (432K), `log/` (964K) | BLOAT | Gitignored runtime debris; `rm -rf`. |
| `.mypy_cache/` (14M), `.pytest_cache/`, `.ruff_cache/`, `.coverage` (68K) | BLOAT | Untracked caches; `rm -rf` (all gitignored except ruff/mypy caches — add to .gitignore). |
| `README.md` (49K) + `README.ko.md` (52K), `GEMINI.md`, `CHANGELOG.md` (62K) | OPTIONAL | Huge dual-language README; trim to match minimal product. |
| `.githooks/`, `.github/`, `pyproject.toml`, `AGENTS.md`, `skills/symphony-skill/` | SUPPORT | Keep; `skills/symphony-skill` is the operator routing incl. the **oneshot** (chat→DAG decompose) templates — CORE-adjacent for the target. |

---

## 3. Removable LOC & dependency risks

| Category | src LOC | Test LOC riding along | Risk if removed |
|---|---:|---:|---|
| BLOAT (src) | **~2,435** | ~3,500 (linear/jira/wiki/CI/mock tests) | Low. Edit sites: `core.py` (CI scheduler + wiki hook), `webapi.py` (3 CI routes), `workflow/config` (CI + tracker-kind fields), `trackers/__init__` (2 `if kind` branches). Migration risk: existing `WORKFLOW.md` with `tracker: linear/jira` or `continuous_improvement:` blocks must fail loudly or be ignored. |
| OPTIONAL (src) | **~8,170** | ~8,400 (tui/webapi/doctor/stats/notifications/i18n/chat-api tests) | Medium, localized. `stats` is imported by `core.py` (needs no-op seam); `i18n` by `prompt.py` (keep 2 tiny functions); `notifications` by `workflow/config+builder` (drop config field); extra backends are lazy `if kind` branches; `service.py` calls `doctor` once. **Caveat: killing all of webapi kills `chat.py`'s only surface — carve out a minimal chat+board API (~500 LOC) before deleting the rest.** |
| tools/ + scripts bloat | ~6,600 (incl. JS/CSS) | ~600 | None — standalone. |
| docs bloat | ~5 MB / 400+ files | — | None (verify `wiki_sweep` deleted first, else its `docs/llm-wiki` walks fail-soft anyway). |
| **Total code removable** | **~10,600 src LOC (26%) + ~6,600 tools/scripts + ~12,000 test LOC** | | |

A minimal Symphony (chat entry, DAG flow, file kanban, codex+claude backends, git handling) lands around
**~28–29 K src LOC**, and much less if `orchestrator/core.py` (7,054 LOC) is itself decomposed — it currently
mixes dispatch, phase transitions, CI scheduling, wiki sweeps, archive, auto-merge and stats emission in one file.

## 4. Dead / unreachable code

- `src/symphony/factory/` — empty package directory, no files. **Dead. Delete.**
- `src/symphony/mock_codex.py` — no src importer; test fixture living in the product package.
- `trackers/linear.py`, `trackers/jira.py` — reachable only via config `kind` switch; dead for the file-board target.
- `scripts/static_todo_browser_acceptance.py`, `scripts/capture_tui_screenshot.py`, `scripts/ui_shots.py` — referenced only by their own mirror tests / doc tooling.
- `tools/board-viewer/` — fully standalone duplicate board; nothing in `src/` references it.
- `.bkit/`, `.serena/` — empty dirs.

## 5. Suggested removal order (safe → invasive)

1. **Zero-risk debris:** caches, `tmp_workspaces/`, `log/`, agent-harness dirs, `factory/`, screenshots, `PLAN.md`, tui-open launchers, `tools/board-viewer/`.
2. **Docs archive:** `docs/SMA-*`, `docs/changelog/`, `docs/REL-066`, process dirs; keep `spec/`, `architecture.md`, `symphony-prompts/{file,workflows}`.
3. **Src bloat:** trackers linear/jira (+ examples + prompts/linear + tests), `wiki_sweep`, `continuous_improvement` (+ core/webapi/config hooks), relocate/delete `mock_codex`.
4. **Optional layer, per product decision:** TUI (+ i18n TUI strings + launchers + textual dep), stats, progress_md, notifications, keep_awake, doctor, extra backends — after choosing the chat surface, carve webapi down to chat+board+issue endpoints.
