"""Comprehensive test suite for GitHub Copilot backend (Phase 1 / TASK-18)."""

from __future__ import annotations

from pathlib import Path
import textwrap
from typing import Any
import uuid
import pytest

from symphony.backends import (
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    BackendInit,
    build_backend,
)
from symphony.backends.copilot import CopilotBackend
from symphony.errors import ConfigValidationError, TurnFailed
from symphony.workflow.builder import build_service_config
from symphony.workflow.config import ServiceConfig
from symphony.workflow.constants import (
    PROFILE_FIELDS_BY_KIND,
    SUPPORTED_AGENT_KINDS,
)
from symphony.workflow.parser import parse_workflow_text
from symphony.workflow.profiles import resolve_agent_config


def _parse_config(workflow_text: str) -> ServiceConfig:
    dedented = textwrap.dedent(workflow_text).strip()
    if not dedented.startswith("---"):
        dedented = f"---\n{dedented}\n---\n"
    definition = parse_workflow_text(dedented, source_path=Path("/tmp/WORKFLOW.md"))
    return build_service_config(definition)


def _make_backend(
    tmp_path: Path,
    cfg: ServiceConfig | None = None,
    events: list[dict[str, Any]] | None = None,
) -> CopilotBackend:
    if cfg is None:
        cfg = _parse_config("""
        tracker: { kind: file }
        agent: { kind: copilot }
        """)
    cwd = tmp_path / "ws"
    cwd.mkdir(exist_ok=True)

    async def on_event(event: dict[str, Any]) -> None:
        if events is not None:
            events.append(event)

    return build_backend(
        BackendInit(
            cfg=cfg,
            cwd=cwd,
            workspace_root=tmp_path,
            on_event=on_event,
        )
    )  # type: ignore[return-value]


# ==============================================================================
# §22 Factory & Architecture Tests
# ==============================================================================


