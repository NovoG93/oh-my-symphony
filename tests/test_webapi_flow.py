"""REST contract for the governed workflow surface (PRD §16).

Drives `build_app` against a real temp board whose WORKFLOW.md turns the
workflow engine on, with the run/node/approval/artifact rows seeded
directly through `RunRegistry(...).governed`. No executor runs here: the
point is the HTTP contract — status codes above all — not the DAG.

The status codes are the reason this file exists. `symphony.webapi._wrap`
collapses every `SymphonyError` to 400, which would make "unknown run"
and "someone already decided this gate" indistinguishable from "you sent
a bad field". The governed handlers use their own mapping instead, and
these tests pin it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from symphony.errors import SymphonyError
from symphony.flow import statuses as st
from symphony.flow.artifacts import ArtifactStore
from symphony.flow.loader import WorkflowLoader
from symphony.issue import Issue
from symphony.orchestrator import Orchestrator
from symphony.orchestrator.run_registry import RunRegistry, registry_path_for_workflow
from symphony.server import build_app
from symphony.workflow import WorkflowState


WORKFLOW_TEXT = """---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, In Progress, Human Review, Done]
  terminal_states: [Done, Blocked]

agent:
  kind: codex
  max_concurrent_agents: 1

workflow_engine:
  enabled: true
  directory: ./.symphony/workflows
  artifact_directory: ./.symphony/artifacts
  default: demo
---

You are working on {{ issue.identifier }}.
"""

TICKET = """---
id: iss-1
identifier: SEED-1
title: seeded ticket
state: Todo
priority: 2
created_at: '2026-07-01T00:00:00Z'
updated_at: '2026-07-01T00:00:00Z'
---

Seed body.
"""

DEMO_YAML = """
version: 1
name: demo
description: plan then gate
nodes:
  - id: plan
    type: agent
    workspace_access: read
    prompt: "Plan ${ticket.identifier}"
  - id: gate
    type: approval
    depends_on: [plan]
    title: Approve the plan
    evidence: [plan]
"""

# `depends_on` names a node that does not exist -> compiler diagnostics.
BROKEN_YAML = """
version: 1
name: broken
description: depends on a ghost
nodes:
  - id: only
    type: shell
    workspace_access: write
    run: "true"
    depends_on: [ghost]
