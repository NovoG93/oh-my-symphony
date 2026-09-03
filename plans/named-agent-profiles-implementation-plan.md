# Named Agent Profiles — Implementation / Completion Plan

Repository: `https://github.com/NovoG93/oh-my-symphony/tree/develop`

## Current State

The `develop` branch already contains a substantial implementation of the **Named Agent Profiles** feature, including:

- `AgentProfileConfig`
- `AgentSelection`
- `agent.stage_profiles`
- `agent.default_profile`
- profile-aware backend resolution
- Claude model injection
- Codex model / reasoning-effort overrides
- ticket-level profile pinning
- profile-aware session scoping
- CLI/tooling support
- dedicated tests
- documentation

Because of this, the recommended task is **not a greenfield implementation**. The remaining work should be treated as a **verification, hardening, observability, and acceptance pass**.

The target outcome is to ensure that named agent profiles are deterministic, backward-compatible, observable, validated early, and safe to use across stage transitions and multiple agent backends.

---

# 1. Keep the Existing Configuration Schema as the Contract

The configuration model should continue to support a structure such as:

```yaml
agent:
  kind: claude
  default_profile: sonnet-builder

  stage_profiles:
    Research: fable-planner
    Plan: sol-planner
    Build: sonnet-builder
    Review: sol-reviewer
    QA: luna-qa

agent_profiles:

  fable-planner:
    kind: claude
    model: fable

  sol-planner:
    kind: codex
    model: sol
    reasoning_effort: high

  sonnet-builder:
    kind: claude
    model: sonnet

  sol-reviewer:
    kind: codex
    model: sol
    reasoning_effort: high

  luna-qa:
    kind: codex
    model: luna
    reasoning_effort: medium
```

The fundamental inheritance model should remain:

```text
global backend configuration
        +
named profile overrides
        =
effective backend configuration
```

Example:

```yaml
codex:
  command: codex app-server
  turn_timeout_ms: 3600000
  reasoning_effort: medium

agent_profiles:
  sol-reviewer:
    kind: codex
    model: sol
    reasoning_effort: high
```

Resolved result:

```text
command: codex app-server
model: sol
reasoning_effort: high
turn_timeout_ms: 3600000
```

### Acceptance Criteria

Ensure tests explicitly prove:

- omitted profile fields inherit from the global backend configuration;
- explicit profile fields override the global backend configuration;
- `None` does not accidentally erase inherited values;
- unsupported profile fields are rejected;
- unknown profile names fail validation before execution;
- backend kind aliases are normalized consistently.

---

# 2. Keep Profile Resolution Centralized

Profile resolution should remain centralized rather than duplicated across:

- orchestrator code;
- agent backends;
- CLI;
- web API;
- tracker implementations.

The authoritative flow should remain conceptually:

```text
Issue
 │
 ├── state
 ├── agent_kind?
 └── agent_profile?
        │
        ▼
selection_for_state()
        │
        ▼
AgentSelection
 ├── kind
 └── profile
        │
        ▼
resolve_agent_config()
        │
        ▼
ResolvedAgentConfig
        │
        ▼
Backend factory
```

The intended resolution priority should be:

```text
1. explicit dispatch profile
2. explicit dispatch kind
3. ticket agent.profile
4. ticket agent.kind
5. agent.stage_profiles[state]
6. agent.stage_kinds[state]
7. agent.default_profile
8. agent.kind
```

### Implementation Task

Search for runtime code that still performs ad-hoc resolution, for example:

```python
issue.agent_kind or cfg.agent.kind
```

or code that only calls:

```python
kind_for_state(...)
```

and migrate runtime dispatch logic to the profile-aware selection path.

`kind_for_state()` may remain for compatibility or display purposes, but the runtime should resolve profiles through a single authoritative path.

---

# 3. Harden Configuration Validation

Validation should happen during workflow loading, not when a ticket reaches a later stage.

## 3.1 Profile Names

Reject empty profile names:

```yaml
agent_profiles:
  "":
```

Recommended valid naming style:

```text
sol-planner
claude-sonnet
review.high
qa_fast
```

Prefer documenting lowercase kebab-case, while keeping profile references deterministic.

Avoid silently changing profile names unless explicitly documented.

## 3.2 Unknown Profile References

The following must fail at startup:

```yaml
agent:
  stage_profiles:
    Review: does-not-exist
```

Likewise:

```yaml
agent:
  default_profile: missing-profile
```

