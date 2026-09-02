"""Shared deny-by-default authorization policy for Symphony HTTP services.

The TUI does not cross this boundary.  Every HTTP API route must be registered
through :func:`add_api_route`, including public API endpoints, so adding a new
route cannot silently make it passwordless.
"""

from __future__ import annotations

import hmac
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable
from urllib.parse import urlsplit

from aiohttp import web

log = logging.getLogger(__name__)
_WARNED_ALIASES: set[str] = set()


AUTH_MODE_ENV: Final = "SYMPHONY_API_AUTH_MODE"
API_TOKEN_ENV: Final = "SYMPHONY_API_TOKEN"
API_TOKEN_FILE_ENV: Final = "SYMPHONY_API_TOKEN_FILE"
CAPABILITIES_ENV: Final = "SYMPHONY_REMOTE_OPERATOR_CAPABILITIES"
TRUSTED_ORIGINS_ENV: Final = "SYMPHONY_TRUSTED_ORIGINS"

NORMAL_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"board", "workers", "workflow", "git", "chat", "runs", "preview", "projects"}
)
ALL_CAPABILITIES: Final[frozenset[str]] = NORMAL_CAPABILITIES | {"debug"}
AUTH_MODES: Final[frozenset[str]] = frozenset({"token", "disabled", "capabilities"})
LOOPBACK_BINDS: Final[frozenset[str]] = frozenset(
    {"", "localhost", "127.0.0.1", "::1", "[::1]"}
)
LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset(
    {"localhost", "127.0.0.1", "[::1]"}
)
MUTATING_METHODS: Final[frozenset[str]] = frozenset(
    {"POST", "PUT", "PATCH", "DELETE"}
)

BIND_HOST_KEY: web.AppKey[str] = web.AppKey("symphony.web_policy.bind_host", str)
ROUTE_POLICIES_KEY: web.AppKey[dict[tuple[str, str], "RoutePolicy"]] = web.AppKey(
    "symphony.web_policy.routes", dict
)


class PolicyConfigurationError(ValueError):
    """The web security environment is unsafe or internally inconsistent."""


@dataclass(frozen=True)
class RoutePolicy:
    capabilities: frozenset[str]
    public: bool = False
    websocket_ticket: bool = False
    denial_code: str = "missing_capability"


@dataclass(frozen=True)
class EffectivePolicy:
    mode: str
    token: str | None
    token_configured: bool
    configured_grants: frozenset[str]
    effective_grants: frozenset[str]
    trusted_origins: frozenset[str]
    deprecated_alias: str | None = None

    def authenticated(self, request: web.Request) -> bool:
        return bool(
            self.mode == "token"
            and self.token is not None
            and request_has_valid_bearer(request, self.token)
        )

    def grants_for(self, request: web.Request) -> frozenset[str]:
        if self.mode == "token":
            if not self.authenticated(request):
                return frozenset()
            return self.effective_grants
        return self.effective_grants

    def fingerprint(self) -> str:
        """Opaque revision used to revoke already-issued WebSocket tickets."""
        material = "\0".join(
            (self.mode, self.token or "", ",".join(sorted(self.effective_grants)))
        )
        return hashlib.sha256(material.encode()).hexdigest()


def configured_api_token() -> str | None:
    """Return the configured bearer secret without ever exposing it in policy data."""
    direct = os.environ.get(API_TOKEN_ENV, "").strip()
    if direct:
        return direct
    filename = os.environ.get(API_TOKEN_FILE_ENV, "").strip()
    if not filename:
        return None
    try:
        token = Path(filename).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def request_has_valid_bearer(request: web.Request, token: str) -> bool:
    parts = request.headers.get("Authorization", "").split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return hmac.compare_digest(parts[1].encode(), token.encode())


def _configured_capabilities() -> frozenset[str]:
    raw = os.environ.get(CAPABILITIES_ENV, "")
    values = frozenset(
        item.strip().lower()
        for item in raw.replace(";", ",").split(",")
        if item.strip()
    )
    unknown = values - ALL_CAPABILITIES
    if unknown:
        raise PolicyConfigurationError(
            "unknown remote operator capabilities: " + ", ".join(sorted(unknown))
        )
    return values


