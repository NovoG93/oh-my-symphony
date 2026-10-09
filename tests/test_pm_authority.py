from __future__ import annotations

import sqlite3
import subprocess
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from symphony.issue import BlockerRef, Issue, PlanningRef
from symphony.orchestrator.run_registry import RunRegistry
from symphony.pm.authority import PmAuthority, _issue_scope_hash
from symphony.pm.documents import _scope_hash
from symphony.pm.models import PmError


def _valid_ref() -> PlanningRef:
    return PlanningRef("c1", "T-ABCDEFGH", "r1", "h1", "a1")


def _issue(ref: PlanningRef | None = None) -> Issue:
    return Issue(
        id="PM-1", identifier="PM-1", title="Managed task", description="scope",
        priority=None, state="Todo", planning_ref=ref,
    )


def _authority(tmp_path: Path) -> tuple[PmAuthority, RunRegistry]:
    path = tmp_path / "state.db"
    registry = RunRegistry(path)
    now = datetime(2026, 10, 4, tzinfo=timezone.utc).isoformat()
    workspace = tmp_path / "planning"
    change_root = workspace / "openspec" / "changes" / "demo"
    change_root.mkdir(parents=True)
    (change_root / "symphony.yaml").write_text("change_id: c1\nplanning_format_version: 1\n", encoding="utf-8")
    for name in ("proposal.md", "ux.md", "design.md", "tasks.md"):
        content = "- [ ] task\n" if name == "tasks.md" else f"{name}\n"
        (change_root / name).write_text(content, encoding="utf-8")
    scope_hash = _scope_hash(change_root, workspace)
    registry.claim_pm_workspace(
        project_id="p1", change_id="c1", workspace_path=workspace,
        base_git_sha="a" * 40, owner="owner",
    )
    manifest = tmp_path / "manifest.json"
    file_hashes = {
        str(path.relative_to(change_root)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(change_root.iterdir()) if path.is_file()
    }
    manifest.write_text(json.dumps({
        "approval_id": "a1", "project_id": "p1", "change_id": "c1",
        "scope_hash": scope_hash, "revision": "rev", "file_hashes": file_hashes,
        "baseline_hashes": {}, "required_procedures": {"T-ABCDEFGH": {}},
    }, sort_keys=True), encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workspace), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(workspace), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-m", "approved snapshot"], check=True, capture_output=True)
    snapshot_commit = subprocess.check_output(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True
    ).strip()
    registry.record_pm_approval(
        approval_id="a1", project_id="p1", change_id="c1",
        scope_hash=scope_hash, revision_hash="rev", manifest_ref=str(manifest),
        reviewer_label="reviewer", approved_at=now, snapshot_commit=snapshot_commit,
    )
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            """
            INSERT INTO pm_handoff_batches (
                handoff_id, operation_id, project_id, change_id, scope_hash,
                approval_id, snapshot_id, state, created_at, published_at
            ) VALUES ('h1','op1','p1','c1',?,'a1','a1','PUBLISHED',?,?)
            """,
            (scope_hash, now, now),
        )
        conn.execute(
            """
            INSERT INTO pm_handoff_items (
                handoff_id, project_id, change_id, task_id, task_revision,
                deterministic_ticket_id, ticket_identifier, state, content_hash,
                required_procedures_json
            ) VALUES ('h1','p1','c1','T-ABCDEFGH','r1','PM-1','PM-1','PUBLISHED',?,'{}')
            """,
            (_issue_scope_hash(_issue(_valid_ref())),),
        )
    registry.register_pm_managed_ticket(
        project_id="p1", ticket_identifier="PM-1", change_id="c1",
        task_id="T-ABCDEFGH", task_revision="r1", handoff_id="h1",
        state="PUBLISHED",
    )
    return PmAuthority(registry, tmp_path / "locks"), registry


def test_durable_mapping_recognizes_markerless_ticket_and_rejects_conflict(tmp_path: Path) -> None:
    authority, _ = _authority(tmp_path)
    membership = authority.recognize(_issue(), "p1")
    assert membership.managed and not membership.runnable
    assert "provenance is missing" in (membership.reason or "")
    malformed = _issue(PlanningRef("wrong", "T-OTHER", "r2", "h2", "s2"))
    denied = authority.recognize(malformed, "p1")
    assert denied.managed and not denied.runnable
    assert "conflicts" in (denied.reason or "")


def test_missing_approval_staging_and_invalid_marker_never_become_ordinary(tmp_path: Path) -> None:
    authority, registry = _authority(tmp_path)
    registry._connect().execute("DELETE FROM pm_approvals")
    denied = authority.recognize(_issue(_valid_ref()), "p1")
    assert denied.managed and not denied.runnable


