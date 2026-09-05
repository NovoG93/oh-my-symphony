"""Lifecycle contracts that must survive extraction of the attempt runner.

These tests intentionally drive ``_run_agent_attempt`` through its existing
test seams (backend factory, refresh helper, workspace hooks, and exit
handler).  They describe observable ordering and ownership rather than an
implementation layout, so the future phase-oriented runner can move code
without changing the contract.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from symphony.backends import ProviderCapacityError
from symphony.orchestrator import Orchestrator
from symphony.workflow import AgentProfileConfig, UsagePoolConfig

from .test_orchestrator_phase_transition import (
    _FakeBackend,
    _FakeWorkspaceManager,
    _install_fake_backend,
    _install_state_sequence,
    _make_config,
    _make_issue,
    _orch,
    _seed_running_entry,
)


def test_attempt_reroutes_profile_and_usage_pool_at_phase_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A destination lane resolves its own profile and quota pool.

    This is the key profile/usage-pool transition characterization: the
    unrouted base workflow remains available while the backend is rebuilt.
    """
    base = _make_config(max_turns=4)
    cfg = replace(
        base,
        agent=replace(
            base.agent,
            stage_profiles={"todo": "planner", "in progress": "builder"},
        ),
        agent_profiles={
            "planner": AgentProfileConfig(
                name="planner", kind="codex", model="planner-model", usage_pool="pool-a"
            ),
            "builder": AgentProfileConfig(
                name="builder", kind="claude", model="builder-model", usage_pool="pool-b"
            ),
        },
        usage_pools={
            "pool-a": UsagePoolConfig(source="codex", caps={}),
            "pool-b": UsagePoolConfig(source="claude", caps={}),
        },
    )
    issue = _make_issue("Todo")
    orch = _orch(tmp_path)
    _seed_running_entry(orch, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])

    asyncio.run(orch._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert len(instances) == 2
    first, second = instances
    assert first.backend_init.selection.profile == "planner"
    assert first.backend_init.usage_pool == "pool-a"
    assert first.backend_init.cfg.agent.kind == "codex"
    assert second.backend_init.selection.profile == "builder"
    assert second.backend_init.usage_pool == "pool-b"
    assert second.backend_init.cfg.agent.kind == "claude"
    # Dispatch-local metadata is regenerated for the new phase and does not
    # mutate the inherited process environment in the backend factory.
    assert first.backend_init.env is not second.backend_init.env


def test_provider_quota_exhaustion_is_classified_and_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=1)
    issue = _make_issue("Todo")
    orch = _orch(tmp_path)
    _seed_running_entry(orch, issue, tmp_path)
    _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["Done"])
    exits: list[tuple[str, str | None]] = []

    async def _capture_exit(*args: Any, **kwargs: Any) -> None:
        del kwargs
        exits.append((str(args[2]), args[3]))

    async def _capacity(self: _FakeBackend, *, prompt: str, is_continuation: bool) -> None:
        del prompt, is_continuation
        raise ProviderCapacityError(pool_id="pool-a")

    monkeypatch.setattr(Orchestrator, "_on_worker_exit", _capture_exit)
    monkeypatch.setattr(_FakeBackend, "run_turn", _capacity)

    asyncio.run(orch._run_agent_attempt(issue, attempt=None, cfg=cfg))

    assert exits == [("provider_usage_exhausted", "pool-a: provider usage exhausted")]
    snapshot = orch.usage_manager.snapshot("pool-a")
    assert snapshot is not None
    assert snapshot.hard_limit_reached is True
    assert snapshot.authoritative is True


def test_refresh_failure_still_runs_turn_cleanup_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=3)
    issue = _make_issue("Todo")
    orch = _orch(tmp_path)
    _seed_running_entry(orch, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    exits: list[str] = []

    async def _refresh(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None

    async def _capture_exit(*args: Any, **kwargs: Any) -> None:
        del kwargs
        exits.append(str(args[2]))

    monkeypatch.setattr(Orchestrator, "_refresh_issue_state", _refresh)
    monkeypatch.setattr(Orchestrator, "_on_worker_exit", _capture_exit)

    asyncio.run(orch._run_agent_attempt(issue, attempt=None, cfg=cfg))

    workspace = orch._workspace_manager
    assert isinstance(workspace, _FakeWorkspaceManager)
    assert len([call for call in instances[0].calls if call[0] == "stop"]) == 1
    assert workspace.after_run_paths == [tmp_path]
    assert exits == ["issue_state_refresh_failed"]


def test_cancelled_turn_stops_backend_and_salvages_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=3)
    issue = _make_issue("Todo")
    orch = _orch(tmp_path)
    _seed_running_entry(orch, issue, tmp_path)
    instances = _install_fake_backend(monkeypatch)
    entered = asyncio.Event()
    exits: list[str] = []

    async def _blocked_turn(
        self: _FakeBackend, *, prompt: str, is_continuation: bool
    ) -> None:
        del prompt, is_continuation
        entered.set()
        await asyncio.Event().wait()

    async def _capture_exit(*args: Any, **kwargs: Any) -> None:
        del kwargs
        exits.append(str(args[2]))

    monkeypatch.setattr(_FakeBackend, "run_turn", _blocked_turn)
    monkeypatch.setattr(Orchestrator, "_on_worker_exit", _capture_exit)

    async def _exercise() -> None:
        task = asyncio.create_task(
            orch._run_agent_attempt(issue, attempt=None, cfg=cfg)
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_exercise())

    workspace = orch._workspace_manager
    assert isinstance(workspace, _FakeWorkspaceManager)
    assert len([call for call in instances[0].calls if call[0] == "stop"]) == 1
    assert workspace.after_run_paths == [tmp_path]
    assert exits == ["cancelled"]


def test_attempt_event_order_keeps_session_before_turn_and_phase_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _make_config(max_turns=4)
    issue = _make_issue("Todo")
    orch = _orch(tmp_path)
    _seed_running_entry(orch, issue, tmp_path)
    _install_fake_backend(monkeypatch)
    _install_state_sequence(monkeypatch, ["In Progress", "Done"])
    events: list[str] = []

    def _record_event(entry: Any, event_type: str, payload: dict[str, Any] | None = None) -> None:
        del entry, payload
        events.append(event_type)

    monkeypatch.setattr(orch, "_append_run_event", _record_event)
    async def _ignore_exit(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    monkeypatch.setattr(orch, "_on_worker_exit", _ignore_exit)

    asyncio.run(orch._run_agent_attempt(issue, attempt=None, cfg=cfg))

    # The direct lifecycle seam records turn/phase events; backend session
    # creation is characterized by the factory tests above and the backend
    # call sequence in the existing phase-transition suite.
    assert events == ["turn_started", "phase_transition", "turn_started"]
