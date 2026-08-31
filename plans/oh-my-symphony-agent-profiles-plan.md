# oh-my-symphony: Named Agent Profiles Implementation Plan

## Goal

Extend `oh-my-symphony` so that different workflow stages can use different configurations of the same agent backend.

Today, stage routing can select different backend kinds, for example:

```yaml
agent:
  stage_kinds:
    Plan: codex
    Implement: claude
    Review: codex
```

The proposed feature adds **named agent profiles**, so individual stages can select not just the backend, but also the model and other backend-specific settings.

Example:

```yaml
agent:
  kind: claude

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

codex:
  command: codex app-server
  reasoning_effort: medium

claude:
  command: claude -p --output-format stream-json --verbose
  resume_across_turns: true
```

The intended result is:

```text
Research    -> Claude / Fable
Plan        -> Codex / Sol
Build       -> Claude / Sonnet
Review      -> Codex / Sol
QA          -> Codex / Luna
```

The implementation should remain fully backward compatible with existing `agent.kind` and `agent.stage_kinds` configurations.

---

## Design Principle

Profiles should inherit from the existing backend configuration and override only explicitly configured fields.

For example:

```yaml
codex:
  command: codex app-server
  reasoning_effort: medium

agent_profiles:
  sol-reviewer:
    kind: codex
    model: sol
    reasoning_effort: high
```

Conceptually:

```text
global codex config
        |
        v
codex:
  command: codex app-server
  reasoning_effort: medium
        |
        +---- profile overlay
                 |
                 v
        sol-reviewer:
          model: sol
          reasoning_effort: high
                 |
                 v
        resolved config:
          command: codex app-server
          model: sol
          reasoning_effort: high
```

This avoids duplicating the full backend configuration in every profile.

---

# 1. Introduce `AgentProfileConfig`

Primary file:

```text
src/symphony/workflow/config.py
```

Add a profile configuration model conceptually similar to:

```python
@dataclass(frozen=True)
class AgentProfileConfig:
    name: str
    kind: str

    model: str | None = None
    reasoning_effort: str | None = None
    command: str | None = None

    turn_timeout_ms: int | None = None
    read_timeout_ms: int | None = None
    stall_timeout_ms: int | None = None

    resume_across_turns: bool | None = None
```

`None` should mean:

> inherit the value from the global backend configuration.

Do not duplicate every backend-specific dataclass inside each profile.

---

# 2. Add Profile Routing to `AgentConfig`

The existing configuration already supports:

```python
stage_kinds: dict[str, str]
```

and resolves the effective backend using logic similar to:

```text
ticket agent_kind
    >
stage_kinds[state]
    >
agent.kind
```

Extend this with:

```python
stage_profiles: dict[str, str]
default_profile: str | None = None
```

Introduce a richer selection object:

```python
@dataclass(frozen=True)
class AgentSelection:
    kind: str
    profile: str | None = None
```

Add a resolver such as:

```python
def selection_for_state(
    self,
    state: str | None,
    *,
    ticket_profile: str | None = None,
    ticket_kind: str | None = None,
) -> AgentSelection:
    ...
```

This should eventually replace using `kind_for_state()` for runtime dispatch.

---

# 3. Define Resolution Precedence

Use a deterministic precedence order.

Recommended:

```text
1. explicit dispatch profile
2. explicit dispatch kind

3. ticket agent_profile
4. ticket agent_kind

5. agent.stage_profiles[state]
6. agent.stage_kinds[state]

7. agent.default_profile
8. agent.kind
```

Example:

```yaml
agent:
  kind: claude

  stage_profiles:
    Review: sol-reviewer
```

A normal Review ticket uses:

```text
sol-reviewer
```

But if the ticket explicitly contains:

```yaml
agent_kind: agy
```

then the ticket override should win.

### Ambiguous ticket overrides

Prefer rejecting tickets that define both:

```yaml
agent_kind: codex
agent_profile: sonnet-builder
```

unless a very explicit precedence rule is introduced.

Rejecting the ambiguous configuration is safer.

---

# 4. Parse `agent_profiles`

Primary file:

```text
src/symphony/workflow/builder.py
```

Add validation and projection logic such as:

```python
def _validated_agent_profiles(
    raw: Any,
) -> dict[str, AgentProfileConfig]:
    ...
```

Validation should include:

- profile names must be non-empty
- profile names must be unique
- `kind` must be a supported backend
- `stage_profiles` must reference an existing profile
- `model` must be a string
- timeout values must be positive
- backend-specific fields must be valid for that backend
- malformed mappings must fail early

Example invalid configuration:

```yaml
agent_profiles:
  reviewer:
    kind: hal9000
```

This should fail during workflow/config validation rather than later during dispatch.

---

