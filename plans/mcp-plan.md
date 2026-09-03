# Symphony MCP Integration Plan

## Goal

Build a small standalone `symphony-mcp` service that allows **Hermes** to control and supervise **oh-my-symphony** through a safe, semantic **Streamable HTTP MCP** interface.

The integration should preserve a clear separation of responsibilities:

- **Hermes** decides *what* should be done.
- **oh-my-symphony** decides *how* the engineering workflow executes.
- **Claude Code, Codex, Antigravity/AGY, Gemini, etc.** perform implementation, review, QA, or documentation stages.
- **symphony-mcp** acts only as an integration, policy, validation, and normalization layer.

Hermes should not directly invoke Claude, Codex, or AGY for workflows delegated to Symphony.

---

# 1. Target Architecture

```text
┌───────────────────────────────┐
│            Hermes             │
│                               │
│ planning / prioritization     │
│ project supervision           │
└───────────────┬───────────────┘
                │
        Streamable HTTP MCP
                │
                ▼
┌───────────────────────────────┐
│        symphony-mcp           │
│                               │
│ semantic MCP tools            │
│ validation                    │
│ authorization / policy        │
│ response normalization        │
│ audit logging                 │
└───────────────┬───────────────┘
                │
          local REST API
                │
                ▼
┌───────────────────────────────┐
│       oh-my-symphony          │
│                               │
│ task DAGs                     │
│ scheduling                    │
│ retries                       │
│ worktrees                     │
│ stage routing                 │
└──────┬────────┬────────┬──────┘
       │        │        │
       ▼        ▼        ▼
    Claude    Codex     AGY
     Build    Review     QA
```

---

# 2. Core Design Principles

1. **Do not reimplement Symphony.**
   - No custom scheduler.
   - No custom retry engine.
   - No custom worktree manager.
   - No custom task database.

2. **Do not expose Symphony's raw REST API 1:1 through MCP.**
   - Hermes should use semantic tools such as `symphony_create_request`.
   - Avoid generic tools such as `http_post` or `update_issue`.

3. **Keep agent routing inside oh-my-symphony.**
   - Hermes chooses the goal/workflow.
   - Symphony chooses the execution agent for each stage.

4. **Treat the MCP service as a security boundary.**
   - Hermes should not need direct network access to Symphony's control API.
   - Only explicitly allowed operations should be exposed.

5. **Prefer stateless, idempotent operations.**
   - Especially important for LLM callers and network retries.

---

# 3. Initial MCP Tool Contract

Start with a deliberately small tool surface.

## Read tools

```text
symphony_list_projects
symphony_get_project
symphony_get_board
symphony_get_request
symphony_get_request_schedule
symphony_get_task
symphony_get_run
```

## Write/control tools

```text
symphony_create_request
symphony_pause_task
symphony_resume_task
```

The primary tool for Hermes should be:

```text
symphony_create_request
```

Example logical input:

```json
{
  "project": "homelab",
  "objective": "Add Renovate PR validation using k3s",
  "description": "Validate Helm chart updates before Renovate PRs can merge.",
  "acceptance_criteria": [
    "CI creates an ephemeral Kubernetes cluster",
    "Helm charts install successfully",
    "all workloads become Ready",
    "failure blocks the PR"
  ],
  "priority": "normal",
  "workflow": "deep"
}
```

Hermes should express intent rather than manually creating every task in the Symphony DAG.

---

# 4. Repository Structure

Recommended standalone repository:

```text
symphony-mcp/
├── pyproject.toml
├── README.md
├── config.example.yaml
├── src/
│   └── symphony_mcp/
│       ├── __init__.py
│       ├── server.py
│       ├── config.py
│       ├── client.py
│       ├── models.py
│       ├── policy.py
│       ├── auth.py
│       ├── audit.py
│       ├── idempotency.py
│       ├── errors.py
│       └── tools/
│           ├── projects.py
│           ├── requests.py
│           ├── tasks.py
│           └── runs.py
└── tests/
    ├── unit/
    ├── integration/
    └── fixtures/
```