def test_durable_fallback_recognizes_markerless_ticket_after_registry_rows_lost(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    registry.create_pm_handoff_batch(
        handoff_id="h-fallback", operation_id="op-fallback", project_id="p1",
        change_id="c-fallback", scope_hash="sha256:scope", approval_id="a-fallback",
        snapshot_id="a-fallback", items=({
            "task_id": "T-FALLBACK", "task_revision": "r-fallback",
            "deterministic_ticket_id": "PM-FALLBACK", "content_hash": "sha256:content",
            "input_hash": "sha256:input", "input_json": "{}",
            "required_procedures_json": "{}",
        },),
    )
    database_path = registry.path
    registry.close()
    database_path.unlink()
    for suffix in ("-wal", "-shm"):
        database_path.with_name(database_path.name + suffix).unlink(missing_ok=True)
    restored = RunRegistry(database_path)
    issue = replace(_issue(), id="PM-FALLBACK", identifier="PM-FALLBACK")
    membership = PmAuthority(restored, tmp_path / "locks").recognize(issue, "p1")
    assert membership.managed and not membership.runnable
    assert membership.change_id == "c-fallback"
    assert "fallback" in (membership.reason or "")


@pytest.mark.parametrize("failure", ["corrupt", "unreadable"])
def test_corrupt_or_unreadable_fallback_fails_closed_with_empty_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    fallback = registry._pm_fallback_path()
    fallback.write_text("{broken", encoding="utf-8")
    if failure == "unreadable":
        monkeypatch.setattr(
            registry, "get_pm_fallback_ticket",
            lambda *_args: (_ for _ in ()).throw(OSError("unreadable")),
        )
    issue = replace(_issue(), id="PM-RESERVED", identifier="PM-RESERVED")
    membership = PmAuthority(registry, tmp_path / "locks").recognize(issue, "p1")
    assert membership.managed and not membership.runnable
    assert "fallback authority" in (membership.reason or "")


def test_new_handoff_refuses_to_overwrite_corrupt_fallback_authority(tmp_path: Path) -> None:
    registry = RunRegistry(tmp_path / "state.db")
    common = {
        "task_id": "T-FIRST", "task_revision": "r1",
        "deterministic_ticket_id": "PM-FIRST", "content_hash": "sha256:content",
        "input_hash": "sha256:input", "input_json": "{}",
        "required_procedures_json": "{}",
    }
    assert registry.create_pm_handoff_batch(
        handoff_id="h-first", operation_id="op-first", project_id="p1",
        change_id="c-first", scope_hash="sha256:first", approval_id="a-first",
        snapshot_id="a-first", items=(common,),
    )
    fallback = registry._pm_fallback_path()
    fallback.write_text("{corrupt", encoding="utf-8")
    second = dict(common, task_id="T-SECOND", deterministic_ticket_id="PM-SECOND")
    with pytest.raises(ValueError, match="refusing to merge"):
        registry.create_pm_handoff_batch(
            handoff_id="h-second", operation_id="op-second", project_id="p1",
            change_id="c-second", scope_hash="sha256:second", approval_id="a-second",
            snapshot_id="a-second", items=(second,),
        )
    assert registry.get_pm_handoff_by_operation("op-second") is None
    assert fallback.read_text(encoding="utf-8") == "{corrupt"


def test_manifest_byte_tampering_revokes_runnable_authority(tmp_path: Path) -> None:
    authority, registry = _authority(tmp_path)
    issue = _issue(_valid_ref())
    assert authority.recognize(issue, "p1").runnable
    approval = registry.get_pm_approval("a1")
    assert approval is not None
    manifest = Path(str(approval["manifest_ref"]))
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    membership = authority.recognize(issue, "p1")
    assert membership.managed and not membership.runnable
    assert "manifest" in (membership.reason or "")


def test_controller_completion_checkbox_does_not_revoke_immutable_approval(tmp_path: Path) -> None:
    authority, registry = _authority(tmp_path)
    issue = _issue(_valid_ref())
    workspace = registry.get_pm_workspace("p1", "c1")
    assert workspace is not None
    tasks = workspace.workspace_path / "openspec" / "changes" / "demo" / "tasks.md"
    tasks.write_text(tasks.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    membership = authority.recognize(issue, "p1")
    assert membership.runnable


def test_current_planning_scope_drift_blocks_reserved_authority(tmp_path: Path) -> None:
    authority, registry = _authority(tmp_path)
    issue = _issue(_valid_ref())
    assert authority.recognize(issue, "p1").runnable
    workspace = registry.get_pm_workspace("p1", "c1")
    assert workspace is not None
    proposal = workspace.workspace_path / "openspec" / "changes" / "demo" / "proposal.md"
    proposal.write_text("drifted\n", encoding="utf-8")
    denied = authority.recognize(issue, "p1")
    assert denied.managed and not denied.runnable
    assert "current planning scope" in (denied.reason or "")
    registry._connect().execute(
        "UPDATE pm_managed_tickets SET state = 'STAGING' WHERE ticket_identifier = 'PM-1'"
    )
    denied = authority.recognize(_issue(_valid_ref()), "p1")
    assert denied.managed and not denied.runnable
    invalid = replace(_issue(), identifier="BROKEN-1", planning_ref_state="invalid")
    denied = authority.recognize(invalid, "p1")
    assert denied.managed and not denied.runnable


@pytest.mark.asyncio
async def test_launch_guard_linearizes_start_and_running_publication(tmp_path: Path) -> None:
    authority, registry = _authority(tmp_path)
    issue = _issue(_valid_ref())
    permit = authority.reserve_dispatch(issue, "p1")
    assert permit is not None

    async def load() -> Issue:
        return issue

    async with authority.launch_guard(permit, load, "run-1")() as publish:
        assert registry.get_pm_dispatch_permit(permit.permit_id).state == "STARTING"  # type: ignore[union-attr]
        await publish(4242)
        running = registry.get_pm_dispatch_permit(permit.permit_id)
        assert running is not None and running.state == "RUNNING"
        assert running.process_pid == 4242 and running.run_id == "run-1"


@pytest.mark.asyncio
async def test_launch_guard_refuses_revocation_and_issue_drift(tmp_path: Path) -> None:
    authority, registry = _authority(tmp_path)
    issue = _issue(_valid_ref())
    revoked = authority.reserve_dispatch(issue, "p1")
    assert revoked is not None
    registry.revoke_pm_dispatch("p1", "PM-1")

    async def stable() -> Issue:
        return issue

    with pytest.raises(PmError, match="stale or revoked"):
        async with authority.launch_guard(revoked, stable, "run-1")():
            pass

    backend_tamper = authority.reserve_dispatch(issue, "p1")
    assert backend_tamper is not None

    async def changed_backend() -> Issue:
        return replace(issue, agent_kind="claude")

    with pytest.raises(PmError, match="authority drifted"):
        async with authority.launch_guard(
            backend_tamper, changed_backend, "run-backend-tamper"
        )():
            pass

    fresh = authority.reserve_dispatch(issue, "p1")
    assert fresh is not None
    calls = 0

    async def drifting() -> Issue:
        nonlocal calls
        calls += 1
        return issue if calls == 1 else replace(issue, title="changed")

    with pytest.raises(PmError, match="changed during"):
        async with authority.launch_guard(fresh, drifting, "run-2")() as publish:
            await publish(5252)
    assert registry.get_pm_dispatch_permit(fresh.permit_id).state == "REVOKED"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_running_revocation_blocks_later_generation_but_stage_change_is_allowed(tmp_path: Path) -> None:
    authority, registry = _authority(tmp_path)
    issue = _issue(_valid_ref())
    permit = authority.reserve_dispatch(issue, "p1")
    assert permit is not None
    staged = replace(issue, state="In Progress")

    async def load() -> Issue:
        return staged

    async with authority.launch_guard(permit, load, "run-1")() as publish:
        await publish(777)
    assert authority.revoke("p1", "PM-1") == 1
    assert registry.get_pm_dispatch_permit(permit.permit_id).state == "REVOKED"  # type: ignore[union-attr]
    with pytest.raises(PmError):
        async with authority.launch_guard(permit, load, "run-1")():
            pass
    with pytest.raises(PmError):
        authority.reserve_dispatch(issue, "p1")


@pytest.mark.asyncio
async def test_requires_code_predecessor_commit_must_be_in_successor_git_base(tmp_path: Path) -> None:
    authority, registry = _authority(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    (repo / "file").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "file"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
    base = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    (repo / "file").write_text("predecessor\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-am", "predecessor"], check=True, capture_output=True)
    predecessor_commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    blocker = Issue("PRE-1", "PRE-1", "Predecessor", "", None, "Done")
    run_id = registry.acquire_run(blocker, workspace_path=repo, attempt=None, attempt_kind="initial", agent_kind="codex")
    assert registry.complete_run(issue_id=blocker.id, run_id=run_id, status="completed", commit_sha=predecessor_commit)
    registry.create_pm_handoff_batch(
        handoff_id="h-pre", operation_id="op-pre", project_id="p1", change_id="c-pre",
        scope_hash="sha256:pre", approval_id="a-pre", snapshot_id="a-pre",
        items=({
            "task_id": "T-PREDECESSOR", "task_revision": "pre-r1",
            "deterministic_ticket_id": "PRE-1", "content_hash": "sha256:pre-content",
            "input_hash": "sha256:pre-input", "input_json": "{}",
            "required_procedures_json": json.dumps({"tests": "sha256:command"}),
        },),
    )
    registry.publish_pm_handoff("h-pre", ticket_identifiers={"T-PREDECESSOR": "PRE-1"})
    registry.record_pm_verification(
        receipt_id="receipt-pre", project_id="p1", ticket_identifier="PRE-1",
        task_revision="pre-r1", operation_id="verify-pre", run_id=run_id,
        procedure_id="tests",
        command_digest="sha256:command", tested_commit=predecessor_commit,
        return_code=0, timed_out=False, cancelled=False,
        stdout_hash="sha256:out", stderr_hash="sha256:err",
        started_at=datetime.now(timezone.utc).isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(), artifact_refs=(),
        result="passed",
    )
    issue = replace(
        _issue(_valid_ref()),
        blocked_by=(BlockerRef("PRE-1", "PRE-1", "Done"),),
    )
    registry._connect().execute(
        "UPDATE pm_handoff_items SET content_hash = ? WHERE handoff_id = 'h1'",
        (_issue_scope_hash(issue),),
    )
    unaccepted = authority.reserve_dispatch(issue, "p1")
    assert unaccepted is not None

    async def load() -> Issue:
        return issue

    with pytest.raises(PmError, match="human-accepted"):
        async with authority.launch_guard(
            unaccepted, load, "run-unaccepted", workspace_path=repo
        )():
            pass
    registry.record_pm_acceptance(
        acceptance_id="accept-pre", project_id="p1", ticket_identifier="PRE-1",
        task_revision="pre-r1", tested_commit=predecessor_commit,
        receipt_ids=("receipt-pre",), receipt_set_hash="sha256:" + hashlib.sha256(
            json.dumps(("receipt-pre",), separators=(",", ":")).encode()
        ).hexdigest(),
        accepted_by="reviewer", accepted_at=datetime.now(timezone.utc).isoformat(),
    )
    permit = authority.reserve_dispatch(issue, "p1")
    assert permit is not None

    subprocess.run(["git", "-C", str(repo), "checkout", "--detach", base], check=True, capture_output=True)
    with pytest.raises(PmError, match="not in the successor base"):
        async with authority.launch_guard(permit, load, "run-successor", workspace_path=repo)():
            pass
    permit = authority.reserve_dispatch(issue, "p1")
    assert permit is not None
    subprocess.run(["git", "-C", str(repo), "checkout", "--detach", predecessor_commit], check=True, capture_output=True)
    async with authority.launch_guard(permit, load, "run-successor", workspace_path=repo)() as publish:
        await publish(888)


@pytest.mark.asyncio
async def test_order_only_predecessor_does_not_require_accepted_commit(tmp_path: Path) -> None:
    authority, registry = _authority(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    (repo / "file").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "file"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
    issue = replace(
        _issue(_valid_ref()),
        blocked_by=(BlockerRef("PRE-ORDER", "PRE-ORDER", "Done"),),
    )
    registry._connect().execute(
        "UPDATE pm_handoff_items SET content_hash=?, input_json=? WHERE handoff_id='h1'",
        (_issue_scope_hash(issue), json.dumps({"dependency_kinds": {"PRE-ORDER": "order_only"}})),
    )
    permit = authority.reserve_dispatch(issue, "p1")
    assert permit is not None

    async def load() -> Issue:
        return issue

    async with authority.launch_guard(
        permit, load, "run-order", workspace_path=repo
    )() as publish:
        await publish(4321)


@pytest.mark.asyncio
async def test_orchestrator_artifacts_block_does_not_revoke_a_later_phase_launch(
    tmp_path: Path,
) -> None:
    authority, registry = _authority(tmp_path)
    issue = _issue(_valid_ref())
    permit = authority.reserve_dispatch(issue, "p1")
    assert permit is not None

    async def first() -> Issue:
        return issue

    async with authority.launch_guard(permit, first, "run-1")() as publish:
        await publish(4242)

    # After turn 1 the host appends its own artifacts block and the worker
    # moves the ticket to the next lane; the Review phase relaunches.
    with_artifacts = replace(
        issue,
        state="Review",
        description=(
            f"{issue.description}\n\n<!-- symphony-artifacts -->\n## Artifacts\n\n"
            "- [proof.txt](<../proof.txt>)\n<!-- /symphony-artifacts -->"
        ),
    )
    assert authority.recognize(with_artifacts, "p1").runnable

    async def later() -> Issue:
        return with_artifacts

    async with authority.launch_guard(permit, later, "run-1")() as publish:
        await publish(4343)
    relaunched = registry.get_pm_dispatch_permit(permit.permit_id)
    assert relaunched is not None and relaunched.state == "RUNNING"
    assert relaunched.process_pid == 4343

    async def scope_edit() -> Issue:
        return replace(with_artifacts, description=with_artifacts.description + "\nExtra scope.")

    with pytest.raises(PmError, match="authority drifted"):
        async with authority.launch_guard(permit, scope_edit, "run-1")():
            pass
