"""Error taxonomy for the symphony-mcp gateway."""

from __future__ import annotations


class SymphonyMCPError(Exception):
    """Base error, mapped to a stable MCP/JSON-RPC error code."""

    code = "internal_error"
    http_status = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AuthenticationError(SymphonyMCPError):
    code = "unauthorized"
    http_status = 401


class PermissionDenied(SymphonyMCPError):
    code = "forbidden"
    http_status = 403


class ProjectNotAllowed(PermissionDenied):
    pass


class ValidationError(SymphonyMCPError):
    code = "invalid_request"
    http_status = 400


class NotFound(SymphonyMCPError):
    code = "not_found"
    http_status = 404


class UpstreamError(SymphonyMCPError):
    code = "upstream_error"
    http_status = 502
