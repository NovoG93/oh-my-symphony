"""HTTP server: web Kanban app + JSON API.

`/` serves the built-in web board (see `webapi.py` for the REST surface:
board/issue CRUD, workflow column + prompt editing, stats). The
endpoints below predate the web app and remain for scripts and the TUI:

    GET  /api/v1/state           — runtime snapshot
    GET  /api/v1/<identifier>    — issue debug detail
    POST /api/v1/refresh         — trigger immediate poll/reconcile
    POST /api/v1/<id>/pause|resume
    POST /api/v1/<id>/recover-blocked
    POST /api/v1/<id>/skip-document  (deprecated alias: /skip-learn)
    POST /api/v1/issues/<id>/confirm-review
"""

from __future__ import annotations

import asyncio
import json
import time

from aiohttp import web

from .logging import get_logger
from .orchestrator import Orchestrator
from .webapi import BIND_HOST_KEY, register_web_routes
from .web_policy import (
    install_route_policies,
    policy_discovery_payload,
    resolve_policy,
)


log = get_logger()


def _error_response(status: int, code: str, message: str) -> web.Response:
    body = {"error": {"code": code, "message": message}}
    return web.json_response(body, status=status)


def build_app(orchestrator: Orchestrator) -> web.Application:
    app = web.Application()

    async def handle_state(_request: web.Request) -> web.Response:
        return web.json_response(orchestrator.snapshot())

    async def handle_issue(request: web.Request) -> web.Response:
        identifier = request.match_info.get("identifier", "")
        snapshot = orchestrator.issue_snapshot(identifier)
        if snapshot is None:
            return _error_response(404, "issue_not_found", f"unknown issue {identifier}")
        return web.json_response(snapshot)

    async def handle_refresh(request: web.Request) -> web.Response:
        try:
            body = await request.json() if request.body_exists else {}
        except json.JSONDecodeError:
            return _error_response(400, "invalid_json", "request body is not JSON")
        if body and not isinstance(body, dict):
            return _error_response(400, "invalid_body", "request body must be an object")
        coalesced = orchestrator.request_refresh()
        return web.json_response(
            {
                "queued": True,
                "coalesced": coalesced,
                "requested_at": _now_iso(),
                "operations": ["poll", "reconcile"],
            },
            status=202,
        )

    async def handle_pause(request: web.Request) -> web.Response:
        identifier = request.match_info.get("identifier", "")
        issue_id = orchestrator.find_running_issue_id(identifier)
        if issue_id is None:
            return _error_response(
                404, "issue_not_running", f"no running worker for {identifier}"
            )
        already = orchestrator.is_paused(issue_id)
        changed = orchestrator.pause_worker(issue_id)
        return web.json_response(
            {
                "issue_identifier": identifier,
                "issue_id": issue_id,
                "paused": True,
                "changed": changed,
                "already_paused": already,
            }
        )

    async def handle_resume(request: web.Request) -> web.Response:
        identifier = request.match_info.get("identifier", "")
        issue_id = orchestrator.find_resumable_issue_id(identifier)
        if issue_id is None:
            return _error_response(
                404,
                "issue_not_resumable",
                f"no running, retry-held, or idle paused worker for {identifier}",
            )
        changed = orchestrator.resume_worker(issue_id)
        return web.json_response(
            {
                "issue_identifier": identifier,
                "issue_id": issue_id,
                "paused": False,
                "changed": changed,
            }
        )

    async def handle_skip_document(request: web.Request) -> web.Response:
        identifier = request.match_info.get("identifier", "")
        changed, message = await orchestrator.skip_document(identifier)
        if not changed:
            status = 404 if message.startswith("unknown issue") else 409
            return _error_response(status, "document_skip_rejected", message)
        return web.json_response(
            {
                "issue_identifier": identifier,
                "skipped": True,
                "message": message,
            }
        )

    async def handle_recover_blocked(request: web.Request) -> web.Response:
        identifier = request.match_info.get("identifier", "")
        try:
            body = await request.json() if request.body_exists else {}
        except json.JSONDecodeError:
            return _error_response(400, "invalid_json", "request body is not JSON")
        if body and not isinstance(body, dict):
            return _error_response(400, "invalid_body", "request body must be an object")
        target_state = (
            body.get("fix_state", body.get("rca_state", body.get("target_state")))
            if isinstance(body, dict)
            else None
        )
        agent_kind = body.get("agent_kind") if isinstance(body, dict) else None
        if target_state is not None and not isinstance(target_state, str):
            return _error_response(
                400, "invalid_body", "fix_state must be a string"
            )
        if agent_kind is not None and not isinstance(agent_kind, str):
            return _error_response(400, "invalid_body", "agent_kind must be a string")
        changed, message, details = await orchestrator.recover_blocked_issue(
            identifier,
            target_state=target_state,
            agent_kind=agent_kind,
        )
        if not changed:
            status = 404 if message.startswith("unknown issue") else 409
            return _error_response(status, "blocked_recovery_rejected", message)
        return web.json_response(
            {
                "issue_identifier": identifier,
                "fix_created": True,
                # Deprecated alias retained for API compatibility.
                "rca_created": True,
                "message": message,
                **details,
            }
        )

    async def handle_method_not_allowed(request: web.Request) -> web.Response:
        return _error_response(405, "method_not_allowed", request.method)

    async def handle_debug_tasks(request: web.Request) -> web.Response:
        # Dump every live asyncio task with its suspended coroutine stack.
        # `Task.get_stack()` returns the deepest frame the task is parked
        # at — exactly what py-spy can't show us across the await boundary.
        # Live stacks and coroutine reprs can name local paths and prompt text.
        # The shared policy requires the explicit `debug` capability in every
        # mode; no peer-address bypass or second authorization gate applies.
        out = []
        for t in asyncio.all_tasks():
            stack_frames = []
            for frame in t.get_stack():
                stack_frames.append(
                    f"{frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}"
                )
            out.append(
                {
                    "name": t.get_name(),
                    "done": t.done(),
                    "cancelled": t.cancelled() if t.done() else False,
                    "coro_repr": repr(t.get_coro()),
                    "stack": stack_frames,
                }
            )
        return web.json_response({"tasks": out})

    async def handle_health(_request: web.Request) -> web.Response:
        full = orchestrator.health()
        payload = {
            key: full[key]
            for key in (
                "status",
                "version",
                "generated_at",
                "workflow_path",
                "orchestrator_pid",
                "counts",
            )
            if key in full
        }
        tick = full.get("tick")
        if isinstance(tick, dict):
            payload["tick"] = {
                key: tick[key]
                for key in (
                    "alive",
                    "started",
                    "last_completed_at",
                    "seconds_since_last",
                    "consecutive_failures",
                    "error_count",
                    "loop_restarts",
                )
                if key in tick
            }
        return web.json_response(payload)

    async def handle_policy(request: web.Request) -> web.Response:
        return web.json_response(policy_discovery_payload(request))

    app.router.add_get("/api/v1/health", handle_health)
    app.router.add_get("/api/v1/auth/policy", handle_policy)
    app.router.add_get("/api/v1/state", handle_state)
    app.router.add_get("/api/v1/refresh", handle_method_not_allowed)
    app.router.add_post("/api/v1/refresh", handle_refresh)
    app.router.add_get("/api/v1/_debug/tasks", handle_debug_tasks)
    # Web app routes (board/issues/workflow/stats + static SPA).
    # Registered before the `{identifier}` catch-alls below so named routes
    # like /api/v1/board resolve to their handlers, not to issue lookup.
    register_web_routes(app, orchestrator)
    app.router.add_post("/api/v1/{identifier}/pause", handle_pause)
    app.router.add_post("/api/v1/{identifier}/resume", handle_resume)
    app.router.add_post("/api/v1/{identifier}/recover-blocked", handle_recover_blocked)
    app.router.add_post("/api/v1/{identifier}/skip-document", handle_skip_document)
    # Deprecated alias — lane renamed Learn -> Document; old scripts keep working.
    app.router.add_post("/api/v1/{identifier}/skip-learn", handle_skip_document)
    app.router.add_get("/api/v1/{identifier}", handle_issue)

    install_route_policies(app)

    return app


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def run_server(
    app: web.Application, host: str, port: int
) -> tuple[web.AppRunner, int]:
    # The API guard middleware only enforces the loopback Host allowlist
    # when the server itself is loopback-bound; record the bind address.
    # Resolve once before opening a socket so unsafe or unknown policies fail
    # startup instead of exposing a half-working service.
    resolve_policy(host)
    app[BIND_HOST_KEY] = host
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    bound_port = port
    for tcp_site in runner.sites:
        sockets = getattr(tcp_site, "_server", None)
        if sockets is not None and getattr(sockets, "sockets", None):
            bound_port = sockets.sockets[0].getsockname()[1]
            break
    log.info("http_server_started", host=host, port=bound_port)
    return runner, bound_port
