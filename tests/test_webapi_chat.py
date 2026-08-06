"""REST + WebSocket contract for the operator chat (`/api/v1/chat/*`)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, cast

import aiohttp
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from symphony import chat as chat_module
from symphony.backends import (
    EVENT_OTHER_MESSAGE,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_STARTED,
    BackendInit,
    TurnResult,
)
from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.workflow import WorkflowState

WORKFLOW_TEXT = """---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, Doing]
  terminal_states: [Done, Archive]

agent:
  kind: claude
---

You are working on {{ issue.identifier }}.
"""


class _StubOrchestrator:
    """Chat routes never touch the orchestrator; handlers here are lazy."""

    def __init__(self, workflow_state: WorkflowState) -> None:
        self._workflow_state = workflow_state

    @property
    def workflow_state(self) -> WorkflowState:
        return self._workflow_state

    def snapshot(self) -> dict[str, Any]:
        return {
            "generated_at": "2026-08-06T00:00:00Z",
            "counts": {"running": 0, "retrying": 0},
            "running": [],
            "retrying": [],
            "codex_totals": {},
            "rate_limits": None,
        }

    def find_running_issue_id(self, _identifier: str) -> str | None:
        return None

    def request_refresh(self) -> bool:
        return False


class _FakeBackend:
    def __init__(self, init: BackendInit) -> None:
        self.init = init
        self.turns: list[str] = []
        self.stopped = False
        self.gate: asyncio.Event | None = None

    async def start(self) -> None:
        pass

    async def initialize(self) -> dict[str, Any]:
        return {}

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
        return "pending"

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> TurnResult:
        self.turns.append(prompt)
        if self.gate is not None:
            await self.gate.wait()
        await self._emit(EVENT_TURN_STARTED, {})
        await self._emit(
            EVENT_OTHER_MESSAGE,
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "thinking"}]},
            },
        )
        await self._emit(EVENT_TURN_COMPLETED, {"message": "the answer"})
        return TurnResult(
            status=EVENT_TURN_COMPLETED, turn_id="t", last_message="the answer"
        )

    async def stop(self) -> None:
        self.stopped = True

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        await self.init.on_event(
            {
                "event": event,
                "timestamp": "2026-08-06T00:00:00Z",
                "payload": payload,
                "usage": {},
                "rate_limits": None,
                "agent_pid": 123,
            }
        )

    @property
    def session_id(self) -> str | None:
        return "sess-1"

    @property
    def pid(self) -> int | None:
        return 123

    @property
    def latest_usage(self) -> dict[str, int]:
        return {}

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None:
        return None

    def is_progress_event(self, _event: dict[str, Any]) -> bool:
        return True


@pytest.fixture()
def fake_backends(monkeypatch: pytest.MonkeyPatch) -> list[_FakeBackend]:
    built: list[_FakeBackend] = []

    def _build(init: BackendInit) -> _FakeBackend:
        backend = _FakeBackend(init)
        built.append(backend)
        return backend

    monkeypatch.setattr(chat_module, "build_backend", _build)
    return built


@pytest.fixture()
def board_dir(tmp_path: Path) -> Path:
    (tmp_path / "WORKFLOW.md").write_text(WORKFLOW_TEXT, encoding="utf-8")
    (tmp_path / "kanban").mkdir()
    return tmp_path


@pytest_asyncio.fixture()
async def client(
    board_dir: Path, fake_backends: list[_FakeBackend]
) -> AsyncIterator[TestClient]:
    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    app = build_app(cast(Orchestrator, _StubOrchestrator(state)))
    cli = TestClient(TestServer(app))
    await cli.start_server()
    try:
        yield cli
    finally:
        await cli.close()


async def _receive_types_until(
    ws: Any, terminal: str, limit: int = 20
) -> list[str]:
    types: list[str] = []
    for _ in range(limit):
        frame = await asyncio.wait_for(ws.receive_json(), timeout=5)
        types.append(frame["type"])
        if frame["type"] == terminal:
            break
    return types


async def test_chat_session_crud(client: TestClient) -> None:
    resp = await client.get("/api/v1/chat/session")
    assert resp.status == 200
    assert (await resp.json())["active"] is False

    resp = await client.post("/api/v1/chat/session", json={"mode": "qa"})
    assert resp.status == 201
    payload = await resp.json()
    assert payload["active"] is True
    assert payload["mode"] == "qa"
    assert payload["agent_kind"] == "claude"

    resp = await client.post("/api/v1/chat/session", json={"mode": "qa"})
    assert resp.status == 409
    assert (await resp.json())["error"]["code"] == "chat_session_exists"

    resp = await client.patch("/api/v1/chat/session", json={"mode": "edit"})
    assert resp.status == 200
    patched = await resp.json()
    assert patched["mode"] == "edit"
    assert patched["context_preserved"] is True  # claude resumes

    resp = await client.delete("/api/v1/chat/session")
    assert resp.status == 200
    resp = await client.delete("/api/v1/chat/session")
    assert resp.status == 404
    resp = await client.patch("/api/v1/chat/session", json={"mode": "qa"})
    assert resp.status == 404

    resp = await client.post("/api/v1/chat/session", json={"mode": "nope"})
    assert resp.status == 400


async def test_chat_message_validation_and_busy(
    client: TestClient, fake_backends: list[_FakeBackend]
) -> None:
    resp = await client.post("/api/v1/chat/message", json={"text": "hi"})
    assert resp.status == 404

    resp = await client.post("/api/v1/chat/session", json={"mode": "qa"})
    assert resp.status == 201
    backend = fake_backends[0]
    backend.gate = asyncio.Event()

    resp = await client.post("/api/v1/chat/message", json={"text": ""})
    assert resp.status == 400

    resp = await client.post("/api/v1/chat/message", json={"text": "slow"})
    assert resp.status == 202

    resp = await client.post("/api/v1/chat/message", json={"text": "too soon"})
    assert resp.status == 409
    assert (await resp.json())["error"]["code"] == "chat_busy"

    backend.gate.set()

    # Non-JSON mutations are rejected by the API guard.
    resp = await client.post(
        "/api/v1/chat/message",
        data="text=hi",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status == 415


async def test_chat_ws_streams_turn_events(client: TestClient) -> None:
    ws = await client.ws_connect("/api/v1/chat/ws")
    hello = await asyncio.wait_for(ws.receive_json(), timeout=5)
    assert hello["type"] == "hello"
    assert hello["snapshot"]["active"] is False

    resp = await client.post("/api/v1/chat/session", json={"mode": "qa"})
    assert resp.status == 201
    frame = await asyncio.wait_for(ws.receive_json(), timeout=5)
    assert frame["type"] == "session_status"

    resp = await client.post("/api/v1/chat/message", json={"text": "explain"})
    assert resp.status == 202
    types = await _receive_types_until(ws, "turn_completed")
    assert types[0] == "user_message"
    assert "turn_started" in types
    assert "agent_message" in types
    assert types[-1] == "turn_completed"
    await ws.close()


async def test_chat_ws_rejects_cross_origin(client: TestClient) -> None:
    with pytest.raises(aiohttp.WSServerHandshakeError):
        await client.ws_connect(
            "/api/v1/chat/ws", headers={"Origin": "http://evil.example"}
        )


async def test_shutdown_stops_chat_backend(
    board_dir: Path, fake_backends: list[_FakeBackend]
) -> None:
    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    app = build_app(cast(Orchestrator, _StubOrchestrator(state)))
    cli = TestClient(TestServer(app))
    await cli.start_server()
    resp = await cli.post("/api/v1/chat/session", json={"mode": "qa"})
    assert resp.status == 201
    ws = await cli.ws_connect("/api/v1/chat/ws")
    await asyncio.wait_for(ws.receive_json(), timeout=5)  # hello

    await cli.close()  # fires app.on_shutdown

    assert fake_backends[0].stopped is True
