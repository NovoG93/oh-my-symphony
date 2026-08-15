# TASK-4 Verify — Security Audit static review

Date: 2026-08-15T18:50Z. Scope: full diff of `symphony/TASK-4` HEAD (050d96f)
vs merge-base 4c9e7b1, i.e. `src/symphony/workflow/config.py`,
`constants.py`, `builder.py`, `__init__.py`,
`tests/test_workflow_agent_profiles.py` (950 insertions, 10 files), including
the fix commits that resolve the previous pass's Review Findings (duplicate
guard at builder.py:1011, duplicate test at tests/test_workflow_agent_profiles.py:375,
`graphify-out` symlink removed — `git show HEAD:graphify-out` is fatal, and
`git diff 4c9e7b1 HEAD` shows no tree entry for it). Runtime testing is
blocked by the workspace permission policy — see `runtime-blocked.md`; every
pass below rests on static review plus the pytest-cache record of the
2026-08-15T18:42Z full-suite run.

## Row evidence

### secrets
Diff reviewed in full (950 insertions). No literal secrets, tokens, keys, or
credential handling added. `AgentProfileConfig` and `PROFILE_FIELDS_BY_KIND`
carry only field names; error messages echo user-supplied profile names, not
credentials. No env-var reads added. The removed `graphify-out` symlink was
the only machine-specific path artifact and is gone from the tree.

### input-validation
The diff's purpose is input validation. Verified statically in
`builder.py:989` (`_validated_agent_profiles`), `builder.py:1120`
(`_validated_stage_profiles`), `builder.py:1164`
(`_validated_default_profile`): mapping-type checks, non-empty stripped
names, duplicate-name guard (`if name in out`, builder.py:1011), kind
allowlist via `SUPPORTED_AGENT_KINDS`, per-kind field allowlist via
`PROFILE_FIELDS_BY_KIND`, string checks on model/reasoning_effort/command,
positive-int checks on the three timeouts (bool explicitly excluded), bool
check on resume_across_turns. No user-controlled value reaches any parser,
filesystem, or process API in this phase; values only land in frozen
dataclasses.

### injection
No new execution path: the new fields are stored in `ServiceConfig` /
`AgentConfig` and are not read by any dispatch, backend, or shell code in
this phase (grep over `src/` this turn: reads of `agent_profiles` /
`stage_profiles` / `default_profile` exist only in config/builder/
constants/__init__). Error messages use f-string formatting of values into
text only. The `command` field mirrors the existing operator-trusted
`*.command` config fields and is not executed here.

### xss
No web/UI output added; no HTML or template rendering touched. n/a.

### csrf
No web routes, forms, or state-changing HTTP endpoints touched. n/a.

### authz
No authentication/authorization code touched; config parsing runs in the
operator's process with the same trust model as the rest of WORKFLOW.md. n/a.

### rate-limit
No network I/O added. n/a.

## Runtime column

`pytest`, `python3 -m pytest`, and `git merge-tree --write-tree` were each
re-attempted on 2026-08-15T18:50Z and denied again by the workspace
permission policy — see `qa/runtime-blocked.md` for the full table. Runtime
proof is limited to the pytest-cache record of the 18:42Z full-suite run:
15/16 profile tests executed with zero profile-test failures (lastfailed has
a single, branch-unrelated entry — see `qa/qa-evidence.md`).
