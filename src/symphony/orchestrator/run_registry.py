"""SQLite-backed run registry for crash-safe dispatch leases."""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, cast, overload

from ..issue import Issue
from .migrations import LATEST_SCHEMA_VERSION, apply_migrations, current_schema_version


DEFAULT_LEASE_TTL = timedelta(minutes=5)

# Bound how long a locked database can stall a caller. Registry ops run
# inline on the event loop (sqlite connections are thread-affine), so this
# is the worst-case tick delay a contended WAL database can inflict.
SQLITE_BUSY_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    issue_id: str
    identifier: str
    status: str
    workspace_path: Path
    lease_expires_at: datetime | None
    last_progress_at: datetime | None
    attempt: int | None = None
    attempt_kind: str = ""
    agent_kind: str = ""
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
    owner_pid: int | None = None
    owner_boot_id: str | None = None
    backend_agent_pid: int | None = None


@dataclass(frozen=True)
class IssueFlags:
    issue_id: str
    retry_attempt: int | None
    budget_exhausted: bool
    paused: bool
    pause_reason: str | None
    updated_at: datetime


@dataclass(frozen=True)
class ReleaseGate:
    """Host-owned pending/approved identity for one release finalizer."""

    finalizer_identifier: str
    verifier_issue_id: str
    verifier_identifier: str
    expected_contract_sha256: str
    cycle_fingerprint: str
    approved_fingerprint: str | None
    status: str
    target_branch: str | None
    approved_target_sha: str | None
    verifier_run_id: str | None
    updated_at: datetime
    finalizer_run_id: str | None = None
    generation: str = ""
    finalizer_completed_at: datetime | None = None
    finalizer_completion_token: str | None = None


@dataclass(frozen=True)
class ReleaseEvidenceIdentity:
    """Append-only host identity for an evidence-only release ticket."""

    issue_id: str
    identifier: str
    finalizer_identifier: str
    role: str
    cycle_generation: str
    retired: bool
    recorded_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ReleaseCycleItem:
    """Host-owned identity for one repair or fresh verifier board ticket."""

    finalizer_identifier: str
    cycle_fingerprint: str
    item_role: str
    item_key: str
    issue_id: str
    identifier: str
    recorded_at: datetime
    updated_at: datetime


_UNSET = object()


def registry_path_for_workflow(workflow_path: str | Path) -> Path:
    return Path(workflow_path).expanduser().resolve().parent / ".symphony" / "state.db"


def clamp_run_history_limit(limit: int) -> int:
    return max(1, min(int(limit), 200))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # PermissionError and friends: the pid exists but isn't ours.
        # Unknown errors default to "alive" — the safe direction is to
        # honor the lease until its TTL rather than double-dispatch.
        return True
    return True


