# TASK-10 QA — Security audit backing

**What**: Seven-point security assessment of the TASK-10 delta (logging fields + one SQLite UPDATE + tests).
**Why**: Provide a durable artifact the `## Security Audit` table rows can cite.

| check | verdict | basis |
| --- | --- | --- |
| secrets | pass | New log fields carry profile/model/reasoning-effort names only (`core.py:6435-6441`, `7084-7098`); no API keys, tokens, or env values are introduced or logged. |
| input-validation | pass | Persisted values originate from validated config dataclasses (`AgentSelection` + `resolve_agent_config`, `workflow/profiles.py:82`), not raw user input; `update_stage_agent_profile` is keyword-only with defaults (`run_registry.py:605-622`). |
| injection | pass | The new UPDATE uses parameterized placeholders exclusively (`run_registry.py:613-640`); no string interpolation of identifiers or values. |
| xss | n/a | No web/UI surface touched; change is orchestrator logging and SQLite persistence only. |
| csrf | n/a | No web/UI surface touched. |
| authz | pass | UPDATE is fenced by `owner_pid`, `owner_boot_id`, and `status = 'active'` (`run_registry.py:624-630`), matching the existing checkpoint pattern (`run_registry.py:575-600`) — no cross-owner or stale-run writes. |
| rate-limit | n/a | No new endpoint; the reroute log fires only when a field actually changes (`core.py:7075-7081`), avoiding log amplification. |
