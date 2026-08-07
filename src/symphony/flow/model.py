"""Immutable workflow definition and compilation types.

Two layers live here, and the distinction is load-bearing:

- `WorkflowDefinition` is *what the YAML said*, after decoding and field
  validation. It is a faithful, normalized transcription — no derived data.
- `CompiledWorkflow` is *what the executor needs*, derived once at dispatch:
  topological layers, transitive ancestry, the content hash, and the
  capability set the backends must satisfy.

A run stores the compiled hash and executes the stored snapshot, so editing
the YAML mid-run cannot change what a running ticket does. That property is
the reason compilation is a distinct step rather than something the
executor does lazily per node.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from . import statuses as st


# Compilation limits (PRD §8.7). Chosen to bound worst-case validation and
# UI cost, not because larger graphs are conceptually wrong.
MAX_NODES = 100
MAX_DEPENDENCIES_PER_NODE = 20
MAX_YAML_BYTES = 1024 * 1024

DEFAULT_NODE_TIMEOUT_SECONDS = 1800
DEFAULT_SHELL_TIMEOUT_SECONDS = 120
DEFAULT_AGENT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 3.0

BACKEND_INHERIT = "inherit"
CONTEXT_FRESH = "fresh"
CONTEXT_CONTINUE = "continue"

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Diagnostic:
    """One validation failure, addressed to a place in the source file."""

    path: str
    message: str
    line: int | None = None

    def render(self, source: str | None = None) -> str:
        location = source or ""
        if self.line is not None:
            location = f"{location}:{self.line}" if location else f"line {self.line}"
        prefix = f"{location} " if location else ""
        return f"{prefix}[{self.path}] {self.message}"


@dataclass(frozen=True)
class RetryPolicy:
    """Explicit retry configuration. Absent means "one attempt"."""

    max_attempts: int = 1
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS
    on: tuple[str, ...] = ()

    def allows(self, error_class: str) -> bool:
        return self.max_attempts > 1 and error_class in self.on

    def to_json(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "backoff_seconds": self.backoff_seconds,
            "on": list(self.on),
        }


@dataclass(frozen=True)
class WorkflowDefaults:
    backend: str = BACKEND_INHERIT
    context: str = CONTEXT_FRESH
    timeout_seconds: int = DEFAULT_NODE_TIMEOUT_SECONDS
    max_parallel_nodes: int = 1

    def to_json(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "context": self.context,
            "timeout_seconds": self.timeout_seconds,
            "max_parallel_nodes": self.max_parallel_nodes,
        }


@dataclass(frozen=True)
class NodeDefinition:
    """One node, with every optional field already resolved to a value.

    Resolving defaults at decode time rather than at execution time means
    the normalized JSON — and therefore the definition hash — captures the
    *effective* configuration. Two files that differ only in which defaults
    they spell out explicitly compile to the same hash.
    """

    id: str
    type: str
    depends_on: tuple[str, ...] = ()
    workspace_access: str = st.ACCESS_WRITE
    timeout_seconds: int = DEFAULT_NODE_TIMEOUT_SECONDS
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    output_type: str | None = None
    external_side_effects: bool = False
    # agent nodes
    backend: str = BACKEND_INHERIT
    context: str = CONTEXT_FRESH
    prompt: str | None = None
    prompt_file: str | None = None
    # shell nodes
    run: str | None = None
    # approval nodes
    title: str = ""
    instructions: str = ""
    evidence: tuple[str, ...] = ()

    @property
    def is_executable(self) -> bool:
        """Approval nodes hold no process and take no workspace lock."""
        return self.type in {st.NODE_TYPE_AGENT, st.NODE_TYPE_SHELL}

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "depends_on": list(self.depends_on),
            "workspace_access": self.workspace_access,
            "timeout_seconds": self.timeout_seconds,
            "retry": self.retry.to_json(),
            "output_type": self.output_type,
            "external_side_effects": self.external_side_effects,
        }
        if self.type == st.NODE_TYPE_AGENT:
            payload.update(
                {
                    "backend": self.backend,
                    "context": self.context,
                    "prompt": self.prompt,
                    "prompt_file": self.prompt_file,
                }
            )
        elif self.type == st.NODE_TYPE_SHELL:
            payload["run"] = self.run
        elif self.type == st.NODE_TYPE_APPROVAL:
            payload.update(
                {
                    "title": self.title,
                    "instructions": self.instructions,
                    "evidence": list(self.evidence),
                }
            )
        return payload


@dataclass(frozen=True)
class WorkflowDefinition:
    version: int
    name: str
    description: str
    defaults: WorkflowDefaults
    nodes: tuple[NodeDefinition, ...]
    source_path: Path

    def to_json(self) -> dict[str, Any]:
        """Normalized form. Node order is by id so YAML reordering is a no-op.

        The source path is deliberately excluded: the same workflow copied
        to a different filename is the same definition, and including the
        path would defeat content addressing.
        """
        return {
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "defaults": self.defaults.to_json(),
            "nodes": [node.to_json() for node in sorted(self.nodes, key=lambda n: n.id)],
        }


@dataclass(frozen=True)
class RiskSummary:
    """What a reviewer needs to see before approving a workflow file."""

    shell_node_ids: tuple[str, ...] = ()
    external_side_effect_node_ids: tuple[str, ...] = ()
    write_node_ids: tuple[str, ...] = ()
    ungated_external_node_ids: tuple[str, ...] = ()

    @property
    def has_risk(self) -> bool:
        return bool(self.shell_node_ids or self.external_side_effect_node_ids)

    def to_json(self) -> dict[str, Any]:
        return {
            "shell_nodes": list(self.shell_node_ids),
            "external_side_effect_nodes": list(self.external_side_effect_node_ids),
            "write_nodes": list(self.write_node_ids),
            "ungated_external_nodes": list(self.ungated_external_node_ids),
        }


@dataclass(frozen=True)
class CompiledWorkflow:
    """An execution plan plus everything preflight and the UI need."""

    definition: WorkflowDefinition
    workflow_hash: str
    normalized_json: str
    layers: tuple[tuple[str, ...], ...]
    ancestors: Mapping[str, frozenset[str]]
    node_by_id: Mapping[str, NodeDefinition]
    required_capabilities: frozenset[str]
    required_backends: frozenset[str]
    risk: RiskSummary

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def version(self) -> int:
        return self.definition.version

    @property
    def nodes(self) -> tuple[NodeDefinition, ...]:
        return self.definition.nodes

    def topological_order(self) -> tuple[str, ...]:
        return tuple(node_id for layer in self.layers for node_id in layer)

    def dependents_of(self, node_id: str) -> frozenset[str]:
        """Nodes that must rerun when `node_id` reruns."""
        return frozenset(
            other for other, deps in self.ancestors.items() if node_id in deps
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.definition.description,
            "workflow_hash": self.workflow_hash,
            "source_path": str(self.definition.source_path),
            "layers": [list(layer) for layer in self.layers],
            "nodes": [node.to_json() for node in self.definition.nodes],
            "required_capabilities": sorted(self.required_capabilities),
            "required_backends": sorted(self.required_backends),
            "risk": self.risk.to_json(),
        }
