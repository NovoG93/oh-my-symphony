# Named Agent Profiles in Symphony

## Overview

Named Agent Profiles allow workflow stages and individual tickets to select not only the agent backend (e.g., Codex, Claude, Gemini, AGY), but also backend-specific configuration such as the model, reasoning effort, custom commands, timeouts, and session resumption settings.

Prior to named profiles, Symphony supported stage routing across backend kinds via `agent.stage_kinds` (e.g., `Plan: codex`, `Implement: claude`). Named profiles extend this capability so different stages can utilize different models or configurations of the same backend (e.g., `sol-planner` with high reasoning effort vs. `luna-qa` with medium reasoning effort) or orchestrate mixed multi-agent workflows.

---

## Key Concepts

### 1. Backend Kinds vs. Named Profiles

- **Backend Kind** (`codex`, `claude`, `gemini`, `agy`, `kiro`, `opencode`, `pi`, `prime-agent`): Defines the underlying agent CLI adapter, subprocess protocol, tool surface, and process execution lifecycle.
- **Named Profile** (`fable-planner`, `sol-reviewer`, `sonnet-builder`, etc.): An overlay configuration applied to a backend kind. A profile specifies a target backend `kind` along with specific settings (e.g., `model`, `reasoning_effort`, `command`, `turn_timeout_ms`).

### 2. Profile Inheritance & Overlay Model

Profiles inherit from the matching global backend configuration block (`codex:`, `claude:`, etc.) in `WORKFLOW.md` and override only explicitly configured, non-null fields.

```text
Global backend configuration (e.g. codex:)
  ├── command: codex app-server
  └── reasoning_effort: medium
            │
            ▼
    Profile overlay (sol-reviewer:)
      ├── kind: codex
      ├── model: sol
      └── reasoning_effort: high
            │
            ▼
    Resolved configuration:
      ├── command: codex app-server   (inherited)
      ├── model: sol                  (from profile)
      └── reasoning_effort: high      (from profile)
```

Unset fields in a profile default to `None`, meaning they automatically inherit their values from the global backend configuration. This prevents configuration duplication across profiles.

### 3. Resolution Precedence (8 Tiers)

When Symphony dispatches an agent for a ticket in a given workflow state, the agent kind and profile are resolved using deterministic 8-tier precedence:

1. **Explicit dispatch profile**: Runtime CLI / dispatch profile argument (`--agent-profile`).
2. **Explicit dispatch kind**: Runtime CLI / dispatch kind argument (`--agent-kind`).
3. **Ticket `agent.profile`**: Frontmatter pin on the ticket (`agent: {profile: ...}` or flat `agent_profile:`).
4. **Ticket `agent.kind`**: Frontmatter pin on the ticket (`agent: {kind: ...}` or flat `agent_kind:`).
5. **Stage profile**: `agent.stage_profiles[state]` in `WORKFLOW.md`.
6. **Stage kind**: `agent.stage_kinds[state]` in `WORKFLOW.md`.
7. **Default profile**: `agent.default_profile` in `WORKFLOW.md`.
8. **Global agent kind**: `agent.kind` in `WORKFLOW.md`.

Resolution is re-evaluated dynamically on **every stage transition** (e.g., In Progress → Verify → Document), ensuring the appropriate profile is selected for each phase of execution.

### 4. Ticket-Level Overrides & Mutual Exclusion

Tickets can explicitly select a profile or backend in their YAML frontmatter:

```yaml
---
id: TASK-42
title: Refactor authentication service
state: In Progress
agent:
  profile: sol-planner
---
```

Or using the flat alias:

```yaml
---
agent_profile: sol-planner
---
```

> **Important**: A ticket must not specify both `agent_kind` and `agent_profile`. Defining both is rejected as ambiguous with a `ConfigValidationError` / `SymphonyError` during ticket creation, update, and dispatch.

### 5. Supported Profile Fields by Backend

To prevent configuration errors, profiles strictly validate allowed fields at configuration load time (`PROFILE_FIELDS_BY_KIND`):

