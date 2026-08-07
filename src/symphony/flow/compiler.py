"""Turn a decoded `WorkflowDefinition` into an immutable execution plan.

Everything that needs the whole graph or the filesystem lives here: cycle
detection, dangling dependencies, variable ancestry, prompt-file existence,
capability requirements, and the content hash a run pins itself to.

Compilation is total — it either produces a plan the executor can run
without further validation, or it raises with every reason it could not.
The executor is therefore allowed to assume its plan is well-formed; there
are no defensive re-checks scattered through the hot path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..errors import WorkflowDefinitionInvalid
from . import statuses as st
from .model import (
    BACKEND_INHERIT,
    CONTEXT_CONTINUE,
    CompiledWorkflow,
    Diagnostic,
    NodeDefinition,
    RiskSummary,
    WorkflowDefinition,
)
from .prompts import extract_references, is_known_variable


# Capability names an agent node can require of its backend. Mirrors the
# fields of `BackendCapabilities`; kept as strings so the compiler does not
# import the backend layer.
CAP_SESSION_RESUME = "session_resume"
CAP_READ_ONLY_WORKSPACE = "enforce_read_only_workspace"


def compile_workflow(
    definition: WorkflowDefinition,
    *,
    workflow_dir: Path,
    max_parallel_nodes: int | None = None,
) -> CompiledWorkflow:
    """Validate the graph and derive the execution plan.

    `workflow_dir` roots `prompt_file` lookups; a prompt path that escapes
    it is rejected rather than resolved, so a workflow file cannot read
    arbitrary host files into a prompt.
    """
    diagnostics: list[Diagnostic] = []
    node_by_id = {node.id: node for node in definition.nodes}

    _check_dependencies_exist(definition, node_by_id, diagnostics)
    layers = _topological_layers(definition, node_by_id, diagnostics)
    ancestors = _transitive_ancestors(definition, node_by_id)
    _check_unreachable(definition, diagnostics)
    _check_prompt_files(definition, workflow_dir, diagnostics)
    _check_variable_references(definition, node_by_id, ancestors, diagnostics)
    _check_evidence_references(definition, node_by_id, ancestors, diagnostics)
    _check_parallelism(definition, max_parallel_nodes, diagnostics)

    if diagnostics:
        _raise(diagnostics, definition)

    normalized = json.dumps(
        definition.to_json(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    workflow_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    return CompiledWorkflow(
        definition=definition,
        workflow_hash=workflow_hash,
        normalized_json=normalized,
        layers=layers,
        ancestors=ancestors,
        node_by_id=node_by_id,
        required_capabilities=_required_capabilities(definition),
        required_backends=frozenset(
            node.backend
            for node in definition.nodes
            if node.type == st.NODE_TYPE_AGENT and node.backend != BACKEND_INHERIT
        ),
        risk=_risk_summary(definition, ancestors),
    )


def _check_dependencies_exist(
    definition: WorkflowDefinition,
    node_by_id: dict[str, NodeDefinition],
    diagnostics: list[Diagnostic],
) -> None:
    for index, node in enumerate(definition.nodes):
        for position, dependency in enumerate(node.depends_on):
            if dependency not in node_by_id:
                diagnostics.append(
                    Diagnostic(
                        path=f"nodes[{index}].depends_on[{position}]",
                        message=f"depends on unknown node {dependency!r}",
                    )
                )


def _topological_layers(
    definition: WorkflowDefinition,
    node_by_id: dict[str, NodeDefinition],
    diagnostics: list[Diagnostic],
) -> tuple[tuple[str, ...], ...]:
    """Kahn's algorithm, grouped by layer.

    Layers are what makes bounded parallelism expressible later: every node
    in one layer has all its dependencies satisfied by earlier layers, so a
    scheduler can consider them together without recomputing readiness.
    """
    remaining = {
        node.id: {dep for dep in node.depends_on if dep in node_by_id}
        for node in definition.nodes
    }
    layers: list[tuple[str, ...]] = []
    while remaining:
        ready = sorted(
            node_id for node_id, deps in remaining.items() if not deps
        )
        if not ready:
            cycle = sorted(remaining)
            diagnostics.append(
                Diagnostic(
                    path="nodes",
                    message=(
                        "dependency cycle among nodes: " + ", ".join(cycle)
                    ),
                )
            )
            return ()
        layers.append(tuple(ready))
        for node_id in ready:
            del remaining[node_id]
        for deps in remaining.values():
            deps.difference_update(ready)
    return tuple(layers)


def _transitive_ancestors(
    definition: WorkflowDefinition, node_by_id: dict[str, NodeDefinition]
) -> dict[str, frozenset[str]]:
    """Every node reachable by following `depends_on` upwards.

    Computed iteratively with a visited set so a cyclic graph — which has
    already been reported by this point — terminates instead of recursing
    forever.
    """
    resolved: dict[str, frozenset[str]] = {}

    def ancestors_of(node_id: str) -> frozenset[str]:
        if node_id in resolved:
            return resolved[node_id]
        collected: set[str] = set()
        stack = [
            dep for dep in node_by_id[node_id].depends_on if dep in node_by_id
        ]
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            collected.add(current)
            stack.extend(
                dep for dep in node_by_id[current].depends_on if dep in node_by_id
            )
        resolved[node_id] = frozenset(collected)
        return resolved[node_id]

    return {node.id: ancestors_of(node.id) for node in definition.nodes}


def _check_unreachable(
    definition: WorkflowDefinition, diagnostics: list[Diagnostic]
) -> None:
    """Flag extra root nodes beyond the first.

    A second root is almost always a forgotten `depends_on` rather than a
    deliberate parallel entry point, and in v1 — which runs sequentially —
    it silently changes what "the workflow ran" means.
    """
    roots = [node.id for node in definition.nodes if not node.depends_on]
    if len(roots) <= 1:
        return
    diagnostics.append(
        Diagnostic(
            path="nodes",
            message=(
                "multiple root nodes with no dependencies: "
                + ", ".join(sorted(roots))
                + "; give each a `depends_on` unless they are genuinely "
                "independent entry points"
            ),
        )
    )


def _check_prompt_files(
    definition: WorkflowDefinition, workflow_dir: Path, diagnostics: list[Diagnostic]
) -> None:
    root = workflow_dir.resolve()
    for index, node in enumerate(definition.nodes):
        if not node.prompt_file:
            continue
        path = f"nodes[{index}].prompt_file"
        candidate = Path(node.prompt_file)
        if candidate.is_absolute():
            diagnostics.append(
                Diagnostic(path=path, message="must be relative to the workflow file")
            )
            continue
        resolved = (root / candidate).resolve()
        if not _is_within(resolved, root):
            diagnostics.append(
                Diagnostic(
                    path=path,
                    message="resolves outside the repository root",
                )
            )
            continue
        if not resolved.is_file():
            diagnostics.append(
                Diagnostic(path=path, message=f"file not found: {node.prompt_file}")
            )


def _check_variable_references(
    definition: WorkflowDefinition,
    node_by_id: dict[str, NodeDefinition],
    ancestors: dict[str, frozenset[str]],
    diagnostics: list[Diagnostic],
) -> None:
    for index, node in enumerate(definition.nodes):
        if node.type != st.NODE_TYPE_AGENT or not node.prompt:
            # `prompt_file` contents are checked at render time; their
            # references cannot be validated without reading the file,
            # which `_check_prompt_files` has only confirmed exists.
            continue
        path = f"nodes[{index}].prompt"
        for ref in extract_references(node.prompt):
            if not is_known_variable(ref):
                diagnostics.append(
                    Diagnostic(
                        path=path,
                        message=(
                            f"unknown variable ${{{ref.expression}}}; v1 supports "
                            "ticket.id, ticket.identifier, ticket.title, "
                            "ticket.description, ticket.labels, run.id, "
                            "run.workspace, nodes.<id>.output, "
                            "nodes.<id>.artifact_dir"
                        ),
                    )
                )
                continue
            if not ref.is_node_reference or ref.node_id is None:
                continue
            if ref.node_id not in node_by_id:
                diagnostics.append(
                    Diagnostic(
                        path=path,
                        message=f"references unknown node {ref.node_id!r}",
                    )
                )
            elif ref.node_id not in ancestors.get(node.id, frozenset()):
                diagnostics.append(
                    Diagnostic(
                        path=path,
                        message=(
                            f"references {ref.node_id!r}, which is not a "
                            "dependency; add it to depends_on so the value is "
                            "guaranteed to exist when this node runs"
                        ),
                    )
                )


def _check_evidence_references(
    definition: WorkflowDefinition,
    node_by_id: dict[str, NodeDefinition],
    ancestors: dict[str, frozenset[str]],
    diagnostics: list[Diagnostic],
) -> None:
    for index, node in enumerate(definition.nodes):
        if node.type != st.NODE_TYPE_APPROVAL:
            continue
        for position, evidence_id in enumerate(node.evidence):
            path = f"nodes[{index}].evidence[{position}]"
            if evidence_id not in node_by_id:
                diagnostics.append(
                    Diagnostic(path=path, message=f"unknown node {evidence_id!r}")
                )
            elif evidence_id not in ancestors.get(node.id, frozenset()):
                diagnostics.append(
                    Diagnostic(
                        path=path,
                        message=(
                            f"{evidence_id!r} is not a dependency of this gate; "
                            "an approver must not be shown evidence that may "
                            "not have run yet"
                        ),
                    )
                )


def _check_parallelism(
    definition: WorkflowDefinition,
    max_parallel_nodes: int | None,
    diagnostics: list[Diagnostic],
) -> None:
    if max_parallel_nodes is None:
        return
    requested = definition.defaults.max_parallel_nodes
    if requested > max_parallel_nodes:
        diagnostics.append(
            Diagnostic(
                path="defaults.max_parallel_nodes",
                message=(
                    f"{requested} exceeds the service maximum of "
                    f"{max_parallel_nodes}"
                ),
            )
        )


def _required_capabilities(definition: WorkflowDefinition) -> frozenset[str]:
    required: set[str] = set()
    for node in definition.nodes:
        if node.type == st.NODE_TYPE_AGENT and node.context == CONTEXT_CONTINUE:
            required.add(CAP_SESSION_RESUME)
    return frozenset(required)


def _risk_summary(
    definition: WorkflowDefinition, ancestors: dict[str, frozenset[str]]
) -> RiskSummary:
    approval_ids = {
        node.id for node in definition.nodes if node.type == st.NODE_TYPE_APPROVAL
    }
    ungated = tuple(
        node.id
        for node in definition.nodes
        if node.external_side_effects
        and not (ancestors.get(node.id, frozenset()) & approval_ids)
    )
    return RiskSummary(
        shell_node_ids=tuple(
            node.id for node in definition.nodes if node.type == st.NODE_TYPE_SHELL
        ),
        external_side_effect_node_ids=tuple(
            node.id for node in definition.nodes if node.external_side_effects
        ),
        write_node_ids=tuple(
            node.id
            for node in definition.nodes
            if node.is_executable and node.workspace_access == st.ACCESS_WRITE
        ),
        ungated_external_node_ids=ungated,
    )


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _raise(diagnostics: list[Diagnostic], definition: WorkflowDefinition) -> None:
    source = str(definition.source_path)
    summary = "; ".join(d.render() for d in diagnostics[:3])
    if len(diagnostics) > 3:
        summary += f"; (+{len(diagnostics) - 3} more)"
    raise WorkflowDefinitionInvalid(
        summary,
        source=source,
        workflow=definition.name,
        diagnostics=tuple(diagnostics),
    )
