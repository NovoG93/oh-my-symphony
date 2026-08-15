from symphony.mcp.models import (
    schedule_items,
    to_artifact,
    to_board,
    to_project,
    to_request_status,
    to_request_summary,
    to_run_info,
    to_task_summary,
    to_workflow,
)


def test_to_request_status_progress():
    issue = {"identifier": "TASK-42", "title": "Add /health", "state": "In Progress"}
    schedule = {
        "nodes": [
            {
                "identifier": "TASK-42",
                "state": "In Progress",
                "decision": {"status": "running"},
            }
        ]
    }
    rs = to_request_status("TASK-42", issue, schedule)
    assert rs.request_id == "TASK-42"
    assert rs.status == "In Progress"
    assert rs.title == "Add /health"
    assert rs.progress == {"completed": 0, "running": 1, "blocked": 0, "failed": 0, "total": 1}
    assert rs.tasks[0].id == "TASK-42"
    assert rs.tasks[0].stage == "In Progress"
    assert rs.tasks[0].status == "running"


def test_progress_classifier():
    issue = {"identifier": "R1", "state": "running"}
    schedule = {
        "nodes": [
            {"identifier": "a", "state": "Todo", "decision": {"status": "successful"}},
            {"identifier": "b", "state": "In Progress", "decision": {"status": "running"}},
            {"identifier": "c", "state": "Verify", "decision": {"status": "needs_action"}},
            {"identifier": "d", "state": "Verify", "decision": {"status": "retrying"}},
        ]
    }
    rs = to_request_status("R1", issue, schedule)
    assert rs.progress == {"completed": 1, "running": 1, "blocked": 1, "failed": 1, "total": 4}


def test_to_request_status_no_schedule():
    rs = to_request_status("TASK-1", {"identifier": "TASK-1", "state": "todo"})
    assert rs.progress["total"] == 0
    assert rs.tasks == []


def test_schedule_items_variants():
    assert schedule_items(None) == []
    assert schedule_items({"nodes": [{"identifier": "a"}]}) == [{"identifier": "a"}]
    assert schedule_items({"nonsense": 1}) == []


def test_to_project_and_run_info():
    p = to_project({"id": "oh-my-symphony", "name": "OMS", "port": 9999})
    assert p.id == "oh-my-symphony"
    assert p.port == 9999

    r = to_run_info(
        {
            "run": {
                "run_id": "r1",
                "issue_id": "TASK-2",
                "agent_kind": "claude",
                "state": "Done",
                "completed_at": "2026-01-01T00:00:00Z",
                "failure_message": None,
            }
        }
    )
    assert r.run_id == "r1"
    assert r.task_id == "TASK-2"
    assert r.agent == "claude"
    assert r.status == "Done"
    assert r.finished_at == "2026-01-01T00:00:00Z"
    assert r.error is None


def test_task_summary_prefers_identifier_and_decision_status():
    t = to_task_summary({"identifier": "X", "state": "Build", "decision": {"status": "successful"}})
    assert t.id == "X"
    assert t.stage == "Build"
    assert t.status == "successful"


def test_to_run_info_prefers_state_and_maps_title():
    r = to_run_info(
        {
            "run_id": "r1",
            "issue_id": "TASK-2",
            "agent_kind": "claude",
            "state": "Done",
            "status": "normal",
            "title": "Fix /health",
        }
    )
    assert r.status == "Done"  # board state wins over the registry lifecycle enum
    assert r.title == "Fix /health"


def test_to_request_summary():
    r = to_request_summary(
        {"kind": "request", "id": "REQ-1", "node_count": 3, "counts": {"running": 1}}
    )
    assert r.kind == "request"
    assert r.id == "REQ-1"
    assert r.node_count == 3
    assert r.counts == {"running": 1}


def test_to_board():
    b = to_board(
        {
            "board": {"name": "oms", "tracker_kind": "file", "default_agent_kind": "agy"},
            "columns": [{"name": "Todo", "terminal": False}, {"name": "Done", "terminal": True}],
            "issues": [{"identifier": "TASK-1", "title": "t", "state": "Todo", "priority": 2}],
            "live": {"TASK-2": {"status": "running"}},
        }
    )
    assert b.name == "oms"
    assert b.default_agent_kind == "agy"
    assert len(b.columns) == 2
    assert b.columns[1].terminal is True
    assert b.issues[0].identifier == "TASK-1"
    assert b.issues[0].priority == 2
    assert b.live == {"TASK-2": {"status": "running"}}


def test_to_artifact():
    a = to_artifact(
        {"name": "qa.md", "title": "QA report", "content_type": "text/plain", "byte_size": 42}
    )
    assert a.name == "qa.md"
    assert a.title == "QA report"
    assert a.byte_size == 42


def test_to_workflow():
    w = to_workflow(
        {
            "workflow_path": "/x/WORKFLOW.md",
            "agent": {"kind": "agy"},
            "agent_kinds": ["agy", "claude", "codex"],
            "columns": [{"name": "Todo", "terminal": False}],
        }
    )
    assert w.workflow_path == "/x/WORKFLOW.md"
    assert w.default_agent_kind == "agy"
    assert w.agent_kinds == ["agy", "claude", "codex"]
    assert len(w.columns) == 1
