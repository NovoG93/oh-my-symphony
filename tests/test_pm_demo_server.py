from __future__ import annotations

import importlib.util
import os
import subprocess
import uuid
from dataclasses import replace

import pytest
import yaml
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer

from symphony.orchestrator import Orchestrator
from symphony.pm import demo, demo_server, host_tools, openspec
from symphony.pm.models import PmError
from symphony.pm.routes import PM_DEMO_KEY, PM_DEVELOPMENT_VERIFICATION_KEY
from symphony.server import build_app
from symphony.webapi import BIND_HOST_KEY, _api_guard
from symphony.workflow import WorkflowState
from tests._pm_support import skip_or_fail


@pytest.fixture(autouse=True)
def environment_unchanged():
    before = dict(os.environ)
    before.pop("PYTEST_CURRENT_TEST", None)
    yield
    after = dict(os.environ)
    after.pop("PYTEST_CURRENT_TEST", None)
    assert after == before


@pytest.fixture(autouse=True)
def clean_environment(environment_unchanged, tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key in {
            "SYMPHONY_LANG", "SYMPHONY_API_TOKEN", "SYMPHONY_TRUSTED_ORIGINS",
            "SYMPHONY_SERVICE_INSTANCE_ID", "GIT_CONFIG_GLOBAL",
        } or key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_")):
            monkeypatch.delenv(key)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "unused-projects.json"))
    monkeypatch.setattr(openspec, "_NODE_CACHE", {})
    from symphony.pm import codex_compatibility
    monkeypatch.setattr(codex_compatibility, "_CACHE", {})

    async def forbid_start(self):
        pytest.fail("a demo server must never start dispatch")

    monkeypatch.setattr(Orchestrator, "start", forbid_start)


@pytest.fixture
def supported_node():
    try:
        openspec.resolve_node()
    except PmError as exc:
        skip_or_fail(exc.message, flag="SYMPHONY_PM_NODE_REQUIRED")


def _environment(monkeypatch, directory):
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(directory / "projects.json"))
    monkeypatch.delenv("SYMPHONY_TRUSTED_ORIGINS", raising=False)


def _orchestrator(workflow):
    state = WorkflowState(workflow)
    cfg, error = state.reload()
    assert cfg is not None and error is None
    return Orchestrator(state)


def _minimal(tmp_path, monkeypatch):
    directory = tmp_path / "demo"
    project = directory / "project"
    project.mkdir(parents=True)
    (project / "kanban").mkdir()
    workflow = project / "WORKFLOW.md"
    workflow.write_text(
        "---\n" + yaml.safe_dump({
            "tracker": {"kind": "file", "board_root": "./kanban", "project_slug": demo.PROJECT_ID},
            "workspace": {"root": str(directory / "worker-workspaces")},
            "pm": {"planning_workspace_root": str(directory / "planning")},
        }) + "---\nPrompt\n", encoding="utf-8",
    )
    _environment(monkeypatch, directory)
    return directory, _orchestrator(workflow)


def _seed(tmp_path, monkeypatch, lang="en"):
    preflight = demo.preflight_demo(tmp_path / "demo", lang)
    directory = demo.claim_demo_directory(preflight)
    workspace = demo.create_demo(directory, preflight.lang)
    _environment(monkeypatch, directory)
    return workspace, _orchestrator(workspace.workflow_path)


async def _client(app):
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.parametrize("wrong", ["missing_registry", "other_registry", "trusted_origin", "trusted_whitespace"])
def test_build_refuses_unisolated_environment_without_changing_it(tmp_path, monkeypatch, wrong):
    directory, orchestrator = _minimal(tmp_path, monkeypatch)
    if wrong == "missing_registry":
        monkeypatch.delenv("SYMPHONY_PROJECTS_FILE")
    elif wrong == "other_registry":
        monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "user-projects.json"))
    else:
        monkeypatch.setenv("SYMPHONY_TRUSTED_ORIGINS", " " if wrong == "trusted_whitespace" else "https://evil.example")
    before = dict(os.environ)
    with pytest.raises(demo.DemoError, match="demo server requires") as refusal:
        demo_server.build_demo_app(orchestrator, directory)
    assert refusal.value.exit_code == 1
    assert dict(os.environ) == before
    assert not (directory / "projects.json").exists()


