from __future__ import annotations

from pathlib import Path

import pytest

from symphony.pm.approval import PmApprovalService
from symphony.pm.handoff import PmHandoffService
from symphony.pm.authority import PmAuthority
from symphony.pm.models import PmError, PmRequestContext, ValidationReport
from symphony.trackers.file import FileBoardTracker
from symphony.workflow import TrackerConfig
from tests.test_pm_approval import _populate
from tests.test_pm_documents import _repository


def _tracker(tmp_path: Path) -> FileBoardTracker:
    return FileBoardTracker(
        TrackerConfig(
            kind="file", endpoint="", api_key="", project_slug="p1",
            active_states=("Todo", "In Progress"),
            terminal_states=("Done", "Cancelled"),
            board_root=tmp_path / "kanban",
        )
    )


def _approved(tmp_path: Path):
    repo, change_id, workspace = _repository(tmp_path)
    _populate(repo, change_id, workspace)
    tasks = workspace / "openspec" / "changes" / "approved" / "tasks.md"
    source = tasks.read_text(encoding="utf-8").replace(
        "depends_on: []",
        "depends_on:\n  - T-ZZZZZZZZ",
        1,
    )
    source += """
<!-- symphony-task:T-ZZZZZZZZ -->
- [ ] Build prerequisite
```yaml symphony-task
id: T-ZZZZZZZZ
parent_id: null
order: 20
type: Backend
depends_on: []
```
### Scope
Build prerequisite.
### Acceptance criteria
- Prerequisite works.
### Verification
- Run prerequisite tests.
### Integration impact
Existing API.
<!-- /symphony-task -->
"""
    tasks.write_text(source, encoding="utf-8")
    change = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    approval = PmApprovalService(repo, repo.registry, lambda _root, _locator: ValidationReport(True))
    ctx = PmRequestContext("p1", change_id, change.revision, "approval")
    approval.approve(ctx, approval.confirmation_token(ctx, owner="owner"), "reviewer", owner="owner")
    return repo, change_id, change


def test_handoff_projects_leaves_dependencies_and_provenance_idempotently(tmp_path: Path) -> None:
    repo, change_id, change = _approved(tmp_path)
    tracker = _tracker(tmp_path)
    service = PmHandoffService(repo, repo.registry, tracker)
    ctx = PmRequestContext("p1", change_id, change.revision, "handoff-op")
    preview = service.preview(ctx, owner="owner")
    assert len(preview.items) == 2
    dependent = next(item for item in preview.items if item.task_id == "T-ABCDEFGH")
    prerequisite = next(item for item in preview.items if item.task_id == "T-ZZZZZZZZ")
    assert dependent.blocked_by == (prerequisite.deterministic_ticket_id,)
    assert dependent.runnable is False and prerequisite.runnable is True

    result = service.publish(ctx, preview.preview_token, preview.preview_token, owner="owner")
    assert result.state == "PUBLISHED"
    issue = tracker.fetch_issue_full_by_id(dependent.deterministic_ticket_id)
    assert issue is not None and issue.planning_ref is not None
    assert issue.planning_ref.task_id == dependent.task_id
    assert issue.blocked_by[0].identifier == prerequisite.deterministic_ticket_id
    prerequisite_issue = tracker.fetch_issue_full_by_id(prerequisite.deterministic_ticket_id)
    assert prerequisite_issue is not None
    assert issue.request == prerequisite_issue.request
    repeated = service.publish(ctx, preview.preview_token, preview.preview_token, owner="owner")
    assert repeated.handoff_id == result.handoff_id
    assert len(tracker.scan_all()) == 2


@pytest.mark.parametrize(
    ("method", "invocation", "moment"),
    [
        ("create_pm_handoff_batch", 1, "before"),
        ("create_pm_handoff_batch", 1, "after"),
        ("create_validated", 1, "before"),
        ("create_validated", 1, "after"),
        ("create_validated", 2, "before"),
        ("create_validated", 2, "after"),
        ("publish_pm_handoff", 1, "before"),
        ("publish_pm_handoff", 1, "after"),
    ],
)
def test_crash_safe_batch_publication(
    tmp_path: Path, method: str, invocation: int, moment: str,
) -> None:
    class Crash(BaseException):
        pass

    repo, change_id, change = _approved(tmp_path)
    tracker = _tracker(tmp_path)
    service = PmHandoffService(repo, repo.registry, tracker)
    authority = PmAuthority(repo.registry, tmp_path / "locks")
    ctx = PmRequestContext("p1", change_id, change.revision, "crash-op")
    preview = service.preview(ctx, owner="owner")
    target = tracker if method == "create_validated" else repo.registry
    original = getattr(target, method)
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == invocation and moment == "before":
            raise Crash
        result = original(*args, **kwargs)
        if calls == invocation and moment == "after":
            raise Crash
        return result

    setattr(target, method, interrupted)
    with pytest.raises(Crash):
        service.publish(ctx, preview.preview_token, preview.preview_token, owner="owner")
    setattr(target, method, original)
    batch = repo.registry.get_pm_handoff_by_operation("crash-op")
    state = str(batch["state"]) if batch else "NONE"
    issues = tracker.scan_all()
    if state != "PUBLISHED":
        assert all(not authority.recognize(issue, "p1").runnable for issue in issues)
    recovered = service.publish(ctx, preview.preview_token, preview.preview_token, owner="owner")
    replay = service.publish(ctx, preview.preview_token, preview.preview_token, owner="owner")
    assert recovered.handoff_id == replay.handoff_id
    assert recovered.state == "PUBLISHED"
    assert len(tracker.scan_all()) == 2


