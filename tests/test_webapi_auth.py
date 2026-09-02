"""Authorization policy gates for the board web API."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, cast

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import symphony.server as server_mod
from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.workflow import WorkflowState
from symphony.webapi import (
    API_TOKEN_ENV,
    API_TOKEN_FILE_ENV,
    _request_has_valid_bearer,
    _request_is_loopback,
)
from symphony.web_policy import (
    AUTH_MODE_ENV,
    BIND_HOST_KEY,
    CAPABILITIES_ENV,
    TRUSTED_ORIGINS_ENV,
    ROUTE_POLICIES_KEY,
    validate_route_policies,
)

# The chat WS handshake builds its `hello` frame from the workflow config,
# so even auth-only tests need a loadable WORKFLOW.md behind the stub.
AUTH_WORKFLOW_TEXT = """---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, Doing]
  terminal_states: [Done, Archive]

agent:
  kind: claude
---
body
"""
SERVICE_CAPABILITY = "a" * 43


class _StubOrchestrator:
    """Just enough orchestrator surface for the routes these tests hit."""

    def __init__(
        self,
        workflow_state: WorkflowState,
        *,
        service_instance_id: str | None = None,
    ) -> None:
        self._workflow_state = workflow_state
        self._service_instance_id = service_instance_id

    @property
    def workflow_state(self) -> WorkflowState:
        return self._workflow_state

    @property
    def service_instance_id(self) -> str | None:
        return self._service_instance_id

    def snapshot(self) -> dict[str, Any]:
        return {"lanes": [], "running": [], "version": "test"}

    def request_refresh(self) -> bool:
        return False

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "service_instance_id": self._service_instance_id,
        }


async def _start_client(
    tmp_path: Path, *, service_instance_id: str | None, bind_host: str | None = None
) -> TestClient:
    # build_app types its parameter as Orchestrator; at runtime it only
    # uses the method protocol we mirror in `_StubOrchestrator`.
    (tmp_path / "WORKFLOW.md").write_text(AUTH_WORKFLOW_TEXT, encoding="utf-8")
    (tmp_path / "kanban").mkdir()
    state = WorkflowState(tmp_path / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    app = build_app(
        cast(
            Orchestrator,
            _StubOrchestrator(state, service_instance_id=service_instance_id),
        )
    )
    if bind_host is not None:
        app[BIND_HOST_KEY] = bind_host
    server = TestServer(app)
    cli = TestClient(server)
    await cli.start_server()
    return cli


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[TestClient]:
    cli = await _start_client(tmp_path, service_instance_id=SERVICE_CAPABILITY)
    try:
        yield cli
    finally:
        await cli.close()


# ---------------------------------------------------------------------------
# SYMPHONY_API_TOKEN bearer gate
# ---------------------------------------------------------------------------


async def test_token_unset_allows_requests_without_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Backward compat: no env var, no Authorization header — loopback
    # default stays frictionless.
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    resp = await client.get("/api/v1/state")
    assert resp.status == 200
    assert (await resp.json())["version"] == "test"


async def test_token_blank_behaves_as_unset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "   ")
    resp = await client.get("/api/v1/state")
    assert resp.status == 200


async def test_token_file_fallback_gates_requests(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    token_file = tmp_path / "api-token"
    token_file.write_text("file-secret\n")
    monkeypatch.setenv(API_TOKEN_FILE_ENV, str(token_file))
    assert (await client.get("/api/v1/state")).status == 401
    response = await client.get(
        "/api/v1/state", headers={"Authorization": "Bearer file-secret"}
    )
    assert response.status == 200


async def test_token_set_gates_get_requests(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")

    no_header = await client.get("/api/v1/state")
    assert no_header.status == 401
    assert (await no_header.json())["error"]["code"] == "unauthorized"

    wrong = await client.get(
        "/api/v1/state", headers={"Authorization": "Bearer wrong-token"}
    )
    assert wrong.status == 401
    assert (await wrong.json())["error"]["code"] == "unauthorized"

    right = await client.get(
        "/api/v1/state", headers={"Authorization": "Bearer sekrit-token"}
    )
    assert right.status == 200


async def test_token_set_gates_mutation_routes_too(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")

    blocked = await client.post("/api/v1/refresh", json={})
    assert blocked.status == 401

    allowed = await client.post(
        "/api/v1/refresh", json={}, headers={"Authorization": "Bearer sekrit-token"}
    )
    assert allowed.status == 202


async def test_health_is_public_and_never_returns_service_probe_credential(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    header = "X-Symphony-Service-Instance"

    missing = await client.get("/api/v1/health")
    assert missing.status == 200
    assert "service_instance_id" not in await missing.json()

    wrong = await client.get("/api/v1/health", headers={header: "b" * 43})
    assert wrong.status == 200

    health = await client.get(
        "/api/v1/health", headers={header: SERVICE_CAPABILITY}
    )
    assert health.status == 200
    assert "service_instance_id" not in await health.json()

    other_route = await client.get(
        "/api/v1/state", headers={header: SERVICE_CAPABILITY}
    )
    assert other_route.status == 401


async def test_health_never_returns_short_foreground_instance_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    short_id_client = await _start_client(
        tmp_path, service_instance_id="instance-a"
    )
    try:
        response = await short_id_client.get(
            "/api/v1/health",
            headers={"X-Symphony-Service-Instance": "instance-a"},
        )
        payload = await response.json()
    finally:
        await short_id_client.close()

    assert response.status == 200
    assert "service_instance_id" not in payload


async def test_token_not_required_outside_api_paths(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Static assets / landing page must stay reachable without the token.
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    resp = await client.get("/")
    assert resp.status in {200, 503}  # SPA present or assets-missing hint
    assert resp.status != 401


def test_request_has_valid_bearer_requires_exact_match() -> None:
    def req(header: str) -> Any:
        return SimpleNamespace(headers={"Authorization": header})

    token = "tok-123"
    assert _request_has_valid_bearer(req("Bearer tok-123"), token)
    # Scheme is case-insensitive RFC 7235; token must match exactly.
    assert _request_has_valid_bearer(req("bearer tok-123"), token)
    assert not _request_has_valid_bearer(req("Bearer tok-12"), token)
    assert not _request_has_valid_bearer(req("Bearer tok-123 extra"), token)
    assert not _request_has_valid_bearer(req("Basic tok-123"), token)
    assert not _request_has_valid_bearer(req(""), token)
    # Non-ASCII input must compare (and fail) instead of raising.
    assert not _request_has_valid_bearer(req("Bearer passwörd"), token)


async def test_policy_discovery_v2_never_exposes_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(AUTH_MODE_ENV, "token")
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    anonymous = await client.get("/api/v1/auth/policy")
    assert anonymous.status == 200
    payload = await anonymous.json()
    assert payload["version"] == 2
    assert payload["mode"] == "token"
    assert payload["token_configured"] is True
    assert payload["authenticated"] is False
    assert payload["effective_grants"] == []
    assert "sekrit-token" not in json.dumps(payload)

    authenticated = await client.get(
        "/api/v1/auth/policy",
        headers={"Authorization": "Bearer sekrit-token"},
    )
    assert (await authenticated.json())["authenticated"] is True


async def test_capabilities_mode_ignores_bearer_and_denies_missing_group(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(AUTH_MODE_ENV, "capabilities")
    monkeypatch.setenv(API_TOKEN_ENV, "ignored-token")
    monkeypatch.setenv(CAPABILITIES_ENV, "board")
    assert (await client.get("/api/v1/board")).status == 200
    denied = await client.get(
        "/api/v1/state", headers={"Authorization": "Bearer ignored-token"}
    )
    assert denied.status == 403
    assert (await denied.json())["error"]["code"] == "missing_capability"


def test_every_board_api_route_has_policy_metadata(tmp_path: Path) -> None:
    (tmp_path / "WORKFLOW.md").write_text(AUTH_WORKFLOW_TEXT, encoding="utf-8")
    (tmp_path / "kanban").mkdir()
    state = WorkflowState(tmp_path / "WORKFLOW.md")
    assert state.reload()[1] is None
    app = build_app(cast(Orchestrator, _StubOrchestrator(state)))
    validate_route_policies(app)
    api_routes = [
        route for route in app.router.routes()
        if route.resource and route.resource.canonical.startswith("/api/")
    ]
    assert len(app[ROUTE_POLICIES_KEY]) == len(api_routes)


async def test_non_loopback_requires_exact_host_and_trusted_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = await _start_client(
        tmp_path, service_instance_id=SERVICE_CAPABILITY, bind_host="0.0.0.0"
    )
    monkeypatch.setenv(AUTH_MODE_ENV, "capabilities")
    monkeypatch.setenv(CAPABILITIES_ENV, "board")
    monkeypatch.setenv(TRUSTED_ORIGINS_ENV, "https://ops.example:9443")
    try:
        assert (await client.get("/api/v1/board")).status == 403
        allowed_headers = {"Host": "ops.example:9443"}
        assert (await client.get("/api/v1/board", headers=allowed_headers)).status == 200
        rejected = await client.post(
            "/api/v1/issues",
            json={},
            headers={**allowed_headers, "Origin": "http://ops.example:9443"},
        )
        assert rejected.status == 403
        assert (await rejected.json())["error"]["code"] == "forbidden_origin"
    finally:
        await client.close()


async def test_websocket_ticket_observes_capability_revocation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(AUTH_MODE_ENV, "capabilities")
    monkeypatch.setenv(CAPABILITIES_ENV, "chat")
    issued = await client.post("/api/v1/chat/ws-ticket", json={})
    ticket = (await issued.json())["ticket"]
    monkeypatch.setenv(CAPABILITIES_ENV, "")
    with pytest.raises(aiohttp.WSServerHandshakeError) as revoked:
        await client.ws_connect(f"/api/v1/chat/ws?ticket={ticket}")
    assert revoked.value.status == 401


async def test_chat_mutation_requires_chat_and_board(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(AUTH_MODE_ENV, "capabilities")
    monkeypatch.setenv(CAPABILITIES_ENV, "chat")
    assert (await client.get("/api/v1/chat/sessions")).status == 200
    denied = await client.post("/api/v1/chat/sessions", json={})
    assert denied.status == 403
    monkeypatch.setenv(CAPABILITIES_ENV, "chat,board")
    allowed = await client.post("/api/v1/chat/sessions", json={})
    assert allowed.status != 403


# ---------------------------------------------------------------------------
# SYMPHONY_API_TOKEN × chat WebSocket handshake
# ---------------------------------------------------------------------------


async def test_token_ws_handshake_uses_single_use_ticket(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The long-lived bearer is exchanged for a short-lived URL ticket."""
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    response = await client.post(
        "/api/v1/chat/ws-ticket",
        json={},
        headers={"Authorization": "Bearer sekrit-token"},
    )
    assert response.status == 200
    ticket = (await response.json())["ticket"]
    ws = await client.ws_connect(f"/api/v1/chat/ws?ticket={ticket}")
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=5)
        assert hello["type"] == "hello"
    finally:
        await ws.close()
    with pytest.raises(aiohttp.WSServerHandshakeError) as reused:
        await client.ws_connect(f"/api/v1/chat/ws?ticket={ticket}")
    assert reused.value.status == 401


