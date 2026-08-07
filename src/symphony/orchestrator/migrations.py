"""Versioned, transactional schema migrations for `.symphony/state.db`.

The registry used to keep its DDL inline as `CREATE TABLE IF NOT EXISTS`
plus a hand-rolled `PRAGMA table_info` diff. That is fine for one table but
gives no way to tell "this database predates feature X" from "this database
already has feature X", and no place to hang a pre-upgrade backup.

Migrations are an ordered, append-only list. Each runs exactly once under
`BEGIN IMMEDIATE`, and the applied version is committed in the same
transaction as its DDL, so a crash mid-upgrade leaves the database at the
last fully applied version rather than half-migrated.

Version 1 is the pre-migration baseline. It is written to be idempotent so
databases created by older Symphony builds — which have the tables but no
`schema_migrations` row — adopt version 1 without any data movement.

Adding a migration: append to `MIGRATIONS` with the next version number.
Never edit or reorder an existing entry; released databases have already
recorded it as applied.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


# Schema version that first carries governed-workflow tables. Upgrading a
# populated database across this line takes a backup first (PRD §14.8).
FIRST_GOVERNED_WORKFLOW_VERSION = 2


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, decl: str
) -> None:
    """`ALTER TABLE ADD COLUMN` guarded by a column-presence check.

    The duplicate-column rescue keeps this safe when two processes race the
    same migration; SQLite reports that as an `OperationalError`, not a
    constraint violation, so it cannot be caught more narrowly.
    """
    if column in _table_columns(conn, table):
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise


def _migrate_001_baseline(conn: sqlite3.Connection) -> None:
    """Dispatch leases and issue flags — the schema as of Symphony 0.16."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            issue_id TEXT NOT NULL,
            identifier TEXT NOT NULL,
            title TEXT NOT NULL,
            state TEXT NOT NULL,
            attempt INTEGER,
            attempt_kind TEXT NOT NULL,
            agent_kind TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            lease_expires_at TEXT,
            last_progress_at TEXT,
            completed_at TEXT,
            owner_pid INTEGER,
            owner_boot_id TEXT,
            backend_agent_pid INTEGER
        )
        """
    )
    # Pre-owner databases: NULL owners read as "unknown, presumed dead" in
    # reclaim_dead_owner_leases.
    _add_column_if_missing(conn, "runs", "owner_pid", "INTEGER")
    _add_column_if_missing(conn, "runs", "owner_boot_id", "TEXT")
    _add_column_if_missing(conn, "runs", "backend_agent_pid", "INTEGER")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runs_issue_status_lease
        ON runs(issue_id, status, lease_expires_at)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS issue_flags (
            issue_id TEXT PRIMARY KEY,
            retry_attempt INTEGER,
            budget_exhausted INTEGER NOT NULL DEFAULT 0,
            paused INTEGER NOT NULL DEFAULT 0,
            pause_reason TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    _add_column_if_missing(conn, "issue_flags", "pause_reason", "TEXT")


def _migrate_002_governed_workflow(conn: sqlite3.Connection) -> None:
    """Node-level execution ledger for governed workflow runs (PRD §14).

    Everything here is additive. `runs.status` keeps its lease/history
    meaning; governed state lives in the separate `execution_status` column
    so an older binary reading this database still sees valid lease rows.
    """
    _add_column_if_missing(conn, "runs", "execution_mode", "TEXT")
    _add_column_if_missing(conn, "runs", "execution_status", "TEXT")
    _add_column_if_missing(conn, "runs", "attention_reason", "TEXT")
    _add_column_if_missing(conn, "runs", "workflow_name", "TEXT")
    _add_column_if_missing(conn, "runs", "workflow_version", "INTEGER")
    _add_column_if_missing(conn, "runs", "workflow_hash", "TEXT")
    _add_column_if_missing(conn, "runs", "ticket_snapshot_json", "TEXT")
    _add_column_if_missing(conn, "runs", "terminal_reason", "TEXT")
    _add_column_if_missing(conn, "runs", "input_tokens", "INTEGER")
    _add_column_if_missing(conn, "runs", "output_tokens", "INTEGER")
    _add_column_if_missing(conn, "runs", "cost_usd", "REAL")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_runs_execution_status
        ON runs(execution_status, updated_at)
        """
    )

    # Content-addressed definitions: identical workflow YAML across many runs
    # is stored once instead of copying up to 1 MiB of JSON per run row.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_snapshots (
            workflow_hash TEXT PRIMARY KEY,
            workflow_name TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            normalized_json TEXT NOT NULL,
            source_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # "This issue already has a nonterminal governed run" — distinct from
    # "this process owns a live worker lease", which stays in `runs`.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_fences (
            issue_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS node_runs (
            node_run_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            node_type TEXT NOT NULL,
            status TEXT NOT NULL,
            backend_kind TEXT,
            workspace_access TEXT,
            started_at TEXT,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            error_class TEXT,
            error_code TEXT,
            error_message_redacted TEXT,
            output_preview TEXT,
            output_sha256 TEXT,
            session_id TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cost_usd REAL,
            head_before TEXT,
            head_after TEXT,
            diffstat_json TEXT,
            external_operation_key TEXT,
            UNIQUE(run_id, node_id, attempt)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_node_runs_run_status
        ON node_runs(run_id, status)
        """
    )

    # Append-only ledger. `seq` is allocated inside the same transaction as
    # the state transition it describes, so the stream can never show an
    # event for a transition that rolled back.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_events (
            run_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            node_id TEXT,
            type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_json_redacted TEXT NOT NULL,
            PRIMARY KEY(run_id, seq)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            scope TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            media_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_expired_at TEXT,
            metadata_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_artifacts_run_node
        ON artifacts(run_id, node_id)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS approvals (
            approval_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            node_attempt INTEGER NOT NULL,
            status TEXT NOT NULL,
            version INTEGER NOT NULL,
            title TEXT NOT NULL,
            instructions TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            resolved_at TEXT,
            decision TEXT,
            actor TEXT,
            source TEXT,
            comment TEXT,
            UNIQUE(run_id, node_id, node_attempt)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_approvals_status
        ON approvals(status, requested_at)
        """
    )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "baseline_runs_and_issue_flags", _migrate_001_baseline),
    Migration(2, "governed_workflow_ledger", _migrate_002_governed_workflow),
)