must fail immediately.

## 3.3 Backend-Specific Fields

Continue using strict backend-specific allowlists.

For example:

```text
Codex:
  model
  reasoning_effort
  command
  turn_timeout_ms
  read_timeout_ms
  stall_timeout_ms

Claude:
  model
  command
  resume_across_turns
  turn_timeout_ms
  read_timeout_ms
  stall_timeout_ms
```

A Claude profile containing `reasoning_effort`, for example, should be rejected instead of silently ignored.

Avoid allowing arbitrary profile keys.

---

# 4. Make Resolved Backend Configuration First-Class

The runtime should construct one resolved backend configuration before creating a worker.

Conceptual flow:

```python
selection = cfg.selection_for_state(
    issue.state,
    ticket_profile=issue.agent_profile,
    ticket_kind=issue.agent_kind,
)

resolved = resolve_agent_config(cfg, selection)

backend = create_backend(
    kind=resolved.kind,
    resolved_config=resolved.active_config,
)
```

The backend should not understand profile resolution.

Claude should receive a final `ClaudeConfig`.

Codex should receive a final `CodexConfig`.

This preserves the existing `AgentBackend` abstraction and keeps profile behavior outside the individual backend implementations.

---

# 5. Verify Codex Profile Execution

Two named Codex profiles must be able to use different model and reasoning configurations.

Example:

```yaml
agent_profiles:

  sol-plan:
    kind: codex
    model: sol
    reasoning_effort: high

  luna-fix:
    kind: codex
    model: luna
    reasoning_effort: medium
```

Required runtime behavior:

```text
sol-plan
    ↓
Codex turn
model = sol
effort = high

luna-fix
    ↓
Codex turn
model = luna
effort = medium
```

### Required Tests

Verify:

- profile model reaches the Codex app-server request;
- profile reasoning effort reaches the request;
- inherited command remains intact;
- inherited timeouts remain intact;
- a profile overriding only one field preserves all other global defaults.

---

# 6. Verify Claude Profile Execution

Claude profiles should support model-specific execution.

Example:

```yaml
claude:
  command: claude -p --output-format stream-json --verbose

agent_profiles:
  sonnet-builder:
    kind: claude
    model: sonnet
```

Effective execution should become conceptually:

```bash
claude --model sonnet -p --output-format stream-json --verbose
```

### Harden Command Handling

Test at least:

```yaml
claude:
  command: claude -p --output-format stream-json
```

where model injection should work.

Also test a custom wrapper:

```yaml
claude:
  command: /usr/local/bin/my-claude-wrapper
```

The implementation should not blindly assume that arbitrary wrapper commands accept Claude CLI flags.

Document this behavior clearly.

---

# 7. Verify Profile-Aware Session Isolation

Session isolation is critical.

Example stage sequence:

```text
Plan
  ↓
Codex / Sol
  ↓
Implement
  ↓
Claude / Sonnet
  ↓
Review
  ↓
Codex / Sol
```

Claude must not inherit Codex state.

Likewise:

```text
Codex / Sol planner
      ↓
Codex / Luna implementation
```

must not accidentally resume the Sol session only because both use Codex.

The intended session identity should be:

```text
(ticket_id, backend_kind, profile_name)
```

Example:

```text
TASK-42 / codex / sol-planner
     session A

TASK-42 / claude / sonnet-builder
     session B

TASK-42 / codex / sol-reviewer
     session C
```

### Required Tests

Test:

```text
same ticket + same profile
→ resume allowed

same ticket + different profile
→ fresh session

same ticket + same backend kind + different profile
→ fresh session

same ticket + different backend kind
→ fresh session

different ticket + same profile
→ fresh session
```

---

# 8. Verify Ticket-Level Profile Pinning

Ticket syntax should support:

```yaml
---
identifier: TASK-42
state: Build

agent:
  profile: sonnet-builder
---
```

A compatibility alias may also be supported:

```yaml
agent_profile: sonnet-builder
```

Avoid allowing ambiguous combinations such as:

```yaml
agent_kind: claude
agent_profile: sonnet-builder
```

because the profile already determines the backend kind.

A ticket should normally select either:

```text
agent_profile
```

or:

```text
agent_kind
```

not both.

### CLI Support

Verify commands such as:

```bash
symphony board new TASK-10 "Implement caching" \
  --agent-profile sol-planner
```

and:

```bash
symphony board update TASK-10 \
  --agent-profile sonnet-builder
```

