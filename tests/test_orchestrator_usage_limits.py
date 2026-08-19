from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import textwrap

import pytest

from symphony.backends.usage import (
    ProviderUsageSnapshot,
    UsageWindow,
)
from symphony.issue import Issue
from symphony.orchestrator.core import Orchestrator, _EligibilityDisposition
from symphony.orchestrator.entries import RunningEntry
from symphony.workflow.builder import build_service_config
from symphony.workflow.config import ServiceConfig
from symphony.workflow.parser import parse_workflow_text
from symphony.workflow.state import WorkflowState


def _parse_config(workflow_text: str) -> ServiceConfig:
    dedented = textwrap.dedent(workflow_text).strip()
    if not dedented.startswith("---"):
        dedented = f"---\n{dedented}\n---\n"
    definition = parse_workflow_text(dedented, source_path=Path("/tmp/WORKFLOW.md"))
    return build_service_config(definition)


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


def _orch(cfg: ServiceConfig | None = None) -> Orchestrator:
    state = WorkflowState(Path("/tmp/WORKFLOW.md"))
    if cfg is None:
        cfg = _parse_config("""
        tracker:
          kind: file
        agent:
          kind: codex
          max_concurrent_agents: 5
        """)
    state._config = cfg  # type: ignore[attr-defined]
    return Orchestrator(state)


# --- Stage 6.10 Scheduler Tests ---


def test_all_profiles_of_same_pool_are_blocked_by_cap() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
      max_concurrent_agents: 5
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
    agent_profiles:
      codex-builder:
        kind: codex
      codex-reviewer:
        kind: codex
    """)
    orch = _orch(cfg)
    now = datetime.now(timezone.utc)
    orch._usage_manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=71.0,
                    remaining_percent=29.0,
                    resets_at=now + timedelta(hours=10),
                )
            },
            authoritative=True,
        ),
    )

    builder_issue = _issue("TASK-1", agent_profile="codex-builder")
    reviewer_issue = _issue("TASK-2", agent_profile="codex-reviewer")

    builder_decision = orch._eligibility_decision(
        builder_issue, cfg, owning_retry=False
    )
    reviewer_decision = orch._eligibility_decision(
        reviewer_issue, cfg, owning_retry=False
    )

    assert builder_decision.disposition is _EligibilityDisposition.WAIT_NON_SLOT
    assert builder_decision.code == "waiting_provider_usage"
    assert "weekly usage cap reached" in builder_decision.reason

    assert reviewer_decision.disposition is _EligibilityDisposition.WAIT_NON_SLOT
    assert reviewer_decision.code == "waiting_provider_usage"
    assert "weekly usage cap reached" in reviewer_decision.reason


def test_other_provider_remains_schedulable() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
      max_concurrent_agents: 5
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
      claude:
        source: claude
        caps:
          weekly: 70
    agent_profiles:
      codex-worker:
        kind: codex
      claude-worker:
        kind: claude
    """)
    orch = _orch(cfg)
    now = datetime.now(timezone.utc)
    orch._usage_manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=80.0,
                    remaining_percent=20.0,
                    resets_at=now + timedelta(hours=10),
                )
            },
            authoritative=True,
        ),
    )
    orch._usage_manager.set_snapshot(
        "claude",
        ProviderUsageSnapshot(
            pool_id="claude",
            source="claude",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=50.0,
                    remaining_percent=50.0,
                    resets_at=now + timedelta(hours=10),
                )
            },
            authoritative=True,
        ),
    )

    codex_issue = _issue("TASK-1", agent_profile="codex-worker")
    claude_issue = _issue("TASK-2", agent_profile="claude-worker")

    assert (
        orch._eligibility_decision(codex_issue, cfg, owning_retry=False).code
        == "waiting_provider_usage"
    )
    assert (
        orch._eligibility_decision(claude_issue, cfg, owning_retry=False).code
        == "ready"
    )


def test_usage_exactly_at_cap_blocks_dispatch() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
    """)
    orch = _orch(cfg)
    orch._usage_manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=70.0,
                    remaining_percent=30.0,
                )
            },
            authoritative=True,
        ),
    )
    issue = _issue("TASK-1")
    decision = orch._eligibility_decision(issue, cfg, owning_retry=False)
    assert decision.disposition is _EligibilityDisposition.WAIT_NON_SLOT
    assert decision.code == "waiting_provider_usage"


def test_usage_below_cap_allows_dispatch() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
    """)
    orch = _orch(cfg)
    orch._usage_manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=69.9,
                    remaining_percent=30.1,
                )
            },
            authoritative=True,
        ),
    )
    issue = _issue("TASK-1")
    decision = orch._eligibility_decision(issue, cfg, owning_retry=False)
    assert decision.disposition is _EligibilityDisposition.READY
    assert decision.code == "ready"


