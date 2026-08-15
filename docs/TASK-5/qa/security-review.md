# TASK-5 Security Review (static, Verify re-pass 2026-08-15T20:05Z)

Scope: full branch diff `6d75be5..390edf2` (merge-base of `symphony/TASK-5` with
`main` to final branch tip). This re-pass re-validates the 2026-08-15T19:46Z
review after the rewind fix (removal of the tracked `graphify-out` symlink in
wip commit 390edf2). No source file changed between the two passes — the only
tree delta is the symlink deletion — so the earlier findings stand; they are
re-verified here against the final commit.

Commands run (all from `/home/symphony/symphony_workspaces/TASK-5`, read-only verbs):

1. `git diff 6d75be5957430b22aaa65e1e717fd2032b21588e HEAD --stat`
   -> 25 files: 21 implementation files + `tests/test_workflow_agent_profiles_runtime.py`
   + 4 docs/TASK-5 evidence files. No `graphify-out` entry (rewind fix verified:
   `git show HEAD:graphify-out` -> fatal "does not exist in 'HEAD'").
2. `grep -nE "password|secret|api_key|token|shell=True|os.system|eval\(|exec\("`
   over every changed source file. All hits are pre-existing code (TrackerConfig
   `api_key` plumbing, chat token-budget accounting, `hmac.compare_digest`
   confirmation hashes, builder `resolve_var_indirection` for tracker API keys).
   The TASK-5 diff itself adds no credential literal, no `shell=True`, no
   `os.system`, no eval/exec. Test file contains only `api_key=""`
   (empty-string placeholder in the `TrackerConfig` helper,
   `tests/test_workflow_agent_profiles_runtime.py:95`).
3. Manual review of every hunk in `src/symphony/workflow/config.py`,
   `src/symphony/workflow/profiles.py`, `src/symphony/workflow/builder.py`,
   `src/symphony/workflow/__init__.py`, `src/symphony/backends/*`,
   `src/symphony/orchestrator/core.py`, `src/symphony/orchestrator/helpers.py`,
   `src/symphony/trackers/file.py`, `src/symphony/issue.py`, `src/symphony/chat.py`.

Findings per row:

- **secrets**: PASS. No new secret/env credential handling. `Issue.to_dict` /
  frontmatter parsing add `agent_profile` (a plain string) only.
- **input-validation**: PASS. Ticket frontmatter overrides are validated:
  ambiguous `agent_kind`+`agent_profile` raises `ConfigValidationError`
  (`config.py` `selection_for_state`, and `orchestrator/helpers.py`
  `_config_for_issue_agent`); unknown profile names raise
  `ConfigValidationError` (`profiles.py` `resolve_agent_config`,
  `config.py` `selection_for_state` tiers 1/3/5/7); unsupported backend kinds
  raise in `profiles.py::_get_backend_config`; profile kind mismatch with the
  selection raises in `resolve_agent_config`. `ticket_kind` is stripped and
  lowercased before use.
- **injection**: PASS. No new subprocess or shell construction. The overlay
  copies only values from `AgentProfileConfig` (sourced from the operator's
  WORKFLOW.md, the same trust domain as the existing global `command`) onto the
  backend config dataclass via `dataclasses.replace`. No string interpolation
  of untrusted input into commands. Backend CLI flag injection is explicitly
  out of Phase-2 scope (Phase 3).
- **xss**: N/A. Ticket scope explicitly excludes UI changes; no HTML/TUI
  rendering touched.
- **csrf**: N/A. No web endpoints or cookie/session handling changed.
- **authz**: N/A. No authentication/authorization paths changed.
- **rate-limit**: N/A. Resolution is purely local; no new external calls or
  rate-limited interfaces introduced.

How to re-run: execute the greps in item 2 from the workspace root and review
the `git diff 6d75be5..HEAD` hunks listed in item 3.
