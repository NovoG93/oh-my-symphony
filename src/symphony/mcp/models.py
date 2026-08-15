"""Normalized domain models + normalization helpers.

These shield Hermes from raw Symphony API shapes. Normalization is defensive
(``.get``-based) and targets the fork's actual payload shapes:
``GET /api/v1/requests/ticket/{id}/schedule`` → ``nodes[]`` with ``decision.status``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Project:
    id: str
    name: str = ""
    repo: str | None = None
    port: int | None = None


@dataclass(frozen=True)
class TaskSummary:
    id: str
    stage: str = ""
    status: str = ""
    agent: str | None = None


@dataclass(frozen=True)
class RequestStatus:
    request_id: str
    status: str
    title: str = ""
    progress: dict[str, int] = field(
        default_factory=lambda: {
            "completed": 0,
            "running": 0,
            "blocked": 0,
            "failed": 0,
            "total": 0,
        }
    )
    tasks: list[TaskSummary] = field(default_factory=list)


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    task_id: str | None = None
    agent: str | None = None
    status: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None


def to_project(data: dict) -> Project:
    return Project(
        id=str(data.get("id") or data.get("name") or ""),
        name=str(data.get("name") or data.get("id") or ""),
        repo=data.get("repo"),
        port=data.get("port"),
    )


def schedule_items(schedule: dict | None) -> list[dict]:
    """Extract the DAG nodes from a request-schedule payload."""
    if not schedule:
        return []
    items = schedule.get("nodes") or schedule.get("tasks") or schedule.get("stages")
    if isinstance(items, dict):
        items = list(items.values())
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict)]


def to_task_summary(data: dict) -> TaskSummary:
    raw_decision = data.get("decision")
    decision: dict = raw_decision if isinstance(raw_decision, dict) else {}
    return TaskSummary(
        id=str(data.get("identifier") or data.get("id") or data.get("task_id") or ""),
        stage=str(data.get("state") or data.get("stage") or data.get("name") or ""),
        status=str(
            decision.get("status") or data.get("status") or data.get("state") or ""
        ),
        agent=data.get("agent") or data.get("agent_kind") or data.get("last_agent_kind"),
    )


_STATUS_TERMINAL = (
    "completed",
    "done",
    "success",
    "successful",
    "passed",
    "merged",
    "closed",
    "documented",
)
_STATUS_RUNNING = ("running", "in_progress", "in-progress", "active", "started")
_STATUS_BLOCKED = (
    "blocked",
    "paused",
    "waiting",
    "on_hold",
    "on-hold",
    "needs_action",
    "needs-action",
    "ready",
)
_STATUS_FAILED = ("failed", "error", "cancelled", "canceled", "skipped", "retrying")


def _progress(tasks: list[TaskSummary]) -> dict[str, int]:
    completed = running = blocked = failed = 0
    for t in tasks:
        s = (t.status or "").lower()
        if s in _STATUS_TERMINAL:
            completed += 1
        elif s in _STATUS_RUNNING:
            running += 1
        elif s in _STATUS_BLOCKED:
            blocked += 1
        elif s in _STATUS_FAILED:
            failed += 1
    return {
        "completed": completed,
        "running": running,
        "blocked": blocked,
        "failed": failed,
        "total": len(tasks),
    }


def to_request_status(
    identifier: str, issue: dict, schedule: dict | None = None
) -> RequestStatus:
    tasks = [to_task_summary(it) for it in schedule_items(schedule)]
    status = issue.get("state") or issue.get("status") or "unknown"
    return RequestStatus(
        request_id=str(issue.get("identifier") or identifier),
        status=str(status),
        title=str(issue.get("title") or ""),
        progress=_progress(tasks),
        tasks=tasks,
    )


def to_run_info(data: dict) -> RunInfo:
    # GET /api/v1/runs/{id} nests the record under "run".
    raw_run = data.get("run")
    run: dict = raw_run if isinstance(raw_run, dict) else data
    return RunInfo(
        run_id=str(run.get("run_id") or run.get("id") or ""),
        task_id=run.get("issue_id") or run.get("identifier") or run.get("task_id"),
        agent=run.get("agent_kind") or run.get("agent"),
        status=run.get("state") or run.get("status"),
        started_at=run.get("started_at"),
        finished_at=run.get("completed_at")
        or run.get("finished_at")
        or run.get("ended_at"),
        error=run.get("failure_message") or run.get("error") or run.get("error_message"),
    )