async def test_token_ws_handshake_rejects_long_lived_bearer_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    with pytest.raises(aiohttp.WSServerHandshakeError) as rejected:
        await client.ws_connect(
            "/api/v1/chat/ws", headers={"Authorization": "Bearer sekrit-token"}
        )
    assert rejected.value.status == 401


async def test_token_ws_handshake_rejects_missing_or_wrong_ticket(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    with pytest.raises(aiohttp.WSServerHandshakeError) as missing:
        await client.ws_connect("/api/v1/chat/ws")
    assert missing.value.status == 401

    with pytest.raises(aiohttp.WSServerHandshakeError) as wrong:
        await client.ws_connect("/api/v1/chat/ws?ticket=not-a-ticket")
    assert wrong.value.status == 401


async def test_token_query_param_rejected_on_plain_routes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Long-lived tokens are never accepted in query strings."""
    monkeypatch.setenv(API_TOKEN_ENV, "sekrit-token")
    resp = await client.get("/api/v1/state", params={"token": "sekrit-token"})
    assert resp.status == 401
    assert (await resp.json())["error"]["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# /api/v1/_debug/tasks loopback gate
# ---------------------------------------------------------------------------


async def test_debug_tasks_allowed_from_loopback_peer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CAPABILITIES_ENV, "debug")
    # TestClient connects over 127.0.0.1 — a real loopback peer.
    resp = await client.get("/api/v1/_debug/tasks")
    assert resp.status == 200
    payload = await resp.json()
    assert isinstance(payload["tasks"], list)


async def test_debug_tasks_rejects_non_loopback_peer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CAPABILITIES_ENV, "debug")
    # Simulate a request whose remote peer is off-machine (e.g. served
    # behind a LAN bind) by forcing the shared loopback predicate off.
    monkeypatch.setattr(server_mod, "_request_is_loopback", lambda _request: False)
    resp = await client.get("/api/v1/_debug/tasks")
    assert resp.status == 403
    payload = await resp.json()
    assert payload["error"]["code"] == "debug_tasks_local_only"


def test_shared_loopback_predicate_rejects_non_loopback_remote() -> None:
    def req(remote: str) -> Any:
        return SimpleNamespace(remote=remote, app={})

    assert _request_is_loopback(req("127.0.0.1"))
    assert not _request_is_loopback(req("203.0.113.9"))


# ---------------------------------------------------------------------------
# 500 handler must not echo exception text
# ---------------------------------------------------------------------------


async def test_unhandled_error_returns_generic_message() -> None:
    from symphony.webapi import _wrap

    async def boom(_request: web.Request) -> web.Response:
        raise RuntimeError("SECRET internal detail: C:\\operator\\board.sqlite")

    app = web.Application()
    app.router.add_get("/api/v1/boom", _wrap(boom))
    server = TestServer(app)
    cli = TestClient(server)
    await cli.start_server()
    try:
        resp = await cli.get("/api/v1/boom")
        assert resp.status == 500
        payload = await resp.json()
        assert payload["error"]["code"] == "internal_error"
        assert payload["error"]["message"] == "internal server error"
        assert "SECRET" not in json.dumps(payload)
    finally:
        await cli.close()