async def test_guard_refuses_all_non_pm_mutations_and_chat_after_api_guard(tmp_path, monkeypatch):
    directory, orchestrator = _minimal(tmp_path, monkeypatch)
    app = demo_server.build_demo_app(orchestrator, directory)
    assert list(app.middlewares) == [_api_guard, demo_server.demo_read_only]
    assert app[PM_DEMO_KEY] == directory.resolve()
    assert app[PM_DEVELOPMENT_VERIFICATION_KEY] is True
    assert app[BIND_HOST_KEY] == "127.0.0.1"
    client = await _client(app)
    try:
        mutations = {
            (route.method, route.resource.canonical)
            for route in app.router.routes()
            if route.resource is not None
            and route.method not in {"GET", "HEAD"}
            and route.resource.canonical.startswith("/api/")
            and not route.resource.canonical.startswith("/api/v1/pm/")
        }
        assert {("POST", "/api/v1/issues"), ("PUT", "/api/v1/workflow/states"),
                ("POST", "/api/v1/refresh")} <= mutations
        mutations |= {("GET", "/api/v1/chat/ws"), ("HEAD", "/api/v1/chat/ws"),
                      ("OPTIONS", "/api/v1/unknown"), ("POST", "/api/v1/pm"),
                      ("POST", "/api/v1/pm-other/action")}
        for method, path in sorted(mutations):
            for parameter in ("identifier", "state", "session_id", "project_id", "name", "lane"):
                path = path.replace("{" + parameter + "}", "fixture")
            response = await client.request(method, path, json={})
            assert response.status == 403, (method, path, await response.text())
            if method != "HEAD":
                assert await response.json() == {"error": {
                    "code": "demo_read_only",
                    "message": "Only PM Workspace actions are available in the demo",
                }}
        for method in ("GET", "HEAD"):
            for path in ("/", "/api/v1/state", "/api/v1/board"):
                response = await client.request(method, path)
                assert response.status == 200, (method, path, await response.text())
        assert (await client.post("/api/v1/pm/unknown", json={})).status == 404
        assert orchestrator._tick_task is None
    finally:
        await client.close()
    assert orchestrator._run_registry is None
    assert list((tmp_path / "home").iterdir()) == []


async def test_token_host_and_origin_checks_run_before_demo_guard(tmp_path, monkeypatch):
    directory, orchestrator = _minimal(tmp_path, monkeypatch)
    monkeypatch.setenv("SYMPHONY_API_TOKEN", "demo-token")
    monkeypatch.setenv("SYMPHONY_TRUSTED_ORIGINS", "https://evil.example")
    # The CLI removes this variable; the app builder never edits the environment.
    monkeypatch.delenv("SYMPHONY_TRUSTED_ORIGINS")
    client = await _client(demo_server.build_demo_app(orchestrator, directory))
    try:
        response = await client.post("/api/v1/refresh", json={})
        assert response.status == 401
        assert (await response.json())["error"]["code"] == "unauthorized"
        headers = {"Authorization": "Bearer demo-token"}
        assert (await client.get("/api/v1/state", headers=headers)).status == 200
        response = await client.post("/api/v1/refresh", json={}, headers=headers)
        assert response.status == 403
        assert (await response.json())["error"]["code"] == "demo_read_only"
        response = await client.post(
            f"/api/v1/pm/changes/{'a' * 32}/validate", json={},
            headers={**headers, "Origin": "https://evil.example"},
        )
        assert response.status == 403
        assert (await response.json())["error"]["code"] == "forbidden_origin"
        response = await client.post(
            "/api/v1/refresh", json={}, headers={**headers, "Host": "evil.example"},
        )
        assert response.status == 403
        assert (await response.json())["error"]["code"] == "forbidden_host"
    finally:
        await client.close()


async def test_paths_are_checked_once_before_serving(tmp_path, monkeypatch):
    directory, orchestrator = _minimal(tmp_path, monkeypatch)
    original = demo_server.check_demo_paths
    calls = []

    def check(config, root):
        calls.append((config, root))
        original(config, root)

    monkeypatch.setattr(demo_server, "check_demo_paths", check)
    app = demo_server.build_demo_app(orchestrator, directory)
    assert calls == [(orchestrator.workflow_state.current(), directory.resolve())]
    client = await _client(app)
    try:
        for _ in range(2):
            assert (await client.get("/api/v1/pm/capabilities")).status == 200
        assert len(calls) == 1
    finally:
        await client.close()