def test_build_backend_returns_copilot_backend(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    assert isinstance(backend, CopilotBackend)


def test_pi_module_contains_no_copilot_symbols() -> None:
    import symphony.backends.pi as pi_mod

    source = Path(pi_mod.__file__).read_text(encoding="utf-8")
    assert "copilot" not in source.lower(), "pi.py must contain zero copilot symbols"


# ==============================================================================
# §23 Command Construction Tests
# ==============================================================================


def test_copilot_command_flags(tmp_path: Path) -> None:
    cfg = _parse_config("""
    tracker: { kind: file }
    agent: { kind: copilot }
    copilot:
      command: my-copilot
      model: gpt-5
      reasoning_effort: high
    """)
    backend = _make_backend(tmp_path, cfg=cfg)
    cmd = backend._command_for_turn(prompt="fix the bug", is_continuation=False)

    assert "my-copilot" in cmd
    assert "--output-format=json" in cmd
    assert "--no-ask-user" in cmd
    assert "--allow-all-tools" in cmd
    assert "--model gpt-5" in cmd
    assert "--reasoning-effort high" in cmd
    assert "-p 'fix the bug'" in cmd or "-p fix the bug" in cmd


def test_copilot_stdin_payload_is_none(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    assert backend._stdin_payload("prompt") is None


# ==============================================================================
# §24 Session Management Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_copilot_session_lifecycle(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    backend = _make_backend(tmp_path, events=events)

    sid = await backend.start_session(initial_prompt="hi", issue_title="Task")
    assert sid
    assert backend.session_id == sid

    cmd1 = backend._command_for_turn(prompt="t1", is_continuation=False)
    assert f"--session-id {sid}" in cmd1

    cmd2 = backend._command_for_turn(prompt="t2", is_continuation=True)
    assert f"--session-id {sid}" in cmd2


@pytest.mark.asyncio
async def test_copilot_resume_session(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    custom_uuid = str(uuid.uuid4())
    resumed = await backend.resume_session(custom_uuid)
    assert resumed is True
    assert backend.session_id == custom_uuid

    cmd = backend._command_for_turn(prompt="turn", is_continuation=False)
    assert f"--session-id {custom_uuid}" in cmd


@pytest.mark.asyncio
async def test_copilot_invalid_resume_session_rejected(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    assert await backend.resume_session("") is False
    assert await backend.resume_session("   ") is False
    assert await backend.resume_session("bad\x00session") is False


# ==============================================================================
# §25 JSONL Parser & Completion Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_copilot_complete_turn_extracts_assistant_message(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    backend = _make_backend(tmp_path, events=events)

    stdout = textwrap.dedent("""
    {"type":"assistant.turn_start"}
    {"type":"assistant.message","data":{"content":"All tests passed!","outputTokens":42}}
    {"type":"result","sessionId":"session-123","exitCode":0}
    """)

    result = await backend._complete_turn(stdout, rc=0)
    assert result.status == EVENT_TURN_COMPLETED
    assert result.last_message == "All tests passed!"

    completed_events = [e for e in events if e["event"] == EVENT_TURN_COMPLETED]
    assert len(completed_events) == 1
    assert completed_events[0]["payload"]["message"] == "All tests passed!"


@pytest.mark.asyncio
async def test_copilot_complete_turn_handles_session_error(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    backend = _make_backend(tmp_path, events=events)

    stdout = textwrap.dedent("""
    {"type":"session.error","data":{"message":"API rate limit exceeded"}}
    """)

    with pytest.raises(TurnFailed, match="copilot error: API rate limit exceeded"):
        await backend._complete_turn(stdout, rc=1)

    failed_events = [e for e in events if e["event"] == EVENT_TURN_FAILED]
    assert len(failed_events) == 1


# ==============================================================================
# §28 Configuration & Profile Tests
# ==============================================================================


def test_copilot_profile_and_constants() -> None:
    assert "copilot" in SUPPORTED_AGENT_KINDS
    assert "copilot" in PROFILE_FIELDS_BY_KIND
    fields = PROFILE_FIELDS_BY_KIND["copilot"]
    assert "model" in fields
    assert "reasoning_effort" in fields
    assert "command" in fields
    assert "resume_across_turns" in fields


def test_copilot_config_parsing() -> None:
    cfg = _parse_config("""
    tracker: { kind: file }
    agent:
      kind: copilot
      stage_profiles:
        Plan: cop-high
    usage_pools:
      cop-pool:
        source: copilot
        caps:
          weekly: 70
    copilot:
      command: copilot-custom
      model: claude-3-7-sonnet
      turn_timeout_ms: 120000
    agent_profiles:
      cop-high:
        kind: copilot
        model: gpt-5
        reasoning_effort: high
        usage_pool: cop-pool
    """)
    assert cfg.agent.kind == "copilot"
    assert cfg.copilot is not None
    assert cfg.copilot.command == "copilot-custom"
    assert cfg.copilot.model == "claude-3-7-sonnet"
    assert cfg.copilot.turn_timeout_ms == 120000

    profile = cfg.agent_profiles["cop-high"]
    assert profile.kind == "copilot"
    assert profile.model == "gpt-5"
    assert profile.reasoning_effort == "high"
    assert profile.usage_pool == "cop-pool"

    # Profile resolution
    selection = cfg.selection_for_state("Plan")
    resolved = resolve_agent_config(cfg, selection)
    assert resolved.kind == "copilot"
    assert resolved.copilot is not None
    assert resolved.copilot.model == "gpt-5"
    assert resolved.copilot.reasoning_effort == "high"
    assert resolved.copilot.command == "copilot-custom"
    assert resolved.copilot.turn_timeout_ms == 120000


def test_invalid_copilot_profile_rejected() -> None:
    # Unknown field for copilot
    with pytest.raises(ConfigValidationError):
        _parse_config("""
        tracker: { kind: file }
        agent_profiles:
          bad:
            kind: copilot
            thread_sandbox: none
        """)