---

# 5. Phase 1 — MCP Server Foundation

Implement a standalone MCP server using the official MCP SDK.

Expose:

```text
/mcp
```

using **Streamable HTTP**.

Example configuration:

```yaml
server:
  host: 0.0.0.0
  port: 8080

symphony:
  base_url: http://127.0.0.1:9999
  timeout_seconds: 30

auth:
  bearer_token_file: /run/secrets/hermes-mcp-token

policy:
  allowed_projects:
    - homelab
    - agenticos

  allow_create_requests: true
  allow_pause_resume: true
  allow_delete: false
  allow_git_operations: false
  allow_workflow_changes: false
```

## Deliverables

- MCP process starts successfully.
- `/mcp` is reachable.
- Health/readiness endpoint is available.
- Authentication is enforced.
- Hermes can discover the MCP server.

---

# 6. Phase 2 — Typed Symphony Client

Create a dedicated client layer responsible for all interaction with oh-my-symphony.

Example API:

```python
class SymphonyClient:
    async def list_projects(self): ...
    async def get_project(self, project_id): ...

    async def create_request(
        self,
        project_id,
        objective,
        description,
        acceptance_criteria,
        priority,
        workflow,
    ): ...

    async def get_request(self, request_id): ...
    async def get_request_schedule(self, request_id): ...

    async def get_task(self, task_id): ...
    async def get_run(self, run_id): ...

    async def pause_task(self, task_id): ...
    async def resume_task(self, task_id): ...
```

The client should handle:

- HTTP timeouts
- retries for safe/idempotent requests
- JSON decoding
- error translation
- response validation
- future Symphony API changes

The MCP tool layer should not know raw Symphony endpoint paths.

---

# 7. Phase 3 — Normalized Domain Models

Do not return raw Symphony API responses directly to Hermes.

Define normalized models such as:

```python
class RequestStatus:
    id: str
    objective: str
    status: str
    completed_tasks: int
    running_tasks: int
    blocked_tasks: int
    failed_tasks: int
    tasks: list[TaskSummary]
```

Example response exposed to Hermes:

```json
{
  "request_id": "REQ-42",
  "status": "running",
  "progress": {
    "completed": 3,
    "running": 1,
    "blocked": 1,
    "failed": 0,
    "total": 5
  },
  "tasks": [
    {
      "id": "PLAN-42",
      "stage": "Plan",
      "status": "completed",
      "agent": "claude"
    },
    {
      "id": "BUILD-42",
      "stage": "Build",
      "status": "completed",
      "agent": "claude"
    },
    {
      "id": "QA-42",
      "stage": "QA",
      "status": "running",
      "agent": "agy"
    },
    {
      "id": "VERIFY-42",
      "stage": "Verify",
      "status": "blocked",
      "agent": "codex"
    }
  ]
}
```

Hermes should receive a simple execution model rather than Symphony implementation details.

---

# 8. Phase 4 — Implement `symphony_create_request`

This is the most important feature and should be the first complete end-to-end workflow.

## Flow

```text
Hermes
   │
   │ symphony_create_request()
   ▼
symphony-mcp
   │
   ├─ authenticate caller
   ├─ validate project
   ├─ validate request
   ├─ enforce policy
   ├─ apply idempotency
   │
   ▼
oh-my-symphony
   │
   ├─ create request
   ├─ generate task DAG
   ├─ assign stages
   └─ queue runnable tasks
```

Recommended input:

```text
project: str
objective: str
description: str | None
acceptance_criteria: list[str]

priority:
  low
  normal
  high
  critical

workflow:
  default
  simple
  deep
```

Do **not** initially allow Hermes to select the concrete agent backend.

Agent selection should remain Symphony policy.

---

# 9. Phase 5 — Configure Agent Routing in oh-my-symphony

Use Symphony's per-stage routing.

Conceptual example:

```yaml
agent:
  kind: claude

  stage_kinds:
    Research: claude
    Plan: claude
    "Plan Review": codex
    Build: claude
    QA: agy
    Verify: codex
    Document: gemini
```

