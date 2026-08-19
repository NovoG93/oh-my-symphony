"""Comprehensive test suite for GitHub Copilot backend (Phase 1 & 2 / TASK-18 & TASK-19)."""

from __future__ import annotations

from pathlib import Path
import textwrap
from typing import Any
import uuid
import pytest

from symphony.backends import (
    EVENT_PROVIDER_USAGE_EXHAUSTED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    BackendInit,
    ProviderCapacityError,
    build_backend,
)
import symphony.backends.per_turn as per_turn_module
from symphony.backends.copilot import (
    CopilotBackend,
    _is_genuine_copilot_exhaustion,
)
from symphony.errors import ConfigValidationError, TurnFailed
from symphony.workflow.builder import build_service_config
from symphony.workflow.config import ServiceConfig
from symphony.workflow.constants import (
    PROFILE_FIELDS_BY_KIND,
    SUPPORTED_AGENT_KINDS,
)
from symphony.workflow.parser import parse_workflow_text
from symphony.workflow.profiles import resolve_agent_config
from tests.test_backends import _FakeSubprocess, _install_subprocess_double


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


def test_copilot_prompt_is_passed_with_p_flag(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    cmd = backend._command_for_turn(prompt="implement feature", is_continuation=False)
    assert "-p 'implement feature'" in cmd or "-p implement feature" in cmd
    assert backend._stdin_payload("implement feature") is None


def test_copilot_json_output_is_enabled(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    cmd = backend._command_for_turn(prompt="task", is_continuation=False)
    assert "--output-format=json" in cmd


def test_copilot_model_is_forwarded(tmp_path: Path) -> None:
    cfg = _parse_config("""
    tracker: { kind: file }
    agent: { kind: copilot }
    copilot:
      model: claude-3-7-sonnet
    """)
    backend = _make_backend(tmp_path, cfg=cfg)
    cmd = backend._command_for_turn(prompt="task", is_continuation=False)
    assert "--model claude-3-7-sonnet" in cmd


def test_copilot_reasoning_effort_is_forwarded(tmp_path: Path) -> None:
    cfg = _parse_config("""
    tracker: { kind: file }
    agent: { kind: copilot }
    copilot:
      reasoning_effort: low
    """)
    backend = _make_backend(tmp_path, cfg=cfg)
    cmd = backend._command_for_turn(prompt="task", is_continuation=False)
    assert "--reasoning-effort low" in cmd


def test_copilot_no_ask_user_is_enabled(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    cmd = backend._command_for_turn(prompt="task", is_continuation=False)
    assert "--no-ask-user" in cmd


def test_copilot_allow_all_tools_is_enabled(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    cmd = backend._command_for_turn(prompt="task", is_continuation=False)
    assert "--allow-all-tools" in cmd
    assert "--allow-all " not in cmd and not cmd.endswith("--allow-all")
    assert "--yolo" not in cmd


def test_writable_roots_become_add_dir_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _make_backend(tmp_path)
    monkeypatch.setattr(
        backend,
        "_git_roots",
        ["/repo/.git", "/shared/lib"],
    )
    cmd = backend._command_for_turn(prompt="task", is_continuation=False)
    assert "--add-dir /repo/.git" in cmd
    assert "--add-dir /shared/lib" in cmd


# ==============================================================================
# §24 Session Management Tests
# ==============================================================================


def test_copilot_first_session_gets_uuid(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    cmd = backend._command_for_turn(prompt="t1", is_continuation=False)
    assert backend.session_id is not None
    # Validate UUID format
    parsed = uuid.UUID(backend.session_id)
    assert str(parsed) == backend.session_id
    assert f"--session-id {backend.session_id}" in cmd


def test_consecutive_turns_reuse_session_id(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    cmd1 = backend._command_for_turn(prompt="t1", is_continuation=False)
    sid1 = backend.session_id
    assert sid1 is not None

    cmd2 = backend._command_for_turn(prompt="t2", is_continuation=True)
    assert backend.session_id == sid1
    assert f"--session-id {sid1}" in cmd1
    assert f"--session-id {sid1}" in cmd2


@pytest.mark.asyncio
async def test_resume_session_uses_existing_uuid(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    custom_uuid = str(uuid.uuid4())
    resumed = await backend.resume_session(custom_uuid)
    assert resumed is True
    assert backend.session_id == custom_uuid

    cmd = backend._command_for_turn(prompt="turn", is_continuation=False)
    assert f"--session-id {custom_uuid}" in cmd


@pytest.mark.asyncio
async def test_invalid_resume_session_uuid_is_rejected(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    assert await backend.resume_session("") is False
    assert await backend.resume_session("   ") is False
    assert await backend.resume_session("bad\x00session") is False
    assert await backend.resume_session("a" * 600) is False


def test_resume_across_turns_false_creates_new_session(tmp_path: Path) -> None:
    cfg = _parse_config("""
    tracker: { kind: file }
    agent: { kind: copilot }
    copilot:
      resume_across_turns: false
    """)
    backend = _make_backend(tmp_path, cfg=cfg)
    cmd1 = backend._command_for_turn(prompt="t1", is_continuation=False)
    sid1 = backend.session_id
    assert sid1 is not None

    cmd2 = backend._command_for_turn(prompt="t2", is_continuation=True)
    sid2 = backend.session_id
    assert sid2 is not None
    assert sid1 != sid2
    assert f"--session-id {sid1}" in cmd1
    assert f"--session-id {sid2}" in cmd2


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


# ==============================================================================
# §25 JSONL Parser & Completion Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_assistant_message_becomes_final_output(tmp_path: Path) -> None:
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
async def test_malformed_json_line_does_not_crash_worker(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    backend = _make_backend(tmp_path, events=events)

    stdout = textwrap.dedent("""
    [not json]
    {"type":"assistant.message","data":{"content":"Recovered from bad line"}}
    random text
    {"type":"result","sessionId":"session-123","exitCode":0}
    """)

    result = await backend._complete_turn(stdout, rc=0)
    assert result.status == EVENT_TURN_COMPLETED
    assert result.last_message == "Recovered from bad line"


@pytest.mark.asyncio
async def test_unknown_event_is_tolerated(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)

    stdout = textwrap.dedent("""
    {"type":"future.unrecognized.event","data":{"foo":"bar"}}
    {"type":"assistant.message","data":{"content":"Work done"}}
    {"type":"session.checkpoint.v99","custom":123}
    {"type":"result","sessionId":"session-123","exitCode":0}
    """)

    result = await backend._complete_turn(stdout, rc=0)
    assert result.status == EVENT_TURN_COMPLETED
    assert result.last_message == "Work done"


@pytest.mark.asyncio
async def test_session_error_fails_turn(tmp_path: Path) -> None:
    events: list[dict[str, Any]] = []
    backend = _make_backend(tmp_path, events=events)

    stdout = textwrap.dedent("""
    {"type":"session.error","data":{"message":"API rate limit exceeded"}}
    """)

    with pytest.raises(TurnFailed, match="copilot error: API rate limit exceeded"):
        await backend._complete_turn(stdout, rc=1)

    failed_events = [e for e in events if e["event"] == EVENT_TURN_FAILED]
    assert len(failed_events) == 1


@pytest.mark.asyncio
async def test_final_message_is_not_duplicated_from_deltas(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)

    stdout = textwrap.dedent("""
    {"type":"assistant.message_delta","data":{"deltaContent":"Hello "}}
    {"type":"assistant.message_delta","data":{"deltaContent":"world"}}
    {"type":"assistant.message","data":{"content":"Hello world","outputTokens":10}}
    {"type":"result","sessionId":"session-123","exitCode":0}
    """)

    result = await backend._complete_turn(stdout, rc=0)
    assert result.last_message == "Hello world"


@pytest.mark.asyncio
async def test_copilot_output_tokens_telemetry(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)

    stdout = textwrap.dedent("""
    {"type":"assistant.message","data":{"content":"Turn 1","outputTokens":55}}
    {"type":"result","sessionId":"session-123","exitCode":0}
    """)
    await backend._complete_turn(stdout, rc=0)
    assert backend.latest_usage["output_tokens"] == 55
    assert backend.latest_usage["total_tokens"] == 55

    stdout2 = textwrap.dedent("""
    {"type":"assistant.message","data":{"content":"Turn 2","outputTokens":45}}
    {"type":"result","sessionId":"session-123","exitCode":0}
    """)
    await backend._complete_turn(stdout2, rc=0)
    assert backend.latest_usage["output_tokens"] == 100
    assert backend.latest_usage["total_tokens"] == 100


@pytest.mark.asyncio
async def test_copilot_result_exit_code_nonzero_fails_turn(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)

    stdout = textwrap.dedent("""
    {"type":"result","sessionId":"session-123","exitCode":42}
    """)
    with pytest.raises(TurnFailed, match="copilot result exitCode 42"):
        await backend._complete_turn(stdout, rc=0)


@pytest.mark.asyncio
async def test_copilot_recovered_session_mismatch_fails_turn(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    await backend.resume_session("expected-session-id")

    stdout = textwrap.dedent("""
    {"type":"result","sessionId":"different-session-id","exitCode":0}
    """)
    with pytest.raises(TurnFailed, match="copilot returned a different recovered session"):
        await backend._complete_turn(stdout, rc=0)


@pytest.mark.asyncio
async def test_copilot_recovered_session_unconfirmed_fails_turn(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    await backend.resume_session("expected-session-id")

    stdout = textwrap.dedent("""
    {"type":"assistant.message","data":{"content":"done"}}
    """)
    with pytest.raises(TurnFailed, match="copilot did not confirm the requested recovered session"):
        await backend._complete_turn(stdout, rc=0)


def test_copilot_is_progress_event(tmp_path: Path) -> None:
    backend = _make_backend(tmp_path)
    assert backend.is_progress_event({"type": "assistant.message"}) is True
    assert backend.is_progress_event({"type": "assistant.message_delta"}) is True
    assert backend.is_progress_event({"type": "assistant.turn_start"}) is True
    assert backend.is_progress_event({"type": "assistant.turn_end"}) is True
    assert backend.is_progress_event({"type": "model.call_start"}) is True
    assert backend.is_progress_event({"type": "tool.call"}) is True
    assert backend.is_progress_event({"type": "tool.execution"}) is True
    assert backend.is_progress_event({"type": "unknown.event"}) is False
    assert backend.is_progress_event({}) is False


# ==============================================================================
# §27 Capacity & Exhaustion Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_genuine_copilot_credit_exhaustion_emits_provider_usage_exhausted(
    tmp_path: Path,
) -> None:
    events: list[dict[str, Any]] = []
    backend = _make_backend(tmp_path, events=events)

    stdout = textwrap.dedent("""
    {"type":"session.error","data":{"message":"Your quota exceeded for the current billing period"}}
    """)
    with pytest.raises(ProviderCapacityError, match="quota exceeded"):
        await backend._complete_turn(stdout, rc=1)

    exhausted_events = [e for e in events if e["event"] == EVENT_PROVIDER_USAGE_EXHAUSTED]
    assert len(exhausted_events) == 1
    assert exhausted_events[0]["payload"]["pool_id"] == "copilot"


def test_generic_rate_limit_does_not_mark_plan_exhausted() -> None:
    assert _is_genuine_copilot_exhaustion("Too many requests: 60 requests per minute limit reached") is False
    assert _is_genuine_copilot_exhaustion("RPM limit exceeded") is False
    assert _is_genuine_copilot_exhaustion("Tokens per minute exceeded") is False
    assert _is_genuine_copilot_exhaustion("AI credits exhausted") is True
    assert _is_genuine_copilot_exhaustion("Quota exceeded") is True
    assert _is_genuine_copilot_exhaustion("insufficient credits") is True


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
    with pytest.raises(ConfigValidationError):
        _parse_config("""
        tracker: { kind: file }
        agent_profiles:
          bad:
            kind: copilot
            thread_sandbox: none
        """)


# ==============================================================================
# End-to-End run_turn Test
# ==============================================================================


@pytest.mark.asyncio
async def test_copilot_run_turn_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_stdout = (
        textwrap.dedent("""
        {"type":"assistant.turn_start"}
        {"type":"assistant.message","data":{"content":"Plan created successfully","outputTokens":120}}
        {"type":"result","sessionId":"cop-sess-1","exitCode":0}
        """).strip().encode("utf-8")
        + b"\n"
    )
    _install_subprocess_double(
        monkeypatch,
        per_turn_module,
        [_FakeSubprocess(stdout_blob=fake_stdout)],
    )

    events: list[dict[str, Any]] = []
    backend = _make_backend(tmp_path, events=events)

    result = await backend.run_turn(prompt="create plan", is_continuation=False)
    assert result.status == EVENT_TURN_COMPLETED
    assert result.last_message == "Plan created successfully"
    assert backend.latest_usage["output_tokens"] == 120

    completed_events = [e for e in events if e["event"] == EVENT_TURN_COMPLETED]
    assert len(completed_events) == 1
    assert completed_events[0]["payload"]["message"] == "Plan created successfully"