# 5. Keep Backend-Specific Validation

Do not silently accept profile fields that a backend cannot use.

Example:

```yaml
agent_profiles:
  qa:
    kind: agy
    reasoning_effort: high
```

If `agy` does not support `reasoning_effort`, this should fail rather than being ignored.

One approach:

```python
PROFILE_FIELDS_BY_KIND = {
    "codex": {
        "model",
        "reasoning_effort",
        "command",
        "turn_timeout_ms",
        "read_timeout_ms",
        "stall_timeout_ms",
    },
    "claude": {
        "model",
        "command",
        "resume_across_turns",
        "turn_timeout_ms",
        "read_timeout_ms",
        "stall_timeout_ms",
    },
}
```

This makes configuration mistakes visible immediately.

---

# 6. Add First-Class `model` Support to Claude

Codex already supports a model field directly.

Claude should gain the same concept.

Extend the Claude config:

```python
@dataclass(frozen=True)
class ClaudeConfig:
    command: str
    model: str = ""
    ...
```

Then in the Claude backend, inject the configured model into the command.

Conceptually:

```python
cmd = _inject_add_dirs(...)

if self._claude.model:
    cmd = inject_cli_arg(
        cmd,
        "--model",
        self._claude.model,
    )
```

This avoids forcing users to encode the model into the global command:

```yaml
claude:
  command: claude -p --model sonnet ...
```

Instead:

```yaml
claude:
  command: claude -p --output-format stream-json --verbose

agent_profiles:
  sonnet-builder:
    kind: claude
    model: sonnet
```

This is cleaner and makes profiles reusable.

---

# 7. Add a Central Profile Resolver

Create a central resolver rather than implementing profile overlays separately in every backend.

Suggested file:

```text
src/symphony/workflow/profiles.py
```

Conceptual API:

```python
def resolve_agent_config(
    cfg: ServiceConfig,
    selection: AgentSelection,
) -> ResolvedAgentConfig:
    ...
```

Possible output model:

```python
@dataclass(frozen=True)
class ResolvedAgentConfig:
    kind: str
    profile_name: str | None

    codex: CodexConfig | None = None
    claude: ClaudeConfig | None = None
    gemini: GeminiConfig | None = None
    agy: AgyConfig | None = None
```

The resolver should:

1. identify the selected backend kind
2. read the global backend configuration
3. read the selected profile
4. overlay non-null profile values
5. return a concrete backend configuration

Use immutable dataclass replacement where possible.

For example:

```text
ServiceConfig.codex
        |
        v
CodexConfig(
  command="codex app-server",
  reasoning_effort="medium",
  ...
)
        |
        +---- profile:
                 model="sol"
                 reasoning_effort="high"
        |
        v
Resolved CodexConfig(
  command="codex app-server",
  model="sol",
  reasoning_effort="high",
  ...
)
```

---

# 8. Pass Resolved Configuration Through `BackendInit`

The backend should receive its already-resolved configuration.

Instead of relying only on:

```python
init.cfg.agent.kind
```

extend `BackendInit`.

Conceptually:

```python
@dataclass
class BackendInit:
    cfg: ServiceConfig

    selection: AgentSelection
    resolved_backend_config: object

    cwd: Path
    workspace_root: Path
    ...
```

Backend construction then uses:

```python
kind = init.selection.kind
```

rather than re-reading the global default.

This prevents backend implementations from needing to understand profiles.

---

# 9. Let Backends Consume Concrete Resolved Config

Example for Claude.

Today the backend likely reads:

```python
self._claude = init.cfg.claude
```

Change this conceptually to:

```python
self._claude = cast(
    ClaudeConfig,
    init.resolved_backend_config,
)
```

Codex should similarly receive a fully resolved `CodexConfig`.

The runtime layer therefore remains simple:

```text
Orchestrator
     |
     | selects profile
     | resolves profile
     v
AgentBackend
     |
     | receives concrete backend config
     v
Claude / Codex / AGY CLI
```

This is preferable to teaching each backend how profile inheritance works.

---

# 10. Re-Resolve the Profile on Every Stage Change

This is critical.

Example workflow:

```text
TASK-42

Plan
 |
 +-- sol-planner
       Codex / Sol
       session A

        |
        v

Build
 |
 +-- sonnet-builder
       Claude / Sonnet
       session B

        |
        v

Review
 |
 +-- sol-reviewer
       Codex / Sol
       session C
```

The backend/profile selection must be evaluated again whenever a ticket changes stage.

Do not keep using the backend configuration from the previous stage.

---

# 11. Scope Sessions by Backend and Profile

Do not resume a session merely because the ticket ID is unchanged.

Session identity should effectively include:

```text
ticket
+
backend kind
+
profile
```

not only:

```text
ticket
```

For example:

