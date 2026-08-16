# TASK-8 Verify — Static Diff Review & Security Analysis

Method: full read of `git diff main...HEAD` (8 changed paths, +1114/-27) and
targeted greps over the changed files. Runtime execution was gate-refused
(`qa/runtime-blocked.md`).

## Scope map (ticket vs changed paths)

| Plan section | Changed paths | Orphan? |
|---|---|---|
| §18 docs | `README.md`, `README.ko.md`, `WORKFLOW.file.example.md`, `docs/features/agent-profiles.md` (new) | no |
| §20 acceptance | `tests/test_workflow_agent_profiles_e2e.py` (new, 8 tests) | no |
| wiki write-back | `docs/llm-wiki/INDEX.md`, `docs/llm-wiki/agent-profiles-validation-and-docs.md` (new) | no |
| evidence | `docs/TASK-8/work/details.md` | no |

`WORKFLOW.example.md` was not touched by this branch; its profile docs already
arrived on main via TASK-6 (commit `3c9ac54`), so both example files carry
profile documentation in the merged state.

No web UI files touched — changed paths are `.py`/`.md` only (AC-6).

## Runtime-claims cross-check (docs vs source)

- 8-tier precedence in `README.md`/`agent-profiles.md` matches
  `config.py` `selection_for_state` tiers 1-8 verbatim.
- `PROFILE_FIELDS_BY_KIND` (`constants.py:98`) matches the codex/claude rows.
- Claude `--model` injection claim matches `claude_code.py::_inject_model`.
- §20 mapping asserted by the E2E test matches the plan's expected table.
- Test imports verified against source: `_inject_model`
  (`backends/claude_code.py:75`), `check_agent_profiles` (`cli/doctor.py:171`),
  `build_service_config` (`workflow/builder.py:151`), `parse_workflow_text`
  (`workflow/parser.py:40`), `resolve_agent_config`
  (`workflow/profiles.py:82`), `AgentSelection` (frozen dataclass,
  `config.py:122`), `ServiceConfig.selection_for_state` (`config.py:832`,
  forwards `agent_profiles=self.agent_profiles`).

## Security analysis (per Security Audit row)

- **secrets**: no credential/token handling in the diff; the only grep hits
  for `api_key|password|secret|token|sk-` in changed files are pre-existing
  prose about token-usage accounting (README lines 21-150 area, wiki INDEX).
- **input-validation**: the new test file feeds constant YAML strings to the
  existing parser/builder; profile field allowlisting, ambiguity rejection,
  and doctor checks are Phase 2-4 runtime code on main, unchanged here.
- **injection**: no `shell=True`, `os.system`, `eval(`, `exec(`, or
  `subprocess` anywhere in the changed files (grep, 0 hits in the new test
  file and docs; README "subprocess" hits are pre-existing backend prose).
- **xss**: no HTML in changed files (grep for `<script|innerHTML|onclick`,
  0 hits).
- **csrf / authz / rate-limit**: no web endpoints, authorization, or network
  code in the diff.

## Review findings (severity)

No CRITICAL/HIGH/MEDIUM findings. Two LOW doc defects found during review and
fixed in place (docs-only edits, no source/test impact):

- **LOW-1 (fixed)** — `README.md:214` and `README.ko.md:204` linked the
  reference doc via an absolute `file:///home/symphony/symphony_workspaces/
  TASK-8/...` URL — broken on GitHub and after the worktree is removed. Fixed
  to the repo-conventional relative link `docs/features/agent-profiles.md`.
- **LOW-2 (fixed)** — the supported-fields docs claimed gemini profiles
  reject `resume_across_turns` ("(except gemini)" /
  "gemini 제외"). False at the config layer: `PROFILE_FIELDS_BY_KIND`
  (`constants.py:115-121`) allowlists it for gemini, so
  `builder.py:1040-1053` accepts it. The gemini backend is however incapable
  of resuming (`backends/gemini.py:43-44` deletes `is_continuation`), so the
  field is accepted but inert there. Fixed in `README.md:224`,
  `README.ko.md:210`, `docs/features/agent-profiles.md` table, and
  `docs/TASK-8/work/details.md` to state "accepted but ignored on gemini".

Backward-compatibility check: the legacy `agent.kind`/`stage_kinds` workflow
in the E2E tests exercises the Phase 2 `selection_for_state` tiers unchanged;
no runtime source changed on this branch, so legacy behavior is whatever the
recorded green suite proved (main + this branch's tests).

How to re-run: `git diff main...HEAD` plus the greps listed above
(secrets grep, `shell=True`/eval/exec grep, HTML grep) on an unrestricted
checkout.
