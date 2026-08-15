import httpx
import pytest

from symphony.mcp.client import SymphonyClient
from symphony.mcp.errors import NotFound, UpstreamError, ValidationError


def _client(handler) -> SymphonyClient:
    return SymphonyClient("http://symphony", transport=httpx.MockTransport(handler))


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
