from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import symphony.orchestrator.migrations as migration_mod


def test_v9_pm_workspace_migration_is_additive_and_enforces_mapping_shape(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path, isolation_level=None)
    assert migration_mod.apply_migrations(conn, path)[-1] == 9
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "pm_workspaces",
        "pm_operations",
        "pm_approvals",
        "pm_handoff_batches",
        "pm_handoff_items",
        "pm_managed_tickets",
        "pm_exchange_records",
        "pm_journals",
        "pm_dispatch_permits",
        "pm_manual_verification_records",
        "pm_coverage_waivers",
        "pm_planning_reserved_ids",
    } <= tables
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(pm_handoff_items)").fetchall()
    }
    assert {"project_id", "change_id", "task_id", "task_revision"} <= columns
    indexes = {
        str(row[1])
        for row in conn.execute("PRAGMA index_list(pm_dispatch_permits)").fetchall()
    }
    assert "idx_pm_active_dispatch_permit" in indexes
    conn.close()


def test_v8_continuation_migration_is_additive_and_link_is_unique(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path, isolation_level=None)
    original = migration_mod.MIGRATIONS
    monkeypatch.setattr(migration_mod, "MIGRATIONS", original[:7])
    assert migration_mod.apply_migrations(conn, path)[-1] == 7
    conn.execute(
        """
        INSERT INTO runs (
            run_id, issue_id, identifier, title, state, attempt, attempt_kind,
            agent_kind, workspace_path, status, started_at, updated_at
        ) VALUES ('existing', 'issue', 'ISSUE-1', 'title', 'In Progress', NULL,
                  'initial', 'codex', '/tmp/ws', 'normal', '2026-01-01', '2026-01-01')
        """
    )
    monkeypatch.setattr(migration_mod, "MIGRATIONS", original)

    assert migration_mod.apply_migrations(conn, path) == [8, 9]
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(runs)").fetchall()
    }
    assert {
        "resume_session_id",
        "checkpoint_state",
        "checkpoint_turn",
        "checkpointed_at",
        "continued_from_run_id",
    } <= columns
    assert conn.execute(
        "SELECT identifier, continued_from_run_id FROM runs WHERE run_id = 'existing'"
    ).fetchone() == ("ISSUE-1", None)

    # NULL links remain unrestricted, but one predecessor can be consumed once.
    conn.execute(
        """
        INSERT INTO runs (
            run_id, issue_id, identifier, title, state, attempt, attempt_kind,
            agent_kind, workspace_path, status, started_at, updated_at,
            continued_from_run_id
        ) SELECT 'successor-1', issue_id, identifier, title, state, attempt,
                 attempt_kind, agent_kind, workspace_path, status, started_at,
                 updated_at, 'existing' FROM runs WHERE run_id = 'existing'
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO runs (
                run_id, issue_id, identifier, title, state, attempt, attempt_kind,
                agent_kind, workspace_path, status, started_at, updated_at,
                continued_from_run_id
            ) SELECT 'successor-2', issue_id, identifier, title, state, attempt,
                     attempt_kind, agent_kind, workspace_path, status, started_at,
                     updated_at, 'existing' FROM runs WHERE run_id = 'existing'
            """
        )
    conn.close()
