import httpx
import pytest
from starlette.responses import JSONResponse

from symphony.mcp.auth import BearerAuthMiddleware, verify_token
from symphony.mcp.errors import AuthenticationError


def test_verify_token_ok():
    verify_token("abc", "abc")
    verify_token("Bearer abc", "abc")
    verify_token("bearer abc", "abc")


def test_verify_token_rejects():
    with pytest.raises(AuthenticationError):
        verify_token("wrong", "abc")
    with pytest.raises(AuthenticationError):
        verify_token(None, "abc")
    with pytest.raises(AuthenticationError):
        verify_token("", "abc")


def test_verify_token_requires_configured_token():
    with pytest.raises(AuthenticationError):
        verify_token("abc", None)


def test_verify_token_rejects_non_ascii_without_server_error():
    with pytest.raises(AuthenticationError):
        verify_token("Bearer café", "secret")


async def _ok_app(scope, receive, send):
    assert scope["type"] == "http"
    resp = JSONResponse({"ok": True})
    await resp(scope, receive, send)


async def test_middleware_enforces_token_on_mcp_only():
    app = BearerAuthMiddleware(_ok_app, "secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/mcp")
        assert r.status_code == 401
        r = await c.get("/mcp", headers={"Authorization": "Bearer secret"})
        assert r.status_code == 200
        # /health is NOT protected
        r = await c.get("/health")
        assert r.status_code == 200
