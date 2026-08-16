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
    title: str | None = None


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
    # GET /api/v1/runs/{id} nests the record under "run"; GET /api/v1/runs
    # returns flat records. Prefer `state` (the ticket's board state) as the
    # human-meaningful status; the raw `status` field is the run registry's
    # internal lifecycle enum (active/normal/orphaned/...), less useful here.
    # Failure is surfaced separately via `error` (failure_message).
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
        title=run.get("title"),
    )


@dataclass(frozen=True)
class RequestSummary:
    kind: str = ""
    id: str = ""
    node_count: int = 0
    counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class BoardColumn:
    name: str = ""
    terminal: bool = False
    description: str = ""


@dataclass(frozen=True)
class BoardIssue:
    identifier: str = ""
    title: str = ""
    state: str = ""
    priority: int | None = None


@dataclass(frozen=True)
class BoardSummary:
    name: str = ""
    tracker_kind: str = ""
    read_only: bool = False
    default_agent_kind: str = ""
    agent_kinds: list[str] = field(default_factory=list)
    columns: list[BoardColumn] = field(default_factory=list)
    issues: list[BoardIssue] = field(default_factory=list)
    live: dict[str, dict] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactInfo:
    name: str = ""
    title: str = ""
    summary: str = ""
    content_type: str = ""
    byte_size: int = 0
    collected_at: str | None = None
    run_id: str | None = None
    turn: int | None = None
    inline: bool = False


@dataclass(frozen=True)
class WorkflowInfo:
    workflow_path: str = ""
    default_agent_kind: str = ""
    agent_kinds: list[str] = field(default_factory=list)
    columns: list[BoardColumn] = field(default_factory=list)
    agent: dict = field(default_factory=dict)
    continuous_improvement: dict = field(default_factory=dict)
    preview: dict = field(default_factory=dict)
    polling_interval_ms: int = 0


def to_request_summary(data: dict) -> RequestSummary:
    return RequestSummary(
        kind=str(data.get("kind") or ""),
        id=str(data.get("id") or ""),
        node_count=int(data.get("node_count") or 0),
        counts=dict(data.get("counts") or {}),
    )


def to_board_column(data: dict) -> BoardColumn:
    return BoardColumn(
        name=str(data.get("name") or ""),
        terminal=bool(data.get("terminal")),
        description=str(data.get("description") or ""),
    )


def to_board_issue(data: dict) -> BoardIssue:
    return BoardIssue(
        identifier=str(data.get("identifier") or data.get("id") or ""),
        title=str(data.get("title") or ""),
        state=str(data.get("state") or ""),
        priority=data.get("priority") if isinstance(data.get("priority"), int) else None,
    )


def to_board(data: dict) -> BoardSummary:
    board = data.get("board") or {}
    board = board if isinstance(board, dict) else {}
    live = data.get("live") or {}
    live = live if isinstance(live, dict) else {}
    return BoardSummary(
        name=str(board.get("name") or ""),
        tracker_kind=str(board.get("tracker_kind") or ""),
        read_only=bool(board.get("read_only")),
        default_agent_kind=str(board.get("default_agent_kind") or ""),
        agent_kinds=list(board.get("agent_kinds") or []),
        columns=[to_board_column(c) for c in (data.get("columns") or []) if isinstance(c, dict)],
        issues=[to_board_issue(i) for i in (data.get("issues") or []) if isinstance(i, dict)],
        live={str(k): dict(v) for k, v in live.items() if isinstance(v, dict)},
    )


def to_artifact(data: dict) -> ArtifactInfo:
    return ArtifactInfo(
        name=str(data.get("name") or ""),
        title=str(data.get("title") or ""),
        summary=str(data.get("summary") or ""),
        content_type=str(data.get("content_type") or ""),
        byte_size=int(data.get("byte_size") or 0),
        collected_at=data.get("collected_at"),
        run_id=data.get("run_id"),
        turn=data.get("turn"),
        inline=bool(data.get("inline")),
    )


def to_workflow(data: dict) -> WorkflowInfo:
    agent = data.get("agent") or {}
    agent = agent if isinstance(agent, dict) else {}
    return WorkflowInfo(
        workflow_path=str(data.get("workflow_path") or ""),
        default_agent_kind=str(agent.get("kind") or ""),
        agent_kinds=list(data.get("agent_kinds") or []),
        columns=[to_board_column(c) for c in (data.get("columns") or []) if isinstance(c, dict)],
        agent=dict(agent),
        continuous_improvement=dict(data.get("continuous_improvement") or {}),
        preview=dict(data.get("preview") or {}),
        polling_interval_ms=int(data.get("polling_interval_ms") or 0),
    )
