"""SQLite persistence for governed workflow runs.

Split out of `run_registry.py` so the lease/dispatch story stays readable;
both share one connection, owned by `RunRegistry`, because SQLite
connections are thread-affine and the orchestrator runs registry calls
inline on the event loop.

The invariant this module exists to enforce: **a state change and the event
that describes it are committed together**. Every mutating method opens one
`BEGIN IMMEDIATE`, writes its row, allocates the next `run_events.seq`, and
commits. A reader tailing `run_events` therefore never sees an event for a
transition that rolled back, and never misses one that landed.

Terminology, because two things here are easy to confuse:

- a **lease** (`runs.status = 'active'`) means "this process owns a live
  worker for this issue"; it expires on a TTL and is reclaimed when the
  owner pid dies.
- a **fence** (`run_fences`) means "this issue already has a nonterminal
  governed run". A run waiting on a human gate has no process and no lease,
  but must still block redispatch — that is the whole reason the fence is a
  separate durable row rather than a longer lease.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..errors import (
    ApprovalAlreadyResolved,
    ApprovalNotFound,
    ApprovalVersionConflict,
    IllegalRunTransition,
    RunFenced,
    RunNotFound,
)
from ..flow import statuses as st
from ..flow.redaction import redact_and_cap, redact_payload

# Time helpers are shared with the lease registry deliberately: two
# timestamp formats in one database would make the ledger unsortable.
from .run_registry import _iso, _parse, _utc


# Pause rows written to mirror a fence carry this prefix so an operator's
# own manual pause is never cleared by run terminalization, and so a
# rollback to an older binary still sees the issue as paused.
FENCE_PAUSE_PREFIX = "governed-run"


@dataclass(frozen=True)
class GovernedRunRecord:
    run_id: str
    issue_id: str
    identifier: str
    execution_mode: str
    execution_status: str
    attention_reason: str | None
    workflow_name: str | None
    workflow_version: int | None
    workflow_hash: str | None
    ticket_snapshot: dict[str, Any] | None
    terminal_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    workspace_path: Path
    started_at: datetime | None
    updated_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class NodeRunRecord:
    node_run_id: str
    run_id: str
    node_id: str
    attempt: int
    node_type: str
    status: str
    backend_kind: str | None
    workspace_access: str | None
    started_at: datetime | None
    updated_at: datetime | None
    completed_at: datetime | None
    error_class: str | None
    error_code: str | None
    error_message: str | None
    output_preview: str | None
    output_sha256: str | None
    session_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    head_before: str | None
    head_after: str | None
    diffstat: dict[str, Any] | None
    external_operation_key: str | None


@dataclass(frozen=True)
class RunEventRecord:
    run_id: str
    seq: int
    node_id: str | None
    type: str
    created_at: datetime | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    run_id: str
    node_id: str
    artifact_type: str
    scope: str
    relative_path: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: datetime | None
    payload_expired_at: datetime | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    run_id: str
    node_id: str
    node_attempt: int
    status: str
    version: int
    title: str
    instructions: str
    requested_at: datetime | None
    resolved_at: datetime | None
    decision: str | None
    actor: str | None
    source: str | None
    comment: str | None


@dataclass(frozen=True)
class FenceRecord:
    issue_id: str
    run_id: str
    reason: str
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class WorkflowSnapshotRecord:
    workflow_hash: str
    workflow_name: str
    schema_version: int
    normalized_json: str
    source_path: str
    created_at: datetime | None


class GovernedRunStore:
    """Node-level ledger operations over the shared `state.db` connection."""

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    # --- workflow snapshots -------------------------------------------

    def put_workflow_snapshot(
        self,
        *,
        workflow_hash: str,
        workflow_name: str,
        schema_version: int,
        normalized_json: str,
        source_path: str,
        now: datetime | None = None,
    ) -> None:
        """Store a definition once, keyed by content hash.

        Re-storing an identical definition is a no-op rather than an error:
        every run of the same unchanged workflow calls this.
        """
        self._connect().execute(
            """
            INSERT INTO workflow_snapshots (
                workflow_hash, workflow_name, schema_version,
                normalized_json, source_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(workflow_hash) DO NOTHING
            """,
            (
                workflow_hash,
                workflow_name,
                schema_version,
                normalized_json,
                source_path,
                _iso(_utc(now)),
            ),
        )

    def get_workflow_snapshot(self, workflow_hash: str) -> WorkflowSnapshotRecord | None:
        row = self._connect().execute(
            "SELECT * FROM workflow_snapshots WHERE workflow_hash = ?",
            (workflow_hash,),
        ).fetchone()
        return _snapshot(row) if row is not None else None

    # --- run lifecycle -------------------------------------------------

    def begin_governed_run(
        self,
        *,
        run_id: str,
        issue_id: str,
        workflow_name: str,
        workflow_version: int,
        workflow_hash: str,
        ticket_snapshot: dict[str, Any],
        now: datetime | None = None,
    ) -> None:
        """Mark an existing lease row as governed and fence its issue.

        The run row itself is created by `RunRegistry.acquire_run`; this
        promotes it. Fence acquisition shares the transaction so a crash
        can never leave a governed run without the fence that protects it.
        """
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT run_id FROM run_fences WHERE issue_id = ?", (issue_id,)
            ).fetchone()
            if existing is not None and str(existing["run_id"]) != run_id:
                conn.execute("ROLLBACK")
                raise RunFenced(
                    "issue already has a nonterminal governed run",
                    issue_id=issue_id,
                    run_id=str(existing["run_id"]),
                )
            cur = conn.execute(
                """
                UPDATE runs
                SET execution_mode = ?, execution_status = ?, workflow_name = ?,
                    workflow_version = ?, workflow_hash = ?,
                    ticket_snapshot_json = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    st.MODE_GOVERNED,
                    st.RUN_CREATED,
                    workflow_name,
                    workflow_version,
                    workflow_hash,
                    json.dumps(redact_payload(ticket_snapshot), ensure_ascii=False),
                    _iso(now),
                    run_id,
                ),
            )
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                raise RunNotFound("no run row to promote", run_id=run_id)
            self._write_fence_locked(
                conn, issue_id=issue_id, run_id=run_id, reason=st.RUN_RUNNING, now=now
            )
            self._append_event_locked(
                conn,
                run_id=run_id,
                event_type="run_created",
                node_id=None,
                payload={
                    "workflow_name": workflow_name,
                    "workflow_hash": workflow_hash,
                    "workflow_version": workflow_version,
                },
                now=now,
            )
            conn.execute("COMMIT")
        except Exception:
            _rollback_quietly(conn)
            raise

    def set_run_status(
        self,
        *,
        run_id: str,
        status: str,
        attention_reason: str | None = None,
        terminal_reason: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Move a run's execution status, updating its fence to match.

        Terminal statuses release the fence and its pause mirror; every
        other status refreshes the fence reason so the board shows *why*
        the issue is held.
        """
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT issue_id, execution_status FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise RunNotFound("unknown run", run_id=run_id)
            current = str(row["execution_status"] or st.RUN_CREATED)
            if not st.is_legal_run_transition(current, status):
                conn.execute("ROLLBACK")
                raise IllegalRunTransition(
                    f"cannot move a {current} run to {status}",
                    run_id=run_id,
                    current=current,
                    target=status,
                )
            issue_id = str(row["issue_id"])
            completed = _iso(now) if status in st.TERMINAL_RUN_STATUSES else None
            conn.execute(
                """
                UPDATE runs
                SET execution_status = ?, attention_reason = ?, terminal_reason = ?,
                    updated_at = ?,
                    completed_at = COALESCE(?, completed_at)
                WHERE run_id = ?
                """,
                (
                    status,
                    attention_reason,
                    terminal_reason,
                    _iso(now),
                    completed,
                    run_id,
                ),
            )
            if status in st.TERMINAL_RUN_STATUSES:
                self._clear_fence_locked(conn, run_id=run_id, issue_id=issue_id)
            else:
                fence_reason = (
                    status if status in st.FENCE_REASONS else st.RUN_RUNNING
                )
                self._write_fence_locked(
                    conn,
                    issue_id=issue_id,
                    run_id=run_id,
                    reason=fence_reason,
                    now=now,
                )
            self._append_event_locked(
                conn,
                run_id=run_id,
                event_type="run_status_changed",
                node_id=None,
                payload={
                    "from": current,
                    "to": status,
                    "attention_reason": attention_reason,
                    "terminal_reason": terminal_reason,
                },
                now=now,
            )
            conn.execute("COMMIT")
        except Exception:
            _rollback_quietly(conn)
            raise

    def record_run_usage(
        self,
        *,
        run_id: str,
        input_tokens: int | None,
        output_tokens: int | None,
        cost_usd: float | None,
        now: datetime | None = None,
    ) -> None:
        """Store run aggregates. `None` means "not reported", never zero."""
        self._connect().execute(
            """
            UPDATE runs
            SET input_tokens = ?, output_tokens = ?, cost_usd = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (input_tokens, output_tokens, cost_usd, _iso(_utc(now)), run_id),
        )

    def get_governed_run(self, run_id: str) -> GovernedRunRecord | None:
        row = self._connect().execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None or row["execution_mode"] != st.MODE_GOVERNED:
            return None
        return _governed_run(row)

    def list_nonterminal_runs(self) -> list[GovernedRunRecord]:
        """Governed runs that startup reconciliation must classify."""
        placeholders = ",".join("?" for _ in st.FENCED_RUN_STATUSES)
        rows = self._connect().execute(
            f"""
            SELECT * FROM runs
            WHERE execution_mode = ?
              AND execution_status IN ({placeholders})
            ORDER BY started_at, run_id
            """,
            (st.MODE_GOVERNED, *sorted(st.FENCED_RUN_STATUSES)),
        ).fetchall()
        return [_governed_run(row) for row in rows]

    def recent_governed_runs(
        self, issue_id: str | None = None, limit: int = 50
    ) -> list[GovernedRunRecord]:
        limit = max(1, min(int(limit), 200))
        if issue_id:
            rows = self._connect().execute(
                """
                SELECT * FROM runs
                WHERE execution_mode = ? AND (issue_id = ? OR identifier = ?)
                ORDER BY rowid DESC LIMIT ?
                """,
                (st.MODE_GOVERNED, issue_id, issue_id, limit),
            ).fetchall()
        else:
            rows = self._connect().execute(
                """
                SELECT * FROM runs WHERE execution_mode = ?
                ORDER BY rowid DESC LIMIT ?
                """,
                (st.MODE_GOVERNED, limit),
            ).fetchall()
        return [_governed_run(row) for row in rows]

    # --- fences ---------------------------------------------------------

    def fence_for_issue(self, issue_id: str) -> FenceRecord | None:
        row = self._connect().execute(
            "SELECT * FROM run_fences WHERE issue_id = ?", (issue_id,)
        ).fetchone()
        return _fence(row) if row is not None else None

    def list_fences(self) -> list[FenceRecord]:
        rows = self._connect().execute(
            "SELECT * FROM run_fences ORDER BY issue_id"
        ).fetchall()
        return [_fence(row) for row in rows]

    def release_fence(self, *, run_id: str, issue_id: str) -> bool:
        """Drop a fence outside a status change (explicit abandon)."""
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            released = self._clear_fence_locked(
                conn, run_id=run_id, issue_id=issue_id
            )
            conn.execute("COMMIT")
            return released
        except Exception:
            _rollback_quietly(conn)
            raise

    def _write_fence_locked(
        self,
        conn: sqlite3.Connection,
        *,
        issue_id: str,
        run_id: str,
        reason: str,
        now: datetime,
    ) -> None:
        conn.execute(
            """
            INSERT INTO run_fences (issue_id, run_id, reason, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(issue_id) DO UPDATE SET
                run_id = excluded.run_id,
                reason = excluded.reason,
                updated_at = excluded.updated_at
            """,
            (issue_id, run_id, reason, _iso(now), _iso(now)),
        )
        # Compatibility mirror: an older Symphony binary knows nothing about
        # run_fences, but it does honour issue_flags.paused. Only gates and
        # attention states mirror — a normally running node already holds a
        # live lease, and pausing it would fight the scheduler.
        if reason in {st.RUN_WAITING_APPROVAL, st.RUN_NEEDS_ATTENTION}:
            self._set_pause_mirror_locked(
                conn,
                issue_id=issue_id,
                reason=f"{FENCE_PAUSE_PREFIX}:{run_id}:{reason}",
                now=now,
            )
        else:
            self._clear_pause_mirror_locked(conn, issue_id=issue_id, run_id=run_id)

    def _clear_fence_locked(
        self, conn: sqlite3.Connection, *, run_id: str, issue_id: str
    ) -> bool:
        cur = conn.execute("DELETE FROM run_fences WHERE run_id = ?", (run_id,))
        self._clear_pause_mirror_locked(conn, issue_id=issue_id, run_id=run_id)
        return cur.rowcount > 0

    def _set_pause_mirror_locked(
        self, conn: sqlite3.Connection, *, issue_id: str, reason: str, now: datetime
    ) -> None:
        row = conn.execute(
            "SELECT * FROM issue_flags WHERE issue_id = ?", (issue_id,)
        ).fetchone()
        existing_reason = row["pause_reason"] if row is not None else None
        # An operator's own pause outranks the mirror: if a human paused this
        # issue for their own reason, do not overwrite their explanation.
        if (
            row is not None
            and bool(row["paused"])
            and existing_reason
            and not str(existing_reason).startswith(f"{FENCE_PAUSE_PREFIX}:")
        ):
            return
        conn.execute(
            """
            INSERT INTO issue_flags (
                issue_id, retry_attempt, budget_exhausted, paused,
                pause_reason, updated_at
            ) VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(issue_id) DO UPDATE SET
                paused = 1,
                pause_reason = excluded.pause_reason,
                updated_at = excluded.updated_at
            """,
            (
                issue_id,
                row["retry_attempt"] if row is not None else None,
                (1 if row is not None and row["budget_exhausted"] else 0),
                reason,
                _iso(now),
            ),
        )

    def _clear_pause_mirror_locked(
        self, conn: sqlite3.Connection, *, issue_id: str, run_id: str
    ) -> None:
        """Clear the pause only if *this* run owns it (PRD §10.2)."""
        conn.execute(
            """
            UPDATE issue_flags
            SET paused = 0, pause_reason = NULL
            WHERE issue_id = ? AND pause_reason LIKE ?
            """,
            (issue_id, f"{FENCE_PAUSE_PREFIX}:{run_id}:%"),
        )

    # --- node attempts ---------------------------------------------------

    def start_node_attempt(
        self,
        *,
        run_id: str,
        node_id: str,
        node_type: str,
        backend_kind: str | None = None,
        workspace_access: str | None = None,
        head_before: str | None = None,
        external_operation_key: str | None = None,
        now: datetime | None = None,
    ) -> NodeRunRecord:
        """Open a new attempt row. Attempt numbers never reuse a value.

        Retries append rather than overwrite so the history shows every
        failure that led to a success.
        """
        now = _utc(now)
        node_run_id = uuid.uuid4().hex
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            prior = conn.execute(
                """
                SELECT COALESCE(MAX(attempt), 0) AS latest FROM node_runs
                WHERE run_id = ? AND node_id = ?
                """,
                (run_id, node_id),
            ).fetchone()
            attempt = int(prior["latest"]) + 1
            conn.execute(
                """
                INSERT INTO node_runs (
                    node_run_id, run_id, node_id, attempt, node_type, status,
                    backend_kind, workspace_access, started_at, updated_at,
                    head_before, external_operation_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_run_id,
                    run_id,
                    node_id,
                    attempt,
                    node_type,
                    st.NODE_RUNNING,
                    backend_kind,
                    workspace_access,
                    _iso(now),
                    _iso(now),
                    head_before,
                    external_operation_key,
                ),
            )
            self._append_event_locked(
                conn,
                run_id=run_id,
                event_type="node_started",
                node_id=node_id,
                payload={
                    "attempt": attempt,
                    "node_type": node_type,
                    "backend_kind": backend_kind,
                    "workspace_access": workspace_access,
                },
                now=now,
            )
            row = conn.execute(
                "SELECT * FROM node_runs WHERE node_run_id = ?", (node_run_id,)
            ).fetchone()
            conn.execute("COMMIT")
            return _node_run(row)
        except Exception:
            _rollback_quietly(conn)
            raise

    def finish_node_attempt(
        self,
        *,
        node_run_id: str,
        status: str,
        error_class: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        output_preview: str | None = None,
        output_sha256: str | None = None,
        session_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        head_after: str | None = None,
        diffstat: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> NodeRunRecord:
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT run_id, node_id, attempt FROM node_runs WHERE node_run_id = ?",
                (node_run_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise RunNotFound("unknown node attempt", node_run_id=node_run_id)
            completed = _iso(now) if status in st.TERMINAL_NODE_STATUSES else None
            conn.execute(
                """
                UPDATE node_runs
                SET status = ?, updated_at = ?, completed_at = ?,
                    error_class = ?, error_code = ?, error_message_redacted = ?,
                    output_preview = ?, output_sha256 = ?, session_id = ?,
                    input_tokens = ?, output_tokens = ?, cost_usd = ?,
                    head_after = ?, diffstat_json = ?
                WHERE node_run_id = ?
                """,
                (
                    status,
                    _iso(now),
                    completed,
                    error_class,
                    error_code,
                    redact_and_cap(error_message, limit=4096),
                    redact_and_cap(output_preview),
                    output_sha256,
                    session_id,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    head_after,
                    json.dumps(diffstat, ensure_ascii=False) if diffstat else None,
                    node_run_id,
                ),
            )
            self._append_event_locked(
                conn,
                run_id=str(row["run_id"]),
                event_type="node_finished",
                node_id=str(row["node_id"]),
                payload={
                    "attempt": int(row["attempt"]),
                    "status": status,
                    "error_class": error_class,
                    "error_code": error_code,
                },
                now=now,
            )
            updated = conn.execute(
                "SELECT * FROM node_runs WHERE node_run_id = ?", (node_run_id,)
            ).fetchone()
            conn.execute("COMMIT")
            return _node_run(updated)
        except Exception:
            _rollback_quietly(conn)
            raise

    def set_node_status(
        self,
        *,
        node_run_id: str,
        status: str,
        now: datetime | None = None,
    ) -> None:
        """Status-only move, e.g. `running -> waiting_approval` on a gate."""
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT run_id, node_id, attempt FROM node_runs WHERE node_run_id = ?",
                (node_run_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise RunNotFound("unknown node attempt", node_run_id=node_run_id)
            conn.execute(
                "UPDATE node_runs SET status = ?, updated_at = ? WHERE node_run_id = ?",
                (status, _iso(now), node_run_id),
            )
            self._append_event_locked(
                conn,
                run_id=str(row["run_id"]),
                event_type="node_status_changed",
                node_id=str(row["node_id"]),
                payload={"attempt": int(row["attempt"]), "status": status},
                now=now,
            )
            conn.execute("COMMIT")
        except Exception:
            _rollback_quietly(conn)
            raise

    def list_node_runs(self, run_id: str) -> list[NodeRunRecord]:
        rows = self._connect().execute(
            """
            SELECT * FROM node_runs WHERE run_id = ?
            ORDER BY started_at, node_id, attempt
            """,
            (run_id,),
        ).fetchall()
        return [_node_run(row) for row in rows]

    def latest_node_attempts(self, run_id: str) -> dict[str, NodeRunRecord]:
        """Newest attempt per node — the view resume decisions are made on."""
        latest: dict[str, NodeRunRecord] = {}
        for record in self.list_node_runs(run_id):
            current = latest.get(record.node_id)
            if current is None or record.attempt > current.attempt:
                latest[record.node_id] = record
        return latest

    def mark_running_nodes_interrupted(
        self, run_id: str, *, now: datetime | None = None
    ) -> list[str]:
        """Reclassify nodes that were mid-flight when the process died.

        Returns the affected node ids. Called only by startup
        reconciliation, after the lease layer has confirmed the owner is
        gone — a live peer's running node must not be touched.
        """
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                "SELECT node_run_id, node_id FROM node_runs WHERE run_id = ? AND status = ?",
                (run_id, st.NODE_RUNNING),
            ).fetchall()
            affected: list[str] = []
            for row in rows:
                conn.execute(
                    """
                    UPDATE node_runs
                    SET status = ?, updated_at = ?, completed_at = ?,
                        error_class = ?, error_code = ?
                    WHERE node_run_id = ?
                    """,
                    (
                        st.NODE_INTERRUPTED,
                        _iso(now),
                        _iso(now),
                        st.ERROR_UNKNOWN,
                        "process_interrupted",
                        str(row["node_run_id"]),
                    ),
                )
                affected.append(str(row["node_id"]))
                self._append_event_locked(
                    conn,
                    run_id=run_id,
                    event_type="node_interrupted",
                    node_id=str(row["node_id"]),
                    payload={"reason": "process_interrupted"},
                    now=now,
                )
            conn.execute("COMMIT")
            return affected
        except Exception:
            _rollback_quietly(conn)
            raise

    # --- events ----------------------------------------------------------

    def append_event(
        self,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        node_id: str | None = None,
        now: datetime | None = None,
    ) -> int:
        """Append a standalone event and return its sequence number."""
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            seq = self._append_event_locked(
                conn,
                run_id=run_id,
                event_type=event_type,
                node_id=node_id,
                payload=payload or {},
                now=now,
            )
            conn.execute("COMMIT")
            return seq
        except Exception:
            _rollback_quietly(conn)
            raise

    def _append_event_locked(
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str,
        event_type: str,
        node_id: str | None,
        payload: dict[str, Any],
        now: datetime,
    ) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS latest FROM run_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        seq = int(row["latest"]) + 1
        conn.execute(
            """
            INSERT INTO run_events (
                run_id, seq, node_id, type, created_at, payload_json_redacted
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                seq,
                node_id,
                event_type,
                _iso(now),
                json.dumps(redact_payload(payload), ensure_ascii=False),
            ),
        )
        return seq

    def events_after(
        self, run_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> list[RunEventRecord]:
        limit = max(1, min(int(limit), 2000))
        rows = self._connect().execute(
            """
            SELECT * FROM run_events
            WHERE run_id = ? AND seq > ?
            ORDER BY seq LIMIT ?
            """,
            (run_id, int(after_seq), limit),
        ).fetchall()
        return [_event(row) for row in rows]

    # --- artifacts --------------------------------------------------------

    def record_artifact(
        self,
        *,
        run_id: str,
        node_id: str,
        artifact_type: str,
        scope: str,
        relative_path: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> ArtifactRecord:
        now = _utc(now)
        artifact_id = uuid.uuid4().hex
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, run_id, node_id, artifact_type, scope,
                    relative_path, media_type, size_bytes, sha256,
                    created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    run_id,
                    node_id,
                    artifact_type,
                    scope,
                    relative_path,
                    media_type,
                    int(size_bytes),
                    sha256,
                    _iso(now),
                    json.dumps(redact_payload(metadata or {}), ensure_ascii=False),
                ),
            )
            self._append_event_locked(
                conn,
                run_id=run_id,
                event_type="artifact_recorded",
                node_id=node_id,
                payload={
                    "artifact_id": artifact_id,
                    "artifact_type": artifact_type,
                    "scope": scope,
                    "size_bytes": int(size_bytes),
                },
                now=now,
            )
            row = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            conn.execute("COMMIT")
            return _artifact(row)
        except Exception:
            _rollback_quietly(conn)
            raise

    def list_artifacts(
        self, run_id: str, node_id: str | None = None
    ) -> list[ArtifactRecord]:
        if node_id:
            rows = self._connect().execute(
                """
                SELECT * FROM artifacts WHERE run_id = ? AND node_id = ?
                ORDER BY created_at, artifact_id
                """,
                (run_id, node_id),
            ).fetchall()
        else:
            rows = self._connect().execute(
                "SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at, artifact_id",
                (run_id,),
            ).fetchall()
        return [_artifact(row) for row in rows]

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        row = self._connect().execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        return _artifact(row) if row is not None else None

    def expire_artifact_payload(
        self, artifact_id: str, *, now: datetime | None = None
    ) -> None:
        """Keep metadata after the bytes are gone (PRD §12.5)."""
        self._connect().execute(
            "UPDATE artifacts SET payload_expired_at = ? WHERE artifact_id = ?",
            (_iso(_utc(now)), artifact_id),
        )

    # --- approvals --------------------------------------------------------

    def create_approval(
        self,
        *,
        run_id: str,
        node_id: str,
        node_attempt: int,
        title: str,
        instructions: str = "",
        now: datetime | None = None,
    ) -> ApprovalRecord:
        """Open a gate. Re-opening the same attempt returns the existing row.

        Idempotency matters here: reconciliation may replay the approval
        node after a crash between the gate write and the run status write.
        """
        now = _utc(now)
        approval_id = uuid.uuid4().hex
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                """
                SELECT * FROM approvals
                WHERE run_id = ? AND node_id = ? AND node_attempt = ?
                """,
                (run_id, node_id, int(node_attempt)),
            ).fetchone()
            if existing is not None:
                conn.execute("COMMIT")
                return _approval(existing)
            conn.execute(
                """
                INSERT INTO approvals (
                    approval_id, run_id, node_id, node_attempt, status, version,
                    title, instructions, requested_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    approval_id,
                    run_id,
                    node_id,
                    int(node_attempt),
                    st.APPROVAL_PENDING,
                    title,
                    instructions,
                    _iso(now),
                ),
            )
            self._append_event_locked(
                conn,
                run_id=run_id,
                event_type="approval_requested",
                node_id=node_id,
                payload={
                    "approval_id": approval_id,
                    "node_attempt": int(node_attempt),
                    "title": title,
                },
                now=now,
            )
            row = conn.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            conn.execute("COMMIT")
            return _approval(row)
        except Exception:
            _rollback_quietly(conn)
            raise

    def resolve_approval(
        self,
        *,
        approval_id: str,
        decision: str,
        expected_version: int | None = None,
        actor: str | None = None,
        source: str = "cli",
        comment: str | None = None,
        now: datetime | None = None,
    ) -> ApprovalRecord:
        """Approve or reject a gate under optimistic locking.

        Three outcomes by design (PRD §11.2): a repeat of the *same*
        decision returns the stored result unchanged, a *conflicting*
        decision raises, and a stale `expected_version` raises. Only a
        pending gate can transition.
        """
        if decision not in st.APPROVAL_DECISIONS:
            raise ApprovalNotFound("unsupported decision", decision=decision)
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise ApprovalNotFound("unknown approval", approval_id=approval_id)
            if str(row["status"]) != st.APPROVAL_PENDING:
                stored = _approval(row)
                conn.execute("ROLLBACK")
                if stored.decision == decision:
                    return stored
                raise ApprovalAlreadyResolved(
                    "gate already resolved with a different decision",
                    approval_id=approval_id,
                    decision=stored.decision,
                )
            if expected_version is not None and int(row["version"]) != int(
                expected_version
            ):
                conn.execute("ROLLBACK")
                raise ApprovalVersionConflict(
                    "approval was modified since it was read",
                    approval_id=approval_id,
                    expected_version=int(expected_version),
                    actual_version=int(row["version"]),
                )
            conn.execute(
                """
                UPDATE approvals
                SET status = ?, decision = ?, version = version + 1,
                    resolved_at = ?, actor = ?, source = ?, comment = ?
                WHERE approval_id = ? AND version = ?
                """,
                (
                    decision,
                    decision,
                    _iso(now),
                    actor,
                    source,
                    redact_and_cap(comment, limit=4096),
                    approval_id,
                    int(row["version"]),
                ),
            )
            self._append_event_locked(
                conn,
                run_id=str(row["run_id"]),
                event_type="approval_resolved",
                node_id=str(row["node_id"]),
                payload={
                    "approval_id": approval_id,
                    "decision": decision,
                    "actor": actor,
                    "source": source,
                },
                now=now,
            )
            updated = conn.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            conn.execute("COMMIT")
            return _approval(updated)
        except Exception:
            _rollback_quietly(conn)
            raise

    def get_approval(self, approval_id: str) -> ApprovalRecord | None:
        row = self._connect().execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        return _approval(row) if row is not None else None

    def list_approvals(
        self, *, status: str | None = None, run_id: str | None = None
    ) -> list[ApprovalRecord]:
        clauses: list[str] = []
        args: list[Any] = []
        if status:
            clauses.append("status = ?")
            args.append(status)
        if run_id:
            clauses.append("run_id = ?")
            args.append(run_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connect().execute(
            f"SELECT * FROM approvals {where} ORDER BY requested_at, approval_id",
            tuple(args),
        ).fetchall()
        return [_approval(row) for row in rows]

    def get_approval_for_node(
        self, *, run_id: str, node_id: str, node_attempt: int
    ) -> ApprovalRecord | None:
        """The gate belonging to one specific node attempt.

        Keyed by attempt, not just node, because a rerun of an approval
        node opens a *new* gate rather than reusing the resolved one.
        """
        row = self._connect().execute(
            """
            SELECT * FROM approvals
            WHERE run_id = ? AND node_id = ? AND node_attempt = ?
            """,
            (run_id, node_id, int(node_attempt)),
        ).fetchone()
        return _approval(row) if row is not None else None

    def pending_approval_for_run(self, run_id: str) -> ApprovalRecord | None:
        rows = self.list_approvals(status=st.APPROVAL_PENDING, run_id=run_id)
        return rows[-1] if rows else None


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    """Roll back if a transaction is still open.

    Several paths above roll back and then raise; the outer `except` would
    otherwise issue a second ROLLBACK and mask the real error with
    "cannot rollback - no transaction is active".
    """
    if not conn.in_transaction:
        return
    try:
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError:
        pass


def _loads(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _int_or_none(value: Any) -> int | None:
    return int(value) if value is not None else None


def _str_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None


def _governed_run(row: sqlite3.Row) -> GovernedRunRecord:
    return GovernedRunRecord(
        run_id=str(row["run_id"]),
        issue_id=str(row["issue_id"]),
        identifier=str(row["identifier"]),
        execution_mode=str(row["execution_mode"] or st.MODE_LEGACY),
        execution_status=str(row["execution_status"] or st.RUN_CREATED),
        attention_reason=_str_or_none(row["attention_reason"]),
        workflow_name=_str_or_none(row["workflow_name"]),
        workflow_version=_int_or_none(row["workflow_version"]),
        workflow_hash=_str_or_none(row["workflow_hash"]),
        ticket_snapshot=_loads(row["ticket_snapshot_json"]) or None,
        terminal_reason=_str_or_none(row["terminal_reason"]),
        input_tokens=_int_or_none(row["input_tokens"]),
        output_tokens=_int_or_none(row["output_tokens"]),
        cost_usd=float(row["cost_usd"]) if row["cost_usd"] is not None else None,
        workspace_path=Path(str(row["workspace_path"])),
        started_at=_parse(row["started_at"]),
        updated_at=_parse(row["updated_at"]),
        completed_at=_parse(row["completed_at"]),
    )


def _node_run(row: sqlite3.Row) -> NodeRunRecord:
    return NodeRunRecord(
        node_run_id=str(row["node_run_id"]),
        run_id=str(row["run_id"]),
        node_id=str(row["node_id"]),
        attempt=int(row["attempt"]),
        node_type=str(row["node_type"]),
        status=str(row["status"]),
        backend_kind=_str_or_none(row["backend_kind"]),
        workspace_access=_str_or_none(row["workspace_access"]),
        started_at=_parse(row["started_at"]),
        updated_at=_parse(row["updated_at"]),
        completed_at=_parse(row["completed_at"]),
        error_class=_str_or_none(row["error_class"]),
        error_code=_str_or_none(row["error_code"]),
        error_message=_str_or_none(row["error_message_redacted"]),
        output_preview=_str_or_none(row["output_preview"]),
        output_sha256=_str_or_none(row["output_sha256"]),
        session_id=_str_or_none(row["session_id"]),
        input_tokens=_int_or_none(row["input_tokens"]),
        output_tokens=_int_or_none(row["output_tokens"]),
        cost_usd=float(row["cost_usd"]) if row["cost_usd"] is not None else None,
        head_before=_str_or_none(row["head_before"]),
        head_after=_str_or_none(row["head_after"]),
        diffstat=_loads(row["diffstat_json"]) or None,
        external_operation_key=_str_or_none(row["external_operation_key"]),
    )


def _event(row: sqlite3.Row) -> RunEventRecord:
    return RunEventRecord(
        run_id=str(row["run_id"]),
        seq=int(row["seq"]),
        node_id=_str_or_none(row["node_id"]),
        type=str(row["type"]),
        created_at=_parse(row["created_at"]),
        payload=_loads(row["payload_json_redacted"]),
    )


def _artifact(row: sqlite3.Row) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=str(row["artifact_id"]),
        run_id=str(row["run_id"]),
        node_id=str(row["node_id"]),
        artifact_type=str(row["artifact_type"]),
        scope=str(row["scope"]),
        relative_path=str(row["relative_path"]),
        media_type=str(row["media_type"]),
        size_bytes=int(row["size_bytes"]),
        sha256=str(row["sha256"]),
        created_at=_parse(row["created_at"]),
        payload_expired_at=_parse(row["payload_expired_at"]),
        metadata=_loads(row["metadata_json"]),
    )


def _approval(row: sqlite3.Row) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=str(row["approval_id"]),
        run_id=str(row["run_id"]),
        node_id=str(row["node_id"]),
        node_attempt=int(row["node_attempt"]),
        status=str(row["status"]),
        version=int(row["version"]),
        title=str(row["title"]),
        instructions=str(row["instructions"] or ""),
        requested_at=_parse(row["requested_at"]),
        resolved_at=_parse(row["resolved_at"]),
        decision=_str_or_none(row["decision"]),
        actor=_str_or_none(row["actor"]),
        source=_str_or_none(row["source"]),
        comment=_str_or_none(row["comment"]),
    )


def _fence(row: sqlite3.Row) -> FenceRecord:
    return FenceRecord(
        issue_id=str(row["issue_id"]),
        run_id=str(row["run_id"]),
        reason=str(row["reason"]),
        created_at=_parse(row["created_at"]),
        updated_at=_parse(row["updated_at"]),
    )


def _snapshot(row: sqlite3.Row) -> WorkflowSnapshotRecord:
    return WorkflowSnapshotRecord(
        workflow_hash=str(row["workflow_hash"]),
        workflow_name=str(row["workflow_name"]),
        schema_version=int(row["schema_version"]),
        normalized_json=str(row["normalized_json"]),
        source_path=str(row["source_path"]),
        created_at=_parse(row["created_at"]),
    )