remain symmetric with existing `--agent-kind` functionality.

---

# 9. Preserve Full Backward Compatibility

Existing workflows must continue to work unchanged.

For example:

```yaml
agent:
  kind: claude

  stage_kinds:
    Build: claude
    Review: codex
```

must behave exactly as before.

Profiles must remain opt-in.

Therefore, when all of these are absent:

```text
agent_profiles
stage_profiles
default_profile
```

the runtime should behave semantically like the pre-profile implementation.

Expected fallback:

```text
stage_profiles missing
      ↓
stage_kinds
      ↓
agent.kind
```

Add explicit regression tests covering existing workflow files.

---

# 10. Improve Runtime Observability

Every running task should expose, where available:

```text
backend
profile
model
reasoning effort
```

Example UI/log representation:

```text
TASK-42
Build
Claude
profile: sonnet-builder
model: sonnet
```

Example:

```text
TASK-43
Review
Codex
profile: sol-reviewer
model: sol
effort: high
```

Recommended structured log:

```text
dispatch ticket=TASK-42
state=Build
kind=claude
profile=sonnet-builder
model=sonnet
```

This will be important later for:

- cost analysis;
- debugging;
- Hermes MCP integration;
- understanding which model performed a failed or successful stage.

---

# 11. Improve `symphony doctor`

`symphony doctor ./WORKFLOW.md` should validate and display profile configuration clearly.

Recommended output:

```text
Agent profiles

✓ fable-planner
  kind: claude
  model: fable
  command: claude

✓ sol-planner
  kind: codex
  model: sol
  reasoning_effort: high
  command: codex

✓ sonnet-builder
  kind: claude
  model: sonnet

✓ sol-reviewer
  kind: codex
  model: sol
  reasoning_effort: high
```

Also show stage routing:

```text
Stage routing

Research  → fable-planner   → claude/fable
Plan      → sol-planner     → codex/sol/high
Build     → sonnet-builder  → claude/sonnet
Review    → sol-reviewer    → codex/sol/high
QA        → luna-qa         → codex/luna/medium
```

### Doctor Acceptance Criteria

`doctor` should detect:

- unknown profiles;
- unsupported profile fields;
- missing backend executables;
- invalid command overrides;
- invalid stage profile references;
- invalid default profiles;
- conflicting ticket/profile configurations where applicable.

---

# 12. Add Effective Profile Data to the API

The web/API layer should expose the **resolved effective execution configuration**, not merely raw ticket metadata.

Recommended response:

```json
{
  "agent": {
    "kind": "claude",
    "profile": "sonnet-builder",
    "model": "sonnet"
  }
}
```

For Codex:

```json
{
  "agent": {
    "kind": "codex",
    "profile": "sol-reviewer",
    "model": "sol",
    "reasoning_effort": "high"
  }
}
```

This is particularly important because a ticket may not explicitly declare a profile.

Its effective profile may come from:

```text
stage_profiles
```

or:

```text
default_profile
```

Future integrations such as Hermes should be able to query the actual resolved execution state without reimplementing profile-selection logic.

---

# 13. Complete the Test Matrix

The existing profile-specific test files should act as the main acceptance suite.

Cover at least the following layers.

## Configuration Tests

```text
✓ parse profiles
✓ inheritance
✓ invalid kinds
✓ invalid fields
✓ unknown stage profile
✓ unknown default profile
✓ normalization
```

## Resolution Tests

Validate all precedence levels:

```text
dispatch_profile
>
dispatch_kind
>
ticket_profile
>
ticket_kind
>
stage_profile
>
stage_kind
>
default_profile
>
global kind
```

## Backend Tests

```text
✓ Codex model passed
✓ Codex reasoning effort passed

✓ Claude model injected
✓ Claude inherited command preserved
✓ Claude wrapper command preserved safely
```

## Runtime Tests

```text
✓ stage transition re-resolves profile
✓ profile transition rebuilds backend when required
✓ session does not leak between profiles
✓ same-profile continuation resumes
```

## End-to-End Test

Simulate:

```text
Todo
→ Plan
→ Build
→ Review
→ Done
```

Assert the effective agent profile at every dispatch.

Use mocks/fake executables rather than real API calls.

---

# 14. Ship a Clear Example `WORKFLOW.md`

Provide an example demonstrating the intended use case:

