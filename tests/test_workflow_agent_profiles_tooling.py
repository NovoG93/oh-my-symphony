"""Tests for Named Agent Profiles — Phase 4: observability + tooling (PLAN §12-§15).

Covers:
- Run records persistence: agent_profile, model, reasoning_effort in schema v9,
  RunRecord dataclass, RunRegistry acquire/get/query/summary, and event log.
- Ticket-level profile overrides: agent.profile and agent_profile in file tracker,
  atomic create/update, and ambiguity rejection.
- CLI: `symphony board new` / `update` / `show` with `--agent-profile`.
- Doctor: `symphony doctor` / `check_agent_profiles` PASS, WARN, and FAIL validation.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import symphony.orchestrator.migrations as migration_mod
from symphony.cli.board import cmd_new, cmd_show, cmd_update
from symphony.cli.doctor import check_agent_profiles
from symphony.errors import SymphonyError
from symphony.issue import Issue
from symphony.orchestrator.run_registry import RunRegistry
from symphony.trackers.file import FileBoardTracker, parse_ticket_file
from symphony.workflow import (
    ServiceConfig,
    TrackerConfig,
    build_service_config,
    load_workflow,
)


def _issue(identifier: str = "TASK-1", state: str = "Todo") -> Issue:
    return Issue(
        id=f"id-{identifier}",
        identifier=identifier,
        title=f"{identifier} title",
        description="",
        priority=2,
        state=state,
        created_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Section 12: Run Records Persistence & Schema Migration v9
# ---------------------------------------------------------------------------


def test_v9_migration_adds_profile_model_reasoning_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path, isolation_level=None)
    original = migration_mod.MIGRATIONS

    # Apply up through v8
    monkeypatch.setattr(migration_mod, "MIGRATIONS", original[:8])
    assert migration_mod.apply_migrations(conn, path)[-1] == 8

    # Apply v9
    monkeypatch.setattr(migration_mod, "MIGRATIONS", original)
    applied = migration_mod.apply_migrations(conn, path)
    assert 9 in applied or migration_mod.current_schema_version(conn) >= 9

    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(runs)").fetchall()
    }
    assert {"agent_profile", "model", "reasoning_effort"} <= columns
    conn.close()


def test_run_registry_persists_profile_and_model(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db", lease_ttl=timedelta(seconds=60))
    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    issue = _issue("CORE-101")

    run_id = registry.acquire_run(
        issue,
        workspace_path=tmp_path / "ws" / issue.identifier,
        attempt=1,
        attempt_kind="initial",
        agent_kind="codex",
        agent_profile="sol-planner",
        model="sol",
        reasoning_effort="high",
        now=now,
    )
    assert run_id

    record = registry.get_run(run_id)
    assert record.run_id == run_id
    assert record.agent_kind == "codex"
    assert record.agent_profile == "sol-planner"
    assert record.model == "sol"
    assert record.reasoning_effort == "high"

    # Query runs includes profile
    rows = registry.query_runs(issue_id="CORE-101")
    assert len(rows) == 1
    assert rows[0].agent_profile == "sol-planner"
    assert rows[0].model == "sol"

    # Events include profile and model payload
    events = registry.run_events(run_id)
    assert len(events) >= 1
    acquired_event = next(e for e in events if e["event_type"] == "run_acquired")
    assert acquired_event["payload"].get("agent_profile") == "sol-planner"
    assert acquired_event["payload"].get("model") == "sol"
    assert acquired_event["payload"].get("reasoning_effort") == "high"

    registry.close()


def test_run_registry_query_runs_filters_and_search(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db", lease_ttl=timedelta(seconds=60))
    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

    r1 = registry.acquire_run(
        _issue("T-1"),
        workspace_path=tmp_path / "ws" / "T-1",
        attempt=1,
        attempt_kind="initial",
        agent_kind="claude",
        agent_profile="sonnet-builder",
        model="sonnet",
        now=now,
    )
    assert r1

    r2 = registry.acquire_run(
        _issue("T-2"),
        workspace_path=tmp_path / "ws" / "T-2",
        attempt=1,
        attempt_kind="initial",
        agent_kind="codex",
        agent_profile="sol-reviewer",
        model="sol",
        reasoning_effort="high",
        now=now,
    )
    assert r2

    # Query by search term matching profile or model
    search_sonnet = registry.query_runs(query="sonnet")
    assert len(search_sonnet) == 1
    assert search_sonnet[0].run_id == r1

    search_sol = registry.query_runs(query="sol-reviewer")
    assert len(search_sol) == 1
    assert search_sol[0].run_id == r2

    registry.close()


# ---------------------------------------------------------------------------
# Section 13: Ticket-Level Profile Overrides
# ---------------------------------------------------------------------------


def _tracker(root: Path) -> TrackerConfig:
    return TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=("Todo", "In Progress", "Verify"),
        terminal_states=("Done", "Cancelled"),
        board_root=root,
    )


def test_file_tracker_create_with_agent_profile(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path / "board")
    fbt = FileBoardTracker(tracker)

    path = fbt.create(
        identifier="TASK-100",
        title="Test profile creation",
        agent_profile="sonnet-builder",
    )
    assert path.exists()

    front, _ = parse_ticket_file(path)
    assert front.get("agent") == {"profile": "sonnet-builder"}

    issue = fbt.fetch_issue_full_by_id("TASK-100")
    assert issue is not None
    assert issue.agent_profile == "sonnet-builder"
    assert issue.agent_kind is None


def test_file_tracker_update_agent_profile(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path / "board")
    fbt = FileBoardTracker(tracker)

    fbt.create(
        identifier="TASK-101",
        title="Test profile update",
        agent_kind="codex",
    )
    issue = fbt.fetch_issue_full_by_id("TASK-101")
    assert issue is not None
    assert issue.agent_kind == "codex"
    assert issue.agent_profile is None

    # Update to profile (should replace agent.kind with agent.profile)
    fbt.update_fields("TASK-101", agent_profile="fable-planner")
    updated = fbt.fetch_issue_full_by_id("TASK-101")
    assert updated is not None
    assert updated.agent_profile == "fable-planner"
    assert updated.agent_kind is None

    # Clear profile
    fbt.update_fields("TASK-101", agent_profile="")
    cleared = fbt.fetch_issue_full_by_id("TASK-101")
    assert cleared is not None
    assert cleared.agent_profile is None
    assert cleared.agent_kind is None



def test_file_tracker_rejects_ambiguous_create(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path / "board")
    fbt = FileBoardTracker(tracker)

    with pytest.raises(SymphonyError, match="ambiguous agent override"):
        fbt.create(
            identifier="TASK-102",
            title="Ambiguous ticket",
            agent_kind="codex",
            agent_profile="sonnet-builder",
        )


def test_record_agent_kind_preserves_existing_profile(tmp_path: Path) -> None:
    tracker = _tracker(tmp_path / "board")
    fbt = FileBoardTracker(tracker)

    fbt.create(
        identifier="TASK-103",
        title="Ticket with profile",
        agent_profile="sonnet-builder",
    )
    # record_agent_kind should not clobber agent.profile
    fbt.record_agent_kind("TASK-103", "codex")

    issue = fbt.fetch_issue_full_by_id("TASK-103")
    assert issue is not None
    assert issue.agent_profile == "sonnet-builder"
    assert issue.agent_kind is None



# ---------------------------------------------------------------------------
# Section 14: CLI Support for --agent-profile
# ---------------------------------------------------------------------------


def _setup_workflow_with_profiles(tmp_path: Path) -> Path:
    wf = tmp_path / "WORKFLOW.md"
    board = tmp_path / "board"
    board.mkdir(parents=True, exist_ok=True)
    wf.write_text(
        """---
