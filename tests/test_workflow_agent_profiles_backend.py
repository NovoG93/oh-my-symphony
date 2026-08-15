"""Phase 3 backend support unit tests for named agent profiles (TASK-6).

Covers:
- Codex: profile model + reasoning_effort reach CLI turn parameters
- Codex: command override works and inherited command remains intact
- Claude: _inject_model helper functions across variations (direct, wrapper, quoting)
- Claude: profile model injects --model into command during run_turn
- Claude: profile command override works and inherited command remains intact
- Claude: resume_across_turns and timeout inheritance from global config
- Session scoping: distinct sessions per profile across stage transitions on same backend
"""

from __future__ import annotations

import asyncio
import contextlib
import shlex
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from symphony.backends import (
    BackendInit,
    TurnResult,
)
from symphony.backends.claude_code import ClaudeCodeBackend, _inject_model
from symphony.backends.codex import CodexAppServerBackend
from symphony.issue import Issue
from symphony.orchestrator import Orchestrator, RunningEntry
from symphony.workflow import (
    AgentConfig,
    AgentProfileConfig,
    AgentSelection,
    ClaudeConfig,
    CodexConfig,
    GeminiConfig,
    ServiceConfig,
    TrackerConfig,
    resolve_agent_config,
)
from symphony.workflow.config import HooksConfig, PiConfig, ServerConfig


def _make_backend_test_cfg(
    *,
    kind: str = "codex",
    agent_profiles: dict[str, AgentProfileConfig] | None = None,
    stage_profiles: dict[str, str] | None = None,
    codex_command: str = "codex app-server",
    codex_model: str = "gpt-5.5",
    codex_reasoning: str = "high",
    claude_command: str = "claude -p --output-format stream-json --verbose",
    claude_model: str = "",
    claude_resume: bool = True,
) -> ServiceConfig:
    agent_cfg = AgentConfig(
        kind=kind,
        max_concurrent_agents=1,
        max_turns=100,
        max_retry_backoff_ms=300_000,
        max_concurrent_agents_by_state={},
        stage_profiles=stage_profiles or {},
    )
    codex_cfg = CodexConfig(
        command=codex_command,
        approval_policy="auto",
        thread_sandbox="none",
        turn_sandbox_policy="none",
        turn_timeout_ms=3_600_000,
        read_timeout_ms=20_000,
        stall_timeout_ms=300_000,
        model=codex_model,
        reasoning_effort=codex_reasoning,
    )
    claude_cfg = ClaudeConfig(
        command=claude_command,
        turn_timeout_ms=3_600_000,
        read_timeout_ms=20_000,
        stall_timeout_ms=300_000,
        resume_across_turns=claude_resume,
        model=claude_model,
    )
    return ServiceConfig(
        workflow_path=Path("/tmp/WORKFLOW.md"),
        poll_interval_ms=1000,
        workspace_root=Path("/tmp/workspaces"),
        tracker=TrackerConfig(
            kind="file",
            endpoint="",
            api_key="",
            project_slug="",
            active_states=("Plan", "Build", "Review"),
            terminal_states=("Done", "Blocked"),
        ),
        hooks=HooksConfig(None, None, None, None, 60_000),
        agent=agent_cfg,
        codex=codex_cfg,
        claude=claude_cfg,
        gemini=GeminiConfig(
            command="gemini",
            turn_timeout_ms=3_600_000,
            read_timeout_ms=20_000,
            stall_timeout_ms=300_000,
        ),
        pi=PiConfig(
            command="pi",
            turn_timeout_ms=3_600_000,
            read_timeout_ms=20_000,
            stall_timeout_ms=300_000,
            resume_across_turns=True,
        ),
        server=ServerConfig(port=None),
        agent_profiles=agent_profiles or {},
    )


async def _noop_event(ev: str, payload: dict[str, Any]) -> None:
    pass


# ---------------------------------------------------------------------------
# 1. Codex profile model + reasoning_effort + command overrides
# ---------------------------------------------------------------------------


