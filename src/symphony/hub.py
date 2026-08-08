"""Central local project hub.

The hub coordinates registered projects without embedding their boards.  Each
project continues to run as an independent Symphony service; opening a project
therefore follows its service URL rather than switching a shared orchestrator.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import signal
import sys
from ipaddress import ip_address
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, cast


from aiohttp import web

from .projects import Project as ProjectRecord
from .projects import ProjectRegistry, canonical_project_repo
from .workflow.builder import build_service_config
from .workflow.parser import load_workflow


_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
_HUB_BIND_HOST: web.AppKey[str] = web.AppKey("symphony.hub.bind_host", str)


@web.middleware
async def _hub_guard(request: web.Request, handler):
    """Block DNS-rebinding and cross-origin mutations on the local hub."""
    bind = str(request.app.get(_HUB_BIND_HOST) or "127.0.0.1").lower()
    raw_host = (request.host or "").strip().lower()
    host = (
        raw_host.split("]", 1)[0] + "]"
        if raw_host.startswith("[")
        else raw_host.rsplit(":", 1)[0]
    )
    if (
        bind in {"", "localhost", "127.0.0.1", "::1", "[::1]"}
        and host not in _ALLOWED_HOSTS
    ):
        return _json_error(403, "forbidden_host", f"host {request.host!r} not allowed")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if bind not in {"localhost", "127.0.0.1", "::1", "[::1]"}:
            return _json_error(
                403, "forbidden_bind", "mutations require a loopback-bound hub"
            )
        peer = (
            request.transport.get_extra_info("peername") if request.transport else None
        )
        peer_host = str(peer[0]).split("%", 1)[0] if peer else ""
        try:
            peer_is_loopback = ip_address(peer_host).is_loopback
        except ValueError:
            peer_is_loopback = False
        if not peer_is_loopback:
            return _json_error(
                403, "forbidden_peer", "mutations require a loopback client"
            )
        origin = request.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme != request.scheme or parsed.netloc.lower() != raw_host:
                return _json_error(
                    403, "forbidden_origin", "cross-origin mutations are not allowed"
                )
        if request.content_type != "application/json":
            return _json_error(
                415, "unsupported_media_type", "mutations require application/json"
            )
    return await handler(request)


class HubRegistry(Protocol):
    """Service boundary used by the hub and its isolated HTTP tests."""

    def list(self) -> list[ProjectRecord]: ...

    def status(self, project_id: str) -> Any: ...

    def start(self, project_id: str) -> Any: ...

    def stop(self, project_id: str) -> Any: ...


_HUB_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Symphony Hub</title>
<style>
:root{color-scheme:light dark;font-family:system-ui,sans-serif}body{max-width:900px;margin:3rem auto;padding:0 1rem}header{display:flex;align-items:baseline;justify-content:space-between}.grid{display:grid;gap:1rem}.card,form{border:1px solid #8886;border-radius:10px;padding:1rem}.row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}.status{font-size:.85rem;font-weight:700}.running{color:#299447}.stopped{color:#777}button,a.open{padding:.45rem .7rem;border-radius:6px;border:1px solid #8888;background:transparent;color:inherit;text-decoration:none;cursor:pointer}code{overflow-wrap:anywhere}label{display:grid;gap:.25rem;flex:1;min-width:12rem}input{padding:.5rem}#error,.diagnostic{color:#c33}</style>
</head>
<body>
<header><div><h1>Symphony Hub</h1><p>Manage local Symphony projects</p></div><button id="refresh" type="button">Refresh</button></header>
<section aria-labelledby="add-heading"><h2 id="add-heading">Add project</h2><form id="add-project"><div class="row"><label>Project name<input name="name" required autocomplete="off"></label><label>Project path<input name="path" required autocomplete="off" placeholder="/path/to/project"></label></div><div class="row"><label>Project ID (optional)<input name="id" autocomplete="off"></label><label>Workflow (optional)<input name="workflow" autocomplete="off" placeholder="WORKFLOW.md"></label><button type="submit">Add project</button></div></form></section>
<h2>Projects</h2><p id="error" role="alert"></p><main id="projects" class="grid" aria-live="polite"></main>
<script>
const root=document.querySelector('#projects'), error=document.querySelector('#error');
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
async function request(path,options){const r=await fetch(path,options);const body=await r.json();if(!r.ok)throw new Error(body.error?.message||`Request failed (${r.status})`);return body}
function pathLine(label,value){const p=el('p');p.append(`${label}: `,el('code',value||'Unavailable'));return p}
function card(p){const article=el('article',undefined,'card'), title=el('h3',p.name), state=el('span',p.running?'Running':'Stopped',`status ${p.running?'running':'stopped'}`), row=el('div',undefined,'row');row.append(state);const open=el('button',p.running?'Open project':'Start and open');open.onclick=async()=>{open.disabled=true;error.textContent='';try{const result=await request(`/api/v1/projects/${encodeURIComponent(p.id)}/open`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});window.location.assign(result.url)}catch(e){error.textContent=e.message}finally{open.disabled=false}};row.append(open);article.append(title,pathLine('Repository',p.repo),pathLine('Workflow',p.workflow),pathLine('Issues are stored here',p.board),row);for(const message of p.diagnostics||[])article.append(el('p',message,'diagnostic'));return article}
async function load(){error.textContent='';try{const body=await request('/api/v1/projects');root.replaceChildren(...body.projects.map(card))}catch(e){error.textContent=e.message}}
document.querySelector('#refresh').onclick=load;document.querySelector('#add-project').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget,data=Object.fromEntries(new FormData(form));for(const key of ['id','workflow'])if(!data[key])delete data[key];error.textContent='';try{await request('/api/v1/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});form.reset();await load()}catch(e){error.textContent=e.message}};load();
</script>
</body></html>
"""


