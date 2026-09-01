"""Regression tests for remote operator authorization boundaries."""

from __future__ import annotations

import pytest
from aiohttp import web

from symphony.service_identity import SERVICE_INSTANCE_HEADER
from symphony.webapi import (
    BIND_HOST_KEY,
    SERVICE_INSTANCE_ID_KEY,
    REMOTE_CAPABILITIES_ENV,
    _api_guard,
    _api_auth_mode,
    _capability_state,
    _host_is_declared_trusted,
    _host_is_declared_trusted_ordinary,
    _privileged_authorized,
    _local_operator,
    _remote_capabilities,
)
from symphony.cli.doctor import check_api_token_env


class _Request:
    def __init__(self, *, remote: str, host: str, headers: dict[str, str] | None = None, path: str = "/api/v1/runs") -> None:
        self.remote = remote
        self.host = host
        self.path = path
        self.method = "GET"
        self.body_exists = False
        self.content_type = "application/json"
        self.headers = headers or {}
        self.app = {BIND_HOST_KEY: "0.0.0.0"}


def test_capability_parser_allowlists_known_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REMOTE_CAPABILITIES_ENV, "runs, PREVIEW;unknown,projects")
    assert _remote_capabilities() == {"runs", "preview", "projects"}


def test_auth_mode_defaults_global_and_rejects_unknown_at_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYMPHONY_API_AUTH_MODE", raising=False)
    assert _api_auth_mode() == "global"
    monkeypatch.setenv("SYMPHONY_API_AUTH_MODE", "bogus")
    assert _api_auth_mode() == "global"


@pytest.mark.asyncio
async def test_operator_mode_leaves_ordinary_remote_api_passwordless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYMPHONY_API_AUTH_MODE", "operator")
    monkeypatch.setenv("SYMPHONY_API_TOKEN", "secret")
    request = _Request(remote="192.0.2.10", host="other.example", path="/api/v1/state")

    async def handler(_request: _Request) -> web.Response:
        return web.json_response({"ok": True})

    response = await _api_guard(request, handler)
    assert response.status == 200


def test_loopback_bypasses_remote_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYMPHONY_API_TOKEN", raising=False)
    monkeypatch.delenv("SYMPHONY_TRUSTED_ORIGINS", raising=False)
    request = _Request(remote="127.0.0.1", host="127.0.0.1")
    assert _privileged_authorized(request, "runs")


def test_local_operator_stays_passwordless_with_configured_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYMPHONY_API_TOKEN", "secret")
    monkeypatch.delenv(REMOTE_CAPABILITIES_ENV, raising=False)
    request = _Request(remote="127.0.0.1", host="127.0.0.1")
    assert _privileged_authorized(request, "runs")


def test_loopback_peer_with_public_host_is_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYMPHONY_API_TOKEN", raising=False)
    monkeypatch.setenv("SYMPHONY_TRUSTED_ORIGINS", "https://public.example")
    request = _Request(remote="127.0.0.1", host="public.example")
    assert not _privileged_authorized(request, "runs")


@pytest.mark.parametrize("header", ["Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Real-IP"])
def test_proxy_evidence_disables_loopback_shortcut(
    monkeypatch: pytest.MonkeyPatch, header: str
) -> None:
    request = _Request(
        remote="127.0.0.1", host="127.0.0.1", headers={header: "public.example"},
    )
    assert not _local_operator(request)


@pytest.mark.parametrize(
    "token,host,capability,expected",
    [
        ("secret", "symphony.example", "runs", True),
        ("secret", "symphony.example:444", "runs", False),
        ("wrong", "symphony.example", "runs", False),
        ("secret", "other.example", "runs", False),
        ("secret", "symphony.example", "preview", False),
    ],
)
def test_remote_requires_token_exact_host_and_matching_capability(
    monkeypatch: pytest.MonkeyPatch, token: str, host: str, capability: str, expected: bool
) -> None:
    monkeypatch.setenv("SYMPHONY_API_TOKEN", "secret")
    monkeypatch.setenv("SYMPHONY_TRUSTED_ORIGINS", "https://symphony.example")
    monkeypatch.setenv(REMOTE_CAPABILITIES_ENV, "runs")
    request = _Request(remote="192.0.2.10", host=host, headers={"Authorization": f"Bearer {token}"})
    assert _privileged_authorized(request, capability) is expected


def test_explicit_trusted_port_matches_only_same_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_API_TOKEN", "secret")
    monkeypatch.setenv("SYMPHONY_TRUSTED_ORIGINS", "http://symphony.example:9999")
    monkeypatch.setenv(REMOTE_CAPABILITIES_ENV, "runs")
    good = _Request(remote="192.0.2.10", host="symphony.example:9999", headers={"Authorization": "Bearer secret"})
    bad = _Request(remote="192.0.2.10", host="symphony.example:9998", headers={"Authorization": "Bearer secret"})
    assert _privileged_authorized(good, "runs")
    assert not _privileged_authorized(bad, "runs")


