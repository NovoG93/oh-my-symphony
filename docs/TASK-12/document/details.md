# TASK-12 Document — pass notes and verification manifest (2026-08-17)

Document stage for TASK-12 (usage-aware agent profiles, Stage 1). No
source/test edits — wiki, user-facing docs, and ticket narrative only.
All commands read-only.

## Brief vs reality check

- AC1 `UsagePoolConfig` frozen dataclass (`source: str`, `caps: dict[str, float]`)
  — confirmed `src/symphony/workflow/config.py:123-128`; exported from
  `symphony.workflow` (`__init__.py:110,153`).
- AC2 `ServiceConfig.usage_pools` with `field(default_factory=dict)` —
  confirmed `config.py:775-776`; `_validated_usage_pools(None) -> {}`
  (`builder.py:1002-1003`) keeps legacy configs loading.
- AC3 `AgentProfileConfig.usage_pool: str | None = None` — confirmed
  `config.py:120`; `"usage_pool"` allowlisted for all 8 kinds
  (`constants.py:106-163`).
- AC4 builder validation — `_validated_usage_pools` (`builder.py:996-1081`):
  mapping required; pool names non-empty/unique after strip; entry keys
  limited to `source`/`caps`; `source` required non-empty string; `caps`
  required mapping with `0 < v <= 100` (bool explicitly rejected; NaN/inf
  fail the float compare); arbitrary window names accepted.
- AC5 unknown pool reference rejected — `builder.py:1203-1218`; sole caller
  passes `usage_pools` (`builder.py:301-304`) so the check is always active
  at load.
- AC6 `usage.py` — `UsageWindow`, `ProviderUsageSnapshot` (authoritative
  flag, default True), `UsageProbe` runtime-checkable protocol,
  `USAGE_PROBES` registry + `get_usage_probe` -> `None` (fail open). Read in
  full: `src/symphony/backends/usage.py:1-46`.
- AC7 Stage 6.1 scenarios — test names verified 1:1: `test_usage_limits.py`
  20 defs incl. 11-case invalid-percent parametrize = 30; 16 defs in
  `test_workflow_agent_profiles.py`. The committed parametrize list uses
  YAML string `"80"` (nodeid `["80"]`), matching the pytest-cache claim that
  the recorded lastfailed `[80]` is a stale removed id.
- Merge topology holds at the current tip: HEAD `1e82dca` is a wip commit on
  top of develop `764ec47`; `git merge-base develop HEAD` = `764ec47` =
  develop tip -> fast-forward, conflict-free. The Verify-reviewed tip
  (`d76191e`) differs from HEAD only by the 8 qa evidence files
  (1046 insertions, no src/test change).
- No Document Defect: every AC traced to code + test evidence; no claim in
  the card contradicts the diff.

## Commands run this pass (all read-only, allowed)

`git status --short` (clean), `git log --oneline develop..HEAD` (1 wip
commit), `git rev-parse HEAD develop`, `git merge-base develop HEAD`,
`git cat-file -t d76191e...`, `git diff --stat d76191e 1e82dca` (qa files
only), `grep -n "^def test_"` on both test files, grep anchors in
config/constants/builder/usage/__init__, plus Read of all cited hunks, qa
evidence, README/README.ko/docs-features/agent-profiles, llm-wiki
INDEX + agent-profile-config.

## Wiki write-back (this stage)

- `docs/llm-wiki/usage-aware-agent-profiles.md` — created (Stage 1 topic
  page + decision log).
- `docs/llm-wiki/INDEX.md` — new row `usage-aware-agent-profiles`.
- `docs/llm-wiki/agent-profile-config.md` — `usage_pool` added to
  optional-field list; decision-log row + `[[usage-aware-agent-profiles]]`
  cross-ref.
- `README.md` / `README.ko.md` — per-kind allowed-field lists gain
  `usage_pool` + one-line usage-pool note (Stage 1 scope caveat).
- `docs/features/agent-profiles.md` — supported-fields table gains
  `usage_pool` on all 8 rows; "Usage Pools" subsection with example +
  validation rules + Stage-1 scope note.
- `CHANGELOG.md` — not touched (TASK-6/7/8 precedent: `[Unreleased]`
  entries are written at release commits, not per-phase tickets).

## How to re-run (unrestricted checkout)

```
cd /home/symphony/symphony_workspaces/TASK-12
.venv/bin/python -m pytest tests/test_usage_limits.py tests/test_workflow_agent_profiles.py -q
.venv/bin/pyright src tests
.venv/bin/python -m pytest -q
```