LATEST_SCHEMA_VERSION = MIGRATIONS[-1].version


def current_schema_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied version, or 0 for an unmigrated database."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 0


def backup_database(conn: sqlite3.Connection, path: Path) -> Path:
    """Snapshot the live database to a timestamped sibling file.

    Uses SQLite's online backup API rather than copying the file, because a
    WAL database's committed contents are split across `-wal` and the main
    file; a plain copy of one of them can lose recent writes.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = path.with_name(f"{path.name}.backup-{stamp}")
    target = sqlite3.connect(destination)
    try:
        conn.backup(target)
    finally:
        target.close()
    return destination


def apply_migrations(conn: sqlite3.Connection, path: Path) -> list[int]:
    """Bring `conn` up to `LATEST_SCHEMA_VERSION`; return versions applied.

    `path` is only used to place the pre-upgrade backup next to the
    database. Returns an empty list when nothing was pending.
    """
    applied: list[int] = []
    version = current_schema_version(conn)
    pending = [m for m in MIGRATIONS if m.version > version]
    if not pending:
        return applied

    # Only worth backing up a database that already holds run history; a
    # database still at version 0 with no rows has nothing to lose.
    crosses_governed_line = version < FIRST_GOVERNED_WORKFLOW_VERSION <= max(
        m.version for m in pending
    )
    if crosses_governed_line and _has_existing_runs(conn):
        backup_database(conn, path)

    for migration in pending:
        conn.execute("BEGIN IMMEDIATE")
        try:
            migration.apply(conn)
            conn.execute(
                """
                INSERT INTO schema_migrations (version, name, applied_at)
                VALUES (?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        applied.append(migration.version)
    return applied


def _has_existing_runs(conn: sqlite3.Connection) -> bool:
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
    ).fetchone()
    if table is None:
        return False
    row = conn.execute("SELECT 1 FROM runs LIMIT 1").fetchone()
    return row is not None
