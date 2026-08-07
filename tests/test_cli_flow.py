"""`symphony workflow|run|approval ...` — the governed-run operator CLI.

Every test drives the real module against a real `.symphony/state.db` and a
real `.symphony/workflows/` directory in `tmp_path`; nothing is mocked below
the CLI. The point of these commands is to tell an operator the truth about
the ledger, so a fake store would test nothing worth testing.

Covered:

  * workflow list / show                human table + `--json`
  * workflow validate                   valid file, and an invalid one where
                                        **every** diagnostic must be printed
  * run show / events                   `--json` shapes
  * run abandon                         non-TTY without `--yes` is a usage
                                        error (exit 2), never a hang
  * run abandon --yes                   terminal status + fence release
  * approval list / resolve             happy path
  * approval resolve --version          stale version exits 1 with the
                                        stable `approval_version_conflict`
                                        code in the message
  * cli.main routing                    `workflow` / `run` / `approval` arms
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from symphony.cli import flow as flow_cli
from symphony.issue import Issue
from symphony.orchestrator.run_registry import RunRegistry, registry_path_for_workflow

# `symphony/cli/__init__.py` re-exports `main` as a function, shadowing the
# submodule attribute — same dance as tests/test_cli_main_routing.py.
cli_main = importlib.import_module("symphony.cli.main")


VALID_WORKFLOW = """\
version: 1
name: demo
description: Demo workflow.
nodes:
  - id: plan
    type: agent
    workspace_access: read
    output_type: plan
    prompt: Plan ${ticket.identifier}
  - id: gate
    type: approval
    depends_on: [plan]
    title: Approve the plan
    instructions: Check the plan.
    evidence: [plan]
"""

# Two independent problems, so "prints every diagnostic" is falsifiable:
# the agent node has no prompt, and the shell node has no `run`.
INVALID_WORKFLOW = """\
version: 1
name: broken
nodes:
  - id: a
    type: agent
  - id: b
    type: shell
"""


@pytest.fixture
def board(tmp_path: Path) -> Path:
    """A WORKFLOW.md with governed mode on and one valid workflow file."""
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        "---\n"
        "tracker:\n"
        "  kind: file\n"
        "  board_root: ./board\n"
        "workflow_engine:\n"
        "  enabled: true\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    workflows = tmp_path / ".symphony" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "demo.yaml").write_text(VALID_WORKFLOW, encoding="utf-8")
    return workflow


def _issue(identifier: str = "MT-1") -> Issue:
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return Issue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=f"{identifier} title",
        description="",
        priority=None,
        state="In Progress",
        created_at=stamp,
        updated_at=stamp,
    )


@pytest.fixture
def governed_run(board: Path) -> dict[str, str]:
    """One governed run parked on a pending approval gate.

    Mirrors the executor's own write order (promote -> running -> node
    attempt -> gate -> waiting_approval) so the CLI reads exactly the row
    shapes it will see in production.
    """
    registry = RunRegistry(registry_path_for_workflow(board))
    issue = _issue()
    run_id = registry.acquire_run(
        issue,
        workspace_path=board.parent / "ws",
        attempt=1,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id is not None
    store = registry.governed
    store.begin_governed_run(
        run_id=run_id,
        issue_id=issue.id,
        workflow_name="demo",
        workflow_version=1,
        workflow_hash="deadbeef" * 8,
        ticket_snapshot={"identifier": issue.identifier},
    )
    store.set_run_status(run_id=run_id, status="running")
    attempt = store.start_node_attempt(
        run_id=run_id, node_id="plan", node_type="agent"
    )
    store.finish_node_attempt(node_run_id=attempt.node_run_id, status="succeeded")
    approval = store.create_approval(
        run_id=run_id,
        node_id="gate",
        node_attempt=1,
        title="Approve the plan",
        instructions="Check the plan.",
    )
    store.set_run_status(run_id=run_id, status="waiting_approval")
    registry.close()
    return {
        "run_id": run_id,
        "approval_id": approval.approval_id,
        "issue_id": issue.id,
    }


@pytest.fixture
def no_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin stdin to a non-terminal so confirmation prompts cannot block."""

    class _Pipe:
        def isatty(self) -> bool:
            return False

        def readline(self) -> str:  # pragma: no cover - must never be reached
            raise AssertionError("the CLI blocked on input() with a non-TTY stdin")

    monkeypatch.setattr("sys.stdin", _Pipe())


# ---------------------------------------------------------------------------
# symphony workflow
# ---------------------------------------------------------------------------