def test_codex_profile_model_and_reasoning_effort_in_turn_params(tmp_path: Path) -> None:
    profiles = {
        "sol-planner": AgentProfileConfig(
            name="sol-planner",
            kind="codex",
            model="gpt-5.6-sol",
            reasoning_effort="high",
        ),
    }
    cfg = _make_backend_test_cfg(agent_profiles=profiles)
    selection = AgentSelection(kind="codex", profile="sol-planner")
    resolved = resolve_agent_config(cfg, selection)
    assert resolved.codex is not None
    assert resolved.codex.model == "gpt-5.6-sol"
    assert resolved.codex.reasoning_effort == "high"

    backend = CodexAppServerBackend(
        BackendInit(
            cfg=cfg,
            cwd=tmp_path,
            workspace_root=tmp_path,
            on_event=_noop_event,
            selection=selection,
            resolved_backend_config=resolved.codex,
        )
    )
    params = backend._build_turn_params("Implement feature")
    assert params["model"] == "gpt-5.6-sol"
    assert params["effort"] == "high"


def test_codex_inherited_command_and_profile_command_override(tmp_path: Path) -> None:
    profiles = {
        "default-codex": AgentProfileConfig(
            name="default-codex",
            kind="codex",
            model="gpt-5.6-sol",
        ),
        "custom-codex": AgentProfileConfig(
            name="custom-codex",
            kind="codex",
            command="custom-codex app-server --danger",
            model="gpt-5.6-sol",
        ),
    }
    cfg = _make_backend_test_cfg(
        codex_command="codex app-server",
        agent_profiles=profiles,
    )

    # Inherited command
    sel_inherited = AgentSelection(kind="codex", profile="default-codex")
    resolved_inherited = resolve_agent_config(cfg, sel_inherited)
    backend_inherited = CodexAppServerBackend(
        BackendInit(
            cfg=cfg,
            cwd=tmp_path,
            workspace_root=tmp_path,
            on_event=_noop_event,
            selection=sel_inherited,
            resolved_backend_config=resolved_inherited.codex,
        )
    )
    cmd, _ = backend_inherited._prepare_command_and_env()
    assert cmd == "codex app-server"

    # Overridden command
    sel_custom = AgentSelection(kind="codex", profile="custom-codex")
    resolved_custom = resolve_agent_config(cfg, sel_custom)
    backend_custom = CodexAppServerBackend(
        BackendInit(
            cfg=cfg,
            cwd=tmp_path,
            workspace_root=tmp_path,
            on_event=_noop_event,
            selection=sel_custom,
            resolved_backend_config=resolved_custom.codex,
        )
    )
    cmd_custom, _ = backend_custom._prepare_command_and_env()
    assert cmd_custom == "custom-codex app-server --danger"


# ---------------------------------------------------------------------------
# 2. Claude _inject_model helper & CLI model injection
# ---------------------------------------------------------------------------


def test_claude_inject_model_helper_cases() -> None:
    # Direct claude command
    assert (
        _inject_model("claude -p --output-format stream-json --verbose", "sonnet")
        == "claude --model sonnet -p --output-format stream-json --verbose"
    )
    assert shlex.split(_inject_model("claude -p", "claude-3-7-sonnet-20250219")) == [
        "claude",
        "--model",
        "claude-3-7-sonnet-20250219",
        "-p",
    ]

    # Preserves leading whitespace
    assert _inject_model("  claude -p", "opus") == "  claude --model opus -p"

    # Preserves pipelines and redirects
    assert (
        _inject_model("claude -p | tee /tmp/log.txt", "sonnet")
        == "claude --model sonnet -p | tee /tmp/log.txt"
    )

    # Empty model is a no-op
    assert _inject_model("claude -p --verbose", "") == "claude -p --verbose"

    # Wrapper script without literal claude token is left intact
    assert (
        _inject_model("./scripts/run-claude-wrapper.sh --verbose", "sonnet")
        == "./scripts/run-claude-wrapper.sh --verbose"
    )

    # Quotes model names containing special characters or spaces
    assert _inject_model("claude -p", "custom model/1") == "claude --model 'custom model/1' -p"