def _status_running(status: Any) -> bool:
    if isinstance(status, bool):
        return status
    if isinstance(status, str):
        return status.lower() == "running"
    if isinstance(status, Mapping):
        if "running" in status:
            return bool(status["running"])
        return str(status.get("state", "")).lower() == "running"
    if hasattr(status, "running"):
        return bool(status.running)
    return str(getattr(status, "state", "")).lower() == "running"


def _service_url(project: ProjectRecord, status: Any) -> str:
    explicit_url = (
        status.get("url")
        if isinstance(status, Mapping)
        else getattr(status, "url", None)
    )
    if explicit_url:
        return str(explicit_url)

    record = (
        status.get("record")
        if isinstance(status, Mapping)
        else getattr(status, "record", None)
    )
    source = record or status
    if isinstance(source, Mapping):
        host = str(source.get("host", project.host))
        port = int(source.get("port", project.port))
    else:
        host = str(getattr(source, "host", project.host))
        port = int(getattr(source, "port", project.port))
    host = "127.0.0.1" if host in {"", "0.0.0.0", "::", "[::]"} else host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}/"


async def _invoke(operation: Any, *args: Any) -> Any:
    # Registry implementations commonly probe or launch OS processes. Keep
    # those synchronous operations off aiohttp's event loop.
    if inspect.iscoroutinefunction(operation):
        return await operation(*args)
    result = await asyncio.to_thread(operation, *args)
    if inspect.isawaitable(result):
        return await result
    return result


def _json_error(status: int, code: str, message: str) -> web.Response:
    return web.json_response(
        {"error": {"code": code, "message": message}}, status=status
    )


def _board_path(project: ProjectRecord) -> str:
    """Resolve the configured file board using the real workflow parser."""
    config = build_service_config(load_workflow(project.workflow))
    if config.tracker.kind != "file" or config.tracker.board_root is None:
        raise ValueError("workflow does not use a file tracker")
    return str(config.tracker.board_root.resolve())


