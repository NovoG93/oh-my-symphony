"""Board dependency-graph validation shared by the board CLI and the web API.

Dependency-light on purpose: only the Issue model and typed errors, so both
`symphony.cli.board` and `symphony.webapi` apply identical rules when a
ticket is created or updated with `blocked_by` edges.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from ..errors import BoardDependencyError
from ..issue import Issue


def blocker_ids(issue: Issue) -> list[str]:
    """Blocker identifiers for one issue, in frontmatter order."""
    out: list[str] = []
    for blocker in issue.blocked_by:
        ident = blocker.identifier or blocker.id
        if ident and ident not in out:
            out.append(ident)
    return out


def board_edges(issues: Sequence[Issue]) -> dict[str, tuple[str, ...]]:
    """``identifier -> blocker identifiers`` for every board ticket."""
    return {issue.identifier: tuple(blocker_ids(issue)) for issue in issues}


def dangling_blockers(issues: Sequence[Issue]) -> dict[str, list[str]]:
    """``identifier -> blocker ids that do not exist on the board``."""
    known = {issue.identifier for issue in issues}
    out: dict[str, list[str]] = {}
    for issue in issues:
        missing = [b for b in blocker_ids(issue) if b not in known]
        if missing:
            out[issue.identifier] = missing
    return out


def find_cycle(edges: Mapping[str, Sequence[str]]) -> list[str] | None:
    """Return one dependency cycle as ``[a, b, ..., a]``, or None.

    Dangling targets (not present as keys) cannot close a cycle and are
    skipped.
    """
    visiting: set[str] = set()
    done: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> list[str] | None:
        visiting.add(node)
        path.append(node)
        for dep in edges.get(node, ()):
            if dep not in edges:
                continue
            if dep in visiting:
                return path[path.index(dep) :] + [dep]
            if dep not in done:
                found = dfs(dep)
                if found is not None:
                    return found
        visiting.discard(node)
        done.add(node)
        path.pop()
        return None

    for node in sorted(edges):
        if node not in done:
            found = dfs(node)
            if found is not None:
                return found
    return None


def _find_cycle_through(
    edges: Mapping[str, Sequence[str]], node: str
) -> list[str] | None:
    """Return a cycle passing through ``node``, or None.

    Only cycles that involve the edited ticket are reported, so a
    pre-existing cycle elsewhere on a hand-edited board never blocks
    unrelated ticket writes.
    """
    path = [node]
    visited: set[str] = set()

    def dfs(current: str) -> list[str] | None:
        for dep in edges.get(current, ()):
            if dep == node:
                return [*path, node]
            if dep in visited or dep not in edges:
                continue
            visited.add(dep)
            path.append(dep)
            found = dfs(dep)
            if found is not None:
                return found
            path.pop()
        return None

    return dfs(node)


def topological_order(edges: Mapping[str, Sequence[str]]) -> list[str]:
    """Blockers-first order (ties broken by identifier). Caller must have
    rejected cycles via :func:`find_cycle` first; cyclic leftovers are
    appended in identifier order so output never silently drops tickets."""
    remaining = {node: {d for d in deps if d in edges} for node, deps in edges.items()}
    order: list[str] = []
    while remaining:
        ready = sorted(n for n, deps in remaining.items() if not deps)
        if not ready:
            order.extend(sorted(remaining))
            break
        for node in ready:
            order.append(node)
            del remaining[node]
        for deps in remaining.values():
            deps.difference_update(ready)
    return order


def validate_ticket_dependencies(
    issues: Sequence[Issue],
    *,
    identifier: str | None,
    blocked_by: Sequence[str],
    new_ticket: bool,
) -> None:
    """Reject a ticket write that would break the board dependency DAG.

    Rules (identical for CLI and web API):
      * a new ticket id must be unique on the board,
      * every ``blocked_by`` target must already exist on the board,
      * the edges must keep the graph acyclic (checked through the edited
        ticket, so pre-existing unrelated cycles do not block the write).

    ``identifier=None`` means "id will be freshly generated": nothing can
    reference it yet, so only blocker existence is checked.
    """
    known = {issue.identifier for issue in issues}
    if new_ticket and identifier is not None and identifier in known:
        raise BoardDependencyError(
            "ticket already exists", identifier=identifier
        )
    missing = sorted(set(blocked_by) - known)
    if missing:
        raise BoardDependencyError(
            f"unknown blocked_by target(s): {', '.join(missing)}",
            identifier=identifier or "<new>",
        )
    if identifier is None or not blocked_by:
        return
    edges = board_edges(issues)
    edges[identifier] = tuple(blocked_by)
    cycle = _find_cycle_through(edges, identifier)
    if cycle is not None:
        raise BoardDependencyError(
            f"blocked_by would create a dependency cycle: {' -> '.join(cycle)}",
        )