@pytest.mark.parametrize(
    ("window", "used", "cap"),
    [
        ("five_hour", 80.0, 80.0),
        ("weekly", 70.0, 70.0),
        ("daily", 90.0, 80.0),
        ("monthly", 95.0, 90.0),
        ("custom_window", 85.0, 80.0),
    ],
)
def test_any_configured_window_can_block(window: str, used: float, cap: float) -> None:
    cfg = _parse_config(f"""
    tracker:
      kind: file
    agent:
      kind: codex
    usage_pools:
      codex:
        source: codex
        caps:
          {window}: {cap}
    """)
    orch = _orch(cfg)
    orch._usage_manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                window: UsageWindow(
                    key=window,
                    used_percent=used,
                    remaining_percent=100.0 - used,
                )
            },
            authoritative=True,
        ),
    )
    issue = _issue("TASK-1")
    decision = orch._eligibility_decision(issue, cfg, owning_retry=False)
    assert decision.disposition is _EligibilityDisposition.WAIT_NON_SLOT
    assert decision.code == "waiting_provider_usage"
    assert window in decision.reason


def test_missing_usage_snapshot_fails_open() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
    """)
    orch = _orch(cfg)
    # No snapshot populated in usage manager
    issue = _issue("TASK-1")
    decision = orch._eligibility_decision(issue, cfg, owning_retry=False)
    assert decision.disposition is _EligibilityDisposition.READY
    assert decision.code == "ready"


def test_probe_exception_fails_open() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
    """)
    orch = _orch(cfg)
    # Probe failure leaves snapshot as None or marks stale
    orch._usage_manager.snapshots["codex"] = ProviderUsageSnapshot(
        pool_id="codex",
        source="codex",
        windows={
            "weekly": UsageWindow(
                key="weekly", used_percent=90.0, remaining_percent=10.0
            )
        },
        stale=True,
        authoritative=True,
    )
    issue = _issue("TASK-1")
    decision = orch._eligibility_decision(issue, cfg, owning_retry=False)
    assert decision.disposition is _EligibilityDisposition.READY
    assert decision.code == "ready"


def test_non_authoritative_usage_fails_open() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
    """)
    orch = _orch(cfg)
    orch._usage_manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=99.0,
                    remaining_percent=1.0,
                )
            },
            authoritative=False,  # Not authoritative -> UI only, never blocks scheduler
        ),
    )
    issue = _issue("TASK-1")
    decision = orch._eligibility_decision(issue, cfg, owning_retry=False)
    assert decision.disposition is _EligibilityDisposition.READY
    assert decision.code == "ready"


def test_no_policy_does_not_block_dispatch() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
    """)
    orch = _orch(cfg)
    orch._usage_manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=99.0,
                    remaining_percent=1.0,
                )
            },
            authoritative=True,
        ),
    )
    issue = _issue("TASK-1")
    # No usage_pools configured in workflow -> dispatch is not blocked
    decision = orch._eligibility_decision(issue, cfg, owning_retry=False)
    assert decision.disposition is _EligibilityDisposition.READY
    assert decision.code == "ready"


def test_hard_limit_reached_blocks_dispatch() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
    """)
    orch = _orch(cfg)
    orch._usage_manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=10.0,
                    remaining_percent=90.0,
                )
            },
            hard_limit_reached=True,
            authoritative=True,
        ),
    )
    issue = _issue("TASK-1")
    decision = orch._eligibility_decision(issue, cfg, owning_retry=False)
    assert decision.disposition is _EligibilityDisposition.WAIT_NON_SLOT
    assert decision.code == "waiting_provider_usage"


def test_stale_snapshot_fails_open() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
    """)
    orch = _orch(cfg)
    orch._usage_manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=95.0,
                    remaining_percent=5.0,
                )
            },
            stale=True,
            authoritative=True,
        ),
    )
    issue = _issue("TASK-1")
    decision = orch._eligibility_decision(issue, cfg, owning_retry=False)
    assert decision.disposition is _EligibilityDisposition.READY
    assert decision.code == "ready"


