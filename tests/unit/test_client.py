import httpx
import pytest

from symphony.mcp.client import SymphonyClient
from symphony.mcp.errors import NotFound, UpstreamError, ValidationError


def _client(handler, *, api_token=None) -> SymphonyClient:
    return SymphonyClient(
        "http://symphony",
        transport=httpx.MockTransport(handler),
        api_token=api_token,
    )


async def test_api_token_is_forwarded_but_mcp_token_is_not():
    def handler(req):
        assert req.headers["authorization"] == "Bearer api-token"
        assert "mcp-token" not in req.headers["authorization"]
        return httpx.Response(200, json={"projects": []})

    c = _client(handler, api_token="api-token")
    await c.list_projects()


async def test_api_token_is_forwarded_to_raw_artifact_download():
    def handler(req):
        assert req.headers["authorization"] == "Bearer api-token"
        return httpx.Response(200, content=b"hello", headers={"content-type": "text/plain"})

    c = _client(handler, api_token="api-token")
    assert (await c.get_artifact_file("TASK-1", "qa.md"))["content"] == b"hello"


async def test_list_projects():
    def handler(req):
        assert req.url.path == "/api/v1/projects"
        return httpx.Response(200, json={"projects": [{"id": "p1"}, {"id": "p2"}]})

    c = _client(handler)
    assert await c.list_projects() == [{"id": "p1"}, {"id": "p2"}]


async def test_create_issue_posts_body():
    captured = {}

    def handler(req):
        captured["path"] = req.url.path
        captured["method"] = req.method
        captured["body"] = req.content
        return httpx.Response(201, json={"identifier": "TASK-1", "state": "todo"})

    c = _client(handler)
    r = await c.create_issue(title="t", description="d", priority=2)
    assert r["identifier"] == "TASK-1"
    assert captured["path"] == "/api/v1/issues"
    assert captured["method"] == "POST"
    assert b'"priority":2' in captured["body"]


async def test_404_raises_not_found():
    c = _client(lambda req: httpx.Response(404, json={}))
    with pytest.raises(NotFound):
        await c.get_issue("NOPE")


async def test_400_raises_validation():
    c = _client(lambda req: httpx.Response(400, text="bad title"))
    with pytest.raises(ValidationError):
        await c.create_issue(title="", description=None, priority=None)


async def test_5xx_retries_then_raises_upstream():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(500, json={})

    c = _client(handler)
    with pytest.raises(UpstreamError):
        await c.get_issue("X")
    assert calls["n"] == 3  # 1 attempt + 2 retries


async def test_post_is_not_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(500, json={})

    c = _client(handler)
    with pytest.raises(UpstreamError):
        await c.create_issue(title="t", description=None, priority=None)
    assert calls["n"] == 1


async def test_list_requests_path():
    def handler(req):
        assert req.url.path == "/api/v1/requests"
        return httpx.Response(200, json={"available": True, "requests": []})

    c = _client(handler)
    data = await c.list_requests()
    assert data["available"] is True


async def test_list_runs_passes_filters():
    captured = {}

    def handler(req):
        captured["params"] = dict(req.url.params)
        return httpx.Response(200, json={"runs": [], "count": 0})

    c = _client(handler)
    await c.list_runs(issue_id="TASK-1", limit=10, status="failed", agent="claude")
    assert captured["params"] == {
        "limit": "10",
        "issue": "TASK-1",
        "status": "failed",
        "agent": "claude",
    }


async def test_patch_issue_sends_fields():
    captured = {}

    def handler(req):
        assert req.method == "PATCH"
        assert req.url.path == "/api/v1/issues/TASK-1"
        captured["body"] = req.content
        return httpx.Response(200, json={"identifier": "TASK-1", "updated": ["state"]})

    c = _client(handler)
    r = await c.patch_issue("TASK-1", fields={"state": "Cancelled"})
    assert r["identifier"] == "TASK-1"
    assert b'"state":"Cancelled"' in captured["body"]


async def test_recover_blocked_body():
    captured = {}

    def handler(req):
        assert req.url.path == "/api/v1/issues/TASK-1/recover-blocked"
        captured["body"] = req.content
        return httpx.Response(200, json={"identifier": "TASK-1", "fix_created": True})

    c = _client(handler)
    await c.recover_blocked("TASK-1", fix_state="Todo", agent_kind="agy")
    assert b'"fix_state":"Todo"' in captured["body"]
    assert b'"agent_kind":"agy"' in captured["body"]


async def test_skip_document_sends_json_object():
    captured = {}

    def handler(req):
        captured["content_type"] = req.headers.get("content-type", "")
        captured["body"] = req.content
        return httpx.Response(200, json={"identifier": "TASK-1", "skipped": True})

    c = _client(handler)
    await c.skip_document("TASK-1")
    assert "application/json" in captured["content_type"]


async def test_get_artifact_file_returns_bytes():
    def handler(req):
        return httpx.Response(200, content=b"hello", headers={"content-type": "text/plain"})

    c = _client(handler)
    data = await c.get_artifact_file("TASK-1", "qa.md")
    assert data["content"] == b"hello"
    assert data["content_type"] == "text/plain"


async def test_get_run_diagnostic_path():
    def handler(req):
        assert req.url.path == "/api/v1/runs/r1/diagnostic"
        return httpx.Response(200, json={"trace": "x"})

    c = _client(handler)
    data = await c.get_run_diagnostic("r1")
    assert data == {"trace": "x"}
