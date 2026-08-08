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
async def registry() -> _Registry:
    return _Registry(
        [
            Project(
                id="alpha",
                name="Alpha board",
                git_repo=str(Path("/work/alpha")),
                workflow=str(Path("/work/alpha/WORKFLOW.md")),
                host="127.0.0.1",
                port=9101,
            ),
            Project(
                id="beta",
                name="Beta board",
                git_repo=str(Path("/work/beta")),
                workflow=str(Path("/work/beta/WORKFLOW.md")),
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
    client: TestClient,
) -> None:
    response = await client.get("/api/v1/projects")
    body = await response.json()

    assert response.status == 200
    assert body == {
        "projects": [
            {
                "id": "alpha",
                "name": "Alpha board",
                "repo": "/work/alpha",
                "workflow": "/work/alpha/WORKFLOW.md",
                "host": "127.0.0.1",
                "port": 9101,
                "running": True,
                "url": "http://127.0.0.1:9101/",
            },
            {
                "id": "beta",
                "name": "Beta board",
                "repo": "/work/beta",
                "workflow": "/work/beta/WORKFLOW.md",
                "host": "0.0.0.0",
                "port": 9102,
                "running": False,
                "url": None,
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