tracker:
  kind: file
  board_root: ./board
  active_states: [Todo, In Progress, Verify]
  terminal_states: [Done, Cancelled]

agent:
  kind: codex

agent_profiles:
  sol-planner:
    kind: codex
    model: sol
    reasoning_effort: high
  sonnet-builder:
    kind: claude
    model: sonnet
---
prompt: "hello"
""",
        encoding="utf-8",
    )
    return wf



def test_cli_board_new_with_agent_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wf = _setup_workflow_with_profiles(tmp_path)
    args = argparse.Namespace(
        workflow=str(wf),
        root=None,
        id="TASK-200",
        title="CLI created ticket",
        state="Todo",
        priority=2,
        labels=None,
        label=None,
        description="Ticket body",
        description_file=None,
        blocked_by=None,
        request=None,
        agent_kind=None,
        agent_profile="sol-planner",
    )
    rc = cmd_new(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "created" in captured.out

    tracker = _tracker(tmp_path / "board")
    fbt = FileBoardTracker(tracker)
    issue = fbt.fetch_issue_full_by_id("TASK-200")
    assert issue is not None
    assert issue.agent_profile == "sol-planner"



def test_cli_board_new_rejects_unknown_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wf = _setup_workflow_with_profiles(tmp_path)
    args = argparse.Namespace(
        workflow=str(wf),
        root=None,
        id="TASK-201",
        title="Bad profile",
        state="Todo",
        priority=None,
        labels=None,
        label=None,
        description=None,
        description_file=None,
        blocked_by=None,
        request=None,
        agent_kind=None,
        agent_profile="non-existent-profile",
    )
    rc = cmd_new(args)
    assert rc == 1
    captured = capsys.readouterr()
    assert "unknown agent profile" in captured.err


def test_cli_board_new_rejects_both_kind_and_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wf = _setup_workflow_with_profiles(tmp_path)
    args = argparse.Namespace(
        workflow=str(wf),
        root=None,
        id="TASK-202",
        title="Ambiguous args",
        state="Todo",
        priority=None,
        labels=None,
        label=None,
        description=None,
        description_file=None,
        blocked_by=None,
        request=None,
        agent_kind="codex",
        agent_profile="sol-planner",
    )
    rc = cmd_new(args)
    assert rc == 1
    captured = capsys.readouterr()
    assert "cannot set both" in captured.err


def test_cli_board_update_agent_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wf = _setup_workflow_with_profiles(tmp_path)
    tracker = _tracker(tmp_path / "board")
    fbt = FileBoardTracker(tracker)
    fbt.create(identifier="TASK-203", title="Existing ticket")

    args = argparse.Namespace(
        workflow=str(wf),
        root=None,
        id="TASK-203",
        state=None,
        blocked_by=None,
        add_blocked_by=None,
        request=None,
        agent_kind=None,
        agent_profile="sonnet-builder",
    )
    rc = cmd_update(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "agent_profile=sonnet-builder" in captured.out

    issue = fbt.fetch_issue_full_by_id("TASK-203")
    assert issue is not None
    assert issue.agent_profile == "sonnet-builder"


def test_cli_board_show_displays_agent_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wf = _setup_workflow_with_profiles(tmp_path)
    tracker = _tracker(tmp_path / "board")
    fbt = FileBoardTracker(tracker)
    fbt.create(
        identifier="TASK-204",
        title="Show ticket",
        agent_profile="sol-planner",
    )

    args = argparse.Namespace(
        workflow=str(wf),
        root=None,
        id="TASK-204",
    )
    rc = cmd_show(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert "agent: profile=sol-planner" in captured.out or "agent profile: sol-planner" in captured.out


# ---------------------------------------------------------------------------
# Section 15: Symphony Doctor Validation
# ---------------------------------------------------------------------------


def _build_cfg(tmp_path: Path, frontmatter: str) -> ServiceConfig:
    import textwrap
    text = "---\n" + textwrap.dedent(frontmatter).lstrip() + "\n---\nprompt: \"hello\"\n"
    path = tmp_path / "WORKFLOW.md"
    path.write_text(text, encoding="utf-8")
    return build_service_config(load_workflow(path))


def test_doctor_profile_checks_pass_valid_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure binary resolution finds a mock binary
    monkeypatch.setattr(shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./board }
        agent:
          kind: codex
          stage_profiles:
            Plan: sol-planner
            Build: sonnet-builder
          default_profile: sol-planner
        agent_profiles:
          sol-planner:
            kind: codex
            model: sol
            reasoning_effort: high
          sonnet-builder:
            kind: claude
            model: sonnet
        codex: { command: codex app-server }
        claude: { command: claude }
        """,
    )

    results = check_agent_profiles(cfg)
    assert len(results) >= 4
    for r in results:
        assert r.status == "pass"

    names = {r.name.lower() for r in results}
    assert "agent.profile.sol-planner" in names
    assert "agent.profile.sonnet-builder" in names
    assert "agent.stage_profiles.plan" in names
    assert "agent.stage_profiles.build" in names
    assert "agent.default_profile" in names