def test_task_becomes_ready_after_usage_reset() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
    """)
    orch = _orch(cfg)
    now = datetime.now(timezone.utc)
    t_reset = now + timedelta(seconds=10)

    # Initially blocked
    orch._usage_manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=72.0,
                    remaining_percent=28.0,
                    resets_at=t_reset,
                )
            },
            authoritative=True,
        ),
    )
    issue = _issue("TASK-1")
    assert (
        orch._eligibility_decision(issue, cfg, owning_retry=False).code
        == "waiting_provider_usage"
    )

    # Reset occurs and fresh telemetry reports capacity restored
    orch._usage_manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=0.0,
                    remaining_percent=100.0,
                    resets_at=t_reset + timedelta(days=7),
                )
            },
            authoritative=True,
        ),
    )
    assert orch._eligibility_decision(issue, cfg, owning_retry=False).code == "ready"


def test_failed_refresh_after_reset_fails_open() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
    """)
    orch = _orch(cfg)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    # Old snapshot that exceeded cap, but reset_at has already passed
    orch._usage_manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=72.0,
                    remaining_percent=28.0,
                    resets_at=past,
                )
            },
            stale=True,  # Probe failed on refresh
            authoritative=True,
        ),
    )
    issue = _issue("TASK-1")
    # Must fail open (an old blocking snapshot must not block forever)
    decision = orch._eligibility_decision(issue, cfg, owning_retry=False)
    assert decision.disposition is _EligibilityDisposition.READY
    assert decision.code == "ready"


def test_wrapper_backend_explicit_usage_pool_shares_quota() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
    usage_pools:
      codex-shared:
        source: codex
        caps:
          weekly: 70
    agent_profiles:
      pi-builder:
        kind: pi
        usage_pool: codex-shared
      opencode-builder:
        kind: opencode
        usage_pool: codex-shared
    """)
    orch = _orch(cfg)
    orch._usage_manager.set_snapshot(
        "codex-shared",
        ProviderUsageSnapshot(
            pool_id="codex-shared",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=85.0,
                    remaining_percent=15.0,
                )
            },
            authoritative=True,
        ),
    )
    pi_issue = _issue("TASK-1", agent_profile="pi-builder")
    opencode_issue = _issue("TASK-2", agent_profile="opencode-builder")

    assert (
        orch._eligibility_decision(pi_issue, cfg, owning_retry=False).code
        == "waiting_provider_usage"
    )
    assert (
        orch._eligibility_decision(opencode_issue, cfg, owning_retry=False).code
        == "waiting_provider_usage"
    )


# --- Stage 6.11 Running-Worker Semantics ---


@pytest.mark.asyncio
async def test_configured_cap_does_not_cancel_running_worker() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
      max_concurrent_agents: 5
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
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

    # Usage crosses configured cap while worker is running
    orch._usage_manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=75.0,
                    remaining_percent=25.0,
                )
            },
            authoritative=True,
        ),
    )

    # Running worker must not be cancelled
    assert not worker_task.done()
    assert not worker_task.cancelled()

    # Scheduler eligibility for a NEW ticket is blocked
    new_issue = _issue("TASK-2")
    assert (
        orch._eligibility_decision(new_issue, cfg, owning_retry=False).code
        == "waiting_provider_usage"
    )

    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass


# --- Stage 6.13 Global Fail-Open Invariant ---


@pytest.mark.parametrize(
    "kind",
    [
        "codex",
        "claude",
        "agy",
        "gemini",
        "kiro",
        "opencode",
        "pi",
        "prime-agent",
        "copilot",
    ],
)
def test_usage_probe_failure_never_prevents_dispatch(kind: str) -> None:
    cfg = _parse_config(f"""
    tracker:
      kind: file
    agent:
      kind: {kind}
    usage_pools:
      {kind}:
        source: {kind}
        caps:
          weekly: 70
    """)
    orch = _orch(cfg)
    # Probe failed / unavailable / stale
    orch._usage_manager.set_snapshot(
        kind,
        ProviderUsageSnapshot(
            pool_id=kind,
            source=kind,
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=80.0,
                    remaining_percent=20.0,
                )
            },
            stale=True,
            authoritative=True,
        ),
    )
    issue = _issue("TASK-1", agent_kind=kind)
    decision = orch._eligibility_decision(issue, cfg, owning_retry=False)
    assert decision.disposition is _EligibilityDisposition.READY
    assert decision.code == "ready"
