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
from typing import Any, Mapping

from aiohttp import web

from .projects import Project as ProjectRecord
from .projects import ProjectRegistry


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
    if bind in {"", "localhost", "127.0.0.1", "::1", "[::1]"} and host not in _ALLOWED_HOSTS:
        return _json_error(403, "forbidden_host", f"host {request.host!r} not allowed")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.content_type != "application/json":
        return _json_error(415, "unsupported_media_type", "mutations require application/json")
    return await handler(request)


_HUB_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Symphony Hub</title>
<style>
:root{color-scheme:light dark;font-family:system-ui,sans-serif}body{max-width:900px;margin:3rem auto;padding:0 1rem}header{display:flex;align-items:baseline;justify-content:space-between}.grid{display:grid;gap:1rem}.card{border:1px solid #8886;border-radius:10px;padding:1rem}.row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}.status{font-size:.85rem;font-weight:700}.running{color:#299447}.stopped{color:#999}button,a.open{padding:.45rem .7rem;border-radius:6px;border:1px solid #8888;background:transparent;color:inherit;text-decoration:none;cursor:pointer}code{opacity:.75}#error{color:#d33}</style>
</head>
<body>
<header><div><h1>Symphony Hub</h1><p>Registered local projects</p></div><button id="refresh">Refresh</button></header>
<p id="error" role="alert"></p><main id="projects" class="grid" aria-live="polite"></main>
<script>
const root=document.querySelector('#projects'), error=document.querySelector('#error');
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
async function request(path,options){const r=await fetch(path,options);const body=await r.json();if(!r.ok)throw new Error(body.error?.message||`Request failed (${r.status})`);return body}
function card(p){const article=el('article',undefined,'card'), title=el('h2',p.name), state=el('span',p.running?'Running':'Stopped',`status ${p.running?'running':'stopped'}`), meta=el('p'), row=el('div',undefined,'row');meta.append(el('code',p.repo));row.append(state);if(p.running&&p.url){const a=el('a','Open project','open');a.href=p.url;a.target='_self';row.append(a)}const action=el('button',p.running?'Stop':'Start');action.onclick=async()=>{action.disabled=true;error.textContent='';try{await request(`/api/v1/projects/${encodeURIComponent(p.id)}/${p.running?'stop':'start'}`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await load()}catch(e){error.textContent=e.message}finally{action.disabled=false}};row.append(action);article.append(title,meta,row);return article}
async function load(){error.textContent='';try{const body=await request('/api/v1/projects');root.replaceChildren(...body.projects.map(card))}catch(e){error.textContent=e.message}}
document.querySelector('#refresh').onclick=load;load();
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
        status.get("url") if isinstance(status, Mapping) else getattr(status, "url", None)
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
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


def build_hub_app(registry: ProjectRegistry) -> web.Application:
    """Build the aiohttp hub using only the project-registry boundary."""

    app = web.Application(middlewares=[_hub_guard])

    async def index(_request: web.Request) -> web.Response:
        return web.Response(text=_HUB_HTML, content_type="text/html")

    async def projects(_request: web.Request) -> web.Response:
        records = await _invoke(registry.list)
        statuses = await asyncio.gather(
            *(_invoke(registry.status, project.id) for project in records)
        )
        payload = []
        for project, status in zip(records, statuses, strict=True):
            running = _status_running(status)
            payload.append(
                {
                    "id": project.id,
                    "name": project.name,
                    "repo": str(project.repo),
                    "workflow": str(project.workflow),
                    "host": project.host,
                    "port": project.port,
                    "running": running,
                    "url": _service_url(project, status) if running else None,
                }
            )
        return web.json_response({"projects": payload})

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
            return _json_error(404, "project_not_found", f"unknown project {project_id}")
        except (ValueError, RuntimeError) as exc:
            missing = str(exc).startswith("unknown project ")
            return _json_error(
                404 if missing else 409,
                "project_not_found" if missing else f"project_{action}_failed",
                str(exc),
            )
        return web.json_response(
            {"project_id": project_id, "running": _status_running(status)}
        )

    app.router.add_get("/", index)
    app.router.add_get("/api/v1/projects", projects)
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
    print(f"symphony: hub ready at http://{display_host}:{bound_port}/", file=sys.stderr)

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
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default: loopback)")
    parser.add_argument("--port", type=int, default=8787, help="bind port (default: 8787)")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    try:
        return asyncio.run(_serve(ProjectRegistry(), args.host, args.port))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