| Backend Kind | Allowed Profile Fields | Description / Notes |
|---|---|---|
| `codex` | `model`, `reasoning_effort`, `command`, `turn_timeout_ms`, `read_timeout_ms`, `stall_timeout_ms` | `model` and `reasoning_effort` are sent directly in turn parameters. |
| `claude` | `model`, `command`, `resume_across_turns`, `turn_timeout_ms`, `read_timeout_ms`, `stall_timeout_ms` | A non-empty `model` injects `--model <name>` immediately following the `claude` command token. |
| `gemini` | `command`, `resume_across_turns`, `turn_timeout_ms`, `read_timeout_ms`, `stall_timeout_ms` | `resume_across_turns` is accepted but inert — the gemini backend has no resume support. |
| `agy` | `command`, `resume_across_turns`, `turn_timeout_ms`, `read_timeout_ms`, `stall_timeout_ms` | |
| `kiro` | `command`, `resume_across_turns`, `turn_timeout_ms`, `read_timeout_ms`, `stall_timeout_ms` | |
| `opencode` | `command`, `resume_across_turns`, `turn_timeout_ms`, `read_timeout_ms`, `stall_timeout_ms` | |
| `pi` | `command`, `resume_across_turns`, `turn_timeout_ms`, `read_timeout_ms`, `stall_timeout_ms` | |
| `prime_agent` | `command`, `resume_across_turns`, `turn_timeout_ms`, `read_timeout_ms`, `stall_timeout_ms` | |

Any unsupported field (e.g., specifying `reasoning_effort` on a `claude` or `agy` profile) is rejected during configuration loading.

### 6. Session Scoping & Isolation

Session identity in Symphony is scoped by `(ticket_id, backend_kind, profile_name)`. When a ticket transitions between stages configured with different profiles or different backends, Symphony reconstructs the backend driver and initializes a fresh session. This guarantees that model context and session state do not leak between different stages.

---

## Configuration Examples

### Example 1: Multi-Model Single-Backend Workflow

Use faster, lightweight models for planning and triage, and high-reasoning models for implementation and review:

```yaml
tracker:
  kind: file
  board_root: ./kanban

agent:
  kind: codex
  default_profile: sol-standard

  stage_profiles:
    Todo: luna-fast
    Plan: sol-high
    "In Progress": sol-high
    Verify: sol-high
    Document: luna-fast

agent_profiles:
  luna-fast:
    kind: codex
    model: luna
    reasoning_effort: low

  sol-standard:
    kind: codex
    model: sol
    reasoning_effort: medium

  sol-high:
    kind: codex
    model: sol
    reasoning_effort: high

codex:
  command: codex app-server
  turn_timeout_ms: 3600000
```

### Example 2: Mixed-Backend Multi-Agent Workflow (§20 Acceptance Configuration)

Combine Claude and Codex across different stages of development:

```yaml
tracker:
  kind: file
  board_root: ./kanban

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

Stage execution resolves to:
- **Research** → Claude with model `fable` (`claude -p --output-format stream-json --verbose --model fable`)
- **Plan** → Codex with model `sol` and high reasoning effort (`codex app-server`)
- **Build** → Claude with model `sonnet` (`claude -p --output-format stream-json --verbose --model sonnet`)
- **Review** → Codex with model `sol` and high reasoning effort (`codex app-server`)
- **QA** → Codex with model `luna` and medium reasoning effort (`codex app-server`)

---

## Migration Guidance for Existing Users

Symphony is fully backward-compatible with legacy configurations. Existing workflows using `agent.kind` and `agent.stage_kinds` continue to work without modification.

### Incremental Migration Steps:

1. **Keep existing backend definitions**: Your existing `codex:`, `claude:`, `gemini:`, etc. blocks remain the global baseline.
2. **Define `agent_profiles:`**: Add an `agent_profiles:` map containing the specific configurations and model variants you wish to name.
3. **Switch to `stage_profiles`**: Replace `agent.stage_kinds` with `agent.stage_profiles` referencing your defined profile names.
4. **Set a default profile (Optional)**: Configure `agent.default_profile` to establish a default profile fallback for unmapped stages.

#### Legacy Configuration:
```yaml
agent:
  kind: claude
  stage_kinds:
    Build: claude
    Review: codex
```

#### Migrated Configuration with Profiles:
```yaml
agent:
  kind: claude
  default_profile: claude-sonnet
  stage_profiles:
    Build: claude-sonnet
    Review: codex-sol

agent_profiles:
  claude-sonnet:
    kind: claude
    model: sonnet
  codex-sol:
    kind: codex
    model: sol
    reasoning_effort: high
```

---

## CLI & Observability Tooling

### CLI Profile Management

Create or update tickets with a designated agent profile using `symphony board`:

```bash
# Create a new ticket with an agent profile
symphony board new TASK-10 "Implement caching" --agent-profile sol-planner

# Update an existing ticket to use a different profile
symphony board update TASK-10 --agent-profile sonnet-builder

# Display ticket details including assigned agent profile
symphony board show TASK-10
```

### Preflight Diagnostics with `symphony doctor`

`symphony doctor` validates configured profiles before workers start:
- Verifies profile backend kinds are supported.
- Checks executable paths on `$PATH`.
- Ensures stage profiles and default profiles resolve to defined entries.
- Warns when a profile overrides the base executable command.