def test_tampered_loaded_config_refuses_before_build_and_keeps_demo(tmp_path, monkeypatch, supported_node):
    workspace, orchestrator = _seed(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    source = workspace.workflow_path.read_text(encoding="utf-8")
    workspace.workflow_path.write_text(
        source.replace(str(workspace.planning_path), str(outside)), encoding="utf-8",
    )
    config, error = orchestrator.workflow_state.reload()
    assert config is not None and error is None
    monkeypatch.setattr(demo_server, "build_app", lambda _: pytest.fail("unsafe config built"))
    with pytest.raises(demo.DemoError, match="demo_config_outside") as refusal:
        demo_server.build_demo_app(orchestrator, workspace.directory)
    assert refusal.value.exit_code == 1
    assert str(refusal.value) == (
        "demo_config_outside: the demo configuration points outside the demo folder "
        f"(pm.planning_workspace_root = {outside})"
    )
    assert workspace.workflow_path.is_file() and workspace.planning_path.is_dir()
    assert not outside.exists()


async def test_reserved_socket_is_served_and_cleanup_closes_registry(tmp_path, monkeypatch):
    sock = demo_server.bind_demo_socket()
    host, port = sock.getsockname()
    assert host == "127.0.0.1" and port > 0
    assert not (tmp_path / "demo").exists()
    directory, orchestrator = _minimal(tmp_path, monkeypatch)
    runner = None
    try:
        runner, served_port = await demo_server.start_demo_server(
            demo_server.build_demo_app(orchestrator, directory), sock,
        )
        assert served_port == port
        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/api/v1/pm/capabilities") as response:
                assert response.status == 200
        assert orchestrator._run_registry is not None
        assert orchestrator._tick_task is None
    finally:
        if runner is not None:
            await runner.cleanup()
        else:
            sock.close()
    assert orchestrator._run_registry is None
    assert sock.fileno() == -1


async def test_site_failure_closes_socket_and_registry(tmp_path, monkeypatch):
    directory, orchestrator = _minimal(tmp_path, monkeypatch)
    cfg = orchestrator.workflow_state.current()
    assert cfg is not None
    orchestrator._ensure_run_registry(cfg)
    sock = demo_server.bind_demo_socket()

    async def fail_start(self):
        raise OSError("fixture listen failure")

    monkeypatch.setattr(web.SockSite, "start", fail_start)
    with pytest.raises(OSError, match="fixture listen failure"):
        await demo_server.start_demo_server(demo_server.build_demo_app(orchestrator, directory), sock)
    assert sock.fileno() == -1
    assert orchestrator._run_registry is None


async def _json(client, method, path, *, status=200, body=None):
    response = await client.request(method, path, json=body)
    assert response.status == status, await response.text()
    return await response.json()


def _envelope(workspace, revision=None):
    result = {"project_id": demo.PROJECT_ID, "change_id": workspace.change_id}
    if revision is not None:
        result["expected_revision"] = revision
    return result


async def _approve(client, workspace):
    path = f"/api/v1/pm/changes/{workspace.change_id}"
    change = await _json(client, "GET", path + f"?project_id={demo.PROJECT_ID}")
    approval = await _json(client, "POST", path + "/approvals", status=201, body={
        **_envelope(workspace, change["revision"]),
        "confirmation_token": change["approval_confirmation_token"],
        "reviewer_label": "Demo reviewer",
    })
    result = subprocess.run(
        ["git", "-C", str(workspace.planning_path), "show", "-s", "--format=%an <%ae>%n%s", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    author, subject = result.stdout.strip().split("\n")
    assert author == "Symphony PM Demo <pm-demo@example.invalid>"
    assert subject == f"Approve PM plan team-leave-request ({approval['approval_id'][:12]})"
    return approval


@pytest.mark.parametrize("lang", ["en", "ko"])
async def test_ordered_demo_api_journey(tmp_path, monkeypatch, supported_node, lang):
    workspace, orchestrator = _seed(tmp_path, monkeypatch, lang)
    client = await _client(demo_server.build_demo_app(orchestrator, workspace.directory))
    path = f"/api/v1/pm/changes/{workspace.change_id}"
    query = f"?project_id={demo.PROJECT_ID}"
    try:
        capabilities = await _json(client, "GET", "/api/v1/pm/capabilities")
        assert capabilities["demo"] == {"active": True}
        assert capabilities["capabilities"]["assistance"]["reasons"] == [
            "Off in the demo: it would contact an AI model.",
        ]
        change = await _json(client, "GET", path + query)
        assert change["state"] == "Validated" and change["approval_id"] is None
        validated = await _json(client, "POST", path + "/validate", body=_envelope(workspace))
        assert validated["valid"] and validated["source"] == "combined"
        assert not [finding for finding in validated["findings"] if finding["severity"] == "blocking"]

        before = await _json(client, "GET", path + "/wbs" + query)
        identities = [(task["task_id"], task["title"]) for task in before["tasks"]]
        group = next(task for task in before["tasks"] if task["kind"] == "Group")
        added = await _json(client, "PATCH", path + "/wbs", body={
            **_envelope(workspace, change["revision"]),
            "payload": {"operations": [{"op": "create_task", "task": {
                "kind": "Backend", "parent_id": group["task_id"],
                "title": "Temporary demo child", "order": 100,
            }}]},
        })
        edited = await _json(client, "GET", path + "/wbs" + query)
        assert len(edited["tasks"]) == len(before["tasks"]) + 1
        child_id = added["impacted_ids"][0]
        assert next(task for task in edited["tasks"] if task["task_id"] == child_id)["parent_id"] == group["task_id"]
        await _json(client, "PATCH", path + "/wbs", body={
            **_envelope(workspace, added["revision"]),
            "payload": {"operations": [{"op": "delete_task", "task_id": child_id}]},
        })
        restored = await _json(client, "GET", path + "/wbs" + query)
        assert [(task["task_id"], task["title"]) for task in restored["tasks"]] == identities

        await _approve(client, workspace)
        change = await _json(client, "GET", path + query)
        preview = await _json(client, "POST", path + "/handoffs/preview", body=_envelope(workspace, change["revision"]))
        published = await _json(client, "POST", path + "/handoffs", status=201, body={
            **_envelope(workspace, change["revision"]),
            "client_operation_id": uuid.uuid4().hex,
            "preview_token": preview["preview_token"], "confirmation_token": preview["preview_token"],
        })
        assert len(published["ticket_identifiers"]) == 5
        tickets = list((workspace.directory / "project" / "kanban").glob("*.md"))
        assert len(tickets) == 5
        for ticket in tickets:
            assert yaml.safe_load(ticket.read_text(encoding="utf-8").split("---", 2)[1])["state"] == "Todo"
        registry = orchestrator._run_registry
        assert registry is not None
        assert registry._connect().execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert (await _json(client, "GET", "/api/v1/state"))["counts"]["running"] == 0
        formats = ["csv", "markdown"]
        if importlib.util.find_spec("openpyxl") is not None:
            formats.append("xlsx")
        for format_name in formats:
            exported = await _json(client, "POST", path + "/exports", status=201, body={
                **_envelope(workspace), "format": format_name,
            })
            download = await client.get(f"/api/v1/pm/exports/{exported['export_id']}/file")
            assert download.status == 200
            assert await download.read()
        refusal = await _json(client, "POST", path + "/archive/preview", status=409, body=_envelope(workspace, change["revision"]))
        assert refusal["error"]["code"] == "acceptance_incomplete"
        assert orchestrator._tick_task is None
    finally:
        await client.close()
    assert orchestrator._run_registry is None
    assert list((tmp_path / "home").iterdir()) == []
    assert not (workspace.directory / "projects.json").exists()


async def test_non_demo_dev_verification_approves_with_real_bundled_openspec(tmp_path, monkeypatch, supported_node):
    workspace, orchestrator = _seed(tmp_path, monkeypatch)
    cfg = orchestrator.workflow_state.current()
    assert cfg is not None and cfg.pm.openspec_executable is None
    monkeypatch.setattr(orchestrator.workflow_state, "current", lambda: replace(cfg, pm=replace(cfg.pm, assistance=True)))
    monkeypatch.setattr(host_tools, "probe_codex", lambda: {
        "required": "0.160.0", "found": "0.160.0", "available": True, "reason": None,
    })
    calls = []
    original = openspec.OpenSpecAdapter._run

    def record(self, root, *args, **kwargs):
        assert self.executable is None
        calls.append(args)
        return original(self, root, *args, **kwargs)

    monkeypatch.setattr(openspec.OpenSpecAdapter, "_run", record)
    app = build_app(orchestrator)
    assert PM_DEMO_KEY not in app and PM_DEVELOPMENT_VERIFICATION_KEY not in app
    app[PM_DEVELOPMENT_VERIFICATION_KEY] = True
    client = await _client(app)
    try:
        capabilities = await _json(client, "GET", "/api/v1/pm/capabilities")
        assert capabilities["demo"] == {"active": False}
        assert all(capabilities["flags"].values())
        validated = await _json(client, "POST", f"/api/v1/pm/changes/{workspace.change_id}/validate", body=_envelope(workspace))
        assert validated["valid"] and validated["source"] == "combined"
        await _approve(client, workspace)
        assert any(args[0] == "validate" and "--strict" in args for args in calls)
        assert any(args[0] == "status" for args in calls)
        assert any(args[:2] == ("instructions", "apply") for args in calls)
    finally:
        await client.close()
        await orchestrator.stop()
    assert list((tmp_path / "home").iterdir()) == []
