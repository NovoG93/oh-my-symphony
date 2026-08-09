"""`symphony release check` — host-side application release validation."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from ..errors import SymphonyError
from ..orchestrator.release_contracts import validate_release_contract
from ..workflow import (
    build_service_config,
    load_workflow,
    resolve_workflow_path,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="symphony release",
        description="Validate machine-bound application release evidence.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser(
        "check",
        help="validate release-contract.yaml and one verifier's evidence",
    )
    check.add_argument(
        "workflow",
        nargs="?",
        default=None,
        help="path to WORKFLOW.md (default: ./WORKFLOW.md)",
    )
    check.add_argument("--ticket", required=True, help="current verifier ticket ID")
    check.add_argument(
        "--workspace",
        required=True,
        help="ticket workspace containing release-contract.yaml and docs/<ticket>/qa",
    )
    check.add_argument(
        "--json",
        action="store_true",
        help="emit the immutable validation result as JSON",
    )
    return parser


def _check(args: argparse.Namespace) -> int:
    workflow_path = resolve_workflow_path(args.workflow)
    if not workflow_path.is_file():
        print(f"symphony release: workflow file not found: {workflow_path}", file=sys.stderr)
        return 2
    try:
        cfg = build_service_config(load_workflow(workflow_path))
    except SymphonyError as exc:
        print(f"symphony release: workflow load failed: {exc}", file=sys.stderr)
        return 2

    result = validate_release_contract(
        workspace_root=Path(args.workspace).expanduser(),
        repository_root=cfg.workflow_path.parent,
        verifier_ticket=args.ticket,
        configured_target_branch=cfg.agent.auto_merge_target_branch,
        board_root=cfg.tracker.board_root,
    )
    if args.json:
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
    else:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status}  app.release-contract")
        print(result.note_text)
        if result.target_sha:
            print(f"Target: {result.target_branch}@{result.target_sha}")
        if result.contract_sha256:
            print(f"Contract SHA-256: {result.contract_sha256}")
        print(f"Fingerprint: {result.fingerprint}")
    return 0 if result.passed else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "check":
        return _check(args)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