def test_token_file_authenticates_remote_request(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    token_file = tmp_path / "api-token"
    token_file.write_text("file-secret\n")
    monkeypatch.delenv("SYMPHONY_API_TOKEN", raising=False)
    monkeypatch.setenv("SYMPHONY_API_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("SYMPHONY_TRUSTED_ORIGINS", "https://symphony.example")
    monkeypatch.setenv(REMOTE_CAPABILITIES_ENV, "runs")
    request = _Request(remote="192.0.2.10", host="symphony.example", headers={"Authorization": "Bearer file-secret"})
    assert _privileged_authorized(request, "runs")


def test_wildcard_never_trusts_privileged_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_TRUSTED_ORIGINS", "*")
    assert not _host_is_declared_trusted("symphony.example")
    assert _host_is_declared_trusted_ordinary("symphony.example")


def test_bare_origin_ordinary_host_allows_any_port_but_privileged_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYMPHONY_TRUSTED_ORIGINS", "symphony.example")
    assert _host_is_declared_trusted_ordinary("symphony.example:9999")
    assert not _host_is_declared_trusted("symphony.example:9999")


def test_doctor_detects_empty_token_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("\n")
    monkeypatch.setenv("SYMPHONY_API_TOKEN_FILE", str(token_file))
    monkeypatch.setenv(REMOTE_CAPABILITIES_ENV, "runs")
    result = check_api_token_env(None)  # type: ignore[arg-type]
    assert result.status == "fail"
    assert "empty" in result.message


def test_doctor_blocks_capabilities_without_token_or_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_API_AUTH_MODE", "operator")
    monkeypatch.setenv(REMOTE_CAPABILITIES_ENV, "runs")
    monkeypatch.delenv("SYMPHONY_API_TOKEN", raising=False)
    monkeypatch.delenv("SYMPHONY_API_TOKEN_FILE", raising=False)
    monkeypatch.delenv("SYMPHONY_TRUSTED_ORIGINS", raising=False)
    assert check_api_token_env(None).status == "fail"  # type: ignore[arg-type]


def test_doctor_blocks_whitespace_token_without_usable_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_API_AUTH_MODE", "operator")
    monkeypatch.setenv(REMOTE_CAPABILITIES_ENV, "runs")
    monkeypatch.setenv("SYMPHONY_API_TOKEN", "   ")
    monkeypatch.delenv("SYMPHONY_API_TOKEN_FILE", raising=False)
    monkeypatch.setenv("SYMPHONY_TRUSTED_ORIGINS", "https://symphony.example")
    assert check_api_token_env(None).status == "fail"  # type: ignore[arg-type]


def test_doctor_rejects_unknown_auth_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_API_AUTH_MODE", "sometimes")
    assert check_api_token_env(None).status == "fail"  # type: ignore[arg-type]


def test_doctor_global_mode_still_validates_configured_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYMPHONY_API_AUTH_MODE", raising=False)
    monkeypatch.setenv(REMOTE_CAPABILITIES_ENV, "runs")
    monkeypatch.setenv("SYMPHONY_API_TOKEN", "secret")
    monkeypatch.delenv("SYMPHONY_TRUSTED_ORIGINS", raising=False)
    assert check_api_token_env(None).status == "fail"  # type: ignore[arg-type]
    monkeypatch.delenv("SYMPHONY_API_TOKEN", raising=False)
    monkeypatch.setenv("SYMPHONY_TRUSTED_ORIGINS", "https://symphony.example")
    assert check_api_token_env(None).status == "fail"  # type: ignore[arg-type]


def test_capability_state_has_safe_denial_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_API_TOKEN", "secret")
    monkeypatch.setenv("SYMPHONY_TRUSTED_ORIGINS", "https://symphony.example")
    monkeypatch.setenv(REMOTE_CAPABILITIES_ENV, "runs,debug,unknown")
    state = _capability_state(_Request(remote="192.0.2.10", host="other.example"))
    assert state["denial_reason"] == "missing or invalid bearer token"
    assert state["capabilities"] == {
        "debug": False,
        "preview": False,
        "projects": False,
        "runs": False,
    }
    assert "secret" not in repr(state)


def test_doctor_rejects_unknown_or_wildcard_remote_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REMOTE_CAPABILITIES_ENV, "runs,wat")
    assert check_api_token_env(None).status == "fail"  # type: ignore[arg-type]
    monkeypatch.setenv(REMOTE_CAPABILITIES_ENV, "runs")
    monkeypatch.setenv("SYMPHONY_TRUSTED_ORIGINS", "*")
    assert check_api_token_env(None).status == "fail"  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_health_probe_exception_is_scoped_to_health(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_API_TOKEN", "secret")
    launch_id = "a" * 32
    request = _Request(
        remote="192.0.2.10",
        host="untrusted.example",
        path="/api/v1/health",
        headers={SERVICE_INSTANCE_HEADER: launch_id},
    )
    request.app[SERVICE_INSTANCE_ID_KEY] = launch_id

    async def handler(_request: _Request) -> web.Response:
        return web.json_response({"ok": True})

    response = await _api_guard(request, handler)
    assert response.status == 200
    request.path = "/api/v1/state"
    response = await _api_guard(request, handler)
    assert response.status == 401