def test_workflow_list_prints_each_file_and_its_validity(
    board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = flow_cli.workflow_main(["list", str(board)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "demo" in out
    assert "yes" in out


def test_workflow_list_json_carries_hash_and_node_count(
    board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = flow_cli.workflow_main(["list", str(board), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert [entry["name"] for entry in payload] == ["demo"]
    assert payload[0]["valid"] is True
    assert payload[0]["node_count"] == 2
    assert payload[0]["workflow_hash"]


def test_workflow_show_prints_layers_and_nodes(
    board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = flow_cli.workflow_main(["show", "demo", str(board)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "demo v1" in out
    assert "workflow_hash:" in out
    # The gate depends on the plan, so they must land in separate layers.
    assert "1. plan" in out
    assert "2. gate" in out


def test_workflow_show_unknown_name_exits_one(
    board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = flow_cli.workflow_main(["show", "missing", str(board)])
    assert rc == 1
    assert "symphony: workflow_not_found" in capsys.readouterr().err


def test_workflow_validate_accepts_a_good_file(
    board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = board.parent / ".symphony" / "workflows" / "demo.yaml"
    rc = flow_cli.workflow_main(["validate", str(target), str(board)])
    assert rc == 0
    assert "is valid" in capsys.readouterr().out


def test_workflow_validate_prints_every_diagnostic(
    board: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not just the three the exception summary keeps."""
    broken = tmp_path / "broken.yaml"
    broken.write_text(INVALID_WORKFLOW, encoding="utf-8")
    rc = flow_cli.workflow_main(["validate", str(broken), str(board)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "is not a valid workflow (2 problems)" in err
    assert "[nodes[0]]" in err
    assert "[nodes[1].run]" in err
    # One diagnostic per line, plus the `symphony: ...` header.
    assert len([line for line in err.splitlines() if line.strip()]) == 3


def test_workflow_validate_json_lists_diagnostics(
    board: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text(INVALID_WORKFLOW, encoding="utf-8")
    rc = flow_cli.workflow_main(["validate", str(broken), str(board), "--json"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is False
    assert payload["error"].startswith("workflow_invalid:")
    assert [item["path"] for item in payload["diagnostics"]] == [
        "nodes[0]",
        "nodes[1].run",
    ]


# ---------------------------------------------------------------------------
# symphony run
# ---------------------------------------------------------------------------


def test_run_show_json_includes_nodes_approvals_and_fence(
    board: Path, governed_run: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    rc = flow_cli.run_main(["show", governed_run["run_id"], str(board), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["execution_status"] == "waiting_approval"
    assert payload["run"]["workflow_hash"] == "deadbeef" * 8
    assert [node["node_id"] for node in payload["nodes"]] == ["plan"]
    assert [item["status"] for item in payload["approvals"]] == ["pending"]
    # A run parked on a gate holds no lease but must still hold its fence.
    assert payload["fence"]["reason"] == "waiting_approval"


def test_run_show_unknown_run_exits_one(
    board: Path, governed_run: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    rc = flow_cli.run_main(["show", "nosuchrun", str(board)])
    assert rc == 1
    assert "no governed run 'nosuchrun'" in capsys.readouterr().err


def test_run_events_json_respects_after_seq(
    board: Path, governed_run: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = governed_run["run_id"]
    rc = flow_cli.run_main(["events", run_id, str(board), "--json"])
    assert rc == 0
    everything = json.loads(capsys.readouterr().out)
    assert [event["type"] for event in everything][:2] == [
        "run_created",
        "run_status_changed",
    ]

    rc = flow_cli.run_main(
        ["events", run_id, str(board), "--after-seq", "4", "--json"]
    )
    assert rc == 0
    tail = json.loads(capsys.readouterr().out)
    assert [event["seq"] for event in tail] == [
        event["seq"] for event in everything if event["seq"] > 4
    ]


def test_run_resume_prints_the_workflow_hash(
    board: Path, governed_run: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """PRD §17 — resume must state which definition would be replayed.

    This run is on a gate, so resume also has to refuse; the hash is still
    printed, because the operator needs it either way.
    """
    rc = flow_cli.run_main(["resume", governed_run["run_id"], str(board)])
    assert rc == 1
    captured = capsys.readouterr()
    assert f"workflow_hash: {'deadbeef' * 8}" in captured.out
    assert "symphony approval resolve" in captured.err


def test_run_resume_marks_a_stalled_run_needs_attention(
    board: Path, governed_run: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    registry = RunRegistry(registry_path_for_workflow(board))
    registry.governed.set_run_status(
        run_id=governed_run["run_id"], status="running"
    )
    registry.close()

    rc = flow_cli.run_main(["resume", governed_run["run_id"], str(board), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution_status"] == "needs_attention"
    assert payload["changed"] is True
    # Honest about the limitation: the CLI does not drive the executor.
    assert "service" in payload["message"]

    # Re-running is a no-op rather than a second status write.
    rc = flow_cli.run_main(["resume", governed_run["run_id"], str(board), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["changed"] is False


def test_run_abandon_without_yes_on_a_pipe_is_a_usage_error(
    board: Path,
    governed_run: dict[str, str],
    no_tty: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = flow_cli.run_main(["abandon", governed_run["run_id"], str(board)])
    assert rc == 2
    assert "pass --yes explicitly" in capsys.readouterr().err
    # And the run is untouched.
    registry = RunRegistry(registry_path_for_workflow(board))
    record = registry.governed.get_governed_run(governed_run["run_id"])
    registry.close()
    assert record is not None
    assert record.execution_status == "waiting_approval"


def test_run_abandon_with_yes_terminates_and_releases_the_fence(
    board: Path, governed_run: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    rc = flow_cli.run_main(
        ["abandon", governed_run["run_id"], str(board), "--yes"]
    )
    assert rc == 0
    assert "abandoned" in capsys.readouterr().out
    registry = RunRegistry(registry_path_for_workflow(board))
    store = registry.governed
    record = store.get_governed_run(governed_run["run_id"])
    fence = store.fence_for_issue(governed_run["issue_id"])
    registry.close()
    assert record is not None
    assert record.execution_status == "abandoned"
    assert record.terminal_reason == "operator_abandon"
    assert fence is None


def test_run_cancel_on_a_terminal_run_surfaces_the_illegal_transition(
    board: Path, governed_run: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert flow_cli.run_main(
        ["abandon", governed_run["run_id"], str(board), "--yes"]
    ) == 0
    capsys.readouterr()
    rc = flow_cli.run_main(["cancel", governed_run["run_id"], str(board)])
    assert rc == 1
    assert "symphony: illegal_run_transition" in capsys.readouterr().err


def test_run_show_without_a_ledger_exits_one(
    board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A read must never create `.symphony/state.db` as a side effect."""
    rc = flow_cli.run_main(["show", "whatever", str(board)])
    assert rc == 1
    assert "no run ledger at" in capsys.readouterr().err
    assert not registry_path_for_workflow(board).exists()


# ---------------------------------------------------------------------------
# symphony approval
# ---------------------------------------------------------------------------


def test_approval_list_defaults_to_pending(
    board: Path, governed_run: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    rc = flow_cli.approval_main(["list", str(board), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["approval_id"] for item in payload] == [
        governed_run["approval_id"]
    ]
    assert payload[0]["title"] == "Approve the plan"


def test_approval_list_without_a_ledger_is_an_empty_list(
    board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = flow_cli.approval_main(["list", str(board), "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_approval_resolve_approves_and_bumps_the_version(
    board: Path, governed_run: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    rc = flow_cli.approval_main(
        [
            "resolve",
            governed_run["approval_id"],
            str(board),
            "--approve",
            "--version",
            "1",
            "--actor",
            "operator",
            "--comment",
            "plan looks right",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "approved"
    assert payload["decision"] == "approved"
    assert payload["version"] == 2
    assert payload["actor"] == "operator"
    assert payload["source"] == "cli"


def test_approval_resolve_stale_version_exits_one_with_the_error_code(
    board: Path, governed_run: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    rc = flow_cli.approval_main(
        [
            "resolve",
            governed_run["approval_id"],
            str(board),
            "--reject",
            "--version",
            "7",
        ]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "symphony: approval_version_conflict" in err
    assert "actual_version=1" in err


def test_approval_resolve_requires_a_decision_flag(board: Path) -> None:
    """No gate is ever resolved without an explicit --approve / --reject."""
    with pytest.raises(SystemExit) as excinfo:
        flow_cli.approval_main(["resolve", "some-id", str(board)])
    assert excinfo.value.code == 2


def test_approval_resolve_unknown_id_exits_one(
    board: Path, governed_run: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    rc = flow_cli.approval_main(["resolve", "nope", str(board), "--approve"])
    assert rc == 1
    assert "no approval 'nope'" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cli.main routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "target"),
    [
        ("workflow", "workflow_main"),
        ("run", "run_main"),
        ("approval", "approval_main"),
    ],
)
def test_main_routes_each_group_and_strips_the_token(
    token: str, target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    def fake(argv: list[str]) -> int:
        seen["argv"] = argv
        return 11

    monkeypatch.setattr(f"symphony.cli.flow.{target}", fake)
    assert cli_main.main([token, "list", "--json"]) == 11
    assert seen["argv"] == ["list", "--json"]


def test_main_workflow_list_end_to_end(
    board: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Routing plus the real command, so the lazy import is exercised."""
    assert cli_main.main(["workflow", "list", str(board)]) == 0
    assert "demo" in capsys.readouterr().out
