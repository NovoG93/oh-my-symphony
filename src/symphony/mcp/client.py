"""Typed HTTP client for the oh-my-symphony REST API (localhost control plane)."""

from __future__ import annotations

import asyncio

import httpx

from .errors import NotFound, UpstreamError, ValidationError


class SymphonyClient:
    """All interaction with oh-my-symphony goes through this class.

    MCP tool code never sees raw REST endpoint paths or response shapes.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=self._base_url, timeout=timeout, transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        retry: bool = False,
    ) -> dict:
        attempts = 3 if retry else 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                resp = await self._client.request(method, path, json=json, params=params)
                if resp.status_code in (429, 500, 502, 503, 504) and retry and attempt < attempts - 1:
                    await _backoff(attempt)
                    continue
                return self._decode(resp)
            except httpx.HTTPError as exc:
                last_exc = exc
                if retry and attempt < attempts - 1:
                    await _backoff(attempt)
                    continue
                raise UpstreamError(f"symphony unreachable: {exc}") from exc
        raise UpstreamError(f"symphony unreachable: {last_exc}")

    @staticmethod
    def _decode(resp: httpx.Response) -> dict:
        if resp.status_code == 404:
            raise NotFound("resource not found in symphony")
        if resp.status_code == 400:
            raise ValidationError(resp.text[:500])
        if resp.status_code >= 400:
            raise UpstreamError(f"symphony returned HTTP {resp.status_code}")
        if resp.status_code == 204:
            return {}
        try:
            return resp.json()
        except Exception as exc:
            raise UpstreamError("symphony returned a non-JSON response") from exc

    # --- read ---
    async def list_projects(self) -> list[dict]:
        data = await self._request("GET", "/api/v1/projects", retry=True)
        return data.get("projects", [])

    async def get_issue(self, identifier: str) -> dict:
        return await self._request("GET", f"/api/v1/issues/{identifier}", retry=True)

    async def get_request_schedule(self, identifier: str) -> dict:
        # Schedules are grouped by kind; board tickets live under "ticket".
        return await self._request(
            "GET", f"/api/v1/requests/ticket/{identifier}/schedule", retry=True
        )

    async def get_run(self, run_id: str) -> dict:
        return await self._request("GET", f"/api/v1/runs/{run_id}", retry=True)

    # --- create ---
    async def create_issue(
        self,
        *,
        title: str,
        description: str | None,
        priority: int | None,
    ) -> dict:
        body: dict = {"title": title, "description": description}
        if priority is not None:
            body["priority"] = priority
        # POST is never auto-retried; idempotency is handled at the tool layer.
        return await self._request("POST", "/api/v1/issues", json=body)

    # --- list / observe ---
    async def list_requests(self) -> dict:
        return await self._request("GET", "/api/v1/requests", retry=True)

    async def get_board(self) -> dict:
        return await self._request("GET", "/api/v1/board", retry=True)

    async def list_runs(
        self,
        *,
        issue_id: str | None = None,
        limit: int = 50,
        status: str | None = None,
        agent: str | None = None,
    ) -> dict:
        params: dict = {"limit": str(limit)}
        if issue_id:
            params["issue"] = issue_id
        if status:
            params["status"] = status
        if agent:
            params["agent"] = agent
        return await self._request("GET", "/api/v1/runs", params=params, retry=True)

    async def get_run_diagnostic(self, run_id: str) -> dict:
        return await self._request(
            "GET", f"/api/v1/runs/{run_id}/diagnostic", retry=True
        )

    async def list_artifacts(self, identifier: str) -> dict:
        return await self._request(
            "GET", f"/api/v1/issues/{identifier}/artifacts", retry=True
        )

    async def get_workflow(self) -> dict:
        return await self._request("GET", "/api/v1/workflow", retry=True)

    async def get_stats(self, days: int = 30) -> dict:
        return await self._request(
            "GET", "/api/v1/stats", params={"days": str(days)}, retry=True
        )

    # --- control ---
    async def patch_issue(self, identifier: str, *, fields: dict) -> dict:
        # PATCH is never auto-retried; mutating operations are idempotent-agnostic.
        return await self._request(
            "PATCH", f"/api/v1/issues/{identifier}", json=fields
        )

    async def recover_blocked(
        self,
        identifier: str,
        *,
        fix_state: str | None = None,
        agent_kind: str | None = None,
    ) -> dict:
        body: dict = {}
        if fix_state is not None:
            body["fix_state"] = fix_state
        if agent_kind is not None:
            body["agent_kind"] = agent_kind
        return await self._request(
            "POST", f"/api/v1/issues/{identifier}/recover-blocked", json=body
        )

    async def skip_document(self, identifier: str) -> dict:
        # No request body, but the mutating-method guard expects a JSON content
        # type, so send an explicit empty object.
        return await self._request(
            "POST", f"/api/v1/issues/{identifier}/skip-document", json={}
        )

    async def get_artifact_file(self, identifier: str, name: str) -> dict:
        """Fetch a raw artifact file (bytes + content type); not JSON-decoded."""
        resp = await self._client.get(
            f"/api/v1/issues/{identifier}/artifacts/{name}"
        )
        if resp.status_code == 404:
            raise NotFound("artifact not found in symphony")
        if resp.status_code >= 400:
            raise UpstreamError(f"symphony returned HTTP {resp.status_code}")
        return {
            "content": resp.content,
            "content_type": resp.headers.get("content-type", ""),
        }


async def _backoff(attempt: int) -> None:
    await asyncio.sleep(0.5 * (2**attempt))