def _default_create_project(
    registry: HubRegistry, values: Mapping[str, Any]
) -> ProjectRecord:
    """Narrow adapter around the shared setup/adoption service."""
    from .projects import create_or_adopt_project, source_checkout

    return create_or_adopt_project(
        registry=cast(ProjectRegistry, registry),
        source=source_checkout(),
        target=Path(str(values["path"])),
        name=str(values["name"]),
        project_id=str(values["id"]) if values.get("id") is not None else None,
        workflow=str(values.get("workflow", "WORKFLOW.md")),
    )


def _project_by_id(records: list[ProjectRecord], project_id: str) -> ProjectRecord:
    for project in records:
        if project.id == project_id:
            return project
    raise KeyError(project_id)


def build_hub_app(
    registry: HubRegistry,
    *,
    create_project: Callable[[HubRegistry, Mapping[str, Any]], ProjectRecord]
    | None = None,
) -> web.Application:
    """Build the aiohttp hub around registry and project-setup boundaries."""

    app = web.Application(middlewares=[_hub_guard], client_max_size=16 * 1024)
    setup = create_project or _default_create_project

    async def index(_request: web.Request) -> web.Response:
        return web.Response(text=_HUB_HTML, content_type="text/html")

    async def projects(_request: web.Request) -> web.Response:
        records = await _invoke(registry.list)
        statuses = await asyncio.gather(
            *(_invoke(registry.status, project.id) for project in records),
            return_exceptions=True,
        )
        repositories = await asyncio.gather(
            *(_invoke(canonical_project_repo, project.repo) for project in records),
            return_exceptions=True,
        )
        boards = await asyncio.gather(
            *(_invoke(_board_path, project) for project in records),
            return_exceptions=True,
        )
        payload = []
        for project, status_result, repository_result, board_result in zip(
            records, statuses, repositories, boards, strict=True
        ):
            diagnostics: list[str] = []
            if isinstance(repository_result, BaseException):
                diagnostics.append(f"Repository path unavailable: {repository_result}")
                repository = str(Path(project.repo).expanduser().resolve())
            else:
                repository = str(repository_result)
            if isinstance(status_result, BaseException):
                diagnostics.append(f"Status unavailable: {status_result}")
                status: Any = "stopped"
            else:
                status = status_result
            if isinstance(board_result, BaseException):
                diagnostics.append(f"Board path unavailable: {board_result}")
                board = None
            else:
                board = board_result
            running = _status_running(status)
            service_url = None
            if running:
                try:
                    service_url = _service_url(project, status)
                except (TypeError, ValueError) as exc:
                    diagnostics.append(f"Service URL unavailable: {exc}")
            payload.append(
                {
                    "id": project.id,
                    "name": project.name,
                    "repo": repository,
                    "workflow": str(Path(project.workflow).expanduser().resolve()),
                    "board": board,
                    "host": project.host,
                    "port": project.port,
                    "running": running,
                    "url": service_url,
                    "diagnostics": diagnostics,
                }
            )
        return web.json_response({"projects": payload})

    async def add_project(request: web.Request) -> web.Response:
        try:
            values = await request.json()
        except (ValueError, TypeError):
            return _json_error(
                400, "invalid_json", "request body must be a JSON object"
            )
        if not isinstance(values, dict):
            return _json_error(
                400, "invalid_request", "request body must be a JSON object"
            )
        missing = [
            key
            for key in ("name", "path")
            if not isinstance(values.get(key), str) or not values[key].strip()
        ]
        if missing:
            return _json_error(
                400,
                "invalid_request",
                f"missing required field{'s' if len(missing) > 1 else ''}: {', '.join(missing)}",
            )
        allowed = {"name", "path", "id", "workflow"}
        unexpected = sorted(set(values) - allowed)
        if unexpected:
            return _json_error(
                400, "invalid_request", f"unknown field: {unexpected[0]}"
            )
        limits = {"name": 200, "path": 4096, "id": 64, "workflow": 1024}
        for key, limit in limits.items():
            if key not in values:
                continue
            if not isinstance(values[key], str):
                return _json_error(
                    400, "invalid_request", f"field {key} must be a string"
                )
            if len(values[key]) > limit:
                return _json_error(400, "invalid_request", f"field {key} is too long")
        try:
            project = await _invoke(setup, registry, values)
        except (OSError, RuntimeError, ValueError) as exc:
            return _json_error(409, "project_setup_failed", str(exc))
        return web.json_response(
            {
                "project": {
                    "id": project.id,
                    "name": project.name,
                    "repo": str(project.repo),
                    "workflow": str(project.workflow),
                    "host": project.host,
                    "port": project.port,
                }
            },
            status=201,
        )

    async def mutate(request: web.Request) -> web.Response:
        project_id = request.match_info["project_id"]
        action = request.match_info["action"]
        operation = registry.start if action == "start" else registry.stop
        try:
            result = await _invoke(operation, project_id)
            if isinstance(result, int) and result != 0:
                raise RuntimeError(f"service command exited with status {result}")
            status = await _invoke(registry.status, project_id)
        except KeyError:
            return _json_error(
                404, "project_not_found", f"unknown project {project_id}"
            )
        except (ValueError, RuntimeError) as exc:
            missing = "unknown project" in str(exc)
            return _json_error(
                404 if missing else 409,
                "project_not_found" if missing else f"project_{action}_failed",
                str(exc),
            )
        return web.json_response(
            {"project_id": project_id, "running": _status_running(status)}
        )

    async def open_project(request: web.Request) -> web.Response:
        project_id = request.match_info["project_id"]
        try:
            records = await _invoke(registry.list)
            project = _project_by_id(records, project_id)
            status = await _invoke(registry.status, project_id)
            if not _status_running(status):
                result = await _invoke(registry.start, project_id)
                if isinstance(result, int) and result != 0:
                    raise RuntimeError(f"service command exited with status {result}")
                status = await _invoke(registry.status, project_id)
            if not _status_running(status):
                raise RuntimeError("service did not report running after start")
        except KeyError:
            return _json_error(
                404, "project_not_found", f"unknown project {project_id}"
            )
        except (OSError, RuntimeError, ValueError) as exc:
            missing = "unknown project" in str(exc)
            return _json_error(
                404 if missing else 409,
                "project_not_found" if missing else "project_open_failed",
                str(exc),
            )
        return web.json_response(
            {
                "project_id": project_id,
                "running": True,
                "url": _service_url(project, status),
            }
        )

    app.router.add_get("/", index)
    app.router.add_get("/api/v1/projects", projects)
    app.router.add_post("/api/v1/projects", add_project)
    app.router.add_post("/api/v1/projects/{project_id}/open", open_project)
    app.router.add_post("/api/v1/projects/{project_id}/{action:start|stop}", mutate)
    return app


async def run_hub(
    app: web.Application, host: str = "127.0.0.1", port: int = 8787
) -> tuple[web.AppRunner, int]:
    """Start a hub application and return its runner and actual bound port."""

    app[_HUB_BIND_HOST] = host
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    bound_port = port
    server = getattr(site, "_server", None)
    if server is not None and server.sockets:
        bound_port = int(server.sockets[0].getsockname()[1])
    return runner, bound_port


async def _serve(registry: ProjectRegistry, host: str, port: int) -> int:
    runner, bound_port = await run_hub(build_hub_app(registry), host=host, port=port)
    display_host = "127.0.0.1" if host in {"", "0.0.0.0", "::", "[::]"} else host
    print(
        f"symphony: hub ready at http://{display_host}:{bound_port}/", file=sys.stderr
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    try:
        await stop_event.wait()
    finally:
        await runner.cleanup()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the central hub until interrupted."""

    parser = argparse.ArgumentParser(
        prog="symphony hub",
        description="Serve the central UI for registered local Symphony projects.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="bind host (default: loopback)"
    )
    parser.add_argument(
        "--port", type=int, default=8787, help="bind port (default: 8787)"
    )
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    try:
        return asyncio.run(_serve(ProjectRegistry(), args.host, args.port))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
