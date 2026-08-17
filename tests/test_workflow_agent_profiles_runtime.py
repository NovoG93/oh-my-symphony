"""Phase 2 runtime resolution tests for named agent profiles (TASK-5).

Covers:
- AgentSelection dataclass
- selection_for_state 8-tier precedence hierarchy
- Ambiguous ticket override rejection (both agent_kind and agent_profile)
- Central resolve_agent_config overlay with immutable dataclass replacement
- BackendInit carrying selection + resolved_backend_config
- Stage transition lifecycle re-resolving profile on state change
- Backward compatibility for legacy workflows without profiles
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from symphony.backends import BackendInit
from symphony.errors import ConfigValidationError
from symphony.issue import Issue
from symphony.orchestrator.helpers import _config_for_issue_agent
from symphony.trackers.file import issue_from_file
from symphony.workflow import (
    AgentConfig,
    AgentProfileConfig,
    AgentSelection,
    ClaudeConfig,
    CodexConfig,
    GeminiConfig,
    ResolvedAgentConfig,
    ServiceConfig,
    TrackerConfig,
    resolve_agent_config,
)


def _make_service_config(
    *,
    kind: str = "codex",
    default_profile: str | None = None,
    stage_profiles: dict[str, str] | None = None,
    stage_kinds: dict[str, str] | None = None,
    agent_profiles: dict[str, AgentProfileConfig] | None = None,
    codex_command: str = "codex app-server",
    codex_model: str = "gpt-5.5",
    codex_reasoning: str = "high",
    claude_command: str = "claude -p",
    claude_model: str = "",
) -> ServiceConfig:
    from symphony.workflow.config import (
        HooksConfig,
        PiConfig,
        ServerConfig,
    )

    agent_cfg = AgentConfig(
        kind=kind,
        max_concurrent_agents=1,
        max_turns=100,
        max_retry_backoff_ms=300_000,
        max_concurrent_agents_by_state={},
        default_profile=default_profile,
        stage_profiles=stage_profiles or {},
        stage_kinds=stage_kinds or {},
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
        resume_across_turns=True,
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
            active_states=("Todo", "Plan", "Build", "Review", "QA"),
            terminal_states=("Done", "Blocked"),
        ),
        hooks=HooksConfig(
            after_create=None,
            before_run=None,
            after_run=None,
            before_remove=None,
            timeout_ms=60_000,
        ),
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


# ---------------------------------------------------------------------------
# AC1: Precedence hierarchy
# ---------------------------------------------------------------------------


def test_agent_selection_dataclass() -> None:
    sel = AgentSelection(kind="codex", profile="sol-planner")
    assert sel.kind == "codex"
    assert sel.profile == "sol-planner"
    with pytest.raises(Exception):
        sel.kind = "claude"  # type: ignore[misc]


def test_precedence_tier1_explicit_dispatch_profile_wins() -> None:
    profiles = {
        "sol-planner": AgentProfileConfig(name="sol-planner", kind="codex", model="sol"),
        "sonnet-builder": AgentProfileConfig(name="sonnet-builder", kind="claude", model="sonnet"),
    }
    cfg = _make_service_config(
        kind="claude",
        default_profile="sonnet-builder",
        stage_profiles={"plan": "sonnet-builder"},
        stage_kinds={"plan": "claude"},
        agent_profiles=profiles,
    )
    sel = cfg.selection_for_state(
        "Plan",
        ticket_profile="sonnet-builder",
        dispatch_profile="sol-planner",
        dispatch_kind="agy",
    )
    assert sel == AgentSelection(kind="codex", profile="sol-planner")


def test_precedence_tier2_explicit_dispatch_kind_wins_over_ticket() -> None:
    profiles = {
        "sonnet-builder": AgentProfileConfig(name="sonnet-builder", kind="claude", model="sonnet"),
    }
    cfg = _make_service_config(
        kind="codex",
        stage_profiles={"plan": "sonnet-builder"},
        agent_profiles=profiles,
    )
    sel = cfg.selection_for_state(
        "Plan",
        ticket_profile="sonnet-builder",
        dispatch_kind="agy",
    )
    assert sel == AgentSelection(kind="agy", profile=None)


def test_precedence_tier3_ticket_agent_profile_wins_over_stage() -> None:
    profiles = {
        "sol-planner": AgentProfileConfig(name="sol-planner", kind="codex", model="sol"),
        "sonnet-builder": AgentProfileConfig(name="sonnet-builder", kind="claude", model="sonnet"),
    }
    cfg = _make_service_config(
        kind="claude",
        stage_profiles={"plan": "sonnet-builder"},
        agent_profiles=profiles,
    )
    sel = cfg.selection_for_state("Plan", ticket_profile="sol-planner")
    assert sel == AgentSelection(kind="codex", profile="sol-planner")


def test_precedence_tier4_ticket_agent_kind_wins_over_stage_profile() -> None:
    profiles = {
        "sonnet-builder": AgentProfileConfig(name="sonnet-builder", kind="claude", model="sonnet"),
    }
    cfg = _make_service_config(
        kind="codex",
        stage_profiles={"plan": "sonnet-builder"},
        agent_profiles=profiles,
    )
    sel = cfg.selection_for_state("Plan", ticket_kind="gemini")
    assert sel == AgentSelection(kind="gemini", profile=None)


def test_precedence_tier5_stage_profile_wins_over_stage_kind() -> None:
    profiles = {
        "sol-planner": AgentProfileConfig(name="sol-planner", kind="codex", model="sol"),
    }
    cfg = _make_service_config(
        kind="claude",
        stage_profiles={"plan": "sol-planner"},
        stage_kinds={"plan": "claude"},
        agent_profiles=profiles,
    )
    sel = cfg.selection_for_state("Plan")
    assert sel == AgentSelection(kind="codex", profile="sol-planner")


def test_precedence_tier6_stage_kind_wins_over_default_profile() -> None:
    profiles = {
        "sonnet-builder": AgentProfileConfig(name="sonnet-builder", kind="claude", model="sonnet"),
    }
    cfg = _make_service_config(
        kind="claude",
        default_profile="sonnet-builder",
        stage_kinds={"plan": "codex"},
        agent_profiles=profiles,
    )
    sel = cfg.selection_for_state("Plan")
    assert sel == AgentSelection(kind="codex", profile=None)


def test_precedence_tier7_default_profile_wins_over_global_kind() -> None:
    profiles = {
        "sol-planner": AgentProfileConfig(name="sol-planner", kind="codex", model="sol"),
    }
    cfg = _make_service_config(
        kind="claude",
        default_profile="sol-planner",
        agent_profiles=profiles,
    )
    sel = cfg.selection_for_state("UnmappedStage")
    assert sel == AgentSelection(kind="codex", profile="sol-planner")


def test_precedence_tier8_global_kind_fallback() -> None:
    cfg = _make_service_config(kind="agy")
    sel = cfg.selection_for_state("UnmappedStage")
    assert sel == AgentSelection(kind="agy", profile=None)


# ---------------------------------------------------------------------------
# AC2: resolve_agent_config overlays non-null profile fields & keeps command
# ---------------------------------------------------------------------------


def test_resolve_agent_config_overlay_codex() -> None:
    profiles = {
        "sol-reviewer": AgentProfileConfig(
            name="sol-reviewer",
            kind="codex",
            model="sol",
            reasoning_effort="high",
            turn_timeout_ms=120_000,
        ),
    }
    cfg = _make_service_config(
        kind="codex",
        codex_command="codex app-server --flag",
        codex_model="gpt-5.5",
        codex_reasoning="medium",
        agent_profiles=profiles,
    )
    sel = AgentSelection(kind="codex", profile="sol-reviewer")
    resolved = resolve_agent_config(cfg, sel)

    assert isinstance(resolved, ResolvedAgentConfig)
    assert resolved.kind == "codex"
    assert resolved.profile_name == "sol-reviewer"
    assert resolved.codex is not None
    assert resolved.codex.model == "sol"
    assert resolved.codex.reasoning_effort == "high"
    assert resolved.codex.turn_timeout_ms == 120_000
    # Inherited command remains intact
    assert resolved.codex.command == "codex app-server --flag"
    assert resolved.codex.stall_timeout_ms == 300_000


def test_resolve_agent_config_overlay_command_override() -> None:
    profiles = {
        "custom-codex": AgentProfileConfig(
            name="custom-codex",
            kind="codex",
            command="custom-codex-cli app-server",
        ),
    }
    cfg = _make_service_config(
        kind="codex",
        codex_command="codex app-server",
        agent_profiles=profiles,
    )
    sel = AgentSelection(kind="codex", profile="custom-codex")
    resolved = resolve_agent_config(cfg, sel)
    assert resolved.codex is not None
    assert resolved.codex.command == "custom-codex-cli app-server"
    assert resolved.codex.model == "gpt-5.5"


def test_resolve_agent_config_no_profile_returns_base() -> None:
    cfg = _make_service_config(kind="codex", codex_command="codex app-server")
    sel = AgentSelection(kind="codex", profile=None)
    resolved = resolve_agent_config(cfg, sel)
    assert resolved.codex == cfg.codex


def test_resolve_agent_config_unknown_profile_raises() -> None:
    cfg = _make_service_config(kind="codex")
    sel = AgentSelection(kind="codex", profile="non-existent")
    with pytest.raises(ConfigValidationError, match="unknown"):
        resolve_agent_config(cfg, sel)


# ---------------------------------------------------------------------------
# AC3: BackendInit carries selection + resolved_backend_config
# ---------------------------------------------------------------------------


def test_backend_init_carries_selection_and_resolved_config() -> None:
    profiles = {
        "sol-planner": AgentProfileConfig(name="sol-planner", kind="codex", model="sol"),
    }
    cfg = _make_service_config(kind="codex", agent_profiles=profiles)
    sel = AgentSelection(kind="codex", profile="sol-planner")
    resolved = resolve_agent_config(cfg, sel)

    async def _on_event(_ev: dict[str, Any]) -> None:
        pass

    init = BackendInit(
        cfg=cfg,
        cwd=Path("/tmp"),
        workspace_root=Path("/tmp"),
        on_event=_on_event,
        selection=sel,
        resolved_backend_config=resolved.active_config,
    )
    assert init.selection == sel
    assert init.resolved_backend_config == resolved.codex
    assert isinstance(init.resolved_backend_config, CodexConfig)
    assert init.resolved_backend_config.model == "sol"


def test_backend_init_defaults_selection_when_omitted() -> None:
    async def _on_event(_ev: dict[str, Any]) -> None:
        pass

    cfg = _make_service_config(kind="claude")
    init = BackendInit(
        cfg=cfg,
        cwd=Path("/tmp"),
        workspace_root=Path("/tmp"),
        on_event=_on_event,
    )
    assert init.selection == AgentSelection(kind="claude", profile=None)
    assert init.resolved_backend_config == cfg.claude


# ---------------------------------------------------------------------------
# AC4: Profile/backend is re-resolved on every stage transition
# ---------------------------------------------------------------------------


def test_stage_transition_re_resolves_profile() -> None:
    profiles = {
        "sol-planner": AgentProfileConfig(name="sol-planner", kind="codex", model="sol"),
        "sonnet-builder": AgentProfileConfig(name="sonnet-builder", kind="claude", model="sonnet"),
        "sol-reviewer": AgentProfileConfig(name="sol-reviewer", kind="codex", model="sol", reasoning_effort="high"),
    }
    cfg = _make_service_config(
        kind="claude",
        stage_profiles={
            "plan": "sol-planner",
            "build": "sonnet-builder",
            "review": "sol-reviewer",
        },
        agent_profiles=profiles,
    )

    issue_plan = Issue(id="1", identifier="T-1", title="test", description="", priority=1, state="Plan")
    issue_build = Issue(id="1", identifier="T-1", title="test", description="", priority=1, state="Build")
    issue_review = Issue(id="1", identifier="T-1", title="test", description="", priority=1, state="Review")

    sel_plan = cfg.selection_for_state(issue_plan.state)
    res_plan = resolve_agent_config(cfg, sel_plan)
    assert sel_plan == AgentSelection(kind="codex", profile="sol-planner")
    assert res_plan.codex is not None and res_plan.codex.model == "sol"

    sel_build = cfg.selection_for_state(issue_build.state)
    res_build = resolve_agent_config(cfg, sel_build)
    assert sel_build == AgentSelection(kind="claude", profile="sonnet-builder")
    assert res_build.claude is not None and res_build.claude.model == "sonnet"

    sel_review = cfg.selection_for_state(issue_review.state)
    res_review = resolve_agent_config(cfg, sel_review)
    assert sel_review == AgentSelection(kind="codex", profile="sol-reviewer")
    assert res_review.codex is not None and res_review.codex.reasoning_effort == "high"


def test_stage_transition_same_kind_different_profile() -> None:
    profiles = {
        "sol-planner": AgentProfileConfig(name="sol-planner", kind="codex", model="sol"),
        "luna-qa": AgentProfileConfig(name="luna-qa", kind="codex", model="luna"),
    }
    cfg = _make_service_config(
        kind="codex",
        stage_profiles={"plan": "sol-planner", "qa": "luna-qa"},
        agent_profiles=profiles,
    )
    sel_plan = cfg.selection_for_state("Plan")
    sel_qa = cfg.selection_for_state("QA")
    assert sel_plan == AgentSelection(kind="codex", profile="sol-planner")
    assert sel_qa == AgentSelection(kind="codex", profile="luna-qa")
    assert sel_plan != sel_qa


# ---------------------------------------------------------------------------
# AC5: Tickets setting both agent_kind and agent_profile are rejected
# ---------------------------------------------------------------------------


def test_ambiguous_ticket_override_rejected() -> None:
    cfg = _make_service_config(kind="codex")
    with pytest.raises(ConfigValidationError, match="ambiguous"):
        cfg.selection_for_state(
            "Plan",
            ticket_kind="codex",
            ticket_profile="sonnet-builder",
        )


def test_ambiguous_ticket_override_rejected_in_config_helper() -> None:
    cfg = _make_service_config(kind="codex")
    issue = Issue(
        id="1",
        identifier="T-1",
        title="ambiguous",
        description="",
        priority=1,
        state="Plan",
        agent_kind="codex",
        agent_profile="sonnet-builder",
    )
    with pytest.raises(ConfigValidationError, match="ambiguous"):
        _config_for_issue_agent(cfg, issue)


# ---------------------------------------------------------------------------
# AC6 & AC7: Backward compatibility
# ---------------------------------------------------------------------------


def test_backward_compatibility_legacy_workflow() -> None:
    cfg = _make_service_config(
        kind="claude",
        stage_kinds={"build": "claude", "review": "codex"},
    )
    # No profiles configured
    sel_build = cfg.selection_for_state("Build")
    sel_review = cfg.selection_for_state("Review")
    sel_other = cfg.selection_for_state("Other")

    assert sel_build == AgentSelection(kind="claude", profile=None)
    assert sel_review == AgentSelection(kind="codex", profile=None)
    assert sel_other == AgentSelection(kind="claude", profile=None)

    res_build = resolve_agent_config(cfg, sel_build)
    res_review = resolve_agent_config(cfg, sel_review)

    assert res_build.claude == cfg.claude
    assert res_review.codex == cfg.codex


def test_parse_file_issue_supports_agent_profile(tmp_path: Path) -> None:
    ticket_file = tmp_path / "TASK-99.md"
    ticket_file.write_text(
        "---\n"
        "id: TASK-99\n"
        "identifier: TASK-99\n"
        "title: Test ticket\n"
        "state: Plan\n"
        "agent:\n"
        "  profile: sol-planner\n"
        "---\n"
        "Body content\n"
    )
    issue = issue_from_file(ticket_file)
    assert issue is not None
    assert issue.agent_profile == "sol-planner"
    assert issue.agent_kind is None


def test_parse_file_issue_flat_agent_profile(tmp_path: Path) -> None:
    ticket_file = tmp_path / "TASK-99.md"
    ticket_file.write_text(
        "---\n"
        "id: TASK-99\n"
        "identifier: TASK-99\n"
        "title: Test ticket\n"
        "state: Plan\n"
        "agent_profile: fable-planner\n"
        "---\n"
        "Body content\n"
    )
    issue = issue_from_file(ticket_file)
    assert issue is not None
    assert issue.agent_profile == "fable-planner"


@dataclass
class _RecordingBackend:
    init: BackendInit
    calls: list[str] = field(default_factory=list)

    @property
    def pid(self) -> int | None:
        return None

    async def start(self) -> None:
        self.calls.append("start")

    async def initialize(self) -> dict[str, Any]:
        self.calls.append("initialize")
        return {}

    async def start_session(self, *, initial_prompt: str, issue_title: str | None) -> str:
        self.calls.append("start_session")
        return "sess-1"

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> Any:
        from symphony.backends import TurnResult

        self.calls.append("run_turn")
        return TurnResult(status="completed", turn_id="t-1", last_message="done")

    async def stop(self) -> None:
        self.calls.append("stop")


class _FakeWorkspace:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.workspace_key = "fake"
        self.created_now = True


class _FakeWorkspaceManager:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.after_run_paths: list[Path] = []

    def path_for(self, identifier: str) -> Path:
        return self.path

    async def create_or_reuse(self, identifier: str) -> _FakeWorkspace:
        return _FakeWorkspace(self.path)

    async def before_run(self, path: Path) -> None:
        pass

    async def after_run(self, path: Path) -> None:
        self.after_run_paths.append(path)

    async def after_run_best_effort(self, path: Path) -> None:
        self.after_run_paths.append(path)

    async def after_done(self, path: Path) -> None:
        pass


def test_orchestrator_stage_transition_re_resolves_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from datetime import datetime, timezone
    import symphony.orchestrator.core as core_mod
    from symphony.orchestrator import Orchestrator, RunningEntry
    from symphony.workflow.state import WorkflowState

    profiles = {
        "sol-planner": AgentProfileConfig(name="sol-planner", kind="codex", model="sol"),
        "sonnet-builder": AgentProfileConfig(name="sonnet-builder", kind="claude", model="sonnet"),
    }
    cfg = _make_service_config(
        kind="claude",
        stage_profiles={"plan": "sol-planner", "build": "sonnet-builder"},
        agent_profiles=profiles,
    )
    issue = Issue(
        id="iss-1",
        identifier="T-1",
        title="orchestrator lifecycle test",
        description="## Plan\n- do it\n\n## Acceptance Tests\n- test\n\n## Done Signals\n- ok\n\n## Implementation\n- done\n\n## Self-Critique\n- ok\n\n## Security Audit\n| check | verdict | evidence |\n| --- | --- | --- |\n| sec | pass | na |\n\n## Review\nok\n\n## QA Evidence\n- ok\n\n## AC Scorecard\n| s | src | res | ev |\n| - | - | - | - |\n| a | b | pass | c |\n\n## Merge Status\nmerged\n\n## Wiki Updates\n- doc\n\n## Human Review\nready\n",
        priority=1,
        state="Plan",
    )
    orch = Orchestrator(WorkflowState(Path("/tmp/no.md")))
    orch._workspace_manager = _FakeWorkspaceManager(tmp_path)  # type: ignore[assignment]
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

    created_backends: list[_RecordingBackend] = []

    def _fake_build_backend(init: BackendInit) -> _RecordingBackend:
        b = _RecordingBackend(init=init)
        created_backends.append(b)
        return b

    monkeypatch.setattr(core_mod, "build_backend", _fake_build_backend)

    sequence = ["Build", "Done"]
    idx = 0

    async def _fake_refresh(c: ServiceConfig, running_id: str) -> Issue:
        nonlocal idx
        st = sequence[idx]
        idx = min(idx + 1, len(sequence) - 1)
        cur_issue = orch._running[running_id].issue
        return replace(cur_issue, state=st)

    monkeypatch.setattr(orch, "_refresh_issue_state", _fake_refresh)

    asyncio.run(orch._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert len(created_backends) == 2
    # First backend (Plan phase): resolved to sol-planner (codex/sol)
    assert created_backends[0].init.selection == AgentSelection(kind="codex", profile="sol-planner")
    assert isinstance(created_backends[0].init.resolved_backend_config, CodexConfig)
    assert created_backends[0].init.resolved_backend_config.model == "sol"

    # Second backend (Build phase): resolved to sonnet-builder (claude/sonnet)
    assert created_backends[1].init.selection == AgentSelection(kind="claude", profile="sonnet-builder")
    assert isinstance(created_backends[1].init.resolved_backend_config, ClaudeConfig)
    assert created_backends[1].init.resolved_backend_config.model == "sonnet"


def test_dispatch_logs_profile_model_reasoning_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from datetime import datetime, timezone
    import symphony.orchestrator.core as core_mod
    from symphony.orchestrator import Orchestrator
    from symphony.workflow.state import WorkflowState

    profiles = {
        "sol-planner": AgentProfileConfig(
            name="sol-planner", kind="codex", model="gpt-5.6-sol", reasoning_effort="high"
        ),
    }
    cfg = _make_service_config(
        kind="codex",
        stage_profiles={"plan": "sol-planner"},
        agent_profiles=profiles,
    )
    issue = Issue(
        id="iss-dispatch-log",
        identifier="T-LOG-1",
        title="dispatch log test",
        description="",
        priority=1,
        state="Plan",
    )
    orch = Orchestrator(WorkflowState(Path("/tmp/no.md")))
    orch._workspace_manager = _FakeWorkspaceManager(tmp_path)  # type: ignore[assignment]

    logged_events: list[tuple[str, dict[str, Any]]] = []

    def _fake_log_info(event: str, **kwargs: Any) -> None:
        logged_events.append((event, kwargs))

    monkeypatch.setattr(core_mod.log, "info", _fake_log_info)
    monkeypatch.setattr(core_mod, "build_backend", lambda init: _RecordingBackend(init=init))

    async def _test() -> None:
        # Mock dispatch worker start so it doesn't run full background loop
        def _fake_run_attempt(iss: Issue, attempt: int | None, c: ServiceConfig) -> None:
            pass

        monkeypatch.setattr(orch, "_run_agent_attempt", _fake_run_attempt)

        dispatched = orch._dispatch(issue, cfg, attempt=1)
        assert dispatched is True

    asyncio.run(_test())

    dispatch_logs = [kwargs for event, kwargs in logged_events if event == "dispatch"]
    assert len(dispatch_logs) == 1
    dlog = dispatch_logs[0]
    assert dlog["issue_id"] == "iss-dispatch-log"
    assert dlog["issue_identifier"] == "T-LOG-1"
    assert dlog["attempt"] == 1
    assert dlog["agent_kind"] == "codex"
    assert dlog["agent_profile"] == "sol-planner"
    assert dlog["model"] == "gpt-5.6-sol"
    assert dlog["reasoning_effort"] == "high"


def test_stage_backend_rerouted_logs_same_kind_different_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from datetime import datetime, timezone
    import symphony.orchestrator.core as core_mod
    from symphony.orchestrator import Orchestrator, RunningEntry
    from symphony.workflow.state import WorkflowState

    profiles = {
        "claude-reviewer": AgentProfileConfig(
            name="claude-reviewer", kind="claude", model="deepseek-v4-pro[1m]"
        ),
        "claude-documenter": AgentProfileConfig(
            name="claude-documenter", kind="claude", model="deepseek-v4-flash"
        ),
    }
    cfg = _make_service_config(
        kind="claude",
        stage_profiles={"plan": "claude-reviewer", "build": "claude-documenter"},
        agent_profiles=profiles,
    )
    issue = Issue(
        id="iss-reroute-same-kind",
        identifier="T-REROUTE-1",
        title="reroute log test",
        description="## Plan\n- ok\n\n## Acceptance Tests\n- ok\n\n## Done Signals\n- ok\n\n## Implementation\n- ok\n\n## Self-Critique\n- ok\n\n## Security Audit\n| check | verdict | evidence |\n| --- | --- | --- |\n| sec | pass | na |\n\n## Review\nok\n\n## QA Evidence\n- ok\n\n## AC Scorecard\n| s | src | res | ev |\n| - | - | - | - |\n| a | b | pass | c |\n\n## Merge Status\nmerged\n\n## Wiki Updates\n- doc\n\n## Human Review\nready\n",
        priority=1,
        state="Plan",
    )
    orch = Orchestrator(WorkflowState(Path("/tmp/no.md")))
    orch._workspace_manager = _FakeWorkspaceManager(tmp_path)  # type: ignore[assignment]
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
        agent_kind="claude",
        agent_profile="claude-reviewer",
        model="deepseek-v4-pro[1m]",
        reasoning_effort="",
        release_authority_resolved=True,
    )

    created_backends: list[_RecordingBackend] = []

    def _fake_build_backend(init: BackendInit) -> _RecordingBackend:
        b = _RecordingBackend(init=init)
        created_backends.append(b)
        return b

    monkeypatch.setattr(core_mod, "build_backend", _fake_build_backend)

    logged_events: list[tuple[str, dict[str, Any]]] = []

    def _fake_log_info(event: str, **kwargs: Any) -> None:
        logged_events.append((event, kwargs))

    monkeypatch.setattr(core_mod.log, "info", _fake_log_info)

    sequence = ["Build", "Done"]
    idx = 0

    async def _fake_refresh(c: ServiceConfig, running_id: str) -> Issue:
        nonlocal idx
        st = sequence[idx]
        idx = min(idx + 1, len(sequence) - 1)
        cur_issue = orch._running[running_id].issue
        return replace(cur_issue, state=st)

    monkeypatch.setattr(orch, "_refresh_issue_state", _fake_refresh)

    asyncio.run(orch._run_agent_attempt(issue, attempt=None, cfg=cfg))

    reroute_logs = [kwargs for event, kwargs in logged_events if event == "stage_backend_rerouted"]
    assert len(reroute_logs) == 1
    rlog = reroute_logs[0]
    assert rlog["issue_id"] == "iss-reroute-same-kind"
    assert rlog["identifier"] == "T-REROUTE-1"
    assert rlog["from_state"] == "plan"
    assert rlog["to_state"] == "build"
    assert rlog["from_kind"] == "claude"
    assert rlog["to_kind"] == "claude"
    assert rlog["from_profile"] == "claude-reviewer"
    assert rlog["to_profile"] == "claude-documenter"
    assert rlog["from_model"] == "deepseek-v4-pro[1m]"
    assert rlog["to_model"] == "deepseek-v4-flash"
    assert rlog["to_reasoning_effort"] == ""


def test_orchestrator_stage_transition_persists_profile_to_run_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from datetime import datetime, timezone
    import symphony.orchestrator.core as core_mod
    from symphony.orchestrator import Orchestrator, RunningEntry
    from symphony.orchestrator.run_registry import RunRegistry
    from symphony.workflow.state import WorkflowState

    registry = RunRegistry(tmp_path / ".symphony" / "state.db")

    profiles = {
        "sol-planner": AgentProfileConfig(
            name="sol-planner", kind="codex", model="sol", reasoning_effort="high"
        ),
        "sonnet-builder": AgentProfileConfig(
            name="sonnet-builder", kind="claude", model="sonnet"
        ),
    }
    cfg = _make_service_config(
        kind="claude",
        stage_profiles={"plan": "sol-planner", "build": "sonnet-builder"},
        agent_profiles=profiles,
    )
    issue = Issue(
        id="iss-persist-test",
        identifier="T-PERSIST-1",
        title="orchestrator persistence test",
        description="## Plan\n- ok\n\n## Acceptance Tests\n- ok\n\n## Done Signals\n- ok\n\n## Implementation\n- ok\n\n## Self-Critique\n- ok\n\n## Security Audit\n| check | verdict | evidence |\n| --- | --- | --- |\n| sec | pass | na |\n\n## Review\nok\n\n## QA Evidence\n- ok\n\n## AC Scorecard\n| s | src | res | ev |\n| - | - | - | - |\n| a | b | pass | c |\n\n## Merge Status\nmerged\n\n## Wiki Updates\n- doc\n\n## Human Review\nready\n",
        priority=1,
        state="Plan",
    )
    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / issue.identifier,
        attempt=None,
        attempt_kind="initial",
        agent_kind="codex",
        agent_profile="sol-planner",
        model="sol",
        reasoning_effort="high",
    )
    assert run_id

    orch = Orchestrator(WorkflowState(Path("/tmp/no.md")))
    orch._run_registry = registry
    orch._run_registry_initialized = True
    orch._workspace_manager = _FakeWorkspaceManager(tmp_path)  # type: ignore[assignment]
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
        agent_kind="codex",
        agent_profile="sol-planner",
        model="sol",
        reasoning_effort="high",
        run_id=run_id,
        release_authority_resolved=True,
    )

    created_backends: list[_RecordingBackend] = []

    def _fake_build_backend(init: BackendInit) -> _RecordingBackend:
        b = _RecordingBackend(init=init)
        created_backends.append(b)
        return b

    monkeypatch.setattr(core_mod, "build_backend", _fake_build_backend)

    sequence = ["Build", "Done"]
    idx = 0

    async def _fake_refresh(c: ServiceConfig, running_id: str) -> Issue:
        nonlocal idx
        st = sequence[idx]
        idx = min(idx + 1, len(sequence) - 1)
        cur_issue = orch._running[running_id].issue
        return replace(cur_issue, state=st)

    monkeypatch.setattr(orch, "_refresh_issue_state", _fake_refresh)

    asyncio.run(orch._run_agent_attempt(issue, attempt=None, cfg=cfg))

    rec = registry.get_run(run_id)
    assert rec.state == "Done"
    assert rec.agent_kind == "claude"
    assert rec.agent_profile == "sonnet-builder"
    assert rec.model == "sonnet"
    assert rec.reasoning_effort == ""



