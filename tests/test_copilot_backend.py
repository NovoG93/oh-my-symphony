"""Comprehensive test suite for GitHub Copilot backend (Phases 1–4 / TASK-18–21)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
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
import symphony.backends.copilot as copilot_module
from symphony.backends.copilot import (
    CopilotBackend,
    CopilotUsageProbe,
    _is_genuine_copilot_exhaustion,
    _parse_resets_at,
    next_month_first_day_utc,
    normalize_copilot_quota,
)
import symphony.backends.per_turn as per_turn_module
from symphony.backends.usage import (
    USAGE_PROBES,
    ProviderUsageSnapshot,
    UsageWindow,
    get_usage_probe,
)
from symphony.chat import _summarize_copilot_frame, _summarize_frame
from symphony.cli.doctor import check_agent_cli, check_copilot_auth, check_pi_auth
from symphony.errors import ConfigValidationError, TurnFailed
from symphony.issue import Issue
from symphony.orchestrator.core import Orchestrator, _EligibilityDisposition
from symphony.orchestrator.entries import RunningEntry
from symphony.workflow.builder import build_service_config
from symphony.workflow.config import ServiceConfig
from symphony.workflow.constants import (
    PROFILE_FIELDS_BY_KIND,
    SUPPORTED_AGENT_KINDS,
)
from symphony.workflow.parser import parse_workflow_text
from symphony.workflow.profiles import resolve_agent_config
from symphony.workflow.state import WorkflowState
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


def _issue(
    identifier: str,
    *,
    state: str = "In Progress",
    agent_kind: str | None = None,
    agent_profile: str | None = None,
) -> Issue:
    return Issue(
        id=identifier,
        identifier=identifier,
        title=identifier,
        description="",
        state=state,
        priority=1,
        agent_kind=agent_kind,
        agent_profile=agent_profile,
    )


def _orch(cfg: ServiceConfig) -> Orchestrator:
    state = WorkflowState(Path("/tmp/WORKFLOW.md"))
    state._config = cfg  # type: ignore[attr-defined]
    return Orchestrator(state)


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
# §26 Usage Probe & Quota Probing Tests
# ==============================================================================


def test_copilot_usage_probe_lives_in_copilot_module() -> None:
    assert hasattr(copilot_module, "CopilotUsageProbe")
    assert USAGE_PROBES.get("copilot") is CopilotUsageProbe


def test_source_copilot_resolves_copilot_usage_probe() -> None:
    probe_cls = get_usage_probe("copilot")
    assert probe_cls is CopilotUsageProbe


def test_legacy_github_copilot_alias_resolves_if_supported() -> None:
    probe_cls = get_usage_probe("github-copilot")
    assert probe_cls is CopilotUsageProbe


@pytest.mark.asyncio
async def test_copilot_quota_probe_failure_fails_open() -> None:
    probe = CopilotUsageProbe(command="nonexistent-copilot-bin-12345")
    snapshot = await probe.fetch_usage()
    assert snapshot is None


def test_remaining_percentage_converts_to_used_percentage() -> None:
    raw = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "quotaSnapshots": {
                "chat": {
                    "isUnlimitedEntitlement": True,
                    "entitlementRequests": 0,
                    "usedRequests": 0,
                    "remainingPercentage": 100,
                    "resetDate": "2026-09-01T00:00:00Z",
                    "hasQuota": True,
                    "tokenBasedBilling": True,
                },
                "completions": {
                    "isUnlimitedEntitlement": True,
                    "entitlementRequests": 0,
                    "usedRequests": 0,
                    "remainingPercentage": 100,
                    "resetDate": "2026-09-01T00:00:00Z",
                    "hasQuota": True,
                    "tokenBasedBilling": True,
                },
                "premium_interactions": {
                    "isUnlimitedEntitlement": False,
                    "entitlementRequests": 1500,
                    "usedRequests": 74,
                    "remainingPercentage": 95.1,
                    "resetDate": "2026-09-01T00:00:00Z",
                    "hasQuota": True,
                    "tokenBasedBilling": True,
                },
            }
        },
    }
    snapshot = normalize_copilot_quota(raw, pool_id="copilot-pool")
    assert snapshot.pool_id == "copilot-pool"
    assert snapshot.source == "copilot"
    assert snapshot.authoritative is True
    assert snapshot.hard_limit_reached is False
    assert "monthly" in snapshot.windows

    window = snapshot.windows["monthly"]
    assert window.key == "monthly"
    assert window.remaining_percent == 95.1
    assert window.used_percent == 4.9
    assert window.resets_at == datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)

    # Test edge case: 0% remaining -> 100% used and hard_limit_reached = True
    raw_exhausted = {
        "result": {
            "quotaSnapshots": {
                "premium_interactions": {
                    "remainingPercentage": 0.0,
                    "hasQuota": False,
                }
            }
        }
    }
    exhausted_snapshot = normalize_copilot_quota(raw_exhausted)
    assert exhausted_snapshot.hard_limit_reached is True
    assert exhausted_snapshot.windows["monthly"].used_percent == 100.0
    assert exhausted_snapshot.windows["monthly"].remaining_percent == 0.0


def test_monthly_reset_is_calculated_correctly() -> None:
    # Explicit ISO resetDate
    raw = {
        "result": {
            "quotaSnapshots": {
                "premium_interactions": {
                    "remainingPercentage": 50.0,
                    "resetDate": "2026-10-15T12:00:00Z",
                }
            }
        }
    }
    snapshot = normalize_copilot_quota(raw)
    assert snapshot.windows["monthly"].resets_at == datetime(
        2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc
    )

    # Fallback when resetDate is missing
    raw_no_reset = {
        "result": {
            "quotaSnapshots": {
                "premium_interactions": {
                    "remainingPercentage": 50.0,
                }
            }
        }
    }
    snapshot_no_reset = normalize_copilot_quota(raw_no_reset)
    assert snapshot_no_reset.windows["monthly"].resets_at is not None

    # Test next_month_first_day_utc calculation
    dt_aug = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)
    assert next_month_first_day_utc(dt_aug) == datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)

    dt_dec = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    assert next_month_first_day_utc(dt_dec) == datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    # Test _parse_resets_at helper directly
    assert _parse_resets_at(None) is None
    assert _parse_resets_at("invalid-date") is None
    assert _parse_resets_at({"invalid": "type"}) is None
    assert _parse_resets_at(1788220800) == datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert _parse_resets_at(1788220800000) == datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    assert _parse_resets_at("2026-09-01T00:00:00Z") == datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)


class _FakeLspStream:
    def __init__(self, data: bytes) -> None:
        self._reader = asyncio.StreamReader()
        self._reader.feed_data(data)
        self._reader.feed_eof()

    async def readline(self) -> bytes:
        return await self._reader.readline()

    async def readexactly(self, n: int) -> bytes:
        return await self._reader.readexactly(n)

    async def read(self, n: int = -1) -> bytes:
        return await self._reader.read(n)


@pytest.mark.asyncio
async def test_copilot_usage_probe_standalone_lsp_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "quotaSnapshots": {
                "premium_interactions": {
                    "isUnlimitedEntitlement": False,
                    "remainingPercentage": 80.0,
                    "resetDate": "2026-09-01T00:00:00Z",
                    "hasQuota": True,
                }
            }
        },
    }).encode("utf-8")
    lsp_frame = f"Content-Length: {len(response_body)}\r\n\r\n".encode("utf-8") + response_body

    fake_proc = _FakeSubprocess()
    fake_proc.stdout = _FakeLspStream(lsp_frame)  # type: ignore[assignment]
    _install_subprocess_double(monkeypatch, copilot_module, [fake_proc])

    probe = CopilotUsageProbe(pool_id="copilot-test")
    snapshot = await probe.fetch_usage()
    assert snapshot is not None
    assert snapshot.pool_id == "copilot-test"
    assert snapshot.source == "copilot"
    assert snapshot.authoritative is True
    assert snapshot.hard_limit_reached is False
    assert "monthly" in snapshot.windows
    assert snapshot.windows["monthly"].used_percent == 20.0
    assert snapshot.windows["monthly"].remaining_percent == 80.0
    assert snapshot.windows["monthly"].resets_at == datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)

    # Verify LSP request was sent to stdin
    assert b"account.getQuota" in fake_proc.stdin.data
    assert b"Content-Length:" in fake_proc.stdin.data


@pytest.mark.asyncio
async def test_copilot_usage_probe_standalone_malformed_lsp_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_proc = _FakeSubprocess()
    fake_proc.stdout = _FakeLspStream(b"not an lsp frame\r\n\r\n")  # type: ignore[assignment]
    _install_subprocess_double(monkeypatch, copilot_module, [fake_proc])

    probe = CopilotUsageProbe()
    snapshot = await probe.fetch_usage()
    assert snapshot is None


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
    assert _is_genuine_copilot_exhaustion("429 Too Many Requests") is False
    assert _is_genuine_copilot_exhaustion("rate limit exceeded") is False
    assert _is_genuine_copilot_exhaustion("AI credits exhausted") is True
    assert _is_genuine_copilot_exhaustion("Quota exceeded") is True
    assert _is_genuine_copilot_exhaustion("insufficient credits") is True
    assert _is_genuine_copilot_exhaustion("premium requests exhausted") is True
    assert _is_genuine_copilot_exhaustion("usage limit reached") is True


def test_exhausted_copilot_pool_blocks_all_copilot_profiles() -> None:
    cfg = _parse_config("""
    tracker: { kind: file }
    agent:
      kind: copilot
      max_concurrent_agents: 5
    usage_pools:
      copilot:
        source: copilot
        caps:
          monthly: 80
    agent_profiles:
      copilot-builder:
        kind: copilot
      copilot-reviewer:
        kind: copilot
    """)
    orch = _orch(cfg)
    orch._usage_manager.set_snapshot(
        "copilot",
        ProviderUsageSnapshot(
            pool_id="copilot",
            source="copilot",
            windows={
                "monthly": UsageWindow(
                    key="monthly",
                    used_percent=10.0,
                    remaining_percent=90.0,
                )
            },
            hard_limit_reached=True,
            authoritative=True,
        ),
    )
    builder_issue = _issue("TASK-1", agent_profile="copilot-builder")
    reviewer_issue = _issue("TASK-2", agent_profile="copilot-reviewer")

    b_decision = orch._eligibility_decision(builder_issue, cfg, owning_retry=False)
    r_decision = orch._eligibility_decision(reviewer_issue, cfg, owning_retry=False)

    assert b_decision.disposition is _EligibilityDisposition.WAIT_NON_SLOT
    assert b_decision.code == "waiting_provider_usage"
    assert r_decision.disposition is _EligibilityDisposition.WAIT_NON_SLOT
    assert r_decision.code == "waiting_provider_usage"


def test_configured_copilot_cap_blocks_new_dispatch() -> None:
    cfg = _parse_config("""
    tracker: { kind: file }
    agent:
      kind: copilot
    usage_pools:
      copilot:
        source: copilot
        caps:
          monthly: 80
    """)
    orch = _orch(cfg)
    orch._usage_manager.set_snapshot(
        "copilot",
        ProviderUsageSnapshot(
            pool_id="copilot",
            source="copilot",
            windows={
                "monthly": UsageWindow(
                    key="monthly",
                    used_percent=85.0,
                    remaining_percent=15.0,
                )
            },
            authoritative=True,
        ),
    )
    issue = _issue("TASK-1")
    decision = orch._eligibility_decision(issue, cfg, owning_retry=False)
    assert decision.disposition is _EligibilityDisposition.WAIT_NON_SLOT
    assert decision.code == "waiting_provider_usage"
    assert "monthly" in decision.reason


@pytest.mark.asyncio
async def test_running_copilot_worker_is_not_cancelled_when_cap_crossed() -> None:
    cfg = _parse_config("""
    tracker: { kind: file }
    agent:
      kind: copilot
      max_concurrent_agents: 5
    usage_pools:
      copilot:
        source: copilot
        caps:
          monthly: 80
    """)
    orch = _orch(cfg)
    running_issue = _issue("TASK-1")
    worker_task = asyncio.create_task(asyncio.sleep(10))

    orch._running[running_issue.id] = RunningEntry(
        issue=running_issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=worker_task,
        workspace_path=Path("/tmp/ws-task-1"),
    )

    # Usage crosses configured monthly cap while worker is running
    orch._usage_manager.set_snapshot(
        "copilot",
        ProviderUsageSnapshot(
            pool_id="copilot",
            source="copilot",
            windows={
                "monthly": UsageWindow(
                    key="monthly",
                    used_percent=85.0,
                    remaining_percent=15.0,
                )
            },
            authoritative=True,
        ),
    )

    # Running worker must not be cancelled
    assert not worker_task.done()
    assert not worker_task.cancelled()

    # New issue is blocked
    new_issue = _issue("TASK-2")
    decision = orch._eligibility_decision(new_issue, cfg, owning_retry=False)
    assert decision.disposition is _EligibilityDisposition.WAIT_NON_SLOT
    assert decision.code == "waiting_provider_usage"

    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass


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


# ==============================================================================
# §29 Doctor, API, UI & Chat Summarization Tests (Phase 4 / TASK-21)
# ==============================================================================


def test_doctor_detects_copilot_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _parse_config("""
    tracker: { kind: file }
    agent: { kind: copilot }
    copilot:
      command: python
    """)
    cli_result = check_agent_cli(cfg)
    assert cli_result.status == "pass"

    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "ghp_secret_token_123")
    result = check_copilot_auth(cfg)
    assert result.status == "pass"
    assert "COPILOT_GITHUB_TOKEN present" in result.message
    # Auth credentials must not be printed in doctor output
    assert "ghp_secret_token_123" not in result.message


def test_doctor_handles_copilot_auth_independently_from_pi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copilot_cfg = _parse_config("""
    tracker: { kind: file }
    agent: { kind: copilot }
    """)
    pi_cfg = _parse_config("""
    tracker: { kind: file }
    agent: { kind: pi }
    """)

    # When kind is copilot, check_pi_auth skips
    pi_result = check_pi_auth(copilot_cfg)
    assert pi_result.status == "pass"
    assert "not pi" in pi_result.message

    # When kind is pi, check_copilot_auth skips
    copilot_result = check_copilot_auth(pi_cfg)
    assert copilot_result.status == "pass"
    assert "not copilot" in copilot_result.message


def test_workflow_api_exposes_copilot_supported_kind() -> None:
    assert "copilot" in SUPPORTED_AGENT_KINDS
    assert "copilot" in PROFILE_FIELDS_BY_KIND


def test_chat_agent_selector_contains_copilot() -> None:
    app_js_path = Path(__file__).parent.parent / "src" / "symphony" / "web" / "static" / "app.js"
    content = app_js_path.read_text(encoding="utf-8")
    assert "copilot: 'GitHub Copilot'" in content or 'copilot: "GitHub Copilot"' in content


def test_summarize_copilot_frame_assistant_message() -> None:
    payload = {
        "type": "assistant.message",
        "data": {
            "content": "Work complete and tests pass.",
            "outputTokens": 88,
        },
    }
    frames = _summarize_copilot_frame(payload)
    assert frames == [("agent_message", "Work complete and tests pass.", {})]


def test_summarize_copilot_frame_assistant_delta() -> None:
    payload = {
        "type": "assistant.message_delta",
        "data": {
            "deltaContent": "Refactoring ",
        },
    }
    frames = _summarize_copilot_frame(payload)
    assert frames == [("agent_delta", "Refactoring ", {})]


def test_summarize_copilot_frame_tool_activity() -> None:
    payload = {
        "type": "tool.call",
        "data": {
            "name": "bash",
            "args": {"command": "pytest"},
        },
    }
    frames = _summarize_copilot_frame(payload)
    assert len(frames) == 1
    assert frames[0][0] == "tool_activity"
    assert frames[0][1] == "bash"
    assert "pytest" in frames[0][2]["detail"]


def test_summarize_copilot_frame_session_error() -> None:
    payload = {
        "type": "session.error",
        "data": {
            "message": "Connection to model timed out",
        },
    }
    frames = _summarize_copilot_frame(payload)
    assert frames == [("tool_activity", "error", {"detail": "Connection to model timed out"})]


def test_summarize_copilot_frame_ephemeral_and_unknown_ignored() -> None:
    assert _summarize_copilot_frame({"type": "assistant.turn_start"}) == []
    assert _summarize_copilot_frame({"type": "assistant.turn_end"}) == []
    assert _summarize_copilot_frame({"type": "model.call_start"}) == []
    assert _summarize_copilot_frame({"type": "session.mcp_servers_loaded"}) == []
    assert _summarize_copilot_frame({"type": "assistant.idle"}) == []
    assert _summarize_copilot_frame({"type": "unrecognized_event_type"}) == []
    assert _summarize_copilot_frame({}) == []


def test_summarize_frame_dispatches_copilot() -> None:
    payload = {
        "type": "assistant.message",
        "data": {"content": "Hello from Copilot"},
    }
    frames = _summarize_frame("copilot", payload)
    assert frames == [("agent_message", "Hello from Copilot", {})]