@pytest.mark.asyncio
async def test_claude_profile_model_injected_in_run_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = {
        "sonnet-builder": AgentProfileConfig(
            name="sonnet-builder",
            kind="claude",
            model="sonnet",
        ),
    }
    cfg = _make_backend_test_cfg(agent_profiles=profiles)
    selection = AgentSelection(kind="claude", profile="sonnet-builder")
    resolved = resolve_agent_config(cfg, selection)
    assert resolved.claude is not None
    assert resolved.claude.model == "sonnet"

    backend = ClaudeCodeBackend(
        BackendInit(
            cfg=cfg,
            cwd=tmp_path,
            workspace_root=tmp_path,
            on_event=_noop_event,
            selection=selection,
            resolved_backend_config=resolved.claude,
        )
    )

    captured_cmds: list[str] = []

    class _FakeStdin:
        def write(self, data: bytes) -> None:
            pass

        async def drain(self) -> None:
            pass

        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

    class _FakeProcess:
        def __init__(self) -> None:
            self.pid = 12345
            self.returncode = 0
            self.stdin = _FakeStdin()
            self.stdout = asyncio.StreamReader()
            # Feed valid result JSON so run_turn completes cleanly
            result_json = b'{"type":"result","subtype":"success","is_error":false,"result":"ok","usage":{"input_tokens":10,"output_tokens":5}}\n'
            self.stdout.feed_data(result_json)
            self.stdout.feed_eof()
            self.stderr = asyncio.StreamReader()
            self.stderr.feed_eof()

        async def wait(self) -> int:
            return 0

    async def _fake_exec(*args: Any, **kwargs: Any) -> Any:
        # args: (resolve_bash(), "-lc", cmd)
        cmd_idx = args.index("-lc") + 1 if "-lc" in args else 2
        captured_cmds.append(args[cmd_idx])
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    res = await backend.run_turn(prompt="Build it", is_continuation=False)
    assert res.status == "turn_completed"
    assert len(captured_cmds) == 1
    assert "--model sonnet" in captured_cmds[0]
    assert captured_cmds[0].startswith("claude --model sonnet ")


def test_claude_inherited_command_and_profile_override(tmp_path: Path) -> None:
    profiles = {
        "default-claude": AgentProfileConfig(
            name="default-claude",
            kind="claude",
            model="sonnet",
        ),
        "custom-claude": AgentProfileConfig(
            name="custom-claude",
            kind="claude",
            command="claude --dangerously-skip-permissions",
            model="sonnet",
        ),
    }
    cfg = _make_backend_test_cfg(
        claude_command="claude -p --verbose",
        agent_profiles=profiles,
    )

    # Inherited command
    sel1 = AgentSelection(kind="claude", profile="default-claude")
    res1 = resolve_agent_config(cfg, sel1)
    b1 = ClaudeCodeBackend(
        BackendInit(
            cfg=cfg,
            cwd=tmp_path,
            workspace_root=tmp_path,
            on_event=_noop_event,
            selection=sel1,
            resolved_backend_config=res1.claude,
        )
    )
    assert b1._claude.command == "claude -p --verbose"

    # Overridden command
    sel2 = AgentSelection(kind="claude", profile="custom-claude")
    res2 = resolve_agent_config(cfg, sel2)
    b2 = ClaudeCodeBackend(
        BackendInit(
            cfg=cfg,
            cwd=tmp_path,
            workspace_root=tmp_path,
            on_event=_noop_event,
            selection=sel2,
            resolved_backend_config=res2.claude,
        )
    )
    assert b2._claude.command == "claude --dangerously-skip-permissions"


