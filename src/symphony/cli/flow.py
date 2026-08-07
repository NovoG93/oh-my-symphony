"""`symphony workflow|run|approval ...` — operator commands over the governed ledger.

Three groups, one module, because they share a single data API: the
`GovernedRunStore` hanging off the run registry in `.symphony/state.db`.

    symphony workflow list|show|validate    workflow definitions on disk
    symphony run show|events|resume|abandon|cancel
    symphony approval list|resolve

What this CLI can and cannot do
-------------------------------

Reads are complete: every command below answers from the ledger the
running service writes, so `run show`, `run events`, and `approval list`
are always truthful.

Writes are **ledger-level only, and that is a real limitation**. This
process does not own the orchestrator — the executor, its worktrees, and
its agent subprocesses live in whichever `symphony` / `symphony service`
process is actually running the board. So:

* `run abandon` and `run cancel` write the terminal status directly. The
  store releases the issue fence in the same transaction, which is the
  operator-visible effect that matters (the ticket becomes dispatchable
  again). A node that is genuinely mid-flight in the service process is
  *not* killed by this command; the service notices the terminal status
  and stops.
* `run resume` cannot itself drive the executor, and nothing picks a run
  up on its own — a run in `needs_attention` is deliberately never
  auto-dispatched, which is the guarantee that a crash does not silently
  restart a ticket. This command only marks the run eligible for resume.
  Actually continuing it requires the in-process orchestrator, so use
  `POST /api/v1/runs/{run_id}/resume` against the running service, or the
  resume action in the web execution panel.

Approving a gate is deliberately narrow: only `approval resolve`, with an
explicit `--approve` / `--reject`, can resolve one. No command infers a
decision from free-form text.

Exit codes
----------

0 success · 1 operational failure (unknown run, invalid workflow,
version conflict, illegal transition) · 2 usage error. Failures print
``symphony: <message>`` on stderr, matching `cli/main.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from ..errors import SymphonyError, WorkflowDefinitionInvalid
from ..flow import statuses as st
from ..flow.loader import WorkflowLoader
from ..flow.model import Diagnostic
from ..orchestrator.flow_store import (
    ApprovalRecord,
    GovernedRunRecord,
    GovernedRunStore,
)
from ..orchestrator.run_registry import RunRegistry, registry_path_for_workflow
from ..workflow import build_service_config, load_workflow, resolve_workflow_path


class _OperatorError(Exception):
    """An operational failure already phrased for the operator (exit 1)."""


class _UsageError(Exception):
    """A malformed invocation (exit 2), raised after argparse has parsed.

    argparse covers the static cases; this covers the one that depends on
    runtime context — `run abandon` without `--yes` on a non-TTY.
    """


# ---------------------------------------------------------------------------
# shared plumbing
# ---------------------------------------------------------------------------


def _fail(message: str) -> int:
    print(f"symphony: {message}", file=sys.stderr)
    return 1


def _add_workflow_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "workflow",
        nargs="?",
        default=None,
        help="path to WORKFLOW.md (default: ./WORKFLOW.md)",
    )


def _add_json_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="emit machine-readable JSON instead of the human table",
    )


def _print_json(payload: object) -> None:
    # `default=str` renders Path and datetime; every record here is a
    # frozen dataclass of scalars, so nothing else can reach it.
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _workflow_path(args: argparse.Namespace) -> Path:
    path = resolve_workflow_path(args.workflow)
    if not path.exists():
        raise _OperatorError(
            f"WORKFLOW.md not found at {path}. Pass a path explicitly or run "
            "from the board directory."
        )
    return path


def _loader(args: argparse.Namespace) -> WorkflowLoader:
    """Workflow loader configured exactly as the orchestrator configures it."""
    workflow_path = _workflow_path(args)
    cfg = build_service_config(load_workflow(workflow_path))
    directory = cfg.workflow_engine.directory
    if directory is None:
        raise _OperatorError("workflow_engine.directory is not configured")
    return WorkflowLoader(
        directory,
        workflow_dir=cfg.workflow_path.parent,
        max_parallel_nodes=cfg.workflow_engine.max_parallel_nodes,
    )


def _registry_path(args: argparse.Namespace) -> Path:
    return registry_path_for_workflow(_workflow_path(args))


def _with_store(
    args: argparse.Namespace, body: Callable[[GovernedRunStore], int]
) -> int:
    """Open the ledger for one command and always close it.

    Refuses to run when `.symphony/state.db` is absent rather than letting
    `RunRegistry` create an empty one as a side effect of a read.
    """
    path = _registry_path(args)
    if not path.exists():
        raise _OperatorError(
            f"no run ledger at {path}; this board has never dispatched a "
            "governed run."
        )
    registry = RunRegistry(path)
    try:
        return body(registry.governed)
    finally:
        registry.close()


def _require_run(store: GovernedRunStore, run_id: str) -> GovernedRunRecord:
    record = store.get_governed_run(run_id)
    if record is None:
        raise _OperatorError(
            f"no governed run {run_id!r} in this ledger (legacy stage-loop "
            "runs are listed by `symphony runs`)"
        )
    return record


def _ts(value: datetime | None) -> str:
    return value.isoformat() if value is not None else "-"


def _or_dash(value: object) -> str:
    return "-" if value is None or value == "" else str(value)


def _short_hash(value: str | None) -> str:
    return value[:12] if value else "-"


def _diagnostics(exc: WorkflowDefinitionInvalid) -> tuple[Diagnostic, ...]:
    raw = exc.context.get("diagnostics")
    if not isinstance(raw, tuple):
        return ()
    return tuple(item for item in raw if isinstance(item, Diagnostic))


def _diagnostic_source(exc: WorkflowDefinitionInvalid, fallback: Path) -> str:
    raw = exc.context.get("source")
    return str(raw) if raw else str(fallback)


def _report_invalid(
    exc: WorkflowDefinitionInvalid, *, source: Path, as_json: bool
) -> int:
    """Print every diagnostic, not just the three in the summary."""
    rendered = _diagnostic_source(exc, source)
    diagnostics = _diagnostics(exc)
    if as_json:
        _print_json(
            {
                "valid": False,
                "source": rendered,
                # `str(exc)` would inline the whole diagnostics repr, which
                # duplicates the list below; the code + summary is enough.
                "error": f"{exc.code}: {exc.message}",
                "diagnostics": [
                    {
                        "path": diag.path,
                        "message": diag.message,
                        "line": diag.line,
                        "rendered": diag.render(rendered),
                    }
                    for diag in diagnostics
                ],
            }
        )
        return 1
    _fail(f"{rendered} is not a valid workflow ({len(diagnostics)} problems)")
    if not diagnostics:
        # Some decode failures (unreadable YAML, oversized file) carry no
        # per-field diagnostics; the message is all there is.
        print(f"  {exc.message}", file=sys.stderr)
    for diag in diagnostics:
        print(f"  {diag.render(rendered)}", file=sys.stderr)
    return 1


def _run_group_main(
    parser: argparse.ArgumentParser, argv: Sequence[str]
) -> int:
    args = parser.parse_args(list(argv))
    try:
        return args.func(args)
    except _UsageError as exc:
        print(f"symphony: {exc}", file=sys.stderr)
        return 2
    except _OperatorError as exc:
        return _fail(str(exc))
    except SymphonyError as exc:
        # Typed store errors already stringify as `code: message (context)`,
        # so the stable error code reaches the operator and any script
        # grepping stderr.
        return _fail(str(exc))


# ---------------------------------------------------------------------------
# symphony workflow
# ---------------------------------------------------------------------------


def cmd_workflow_list(args: argparse.Namespace) -> int:
    loader = _loader(args)
    entries = loader.list_workflows()
    if args.as_json:
        _print_json([entry.to_json() for entry in entries])
        return 0
    if not entries:
        print(f"no workflow files under {loader.directory}")
        return 0
    print(f"{'name':<24} {'valid':<6} {'nodes':<6} hash")
    for entry in entries:
        print(
            f"{entry.name:<24} {'yes' if entry.valid else 'no':<6} "
            f"{entry.node_count:<6} {_short_hash(entry.workflow_hash)}"
        )
    for entry in entries:
        if not entry.valid:
            print(f"  {entry.name}: {entry.error}", file=sys.stderr)
    return 0


def cmd_workflow_show(args: argparse.Namespace) -> int:
    loader = _loader(args)
    try:
        compiled = loader.load(args.name)
    except WorkflowDefinitionInvalid as exc:
        return _report_invalid(
            exc, source=loader.directory / f"{args.name}.yaml", as_json=args.as_json
        )
    if args.as_json:
        _print_json(compiled.to_json())
        return 0
    definition = compiled.definition
    print(f"{compiled.name} v{compiled.version}")
    print(f"source: {definition.source_path}")
    print(f"workflow_hash: {compiled.workflow_hash}")
    if definition.description:
        print(f"description: {definition.description}")
    print(f"backends: {', '.join(sorted(compiled.required_backends)) or '-'}")
    print(
        f"capabilities: {', '.join(sorted(compiled.required_capabilities)) or '-'}"
    )
    print("layers:")
    for index, layer in enumerate(compiled.layers, start=1):
        print(f"  {index}. {', '.join(layer)}")
    print("nodes:")
    for node in compiled.nodes:
        depends = ", ".join(node.depends_on) or "-"
        print(
            f"  {node.id:<20} {node.type:<9} access={node.workspace_access:<6} "
            f"depends_on={depends}"
        )
    risk = compiled.risk
    if risk.has_risk:
        print("risk:")
        if risk.shell_node_ids:
            print(f"  shell: {', '.join(risk.shell_node_ids)}")
        if risk.external_side_effect_node_ids:
            print(
                "  external side effects: "
                f"{', '.join(risk.external_side_effect_node_ids)}"
            )
        if risk.ungated_external_node_ids:
            print(
                "  ungated external side effects: "
                f"{', '.join(risk.ungated_external_node_ids)}"
            )
    return 0


def cmd_workflow_validate(args: argparse.Namespace) -> int:
    loader = _loader(args)
    path = Path(args.path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.is_file():
        raise _OperatorError(f"no such workflow file: {path}")
    try:
        compiled = loader.compile_text(
            path.read_text(encoding="utf-8"), source_path=path
        )
    except WorkflowDefinitionInvalid as exc:
        return _report_invalid(exc, source=path, as_json=args.as_json)
    if args.as_json:
        _print_json(
            {
                "valid": True,
                "source": str(path),
                "name": compiled.name,
                "workflow_hash": compiled.workflow_hash,
                "node_count": len(compiled.nodes),
                "diagnostics": [],
            }
        )
        return 0
    print(
        f"{path} is valid: {compiled.name} v{compiled.version}, "
        f"{len(compiled.nodes)} nodes, hash {compiled.workflow_hash}"
    )
    return 0


def build_workflow_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="symphony workflow",
        description="Inspect and validate governed workflow definitions.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list workflow files and their validity")
    _add_workflow_arg(p_list)
    _add_json_arg(p_list)
    p_list.set_defaults(func=cmd_workflow_list)

    p_show = sub.add_parser("show", help="print one compiled workflow")
    p_show.add_argument("name", help="workflow name (the file stem)")
    _add_workflow_arg(p_show)
    _add_json_arg(p_show)
    p_show.set_defaults(func=cmd_workflow_show)

    p_validate = sub.add_parser(
        "validate", help="compile a YAML file and print every diagnostic"
    )
    p_validate.add_argument("path", help="path to a workflow YAML file")
    _add_workflow_arg(p_validate)
    _add_json_arg(p_validate)
    p_validate.set_defaults(func=cmd_workflow_validate)

    return parser


def workflow_main(argv: list[str]) -> int:
    return _run_group_main(build_workflow_parser(), argv)


# ---------------------------------------------------------------------------
# symphony run
# ---------------------------------------------------------------------------


def _run_header_lines(record: GovernedRunRecord) -> list[str]:
    return [
        f"run {record.run_id}  {record.identifier} (issue {record.issue_id})",
        f"status: {record.execution_status}  mode: {record.execution_mode}",
        f"workflow: {_or_dash(record.workflow_name)} "
        f"v{_or_dash(record.workflow_version)}",
        f"workflow_hash: {_or_dash(record.workflow_hash)}",
        f"workspace: {record.workspace_path}",
        f"attention_reason: {_or_dash(record.attention_reason)}",
        f"terminal_reason: {_or_dash(record.terminal_reason)}",
        f"started: {_ts(record.started_at)}  updated: {_ts(record.updated_at)}  "
        f"completed: {_ts(record.completed_at)}",
    ]


def cmd_run_show(args: argparse.Namespace) -> int:
    def body(store: GovernedRunStore) -> int:
        record = _require_run(store, args.run_id)
        nodes = store.list_node_runs(record.run_id)
        approvals = store.list_approvals(run_id=record.run_id)
        artifacts = store.list_artifacts(record.run_id)
        fence = store.fence_for_issue(record.issue_id)
        if args.as_json:
            _print_json(
                {
                    "run": asdict(record),
                    "nodes": [asdict(node) for node in nodes],
                    "approvals": [asdict(item) for item in approvals],
                    "artifacts": [asdict(item) for item in artifacts],
                    "fence": asdict(fence) if fence is not None else None,
                }
            )
            return 0
        for line in _run_header_lines(record):
            print(line)
        print(f"fence: {fence.reason if fence is not None else '-'}")
        print("nodes:")
        if not nodes:
            print("  (none)")
        for node in nodes:
            print(
                f"  {node.node_id:<20} #{node.attempt}  {node.node_type:<9} "
                f"{node.status:<16} {_or_dash(node.error_code)}"
            )
        print("approvals:")
        if not approvals:
            print("  (none)")
        for item in approvals:
            print(
                f"  {item.approval_id}  {item.node_id:<20} {item.status:<9} "
                f"v{item.version}  {item.title}"
            )
        print(f"artifacts: {len(artifacts)}")
        return 0

    return _with_store(args, body)


def cmd_run_events(args: argparse.Namespace) -> int:
    def body(store: GovernedRunStore) -> int:
        record = _require_run(store, args.run_id)
        events = store.events_after(
            record.run_id, after_seq=args.after_seq, limit=args.limit
        )
        if args.as_json:
            _print_json([asdict(event) for event in events])
            return 0
        if not events:
            print(f"no events after seq {args.after_seq} for run {record.run_id}")
            return 0
        print(f"{'seq':<6} {'time':<32} {'node':<20} type  payload")
        for event in events:
            payload = json.dumps(event.payload, sort_keys=True, ensure_ascii=False)
            print(
                f"{event.seq:<6} {_ts(event.created_at):<32} "
                f"{_or_dash(event.node_id):<20} {event.type}  {payload}"
            )
        return 0

    return _with_store(args, body)


def cmd_run_resume(args: argparse.Namespace) -> int:
    """Mark a stalled run resumable. The running service does the work.

    See the module docstring: this process owns no executor, so the only
    honest write is the ledger status the service reconciles from.
    """

    def body(store: GovernedRunStore) -> int:
        record = _require_run(store, args.run_id)
        snapshot = (
            store.get_workflow_snapshot(record.workflow_hash)
            if record.workflow_hash
            else None
        )
        # PRD §17 — the hash of the definition a resume would replay is
        # printed before anything else, on every path.
        if not args.as_json:
            print(f"run: {record.run_id}")
            print(f"workflow_hash: {_or_dash(record.workflow_hash)}")
            print(f"workflow_snapshot: {'stored' if snapshot else 'missing'}")

        if record.execution_status in st.TERMINAL_RUN_STATUSES:
            raise _OperatorError(
                f"cannot resume run {record.run_id}: it is already "
                f"{record.execution_status}"
            )
        if record.execution_status == st.RUN_WAITING_APPROVAL:
            raise _OperatorError(
                f"run {record.run_id} is waiting on a human gate; resolve it "
                "with `symphony approval resolve` instead of resuming"
            )

        already = record.execution_status == st.RUN_NEEDS_ATTENTION
        if not already:
            store.set_run_status(
                run_id=record.run_id,
                status=st.RUN_NEEDS_ATTENTION,
                attention_reason=st.ATTENTION_INTERRUPTED,
            )
        # Say what actually happens. Nothing auto-resumes a needs_attention
        # run — that is the point of the state — so telling the operator to
        # wait would leave them waiting indefinitely.
        next_step = (
            f"To continue it, call POST /api/v1/runs/{record.run_id}/resume "
            "on the running service, or use the resume action in the web "
            "execution panel. This CLI cannot drive the executor."
        )
        message = (
            f"run is already marked needs_attention. {next_step}"
            if already
            else f"run marked needs_attention. {next_step}"
        )
        if args.as_json:
            _print_json(
                {
                    "run_id": record.run_id,
                    "workflow_hash": record.workflow_hash,
                    "workflow_snapshot": "stored" if snapshot else "missing",
                    "execution_status": st.RUN_NEEDS_ATTENTION,
                    "changed": not already,
                    "message": message,
                }
            )
            return 0
        print(message)
        return 0

    return _with_store(args, body)


def _confirm_abandon(args: argparse.Namespace, run_id: str) -> None:
    if args.yes:
        return
    if not sys.stdin.isatty():
        # Blocking on input() in a pipeline or CI job would hang forever.
        raise _UsageError(
            f"refusing to abandon run {run_id} without confirmation: stdin is "
            "not a terminal, so pass --yes explicitly"
        )
    answer = input(f"abandon run {run_id}? this releases its fence [y/N]: ")
    if answer.strip().lower() not in {"y", "yes"}:
        raise _OperatorError("aborted; run left untouched")


def cmd_run_abandon(args: argparse.Namespace) -> int:
    def body(store: GovernedRunStore) -> int:
        record = _require_run(store, args.run_id)
        _confirm_abandon(args, record.run_id)
        store.set_run_status(
            run_id=record.run_id,
            status=st.RUN_ABANDONED,
            terminal_reason="operator_abandon",
        )
        return _report_terminal(args, record, st.RUN_ABANDONED, "operator_abandon")

    return _with_store(args, body)


def cmd_run_cancel(args: argparse.Namespace) -> int:
    def body(store: GovernedRunStore) -> int:
        record = _require_run(store, args.run_id)
        store.set_run_status(
            run_id=record.run_id,
            status=st.RUN_CANCELLED,
            terminal_reason="operator_cancel",
        )
        return _report_terminal(args, record, st.RUN_CANCELLED, "operator_cancel")

    return _with_store(args, body)


def _report_terminal(
    args: argparse.Namespace,
    record: GovernedRunRecord,
    status: str,
    terminal_reason: str,
) -> int:
    if args.as_json:
        _print_json(
            {
                "run_id": record.run_id,
                "issue_id": record.issue_id,
                "execution_status": status,
                "terminal_reason": terminal_reason,
                "fence_released": True,
            }
        )
        return 0
    print(f"run {record.run_id} -> {status} ({terminal_reason})")
    print(f"fence released for issue {record.issue_id}")
    return 0


def build_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="symphony run",
        description="Inspect and steer governed workflow runs.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_show = sub.add_parser("show", help="print one run with its nodes and gates")
    p_show.add_argument("run_id")
    _add_workflow_arg(p_show)
    _add_json_arg(p_show)
    p_show.set_defaults(func=cmd_run_show)

    p_events = sub.add_parser("events", help="print the run's event ledger")
    p_events.add_argument("run_id")
    _add_workflow_arg(p_events)
    p_events.add_argument(
        "--after-seq", type=int, default=0, help="only events with seq > N"
    )
    p_events.add_argument(
        "--limit", type=int, default=200, help="max events (clamped to 1-2000)"
    )
    _add_json_arg(p_events)
    p_events.set_defaults(func=cmd_run_events)

    p_resume = sub.add_parser(
        "resume", help="mark a stalled run for the running service to resume"
    )
    p_resume.add_argument("run_id")
    _add_workflow_arg(p_resume)
    _add_json_arg(p_resume)
    p_resume.set_defaults(func=cmd_run_resume)

    p_abandon = sub.add_parser(
        "abandon", help="terminate a run and release its issue fence"
    )
    p_abandon.add_argument("run_id")
    _add_workflow_arg(p_abandon)
    p_abandon.add_argument(
        "--yes", action="store_true", help="skip the interactive confirmation"
    )
    _add_json_arg(p_abandon)
    p_abandon.set_defaults(func=cmd_run_abandon)

    p_cancel = sub.add_parser("cancel", help="cancel a run and release its fence")
    p_cancel.add_argument("run_id")
    _add_workflow_arg(p_cancel)
    _add_json_arg(p_cancel)
    p_cancel.set_defaults(func=cmd_run_cancel)

    return parser


def run_main(argv: list[str]) -> int:
    return _run_group_main(build_run_parser(), argv)


# ---------------------------------------------------------------------------
# symphony approval
# ---------------------------------------------------------------------------


def _approval_row(item: ApprovalRecord) -> str:
    return (
        f"{item.approval_id}  {item.status:<9} v{item.version:<3} "
        f"{item.node_id:<20} {item.title}"
    )


def cmd_approval_list(args: argparse.Namespace) -> int:
    path = _registry_path(args)
    if not path.exists():
        # An empty ledger is an empty list, not a failure — `approval list`
        # is the command an operator polls.
        if args.as_json:
            _print_json([])
        else:
            print("no approvals")
        return 0

    def body(store: GovernedRunStore) -> int:
        status = None if args.status == "all" else args.status
        items = store.list_approvals(status=status, run_id=args.run)
        if args.as_json:
            _print_json([asdict(item) for item in items])
            return 0
        if not items:
            print(f"no {args.status} approvals")
            return 0
        for item in items:
            print(_approval_row(item))
        return 0

    return _with_store(args, body)


def cmd_approval_resolve(args: argparse.Namespace) -> int:
    decision = st.APPROVAL_APPROVED if args.approve else st.APPROVAL_REJECTED

    def body(store: GovernedRunStore) -> int:
        if store.get_approval(args.approval_id) is None:
            raise _OperatorError(f"no approval {args.approval_id!r} in this ledger")
        record = store.resolve_approval(
            approval_id=args.approval_id,
            decision=decision,
            expected_version=args.version,
            actor=args.actor,
            source="cli",
            comment=args.comment,
        )
        if args.as_json:
            _print_json(asdict(record))
            return 0
        print(
            f"approval {record.approval_id} -> {record.status} "
            f"(v{record.version}, actor {_or_dash(record.actor)})"
        )
        print(f"run {record.run_id} node {record.node_id}")
        return 0

    return _with_store(args, body)


def build_approval_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="symphony approval",
        description="List and resolve human approval gates.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list approval gates")
    _add_workflow_arg(p_list)
    p_list.add_argument(
        "--status",
        default=st.APPROVAL_PENDING,
        choices=[
            st.APPROVAL_PENDING,
            st.APPROVAL_APPROVED,
            st.APPROVAL_REJECTED,
            "all",
        ],
        help="filter by status (default: pending)",
    )
    p_list.add_argument("--run", default=None, help="filter by run id")
    _add_json_arg(p_list)
    p_list.set_defaults(func=cmd_approval_list)

    p_resolve = sub.add_parser("resolve", help="approve or reject one gate")
    p_resolve.add_argument("approval_id")
    _add_workflow_arg(p_resolve)
    decision = p_resolve.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true", help="approve the gate")
    decision.add_argument("--reject", action="store_true", help="reject the gate")
    p_resolve.add_argument(
        "--version",
        type=int,
        default=None,
        help="expected approval version; a mismatch fails instead of racing",
    )
    p_resolve.add_argument("--comment", default=None, help="decision rationale")
    p_resolve.add_argument("--actor", default=None, help="who is deciding")
    _add_json_arg(p_resolve)
    p_resolve.set_defaults(func=cmd_approval_resolve)

    return parser


def approval_main(argv: list[str]) -> int:
    return _run_group_main(build_approval_parser(), argv)