"""


@dataclass
class _Seed:
    run_id: str
    approval_id: str
    artifact_id: str
    artifact_body: str
    first_seq: int


@pytest.fixture()
def board_dir(tmp_path: Path) -> Path:
    (tmp_path / "WORKFLOW.md").write_text(WORKFLOW_TEXT, encoding="utf-8")
    kanban = tmp_path / "kanban"
    kanban.mkdir()
    (kanban / "SEED-1.md").write_text(TICKET, encoding="utf-8")
    flows = tmp_path / ".symphony" / "workflows"
    flows.mkdir(parents=True)
    (flows / "demo.yaml").write_text(DEMO_YAML, encoding="utf-8")
    (flows / "broken.yaml").write_text(BROKEN_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def seed(board_dir: Path) -> _Seed:
    """One governed run parked on a pending gate, with one artifact."""
    loader = WorkflowLoader(
        board_dir / ".symphony" / "workflows", workflow_dir=board_dir
    )
    compiled = loader.load("demo")

    registry = RunRegistry(registry_path_for_workflow(board_dir / "WORKFLOW.md"))
    try:
        issue = Issue(
            id="iss-1",
            identifier="SEED-1",
            title="seeded ticket",
            description="Seed body.",
            priority=2,
            state="Todo",
        )
        run_id = registry.acquire_run(
            issue,
            workspace_path=board_dir,
            attempt=1,
            attempt_kind="initial",
            agent_kind="codex",
        )
        assert run_id is not None
        governed = registry.governed
        governed.put_workflow_snapshot(
            workflow_hash=compiled.workflow_hash,
            workflow_name=compiled.name,
            schema_version=1,
            normalized_json=compiled.normalized_json,
            source_path=str(compiled.definition.source_path),
        )
        governed.begin_governed_run(
            run_id=run_id,
            issue_id=issue.id,
            workflow_name=compiled.name,
            workflow_version=compiled.version,
            workflow_hash=compiled.workflow_hash,
            ticket_snapshot=issue.to_template_dict(),
        )
        governed.set_run_status(run_id=run_id, status=st.RUN_RUNNING)
        node = governed.start_node_attempt(
            run_id=run_id,
            node_id="plan",
            node_type=st.NODE_TYPE_AGENT,
            backend_kind="codex",
            workspace_access=st.ACCESS_READ,
        )
        body = "plan output for SEED-1\n"
        stored = ArtifactStore(board_dir / ".symphony" / "artifacts").write_text(
            run_id=run_id, node_id="plan", filename="output.txt", content=body
        )
        artifact = governed.record_artifact(
            run_id=run_id,
            node_id="plan",
            artifact_type="agent_output",
            scope=st.SCOPE_RUNTIME,
            relative_path=stored.relative_path,
            media_type=stored.media_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
        )
        approval = governed.create_approval(
            run_id=run_id,
            node_id="gate",
            node_attempt=node.attempt,
            title="Approve the plan",
            instructions="Read the plan artifact.",
        )
        governed.set_run_status(run_id=run_id, status=st.RUN_WAITING_APPROVAL)
        first_seq = governed.events_after(run_id, after_seq=0, limit=1)[0].seq
    finally:
        registry.close()

    return _Seed(
        run_id=run_id,
        approval_id=approval.approval_id,
        artifact_id=artifact.artifact_id,
        artifact_body=body,
        first_seq=first_seq,
    )


async def _start_client(board_dir: Path) -> TestClient:
    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    cli = TestClient(TestServer(build_app(Orchestrator(state))))
    await cli.start_server()
    return cli


@pytest_asyncio.fixture()
async def client(board_dir: Path) -> AsyncIterator[TestClient]:
    cli = await _start_client(board_dir)
    try:
        yield cli
    finally:
        await cli.close()


# ---------------------------------------------------------------------------
# workflow definitions
# ---------------------------------------------------------------------------


async def test_workflows_list_is_not_shadowed_by_identifier_catch_all(
    client: TestClient,
) -> None:
    # `server.build_app` registers GET /api/v1/{identifier} after the web
    # routes. If the governed routes were registered anywhere else, this
    # would come back as issue_not_found.
    resp = await client.get("/api/v1/workflows")
    assert resp.status == 200
    payload = await resp.json()
    assert "error" not in payload
    assert payload["enabled"] is True
    assert payload["default"] == "demo"
    by_name = {entry["name"]: entry for entry in payload["workflows"]}
    assert by_name["demo"]["valid"] is True
    assert by_name["demo"]["node_count"] == 2
    assert by_name["broken"]["valid"] is False
    assert "ghost" in by_name["broken"]["error"]


async def test_workflows_readable_while_the_engine_is_still_disabled(
    board_dir: Path,
) -> None:
    # Validating the YAML is how an operator decides whether to switch the
    # engine on, so listing must not require it to be on already.
    (board_dir / "WORKFLOW.md").write_text(
        WORKFLOW_TEXT.replace("enabled: true", "enabled: false"), encoding="utf-8"
    )
    cli = await _start_client(board_dir)
    try:
        payload = await (await cli.get("/api/v1/workflows")).json()
        assert payload["enabled"] is False
        assert {entry["name"] for entry in payload["workflows"]} == {"demo", "broken"}
        validated = await (
            await cli.post("/api/v1/workflows/validate", json={"content": DEMO_YAML})
        ).json()
        assert validated["valid"] is True
    finally:
        await cli.close()


async def test_workflow_detail_returns_compiled_definition(
    client: TestClient,
) -> None:
    resp = await client.get("/api/v1/workflows/demo")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["name"] == "demo"
    assert [node["id"] for node in payload["nodes"]] == ["plan", "gate"]
    assert payload["layers"] == [["plan"], ["gate"]]


async def test_workflow_detail_unknown_name_returns_404(client: TestClient) -> None:
    resp = await client.get("/api/v1/workflows/nope")
    assert resp.status == 404
    assert (await resp.json())["error"]["code"] == "workflow_not_found"


async def test_workflow_detail_invalid_file_returns_400(client: TestClient) -> None:
    resp = await client.get("/api/v1/workflows/broken")
    assert resp.status == 400
    assert (await resp.json())["error"]["code"] == "workflow_invalid"


async def test_validate_accepts_valid_content(client: TestClient) -> None:
    resp = await client.post(
        "/api/v1/workflows/validate", json={"content": DEMO_YAML, "name": "demo"}
    )
    assert resp.status == 200
    payload = await resp.json()
    assert payload["valid"] is True
    assert payload["diagnostics"] == []
    assert payload["workflow"]["name"] == "demo"


async def test_validate_returns_200_with_diagnostics_when_invalid(
    client: TestClient,
) -> None:
    resp = await client.post("/api/v1/workflows/validate", json={"content": BROKEN_YAML})
    assert resp.status == 200
    payload = await resp.json()
    assert payload["valid"] is False
    assert payload["diagnostics"]
    assert any("ghost" in d["message"] for d in payload["diagnostics"])


async def test_validate_rejects_non_string_content(client: TestClient) -> None:
    resp = await client.post("/api/v1/workflows/validate", json={"content": 17})
    assert resp.status == 400
    assert (await resp.json())["error"]["code"] == "invalid_content"


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------


async def test_run_detail_returns_nodes_approvals_and_artifacts(
    client: TestClient, seed: _Seed
) -> None:
    resp = await client.get(f"/api/v1/runs/{seed.run_id}")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["run_id"] == seed.run_id
    assert payload["identifier"] == "SEED-1"
    assert payload["execution_status"] == st.RUN_WAITING_APPROVAL
    assert payload["workflow_name"] == "demo"
    assert [node["node_id"] for node in payload["nodes"]] == ["plan"]
    assert [a["approval_id"] for a in payload["approvals"]] == [seed.approval_id]
    assert [a["artifact_id"] for a in payload["artifacts"]] == [seed.artifact_id]
    assert payload["progress"]["total"] == 2
    assert "approve" in payload["actions"]


async def test_run_detail_unknown_run_returns_404(client: TestClient) -> None:
    resp = await client.get("/api/v1/runs/does-not-exist")
    assert resp.status == 404
    assert (await resp.json())["error"]["code"] == "run_not_found"


async def test_run_events_are_ordered_and_page_after_seq(
    client: TestClient, seed: _Seed
) -> None:
    first = await (await client.get(f"/api/v1/runs/{seed.run_id}/events")).json()
    seqs = [event["seq"] for event in first["events"]]
    assert seqs == sorted(seqs)
    assert seqs[0] == seed.first_seq
    assert first["next_after_seq"] == seqs[-1]
    assert {"run_created", "approval_requested"} <= {e["type"] for e in first["events"]}

    resp = await client.get(
        f"/api/v1/runs/{seed.run_id}/events", params={"after_seq": str(seqs[0])}
    )
    assert resp.status == 200
    tail = await resp.json()
    assert [e["seq"] for e in tail["events"]] == seqs[1:]

    limited = await (
        await client.get(
            f"/api/v1/runs/{seed.run_id}/events", params={"limit": "1"}
        )
    ).json()
    assert limited["count"] == 1


async def test_run_events_unknown_run_returns_404(client: TestClient) -> None:
    resp = await client.get("/api/v1/runs/does-not-exist/events")
    assert resp.status == 404
    assert (await resp.json())["error"]["code"] == "run_not_found"


# ---------------------------------------------------------------------------
# artifacts
# ---------------------------------------------------------------------------


async def test_run_artifacts_returns_metadata_without_bytes(
    client: TestClient, seed: _Seed
) -> None:
    resp = await client.get(f"/api/v1/runs/{seed.run_id}/artifacts")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["count"] == 1
    record = payload["artifacts"][0]
    assert record["artifact_id"] == seed.artifact_id
    assert record["node_id"] == "plan"
    assert record["size_bytes"] == len(seed.artifact_body)
    assert "content" not in record


async def test_artifact_download_streams_the_stored_bytes(
    client: TestClient, seed: _Seed
) -> None:
    resp = await client.get(f"/api/v1/artifacts/{seed.artifact_id}")
    assert resp.status == 200
    assert await resp.text() == seed.artifact_body
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Content-Disposition"].startswith("attachment")


async def test_artifact_download_unknown_id_returns_404(
    client: TestClient, seed: _Seed
) -> None:
    del seed
    resp = await client.get("/api/v1/artifacts/deadbeef")
    assert resp.status == 404
    assert (await resp.json())["error"]["code"] == "artifact_not_found"


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------


async def test_approvals_list_filters_by_status_and_carries_the_ticket(
    client: TestClient, seed: _Seed
) -> None:
    resp = await client.get("/api/v1/approvals", params={"status": "pending"})
    assert resp.status == 200
    payload = await resp.json()
    assert payload["count"] == 1
    item = payload["approvals"][0]
    assert item["approval_id"] == seed.approval_id
    assert item["status"] == st.APPROVAL_PENDING
    assert item["version"] == 1
    assert item["identifier"] == "SEED-1"
    assert item["workflow_name"] == "demo"


async def test_resolve_returns_the_updated_record_and_stamps_web_source(
    client: TestClient, seed: _Seed
) -> None:
    resp = await client.post(
        f"/api/v1/approvals/{seed.approval_id}/resolve",
        json={
            "decision": "approved",
            "expected_version": 1,
            "actor": "operator",
            "comment": "looks right",
        },
    )
    assert resp.status == 200
    payload = await resp.json()
    assert payload["approval_id"] == seed.approval_id
    assert payload["status"] == st.APPROVAL_APPROVED
    assert payload["decision"] == "approved"
    assert payload["version"] == 2
    assert payload["actor"] == "operator"
    assert payload["source"] == "web"
    assert payload["resolved_at"] is not None

    listed = await (
        await client.get("/api/v1/approvals", params={"status": "pending"})
    ).json()
    assert listed["count"] == 0


async def test_resolve_with_stale_expected_version_returns_409(
    client: TestClient, seed: _Seed
) -> None:
    resp = await client.post(
        f"/api/v1/approvals/{seed.approval_id}/resolve",
        json={"decision": "approved", "expected_version": 99},
    )
    assert resp.status == 409
    assert (await resp.json())["error"]["code"] == "approval_version_conflict"


async def test_resolve_with_conflicting_second_decision_returns_409(
    client: TestClient, seed: _Seed
) -> None:
    first = await client.post(
        f"/api/v1/approvals/{seed.approval_id}/resolve", json={"decision": "approved"}
    )
    assert first.status == 200
    resp = await client.post(
        f"/api/v1/approvals/{seed.approval_id}/resolve", json={"decision": "rejected"}
    )
    assert resp.status == 409
    assert (await resp.json())["error"]["code"] == "approval_already_resolved"


async def test_resolve_unknown_approval_returns_404(client: TestClient) -> None:
    resp = await client.post(
        "/api/v1/approvals/deadbeef/resolve", json={"decision": "approved"}
    )
    assert resp.status == 404
    assert (await resp.json())["error"]["code"] == "approval_not_found"


@pytest.mark.parametrize("body", [{"decision": "yes"}, {"approved": True}, {}])
async def test_resolve_refuses_anything_but_the_two_exact_decisions(
    client: TestClient, seed: _Seed, body: dict[str, object]
) -> None:
    resp = await client.post(
        f"/api/v1/approvals/{seed.approval_id}/resolve", json=body
    )
    assert resp.status == 400
    assert (await resp.json())["error"]["code"] == "invalid_decision"
    # And the gate is untouched.
    listed = await (
        await client.get("/api/v1/approvals", params={"status": "pending"})
    ).json()
    assert listed["count"] == 1


# ---------------------------------------------------------------------------
# run lifecycle mutations
# ---------------------------------------------------------------------------


async def test_abandon_returns_the_updated_run_detail(
    client: TestClient, seed: _Seed
) -> None:
    resp = await client.post(
        f"/api/v1/runs/{seed.run_id}/abandon", json={"reason": "superseded"}
    )
    assert resp.status == 200
    payload = await resp.json()
    assert payload["run_id"] == seed.run_id
    assert payload["execution_status"] == st.RUN_ABANDONED
    assert payload["terminal_reason"] == "superseded"
    assert payload["actions"] == []


async def test_cancel_returns_the_updated_run_detail(
    client: TestClient, seed: _Seed
) -> None:
    resp = await client.post(f"/api/v1/runs/{seed.run_id}/cancel")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["execution_status"] == st.RUN_CANCELLED


async def test_second_terminal_transition_returns_409(
    client: TestClient, seed: _Seed
) -> None:
    assert (await client.post(f"/api/v1/runs/{seed.run_id}/cancel")).status == 200
    resp = await client.post(f"/api/v1/runs/{seed.run_id}/abandon")
    assert resp.status == 409
    assert (await resp.json())["error"]["code"] == "illegal_run_transition"


async def test_abandon_unknown_run_returns_404(client: TestClient) -> None:
    resp = await client.post("/api/v1/runs/does-not-exist/abandon")
    assert resp.status == 404
    assert (await resp.json())["error"]["code"] == "run_not_found"


async def test_resume_unknown_run_returns_404(client: TestClient) -> None:
    resp = await client.post("/api/v1/runs/does-not-exist/resume")
    assert resp.status == 404
    assert (await resp.json())["error"]["code"] == "run_not_found"


# ---------------------------------------------------------------------------
# registry availability
# ---------------------------------------------------------------------------


async def test_unopenable_registry_is_a_503_not_a_500(
    client: TestClient, seed: _Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: Orchestrator, cfg: object) -> object:
        del self, cfg
        raise SymphonyError("state.db could not be opened")

    monkeypatch.setattr(Orchestrator, "governed_store", boom)
    for path in (
        f"/api/v1/runs/{seed.run_id}",
        f"/api/v1/runs/{seed.run_id}/events",
        f"/api/v1/runs/{seed.run_id}/artifacts",
        f"/api/v1/artifacts/{seed.artifact_id}",
        "/api/v1/approvals",
    ):
        resp = await client.get(path)
        assert resp.status == 503, path
        assert (await resp.json())["error"]["code"] == "registry_unavailable", path