def test_claude_resume_and_timeout_inheritance(tmp_path: Path) -> None:
    profiles = {
        "inheriting": AgentProfileConfig(
            name="inheriting",
            kind="claude",
            model="haiku",
        ),
        "no-resume": AgentProfileConfig(
            name="no-resume",
            kind="claude",
            model="haiku",
            resume_across_turns=False,
            turn_timeout_ms=120_000,
        ),
    }
    cfg = _make_backend_test_cfg(
        claude_resume=True,
        agent_profiles=profiles,
    )

    # Inheriting profile
    res_inh = resolve_agent_config(cfg, AgentSelection(kind="claude", profile="inheriting"))
    assert res_inh.claude is not None
    assert res_inh.claude.resume_across_turns is True
    assert res_inh.claude.turn_timeout_ms == 3_600_000
    assert res_inh.claude.model == "haiku"

    # Overriding profile
    res_over = resolve_agent_config(cfg, AgentSelection(kind="claude", profile="no-resume"))
    assert res_over.claude is not None
    assert res_over.claude.resume_across_turns is False
    assert res_over.claude.turn_timeout_ms == 120_000
    assert res_over.claude.model == "haiku"


# ---------------------------------------------------------------------------
# 3. Session scoping by ticket + backend kind + profile
# ---------------------------------------------------------------------------


@dataclass
class _TrackingBackend:
    init: BackendInit
    created_sessions: list[str] = field(default_factory=list)
    resumed_sessions: list[str] = field(default_factory=list)
    turn_prompts: list[str] = field(default_factory=list)
    is_continuations: list[bool] = field(default_factory=list)

    @property
    def pid(self) -> int | None:
        return None

    async def start(self) -> None:
        pass

    async def initialize(self) -> dict[str, Any]:
        return {}

    async def start_session(self, *, initial_prompt: str, issue_title: str | None) -> str:
        model = getattr(self.init.resolved_backend_config, "model", "default")
        sess_id = f"session-{self.init.selection.kind}-{self.init.selection.profile}-{model}"
        self.created_sessions.append(sess_id)
        return sess_id

    async def resume_session(self, session_id: str) -> bool:
        self.resumed_sessions.append(session_id)
        return True

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> TurnResult:
        self.turn_prompts.append(prompt)
        self.is_continuations.append(is_continuation)
        return TurnResult(status="completed", turn_id="t-1", last_message="done")

    async def stop(self) -> None:
        pass


class _TestWorkspace:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.workspace_key = "test"
        self.created_now = True


class _TestWorkspaceManager:
    def __init__(self, path: Path) -> None:
        self.path = path

    def path_for(self, identifier: str) -> Path:
        return self.path

    async def create_or_reuse(self, identifier: str) -> _TestWorkspace:
        return _TestWorkspace(self.path)

    async def before_run(self, path: Path) -> None:
        pass

    async def after_run(self, path: Path) -> None:
        pass

    async def after_run_best_effort(self, path: Path) -> None:
        pass

    async def after_done(self, path: Path) -> None:
        pass