```text
Build
Claude / Sonnet
session=abc

      |
      v

Review
Codex / Sol
session=new

      |
      v

Fix
Claude / Sonnet
```

If resume behavior is enabled, the implementation must ensure that the resumed session belongs to the same effective backend/profile.

This becomes especially important when two profiles use the same backend but different models:

```text
Plan:
Codex / Sol

Review:
Codex / Luna
```

Even though the backend kind is still `codex`, those should be treated as separate execution profiles.

---

# 12. Persist Profile and Model in Run Records

Current execution records should be extended beyond just:

```text
agent_kind
```

Add at least:

```text
agent_profile
model
```

Optionally:

```text
reasoning_effort
```

Example:

```text
TASK-42
state=Build
agent_kind=claude
agent_profile=sonnet-builder
model=sonnet
```

This will be valuable for:

- debugging
- cost analysis
- performance comparisons
- model benchmarking
- auditability
- future automatic profile selection

---

# 13. Add Ticket-Level Profile Overrides

Tickets should support selecting a profile explicitly.

Recommended nested syntax:

```yaml
agent:
  profile: sol-reviewer
```

Optionally support a flat form:

```yaml
agent_profile: sol-reviewer
```

Existing syntax remains valid:

```yaml
agent:
  kind: codex
```

This allows special-case tickets to override the normal stage policy.

Example:

```yaml
---
identifier: CORE-991
state: Plan

agent:
  profile: fable-planner
---
```

This could be useful when Hermes detects that a task needs an unusually capable planning model.

---

# 14. Add CLI Support

Extend the existing board CLI.

For task creation:

```bash
symphony board new TASK-1 "Implement authentication" \
  --agent-profile sonnet-builder
```

For updates:

```bash
symphony board update TASK-1 \
  --agent-profile sol-reviewer
```

This is important for future integrations, including a Hermes MCP layer, because external tools can call validated Symphony APIs/CLI operations instead of editing ticket Markdown directly.

---

# 15. Extend `symphony doctor`

The doctor command should validate profile configuration before workers start.

Example:

```text
PASS agent.profile.sol-planner
     kind=codex
     model=sol
     reasoning_effort=high

PASS agent.profile.sonnet-builder
     kind=claude
     model=sonnet

FAIL agent.stage_profiles.QA
     unknown profile "luna-builderr"
```

Possible warning:

```text
WARN agent.profile.custom-codex
     profile overrides the global Codex command
```

Checks should include:

- profile exists
- backend kind is supported
- configured executable exists where possible
- model field is valid syntax
- referenced stages resolve correctly
- unsupported profile properties are rejected

---

# 16. Defer Web UI Changes Until the Runtime Works

Do not make UI support a blocker for the initial feature.

First deliver:

```text
WORKFLOW.md
CLI
configuration parser
runtime resolution
backend integration
tests
doctor
documentation
```

Then add a Settings/UI representation.

Example UI table:

```text
Stage       Profile             Runtime     Model
----------------------------------------------------
Research    fable-planner       Claude      Fable
Plan        sol-planner         Codex       Sol
Build       sonnet-builder      Claude      Sonnet
Review      sol-reviewer        Codex       Sol
QA          luna-qa             Codex       Luna
```

---

# 17. Test Strategy

The feature should have strong test coverage because a routing bug could silently execute the wrong model.

## Configuration Parsing

Test:

```text
[PASS] parses agent_profiles
[PASS] parses stage_profiles
[PASS] parses default_profile
[PASS] unknown profile fails
[PASS] unsupported backend kind fails
[PASS] duplicate profile fails
[PASS] invalid timeout fails
[PASS] unsupported field for backend fails
```

---

## Resolution Logic

Test:

```text
[PASS] stage_profile beats stage_kind
[PASS] ticket agent_profile beats stage_profile
[PASS] ticket agent_kind beats stage_profile
[PASS] default_profile is used as fallback
[PASS] global agent.kind remains final fallback
```

Also verify behavior when only the old configuration format is present.

---

## Codex Backend

Test that:

```text
[PASS] profile model reaches Codex
[PASS] profile reasoning_effort reaches Codex
[PASS] inherited command remains intact
[PASS] command override works
```

---

## Claude Backend

Test that:

```text
[PASS] profile model injects --model
[PASS] inherited command remains intact
[PASS] profile command override works
[PASS] resume settings inherit correctly
```

---

## Stage Transition Lifecycle

Test:

```text
Plan / sol-planner
        |
        v
Build / sonnet-builder
        |
        v
Review / sol-reviewer
```

Verify:

```text
[PASS] profile is re-resolved on stage transition
[PASS] backend is recreated when required
[PASS] model changes when profile changes
[PASS] old session is not resumed incorrectly
[PASS] run registry stores correct profile/model
```

