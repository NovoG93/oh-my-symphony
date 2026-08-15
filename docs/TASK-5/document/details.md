# TASK-5 Document — pass notes and verification manifest (2026-08-15T20:08Z)

Document stage for TASK-5 (Phase-2 runtime resolution). No source/test
edits — wiki + ticket narrative only. All commands read-only.

## Brief vs reality check

- `AgentSelection` frozen dataclass + `selection_for_state` with the 8-tier
  precedence docstring (plan §3) — confirmed at `src/symphony/workflow/config.py:123`
  and `:310`; `dispatch_profile`/`dispatch_kind` params exist for Phase 3.
- `resolve_agent_config` — confirmed at `src/symphony/workflow/profiles.py:82`:
  unknown profile -> `ConfigValidationError`; kind mismatch ->
  `ConfigValidationError`; overlay = `dataclasses.replace(base, **overrides)`
  over non-null fields allowed by `PROFILE_FIELDS_BY_KIND`; profile-less
  selection returns base unchanged (command intact by construction).
- `BackendInit.selection` / `resolved_backend_config` — confirmed at
  `src/symphony/backends/__init__.py:126-136`, `__post_init__` defaults both.
- F-01 per-transition re-resolution from `base_cfg` — confirmed at
  `src/symphony/orchestrator/core.py:6989`.
- Ambiguity guard raises `ConfigValidationError` — confirmed in
  `config.py` resolver and `orchestrator/helpers.py:_config_for_issue_agent`.
- Exports present in `src/symphony/workflow/__init__.py` (AgentSelection,
  ResolvedAgentConfig, resolve_agent_config, selection_for_state).
- Test file has 23 test defs, names map 1:1 onto the AC list incl. the
  `_run_agent_attempt` lifecycle test.

## Tip drift note (not a defect)

The card's `## Merge Status` cites tip `390edf2`; HEAD has since moved to
`21d19b0` (wip 20:05:48Z), a docs/TASK-5-only commit (6 evidence files).
Re-ran the disjoint-set check against the new tip:

- `git diff 6d75be5 HEAD --stat` -> 27 files (src, tests, docs/TASK-5).
- `git diff 6d75be5 main --stat` -> 2 files (WORKFLOW.md,
  docs/symphony-prompts/file/base.md).
- Intersection: empty -> the preflight conclusion is unchanged at the new tip.

## Document-stage manifest

| # | Command (read-only) | Exit | Proves |
| - | ------------------- | ---- | ------ |
| 1 | `git status --short` / `git rev-parse HEAD` | 0 / clean @ 21d19b0 | tree clean at the reviewed tip |
| 2 | `git show HEAD:graphify-out` | 128 (fatal) | rewind fix holds at tip: symlink untracked |
| 3 | `git ls-files \| grep -c graphify` | 0 matches | no graphify path tracked anywhere |
| 4 | `git diff 6d75be5 HEAD --stat` | 27 files | branch-side change set at new tip |
| 5 | `git diff 6d75be5 main --stat` | 2 files | main-side set; disjoint -> merge safe |
| 6 | `grep -n` resolver/overlay/BackendInit/F-01 anchors | 0 | implementation matches every cited claim |
| 7 | `grep -c "def test_" tests/test_workflow_agent_profiles_runtime.py` | 23 | full AC test set present |

## Wiki write-back (this stage)

- `docs/llm-wiki/agent-profile-resolution.md` — created (Phase-2 topic page
  + decision log).
- `docs/llm-wiki/agent-profile-config.md` — phase-boundary note updated to
  point at the new Phase-2 page.
- `docs/llm-wiki/INDEX.md` — new row `agent-profile-resolution`.
- User-facing docs (README, CHANGELOG, WORKFLOW.md, config/policy refs):
  not touched by this change — no updates needed.

## Carried forward for Phase 3+ (from qa/ + verify/)

- Stall reconciler (`_stall_timeout_ms_for_entry`, core.py:10110) reads
  base kind timeouts, not profile-overlaid `stall_timeout_ms` — follow-up.
- CLI profile flags (Phase 3/4) and UI (Phase 5) untouched by design.