def _trusted_origins() -> frozenset[str]:
    values: set[str] = set()
    raw = os.environ.get(TRUSTED_ORIGINS_ENV, "")
    for item in raw.replace(";", ",").split(","):
        origin = item.strip().lower().rstrip("/")
        if not origin:
            continue
        if "*" in origin:
            raise PolicyConfigurationError("wildcard trusted origins are not allowed")
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise PolicyConfigurationError(
                f"trusted origin must be an exact http(s) origin: {item!r}"
            )
        values.add(origin)
    return frozenset(values)


def resolve_policy(bind_host: str) -> EffectivePolicy:
    token = configured_api_token()
    requested = os.environ.get(AUTH_MODE_ENV, "").strip().lower()
    alias: str | None = None
    if requested in {"global", "operator"}:
        alias = requested
        if requested not in _WARNED_ALIASES:
            log.warning(
                "SYMPHONY_API_AUTH_MODE=%s is deprecated; using token mode",
                requested,
            )
            _WARNED_ALIASES.add(requested)
        requested = "token"
    if not requested:
        if token is not None:
            requested = "token"
        elif bind_host.strip().lower() in LOOPBACK_BINDS:
            requested = "disabled"
        else:
            raise PolicyConfigurationError(
                "non-loopback HTTP binds require an explicit SYMPHONY_API_AUTH_MODE"
            )
    if requested not in AUTH_MODES:
        raise PolicyConfigurationError(f"unknown API auth mode: {requested!r}")
    if (
        requested == "token"
        and token is not None
        and any(character.isspace() for character in token)
    ):
        raise PolicyConfigurationError(
            "configured API token contains internal whitespace"
        )
    if requested == "token" and token is None:
        raise PolicyConfigurationError("token mode requires SYMPHONY_API_TOKEN or TOKEN_FILE")

    configured = _configured_capabilities()
    if requested == "capabilities":
        effective = configured
    else:
        effective = NORMAL_CAPABILITIES | ({"debug"} if "debug" in configured else set())
    trusted_origins = _trusted_origins()
    if bind_host.strip().lower() not in LOOPBACK_BINDS and not trusted_origins:
        raise PolicyConfigurationError(
            "non-loopback HTTP binds require exact SYMPHONY_TRUSTED_ORIGINS"
        )
    return EffectivePolicy(
        mode=requested,
        token=token if requested == "token" else None,
        token_configured=token is not None,
        configured_grants=configured,
        effective_grants=frozenset(effective),
        trusted_origins=trusted_origins,
        deprecated_alias=alias,
    )


def _route_key(method: str, path: str) -> tuple[str, str]:
    return method.upper(), path


def _canonical(route: web.AbstractRoute) -> str:
    resource = route.resource
    if resource is None:
        raise PolicyConfigurationError("API route has no resource")
    return resource.canonical


def add_api_route(
    app: web.Application,
    method: str,
    path: str,
    handler,
    *,
    capabilities: Iterable[str] = (),
    public: bool = False,
    websocket_ticket: bool = False,
    denial_code: str = "missing_capability",
) -> web.AbstractRoute:
    required = frozenset(capabilities)
    unknown = required - ALL_CAPABILITIES
    if unknown:
        raise PolicyConfigurationError(f"route {method} {path} has unknown capabilities")
    if public and required:
        raise PolicyConfigurationError(f"public route {method} {path} cannot require capabilities")
    if websocket_ticket and not required:
        raise PolicyConfigurationError(
            f"ticket-authenticated route {method} {path} must require capabilities"
        )
    route = app.router.add_route(method.upper(), path, handler)
    policies = app.setdefault(ROUTE_POLICIES_KEY, {})
    key = _route_key(method, _canonical(route))
    if key in policies:
        raise PolicyConfigurationError(f"duplicate policy metadata for {method} {path}")
    policies[key] = RoutePolicy(
        required,
        public=public,
        websocket_ticket=websocket_ticket,
        denial_code=denial_code,
    )
    return route


def route_policy(request: web.Request) -> RoutePolicy | None:
    route = request.match_info.route
    canonical = _canonical(route)
    return request.app.get(ROUTE_POLICIES_KEY, {}).get(
        _route_key(request.method, canonical)
    )