def test_doctor_profile_checks_warn_on_command_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./board }
        agent: { kind: codex }
        agent_profiles:
          custom-codex:
            kind: codex
            command: codex-custom app-server
            model: sol
        codex: { command: codex app-server }
        """,
    )

    results = check_agent_profiles(cfg)
    custom_res = next(r for r in results if r.name == "agent.profile.custom-codex")
    assert custom_res.status == "warn"
    assert "overrides" in custom_res.message


def test_doctor_profile_checks_fail_on_missing_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./board }
        agent: { kind: codex }
        agent_profiles:
          bad-cli:
            kind: codex
            command: nonexistent-binary
        codex: { command: codex app-server }
        """,
    )

    results = check_agent_profiles(cfg)
    res = next(r for r in results if r.name == "agent.profile.bad-cli")
    assert res.status == "fail"
    assert "not on $PATH" in res.message


def test_doctor_profile_checks_fail_on_bad_model_syntax(tmp_path: Path) -> None:
    cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./board }
        agent: { kind: codex }
        agent_profiles:
          bad-model:
            kind: codex
            model: "invalid model with space"
        codex: { command: codex app-server }
        """,
    )

    results = check_agent_profiles(cfg)
    res = next(r for r in results if r.name == "agent.profile.bad-model")
    assert res.status == "fail"
    assert "model" in res.message


def test_doctor_profile_checks_fail_on_unresolved_stage_profile(tmp_path: Path) -> None:
    base_cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./board }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    cfg = replace(base_cfg, agent=replace(base_cfg.agent, stage_profiles={"qa": "luna-builder-typo"}))
    results = check_agent_profiles(cfg)
    res = next(r for r in results if r.name == "agent.stage_profiles.qa")
    assert res.status == "fail"
    assert 'unknown profile "luna-builder-typo"' in res.message


def test_doctor_profile_checks_fail_on_unresolved_default_profile(tmp_path: Path) -> None:
    base_cfg = _build_cfg(
        tmp_path,
        """
        tracker: { kind: file, board_root: ./board }
        agent: { kind: codex }
        codex: { command: codex app-server }
        """,
    )
    cfg = replace(base_cfg, agent=replace(base_cfg.agent, default_profile="missing-default"))
    results = check_agent_profiles(cfg)
    res = next(r for r in results if r.name == "agent.default_profile")
    assert res.status == "fail"
    assert 'unknown profile "missing-default"' in res.message

