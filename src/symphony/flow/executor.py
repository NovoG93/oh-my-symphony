"""Run a compiled workflow DAG inside one Symphony ticket run.

This executor is subordinate, not a second scheduler. It never decides
which ticket runs, never creates a workspace or branch, never enforces
global concurrency. The orchestrator has already done all of that and has
already acquired the dispatch lease before `execute` is awaited. What lives
here is only: which node is next, did it succeed, what happens if it did
not, and when does the run stop.

Three shapes of stopping, and the difference matters:

- **Terminal** — every required node succeeded, or one failed fatally, or
  a gate was rejected. The fence releases and the ticket moves on.
- **Suspended** — an approval gate opened. There is no process and no
  lease, but the fence is retained so ordinary polling cannot redispatch
  the ticket. The run continues only when a human resolves the gate and an
  operator resumes.
- **Interrupted** — the process died mid-node. Startup reconciliation
  rewrites the node to `interrupted` and parks the run in
  `needs_attention`. Nothing restarts on its own; PRD §G4 requires an
  explicit decision because a half-finished node may have side effects the
  engine cannot see.

v1 executes one node at a time. `max_parallel_nodes` is honoured by the
compiler and the config, but no backend can currently be held to read-only
workspace access (see `flow.preflight.parallel_safe_nodes`), so every node
takes the exclusive lock and the topological order *is* the schedule.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Protocol

from ..errors import (
    RunNotFound,
    RunNotResumable,
    SymphonyError,
    TurnTimeout,
    WorkflowDefinitionInvalid,
    WorkflowDefinitionNotFound,
)
from ..issue import Issue
from ..logging import get_logger
from ..orchestrator.executors import TicketRunContext
from ..orchestrator.flow_store import GovernedRunStore
from ..workflow import ServiceConfig
from . import statuses as st
from .agent_node import run_agent_node
from .artifacts import ArtifactStore
from .gitprov import provenance_between, snapshot as git_snapshot
from .loader import WorkflowLoader
from .model import CompiledWorkflow, NodeDefinition
from .preflight import resolve_node_backend, validate_backends
from .prompts import render_prompt, ticket_snapshot_to_context
from .retries import backoff_seconds, classify_failure, should_retry
from .shell_node import run_shell_node


log = get_logger()

# Name of the file each node's full output is written to. Referenced by
# `${nodes.<id>.artifact_dir}` consumers and by resume, which rehydrates
# prior outputs from disk rather than from the capped SQLite preview.
OUTPUT_FILENAME = "output.txt"
STDERR_FILENAME = "stderr.txt"


class GovernedExecutorHost(Protocol):
    """The orchestrator services a governed run needs.

    Kept narrow on purpose. Everything here is something only the
    orchestrator can do — it owns the workspace manager, the lease, and the
    tracker adapter. Anything the executor can do for itself is not on this
    list.
    """

    async def prepare_governed_workspace(self, identifier: str) -> Path: ...

    async def release_governed_workspace(self, workspace: Path) -> None: ...

    def governed_store(self, cfg: ServiceConfig) -> GovernedRunStore: ...

    def governed_loader(self, cfg: ServiceConfig) -> WorkflowLoader: ...

    def governed_artifacts(self, cfg: ServiceConfig) -> ArtifactStore: ...

    def heartbeat_governed_run(self, issue_id: str, run_id: str) -> None: ...

    def sync_governed_pid(
        self, issue_id: str, backend_agent_pid: int | None
    ) -> None: ...

    def apply_governed_ticket_state(
        self, cfg: ServiceConfig, issue: Issue, condition: str
    ) -> None: ...

    def write_governed_summary(
        self,
        cfg: ServiceConfig,
        issue: Issue,
        *,
        run_id: str,
        workflow_name: str,
        result: str,
        artifact_dir: str,
    ) -> None: ...


@dataclass
class _NodeState:
    """Mutable per-node bookkeeping for one drive of the graph."""

    outputs: dict[str, str]
    artifact_dirs: dict[str, str]
    succeeded: set[str]


class GovernedWorkflowExecutor:
    """Executes a compiled DAG for one ticket run."""

    mode = st.MODE_GOVERNED

    def __init__(self, host: GovernedExecutorHost) -> None:
        self._host = host

    # --- entry points ---------------------------------------------------

    async def execute(self, context: TicketRunContext) -> None:
        """Start a fresh governed run for a newly dispatched ticket."""
        cfg = context.cfg
        issue = context.issue
        store = self._host.governed_store(cfg)

        try:
            compiled = self._select_workflow(cfg, issue)
            validate_backends(compiled, cfg, ticket_backend=issue.agent_kind)
        except (
            WorkflowDefinitionInvalid,
            WorkflowDefinitionNotFound,
            SymphonyError,
        ) as exc:
            # Fail closed and loudly: an unusable workflow must never
            # silently fall back to another workflow or to legacy mode.
            log.error(
                "governed_workflow_preflight_failed",
                issue_id=issue.id,
                identifier=issue.identifier,
                error=str(exc),
            )
            store.append_event(
                run_id=context.run_id,
                event_type="run_preflight_failed",
                payload={"error": str(exc), "code": getattr(exc, "code", "")},
            )
            raise

        snapshot = _ticket_snapshot(issue)
        store.put_workflow_snapshot(
            workflow_hash=compiled.workflow_hash,
            workflow_name=compiled.name,
            schema_version=compiled.version,
            normalized_json=compiled.normalized_json,
            source_path=str(compiled.definition.source_path),
        )
        store.begin_governed_run(
            run_id=context.run_id,
            issue_id=issue.id,
            workflow_name=compiled.name,
            workflow_version=compiled.version,
            workflow_hash=compiled.workflow_hash,
            ticket_snapshot=snapshot,
        )

        workspace = await self._host.prepare_governed_workspace(issue.identifier)
        await self._drive(
            cfg=cfg,
            issue=issue,
            run_id=context.run_id,
            compiled=compiled,
            workspace=workspace,
            ticket_snapshot=snapshot,
            store=store,
        )

    async def resume(
        self, *, cfg: ServiceConfig, issue: Issue, run_id: str
    ) -> None:
        """Continue a suspended or interrupted run from its stored snapshot.

        The *stored* snapshot, never the current YAML file: a definition
        edited while the run was parked must not change what the run does
        (PRD §10.4). Succeeded nodes are skipped, their outputs rehydrated
        from artifacts.
        """
        store = self._host.governed_store(cfg)
        record = store.get_governed_run(run_id)
        if record is None:
            raise RunNotFound("no governed run with that id", run_id=run_id)
        if record.execution_status in st.TERMINAL_RUN_STATUSES:
            raise RunNotResumable(
                f"run is {record.execution_status}", run_id=run_id
            )
        if record.workflow_hash is None:
            raise RunNotResumable("run has no stored definition", run_id=run_id)
        stored = store.get_workflow_snapshot(record.workflow_hash)
        if stored is None:
            raise RunNotResumable(
                "the stored workflow snapshot is missing; this run cannot be "
                "reproduced and must be abandoned",
                run_id=run_id,
                workflow_hash=record.workflow_hash,
            )
        compiled = self._recompile_snapshot(
            cfg, stored.normalized_json, stored.source_path
        )
        if compiled.workflow_hash != record.workflow_hash:
            raise RunNotResumable(
                "the stored definition no longer compiles to the same hash",
                run_id=run_id,
            )

        workspace = await self._host.prepare_governed_workspace(issue.identifier)
        store.set_run_status(run_id=run_id, status=st.RUN_RUNNING)
        self._host.apply_governed_ticket_state(cfg, issue, st.RUN_RUNNING)
        await self._drive(
            cfg=cfg,
            issue=issue,
            run_id=run_id,
            compiled=compiled,
            workspace=workspace,
            ticket_snapshot=record.ticket_snapshot or _ticket_snapshot(issue),
            store=store,
        )

    # --- the node loop --------------------------------------------------

    async def _drive(
        self,
        *,
        cfg: ServiceConfig,
        issue: Issue,
        run_id: str,
        compiled: CompiledWorkflow,
        workspace: Path,
        ticket_snapshot: Mapping[str, Any],
        store: GovernedRunStore,
    ) -> None:
        artifacts = self._host.governed_artifacts(cfg)
        state = self._rehydrate(store, artifacts, run_id, compiled)

        store.set_run_status(run_id=run_id, status=st.RUN_RUNNING)
        self._host.apply_governed_ticket_state(cfg, issue, st.RUN_RUNNING)

        for node_id in compiled.topological_order():
            node = compiled.node_by_id[node_id]
            if node_id in state.succeeded:
                continue

            # A dependency that did not succeed means this node is not
            # reachable in this drive. v1 has no conditional execution, so
            # the only way to get here is a prior failure the run already
            # terminalized on — defensive, but cheap.
            unmet = [
                dep for dep in node.depends_on if dep not in state.succeeded
            ]
            if unmet:
                await self._terminalize(
                    cfg=cfg,
                    issue=issue,
                    run_id=run_id,
                    store=store,
                    artifacts=artifacts,
                    status=st.RUN_NEEDS_ATTENTION,
                    attention_reason=st.ATTENTION_NODE_FAILED,
                    terminal_reason=f"unmet_dependencies:{node_id}",
                    workflow_name=compiled.name,
                    workspace=workspace,
                )
                return

            self._host.heartbeat_governed_run(issue.id, run_id)

            if node.type == st.NODE_TYPE_APPROVAL:
                resolved = await self._run_approval_node(
                    cfg=cfg,
                    issue=issue,
                    run_id=run_id,
                    node=node,
                    store=store,
                    artifacts=artifacts,
                    workflow_name=compiled.name,
                    workspace=workspace,
                )
                if resolved is None:
                    return  # suspended on the gate
                if not resolved:
                    return  # rejected; already terminalized
                state.succeeded.add(node_id)
                continue

            ok = await self._run_executable_node(
                cfg=cfg,
                issue=issue,
                run_id=run_id,
                node=node,
                compiled=compiled,
                workspace=workspace,
                ticket_snapshot=ticket_snapshot,
                store=store,
                artifacts=artifacts,
                state=state,
            )
            if not ok:
                return  # already terminalized as needs_attention
            state.succeeded.add(node_id)

        await self._terminalize(
            cfg=cfg,
            issue=issue,
            run_id=run_id,
            store=store,
            artifacts=artifacts,
            status=st.RUN_SUCCEEDED,
            attention_reason=None,
            terminal_reason="all_nodes_succeeded",
            workflow_name=compiled.name,
            workspace=workspace,
        )

    # --- node kinds -----------------------------------------------------

    async def _run_executable_node(
        self,
        *,
        cfg: ServiceConfig,
        issue: Issue,
        run_id: str,
        node: NodeDefinition,
        compiled: CompiledWorkflow,
        workspace: Path,
        ticket_snapshot: Mapping[str, Any],
        store: GovernedRunStore,
        artifacts: ArtifactStore,
        state: _NodeState,
    ) -> bool:
        """Run one agent or shell node, with retries. True when it succeeded."""
        attempt = 0
        while True:
            attempt += 1
            before = git_snapshot(workspace)
            backend_kind = (
                resolve_node_backend(
                    node.backend,
                    ticket_backend=issue.agent_kind,
                    service_default=cfg.agent.kind,
                )
                if node.type == st.NODE_TYPE_AGENT
                else None
            )
            record = store.start_node_attempt(
                run_id=run_id,
                node_id=node.id,
                node_type=node.type,
                backend_kind=backend_kind,
                workspace_access=node.workspace_access,
                head_before=before.head,
                external_operation_key=(
                    f"{run_id}:{node.id}" if node.external_side_effects else None
                ),
            )

            failure: BaseException | None = None
            exit_code: int | None = None
            output = ""
            stderr = ""
            session_id: str | None = None
            input_tokens: int | None = None
            output_tokens: int | None = None

            try:
                if node.type == st.NODE_TYPE_AGENT:
                    prompt = self._render_node_prompt(
                        cfg=cfg,
                        node=node,
                        run_id=run_id,
                        workspace=workspace,
                        ticket_snapshot=ticket_snapshot,
                        state=state,
                    )
                    node_cfg = _config_with_backend(cfg, backend_kind or cfg.agent.kind)
                    result = await run_agent_node(
                        cfg=node_cfg,
                        node=node,
                        prompt=prompt,
                        workspace=workspace,
                        workspace_root=cfg.workspace_root,
                        timeout_seconds=node.timeout_seconds,
                        on_pid=lambda pid: self._host.sync_governed_pid(issue.id, pid),
                    )
                    output = result.output
                    session_id = result.session_id
                    input_tokens = result.input_tokens
                    output_tokens = result.output_tokens
                else:
                    shell = await run_shell_node(
                        command=node.run or "",
                        workspace=workspace,
                        timeout_seconds=node.timeout_seconds,
                        env_extra=_shell_env(issue, run_id, node, state),
                    )
                    output = shell.stdout
                    stderr = shell.stderr
                    exit_code = shell.exit_code
                    if shell.timed_out:
                        failure = TurnTimeout(
                            f"command exceeded {node.timeout_seconds}s"
                        )
                    elif shell.exit_code != 0:
                        failure = None  # nonzero exit, no exception
            except BaseException as exc:  # noqa: BLE001 - classified below
                failure = exc

            after = git_snapshot(workspace)
            provenance = provenance_between(workspace, before, after)
            stored_output = self._persist_node_output(
                artifacts=artifacts,
                store=store,
                run_id=run_id,
                node=node,
                output=output,
                stderr=stderr,
            )

            node_failed = failure is not None or (
                node.type == st.NODE_TYPE_SHELL and exit_code not in (0, None)
            )
            if not node_failed:
                store.finish_node_attempt(
                    node_run_id=record.node_run_id,
                    status=st.NODE_SUCCEEDED,
                    output_preview=output,
                    output_sha256=stored_output,
                    session_id=session_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    head_after=after.head,
                    diffstat=provenance.diffstat,
                )
                state.outputs[node.id] = output
                state.artifact_dirs[node.id] = artifacts.relative_node_dir(
                    run_id, node.id
                )
                return True

            classification = classify_failure(
                failure,
                node_type=node.type,
                exit_code=exit_code,
                external_side_effects=node.external_side_effects,
            )
            retrying = should_retry(
                classification,
                policy=node.retry,
                attempt=attempt,
                external_side_effects=node.external_side_effects,
            )
            store.finish_node_attempt(
                node_run_id=record.node_run_id,
                status=st.NODE_FAILED,
                error_class=classification.error_class,
                error_code=classification.error_code,
                error_message=classification.message,
                output_preview=output or stderr,
                output_sha256=stored_output,
                session_id=session_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                head_after=after.head,
                diffstat=provenance.diffstat,
            )
            log.warning(
                "governed_node_failed",
                issue_id=issue.id,
                run_id=run_id,
                node=node.id,
                attempt=attempt,
                error_class=classification.error_class,
                error_code=classification.error_code,
                retrying=retrying,
            )
            if not retrying:
                await self._terminalize(
                    cfg=cfg,
                    issue=issue,
                    run_id=run_id,
                    store=store,
                    artifacts=artifacts,
                    status=st.RUN_NEEDS_ATTENTION,
                    attention_reason=st.ATTENTION_NODE_FAILED,
                    terminal_reason=f"{node.id}:{classification.error_code}",
                    workflow_name=compiled.name,
                    workspace=workspace,
                )
                return False

            await asyncio.sleep(backoff_seconds(node.retry, attempt))

    async def _run_approval_node(
        self,
        *,
        cfg: ServiceConfig,
        issue: Issue,
        run_id: str,
        node: NodeDefinition,
        store: GovernedRunStore,
        artifacts: ArtifactStore,
        workflow_name: str,
        workspace: Path,
    ) -> bool | None:
        """Open or resolve a gate.

        Returns `True` when approved and the run may continue, `False` when
        rejected (already terminalized), and `None` when the gate is
        pending and the run must suspend.
        """
        latest = store.latest_node_attempts(run_id).get(node.id)
        if latest is not None and latest.status == st.NODE_WAITING_APPROVAL:
            approval = store.get_approval_for_node(
                run_id=run_id, node_id=node.id, node_attempt=latest.attempt
            )
            if approval is None or approval.status == st.APPROVAL_PENDING:
                return None
            if approval.decision == st.APPROVAL_APPROVED:
                store.set_node_status(
                    node_run_id=latest.node_run_id, status=st.NODE_SUCCEEDED
                )
                return True
            store.set_node_status(
                node_run_id=latest.node_run_id, status=st.NODE_REJECTED
            )
            await self._terminalize(
                cfg=cfg,
                issue=issue,
                run_id=run_id,
                store=store,
                artifacts=artifacts,
                status=st.RUN_REJECTED,
                attention_reason=None,
                terminal_reason=f"{node.id}:rejected",
                workflow_name=workflow_name,
                workspace=workspace,
            )
            return False

        # Opening the gate: create the attempt, the approval record, and the
        # run suspension. The store commits each with its event, and the
        # fence is what actually blocks redispatch once the worker exits.
        record = store.start_node_attempt(
            run_id=run_id,
            node_id=node.id,
            node_type=node.type,
            workspace_access=st.ACCESS_NONE,
        )
        store.create_approval(
            run_id=run_id,
            node_id=node.id,
            node_attempt=record.attempt,
            title=node.title,
            instructions=node.instructions,
        )
        store.set_node_status(
            node_run_id=record.node_run_id, status=st.NODE_WAITING_APPROVAL
        )
        store.set_run_status(run_id=run_id, status=st.RUN_WAITING_APPROVAL)
        self._host.apply_governed_ticket_state(cfg, issue, st.RUN_WAITING_APPROVAL)
        await self._host.release_governed_workspace(workspace)
        log.info(
            "governed_gate_opened",
            issue_id=issue.id,
            run_id=run_id,
            node=node.id,
            title=node.title,
        )
        return None

    # --- shared helpers -------------------------------------------------

    def _select_workflow(self, cfg: ServiceConfig, issue: Issue) -> CompiledWorkflow:
        """Ticket override, then service default. Never a silent fallback."""
        loader = self._host.governed_loader(cfg)
        name = issue.workflow or cfg.workflow_engine.default
        return loader.load(name)

    def _recompile_snapshot(
        self, cfg: ServiceConfig, normalized_json: str, source_path: str
    ) -> CompiledWorkflow:
        """Rebuild a `CompiledWorkflow` from its stored normalized form."""
        from .snapshot import compile_from_normalized

        return compile_from_normalized(
            normalized_json,
            source_path=Path(source_path),
            workflow_dir=cfg.workflow_path.parent,
            max_parallel_nodes=cfg.workflow_engine.max_parallel_nodes,
        )

    def _rehydrate(
        self,
        store: GovernedRunStore,
        artifacts: ArtifactStore,
        run_id: str,
        compiled: CompiledWorkflow,
    ) -> _NodeState:
        """Rebuild prior node outputs so a resumed run can substitute them.

        Outputs come from artifacts, not from the SQLite preview: the
        preview is capped and redacted for display, while a downstream
        prompt needs what the node actually produced.
        """
        state = _NodeState(outputs={}, artifact_dirs={}, succeeded=set())
        for node_id, record in store.latest_node_attempts(run_id).items():
            if record.status != st.NODE_SUCCEEDED:
                continue
            if node_id not in compiled.node_by_id:
                continue
            state.succeeded.add(node_id)
            state.artifact_dirs[node_id] = artifacts.relative_node_dir(run_id, node_id)
            state.outputs[node_id] = self._read_output(
                artifacts, run_id, node_id, fallback=record.output_preview or ""
            )
        return state

    def _read_output(
        self, artifacts: ArtifactStore, run_id: str, node_id: str, *, fallback: str
    ) -> str:
        relative = f"{artifacts.relative_node_dir(run_id, node_id)}/{OUTPUT_FILENAME}"
        try:
            return artifacts.resolve(relative).read_text(encoding="utf-8")
        except (SymphonyError, OSError):
            return fallback

    def _persist_node_output(
        self,
        *,
        artifacts: ArtifactStore,
        store: GovernedRunStore,
        run_id: str,
        node: NodeDefinition,
        output: str,
        stderr: str,
    ) -> str | None:
        """Write full output to the artifact store; return its sha256."""
        digest: str | None = None
        if output:
            stored = artifacts.write_text(
                run_id=run_id,
                node_id=node.id,
                filename=OUTPUT_FILENAME,
                content=output,
            )
            digest = stored.sha256
            store.record_artifact(
                run_id=run_id,
                node_id=node.id,
                artifact_type=node.output_type or "output",
                scope=st.SCOPE_RUNTIME,
                relative_path=stored.relative_path,
                media_type=stored.media_type,
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
            )
        if stderr:
            stored_err = artifacts.write_text(
                run_id=run_id,
                node_id=node.id,
                filename=STDERR_FILENAME,
                content=stderr,
            )
            store.record_artifact(
                run_id=run_id,
                node_id=node.id,
                artifact_type="stderr",
                scope=st.SCOPE_RUNTIME,
                relative_path=stored_err.relative_path,
                media_type=stored_err.media_type,
                size_bytes=stored_err.size_bytes,
                sha256=stored_err.sha256,
            )
        return digest

    def _render_node_prompt(
        self,
        *,
        cfg: ServiceConfig,
        node: NodeDefinition,
        run_id: str,
        workspace: Path,
        ticket_snapshot: Mapping[str, Any],
        state: _NodeState,
    ) -> str:
        template = node.prompt
        if template is None and node.prompt_file:
            template = (cfg.workflow_path.parent / node.prompt_file).read_text(
                encoding="utf-8"
            )
        context = ticket_snapshot_to_context(
            ticket_snapshot,
            run_id=run_id,
            workspace=str(workspace),
            node_outputs=state.outputs,
            node_artifact_dirs=state.artifact_dirs,
        )
        return render_prompt(template or "", context)

    async def _terminalize(
        self,
        *,
        cfg: ServiceConfig,
        issue: Issue,
        run_id: str,
        store: GovernedRunStore,
        artifacts: ArtifactStore,
        status: str,
        attention_reason: str | None,
        terminal_reason: str,
        workflow_name: str,
        workspace: Path,
    ) -> None:
        """Finish a run, writing the ticket before committing the DB state.

        Order matters (PRD §13.4): the file ticket is written first, then
        the run's terminal status. If the process dies between the two, the
        fence is still held and startup reconciliation completes the
        database transition — whereas the reverse order could release the
        fence while the board still shows the ticket as running.
        """
        result = "succeeded" if status == st.RUN_SUCCEEDED else status
        try:
            self._host.write_governed_summary(
                cfg,
                issue,
                run_id=run_id,
                workflow_name=workflow_name,
                result=result,
                artifact_dir=str(artifacts.root / run_id),
            )
            self._host.apply_governed_ticket_state(cfg, issue, status)
        except Exception as exc:  # noqa: BLE001 - ticket write must not strand the run
            log.warning(
                "governed_ticket_write_failed",
                issue_id=issue.id,
                run_id=run_id,
                error=str(exc),
            )
        store.set_run_status(
            run_id=run_id,
            status=status,
            attention_reason=attention_reason,
            terminal_reason=terminal_reason,
        )
        await self._host.release_governed_workspace(workspace)
        log.info(
            "governed_run_finished",
            issue_id=issue.id,
            run_id=run_id,
            status=status,
            terminal_reason=terminal_reason,
        )


def _ticket_snapshot(issue: Issue) -> dict[str, Any]:
    """Bounded dispatch-time ticket input (PRD §14.1).

    Bounded because it is stored on the run row and rendered into prompts
    on every resume; an unbounded description would be copied into the
    database for the life of the run.
    """
    description = issue.description or ""
    limit = 64_000
    if len(description) > limit:
        description = description[:limit] + "\n… [ticket description truncated]"
    return {
        "id": issue.id,
        "identifier": issue.identifier,
        "title": issue.title,
        "description": description,
        "labels": list(issue.labels),
        "state": issue.state,
    }


def _shell_env(
    issue: Issue, run_id: str, node: NodeDefinition, state: _NodeState
) -> dict[str, str]:
    """Context for a shell node, passed as environment, never interpolated.

    A shell node's `run:` string is never templated. Ticket text reaching a
    command line would be a shell-injection hole no amount of quoting
    reliably closes, so the data arrives as variables the command may read
    if it chooses (PRD §8.4).
    """
    env = {
        "SYMPHONY_RUN_ID": run_id,
        "SYMPHONY_NODE_ID": node.id,
        "SYMPHONY_ISSUE_ID": issue.id,
        "SYMPHONY_ISSUE_IDENTIFIER": issue.identifier,
        "SYMPHONY_ISSUE_TITLE": issue.title,
    }
    for node_id, directory in state.artifact_dirs.items():
        key = "SYMPHONY_ARTIFACT_DIR_" + _env_suffix(node_id)
        env[key] = directory
    return env


def _env_suffix(node_id: str) -> str:
    """Node ids are lowercase-hyphen; env keys must be uppercase-underscore."""
    return node_id.replace("-", "_").upper()


def _config_with_backend(cfg: ServiceConfig, kind: str) -> ServiceConfig:
    """A config view whose `agent.kind` is the node's chosen backend.

    A node-level backend override must not mutate the ticket's default —
    the next node inherits from the ticket, not from its predecessor.
    """
    if cfg.agent.kind == kind:
        return cfg
    return replace(cfg, agent=replace(cfg.agent, kind=kind))


