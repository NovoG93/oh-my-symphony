"""HTTP contract for the central local Symphony project hub."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from symphony.hub import build_hub_app, run_hub
from symphony.projects import Project, ProjectRegistry


@dataclass
class _Registry:
    records: list[Project]
    running: set[str] = field(default_factory=set)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def list(self) -> list[Project]:
        return self.records

    def status(self, project_id: str) -> str:
        self._find(project_id)
        return "running" if project_id in self.running else "stopped"

    def start(self, project_id: str) -> None:
        self._find(project_id)
        self.calls.append(("start", project_id))
        self.running.add(project_id)

    def stop(self, project_id: str) -> None:
        self._find(project_id)
        self.calls.append(("stop", project_id))
        self.running.discard(project_id)

    def _find(self, project_id: str) -> Project:
        for record in self.records:
            if record.id == project_id:
                return record
        raise KeyError(project_id)


@pytest_asyncio.fixture
async def registry(tmp_path: Path) -> _Registry:
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    for repo in (alpha, beta):
        repo.mkdir()
        (repo / "WORKFLOW.md").write_text(
            "---\n"
            "tracker:\n"
            "  kind: file\n"
            "  board_root: ./boards\n"
            "---\n"
            "Work on issue: {{ issue.title }}\n"
        )
        (repo / "boards").mkdir()
    return _Registry(
        [
            Project(
                id="alpha",
                name="Alpha board",
                git_repo=str(alpha),
                workflow=str(alpha / "WORKFLOW.md"),
                host="127.0.0.1",
                port=9101,
            ),
            Project(
                id="beta",
                name="Beta board",
                git_repo=str(beta),
                workflow=str(beta / "WORKFLOW.md"),
                host="0.0.0.0",
                port=9102,
            ),
        ],
        running={"alpha"},
    )


@pytest_asyncio.fixture
async def client(registry: _Registry) -> AsyncIterator[TestClient]:
    test_client = TestClient(TestServer(build_hub_app(registry)))
    await test_client.start_server()
    try:
        yield test_client
    finally:
        await test_client.close()


async def test_index_is_standalone_hub_ui(client: TestClient) -> None:
    response = await client.get("/")
    text = await response.text()

    assert response.status == 200
    assert response.content_type == "text/html"
    assert "Symphony Hub" in text
    assert "/api/v1/projects" in text
    assert "Open project" in text


async def test_projects_reports_independent_service_urls(
    client: TestClient, registry: _Registry
) -> None:
    response = await client.get("/api/v1/projects")
    body = await response.json()

    assert response.status == 200
    alpha, beta = registry.records
    assert body == {
        "projects": [
            {
                "id": "alpha",
                "name": "Alpha board",
                "repo": alpha.git_repo,
                "workflow": alpha.workflow,
                "board": str(Path(alpha.git_repo) / "boards"),
                "host": "127.0.0.1",
                "port": 9101,
                "running": True,
                "url": "http://127.0.0.1:9101/",
                "diagnostics": [],
            },
            {
                "id": "beta",
                "name": "Beta board",
                "repo": beta.git_repo,
                "workflow": beta.workflow,
                "board": str(Path(beta.git_repo) / "boards"),
                "host": "0.0.0.0",
                "port": 9102,
                "running": False,
                "url": None,
                "diagnostics": [],
            },
        ]
    }


async def test_start_and_stop_change_only_requested_project(
    client: TestClient, registry: _Registry
) -> None:
    started = await client.post("/api/v1/projects/beta/start", json={})
    assert started.status == 200
    assert await started.json() == {"project_id": "beta", "running": True}
    assert registry.running == {"alpha", "beta"}

    stopped = await client.post("/api/v1/projects/alpha/stop", json={})
    assert stopped.status == 200
    assert await stopped.json() == {"project_id": "alpha", "running": False}
    assert registry.running == {"beta"}
    assert registry.calls == [("start", "beta"), ("stop", "alpha")]


async def test_unknown_project_mutation_is_json_404(client: TestClient) -> None:
    response = await client.post("/api/v1/projects/missing/start", json={})

    assert response.status == 404
    assert await response.json() == {
        "error": {
            "code": "project_not_found",
            "message": "unknown project missing",
        }
    }


async def test_add_project_uses_setup_boundary(registry: _Registry) -> None:
    calls = []

    def create_project(target_registry, values):
        calls.append((target_registry, dict(values)))
        project = Project(
            id="gamma",
            name=values["name"],
            git_repo=values["path"],
            workflow=str(Path(values["path"]) / values.get("workflow", "WORKFLOW.md")),
            host="127.0.0.1",
            port=9103,
        )
        target_registry.records.append(project)
        return project

    test_client = TestClient(
        TestServer(build_hub_app(registry, create_project=create_project))
    )
    await test_client.start_server()
    try:
        response = await test_client.post(
            "/api/v1/projects",
            json={"name": "Gamma", "path": "/work/gamma", "id": "gamma"},
        )
        body = await response.json()
    finally:
        await test_client.close()

    assert response.status == 201
    assert body["project"]["id"] == "gamma"
    assert calls == [
        (
            registry,
            {"name": "Gamma", "path": "/work/gamma", "id": "gamma"},
        )
    ]


async def test_add_project_validates_payload_before_setup(
    client: TestClient,
) -> None:
    response = await client.post("/api/v1/projects", json={"name": "Missing path"})
    assert response.status == 400
    assert (await response.json())["error"]["code"] == "invalid_request"


async def test_open_starts_only_selected_stopped_project(
    client: TestClient, registry: _Registry
) -> None:
    response = await client.post("/api/v1/projects/beta/open", json={})

    assert response.status == 200
    assert await response.json() == {
        "project_id": "beta",
        "running": True,
        "url": "http://127.0.0.1:9102/",
    }
    assert registry.calls == [("start", "beta")]
    assert registry.running == {"alpha", "beta"}

    response = await client.post("/api/v1/projects/alpha/open", json={})
    assert response.status == 200
    assert registry.calls == [("start", "beta")]


async def test_open_start_failure_returns_actionable_guidance(
    client: TestClient, registry: _Registry
) -> None:
    registry.start = lambda _project_id: 1  # type: ignore[method-assign]

    response = await client.post("/api/v1/projects/beta/open", json={})
    payload = await response.json()

    assert response.status == 409
    assert payload["error"]["code"] == "project_open_failed"
    assert "symphony service status" in payload["error"]["message"]
    assert "service command exited" not in payload["error"]["message"]


async def test_listing_degrades_status_and_board_diagnostics(
    client: TestClient, registry: _Registry
) -> None:
    original_status = registry.status

    def status(project_id: str) -> str:
        if project_id == "beta":
            raise RuntimeError("probe failed")
        return original_status(project_id)

    registry.status = status  # type: ignore[method-assign]
    Path(registry.records[0].workflow).unlink()
    response = await client.get("/api/v1/projects")
    projects = (await response.json())["projects"]

    assert response.status == 200
    assert projects[0]["board"] is None
    assert projects[0]["diagnostics"][0].startswith("Board path unavailable:")
    assert projects[1]["running"] is False
    assert projects[1]["diagnostics"] == ["Status unavailable: probe failed"]


async def test_hub_rejects_cross_origin_mutation(client: TestClient) -> None:
    response = await client.post(
        "/api/v1/projects/beta/open",
        json={},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status == 403
    assert (await response.json())["error"]["code"] == "forbidden_origin"


async def test_run_hub_reports_ephemeral_port(registry: _Registry) -> None:
    runner, port = await run_hub(build_hub_app(registry), port=0)
    try:
        assert port > 0
    finally:
        await runner.cleanup()


def test_main_parses_host_and_port(monkeypatch) -> None:
    import symphony.hub as hub

    captured = {}

    async def fake_serve(registry, host: str, port: int) -> int:
        captured.update(registry=registry, host=host, port=port)
        return 9

    monkeypatch.setattr(hub, "_serve", fake_serve)
    assert hub.main(["--host", "localhost", "--port", "8123"]) == 9
    assert isinstance(captured["registry"], ProjectRegistry)
    assert (captured["host"], captured["port"]) == ("localhost", 8123)


async def test_hub_rejects_non_loopback_host_header(client: TestClient) -> None:
    response = await client.get("/api/v1/projects", headers={"Host": "evil.example"})
    assert response.status == 403
    assert (await response.json())["error"]["code"] == "forbidden_host"


async def test_hub_mutations_require_json(client: TestClient) -> None:
    response = await client.post(
        "/api/v1/projects/beta/start",
        data="",
        headers={"Content-Type": "text/plain"},
    )
    assert response.status == 415
    assert (await response.json())["error"]["code"] == "unsupported_media_type"
