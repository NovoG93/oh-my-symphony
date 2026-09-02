"""Bearer-token authentication for the MCP gateway."""

from __future__ import annotations

import hmac

from starlette.responses import JSONResponse

from .errors import AuthenticationError


def verify_token(provided: str | None, expected: str | None) -> None:
    """Constant-time bearer-token check. Raises AuthenticationError on failure."""
    if not expected:
        raise AuthenticationError("MCP server has no token configured")
    if not provided:
        raise AuthenticationError("missing bearer token")
    token = provided
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    # Compare bytes so malformed/non-ASCII header values fail closed instead
    # of making ``compare_digest`` raise a TypeError and leaking a 500.
    if not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        raise AuthenticationError("invalid bearer token")


class BearerAuthMiddleware:
    """ASGI middleware enforcing a bearer token on every ``/mcp`` request.

    ``/health`` and any non-``/mcp`` route are left unauthenticated.
    """

    def __init__(self, app, token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path.startswith("/mcp"):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            try:
                verify_token(headers.get("authorization"), self.token)
            except AuthenticationError as exc:
                resp = JSONResponse(
                    {"error": {"code": exc.code, "message": exc.message}},
                    status_code=exc.http_status,
                )
                await resp(scope, receive, send)
                return
        await self.app(scope, receive, send)