Result:

```text
Hermes
  │
  │ create request
  ▼
Symphony
  │
  ├── Research ───────── Claude
  ├── Plan ───────────── Claude
  ├── Plan Review ────── Codex
  ├── Build ──────────── Claude
  ├── QA ─────────────── AGY
  ├── Verify ─────────── Codex
  └── Documentation ──── Gemini
```

Hermes remains independent of:

- CLI syntax
- model IDs
- agent credentials
- backend-specific settings

---

# 10. Phase 6 — Supervision Tools

Add tools that allow Hermes to supervise delegated work.

Priority order:

```text
symphony_get_request
symphony_get_request_schedule
symphony_get_task
symphony_get_run
```

`symphony_get_request` should be the main aggregated status tool.

Example use cases:

- "How is feature X progressing?"
- "Why is this request blocked?"
- "Which stage failed?"
- "Which agent is currently running?"
- "Has QA finished?"
- "Is the work ready for human approval?"

Lower-level task/run tools are only needed for drill-down and diagnosis.

---

# 11. Phase 7 — Safe Control Operations

Expose only:

```text
symphony_pause_task
symphony_resume_task
```

Do not expose in v1:

```text
delete task
delete project
merge PR
push branch
workflow modification
agent command modification
raw shell
arbitrary git commands
```

Recommended permission philosophy:

```text
READ
  broad

CREATE
  moderately restricted

CONTROL
  restricted

DELETE / CONFIGURE / SHELL
  unavailable
```

---

# 12. Phase 8 — Authentication

At minimum use bearer-token authentication:

```http
Authorization: Bearer <token>
```

Prefer runtime secret injection rather than storing the token directly in Hermes' persistent configuration.

Recommended network layout:

```text
Hermes
   │
   │ allowed
   ▼
symphony-mcp :8080

Hermes
   X
   │ denied
   ▼
oh-my-symphony :9999
```

Only the MCP gateway should be able to reach Symphony's API.

If possible, also prevent external network access to the Symphony control API.

---

# 13. Phase 9 — Project-Level RBAC

Add explicit client permissions.

Example:

```yaml
clients:

  hermes:
    projects:
      - homelab
      - agenticos

    permissions:
      - request:create
      - request:read
      - task:read
      - run:read
      - task:pause
      - task:resume

  monitoring-agent:
    projects:
      - "*"

    permissions:
      - request:read
      - task:read
      - run:read
```

The MCP service should reject actions outside the caller's allowed projects or permissions.

---

# 14. Phase 10 — Idempotency

LLM callers may retry a tool call if a response is lost.

Without idempotency:

```text
create request
response lost
retry

REQ-42 created
REQ-43 created
```

Support a caller-supplied key:

```json
{
  "client_request_id": "hermes-7b221...",
  "project": "homelab",
  "objective": "..."
}
```

Store:

```text
hermes-7b221... -> REQ-42
```

A retry should return the existing request rather than create a duplicate.

This should be considered essential before enabling autonomous use.

---

# 15. Phase 11 — Audit Logging

Log all mutating operations.

Example:

```json
{
  "timestamp": "2026-08-14T21:00:00+02:00",
  "client": "hermes",
  "tool": "symphony_create_request",
  "project": "homelab",
  "request_id": "REQ-42",
  "result": "success"
}
```

Do not log:

- API keys
- access tokens
- environment secrets
- model credentials
- complete secret-bearing command lines

Audit logs should make it possible to answer:

- Who created this request?
- Which MCP tool was used?
- Which project was affected?
- Was the action successful?
- Was a request retried?

---

# 16. Phase 12 — Hermes Integration

Configure the MCP endpoint in Hermes and provide a dedicated orchestration skill/instruction.

Recommended behavior:

