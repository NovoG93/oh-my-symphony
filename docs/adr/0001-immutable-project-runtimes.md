# ADR 0001: Project switching navigates immutable project runtimes

- Status: Accepted
- Date: 2026-08-08

## Context

Operators need to create, adopt, inspect, and switch between multiple local Symphony projects from the web interface. Each project can have active workers, workspaces, Git operations, chat sessions, statistics, and Product Preview state.

Retargeting a live orchestrator to a different path would mix the old workers and runtime state with a new board and repository. A central single-port proxy would also need to rewrite static, API, and WebSocket traffic across independent services.

## Decision

A registered Project is an immutable binding to one repository, workflow, board, and service endpoint. Project switching starts the selected service when necessary and navigates the browser to that service. Existing project services and workers continue unchanged.

Project creation and adoption are control-plane operations. Missing paths are created and initialized as Git repositories. Existing directories are preserved, existing Git metadata is reused, and only missing Symphony bootstrap files are added. Existing files and unrelated Git changes are never overwritten or staged.

## Consequences

- Multiple projects and their workers can run concurrently without path races.
- The browser URL changes when switching projects because each service remains independent.
- Project paths are displayed but not edited in place; operators create/adopt another project and switch.
- Filesystem mutations remain protected by loopback host validation, JSON-only mutation requests, repository containment, and source-repository refusal.
