"""symphony-mcp: Streamable HTTP MCP gateway exposing oh-my-symphony to Hermes.

A separate, stateless process that talks to the running oh-my-symphony
orchestrator over its local REST API (default http://127.0.0.1:9999) and exposes
a small, semantic, authenticated MCP tool surface at ``/mcp``.

v1 tool surface (5 tools): list_projects, create_request, get_request, get_task,
get_run.
"""

from __future__ import annotations

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
    to_project,
    to_request_status,
    to_run_info,
    to_task_summary,
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
    policy = Policy(settings.allowed_projects)
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
