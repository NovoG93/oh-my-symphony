"""Run, node, and approval state vocabularies for governed workflow runs.

Kept in one dependency-free module because three layers need the same
strings and must not drift: the SQLite store, the executor's transition
guards, and the CLI/API/web presentation.

The transition tables are data, not code paths. `is_legal_run_transition`
is the single place that answers "may this run move from A to B", so an
illegal move fails at the boundary instead of corrupting the ledger.
"""

from __future__ import annotations


# --- run execution status (PRD §9.1) ---------------------------------------

RUN_CREATED = "created"
RUN_RUNNING = "running"
RUN_WAITING_APPROVAL = "waiting_approval"
RUN_NEEDS_ATTENTION = "needs_attention"
RUN_SUCCEEDED = "succeeded"
RUN_REJECTED = "rejected"
RUN_CANCELLED = "cancelled"
RUN_ABANDONED = "abandoned"

TERMINAL_RUN_STATUSES = frozenset(
    {RUN_SUCCEEDED, RUN_REJECTED, RUN_CANCELLED, RUN_ABANDONED}
)

# Statuses that must keep blocking redispatch. `needs_attention` and
# `waiting_approval` have no live process, which is exactly why the fence
# exists separately from the process lease.
FENCED_RUN_STATUSES = frozenset(
    {RUN_CREATED, RUN_RUNNING, RUN_WAITING_APPROVAL, RUN_NEEDS_ATTENTION}
)

_LEGAL_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    RUN_CREATED: frozenset({RUN_RUNNING, RUN_CANCELLED, RUN_ABANDONED}),
    RUN_RUNNING: frozenset(
        {
            RUN_WAITING_APPROVAL,
            RUN_NEEDS_ATTENTION,
            RUN_SUCCEEDED,
            RUN_REJECTED,
            RUN_CANCELLED,
            RUN_ABANDONED,
        }
    ),
    RUN_WAITING_APPROVAL: frozenset(
        {
            RUN_RUNNING,
            RUN_REJECTED,
            RUN_NEEDS_ATTENTION,
            RUN_CANCELLED,
            RUN_ABANDONED,
        }
    ),
    RUN_NEEDS_ATTENTION: frozenset({RUN_RUNNING, RUN_ABANDONED, RUN_CANCELLED}),
    RUN_SUCCEEDED: frozenset(),
    RUN_REJECTED: frozenset(),
    RUN_CANCELLED: frozenset(),
    RUN_ABANDONED: frozenset(),
}


# --- attention reasons (PRD §14.1) -----------------------------------------

ATTENTION_NODE_FAILED = "node_failed"
ATTENTION_INTERRUPTED = "interrupted"
ATTENTION_INTEGRITY_FAILED = "integrity_failed"
ATTENTION_TICKET_STATE_CONFLICT = "ticket_state_conflict"

ATTENTION_REASONS = frozenset(
    {
        ATTENTION_NODE_FAILED,
        ATTENTION_INTERRUPTED,
        ATTENTION_INTEGRITY_FAILED,
        ATTENTION_TICKET_STATE_CONFLICT,
    }
)


# --- node status (PRD §9.2) ------------------------------------------------

NODE_PENDING = "pending"
NODE_READY = "ready"
NODE_RUNNING = "running"
NODE_WAITING_APPROVAL = "waiting_approval"
NODE_SUCCEEDED = "succeeded"
NODE_FAILED = "failed"
NODE_INTERRUPTED = "interrupted"
NODE_CANCELLED = "cancelled"
NODE_REJECTED = "rejected"
NODE_SKIPPED = "skipped"

TERMINAL_NODE_STATUSES = frozenset(
    {
        NODE_SUCCEEDED,
        NODE_FAILED,
        NODE_INTERRUPTED,
        NODE_CANCELLED,
        NODE_REJECTED,
        NODE_SKIPPED,
    }
)

# A node in one of these was mid-flight when the process died; startup
# reconciliation rewrites them to `interrupted`.
ACTIVE_NODE_STATUSES = frozenset({NODE_RUNNING})


# --- error classes (PRD §9.5) ----------------------------------------------

ERROR_FATAL = "fatal"
ERROR_TRANSIENT = "transient"
ERROR_UNKNOWN = "unknown"
ERROR_VALIDATION = "validation"
ERROR_CANCELLED = "cancelled"

ERROR_CLASSES = frozenset(
    {ERROR_FATAL, ERROR_TRANSIENT, ERROR_UNKNOWN, ERROR_VALIDATION, ERROR_CANCELLED}
)


# --- approval (PRD §11) ----------------------------------------------------

APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"

APPROVAL_DECISIONS = frozenset({APPROVAL_APPROVED, APPROVAL_REJECTED})


# --- fence reasons (PRD §14.3) ---------------------------------------------

FENCE_REASONS = frozenset({RUN_RUNNING, RUN_WAITING_APPROVAL, RUN_NEEDS_ATTENTION})


# --- execution mode (PRD §14.1) --------------------------------------------

MODE_LEGACY = "legacy_stage_loop"
MODE_GOVERNED = "governed_workflow"


# --- node types / workspace access (PRD §8.2) ------------------------------

NODE_TYPE_AGENT = "agent"
NODE_TYPE_SHELL = "shell"
NODE_TYPE_APPROVAL = "approval"

NODE_TYPES = frozenset({NODE_TYPE_AGENT, NODE_TYPE_SHELL, NODE_TYPE_APPROVAL})

ACCESS_READ = "read"
ACCESS_WRITE = "write"
ACCESS_NONE = "none"

WORKSPACE_ACCESS_VALUES = frozenset({ACCESS_READ, ACCESS_WRITE, ACCESS_NONE})


# --- artifact scopes (PRD §12.1) -------------------------------------------

SCOPE_RUNTIME = "runtime"
SCOPE_REPOSITORY = "repository"

ARTIFACT_SCOPES = frozenset({SCOPE_RUNTIME, SCOPE_REPOSITORY})


def is_terminal_run(status: str) -> bool:
    return status in TERMINAL_RUN_STATUSES


def is_legal_run_transition(current: str, target: str) -> bool:
    """Whether a run may move `current -> target`.

    A self-transition is legal so idempotent writes (a reconciliation that
    re-asserts the state it already found) do not have to special-case.
    """
    if current == target:
        return True
    return target in _LEGAL_RUN_TRANSITIONS.get(current, frozenset())