---

## Same Backend, Different Profile

This case is especially important:

```text
Plan
Codex / Sol

    |
    v

Review
Codex / Luna
```

Verify that profile changes are recognized even though:

```text
kind == "codex"
```

in both stages.

---

## Backward Compatibility

Existing configuration:

```yaml
agent:
  kind: claude

  stage_kinds:
    Build: claude
    Review: codex
```

must continue to behave exactly as before.

Add an explicit regression test proving that a workflow without:

```text
agent_profiles
stage_profiles
default_profile
```

has unchanged behavior.

---

# 18. Documentation

Update at least:

```text
README.md
WORKFLOW.file.example.md
WORKFLOW.example.md
docs/
```

Document:

- difference between backend kinds and profiles
- profile inheritance
- stage profile resolution
- ticket-level overrides
- supported fields by backend
- backward compatibility
- examples for mixed Claude/Codex workflows
- examples using multiple models of the same backend

Example:

```yaml
agent:
  stage_profiles:
    Plan: codex-sol
    Implement: codex-luna

agent_profiles:
  codex-sol:
    kind: codex
    model: sol
    reasoning_effort: high

  codex-luna:
    kind: codex
    model: luna
    reasoning_effort: medium
```

---

# 19. Suggested Implementation Phases

## Phase 1 — Configuration Model

Implement:

- `AgentProfileConfig`
- `agent_profiles`
- `stage_profiles`
- `default_profile`
- validation
- parsing tests

No runtime behavior changes yet.

---

## Phase 2 — Runtime Resolution

Implement:

- `AgentSelection`
- profile resolution
- inheritance/overlay logic
- `BackendInit` changes
- stage-based profile resolution
- lifecycle tests

At the end of this phase, profiles should select different concrete backend configurations.

---

## Phase 3 — Backend Support

Implement:

- Codex model/profile overrides
- Codex reasoning overrides
- first-class `ClaudeConfig.model`
- Claude `--model` injection
- command overrides
- profile-aware session handling

At the end of this phase, the main user-facing feature works.

---

## Phase 4 — Observability and Tooling

Implement:

- run record `agent_profile`
- run record `model`
- optional `reasoning_effort`
- ticket profile override
- CLI `--agent-profile`
- `symphony doctor` profile validation

This makes the feature operationally safe.

---

## Phase 5 — UX and Documentation

Implement:

- updated example workflows
- README documentation
- migration guidance
- web UI profile display
- optional profile editing in web settings

Do this after runtime behavior is stable.

---

# 20. Acceptance Criteria

The feature should be considered complete when this configuration works:

```yaml
agent:
  kind: claude

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

codex:
  command: codex app-server

claude:
  command: claude -p --output-format stream-json --verbose
```

And runtime behavior visibly produces:

```text
RESEARCH
Claude
profile=fable-planner
model=fable

      |
      v

PLAN
Codex
profile=sol-planner
model=sol
reasoning=high

      |
      v

BUILD
Claude
profile=sonnet-builder
model=sonnet

      |
      v

REVIEW
Codex
profile=sol-reviewer
model=sol
reasoning=high

      |
      v

QA
Codex
profile=luna-qa
model=luna
reasoning=medium
```

At the same time, an existing configuration such as:

```yaml
agent:
  kind: claude

  stage_kinds:
    Build: claude
    Review: codex
```

must continue to work unchanged.

---

# 21. Recommended Relationship to the Hermes MCP Integration

Implement this profile feature **before** the Hermes MCP layer.

The intended separation should be:

```text
Hermes
   |
   | decides WHAT work should exist
   v
Symphony MCP API
   |
   | creates / manages requests and tickets
   v
oh-my-symphony
   |
   | WORKFLOW.md decides WHO executes each stage
   v
Claude / Codex / AGY / other agent backends
```

Hermes should not normally need to decide:

```text
use Sonnet for this stage
use Sol for the next stage
```

Instead, that policy belongs in `WORKFLOW.md`.

Hermes should create structured work and supervise progress, while Symphony remains responsible for execution policy.

This produces a clean responsibility split:

```text
Hermes:
- intent
- prioritization
- task creation
- supervision

WORKFLOW.md:
- engineering process
- stage routing
- agent profile selection
- model selection
- concurrency
- quality gates

oh-my-symphony:
- scheduling
- worktrees
- retries
- lifecycle
- execution

Claude / Codex / AGY:
- actual engineering work
```

---

# Final Recommended Configuration Model

The long-term configuration should support all three routing levels:

```yaml
agent:
  kind: claude
  default_profile: sonnet-builder

  stage_kinds:
    Emergency: agy

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

This keeps the existing backend abstraction intact while making model selection a first-class part of the Symphony workflow policy.
