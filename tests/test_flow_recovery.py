"""Fault-injection checks for governed run recovery (PRD §25.3).

Each test injects a crash at one of the checkpoints the PRD enumerates,
then runs startup reconciliation and asserts the run lands in exactly one
unambiguous state. The property under test is the same every time:

    after any crash, the ticket is never quietly started over.

That is why almost every assertion pairs a status with a fence check. A
correct status with a released fence would still let the next poll tick
dispatch the ticket again, which is the failure mode the whole fence
mechanism exists to prevent.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from symphony.flow import statuses as st
from symphony.flow.artifacts import ArtifactStore
from symphony.issue import Issue
from symphony.orchestrator import Orchestrator
from symphony.orchestrator.run_registry import RunRegistry
from symphony.workflow import WorkflowState, build_service_config
from symphony.workflow.parser import parse_workflow_text


WORKFLOW_MD = textwrap.dedent(
    """\
    ---
    tracker:
      kind: file
      board_root: ./kanban
      active_states: [Todo, "In Progress", "Human Review", Done]
      terminal_states: [Done, Blocked]
    agent:
      kind: codex
      max_concurrent_agents: 1
      max_turns: 12
    workflow_engine:
      enabled: true
      directory: ./.symphony/workflows
      default: demo
      ticket_state_mapping:
        running: "In Progress"
        waiting_approval: "Human Review"
        succeeded: Done
    ---
    # Board
    """
)

WORKFLOW_YAML = textwrap.dedent(
    """\
    version: 1
    name: demo
    nodes:
      - id: plan
        type: agent
        workspace_access: read
        prompt: "plan it"
      - id: gate
        type: approval
        depends_on: [plan]
        title: Approve
        evidence: [plan]
    """
)


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, Any]:
    (tmp_path / "kanban").mkdir()
    (tmp_path / ".symphony" / "workflows").mkdir(parents=True)
    (tmp_path / "WORKFLOW.md").write_text(WORKFLOW_MD, encoding="utf-8")
    (tmp_path / ".symphony" / "workflows" / "demo.yaml").write_text(
        WORKFLOW_YAML, encoding="utf-8"
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()

    cfg = build_service_config(
        parse_workflow_text(
            (tmp_path / "WORKFLOW.md").read_text(encoding="utf-8"),
            source_path=tmp_path / "WORKFLOW.md",
        )
    )
    registry = RunRegistry(tmp_path / ".symphony" / "state.db")
    orchestrator = Orchestrator(WorkflowState(tmp_path / "WORKFLOW.md"))
    # Inject the registry directly: `_ensure_run_registry` would open a
    # second connection to the same file, and these tests need to observe
    # the rows they seeded.
    orchestrator._run_registry = registry
    return {
        "root": tmp_path,
        "cfg": cfg,
        "registry": registry,
        "store": registry.governed,
        "orchestrator": orchestrator,
        "workspace": workspace,
        "artifacts": ArtifactStore(tmp_path / ".symphony" / "artifacts"),
    }


def _issue() -> Issue:
    return Issue(
        id="issue-1",
        identifier="TASK-1",
        title="t",
        description="d",
        priority=None,
        state="In Progress",
    )


def _seed_run(env: dict[str, Any]) -> str:
    """A governed run that has been created and fenced, nothing more."""
    issue = _issue()
    run_id = env["registry"].acquire_run(
        issue,
        workspace_path=env["workspace"],
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
    )
    assert run_id is not None
    store = env["store"]
    store.put_workflow_snapshot(
        workflow_hash="hash-1",
        workflow_name="demo",
        schema_version=1,
        normalized_json='{"nodes":[]}',
        source_path="demo.yaml",
    )
    store.begin_governed_run(
        run_id=run_id,
        issue_id=issue.id,
        workflow_name="demo",
        workflow_version=1,
        workflow_hash="hash-1",
        ticket_snapshot={"id": issue.id, "identifier": issue.identifier},
    )
    return run_id


def _reconcile(env: dict[str, Any]) -> None:
    env["orchestrator"]._reconcile_governed_runs(env["cfg"])


def test_crash_while_a_node_was_running(env: dict[str, Any]) -> None:
    """Checkpoint: the process died with a backend mid-turn."""
    store = env["store"]
    run_id = _seed_run(env)
    store.set_run_status(run_id=run_id, status=st.RUN_RUNNING)
    store.start_node_attempt(
        run_id=run_id, node_id="plan", node_type="agent", backend_kind="codex"
    )

    _reconcile(env)

    record = store.get_governed_run(run_id)
    assert record is not None
    assert record.execution_status == st.RUN_NEEDS_ATTENTION
    assert record.attention_reason == st.ATTENTION_INTERRUPTED
    node = store.list_node_runs(run_id)[0]
    assert node.status == st.NODE_INTERRUPTED
    assert node.error_code == "process_interrupted"
    # The run is inactive but still owns the issue.
    assert store.fence_for_issue("issue-1") is not None


def test_crash_between_nodes(env: dict[str, Any]) -> None:
    """Checkpoint: a node succeeded, the next had not started."""
    store = env["store"]
    run_id = _seed_run(env)
    store.set_run_status(run_id=run_id, status=st.RUN_RUNNING)
    node = store.start_node_attempt(
        run_id=run_id, node_id="plan", node_type="agent", backend_kind="codex"
    )
    store.finish_node_attempt(
        node_run_id=node.node_run_id, status=st.NODE_SUCCEEDED, output_preview="ok"
    )

    _reconcile(env)

    record = store.get_governed_run(run_id)
    assert record is not None
    # Nothing was interrupted mid-flight, but the run still cannot restart
    # itself — the decision is the operator's.
    assert record.execution_status == st.RUN_NEEDS_ATTENTION
    assert record.attention_reason == st.ATTENTION_INTERRUPTED
    assert store.list_node_runs(run_id)[0].status == st.NODE_SUCCEEDED
    assert store.fence_for_issue("issue-1") is not None


def test_crash_after_a_gate_opened_leaves_it_waiting(env: dict[str, Any]) -> None:
    """Checkpoint: the approval row and suspension were already committed."""
    store = env["store"]
    run_id = _seed_run(env)
    store.set_run_status(run_id=run_id, status=st.RUN_RUNNING)
    node = store.start_node_attempt(
        run_id=run_id, node_id="gate", node_type="approval", workspace_access="none"
    )
    store.create_approval(
        run_id=run_id, node_id="gate", node_attempt=node.attempt, title="Approve"
    )
    store.set_node_status(
        node_run_id=node.node_run_id, status=st.NODE_WAITING_APPROVAL
    )
    store.set_run_status(run_id=run_id, status=st.RUN_WAITING_APPROVAL)

    _reconcile(env)

    record = store.get_governed_run(run_id)
    assert record is not None
    # A gate is a deliberate park, not damage — reconciliation must not
    # convert it into needs_attention and lose the pending decision.
    assert record.execution_status == st.RUN_WAITING_APPROVAL
    assert len(store.list_approvals(status=st.APPROVAL_PENDING, run_id=run_id)) == 1
    fence = store.fence_for_issue("issue-1")
    assert fence is not None and fence.reason == st.RUN_WAITING_APPROVAL
    assert env["registry"].get_issue_flags("issue-1").paused is True


def test_a_modified_artifact_blocks_the_run(env: dict[str, Any]) -> None:
    """Checkpoint: the artifact a downstream prompt interpolates changed."""
    store = env["store"]
    artifacts = env["artifacts"]
    run_id = _seed_run(env)
    store.set_run_status(run_id=run_id, status=st.RUN_RUNNING)
    node = store.start_node_attempt(
        run_id=run_id, node_id="plan", node_type="agent", backend_kind="codex"
    )
    stored = artifacts.write_text(
        run_id=run_id, node_id="plan", filename="output.txt", content="THE PLAN"
    )
    store.record_artifact(
        run_id=run_id,
        node_id="plan",
        artifact_type="plan",
        scope=st.SCOPE_RUNTIME,
        relative_path=stored.relative_path,
        media_type=stored.media_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
    )
    store.finish_node_attempt(
        node_run_id=node.node_run_id, status=st.NODE_SUCCEEDED, output_preview="THE PLAN"
    )
    # Someone edits the plan on disk between the crash and the restart.
    stored.absolute_path.write_text("A DIFFERENT PLAN", encoding="utf-8")

    _reconcile(env)

    record = store.get_governed_run(run_id)
    assert record is not None
    assert record.execution_status == st.RUN_NEEDS_ATTENTION
    assert record.attention_reason == st.ATTENTION_INTEGRITY_FAILED
    assert record.terminal_reason is not None
    assert "artifact_mismatch" in record.terminal_reason
    assert store.fence_for_issue("issue-1") is not None


def test_a_missing_workspace_blocks_the_run(env: dict[str, Any]) -> None:
    """Checkpoint: the worktree was removed while the run was parked."""
    import shutil

    store = env["store"]
    run_id = _seed_run(env)
    store.set_run_status(run_id=run_id, status=st.RUN_RUNNING)
    shutil.rmtree(env["workspace"])

    _reconcile(env)

    record = store.get_governed_run(run_id)
    assert record is not None
    assert record.execution_status == st.RUN_NEEDS_ATTENTION
    assert record.attention_reason == st.ATTENTION_INTEGRITY_FAILED
    assert record.terminal_reason is not None
    assert "workspace_missing" in record.terminal_reason


def test_reconciliation_is_idempotent(env: dict[str, Any]) -> None:
    """A restart loop must not keep rewriting history."""
    store = env["store"]
    run_id = _seed_run(env)
    store.set_run_status(run_id=run_id, status=st.RUN_RUNNING)
    store.start_node_attempt(
        run_id=run_id, node_id="plan", node_type="agent", backend_kind="codex"
    )

    _reconcile(env)
    first = store.get_governed_run(run_id)
    first_nodes = store.list_node_runs(run_id)
    first_events = len(store.events_after(run_id, after_seq=0, limit=2000))

    _reconcile(env)
    second = store.get_governed_run(run_id)
    second_nodes = store.list_node_runs(run_id)
    second_events = len(store.events_after(run_id, after_seq=0, limit=2000))

    assert first is not None and second is not None
    assert (first.execution_status, first.attention_reason) == (
        second.execution_status,
        second.attention_reason,
    )
    assert len(first_nodes) == len(second_nodes)
    # No new node attempt, and no duplicate interruption event.
    assert first_events == second_events


def test_a_terminal_run_releases_its_fence_and_pause_mirror(
    env: dict[str, Any],
) -> None:
    """Checkpoint: crash during terminalization, then a clean finish."""
    store = env["store"]
    run_id = _seed_run(env)
    store.set_run_status(run_id=run_id, status=st.RUN_RUNNING)
    store.set_run_status(run_id=run_id, status=st.RUN_WAITING_APPROVAL)
    assert env["registry"].get_issue_flags("issue-1").paused is True

    store.set_run_status(
        run_id=run_id, status=st.RUN_ABANDONED, terminal_reason="operator_abandon"
    )

    assert store.fence_for_issue("issue-1") is None
    flags = env["registry"].get_issue_flags("issue-1")
    assert flags is None or flags.paused is False
    # History survives abandonment.
    assert store.get_governed_run(run_id) is not None
    _reconcile(env)
    record = store.get_governed_run(run_id)
    assert record is not None and record.execution_status == st.RUN_ABANDONED


def test_an_operator_pause_is_not_clobbered_by_a_run(env: dict[str, Any]) -> None:
    """A human's own pause reason must outrank the compatibility mirror."""
    store = env["store"]
    registry = env["registry"]
    run_id = _seed_run(env)
    registry.set_issue_flags(
        "issue-1", paused=True, pause_reason="operator: investigating flaky test"
    )

    store.set_run_status(run_id=run_id, status=st.RUN_RUNNING)
    store.set_run_status(run_id=run_id, status=st.RUN_WAITING_APPROVAL)
    assert (
        registry.get_issue_flags("issue-1").pause_reason
        == "operator: investigating flaky test"
    )

    # Terminalizing the run must not clear a pause it never set.
    store.set_run_status(run_id=run_id, status=st.RUN_ABANDONED)
    flags = registry.get_issue_flags("issue-1")
    assert flags is not None and flags.paused is True
    assert flags.pause_reason == "operator: investigating flaky test"


