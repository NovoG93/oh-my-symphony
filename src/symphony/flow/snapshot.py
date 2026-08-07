"""Rebuild a `CompiledWorkflow` from its stored normalized JSON.

A resumed run must execute the definition it started with, not whatever is
in the YAML file today. The run row stores a content hash; the
`workflow_snapshots` table stores the normalized JSON that hashes to it.
This module closes the loop by turning that JSON back into an execution
plan.

Two properties make the round trip trustworthy:

- `WorkflowDefinition.to_json` resolves every default and sorts nodes by
  id, so re-serializing a reconstructed definition yields byte-identical
  JSON. The caller compares hashes and refuses to resume on a mismatch.
- Recompilation re-runs the full graph and filesystem validation. A prompt
  file deleted while the run was parked therefore blocks resume with a
  precise error instead of failing mid-node.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..errors import WorkflowDefinitionInvalid
from .compiler import compile_workflow
from .model import (
    CompiledWorkflow,
    NodeDefinition,
    RetryPolicy,
    WorkflowDefaults,
    WorkflowDefinition,
)


def compile_from_normalized(
    normalized_json: str,
    *,
    source_path: Path,
    workflow_dir: Path,
    max_parallel_nodes: int | None = None,
) -> CompiledWorkflow:
    """Reconstruct and recompile a stored definition."""
    try:
        payload = json.loads(normalized_json)
    except (TypeError, ValueError) as exc:
        raise WorkflowDefinitionInvalid(
            "stored workflow snapshot is not valid JSON",
            source=str(source_path),
            diagnostics=(),
        ) from exc
    if not isinstance(payload, dict):
        raise WorkflowDefinitionInvalid(
            "stored workflow snapshot is not an object",
            source=str(source_path),
            diagnostics=(),
        )

    defaults_raw = payload.get("defaults") or {}
    defaults = WorkflowDefaults(
        backend=str(defaults_raw.get("backend", "inherit")),
        context=str(defaults_raw.get("context", "fresh")),
        timeout_seconds=int(defaults_raw.get("timeout_seconds", 1800)),
        max_parallel_nodes=int(defaults_raw.get("max_parallel_nodes", 1)),
    )

    nodes = tuple(
        _node_from_json(entry) for entry in payload.get("nodes") or [] if isinstance(entry, dict)
    )
    definition = WorkflowDefinition(
        version=int(payload.get("version", 1)),
        name=str(payload.get("name", "")),
        description=str(payload.get("description", "")),
        defaults=defaults,
        nodes=nodes,
        source_path=source_path,
    )
    return compile_workflow(
        definition,
        workflow_dir=workflow_dir,
        max_parallel_nodes=max_parallel_nodes,
    )


def _node_from_json(raw: dict[str, Any]) -> NodeDefinition:
    retry_raw = raw.get("retry") or {}
    retry = RetryPolicy(
        max_attempts=int(retry_raw.get("max_attempts", 1)),
        backoff_seconds=float(retry_raw.get("backoff_seconds", 3.0)),
        on=tuple(str(item) for item in retry_raw.get("on") or ()),
    )
    return NodeDefinition(
        id=str(raw.get("id", "")),
        type=str(raw.get("type", "")),
        depends_on=tuple(str(item) for item in raw.get("depends_on") or ()),
        workspace_access=str(raw.get("workspace_access", "write")),
        timeout_seconds=int(raw.get("timeout_seconds", 1800)),
        retry=retry,
        output_type=_optional_str(raw.get("output_type")),
        external_side_effects=bool(raw.get("external_side_effects", False)),
        backend=str(raw.get("backend", "inherit")),
        context=str(raw.get("context", "fresh")),
        prompt=_optional_str(raw.get("prompt")),
        prompt_file=_optional_str(raw.get("prompt_file")),
        run=_optional_str(raw.get("run")),
        title=str(raw.get("title", "")),
        instructions=str(raw.get("instructions", "")),
        evidence=tuple(str(item) for item in raw.get("evidence") or ()),
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
