"""Unit contract for the shared board/Hub HTTP authorization policy."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp import web

from symphony.web_policy import (
    ALL_CAPABILITIES,
    API_TOKEN_ENV,
    AUTH_MODE_ENV,
    CAPABILITIES_ENV,
    TRUSTED_ORIGINS_ENV,
    NORMAL_CAPABILITIES,
    PolicyConfigurationError,
    WebSocketTicketStore,
    install_route_policies,
    ROUTE_POLICIES_KEY,
    resolve_policy,
)


def _request(header: str = "") -> SimpleNamespace:
    return SimpleNamespace(headers={"Authorization": header})


def test_unset_mode_infers_disabled_only_on_loopback(monkeypatch) -> None:
    monkeypatch.delenv(AUTH_MODE_ENV, raising=False)
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    assert resolve_policy("127.0.0.1").mode == "disabled"
    with pytest.raises(PolicyConfigurationError, match="explicit"):
        resolve_policy("0.0.0.0")


def test_unset_mode_infers_token_when_secret_is_configured(monkeypatch) -> None:
    monkeypatch.delenv(AUTH_MODE_ENV, raising=False)
    monkeypatch.setenv(API_TOKEN_ENV, "secret")
    policy = resolve_policy("127.0.0.1")
    assert policy.mode == "token"
    assert policy.grants_for(_request()) == frozenset()
    assert policy.grants_for(_request("Bearer secret")) == NORMAL_CAPABILITIES


@pytest.mark.parametrize("alias", ["global", "operator"])
def test_deprecated_modes_alias_token_without_passwordless_behavior(
    monkeypatch, alias: str
) -> None:
    monkeypatch.setenv(AUTH_MODE_ENV, alias)
    monkeypatch.setenv(API_TOKEN_ENV, "secret")
    policy = resolve_policy("127.0.0.1")
    assert policy.mode == "token"
    assert policy.deprecated_alias == alias
    assert not policy.grants_for(_request())


def test_unknown_mode_and_capability_fail_closed(monkeypatch) -> None:
    monkeypatch.setenv(AUTH_MODE_ENV, "surprise")
    with pytest.raises(PolicyConfigurationError, match="unknown API auth mode"):
        resolve_policy("127.0.0.1")
    monkeypatch.setenv(AUTH_MODE_ENV, "capabilities")
    monkeypatch.setenv(CAPABILITIES_ENV, "board,shell")
    with pytest.raises(PolicyConfigurationError, match="unknown remote"):
        resolve_policy("127.0.0.1")


def test_capabilities_mode_ignores_token_and_uses_only_list(monkeypatch) -> None:
    monkeypatch.setenv(AUTH_MODE_ENV, "capabilities")
    monkeypatch.setenv(API_TOKEN_ENV, "must-not-be-honored")
    monkeypatch.setenv(CAPABILITIES_ENV, "board,projects")
    policy = resolve_policy("127.0.0.1")
    assert policy.token is None
    assert policy.token_configured is True
    assert policy.effective_grants == {"board", "projects"}
    assert policy.grants_for(_request()) == {"board", "projects"}


@pytest.mark.parametrize("mode", ["token", "disabled"])
def test_normal_capability_list_is_ignored_except_debug(monkeypatch, mode: str) -> None:
    monkeypatch.setenv(AUTH_MODE_ENV, mode)
    monkeypatch.setenv(CAPABILITIES_ENV, "board,debug")
    if mode == "token":
        monkeypatch.setenv(API_TOKEN_ENV, "secret")
    policy = resolve_policy("127.0.0.1")
    assert policy.effective_grants == ALL_CAPABILITIES


def test_debug_is_not_implicit_in_disabled_or_token(monkeypatch) -> None:
    monkeypatch.setenv(AUTH_MODE_ENV, "disabled")
    monkeypatch.delenv(CAPABILITIES_ENV, raising=False)
    assert "debug" not in resolve_policy("127.0.0.1").effective_grants
    monkeypatch.setenv(AUTH_MODE_ENV, "token")
    monkeypatch.setenv(API_TOKEN_ENV, "secret")
    assert "debug" not in resolve_policy("127.0.0.1").effective_grants


def test_non_loopback_requires_exact_origin_and_rejects_wildcards(monkeypatch) -> None:
    monkeypatch.setenv(AUTH_MODE_ENV, "capabilities")
    monkeypatch.setenv(CAPABILITIES_ENV, "projects")
    with pytest.raises(PolicyConfigurationError, match="exact"):
        resolve_policy("0.0.0.0")
    monkeypatch.setenv(TRUSTED_ORIGINS_ENV, "*")
    with pytest.raises(PolicyConfigurationError, match="wildcard"):
        resolve_policy("0.0.0.0")
    monkeypatch.setenv(TRUSTED_ORIGINS_ENV, "https://ops.example:9443")
    assert resolve_policy("0.0.0.0").trusted_origins == {
        "https://ops.example:9443"
    }


async def test_unmatched_api_path_defers_to_framework_404() -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from symphony.web_policy import add_api_route, policy_middleware

    app = web.Application(middlewares=[policy_middleware])

    async def board(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    add_api_route(app, "GET", "/api/v1/board", board, capabilities=["board"])
    install_route_policies(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/v1/not-a-route")
        assert response.status == 404
    finally:
        await client.close()


async def test_matched_route_without_policy_metadata_still_fails_closed() -> None:
    """Deferral must never bypass a real handler: an unclassified route is a
    server misconfiguration and keeps its controlled 500."""
    from aiohttp.test_utils import TestClient, TestServer

    from symphony.web_policy import policy_middleware

    app = web.Application(middlewares=[policy_middleware])

    async def rogue(_request: web.Request) -> web.Response:
        return web.json_response({"rogue": True})

    install_route_policies(app)
    # Registered outside add_api_route/install_route_policies: matched by the
    # router but without authorization metadata.
    app.router.add_get("/api/v1/rogue", rogue)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/v1/rogue")
        assert response.status == 500
        assert (await response.json())["error"]["code"] == "unclassified_route"
    finally:
        await client.close()


def test_unclassified_route_fails_registration() -> None:
    app = web.Application()

    async def handler(_request):
        return web.Response()

    app.router.add_get("/api/v1/unclassified", handler)
    with pytest.raises(PolicyConfigurationError, match="lacks authorization"):
        install_route_policies(app)


def test_intent_approval_route_is_explicit_chat_board_only() -> None:
    app = web.Application()

    async def handler(_request):
        return web.Response()

    path = "/api/v1/chat/sessions/{session_id}/intent/{action_id}/approve"
    app.router.add_post(path, handler)
    install_route_policies(app)
    policy = app[ROUTE_POLICIES_KEY][("POST", path)]
    assert policy.capabilities == frozenset({"chat", "board"})
    assert "projects" not in policy.capabilities


def test_websocket_ticket_is_origin_bound_single_use_and_expires(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr("symphony.web_policy.time.monotonic", lambda: clock[0])
    store = WebSocketTicketStore()
    ticket, ttl = store.issue(
        origin="https://ops.example", grants=frozenset({"chat"}), policy_fingerprint="v1"
    )
    assert ttl == 30
    assert not store.consume(
        ticket,
        origin="https://evil.example",
        required=frozenset({"chat"}),
        policy_fingerprint="v1",
    )
    ticket, _ = store.issue(
        origin="https://ops.example", grants=frozenset({"chat"}), policy_fingerprint="v1"
    )
    assert store.consume(
        ticket,
        origin="https://ops.example",
        required=frozenset({"chat"}),
        policy_fingerprint="v1",
    )
    assert not store.consume(
        ticket,
        origin="https://ops.example",
        required=frozenset({"chat"}),
        policy_fingerprint="v1",
    )
    ticket, _ = store.issue(
        origin="https://ops.example", grants=frozenset({"chat"}), policy_fingerprint="v1"
    )
    clock[0] += 31
    assert not store.consume(
        ticket,
        origin="https://ops.example",
        required=frozenset({"chat"}),
        policy_fingerprint="v1",
    )