def test_session_scoping_different_profiles_same_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distinct profiles on the same backend kind (e.g. Codex/Sol vs Codex/Luna)

    must start separate sessions and not cross-resume.
    """
    from datetime import datetime, timezone
    import symphony.orchestrator.core as core_mod
    from symphony.workflow.state import WorkflowState

    profiles = {
        "sol-planner": AgentProfileConfig(name="sol-planner", kind="codex", model="gpt-5.6-sol"),
        "luna-reviewer": AgentProfileConfig(name="luna-reviewer", kind="codex", model="gpt-5.6-luna"),
    }
    cfg = _make_backend_test_cfg(
        kind="codex",
        stage_profiles={"plan": "sol-planner", "review": "luna-reviewer"},
        agent_profiles=profiles,
    )
    issue = Issue(
        id="iss-scope-1",
        identifier="TASK-99",
        title="Session scoping test",
        description=(
            "## Plan\n- plan\n\n## Acceptance Tests\n- t\n\n## Done Signals\n- ok\n\n"
            "## Implementation\n- imp\n\n## Self-Critique\n- c\n\n"
            "## Security Audit\n| c | v | e |\n|---|---|---|\n| a | pass | n |\n\n"
            "## Review\nok\n\n## QA Evidence\n- e\n\n## AC Scorecard\n| a | b | c | d |\n| - | - | - | - |\n| 1 | 2 | pass | 4 |\n\n"
            "## Merge Status\nm\n\n## Wiki Updates\n- w\n\n## Human Review\nr\n"
        ),
        priority=1,
        state="Plan",
    )

    orch = Orchestrator(WorkflowState(Path("/tmp/no.md")))
    orch._workspace_manager = _TestWorkspaceManager(tmp_path)  # type: ignore[assignment]
    (tmp_path / "docs" / issue.identifier / "work").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / issue.identifier / "work" / "notes.md").write_text("ok")
    (tmp_path / "docs" / issue.identifier / "qa").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / issue.identifier / "qa" / "version.log").write_text("ok")

    orch._running[issue.id] = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=tmp_path,
        release_authority_resolved=True,
    )

    tracked_backends: list[_TrackingBackend] = []

    def _fake_build(init: BackendInit) -> _TrackingBackend:
        b = _TrackingBackend(init=init)
        tracked_backends.append(b)
        return b

    monkeypatch.setattr(core_mod, "build_backend", _fake_build)

    # State transitions: Plan -> Review -> Done
    seq = ["Review", "Done"]
    idx = 0

    async def _fake_refresh(c: ServiceConfig, running_id: str) -> Issue:
        nonlocal idx
        st = seq[idx]
        idx = min(idx + 1, len(seq) - 1)
        cur = orch._running[running_id].issue
        return replace(cur, state=st)

    monkeypatch.setattr(orch, "_refresh_issue_state", _fake_refresh)

    asyncio.run(orch._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert len(tracked_backends) == 2

    # Backend 1: Plan (Codex / Sol)
    b1 = tracked_backends[0]
    assert b1.init.selection == AgentSelection(kind="codex", profile="sol-planner")
    assert b1.created_sessions == ["session-codex-sol-planner-gpt-5.6-sol"]
    assert b1.resumed_sessions == []  # fresh session

    # Backend 2: Review (Codex / Luna)
    b2 = tracked_backends[1]
    assert b2.init.selection == AgentSelection(kind="codex", profile="luna-reviewer")
    assert b2.created_sessions == ["session-codex-luna-reviewer-gpt-5.6-luna"]
    assert b2.resumed_sessions == []  # fresh session, did NOT resume b1's session


def test_session_scoping_claude_models_distinct_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Distinct Claude profiles (e.g. Sonnet build vs Haiku review) start distinct sessions."""
    from datetime import datetime, timezone
    import symphony.orchestrator.core as core_mod
    from symphony.workflow.state import WorkflowState

    profiles = {
        "sonnet-builder": AgentProfileConfig(name="sonnet-builder", kind="claude", model="sonnet"),
        "haiku-reviewer": AgentProfileConfig(name="haiku-reviewer", kind="claude", model="haiku"),
    }
    cfg = _make_backend_test_cfg(
        kind="claude",
        stage_profiles={"plan": "sonnet-builder", "review": "haiku-reviewer"},
        agent_profiles=profiles,
    )
    issue = Issue(
        id="iss-scope-2",
        identifier="TASK-99",
        title="Session scoping claude test",
        description=(
            "## Plan\n- plan\n\n## Acceptance Tests\n- t\n\n## Done Signals\n- ok\n\n"
            "## Implementation\n- imp\n\n## Self-Critique\n- c\n\n"
            "## Security Audit\n| c | v | e |\n|---|---|---|\n| a | pass | n |\n\n"
            "## Review\nok\n\n## QA Evidence\n- e\n\n## AC Scorecard\n| a | b | c | d |\n| - | - | - | - |\n| 1 | 2 | pass | 4 |\n\n"
            "## Merge Status\nm\n\n## Wiki Updates\n- w\n\n## Human Review\nr\n"
        ),
        priority=1,
        state="Plan",
    )

    orch = Orchestrator(WorkflowState(Path("/tmp/no.md")))
    orch._workspace_manager = _TestWorkspaceManager(tmp_path)  # type: ignore[assignment]
    (tmp_path / "docs" / issue.identifier / "work").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / issue.identifier / "work" / "notes.md").write_text("ok")
    (tmp_path / "docs" / issue.identifier / "qa").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / issue.identifier / "qa" / "version.log").write_text("ok")

    orch._running[issue.id] = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=None,
        worker_task=None,  # type: ignore[arg-type]
        workspace_path=tmp_path,
        release_authority_resolved=True,
    )

    tracked_backends: list[_TrackingBackend] = []

    def _fake_build(init: BackendInit) -> _TrackingBackend:
        b = _TrackingBackend(init=init)
        tracked_backends.append(b)
        return b

    monkeypatch.setattr(core_mod, "build_backend", _fake_build)

    seq = ["Review", "Done"]
    idx = 0

    async def _fake_refresh(c: ServiceConfig, running_id: str) -> Issue:
        nonlocal idx
        st = seq[idx]
        idx = min(idx + 1, len(seq) - 1)
        cur = orch._running[running_id].issue
        return replace(cur, state=st)

    monkeypatch.setattr(orch, "_refresh_issue_state", _fake_refresh)

    asyncio.run(orch._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert len(tracked_backends) == 2
    assert tracked_backends[0].created_sessions == ["session-claude-sonnet-builder-sonnet"]
    assert tracked_backends[1].created_sessions == ["session-claude-haiku-reviewer-haiku"]
    assert tracked_backends[1].resumed_sessions == []


async def test_dispatch_refuses_ambiguous_and_unknown_agent_profile_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_dispatch must catch ConfigValidationError, log error, and return False instead of raising."""
    from symphony.workflow.state import WorkflowState

    profiles = {
        "fast-coder": AgentProfileConfig(name="fast-coder", kind="codex", model="gpt-5.5"),
    }
    cfg = _make_backend_test_cfg(
        kind="codex",
        agent_profiles=profiles,
    )
    orch = Orchestrator(WorkflowState(Path("/tmp/workflow.md")))
    orch._workspace_manager = _TestWorkspaceManager(tmp_path)  # type: ignore[assignment]

    # 1. Ambiguous override: both agent_kind and agent_profile set
    issue_ambiguous = Issue(
        id="iss-ambiguous",
        identifier="TASK-AMBIGUOUS",
        title="Ambiguous ticket",
        description="## Plan\n- do thing",
        priority=1,
        state="In Progress",
        agent_kind="claude",
        agent_profile="fast-coder",
    )
    started_ambiguous = orch._dispatch(issue_ambiguous, cfg, attempt=None)
    assert started_ambiguous is False
    assert "iss-ambiguous" not in orch._running

    # 2. Unknown profile: agent_profile not present in non-empty agent_profiles
    issue_unknown = Issue(
        id="iss-unknown",
        identifier="TASK-UNKNOWN",
        title="Unknown profile ticket",
        description="## Plan\n- do thing",
        priority=1,
        state="In Progress",
        agent_profile="nonexistent-profile",
    )
    started_unknown = orch._dispatch(issue_unknown, cfg, attempt=None)
    assert started_unknown is False
    assert "iss-unknown" not in orch._running

    # 3. Valid profile dispatches without raising ConfigValidationError
    issue_valid = Issue(
        id="iss-valid",
        identifier="TASK-VALID",
        title="Valid profile ticket",
        description="## Plan\n- do thing",
        priority=1,
        state="In Progress",
        agent_profile="fast-coder",
    )
    mock_run_attempt = AsyncMock()
    monkeypatch.setattr(orch, "_run_agent_attempt", mock_run_attempt)
    started_valid = orch._dispatch(issue_valid, cfg, attempt=None)
    assert started_valid is True
    assert "iss-valid" in orch._running
    entry = orch._running.get("iss-valid")
    if entry and entry.worker_task:
        entry.worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await entry.worker_task