```yaml
agent:
  kind: claude
  default_profile: sonnet-builder

  stage_profiles:
    Todo: fable-triage
    Research: fable-research
    Plan: sol-planner
    "Plan Review": opus-reviewer
    Implement: sonnet-builder
    Fix: luna-fixer
    Review: sol-reviewer
    QA: luna-qa
    Document: fable-docs

agent_profiles:

  fable-triage:
    kind: claude
    model: fable

  fable-research:
    kind: claude
    model: fable

  sol-planner:
    kind: codex
    model: sol
    reasoning_effort: high

  opus-reviewer:
    kind: claude
    model: opus

  sonnet-builder:
    kind: claude
    model: sonnet

  luna-fixer:
    kind: codex
    model: luna
    reasoning_effort: medium

  sol-reviewer:
    kind: codex
    model: sol
    reasoning_effort: high

  luna-qa:
    kind: codex
    model: luna
    reasoning_effort: medium

  fable-docs:
    kind: claude
    model: fable
```

Conceptually:

```text
                   Hermes
                      │
                      ▼
                  Symphony
                      │
     ┌────────────────┼──────────────────┐
     │                │                  │
     ▼                ▼                  ▼
 Research/Plan    Implementation      Review/QA
     │                │                  │
Fable / Sol       Sonnet / Luna        Sol / Luna
```

---

# 15. Suggested Commit / Implementation Order

If changes are still required after auditing the existing branch, make them in this order.

## Commit 1 — Configuration Model and Validation

Implement or harden:

- `AgentProfileConfig`;
- `agent_profiles`;
- `stage_profiles`;
- `default_profile`;
- backend-specific field validation;
- profile-reference validation.

## Commit 2 — Central Profile Resolver

Implement or harden:

- `AgentSelection`;
- the full precedence chain;
- immutable profile overlays;
- resolved backend configuration;
- backend-specific allowlists.

## Commit 3 — Runtime / Backend Integration

Verify:

- resolved configuration is passed to backend factories;
- Codex model is applied;
- Codex reasoning effort is applied;
- Claude model is applied;
- inherited backend values are preserved.

## Commit 4 — Session Isolation

Ensure:

- profile-aware session keys;
- correct behavior across stage transitions;
- no state leakage across model/profile changes;
- continuation only when the profile identity remains compatible.

## Commit 5 — Ticket and CLI Support

Ensure:

- `agent_profile`;
- `agent.profile`;
- `--agent-profile`;
- ticket update support;
- mutual exclusion / ambiguity checks.

## Commit 6 — Doctor, API, UI, and Observability

Add or improve:

- effective profile information;
- resolved model information;
- reasoning effort information;
- stage routing table;
- diagnostics.

## Commit 7 — End-to-End and Regression Tests

Complete:

- config tests;
- resolution tests;
- backend tests;
- runtime tests;
- session isolation tests;
- E2E workflow;
- legacy workflow regression suite.

## Commit 8 — Documentation and Examples

Update:

- profile documentation;
- `WORKFLOW.md` example;
- migration / compatibility notes;
- backend-specific supported fields;
- custom command limitations.

---

# Definition of Done

The feature is complete when all of the following are true:

- A workflow can define multiple named profiles for the same backend.
- Profiles can select different models.
- Codex profiles can select different reasoning-effort levels.
- Stages can map to profiles.
- Tickets can override the selected profile.
- Profile resolution follows one deterministic precedence chain.
- Global backend settings are inherited correctly.
- Invalid profiles fail during configuration loading.
- Backend implementations receive already-resolved configuration.
- Session state cannot leak between different profiles.
- Legacy `agent.kind` and `stage_kinds` workflows remain fully compatible.
- `symphony doctor` validates and explains profile routing.
- API/UI/logging expose effective profile/model information.
- All configuration, runtime, backend, session, E2E, and regression tests pass.
- Documentation includes a complete multi-model workflow example.

---

# Recommended Task Prompt for Claude Code / Codex

Use the following as the implementation task:

> Audit the existing Named Agent Profiles implementation on the `develop` branch of `NovoG93/oh-my-symphony` against this plan. Do not reimplement functionality that is already correct. Identify gaps in validation, precedence handling, backend configuration inheritance, session isolation, CLI/tooling support, observability, API exposure, `symphony doctor`, tests, and documentation. Make the smallest changes necessary to satisfy every Definition of Done item. Preserve backward compatibility with workflows using only `agent.kind` and `agent.stage_kinds`. Add or update tests for every behavioral change and run the complete relevant test suite before considering the task complete.
