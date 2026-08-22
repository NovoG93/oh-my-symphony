"""Auth + loopback gates for the web API (`symphony.webapi`, `symphony.server`).

Covers the optional `SYMPHONY_API_TOKEN` bearer gate on the whole `/api/`
surface, the loopback-only `/api/v1/_debug/tasks` route, and the guarantee
that unhandled server errors never echo internal exception text to clients.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, AsyncIterator, cast

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import symphony.server as server_mod
from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.webapi import (
    API_TOKEN_ENV,
    _request_has_valid_bearer,
    _request_is_loopback,
)


class _StubOrchestrator:
    """Just enough orchestrator surface for the routes these tests hit."""

    def snapshot(self) -> dict[str, Any]:
        return {"lanes": [], "running": [], "version": "test"}

    def request_refresh(self) -> bool:
        return False

    def health(self) -> dict[str, Any]:
        return {"ok": True}


@pytest_asyncio.fixture
async def client() -> AsyncIterator[TestClient]:
    # build_app types its parameter as Orchestrator; at runtime it only
    # uses the method protocol we mirror in `_StubOrchestrator`.
    app = build_app(cast(Orchestrator, _StubOrchestrator()))
    server = TestServer(app)
    cli = TestClient(server)
    await cli.start_server()
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


# ---------------------------------------------------------------------------
# /api/v1/_debug/tasks loopback gate
# ---------------------------------------------------------------------------


async def test_debug_tasks_allowed_from_loopback_peer(
    client: TestClient,
) -> None:
    # TestClient connects over 127.0.0.1 — a real loopback peer.
    resp = await client.get("/api/v1/_debug/tasks")
    assert resp.status == 200
    payload = await resp.json()
    assert isinstance(payload["tasks"], list)


async def test_debug_tasks_rejects_non_loopback_peer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
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