def validate_route_policies(app: web.Application) -> None:
    policies = app.get(ROUTE_POLICIES_KEY, {})
    missing: list[str] = []
    for route in app.router.routes():
        canonical = _canonical(route)
        if canonical.startswith("/api/") and _route_key(route.method, canonical) not in policies:
            missing.append(f"{route.method} {canonical}")
    if missing:
        raise PolicyConfigurationError(
            "API routes lack authorization metadata: " + ", ".join(sorted(missing))
        )


def install_route_policies(app: web.Application) -> None:
    """Classify every currently registered API route and fail closed.

    The table is intentionally centralized here and shared by board services
    and the Hub.  Prefixes describe stable API namespaces; routes outside a
    namespace must be added explicitly before an application can start.
    """
    policies = app.setdefault(ROUTE_POLICIES_KEY, {})
    for route in app.router.routes():
        path = _canonical(route)
        if not path.startswith("/api/"):
            continue
        public = path in {"/api/v1/health", "/api/v1/auth/policy"}
        websocket_ticket = path == "/api/v1/chat/ws"
        required: frozenset[str]
        if public:
            required = frozenset()
        elif path == "/api/v1/_debug/tasks":
            required = frozenset({"debug"})
        elif path in {"/api/v1/state", "/api/v1/refresh", "/api/v1/stats"}:
            required = frozenset({"workers"})
        elif path.endswith("/pause") or path.endswith("/resume"):
            required = frozenset({"workers"})
        elif path.startswith("/api/v1/runs"):
            required = frozenset({"runs"})
        elif path.startswith("/api/v1/workflow") or path.startswith(
            "/api/v1/continuous-improvement"
        ):
            required = frozenset({"workflow"})
        elif path.startswith("/api/v1/git"):
            required = frozenset({"git"})
        elif path.startswith("/api/v1/chat"):
            required = frozenset({"chat"})
            if route.method in MUTATING_METHODS and path != "/api/v1/chat/ws-ticket":
                required |= {"board"}
            if "/project-setup/" in path:
                required |= {"projects"}
        elif path.startswith("/api/v1/preview"):
            required = frozenset({"preview"})
        elif path.startswith("/api/v1/projects"):
            required = frozenset({"projects"})
        elif (
            path.startswith("/api/v1/board")
            or path.startswith("/api/v1/requests")
            or path.startswith("/api/v1/issues")
            or path in {"/api/v1/{identifier}"}
            or path.endswith("/recover-blocked")
            or path.endswith("/skip-document")
            or path.endswith("/skip-learn")
        ):
            required = frozenset({"board"})
        else:
            raise PolicyConfigurationError(
                f"API route lacks authorization metadata: {route.method} {path}"
            )
        policies[_route_key(route.method, path)] = RoutePolicy(
            required, public=public, websocket_ticket=websocket_ticket
        )
    validate_route_policies(app)


def _json_error(status: int, code: str, message: str) -> web.Response:
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


def capability_denial(
    request: web.Request,
    required: Iterable[str],
    *,
    denial_code: str = "missing_capability",
) -> web.Response | None:
    """Authorize a data-dependent action within an already-classified route.

    Most authorization is static route metadata. A small number of endpoints
    multiplex ordinary work and a more privileged action based on server-owned
    state (for example, a numeric Chat reply that confirms project setup). Such
    handlers must use this shared evaluator before crossing that boundary.
    """
    capabilities = frozenset(required)
    unknown = capabilities - ALL_CAPABILITIES
    if unknown:
        raise PolicyConfigurationError(
            "dynamic authorization requested unknown capabilities: "
            + ", ".join(sorted(unknown))
        )
    try:
        policy = resolve_policy(
            str(request.app.get(BIND_HOST_KEY) or "127.0.0.1")
        )
    except PolicyConfigurationError:
        return _json_error(
            503, "invalid_auth_policy", "web authorization is misconfigured"
        )
    if policy.mode == "token" and not policy.authenticated(request):
        return _json_error(401, "unauthorized", "missing or invalid bearer token")
    if not capabilities.issubset(policy.grants_for(request)):
        return _json_error(403, denial_code, "required capability is not granted")
    return None


def _request_host(request: web.Request) -> str:
    return (request.host or "").strip().lower()


