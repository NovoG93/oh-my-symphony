import pytest
from mcp.server.fastmcp.exceptions import ToolError

from symphony.mcp.config import Settings
from symphony.mcp.server import build_mcp


def _settings(tmp_path, allow_control=False):
    return Settings(
        allow_control=allow_control,
        allowed_projects=frozenset({"oh-my-symphony"}),
        idempotency_db=tmp_path / "idem.sqlite3",
        audit_log=tmp_path / "audit.jsonl",
    )


@pytest.mark.asyncio
async def test_registers_16_tools(tmp_path):
    mcp = build_mcp(_settings(tmp_path))
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert len(names) == 17
    for expected in (
        "symphony_list_projects",
        "symphony_create_request",
        "symphony_get_request",
        "symphony_get_task",
        "symphony_get_run",
        "symphony_list_requests",
        "symphony_get_board",
        "symphony_list_runs",
        "symphony_get_run_diagnostic",
        "symphony_list_artifacts",
        "symphony_get_artifact",
        "symphony_get_workflow",
        "symphony_get_stats",
        "symphony_cancel_request",
        "symphony_update_request",
        "symphony_recover_blocked",
        "symphony_skip_document",
    ):
        assert expected in names, f"missing tool {expected}"


@pytest.mark.asyncio
async def test_control_tool_gated_when_disabled(tmp_path):
    mcp = build_mcp(_settings(tmp_path, allow_control=False))
    with pytest.raises(ToolError) as exc:
        await mcp.call_tool("symphony_cancel_request", {"request_id": "TASK-1"})
    assert "control actions are disabled" in str(exc.value)