```text
When software engineering work requires autonomous execution:

1. Define the objective and acceptance criteria.
2. Use symphony_create_request to delegate the work.
3. Do not directly invoke coding agents for work managed by Symphony.
4. Use symphony_get_request to inspect overall progress.
5. Drill into tasks or runs only when failures require investigation.
6. Avoid repeatedly polling requests without a reason.
7. Pause work if execution is clearly proceeding in the wrong direction.
8. Escalate to the user when human approval or a product decision is required.
```

This instruction is part of the architecture, not just documentation.

---

# 17. Phase 13 — Unit Tests

Mock the Symphony API and test:

- request validation
- authentication
- RBAC
- error mapping
- normalized responses
- retry behavior
- idempotency
- project restrictions
- pause/resume permissions
- malformed API responses

Example:

```text
MCP call
   ↓
mock Symphony HTTP response
   ↓
normalized MCP response
```

---

# 18. Phase 14 — Integration Tests

Run real:

```text
symphony-mcp
      +
oh-my-symphony
```

Use simple/dummy agents if needed.

Test:

```text
create request
   ↓
request appears in Symphony
   ↓
DAG is generated
   ↓
task transitions occur
   ↓
status is returned through MCP
```

Also verify that Hermes cannot directly access the Symphony API if the deployment is intended to enforce that boundary.

---

# 19. Phase 15 — Full End-to-End Test

Use a trivial repository and real agents.

Example task:

> Add `/health` returning `{"status":"ok"}` and add tests.

Expected execution:

```text
Hermes
 ↓
symphony_create_request
 ↓
oh-my-symphony
 ↓
Claude — implementation
 ↓
Codex — review
 ↓
AGY — QA
 ↓
Hermes — get_request
 ↓
user receives final status
```

The full flow should succeed without Hermes directly invoking Claude, Codex, or AGY.

---

# 20. Phase 16 — Failure Testing

Deliberately test:

- Claude exits non-zero
- Codex crashes
- AGY times out
- Git conflict
- failing tests
- Symphony restart
- MCP restart
- lost MCP response
- duplicate create request
- Hermes-to-MCP network interruption
- MCP-to-Symphony network interruption
- invalid project
- invalid workflow
- request DAG blocked
- rate limit / concurrency exhaustion
- malformed Symphony response

Hermes should receive structured failures.

Example:

```json
{
  "status": "blocked",
  "reason": "dependency_failed",
  "failed_task": "QA-42",
  "retryable": true
}
```

Avoid exposing only generic errors such as:

```text
Internal Server Error
```

---

# 21. Phase 17 — Artifact Retrieval

Later, add:

```text
symphony_get_artifacts
```

Possible artifact types:

- pull request URL
- commit SHA
- test report
- code review summary
- screenshots
- coverage report
- generated documentation
- QA report

Example:

```json
{
  "artifacts": [
    {
      "type": "pull_request",
      "url": "...",
      "task": "BUILD-42"
    },
    {
      "type": "review",
      "task": "VERIFY-42",
      "summary": "No blocking issues found."
    }
  ]
}
```

This lets Hermes reason about outputs without direct filesystem access.

---

# 22. Phase 18 — Human Approval Gates

For sensitive operations such as merging, introduce explicit approval gates.

Example:

```text
Research
   ↓
Plan
   ↓
Plan Review
   ↓
Build
   ↓
QA
   ↓
Verify
   ↓
┌─────────────────────┐
│ Human approval gate │
└──────────┬──────────┘
           ↓
         Merge
```

Potential MCP tools:

```text
symphony_list_approvals
symphony_get_approval
```

If an approval action is later added:

```text
symphony_approve
```

Hermes should **not** automatically be given permission to approve its own work.

---

# 23. Phase 19 — Events / Notifications

Add event-driven updates only after the stateless workflow is proven.

Possible events:

```text
request.completed
request.blocked
task.failed
task.requires_human
approval.required
```

Possible flow:

```text
Symphony
   │
   │ event
   ▼
symphony-mcp
   │
   ▼
Hermes
```

Do not make persistent event streaming a requirement for v1.

The initial design should work reliably with:

```text
create_request
get_request
```

and explicit status queries.

---

# 24. Future Upstream Integration

