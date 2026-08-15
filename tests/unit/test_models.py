from symphony.mcp.models import (
    schedule_items,
    to_project,
    to_request_status,
    to_run_info,
    to_task_summary,
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
