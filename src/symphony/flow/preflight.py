"""Dispatch-time checks that need the backend layer.

Split from `compiler.py` on purpose. Compilation answers "is this workflow
well-formed?" and depends on nothing but the definition and the filesystem,
which keeps it cheap enough to run in the CLI validator and the web editor.
These checks answer "can *this host, right now* run it?" — a question whose
answer changes when config changes, and which requires importing the
backend package.

Everything here fails closed. A workflow that asks for a capability the
selected backend lacks blocks dispatch with the node id and backend name,
rather than starting and discovering the problem three nodes in.
"""

from __future__ import annotations

from ..backends import capabilities_for, missing_capabilities
from ..errors import BackendCapabilityMissing
from ..workflow import ServiceConfig
from . import statuses as st
from .model import BACKEND_INHERIT, CONTEXT_CONTINUE, CompiledWorkflow


def resolve_node_backend(
    node_backend: str, *, ticket_backend: str | None, service_default: str
) -> str:
    """Backend precedence for one agent node (PRD §8.3).

    1. the node's explicit `backend`, when not `inherit`
    2. the ticket's `agent.kind` override
    3. the service default

    Auto-triage sits between 2 and 3 in the PRD, but it resolves into the
    ticket override before dispatch, so by the time a node asks there are
    only three levels left. A node-level choice never mutates the ticket's
    default — the next node inherits from the ticket, not from its sibling.
    """
    if node_backend and node_backend != BACKEND_INHERIT:
        return node_backend
    if ticket_backend:
        return ticket_backend
    return service_default


def validate_backends(
    compiled: CompiledWorkflow,
    cfg: ServiceConfig,
    *,
    ticket_backend: str | None = None,
) -> None:
    """Check every agent node's backend can do what the node requires.

    Raises `BackendCapabilityMissing` naming the first node that cannot
    run, with the capability and backend involved.
    """
    for node in compiled.nodes:
        if node.type != st.NODE_TYPE_AGENT:
            continue
        kind = resolve_node_backend(
            node.backend,
            ticket_backend=ticket_backend,
            service_default=cfg.agent.kind,
        )
        capabilities = capabilities_for(cfg, kind)
        required: set[str] = set()
        if node.context == CONTEXT_CONTINUE:
            required.add("session_resume")
        missing = missing_capabilities(frozenset(required), capabilities)
        if missing:
            raise BackendCapabilityMissing(
                f"node {node.id!r} requires {', '.join(missing)}, which backend "
                f"{kind!r} does not provide",
                node=node.id,
                backend=kind,
                missing=list(missing),
                workflow=compiled.name,
            )


def parallel_safe_nodes(
    compiled: CompiledWorkflow, cfg: ServiceConfig, *, ticket_backend: str | None = None
) -> frozenset[str]:
    """Read nodes whose backend can genuinely be held to read-only access.

    A node declaring `workspace_access: read` is a statement of intent, not
    an enforcement mechanism. Only nodes in this set may run concurrently;
    everything else takes the exclusive workspace lock even when the
    workflow asked for parallelism (PRD §9.3).

    In this release the set is empty for every backend, because no adapter
    is launched with a read-only sandbox yet. It is computed rather than
    hardcoded so that wiring one backend's sandbox flag is the only change
    needed to enable parallel review.
    """
    safe: set[str] = set()
    for node in compiled.nodes:
        if node.type != st.NODE_TYPE_AGENT or node.workspace_access != st.ACCESS_READ:
            continue
        kind = resolve_node_backend(
            node.backend,
            ticket_backend=ticket_backend,
            service_default=cfg.agent.kind,
        )
        if capabilities_for(cfg, kind).enforce_read_only_workspace:
            safe.add(node.id)
    return frozenset(safe)
