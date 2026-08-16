from symphony.mcp.errors import (
    AuthenticationError,
    PermissionDenied,
    ProjectNotAllowed,
    UpstreamError,
    ValidationError,
)


def test_error_codes_and_status():
    assert AuthenticationError("x").code == "unauthorized"
    assert AuthenticationError("x").http_status == 401
    assert PermissionDenied("x").code == "forbidden"
    assert ProjectNotAllowed("x").http_status == 403
    assert ValidationError("x").code == "invalid_request"
    assert UpstreamError("x").http_status == 502


def test_error_has_message():
    e = ValidationError("bad priority")
    assert e.message == "bad priority"
    assert str(e) == "bad priority"
