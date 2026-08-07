"""Decode workflow YAML into a validated `WorkflowDefinition`.

This layer answers only "is each field well-formed?". Questions that need
the whole graph (cycles, dangling `depends_on`, variable ancestry) or the
filesystem (does `prompt_file` exist?) belong to `compiler.py`.

Every failure is collected rather than raised on the first problem, so a
malformed file reports all of its errors in one pass with source lines.
That matters because the operator is usually editing the file in a text
editor, not iterating against the CLI.

Unknown fields are rejected outright. A silently ignored typo like
`workspace_acess: read` would hand a node write access to the workspace
while the author believed it was read-only, so v1 fails closed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, NoReturn

import yaml

from ..errors import WorkflowDefinitionInvalid
from ..workflow.constants import SUPPORTED_AGENT_KINDS
from . import statuses as st
from .model import (
    BACKEND_INHERIT,
    CONTEXT_CONTINUE,
    CONTEXT_FRESH,
    DEFAULT_NODE_TIMEOUT_SECONDS,
    DEFAULT_SHELL_TIMEOUT_SECONDS,
    MAX_DEPENDENCIES_PER_NODE,
    MAX_NODES,
    MAX_YAML_BYTES,
    SCHEMA_VERSION,
    Diagnostic,
    NodeDefinition,
    RetryPolicy,
    WorkflowDefaults,
    WorkflowDefinition,
)


_NODE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_WORKFLOW_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")

# Upper bound on any node timeout, so one workflow cannot pin a worker slot
# indefinitely. Twelve hours is well past any legitimate single node.
MAX_TIMEOUT_SECONDS = 12 * 60 * 60

_TOP_LEVEL_FIELDS = frozenset({"version", "name", "description", "defaults", "nodes"})
_DEFAULTS_FIELDS = frozenset(
    {"backend", "context", "timeout_seconds", "max_parallel_nodes"}
)
_COMMON_NODE_FIELDS = frozenset(
    {
        "id",
        "type",
        "depends_on",
        "workspace_access",
        "timeout_seconds",
        "retry",
        "output_type",
        "external_side_effects",
    }
)
_NODE_FIELDS_BY_TYPE: dict[str, frozenset[str]] = {
    st.NODE_TYPE_AGENT: _COMMON_NODE_FIELDS
    | {"backend", "context", "prompt", "prompt_file"},
    st.NODE_TYPE_SHELL: _COMMON_NODE_FIELDS | {"run"},
    st.NODE_TYPE_APPROVAL: _COMMON_NODE_FIELDS | {"title", "instructions", "evidence"},
}
_RETRY_FIELDS = frozenset({"max_attempts", "backoff_seconds", "on"})


class _Collector:
    """Accumulates diagnostics and resolves source lines for field paths."""

    def __init__(self, lines: dict[str, int]) -> None:
        self._lines = lines
        self.diagnostics: list[Diagnostic] = []

    def add(self, path: str, message: str) -> None:
        self.diagnostics.append(
            Diagnostic(path=path, message=message, line=self._lines.get(path))
        )

    # Each helper returns a usable fallback so decoding can continue and
    # collect further problems instead of unwinding on the first bad field.

    def string(
        self,
        mapping: dict[str, Any],
        key: str,
        path: str,
        *,
        required: bool = False,
        default: str = "",
    ) -> str:
        if key not in mapping or mapping[key] is None:
            if required:
                self.add(path, "required field is missing")
            return default
        value = mapping[key]
        if not isinstance(value, str):
            self.add(path, f"expected a string, got {type(value).__name__}")
            return default
        return value

    def integer(
        self,
        mapping: dict[str, Any],
        key: str,
        path: str,
        *,
        default: int,
        minimum: int = 1,
        maximum: int | None = None,
    ) -> int:
        if key not in mapping or mapping[key] is None:
            return default
        value = mapping[key]
        # `bool` is an `int` subclass; accepting it here would silently turn
        # `timeout_seconds: true` into 1 second.
        if isinstance(value, bool) or not isinstance(value, int):
            self.add(path, f"expected an integer, got {type(value).__name__}")
            return default
        if value < minimum:
            self.add(path, f"must be at least {minimum}, got {value}")
            return default
        if maximum is not None and value > maximum:
            self.add(path, f"must be at most {maximum}, got {value}")
            return default
        return value

    def number(
        self, mapping: dict[str, Any], key: str, path: str, *, default: float
    ) -> float:
        if key not in mapping or mapping[key] is None:
            return default
        value = mapping[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.add(path, f"expected a number, got {type(value).__name__}")
            return default
        if value < 0:
            self.add(path, f"must not be negative, got {value}")
            return default
        return float(value)

    def boolean(
        self, mapping: dict[str, Any], key: str, path: str, *, default: bool = False
    ) -> bool:
        if key not in mapping or mapping[key] is None:
            return default
        value = mapping[key]
        if not isinstance(value, bool):
            self.add(path, f"expected true or false, got {type(value).__name__}")
            return default
        return value

    def string_list(
        self, mapping: dict[str, Any], key: str, path: str, *, limit: int | None = None
    ) -> tuple[str, ...]:
        if key not in mapping or mapping[key] is None:
            return ()
        value = mapping[key]
        if not isinstance(value, list):
            self.add(path, f"expected a list, got {type(value).__name__}")
            return ()
        if limit is not None and len(value) > limit:
            self.add(path, f"may list at most {limit} entries, got {len(value)}")
            return ()
        items: list[str] = []
        for index, item in enumerate(value):
            if not isinstance(item, str):
                self.add(f"{path}[{index}]", "expected a string")
                continue
            items.append(item)
        return tuple(items)

    def choice(self, value: str, allowed: Iterable[str], path: str, *, default: str) -> str:
        options = sorted(allowed)
        if value not in options:
            self.add(path, f"must be one of {', '.join(options)}; got {value!r}")
            return default
        return value

    def mapping(self, value: Any, path: str) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            self.add(path, f"expected a mapping, got {type(value).__name__}")
            return {}
        return value

    def reject_unknown(
        self, mapping: dict[str, Any], allowed: frozenset[str], path: str
    ) -> None:
        # `key=str` because YAML keys are not guaranteed to be strings —
        # `1: x` or `true: x` are legal, and a bare `sorted()` on mixed types
        # raises TypeError, which would surface as a stack trace instead of
        # the diagnostic the author needs.
        for key in sorted(mapping, key=str):
            if key in allowed:
                continue
            child = f"{path}.{key}" if path else str(key)
            self.add(
                child,
                "unknown field; v1 rejects unrecognized keys so typos cannot "
                "silently change behavior",
            )


def line_map(text: str) -> dict[str, int]:
    """Map dotted field paths to 1-based source lines.

    Built from a second parse via `yaml.compose`, which keeps position
    marks that `safe_load` discards. Two passes over a file capped at 1 MiB
    is cheap, and it keeps the value tree free of injected `__line__` keys
    that would then have to be excluded from every unknown-field check.
    """
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return {}
    lines: dict[str, int] = {}

    def walk(node: Any, path: str) -> None:
        if node is None:
            return
        lines.setdefault(path, node.start_mark.line + 1)
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                key = str(getattr(key_node, "value", ""))
                child = f"{path}.{key}" if path else key
                lines[child] = key_node.start_mark.line + 1
                walk(value_node, child)
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                walk(item, f"{path}[{index}]")

    walk(root, "")
    return lines


def decode_workflow(text: str, *, source_path: Path) -> WorkflowDefinition:
    """Parse and field-validate one workflow file.

    Raises `WorkflowDefinitionInvalid` carrying every diagnostic found, so
    the CLI and the web validator can render a complete error list.
    """
    encoded_size = len(text.encode("utf-8"))
    if encoded_size > MAX_YAML_BYTES:
        raise WorkflowDefinitionInvalid(
            f"workflow file is {encoded_size} bytes, over the {MAX_YAML_BYTES} limit",
            source=str(source_path),
            diagnostics=(),
        )

    lines = line_map(text)
    collector = _Collector(lines)

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise WorkflowDefinitionInvalid(
            f"could not parse YAML: {exc}",
            source=str(source_path),
            diagnostics=(
                Diagnostic(
                    path="",
                    message=str(getattr(exc, "problem", None) or exc),
                    line=(mark.line + 1) if mark is not None else None,
                ),
            ),
        ) from exc

    document = collector.mapping(raw, "")
    if not document:
        collector.add("", "workflow file is empty or not a mapping")
        _raise(collector, source_path)

    collector.reject_unknown(document, _TOP_LEVEL_FIELDS, "")

    version = collector.integer(
        document, "version", "version", default=0, minimum=0, maximum=99
    )
    if version != SCHEMA_VERSION:
        collector.add(
            "version",
            f"unsupported schema version {version}; this build understands "
            f"version {SCHEMA_VERSION}",
        )

    name = collector.string(document, "name", "name", required=True)
    if name and not _WORKFLOW_NAME_RE.match(name):
        collector.add(
            "name",
            "must be lowercase letters, digits, and hyphens, starting with a letter",
        )
    description = collector.string(document, "description", "description")

    defaults = _decode_defaults(
        collector, collector.mapping(document.get("defaults"), "defaults")
    )

    raw_nodes = document.get("nodes")
    if raw_nodes is None:
        collector.add("nodes", "required field is missing")
        _raise(collector, source_path)
    if not isinstance(raw_nodes, list):
        collector.add("nodes", f"expected a list, got {type(raw_nodes).__name__}")
        _raise(collector, source_path)
    if not raw_nodes:
        collector.add("nodes", "a workflow must declare at least one node")
        _raise(collector, source_path)
    if len(raw_nodes) > MAX_NODES:
        collector.add("nodes", f"at most {MAX_NODES} nodes, got {len(raw_nodes)}")
        _raise(collector, source_path)

    nodes: list[NodeDefinition] = []
    seen_ids: set[str] = set()
    for index, raw_node in enumerate(raw_nodes):
        path = f"nodes[{index}]"
        node_map = collector.mapping(raw_node, path)
        if not node_map:
            collector.add(path, "node must be a mapping")
            continue
        node = _decode_node(collector, node_map, path, defaults)
        if node is None:
            continue
        if node.id in seen_ids:
            collector.add(f"{path}.id", f"duplicate node id {node.id!r}")
            continue
        seen_ids.add(node.id)
        nodes.append(node)

    if collector.diagnostics:
        _raise(collector, source_path)

    return WorkflowDefinition(
        version=version,
        name=name,
        description=description,
        defaults=defaults,
        nodes=tuple(nodes),
        source_path=source_path,
    )


def _decode_defaults(
    collector: _Collector, raw: dict[str, Any]
) -> WorkflowDefaults:
    collector.reject_unknown(raw, _DEFAULTS_FIELDS, "defaults")
    backend = collector.string(
        raw, "backend", "defaults.backend", default=BACKEND_INHERIT
    )
    backend = collector.choice(
        backend,
        {BACKEND_INHERIT, *SUPPORTED_AGENT_KINDS},
        "defaults.backend",
        default=BACKEND_INHERIT,
    )
    context = collector.string(
        raw, "context", "defaults.context", default=CONTEXT_FRESH
    )
    context = collector.choice(
        context,
        {CONTEXT_FRESH, CONTEXT_CONTINUE},
        "defaults.context",
        default=CONTEXT_FRESH,
    )
    timeout = collector.integer(
        raw,
        "timeout_seconds",
        "defaults.timeout_seconds",
        default=DEFAULT_NODE_TIMEOUT_SECONDS,
        maximum=MAX_TIMEOUT_SECONDS,
    )
    parallel = collector.integer(
        raw,
        "max_parallel_nodes",
        "defaults.max_parallel_nodes",
        default=1,
        maximum=16,
    )
    return WorkflowDefaults(
        backend=backend,
        context=context,
        timeout_seconds=timeout,
        max_parallel_nodes=parallel,
    )


def _decode_node(
    collector: _Collector,
    raw: dict[str, Any],
    path: str,
    defaults: WorkflowDefaults,
) -> NodeDefinition | None:
    node_id = collector.string(raw, "id", f"{path}.id", required=True)
    if not node_id:
        return None
    if not _NODE_ID_RE.match(node_id):
        collector.add(
            f"{path}.id",
            "must be lowercase letters, digits, and hyphens, starting with a "
            "letter, at most 63 characters",
        )
        return None

    node_type = collector.string(raw, "type", f"{path}.type", required=True)
    if node_type not in st.NODE_TYPES:
        collector.add(
            f"{path}.type",
            f"must be one of {', '.join(sorted(st.NODE_TYPES))}; got {node_type!r}",
        )
        return None

    collector.reject_unknown(raw, _NODE_FIELDS_BY_TYPE[node_type], path)

    depends_on = collector.string_list(
        raw, "depends_on", f"{path}.depends_on", limit=MAX_DEPENDENCIES_PER_NODE
    )
    if node_id in depends_on:
        collector.add(f"{path}.depends_on", "a node cannot depend on itself")
        depends_on = tuple(dep for dep in depends_on if dep != node_id)
    if len(set(depends_on)) != len(depends_on):
        collector.add(f"{path}.depends_on", "contains duplicate entries")
        depends_on = tuple(dict.fromkeys(depends_on))

    workspace_access = _decode_workspace_access(collector, raw, path, node_type)
    retry = _decode_retry(collector, raw, path, node_type)
    output_type = collector.string(raw, "output_type", f"{path}.output_type") or None
    external = collector.boolean(
        raw, "external_side_effects", f"{path}.external_side_effects"
    )

    # Shell nodes get the short default from PRD §8.4 rather than the
    # workflow-wide agent default: a deterministic command that has not
    # finished in two minutes is far more likely stuck than working.
    type_default_timeout = (
        DEFAULT_SHELL_TIMEOUT_SECONDS
        if node_type == st.NODE_TYPE_SHELL
        else defaults.timeout_seconds
    )
    timeout = collector.integer(
        raw,
        "timeout_seconds",
        f"{path}.timeout_seconds",
        default=type_default_timeout,
        maximum=MAX_TIMEOUT_SECONDS,
    )

    backend = defaults.backend
    context = defaults.context
    prompt: str | None = None
    prompt_file: str | None = None
    run: str | None = None
    title = ""
    instructions = ""
    evidence: tuple[str, ...] = ()

    if node_type == st.NODE_TYPE_AGENT:
        backend = collector.choice(
            collector.string(raw, "backend", f"{path}.backend", default=backend),
            {BACKEND_INHERIT, *SUPPORTED_AGENT_KINDS},
            f"{path}.backend",
            default=BACKEND_INHERIT,
        )
        context = collector.choice(
            collector.string(raw, "context", f"{path}.context", default=context),
            {CONTEXT_FRESH, CONTEXT_CONTINUE},
            f"{path}.context",
            default=CONTEXT_FRESH,
        )
        has_prompt = isinstance(raw.get("prompt"), str) and raw["prompt"].strip()
        has_file = isinstance(raw.get("prompt_file"), str) and raw["prompt_file"].strip()
        if has_prompt and has_file:
            collector.add(
                path, "an agent node sets exactly one of `prompt` or `prompt_file`"
            )
        elif not has_prompt and not has_file:
            collector.add(
                path, "an agent node requires either `prompt` or `prompt_file`"
            )
        prompt = raw["prompt"] if has_prompt else None
        prompt_file = raw["prompt_file"] if has_file else None
    elif node_type == st.NODE_TYPE_SHELL:
        run = collector.string(raw, "run", f"{path}.run", required=True)
        if run and not run.strip():
            collector.add(f"{path}.run", "command must not be blank")
    else:
        title = collector.string(raw, "title", f"{path}.title", required=True)
        instructions = collector.string(raw, "instructions", f"{path}.instructions")
        evidence = collector.string_list(raw, "evidence", f"{path}.evidence")

    return NodeDefinition(
        id=node_id,
        type=node_type,
        depends_on=depends_on,
        workspace_access=workspace_access,
        timeout_seconds=timeout,
        retry=retry,
        output_type=output_type,
        external_side_effects=external,
        backend=backend,
        context=context,
        prompt=prompt,
        prompt_file=prompt_file,
        run=run,
        title=title,
        instructions=instructions,
        evidence=evidence,
    )


def _decode_workspace_access(
    collector: _Collector, raw: dict[str, Any], path: str, node_type: str
) -> str:
    field_path = f"{path}.workspace_access"
    if node_type == st.NODE_TYPE_APPROVAL:
        declared = raw.get("workspace_access")
        if declared is not None and declared != st.ACCESS_NONE:
            collector.add(
                field_path,
                "an approval node holds no workspace lock; omit this field or "
                "set it to none",
            )
        return st.ACCESS_NONE
    # PRD §9.3: during the compatibility period an omitted access declaration
    # means `write`, the exclusive-lock choice. Defaulting to `read` would
    # hand parallelism to nodes that never asked to be sandboxed.
    declared = collector.string(
        raw, "workspace_access", field_path, default=st.ACCESS_WRITE
    )
    access = collector.choice(
        declared,
        st.WORKSPACE_ACCESS_VALUES,
        field_path,
        default=st.ACCESS_WRITE,
    )
    if access == st.ACCESS_NONE:
        collector.add(
            field_path,
            "executable nodes run in the workspace; use read or write",
        )
        return st.ACCESS_WRITE
    return access


def _decode_retry(
    collector: _Collector, raw: dict[str, Any], path: str, node_type: str
) -> RetryPolicy:
    if "retry" not in raw or raw["retry"] is None:
        return RetryPolicy()
    retry_map = _restore_yaml_boolean_keys(
        collector.mapping(raw["retry"], f"{path}.retry")
    )
    collector.reject_unknown(retry_map, _RETRY_FIELDS, f"{path}.retry")
    max_attempts = collector.integer(
        retry_map,
        "max_attempts",
        f"{path}.retry.max_attempts",
        default=1,
        minimum=1,
        maximum=10,
    )
    backoff = collector.number(
        retry_map, "backoff_seconds", f"{path}.retry.backoff_seconds", default=3.0
    )
    on = collector.string_list(retry_map, "on", f"{path}.retry.on")
    for index, error_class in enumerate(on):
        if error_class not in st.ERROR_CLASSES:
            collector.add(
                f"{path}.retry.on[{index}]",
                f"unknown error class {error_class!r}; expected one of "
                f"{', '.join(sorted(st.ERROR_CLASSES))}",
            )
    if st.ERROR_CANCELLED in on:
        collector.add(
            f"{path}.retry.on",
            "operator cancellation is never retried",
        )
        on = tuple(item for item in on if item != st.ERROR_CANCELLED)
    if st.ERROR_FATAL in on:
        collector.add(
            f"{path}.retry.on",
            "fatal errors (auth, permissions, invalid config, exhausted budget) "
            "are never retried",
        )
        on = tuple(item for item in on if item != st.ERROR_FATAL)
    if max_attempts > 1 and not on:
        collector.add(
            f"{path}.retry.on",
            "declare which error classes may retry; a bare attempt count would "
            "retry deterministic failures forever",
        )
    if node_type == st.NODE_TYPE_APPROVAL and max_attempts > 1:
        collector.add(
            f"{path}.retry", "an approval node runs no process and cannot retry"
        )
        return RetryPolicy()
    return RetryPolicy(max_attempts=max_attempts, backoff_seconds=backoff, on=on)


def _restore_yaml_boolean_keys(mapping: dict[Any, Any]) -> dict[Any, Any]:
    """Undo YAML 1.1's boolean coercion of the bare key `on`.

    PyYAML implements YAML 1.1, where `on`, `off`, `yes`, and `no` are
    booleans — including as mapping *keys*. So the retry field the PRD
    specifies as

        retry:
          on: [transient]

    arrives here as `{True: ["transient"]}`, and every lookup for `"on"`
    misses. Quoting it (`"on":`) works, but nobody reading the spec would
    know to, so the field would appear silently broken.

    Only `retry` mappings pass through this, and `on` is the only truthy
    field name in that mapping, so restoring `True -> "on"` is unambiguous.
    """
    if True not in mapping:
        return mapping
    restored = {("on" if key is True else key): value for key, value in mapping.items()}
    return restored


def _raise(collector: _Collector, source_path: Path) -> NoReturn:
    diagnostics = tuple(collector.diagnostics)
    summary = "; ".join(d.render() for d in diagnostics[:3])
    if len(diagnostics) > 3:
        summary += f"; (+{len(diagnostics) - 3} more)"
    raise WorkflowDefinitionInvalid(
        summary or "workflow definition is invalid",
        source=str(source_path),
        diagnostics=diagnostics,
    )
