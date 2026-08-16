# TASK-8 Work Notes: Named Agent Profiles — Phase 5 Documentation & End-to-End Validation

## Overview

TASK-8 completes the named agent profiles feature (Phase 5 of 5) according to `/home/symphony/agent-profiles-plan.md`. Phases 1–4 implemented the configuration model, 8-tier resolution, backend execution overlays, observability provenance (migration v9), ticket overrides, board CLI integration, and doctor preflight checks.

Phase 5 focuses on:
1. Comprehensive documentation across `README.md`, `WORKFLOW.example.md`, `WORKFLOW.file.example.md`, and `docs/features/agent-profiles.md`.
2. Explicit migration guidance for existing `agent.kind` and `agent.stage_kinds` users.
3. Verification and end-to-end testing of the complete §20 acceptance configuration.
4. Backward compatibility validation proving legacy workflows run unmodified.
5. Boundary enforcement: Web UI profile editing is explicitly deferred.

---

## 1. Documentation Topics & Coverage

### 1.1 Backend Kinds vs Named Profiles
- **Backend Kind** (`codex`, `claude`, `gemini`, `agy`, `kiro`, `opencode`, `pi`, `prime-agent`): Specifies the binary adapter, IPC mechanism, CLI flags, and process lifecycle.
- **Named Profile** (`fable-planner`, `sol-reviewer`, `sonnet-builder`, etc.): Specifies an overlay configuration (model, reasoning effort, command, timeouts, session continuity) applied to a specific backend kind.

### 1.2 Profile Inheritance & Overlay Model
- Profiles define `kind: <backend>` and override only non-null, allowlisted fields (`PROFILE_FIELDS_BY_KIND`).
- Unset fields (`None`) automatically inherit from the global backend block (`codex:`, `claude:`, etc.).
- Global command arguments (e.g. `--permission-mode acceptEdits`, `--add-dir`, `app-server`) remain intact unless explicitly overridden by profile `command:`.

### 1.3 Precedence Hierarchy (8 Tiers)
When resolving the agent for a given tracker state and dispatch:
1. `dispatch_profile` (explicit CLI/runtime dispatch profile parameter)
2. `dispatch_kind` (explicit CLI/runtime dispatch kind parameter)
3. `ticket agent.profile` / `agent_profile:` (ticket frontmatter pin)
4. `ticket agent.kind` / `agent_kind:` (ticket frontmatter pin)
5. `agent.stage_profiles[state]` (workflow stage-to-profile mapping)
6. `agent.stage_kinds[state]` (workflow stage-to-kind mapping)
7. `agent.default_profile` (workflow default profile fallback)
8. `agent.kind` (workflow global backend kind fallback)

### 1.4 Mutual Exclusion & Ticket Overrides
- Tickets setting both `agent_kind` and `agent_profile` are rejected as ambiguous (`ConfigValidationError` / `SymphonyError`).
- CLI commands `symphony board new --agent-profile <name>` and `symphony board update --agent-profile <name>` validate that profile names exist in `agent_profiles:`.

### 1.5 Supported Fields by Backend (`PROFILE_FIELDS_BY_KIND`)
| Backend | Allowed Profile Fields |
|---|---|
| `codex` | `model`, `reasoning_effort`, `command`, `turn_timeout_ms`, `read_timeout_ms`, `stall_timeout_ms` |
| `claude` | `model`, `command`, `resume_across_turns`, `turn_timeout_ms`, `read_timeout_ms`, `stall_timeout_ms` |
| `gemini`, `agy`, `kiro`, `opencode`, `pi`, `prime_agent` | `command`, `resume_across_turns` (accepted for all; inert on gemini — the gemini backend has no resume support), `turn_timeout_ms`, `read_timeout_ms`, `stall_timeout_ms` |

### 1.6 Session Identity & Isolation
- Session identity is scoped by `(ticket_id, backend_kind, profile_name)`.
- Transitioning between stages with different profiles (even on the same backend kind, e.g. `codex/sol` -> `codex/luna`) starts a clean, isolated session without cross-contaminating session state.

### 1.7 Migration Guide for Existing Users
- Existing `WORKFLOW.md` files using only `agent.kind` or `agent.stage_kinds` continue to work without any changes.
- To migrate to profiles:
  1. Add `agent_profiles:` defining reusable profiles with specific models/settings.
  2. Replace `agent.stage_kinds:` with `agent.stage_profiles:` for fine-grained stage routing.
  3. Optionally set `agent.default_profile:` as the baseline profile for unmapped stages.

---

## 2. §20 Acceptance Test Specification

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

### Expected Resolution Mapping:
- **Research**: `Claude / fable` (command: `claude -p --output-format stream-json --verbose --model fable`)
- **Plan**: `Codex / sol` (reasoning: `high`, command: `codex app-server`)
- **Build**: `Claude / sonnet` (command: `claude -p --output-format stream-json --verbose --model sonnet`)
- **Review**: `Codex / sol` (reasoning: `high`, command: `codex app-server`)
- **QA**: `Codex / luna` (reasoning: `medium`, command: `codex app-server`)

---

## 3. Backward Compatibility Specification

```yaml
agent:
  kind: claude
  stage_kinds:
    Build: claude
    Review: codex
```

### Expected Resolution Mapping:
- **Build**: `Claude` (profile: `None`, model: default, command: default)
- **Review**: `Codex` (profile: `None`, model: default, command: default)
- **Other stages**: `Claude` (profile: `None`, model: default, command: default)