def test_handoff_immutable_input_persists_resolved_target_state(tmp_path: Path) -> None:
    repo, change_id, change = _approved(tmp_path)
    tracker = _tracker(tmp_path)
    service = PmHandoffService(repo, repo.registry, tracker, target_state="In Progress")
    ctx = PmRequestContext("p1", change_id, change.revision, "target-state-op")
    preview = service.preview(ctx, owner="owner")
    result = service.publish(ctx, preview.preview_token, preview.preview_token, owner="owner")
    rows = repo.registry.get_pm_handoff_items(result.handoff_id)
    assert rows
    assert all(__import__("json").loads(str(row["input_json"]))["state"] == "In Progress" for row in rows)
    assert all(str(row["input_hash"]).startswith("sha256:") for row in rows)
    assert all(tracker.fetch_issue_full_by_id(identifier).state == "In Progress" for identifier in result.ticket_identifiers)  # type: ignore[union-attr]


def test_handoff_collision_preserves_unrelated_ticket(tmp_path: Path) -> None:
    repo, change_id, change = _approved(tmp_path)
    tracker = _tracker(tmp_path)
    service = PmHandoffService(repo, repo.registry, tracker)
    ctx = PmRequestContext("p1", change_id, change.revision, "collision-op")
    preview = service.preview(ctx, owner="owner")
    collision = preview.items[0].deterministic_ticket_id
    tracker.create(identifier=collision, title="Unrelated", description="keep me")
    with pytest.raises(PmError) as exc:
        service.publish(ctx, preview.preview_token, preview.preview_token, owner="owner")
    assert exc.value.code == "ticket_collision"
    persisted = tracker.fetch_issue_full_by_id(collision)
    assert persisted is not None and persisted.title == "Unrelated"


def test_reconcile_rejects_ticket_scope_dependency_and_backend_tampering(tmp_path: Path) -> None:
    repo, change_id, change = _approved(tmp_path)
    tracker = _tracker(tmp_path)
    service = PmHandoffService(repo, repo.registry, tracker)
    ctx = PmRequestContext("p1", change_id, change.revision, "tamper-op")
    preview = service.preview(ctx, owner="owner")
    result = service.publish(ctx, preview.preview_token, preview.preview_token, owner="owner")
    target = preview.items[0].deterministic_ticket_id
    tracker.update_fields(target, title="tampered", blocked_by=[], agent_kind="claude")
    with pytest.raises(PmError) as exc:
        service.reconcile(result.operation_id)
    assert exc.value.code == "handoff_reconcile"


def test_reconcile_recreates_missing_ticket_from_immutable_staged_input(tmp_path: Path) -> None:
    repo, change_id, change = _approved(tmp_path)
    tracker = _tracker(tmp_path)
    service = PmHandoffService(repo, repo.registry, tracker)
    ctx = PmRequestContext("p1", change_id, change.revision, "recreate-op")
    preview = service.preview(ctx, owner="owner")
    result = service.publish(ctx, preview.preview_token, preview.preview_token, owner="owner")
    missing = preview.items[0].deterministic_ticket_id
    tracker.delete(missing)
    reconciled = service.reconcile(result.operation_id)
    assert reconciled.state == "PUBLISHED"
    restored = tracker.fetch_issue_full_by_id(missing)
    assert restored is not None and restored.planning_ref is not None


def test_legacy_conversion_is_durable_idempotent_and_blocks_reopen(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    tracker = _tracker(tmp_path)
    tracker.create(identifier="LEGACY-1", title="Legacy scope", description="Implement once")
    service = PmHandoffService(repo, repo.registry, tracker, park_state="Cancelled")
    ctx = PmRequestContext("p1", change_id)
    first = service.convert_legacy(ctx, "LEGACY-1", str(workspace), "converted", "Converted", owner="owner")
    second = service.convert_legacy(ctx, "LEGACY-1", str(workspace), "converted", "Converted", owner="owner")
    assert first["state"] == "Cancelled" and second["idempotent"] is True
    source = tracker.fetch_issue_full_by_id("LEGACY-1")
    assert source is not None and source.state == "Cancelled"
    denied = PmAuthority(repo.registry, tmp_path / "ignored").recognize(source, "p1")
    assert denied.managed and not denied.runnable


def test_legacy_conversion_reservation_refuses_lease_acquired_after_precheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    tracker = _tracker(tmp_path)
    tracker.create(identifier="LEGACY-RACE", title="Legacy", description="Scope")
    source = tracker.fetch_issue_full_by_id("LEGACY-RACE")
    assert source is not None
    run_id = repo.registry.acquire_run(
        source, workspace_path=workspace, attempt=None,
        attempt_kind="initial", agent_kind="codex",
    )
    assert run_id is not None
    monkeypatch.setattr(repo.registry, "has_active_lease", lambda _issue_id: False)
    service = PmHandoffService(repo, repo.registry, tracker, park_state="Cancelled")
    with pytest.raises(PmError) as exc:
        service.convert_legacy(
            PmRequestContext("p1", change_id), "LEGACY-RACE", str(workspace),
            "converted-race", "Converted", owner="owner",
        )
    assert exc.value.code == "source_ticket_running"
    current = tracker.fetch_issue_full_by_id("LEGACY-RACE")
    assert current is not None and current.state == "Todo"
    assert repo.registry.get_pm_legacy_conversion_by_source("p1", "LEGACY-RACE") is None