def _host_allowed(request: web.Request, policy: EffectivePolicy) -> bool:
    bind = str(request.app.get(BIND_HOST_KEY) or "127.0.0.1").lower()
    host = _request_host(request)
    if bind in LOOPBACK_BINDS:
        bare = host.split("]", 1)[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
        return bare in LOOPBACK_HOSTS or host in {
            urlsplit(origin).netloc for origin in policy.trusted_origins
        }
    return host in {urlsplit(origin).netloc for origin in policy.trusted_origins}


def _origin_allowed(request: web.Request, policy: EffectivePolicy) -> bool:
    origin = request.headers.get("Origin", "").strip().lower().rstrip("/")
    if not origin:
        return True
    if origin in policy.trusted_origins:
        return True
    bind = str(request.app.get(BIND_HOST_KEY) or "127.0.0.1").lower()
    return bind in LOOPBACK_BINDS and origin == f"{request.scheme}://{_request_host(request)}"


@web.middleware
async def policy_middleware(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)
    metadata = route_policy(request)
    if metadata is None:
        return _json_error(500, "unclassified_route", "API route has no policy metadata")
    try:
        policy = resolve_policy(str(request.app.get(BIND_HOST_KEY) or "127.0.0.1"))
    except PolicyConfigurationError:
        return _json_error(503, "invalid_auth_policy", "web authorization is misconfigured")
    if not _host_allowed(request, policy):
        return _json_error(403, "forbidden_host", f"host {request.host!r} not allowed")
    if request.method in MUTATING_METHODS or request.headers.get("Upgrade", "").lower() == "websocket":
        if not _origin_allowed(request, policy):
            return _json_error(403, "forbidden_origin", "origin is not trusted")
    if metadata.public:
        return await handler(request)
    if metadata.websocket_ticket:
        # The route handler atomically consumes its origin-bound, single-use
        # ticket. It deliberately does not accept the long-lived bearer.
        return await handler(request)
    if policy.mode == "token" and not policy.authenticated(request):
        return _json_error(401, "unauthorized", "missing or invalid bearer token")
    grants = policy.grants_for(request)
    if not metadata.capabilities.issubset(grants):
        return _json_error(403, metadata.denial_code, "required capability is not granted")
    if request.method in MUTATING_METHODS and request.body_exists and request.content_type != "application/json":
        return _json_error(415, "unsupported_media_type", "mutations require application/json")
    return await handler(request)


def policy_discovery_payload(request: web.Request) -> dict[str, object]:
    policy = resolve_policy(str(request.app.get(BIND_HOST_KEY) or "127.0.0.1"))
    authenticated = policy.authenticated(request)
    return {
        "version": 2,
        "mode": policy.mode,
        "token_configured": policy.token_configured,
        "authenticated": authenticated,
        "trusted_host": _host_allowed(request, policy),
        "configured_grants": sorted(policy.configured_grants),
        "effective_grants": sorted(policy.grants_for(request)),
        "denial_codes": {
            "authentication": "unauthorized",
            "capability": "missing_capability",
            "host": "forbidden_host",
            "origin": "forbidden_origin",
        },
    }


@dataclass
class WebSocketTicket:
    expires_at: float
    origin: str
    grants: frozenset[str]
    policy_fingerprint: str


class WebSocketTicketStore:
    """Short-lived, single-use WebSocket handoff credentials."""

    def __init__(self) -> None:
        self._tickets: dict[str, WebSocketTicket] = {}

    def issue(
        self, *, origin: str, grants: frozenset[str], policy_fingerprint: str
    ) -> tuple[str, int]:
        now = time.monotonic()
        self._purge(now)
        value = secrets.token_urlsafe(32)
        self._tickets[value] = WebSocketTicket(
            now + 30.0, origin, grants, policy_fingerprint
        )
        return value, 30

    def consume(
        self,
        value: str,
        *,
        origin: str,
        required: frozenset[str],
        policy_fingerprint: str,
    ) -> bool:
        now = time.monotonic()
        self._purge(now)
        ticket = self._tickets.pop(value, None)
        return bool(
            ticket
            and ticket.expires_at >= now
            and ticket.origin == origin
            and required.issubset(ticket.grants)
            and ticket.policy_fingerprint == policy_fingerprint
        )

    def _purge(self, now: float) -> None:
        for key, ticket in list(self._tickets.items()):
            if ticket.expires_at < now:
                self._tickets.pop(key, None)
