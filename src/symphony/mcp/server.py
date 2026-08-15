"""symphony-mcp: Streamable HTTP MCP gateway exposing oh-my-symphony to Hermes.

A separate, stateless process that talks to the running oh-my-symphony
orchestrator over its local REST API (default http://127.0.0.1:9999) and exposes
a small, semantic, authenticated MCP tool surface at ``/mcp``.

v2 tool surface (17 tools): list_projects, create_request, get_request, get_task,
get_run, plus list_requests, get_board, list_runs, get_run_diagnostic,
list_artifacts, get_artifact, get_workflow, get_stats (read) and
cancel_request, update_request, recover_blocked, skip_document (control,
gated behind SYMPHONY_MCP_ALLOW_CONTROL).
"""

from __future__ import annotations

import base64
import dataclasses
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from . import audit as audit_mod
from .auth import BearerAuthMiddleware
from .client import SymphonyClient
from .config import Settings, load
from .errors import UpstreamError, ValidationError
from .idempotency import IdempotencyStore
from .models import (
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
from .policy import Policy

_PRIORITY_MAP = {"low": 1, "normal": 2, "high": 3, "critical": 4}
_WORKFLOWS = ("default", "simple", "deep")


def _compose_description(
    description: str | None, acceptance_criteria: list[str] | None
) -> str | None:
    parts: list[str] = []
    if description:
        parts.append(description)
    if acceptance_criteria:
        parts.append(
            "Acceptance criteria:\n" + "\n".join(f"- {c}" for c in acceptance_criteria)
        )
    return "\n\n".join(parts) if parts else None


async def _request_status(client: SymphonyClient, identifier: str):
    issue = await client.get_issue(identifier)
    schedule = await client.get_request_schedule(identifier)
    return to_request_status(identifier, issue, schedule)


def build_mcp(settings: Settings) -> FastMCP:
    # We bind 0.0.0.0 (LAN-reachable for Hermes), so FastMCP's loopback
    # auto-enable of DNS-rebinding protection must be turned off explicitly.
    # The bearer token is the real authentication here; re-enable DNS-rebinding
    # protection with an explicit allowed_hosts list if ever exposed beyond LAN.
    mcp = FastMCP(
        "symphony-mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    client = SymphonyClient(settings.symphony_base_url, settings.timeout_seconds)
    policy = Policy(settings.allowed_projects, allow_control=settings.allow_control)
    idem = IdempotencyStore(settings.idempotency_db)

    @mcp.tool()
    async def symphony_list_projects() -> list[dict[str, Any]]:
        """List projects known to oh-my-symphony."""
        rows = await client.list_projects()
        return [dataclasses.asdict(to_project(p)) for p in rows]

    @mcp.tool()
    async def symphony_create_request(
        project: str,
        objective: str,
        description: str | None = None,
        acceptance_criteria: list[str] | None = None,
        priority: str = "normal",
        workflow: str = "default",
        client_request_id: str | None = None,
    ) -> dict[str, Any]:
        """Delegate new work to oh-my-symphony and return the created request's status.

        Hermes expresses intent; oh-my-symphony decides how tasks are scheduled and
        which agent runs each stage.
        """
        policy.assert_project_allowed(project)
        if priority not in _PRIORITY_MAP:
            raise ValidationError(f"priority must be one of {sorted(_PRIORITY_MAP)}")
        if workflow not in _WORKFLOWS:
            raise ValidationError(f"workflow must be one of {_WORKFLOWS}")

        if client_request_id:
            existing = idem.get(client_request_id)
            if existing:
                return dataclasses.asdict(await _request_status(client, existing))

        created = await client.create_issue(
            title=objective,
            description=_compose_description(description, acceptance_criteria),
            priority=_PRIORITY_MAP[priority],
        )
        identifier = created.get("identifier") if isinstance(created, dict) else None
        if not identifier:
            raise UpstreamError("symphony did not return an identifier for the new request")

        if client_request_id:
            idem.put(client_request_id, str(identifier))

        audit_mod.audit(
            settings.audit_log,
            {
                "client": "hermes",
                "tool": "symphony_create_request",
                "project": project,
                "request_id": identifier,
                "client_request_id": client_request_id,
                "result": "success",
            },
        )
        return dataclasses.asdict(await _request_status(client, str(identifier)))

    @mcp.tool()
    async def symphony_get_request(request_id: str) -> dict[str, Any]:
        """Return the aggregated status of a request (progress + per-task summary)."""
        return dataclasses.asdict(await _request_status(client, request_id))

    @mcp.tool()
    async def symphony_get_task(task_id: str) -> dict[str, Any]:
        """Return a single task/stage's status (task_id is a ticket identifier)."""
        schedule = await client.get_request_schedule(task_id)
        items = schedule_items(schedule)
        for item in items:
            summary = to_task_summary(item)
            if task_id == summary.id:
                return dataclasses.asdict(summary)
        if len(items) == 1:
            return dataclasses.asdict(to_task_summary(items[0]))
        raise UpstreamError(f"task {task_id!r} not found")

    @mcp.tool()
    async def symphony_get_run(run_id: str) -> dict[str, Any]:
        """Return a single agent run record."""
        data = await client.get_run(run_id)
        return dataclasses.asdict(to_run_info(data))

    @mcp.tool()
    async def symphony_list_requests() -> dict[str, Any]:
        """List all requests/tickets on the board with per-node state counts."""
        data = await client.list_requests()
        return {
            "available": bool(data.get("available")),
            "reason": data.get("reason"),
            "tracker_kind": data.get("tracker_kind"),
            "stale": bool(data.get("stale")),
            "policy": data.get("policy"),
            "requests": [
                dataclasses.asdict(to_request_summary(r))
                for r in (data.get("requests") or [])
                if isinstance(r, dict)
            ],
        }

    @mcp.tool()
    async def symphony_get_board() -> dict[str, Any]:
        """Return the full kanban board (columns, tickets, and live workers)."""
        return dataclasses.asdict(to_board(await client.get_board()))

    @mcp.tool()
    async def symphony_list_runs(
        issue_id: str | None = None,
        limit: int = 50,
        status: str | None = None,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """List recent agent runs, optionally filtered by ticket, status, or agent."""
        data = await client.list_runs(
            issue_id=issue_id, limit=limit, status=status, agent=agent
        )
        return {
            "count": int(data.get("count") or 0),
            "runs": [
                dataclasses.asdict(to_run_info(r))
                for r in (data.get("runs") or [])
                if isinstance(r, dict)
            ],
            "registry_error": data.get("registry_error"),
        }

    @mcp.tool()
    async def symphony_get_run_diagnostic(run_id: str) -> dict[str, Any]:
        """Return the full diagnostic JSON for a run (detailed trace/failure info)."""
        return await client.get_run_diagnostic(run_id)

    @mcp.tool()
    async def symphony_list_artifacts(request_id: str) -> dict[str, Any]:
        """List the artifacts (evidence/reports/deliverables) collected for a ticket."""
        data = await client.list_artifacts(request_id)
        return {
            "enabled": bool(data.get("enabled")),
            "artifacts": [
                dataclasses.asdict(to_artifact(a))
                for a in (data.get("artifacts") or [])
                if isinstance(a, dict)
            ],
        }

    @mcp.tool()
    async def symphony_get_artifact(request_id: str, name: str) -> dict[str, Any]:
        """Fetch a single artifact's content (text as UTF-8, binary as base64)."""
        data = await client.get_artifact_file(request_id, name)
        raw = data.get("content") or b""
        content_type = str(data.get("content_type") or "")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "identifier": request_id,
                "name": name,
                "content_type": content_type,
                "encoding": "base64",
                "content": base64.b64encode(raw).decode("ascii"),
            }
        return {
            "identifier": request_id,
            "name": name,
            "content_type": content_type,
            "encoding": "utf-8",
            "content": text,
        }

    @mcp.tool()
    async def symphony_get_workflow() -> dict[str, Any]:
        """Return the current workflow configuration (agent routing, stages, CI, preview)."""
        return dataclasses.asdict(to_workflow(await client.get_workflow()))

    @mcp.tool()
    async def symphony_get_stats(days: int = 30) -> dict[str, Any]:
        """Return board throughput/activity stats for the last N days (1-365)."""
        return await client.get_stats(days=days)

    @mcp.tool()
    async def symphony_cancel_request(request_id: str) -> dict[str, Any]:
        """Cancel a request by moving it to the Cancelled state. (control action)"""
        policy.assert_control_allowed()
        result = await client.patch_issue(request_id, fields={"state": "Cancelled"})
        audit_mod.audit(
            settings.audit_log,
            {
                "client": "hermes",
                "tool": "symphony_cancel_request",
                "request_id": request_id,
                "result": "success",
            },
        )
        return result

    @mcp.tool()
    async def symphony_update_request(
        request_id: str,
        title: str | None = None,
        description: str | None = None,
        priority: str | None = None,
        labels: list[str] | None = None,
        state: str | None = None,
        agent_kind: str | None = None,
        skills: list[str] | None = None,
        blocked_by: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update editable fields of an existing ticket. (control action)"""
        policy.assert_control_allowed()
        fields: dict[str, Any] = {}
        if title is not None:
            fields["title"] = title
        if description is not None:
            fields["description"] = description
        if priority is not None:
            if priority not in _PRIORITY_MAP:
                raise ValidationError(f"priority must be one of {sorted(_PRIORITY_MAP)}")
            fields["priority"] = _PRIORITY_MAP[priority]
        if labels is not None:
            fields["labels"] = labels
        if state is not None:
            fields["state"] = state
        if agent_kind is not None:
            fields["agent_kind"] = agent_kind
        if skills is not None:
            fields["skills"] = skills
        if blocked_by is not None:
            fields["blocked_by"] = blocked_by
        if not fields:
            raise ValidationError("no fields provided to update")
        result = await client.patch_issue(request_id, fields=fields)
        audit_mod.audit(
            settings.audit_log,
            {
                "client": "hermes",
                "tool": "symphony_update_request",
                "request_id": request_id,
                "fields": sorted(fields),
                "result": "success",
            },
        )
        return result

    @mcp.tool()
    async def symphony_recover_blocked(
        request_id: str,
        fix_state: str | None = None,
        agent_kind: str | None = None,
    ) -> dict[str, Any]:
        """Recover a blocked/paused ticket by creating a recovery ticket. (control action)"""
        policy.assert_control_allowed()
        result = await client.recover_blocked(
            request_id, fix_state=fix_state, agent_kind=agent_kind
        )
        audit_mod.audit(
            settings.audit_log,
            {
                "client": "hermes",
                "tool": "symphony_recover_blocked",
                "request_id": request_id,
                "result": "success",
            },
        )
        return result

    @mcp.tool()
    async def symphony_skip_document(request_id: str) -> dict[str, Any]:
        """Skip the Document stage for a ticket. (control action)"""
        policy.assert_control_allowed()
        result = await client.skip_document(request_id)
        audit_mod.audit(
            settings.audit_log,
            {
                "client": "hermes",
                "tool": "symphony_skip_document",
                "request_id": request_id,
                "result": "success",
            },
        )
        return result

    return mcp


def create_app(settings: Settings | None = None):
    """Build the authenticated ASGI app (FastMCP + /health + bearer middleware)."""
    settings = settings or load()
    mcp = build_mcp(settings)
    app = mcp.streamable_http_app()

    async def health(_request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.add_route("/health", health)
    app.add_middleware(BearerAuthMiddleware, token=settings.token)
    return app


def main() -> None:
    settings = load()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