def test_a_fence_blocks_dispatch_eligibility(env: dict[str, Any]) -> None:
    """The fence is only useful if the scheduler actually consults it."""
    store = env["store"]
    run_id = _seed_run(env)
    store.set_run_status(run_id=run_id, status=st.RUN_RUNNING)
    store.set_run_status(run_id=run_id, status=st.RUN_WAITING_APPROVAL)

    orchestrator = env["orchestrator"]
    assert orchestrator._governed_fence_reason("issue-1") == st.RUN_WAITING_APPROVAL
    assert orchestrator._governed_fence_reason("issue-other") is None

    store.set_run_status(run_id=run_id, status=st.RUN_ABANDONED)
    assert orchestrator._governed_fence_reason("issue-1") is None


def test_one_issue_can_hold_only_one_nonterminal_run(env: dict[str, Any]) -> None:
    from symphony.errors import RunFenced

    store = env["store"]
    _seed_run(env)
    issue = _issue()
    second_run = env["registry"].acquire_run(
        issue,
        workspace_path=env["workspace"],
        attempt=1,
        attempt_kind="retry",
        agent_kind="codex",
    )
    # The lease layer already refuses a second active run for this issue.
    if second_run is not None:  # pragma: no cover - defensive
        with pytest.raises(RunFenced):
            store.begin_governed_run(
                run_id=second_run,
                issue_id=issue.id,
                workflow_name="demo",
                workflow_version=1,
                workflow_hash="hash-1",
                ticket_snapshot={},
            )
    assert len(store.list_fences()) == 1