class RunRegistry:
    """Persist one active dispatch lease per issue in `.symphony/state.db`."""

    def __init__(
        self,
        path: Path,
        lease_ttl: timedelta = DEFAULT_LEASE_TTL,
        *,
        owner_pid: int | None = None,
        boot_id: str | None = None,
    ) -> None:
        self._path = path
        self._lease_ttl = lease_ttl
        self._conn: sqlite3.Connection | None = None
        # Owner identity lets a restarted process distinguish "a dead
        # process's leftover lease" (reclaim now) from "a live peer's lease"
        # (honor until TTL). The boot id is unique per registry instance so
        # our own live leases are never self-reclaimed.
        self._owner_pid = owner_pid if owner_pid is not None else os.getpid()
        self._boot_id = boot_id or uuid.uuid4().hex
        self._applied_migrations: list[int] = []
        self._ensure_schema()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def applied_migrations(self) -> tuple[int, ...]:
        """Schema versions this instance applied at open time (doctor uses it)."""
        return tuple(self._applied_migrations)

    def schema_version(self) -> int:
        return current_schema_version(self._connect())

    def schema_is_current(self) -> bool:
        return self.schema_version() >= LATEST_SCHEMA_VERSION

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def acquire_run(
        self,
        issue: Issue,
        *,
        workspace_path: Path,
        attempt: int | None,
        attempt_kind: str,
        agent_kind: str,
        now: datetime | None = None,
    ) -> str | None:
        now = _utc(now)
        expires = now + self._lease_ttl
        run_id = uuid.uuid4().hex
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            if self._active_issue_locked(issue.id, now) is not None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, issue_id, identifier, title, state, attempt,
                    attempt_kind, agent_kind, workspace_path, status, started_at,
                    updated_at, lease_expires_at, last_progress_at, completed_at,
                    owner_pid, owner_boot_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    run_id,
                    issue.id,
                    issue.identifier,
                    issue.title,
                    issue.state,
                    attempt,
                    attempt_kind,
                    agent_kind,
                    str(workspace_path),
                    _iso(now),
                    _iso(now),
                    _iso(expires),
                    _iso(now),
                    self._owner_pid,
                    self._boot_id,
                ),
            )
            conn.execute("COMMIT")
            return run_id
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def heartbeat(
        self,
        *,
        issue_id: str,
        run_id: str,
        now: datetime | None = None,
        progress_at: datetime | None = None,
        backend_agent_pid: int | None = None,
    ) -> bool:
        now = _utc(now)
        expires = now + self._lease_ttl
        progress = _utc(progress_at) if progress_at is not None else None
        if progress is None and backend_agent_pid is None:
            sql = """
                UPDATE runs
                SET updated_at = ?, lease_expires_at = ?
                WHERE issue_id = ? AND run_id = ? AND status = 'active'
            """
            args = (_iso(now), _iso(expires), issue_id, run_id)
        elif progress is None:
            sql = """
                UPDATE runs
                SET updated_at = ?, lease_expires_at = ?, backend_agent_pid = ?
                WHERE issue_id = ? AND run_id = ? AND status = 'active'
            """
            args = (_iso(now), _iso(expires), backend_agent_pid, issue_id, run_id)
        elif backend_agent_pid is None:
            sql = """
                UPDATE runs
                SET updated_at = ?, lease_expires_at = ?, last_progress_at = ?
                WHERE issue_id = ? AND run_id = ? AND status = 'active'
            """
            args = (_iso(now), _iso(expires), _iso(progress), issue_id, run_id)
        else:
            sql = """
                UPDATE runs
                SET updated_at = ?, lease_expires_at = ?, last_progress_at = ?,
                    backend_agent_pid = ?
                WHERE issue_id = ? AND run_id = ? AND status = 'active'
            """
            args = (
                _iso(now),
                _iso(expires),
                _iso(progress),
                backend_agent_pid,
                issue_id,
                run_id,
            )
        cur = self._connect().execute(sql, args)
        return cur.rowcount > 0

    def clear_backend_agent_pid(
        self,
        *,
        issue_id: str,
        run_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Clear process ownership without overloading heartbeat(None)."""
        now = _utc(now)
        expires = now + self._lease_ttl
        cur = self._connect().execute(
            """
            UPDATE runs
            SET updated_at = ?, lease_expires_at = ?, backend_agent_pid = NULL
            WHERE issue_id = ? AND run_id = ? AND status = 'active'
            """,
            (_iso(now), _iso(expires), issue_id, run_id),
        )
        return cur.rowcount > 0

    def complete_run(
        self,
        *,
        issue_id: str,
        run_id: str,
        status: str,
        now: datetime | None = None,
    ) -> bool:
        now = _utc(now)
        cur = self._connect().execute(
            """
            UPDATE runs
            SET status = ?, updated_at = ?, completed_at = ?, lease_expires_at = NULL
            WHERE issue_id = ? AND run_id = ?
            """,
            (status, _iso(now), _iso(now), issue_id, run_id),
        )
        return cur.rowcount > 0

    def has_active_lease(self, issue_id: str, now: datetime | None = None) -> bool:
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            found = self._active_issue_locked(issue_id, now) is not None
            conn.execute("COMMIT")
            return found
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def active_leases(self, now: datetime | None = None) -> list[RunRecord]:
        now = _utc(now)
        self.expire_stale(now=now)
        rows = self._connect().execute(
            """
            SELECT * FROM runs
            WHERE status = 'reclaiming'
               OR (status = 'active' AND lease_expires_at > ?)
            ORDER BY started_at, run_id
            """,
            (_iso(now),),
        )
        return [_record(row) for row in rows.fetchall()]

    def bind_release_verifier_run(
        self,
        *,
        gate: ReleaseGate,
        verifier_run_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Bind one exact active run to the current pending verifier cycle."""
        if gate.status != "pending":
            return False
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            active = self._active_issue_locked(gate.verifier_issue_id, now)
            if (
                active is None
                or str(active["run_id"]) != verifier_run_id
                or str(active["identifier"]) != gate.verifier_identifier
                or str(active["status"]) != "active"
            ):
                conn.execute("ROLLBACK")
                return False
            current = conn.execute(
                """
                SELECT verifier_run_id, updated_at FROM release_gates
                WHERE finalizer_identifier = ?
                  AND verifier_issue_id = ?
                  AND verifier_identifier = ?
                  AND expected_contract_sha256 = ?
                  AND cycle_fingerprint = ?
                  AND generation = ?
                  AND status = 'pending'
                """,
                (
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    gate.generation,
                ),
            ).fetchone()
            if current is None:
                conn.execute("ROLLBACK")
                return False
            if str(active["started_at"]) < str(current["updated_at"]):
                conn.execute("ROLLBACK")
                return False
            bound_run_id = current["verifier_run_id"]
            if bound_run_id and str(bound_run_id) != verifier_run_id:
                prior = conn.execute(
                    "SELECT status, lease_expires_at FROM runs WHERE run_id = ?",
                    (str(bound_run_id),),
                ).fetchone()
                if prior is not None and (
                    str(prior["status"]) == "reclaiming"
                    or (
                        str(prior["status"]) == "active"
                        and prior["lease_expires_at"] is not None
                        and str(prior["lease_expires_at"]) > _iso(now)
                    )
                ):
                    conn.execute("ROLLBACK")
                    return False
            cur = conn.execute(
                """
                UPDATE release_gates SET verifier_run_id = ?
                WHERE finalizer_identifier = ?
                  AND verifier_issue_id = ?
                  AND verifier_identifier = ?
                  AND expected_contract_sha256 = ?
                  AND cycle_fingerprint = ?
                  AND generation = ?
                  AND status = 'pending'
                """,
                (
                    verifier_run_id,
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    gate.generation,
                ),
            )
            conn.execute("COMMIT")
            return cur.rowcount == 1
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def bind_release_finalizer_run(
        self,
        *,
        gate: ReleaseGate,
        finalizer_issue_id: str,
        finalizer_run_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Bind an exact active finalizer lease to the approved gate cycle."""
        if gate.status != "approved":
            return False
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            active = self._active_issue_locked(finalizer_issue_id, now)
            if (
                active is None
                or str(active["run_id"]) != finalizer_run_id
                or str(active["identifier"]) != gate.finalizer_identifier
                or str(active["status"]) != "active"
            ):
                conn.execute("ROLLBACK")
                return False
            if self._active_issue_locked(gate.verifier_issue_id, now) is not None:
                conn.execute("ROLLBACK")
                return False
            current = conn.execute(
                """
                SELECT updated_at, finalizer_run_id, finalizer_completed_at
                FROM release_gates
                WHERE finalizer_identifier = ?
                  AND verifier_issue_id = ?
                  AND verifier_identifier = ?
                  AND expected_contract_sha256 = ?
                  AND cycle_fingerprint = ?
                  AND generation = ?
                  AND approved_fingerprint = ?
                  AND target_branch = ?
                  AND approved_target_sha = ?
                  AND verifier_run_id = ?
                  AND status = 'approved'
                """,
                (
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    gate.generation,
                    gate.approved_fingerprint,
                    gate.target_branch,
                    gate.approved_target_sha,
                    gate.verifier_run_id,
                ),
            ).fetchone()
            if current is None or str(active["started_at"]) < str(
                current["updated_at"]
            ):
                conn.execute("ROLLBACK")
                return False
            prior_finalizer_run_id = current["finalizer_run_id"]
            if (
                current["finalizer_completed_at"] is not None
                or (
                    prior_finalizer_run_id is not None
                    and str(prior_finalizer_run_id) != finalizer_run_id
                    and self._run_is_live_locked(
                        str(prior_finalizer_run_id), now
                    )
                )
            ):
                conn.execute("ROLLBACK")
                return False
            cur = conn.execute(
                """
                UPDATE release_gates SET finalizer_run_id = ?
                WHERE finalizer_identifier = ?
                  AND verifier_issue_id = ?
                  AND verifier_identifier = ?
                  AND expected_contract_sha256 = ?
                  AND cycle_fingerprint = ?
                  AND generation = ?
                  AND approved_fingerprint = ?
                  AND target_branch = ?
                  AND approved_target_sha = ?
                  AND verifier_run_id = ?
                  AND status = 'approved'
                  AND finalizer_completed_at IS NULL
                """,
                (
                    finalizer_run_id,
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    gate.generation,
                    gate.approved_fingerprint,
                    gate.target_branch,
                    gate.approved_target_sha,
                    gate.verifier_run_id,
                ),
            )
            conn.execute("COMMIT")
            return cur.rowcount == 1
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def release_finalizer_run_is_authorized(
        self,
        *,
        gate: ReleaseGate,
        finalizer_issue_id: str,
        allow_active: bool = True,
        now: datetime | None = None,
    ) -> bool:
        """Check current gate and exact bound run in one read transaction."""
        if gate.status != "approved" or not gate.finalizer_run_id:
            return False
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            row = conn.execute(
                """
                SELECT 1
                FROM release_gates AS gate
                JOIN runs AS run ON run.run_id = gate.finalizer_run_id
                WHERE gate.finalizer_identifier = ?
                  AND gate.verifier_issue_id = ?
                  AND gate.verifier_identifier = ?
                  AND gate.expected_contract_sha256 = ?
                  AND gate.cycle_fingerprint = ?
                  AND gate.generation = ?
                  AND gate.approved_fingerprint = ?
                  AND gate.target_branch = ?
                  AND gate.approved_target_sha = ?
                  AND gate.verifier_run_id = ?
                  AND gate.finalizer_run_id = ?
                  AND gate.status = 'approved'
                  AND (
                    (gate.finalizer_completed_at IS NULL AND ? IS NULL)
                    OR gate.finalizer_completed_at = ?
                  )
                  AND (
                    (gate.finalizer_completion_token IS NULL AND ? IS NULL)
                    OR gate.finalizer_completion_token = ?
                  )
                  AND run.issue_id = ?
                  AND run.identifier = gate.finalizer_identifier
                  AND (
                    (? = 1 AND run.status = 'active' AND run.lease_expires_at > ?)
                    OR (
                        run.status = 'normal'
                        AND run.completed_at IS NOT NULL
                        AND gate.finalizer_completed_at IS NOT NULL
                        AND gate.finalizer_completion_token IS NOT NULL
                    )
                  )
                LIMIT 1
                """,
                (
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    gate.generation,
                    gate.approved_fingerprint,
                    gate.target_branch,
                    gate.approved_target_sha,
                    gate.verifier_run_id,
                    gate.finalizer_run_id,
                    (
                        _iso(gate.finalizer_completed_at)
                        if gate.finalizer_completed_at is not None
                        else None
                    ),
                    (
                        _iso(gate.finalizer_completed_at)
                        if gate.finalizer_completed_at is not None
                        else None
                    ),
                    gate.finalizer_completion_token,
                    gate.finalizer_completion_token,
                    finalizer_issue_id,
                    1 if allow_active else 0,
                    _iso(now),
                ),
            ).fetchone()
            conn.execute("COMMIT")
            return row is not None
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def release_verifier_run_is_authorized(
        self,
        *,
        gate: ReleaseGate,
        verifier_issue_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Check the exact current gate generation and active verifier run."""
        if not gate.verifier_run_id:
            return False
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            row = conn.execute(
                """
                SELECT 1
                FROM release_gates AS gate
                JOIN runs AS run ON run.run_id = gate.verifier_run_id
                WHERE gate.finalizer_identifier = ?
                  AND gate.verifier_issue_id = ?
                  AND gate.verifier_identifier = ?
                  AND gate.expected_contract_sha256 = ?
                  AND gate.cycle_fingerprint = ?
                  AND gate.generation = ?
                  AND gate.verifier_run_id = ?
                  AND gate.status IN ('pending', 'approved')
                  AND run.issue_id = ?
                  AND run.identifier = gate.verifier_identifier
                  AND run.status = 'active'
                  AND run.lease_expires_at > ?
                LIMIT 1
                """,
                (
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    gate.generation,
                    gate.verifier_run_id,
                    verifier_issue_id,
                    _iso(now),
                ),
            ).fetchone()
            conn.execute("COMMIT")
            return row is not None
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def mark_release_finalizer_completed(
        self,
        *,
        gate: ReleaseGate,
        finalizer_issue_id: str,
        completion_token: str,
        now: datetime | None = None,
    ) -> bool:
        """Persist terminal delivery proof for the exact active finalizer run."""
        if (
            gate.status != "approved"
            or not gate.finalizer_run_id
            or not completion_token.strip()
        ):
            return False
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            active = self._active_issue_locked(finalizer_issue_id, now)
            if (
                active is None
                or str(active["run_id"]) != gate.finalizer_run_id
                or str(active["identifier"]) != gate.finalizer_identifier
                or str(active["status"]) != "active"
                or self._active_issue_locked(gate.verifier_issue_id, now) is not None
            ):
                conn.execute("ROLLBACK")
                return False
            cur = conn.execute(
                """
                UPDATE release_gates
                SET finalizer_completed_at = COALESCE(finalizer_completed_at, ?),
                    finalizer_completion_token = COALESCE(
                        finalizer_completion_token, ?
                    )
                WHERE finalizer_identifier = ?
                  AND verifier_issue_id = ?
                  AND verifier_identifier = ?
                  AND expected_contract_sha256 = ?
                  AND cycle_fingerprint = ?
                  AND generation = ?
                  AND approved_fingerprint = ?
                  AND target_branch = ?
                  AND approved_target_sha = ?
                  AND verifier_run_id = ?
                  AND finalizer_run_id = ?
                  AND status = 'approved'
                """,
                (
                    _iso(now),
                    completion_token,
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    gate.generation,
                    gate.approved_fingerprint,
                    gate.target_branch,
                    gate.approved_target_sha,
                    gate.verifier_run_id,
                    gate.finalizer_run_id,
                ),
            )
            conn.execute("COMMIT")
            return cur.rowcount == 1
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def replace_pending_release_gate(
        self,
        gate: ReleaseGate,
        *,
        now: datetime | None = None,
        invalidating_finalizer_run_id: str | None = None,
    ) -> ReleaseGate:
        """Atomically invalidate approval and create a new cycle generation.

        A live finalizer normally fences replacement.  The one exception is
        the exact finalizer run that discovered its own approval became stale;
        it may invalidate that cycle before releasing its lease.  The caller
        must provide that bound run id, which is checked under the same write
        transaction as the replacement.
        """
        if gate.status != "pending":
            raise ValueError("replacement release gate must have pending status")
        now = _utc(now)
        generation = uuid.uuid4().hex
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            prior = conn.execute(
                """
                SELECT verifier_issue_id, verifier_identifier, verifier_run_id,
                       generation, finalizer_run_id
                FROM release_gates WHERE finalizer_identifier = ?
                """,
                (gate.finalizer_identifier,),
            ).fetchone()
            if prior is not None:
                self._expire_stale_locked(now)
                prior_verifier_run_id = prior["verifier_run_id"]
                active_verifier = self._active_issue_locked(
                    str(prior["verifier_issue_id"]), now
                )
                if active_verifier is not None and (
                    prior_verifier_run_id is None
                    or str(active_verifier["run_id"])
                    != str(prior_verifier_run_id)
                ):
                    raise RuntimeError(
                        "release verifier cleanup or foreign lease must finish "
                        "before replacing its gate"
                    )
                prior_finalizer_run_id = prior["finalizer_run_id"]
                active_finalizer = self._active_identifier_locked(
                    gate.finalizer_identifier, now
                )
                if invalidating_finalizer_run_id is not None:
                    if (
                        prior_finalizer_run_id is None
                        or str(prior_finalizer_run_id)
                        != invalidating_finalizer_run_id
                        or active_finalizer is None
                        or str(active_finalizer["run_id"])
                        != invalidating_finalizer_run_id
                    ):
                        raise RuntimeError(
                            "only the exact active release finalizer may invalidate "
                            "its gate"
                        )
                elif active_finalizer is not None:
                    raise RuntimeError(
                        "active release finalizer must finish before replacing its gate"
                    )
                conn.execute(
                    """
                    INSERT INTO release_evidence_issues (
                        issue_id, identifier, finalizer_identifier, role,
                        cycle_generation, retired, recorded_at, updated_at
                    ) VALUES (?, ?, ?, 'verifier', ?, 1, ?, ?)
                    ON CONFLICT(issue_id) DO UPDATE SET
                        identifier = excluded.identifier,
                        finalizer_identifier = excluded.finalizer_identifier,
                        role = 'verifier',
                        cycle_generation = excluded.cycle_generation,
                        retired = 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(prior["verifier_issue_id"]),
                        str(prior["verifier_identifier"]),
                        gate.finalizer_identifier,
                        str(prior["generation"] or ""),
                        _iso(now),
                        _iso(now),
                    ),
                )
            conn.execute(
                """
                INSERT INTO release_gates (
                    finalizer_identifier, verifier_issue_id, verifier_identifier,
                    expected_contract_sha256, cycle_fingerprint,
                    approved_fingerprint, status, target_branch,
                    approved_target_sha, verifier_run_id, finalizer_run_id,
                    generation, finalizer_completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, 'pending', NULL, NULL, NULL, NULL,
                          ?, NULL, ?)
                ON CONFLICT(finalizer_identifier) DO UPDATE SET
                    verifier_issue_id = excluded.verifier_issue_id,
                    verifier_identifier = excluded.verifier_identifier,
                    expected_contract_sha256 = excluded.expected_contract_sha256,
                    cycle_fingerprint = excluded.cycle_fingerprint,
                    approved_fingerprint = NULL,
                    status = 'pending',
                    target_branch = NULL,
                    approved_target_sha = NULL,
                    verifier_run_id = NULL,
                    finalizer_run_id = NULL,
                    generation = excluded.generation,
                    finalizer_completed_at = NULL,
                    finalizer_completion_token = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    gate.finalizer_identifier,
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.expected_contract_sha256,
                    gate.cycle_fingerprint,
                    generation,
                    _iso(now),
                ),
            )
            conn.execute(
                """
                INSERT INTO release_evidence_issues (
                    issue_id, identifier, finalizer_identifier, role,
                    cycle_generation, retired, recorded_at, updated_at
                ) VALUES (?, ?, ?, 'verifier', ?, 0, ?, ?)
                ON CONFLICT(issue_id) DO UPDATE SET
                    identifier = excluded.identifier,
                    finalizer_identifier = excluded.finalizer_identifier,
                    role = 'verifier',
                    cycle_generation = excluded.cycle_generation,
                    retired = 0,
                    updated_at = excluded.updated_at
                """,
                (
                    gate.verifier_issue_id,
                    gate.verifier_identifier,
                    gate.finalizer_identifier,
                    generation,
                    _iso(now),
                    _iso(now),
                ),
            )
            row = conn.execute(
                "SELECT * FROM release_gates WHERE finalizer_identifier = ?",
                (gate.finalizer_identifier,),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        if row is None:
            raise RuntimeError("pending release gate disappeared after replacement")
        return _release_gate(row)

    def approve_release_gate(
        self,
        *,
        finalizer_identifier: str,
        verifier_issue_id: str,
        verifier_identifier: str,
        expected_contract_sha256: str,
        expected_cycle_fingerprint: str,
        expected_generation: str,
        approved_fingerprint: str,
        target_branch: str,
        target_sha: str,
        verifier_run_id: str,
        now: datetime | None = None,
    ) -> bool:
        """Approve the exact pending tuple and exact active run atomically."""
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._expire_stale_locked(now)
            active = self._active_issue_locked(verifier_issue_id, now)
            if (
                active is None
                or str(active["run_id"]) != verifier_run_id
                or str(active["identifier"]) != verifier_identifier
                or str(active["status"]) != "active"
            ):
                conn.execute("ROLLBACK")
                return False
            pending = conn.execute(
                """
                SELECT updated_at, verifier_run_id FROM release_gates
                WHERE finalizer_identifier = ?
                  AND verifier_issue_id = ?
                  AND verifier_identifier = ?
                  AND expected_contract_sha256 = ?
                  AND cycle_fingerprint = ?
                  AND generation = ?
                  AND status = 'pending'
                """,
                (
                    finalizer_identifier,
                    verifier_issue_id,
                    verifier_identifier,
                    expected_contract_sha256,
                    expected_cycle_fingerprint,
                    expected_generation,
                ),
            ).fetchone()
            if (
                pending is None
                or str(pending["verifier_run_id"] or "") != verifier_run_id
                or str(active["started_at"]) < str(pending["updated_at"])
            ):
                conn.execute("ROLLBACK")
                return False
            cur = conn.execute(
                """
                UPDATE release_gates
                SET status = 'approved', target_branch = ?, approved_target_sha = ?,
                    approved_fingerprint = ?, verifier_run_id = ?,
                    finalizer_run_id = NULL, updated_at = ?
                WHERE finalizer_identifier = ?
                  AND verifier_issue_id = ?
                  AND verifier_identifier = ?
                  AND expected_contract_sha256 = ?
                  AND cycle_fingerprint = ?
                  AND generation = ?
                  AND verifier_run_id = ?
                  AND status = 'pending'
                """,
                (
                    target_branch,
                    target_sha,
                    approved_fingerprint,
                    verifier_run_id,
                    _iso(now),
                    finalizer_identifier,
                    verifier_issue_id,
                    verifier_identifier,
                    expected_contract_sha256,
                    expected_cycle_fingerprint,
                    expected_generation,
                    verifier_run_id,
                ),
            )
            conn.execute("COMMIT")
            return cur.rowcount == 1
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def get_release_gate(
        self, finalizer_identifier: str
    ) -> ReleaseGate | None:
        row = self._connect().execute(
            "SELECT * FROM release_gates WHERE finalizer_identifier = ?",
            (finalizer_identifier,),
        ).fetchone()
        return _release_gate(row) if row is not None else None

    def get_release_gate_for_verifier(
        self, verifier_identifier: str
    ) -> ReleaseGate | None:
        row = self._connect().execute(
            "SELECT * FROM release_gates WHERE verifier_identifier = ?",
            (verifier_identifier,),
        ).fetchone()
        return _release_gate(row) if row is not None else None

    def get_release_evidence_identity(
        self, identifier: str
    ) -> ReleaseEvidenceIdentity | None:
        row = self._connect().execute(
            "SELECT * FROM release_evidence_issues WHERE identifier = ?",
            (identifier,),
        ).fetchone()
        return _release_evidence_identity(row) if row is not None else None

    def get_release_cycle_item(
        self,
        *,
        finalizer_identifier: str,
        cycle_fingerprint: str,
        item_role: str,
        item_key: str,
    ) -> ReleaseCycleItem | None:
        """Return the host-owned ticket identity for one lifecycle item."""
        row = self._connect().execute(
            """
            SELECT * FROM release_cycle_items
            WHERE finalizer_identifier = ?
              AND cycle_fingerprint = ?
              AND item_role = ?
              AND item_key = ?
            """,
            (
                finalizer_identifier,
                cycle_fingerprint,
                item_role,
                item_key,
            ),
        ).fetchone()
        return _release_cycle_item(row) if row is not None else None

    def record_release_cycle_item(
        self,
        *,
        finalizer_identifier: str,
        cycle_fingerprint: str,
        item_role: str,
        item_key: str,
        issue: Issue,
        now: datetime | None = None,
    ) -> ReleaseCycleItem:
        """Bind one logical release-cycle item to exactly one board ticket.

        The mapping is immutable. A retry may record the same tuple again,
        but it may never redirect a repair/verifier key to a different ticket
        or reuse one ticket for a different logical item.
        """
        return self._record_release_cycle_item_identity(
            finalizer_identifier=finalizer_identifier,
            cycle_fingerprint=cycle_fingerprint,
            item_role=item_role,
            item_key=item_key,
            issue_id=issue.id,
            identifier=issue.identifier,
            now=now,
        )

    def reserve_release_cycle_item(
        self,
        *,
        finalizer_identifier: str,
        cycle_fingerprint: str,
        item_role: str,
        item_key: str,
        identifier: str,
        now: datetime | None = None,
    ) -> ReleaseCycleItem:
        """Durably reserve a deterministic local-board ticket before create.

        File-board creation and SQLite cannot share one transaction.  By
        writing the exact host-derived identifier first, a crash after the
        board write can be resumed without trusting worker-editable labels or
        allocating a duplicate ticket.  File-board issue ids equal their
        identifiers, so the final readback can only confirm this same tuple.
        """
        return self._record_release_cycle_item_identity(
            finalizer_identifier=finalizer_identifier,
            cycle_fingerprint=cycle_fingerprint,
            item_role=item_role,
            item_key=item_key,
            issue_id=identifier,
            identifier=identifier,
            now=now,
        )

    def _record_release_cycle_item_identity(
        self,
        *,
        finalizer_identifier: str,
        cycle_fingerprint: str,
        item_role: str,
        item_key: str,
        issue_id: str,
        identifier: str,
        now: datetime | None,
    ) -> ReleaseCycleItem:
        if item_role not in {"repair", "verifier"}:
            raise ValueError("release cycle item role must be repair or verifier")
        if not all(
            value.strip()
            for value in (
                finalizer_identifier,
                cycle_fingerprint,
                item_key,
                issue_id,
                identifier,
            )
        ):
            raise ValueError("release cycle item identity fields must be non-empty")
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                """
                SELECT * FROM release_cycle_items
                WHERE finalizer_identifier = ?
                  AND cycle_fingerprint = ?
                  AND item_role = ?
                  AND item_key = ?
                """,
                (
                    finalizer_identifier,
                    cycle_fingerprint,
                    item_role,
                    item_key,
                ),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["issue_id"]) != issue_id
                    or str(existing["identifier"]) != identifier
                ):
                    raise RuntimeError(
                        "release cycle item is already bound to a different ticket"
                    )
                conn.execute(
                    """
                    UPDATE release_cycle_items SET updated_at = ?
                    WHERE finalizer_identifier = ?
                      AND cycle_fingerprint = ?
                      AND item_role = ?
                      AND item_key = ?
                    """,
                    (
                        _iso(now),
                        finalizer_identifier,
                        cycle_fingerprint,
                        item_role,
                        item_key,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO release_cycle_items (
                        finalizer_identifier, cycle_fingerprint, item_role,
                        item_key, issue_id, identifier, recorded_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        finalizer_identifier,
                        cycle_fingerprint,
                        item_role,
                        item_key,
                        issue_id,
                        identifier,
                        _iso(now),
                        _iso(now),
                    ),
                )
            row = conn.execute(
                """
                SELECT * FROM release_cycle_items
                WHERE finalizer_identifier = ?
                  AND cycle_fingerprint = ?
                  AND item_role = ?
                  AND item_key = ?
                """,
                (
                    finalizer_identifier,
                    cycle_fingerprint,
                    item_role,
                    item_key,
                ),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        if row is None:
            raise RuntimeError("release cycle item disappeared after recording")
        return _release_cycle_item(row)

    def has_release_authority(self) -> bool:
        row = self._connect().execute(
            """
            SELECT 1 FROM release_gates
            UNION ALL
            SELECT 1 FROM release_evidence_issues
            UNION ALL
            SELECT 1 FROM release_cycle_items
            LIMIT 1
            """
        ).fetchone()
        return row is not None

    def invalidate_release_gate(self, finalizer_identifier: str) -> bool:
        cur = self._connect().execute(
            "DELETE FROM release_gates WHERE finalizer_identifier = ?",
            (finalizer_identifier,),
        )
        return cur.rowcount > 0

    def expire_stale(self, now: datetime | None = None) -> int:
        now = _utc(now)
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            count = self._expire_stale_locked(now)
            conn.execute("COMMIT")
            return count
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def reclaim_dead_owner_leases(
        self,
        now: datetime | None = None,
        *,
        pid_alive: Callable[[int], bool] | None = None,
    ) -> list[RunRecord]:
        """Free unexpired leases whose owner process is gone.

        A crashed process's last heartbeat can push `lease_expires_at` up to
        a full TTL into the future; without this pass a restart cannot
        re-dispatch the interrupted ticket for minutes. Leases owned by this
        registry instance (same boot id) or by a live pid are left alone —
        the safe direction for pid reuse is to wait out the TTL.
        Rows first enter `reclaiming`, which remains lease-blocking while the
        caller performs OS cleanup outside this transaction. Existing
        `reclaiming` rows are returned again so a crash between claim, kill,
        and finalize is retry-safe.
        """
        now = _utc(now)
        alive = pid_alive or _pid_alive
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                """
                SELECT * FROM runs
                WHERE status = 'reclaiming'
                   OR (status = 'active' AND lease_expires_at > ?)
                ORDER BY started_at, run_id
                """,
                (_iso(now),),
            ).fetchall()
            reclaimed: list[RunRecord] = []
            for row in rows:
                if row["status"] == "reclaiming":
                    reclaimed.append(_record(row))
                    continue
                if row["owner_boot_id"] == self._boot_id:
                    continue
                pid = row["owner_pid"]
                if pid is not None and alive(int(pid)):
                    continue
                conn.execute(
                    """
                    UPDATE runs
                    SET status = 'reclaiming', updated_at = ?, completed_at = NULL
                    WHERE run_id = ?
                    """,
                    (_iso(now), row["run_id"]),
                )
                claimed = conn.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (row["run_id"],)
                ).fetchone()
                if claimed is not None:
                    reclaimed.append(_record(claimed))
            conn.execute("COMMIT")
            return reclaimed
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def finalize_reclaimed_lease(
        self, run_id: str, now: datetime | None = None
    ) -> bool:
        """Release a reclaim fence after external process cleanup completes."""
        now = _utc(now)
        cur = self._connect().execute(
            """
            UPDATE runs
            SET status = 'orphaned', updated_at = ?, completed_at = ?,
                lease_expires_at = NULL
            WHERE run_id = ? AND status = 'reclaiming'
            """,
            (_iso(now), _iso(now), run_id),
        )
        return cur.rowcount > 0

    def get_run(self, run_id: str) -> RunRecord:
        row = self._connect().execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _record(row)

    def recent_runs(
        self, issue_id: str | None = None, limit: int = 50
    ) -> list[RunRecord]:
        """Return newest run rows, clamping limit into [1, 200]."""
        limit = clamp_run_history_limit(limit)
        if issue_id:
            rows = self._connect().execute(
                """
                SELECT * FROM runs
                WHERE issue_id = ? OR identifier = ?
                ORDER BY rowid DESC
                LIMIT ?
                """,
                (issue_id, issue_id, limit),
            ).fetchall()
        else:
            rows = self._connect().execute(
                """
                SELECT * FROM runs
                ORDER BY rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_record(row) for row in rows]

    def get_issue_flags(self, issue_id: str) -> IssueFlags | None:
        row = self._connect().execute(
            "SELECT * FROM issue_flags WHERE issue_id = ?",
            (issue_id,),
        ).fetchone()
        return _issue_flags(row) if row is not None else None

    def list_issue_flags(self) -> list[IssueFlags]:
        rows = self._connect().execute(
            "SELECT * FROM issue_flags ORDER BY issue_id"
        ).fetchall()
        return [_issue_flags(row) for row in rows]

    def set_issue_flags(
        self,
        issue_id: str,
        *,
        retry_attempt: int | None | object = _UNSET,
        budget_exhausted: bool | object = _UNSET,
        paused: bool | object = _UNSET,
        pause_reason: str | None | object = _UNSET,
        now: datetime | None = None,
    ) -> None:
        existing = self.get_issue_flags(issue_id)
        next_retry_attempt = (
            existing.retry_attempt if existing is not None else None
        )
        next_budget_exhausted = (
            existing.budget_exhausted if existing is not None else False
        )
        next_paused = existing.paused if existing is not None else False
        next_pause_reason = existing.pause_reason if existing is not None else None
        if retry_attempt is not _UNSET:
            next_retry_attempt = cast("int | None", retry_attempt)
        if budget_exhausted is not _UNSET:
            next_budget_exhausted = bool(budget_exhausted)
        if paused is not _UNSET:
            next_paused = bool(paused)
        if pause_reason is not _UNSET:
            next_pause_reason = cast("str | None", pause_reason)
        elif not next_paused:
            next_pause_reason = None
        self._write_issue_flags(
            issue_id,
            retry_attempt=next_retry_attempt,
            budget_exhausted=next_budget_exhausted,
            paused=next_paused,
            pause_reason=next_pause_reason,
            now=now,
        )

    def clear_issue_flags(
        self,
        issue_id: str,
        *,
        retry_attempt: bool = False,
        budget_exhausted: bool = False,
        paused: bool = False,
        now: datetime | None = None,
    ) -> None:
        existing = self.get_issue_flags(issue_id)
        if existing is None:
            return
        self._write_issue_flags(
            issue_id,
            retry_attempt=None if retry_attempt else existing.retry_attempt,
            budget_exhausted=False if budget_exhausted else existing.budget_exhausted,
            paused=False if paused else existing.paused,
            pause_reason=None if paused else existing.pause_reason,
            now=now,
        )

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                self._path, timeout=SQLITE_BUSY_TIMEOUT_S, isolation_level=None
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def _ensure_schema(self) -> None:
        """Bring the database up to the latest schema version.

        The DDL itself lives in `migrations.py`; this stays a one-liner so
        there is exactly one place that decides what the schema looks like.
        """
        self._applied_migrations = apply_migrations(self._connect(), self._path)

    def _write_issue_flags(
        self,
        issue_id: str,
        *,
        retry_attempt: int | None,
        budget_exhausted: bool,
        paused: bool,
        pause_reason: str | None,
        now: datetime | None,
    ) -> None:
        conn = self._connect()
        if not paused:
            pause_reason = None
        if retry_attempt is None and not budget_exhausted and not paused:
            conn.execute("DELETE FROM issue_flags WHERE issue_id = ?", (issue_id,))
            return
        updated_at = _iso(_utc(now))
        conn.execute(
            """
            INSERT INTO issue_flags (
                issue_id, retry_attempt, budget_exhausted, paused,
                pause_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(issue_id) DO UPDATE SET
                retry_attempt = excluded.retry_attempt,
                budget_exhausted = excluded.budget_exhausted,
                paused = excluded.paused,
                pause_reason = excluded.pause_reason,
                updated_at = excluded.updated_at
            """,
            (
                issue_id,
                retry_attempt,
                1 if budget_exhausted else 0,
                1 if paused else 0,
                pause_reason,
                updated_at,
            ),
        )

    def _run_is_live_locked(self, run_id: str, now: datetime) -> bool:
        row = self._connect().execute(
            "SELECT status, lease_expires_at FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return False
        return str(row["status"]) == "reclaiming" or (
            str(row["status"]) == "active"
            and row["lease_expires_at"] is not None
            and str(row["lease_expires_at"]) > _iso(now)
        )

    def _active_issue_locked(
        self, issue_id: str, now: datetime
    ) -> sqlite3.Row | None:
        return self._connect().execute(
            """
            SELECT * FROM runs
            WHERE issue_id = ?
              AND (
                  status = 'reclaiming'
                  OR (status = 'active' AND lease_expires_at > ?)
              )
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (issue_id, _iso(now)),
        ).fetchone()

    def _active_identifier_locked(
        self, identifier: str, now: datetime
    ) -> sqlite3.Row | None:
        return self._connect().execute(
            """
            SELECT * FROM runs
            WHERE identifier = ?
              AND (
                  status = 'reclaiming'
                  OR (status = 'active' AND lease_expires_at > ?)
              )
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (identifier, _iso(now)),
        ).fetchone()

    def _expire_stale_locked(self, now: datetime) -> int:
        cur = self._connect().execute(
            """
            UPDATE runs
            SET status = 'expired', updated_at = ?, completed_at = ?
            WHERE status = 'active'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (_iso(now), _iso(now), _iso(now)),
        )
        return cur.rowcount


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@overload
def _iso(value: datetime) -> str: ...


@overload
def _iso(value: None) -> None: ...


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _record(row: sqlite3.Row) -> RunRecord:
    owner_pid = row["owner_pid"]
    backend_agent_pid = row["backend_agent_pid"]
    return RunRecord(
        run_id=str(row["run_id"]),
        issue_id=str(row["issue_id"]),
        identifier=str(row["identifier"]),
        status=str(row["status"]),
        workspace_path=Path(str(row["workspace_path"])),
        lease_expires_at=_parse(row["lease_expires_at"]),
        last_progress_at=_parse(row["last_progress_at"]),
        attempt=int(row["attempt"]) if row["attempt"] is not None else None,
        attempt_kind=str(row["attempt_kind"]),
        agent_kind=str(row["agent_kind"]),
        started_at=_parse(row["started_at"]),
        updated_at=_parse(row["updated_at"]),
        completed_at=_parse(row["completed_at"]),
        owner_pid=int(owner_pid) if owner_pid is not None else None,
        owner_boot_id=row["owner_boot_id"],
        backend_agent_pid=(
            int(backend_agent_pid) if backend_agent_pid is not None else None
        ),
    )


def _issue_flags(row: sqlite3.Row) -> IssueFlags:
    return IssueFlags(
        issue_id=str(row["issue_id"]),
        retry_attempt=(
            int(row["retry_attempt"]) if row["retry_attempt"] is not None else None
        ),
        budget_exhausted=bool(row["budget_exhausted"]),
        paused=bool(row["paused"]),
        pause_reason=row["pause_reason"],
        updated_at=_parse(row["updated_at"]) or datetime.now(timezone.utc),
    )


def _release_gate(row: sqlite3.Row) -> ReleaseGate:
    return ReleaseGate(
        finalizer_identifier=str(row["finalizer_identifier"]),
        verifier_issue_id=str(row["verifier_issue_id"]),
        verifier_identifier=str(row["verifier_identifier"]),
        expected_contract_sha256=str(row["expected_contract_sha256"]),
        cycle_fingerprint=str(row["cycle_fingerprint"]),
        approved_fingerprint=(
            str(row["approved_fingerprint"])
            if row["approved_fingerprint"]
            else None
        ),
        status=str(row["status"]),
        target_branch=(str(row["target_branch"]) if row["target_branch"] else None),
        approved_target_sha=(
            str(row["approved_target_sha"])
            if row["approved_target_sha"]
            else None
        ),
        verifier_run_id=(
            str(row["verifier_run_id"]) if row["verifier_run_id"] else None
        ),
        updated_at=_parse(row["updated_at"]) or datetime.now(timezone.utc),
        finalizer_run_id=(
            str(row["finalizer_run_id"]) if row["finalizer_run_id"] else None
        ),
        generation=str(row["generation"] or ""),
        finalizer_completed_at=_parse(row["finalizer_completed_at"]),
        finalizer_completion_token=(
            str(row["finalizer_completion_token"])
            if row["finalizer_completion_token"]
            else None
        ),
    )


def _release_evidence_identity(row: sqlite3.Row) -> ReleaseEvidenceIdentity:
    return ReleaseEvidenceIdentity(
        issue_id=str(row["issue_id"]),
        identifier=str(row["identifier"]),
        finalizer_identifier=str(row["finalizer_identifier"]),
        role=str(row["role"]),
        cycle_generation=str(row["cycle_generation"]),
        retired=bool(row["retired"]),
        recorded_at=_parse(row["recorded_at"]) or datetime.now(timezone.utc),
        updated_at=_parse(row["updated_at"]) or datetime.now(timezone.utc),
    )


def _release_cycle_item(row: sqlite3.Row) -> ReleaseCycleItem:
    return ReleaseCycleItem(
        finalizer_identifier=str(row["finalizer_identifier"]),
        cycle_fingerprint=str(row["cycle_fingerprint"]),
        item_role=str(row["item_role"]),
        item_key=str(row["item_key"]),
        issue_id=str(row["issue_id"]),
        identifier=str(row["identifier"]),
        recorded_at=_parse(row["recorded_at"]) or datetime.now(timezone.utc),
        updated_at=_parse(row["updated_at"]) or datetime.now(timezone.utc),
    )