After the standalone MCP adapter is proven, consider contributing MCP support directly to oh-my-symphony.

Possible internal structure:

```text
src/symphony/
├── service/
│   └── ...
├── webapi/
│   └── ...
└── mcp/
    ├── server.py
    └── tools.py
```

Both REST and MCP should call a shared service layer:

```text
           ┌── REST
           │
Service ───┤
           │
           └── MCP
```

Avoid having upstream MCP merely proxy its own HTTP API if both layers live in the same process.

Possible configuration:

```yaml
mcp:
  enabled: true
  host: 0.0.0.0
  port: 8766
```

---

# 25. Recommended Milestones

| Milestone | Scope | Result |
|---|---|---|
| M1 | MCP server + health + auth | Hermes can connect |
| M2 | Typed Symphony client | MCP can communicate with Symphony |
| M3 | `list_projects`, `get_board` | Read-only integration works |
| M4 | `create_request` | Hermes can delegate work |
| M5 | `get_request`, request schedule | Hermes can supervise |
| M6 | Task/run inspection | Hermes can diagnose failures |
| M7 | Pause/resume | Hermes can intervene |
| M8 | RBAC + audit + idempotency | Safe autonomous use |
| M9 | Hermes orchestration skill | Correct delegation behavior |
| M10 | Claude → Codex → AGY E2E | Full architecture proven |
| M11 | Artifact/PR retrieval | Hermes understands outputs |
| M12 | Human approval gates | Safe merge/deploy boundary |
| M13 | Events/notifications | Reduced polling |
| M14 | Upstream integration | Consider contributing MCP to oh-my-symphony |

---

# 26. MVP Scope

Do not overbuild v1.

The MVP should contain only:

```text
symphony_list_projects
symphony_create_request
symphony_get_request
symphony_get_task
symphony_get_run
```

Architecture:

```text
             Hermes
                │
               MCP
                │
                ▼
        ┌────────────────┐
        │ symphony-mcp   │
        │                │
        │ list_projects  │
        │ create_request │
        │ get_request    │
        │ get_task       │
        │ get_run        │
        └───────┬────────┘
                │
                ▼
          oh-my-symphony
```

Security requirements for MVP:

- authenticated MCP endpoint
- Hermes has no direct access to Symphony API
- project allowlist
- no delete/configuration/shell tools
- basic audit logs
- idempotent request creation

---

# 27. MVP Definition of Done

The first version is successful when the following interaction works:

User tells Hermes:

> Implement a small feature in repository X. Have it independently reviewed and tested.

Hermes then:

```text
1. Understands the requirement.
2. Defines objective and acceptance criteria.
3. Calls symphony_create_request().
4. Symphony creates the execution DAG.
5. Claude performs implementation.
6. Codex performs independent review.
7. AGY performs QA/verification.
8. Hermes calls symphony_get_request().
9. Hermes reports the real Symphony status and result to the user.
```

Constraints:

- Hermes never directly launches Claude, Codex, or AGY for the managed request.
- Hermes never writes directly to Symphony's task database or board.
- Hermes never requires shell access on the Symphony host.
- A duplicate MCP request does not create duplicate Symphony requests.
- Failures are surfaced as structured states.
- Sensitive final actions such as merge/deploy remain outside autonomous Hermes permission unless explicitly enabled later.

---

# 28. Final Recommended Responsibility Split

```text
Hermes
  Product owner / architect / supervisor
  Decides WHAT should happen.

symphony-mcp
  Integration and policy boundary
  Controls WHAT Hermes is allowed to ask Symphony to do.

oh-my-symphony
  Engineering execution control plane
  Decides HOW tasks are scheduled and executed.

Claude / Codex / AGY / Gemini
  Specialized engineering workers
  Perform implementation, review, QA, and documentation.

Human
  Retains approval over sensitive operations such as merge,
  deployment, production changes, or destructive actions.
```

This architecture keeps the custom code small while preserving the orchestration, workflow, agent routing, retry, and workspace functionality that already exists in oh-my-symphony.
