from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from symphony.pm.application import PmApplication
from symphony.pm.approval import PmApprovalService
from symphony.pm.exchange import WorkbookExchange
from symphony.pm.handoff import PmHandoffService
from symphony.pm.models import ArchiveCandidate, ArchiveResult, PmError, PmRequestContext, ValidationReport
from symphony.pm.openspec import OpenSpecAdapter
from symphony.pm.verification import PmVerificationService
from tests.test_pm_documents import _repository
from tests.test_pm_handoff import _tracker


class _ArchiveAdapter:
    def validate(self, _root: Path, _locator: str) -> ValidationReport:
        return ValidationReport(True)

    def archive_preview(self, root: Path, locator: str) -> ArchiveCandidate:
        source = root / "openspec" / "changes" / locator
        target = root / "openspec" / "changes" / "archive" / f"2026-10-04-{locator}"
        return ArchiveCandidate(
            str(root), "c" * 32, "tree-revision", str(root.resolve()),
            tuple(str(item) for item in source.rglob("*") if item.is_file()),
            (str(target),),
        )

    def archive(self, candidate: ArchiveCandidate, controlled_workspace: Path) -> ArchiveResult:
        assert candidate.controlled_workspace == str(controlled_workspace.resolve())
        target = Path(candidate.target_paths[0])
        source = controlled_workspace / "openspec" / "changes" / target.name.removeprefix("2026-10-04-")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(source, target)
        baseline = controlled_workspace / "openspec" / "specs" / "requirements" / "spec.md"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text("# Archived baseline\n", encoding="utf-8")
        return ArchiveResult(str(target), (str(baseline),), {"archive": {"path": str(target)}})


def _ready_application(tmp_path: Path):
    repo, change_id, workspace = _repository(tmp_path)
    repo.create_change(change_id=change_id, locator="archive-ready", title="Archive ready", owner="owner")
    root = workspace / "openspec" / "changes" / "archive-ready"
    identity_path = root / "symphony.yaml"
    identity = yaml.safe_load(identity_path.read_text(encoding="utf-8"))
    identity.pop("work_item_ids", None)
    identity_path.write_text(yaml.safe_dump(identity, sort_keys=False), encoding="utf-8")
    (root / "proposal.md").write_text("# Proposal\n\nProblem and outcome.\n", encoding="utf-8")
    (root / "ux.md").write_text("# UX\n\nNo material impact.\n", encoding="utf-8")
    (root / "design.md").write_text("# Design\n\nUse existing seams.\n", encoding="utf-8")
    spec = root / "specs" / "requirements" / "spec.md"
    spec.write_text("# Requirements\n\n### Requirement: Observable behavior\n<!-- symphony:req=REQ-1 -->\n\n#### Scenario: Behavior succeeds\n<!-- symphony:scenario=SC-1 -->\n- GIVEN a valid request\n- WHEN it runs\n- THEN it succeeds\n", encoding="utf-8")
    (root / "tasks.md").write_text("""# Plan
<!-- symphony-wbs-format: 1 -->
<!-- symphony-task:T-ABCDEFGH -->
- [ ] Implement approved scope
```yaml symphony-task
id: T-ABCDEFGH
parent_id: null
order: 10
type: Backend
depends_on: []
requirements: [REQ-1]
```
### Scope
Implement approved scope.
### Acceptance criteria
- The behavior is observable.
### Verification
- Run the focused tests.
### Integration impact
Existing API.
<!-- /symphony-task -->
""", encoding="utf-8")
    change = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    # This fixture isolates archive transaction ordering. Semantic-readiness
    # validation has its own suite and would couple this crash test to every
    # newly required Idea/identity field.
    repo.validate = lambda *_args, **_kwargs: ValidationReport(True)  # type: ignore[method-assign]
    tracker = _tracker(tmp_path)
    procedures = {"focused": ("/usr/bin/true",)}
    approval = PmApprovalService(
        repo, repo.registry, lambda _root, _locator: ValidationReport(True),
        verification_procedures=procedures,
    )
    approval_ctx = PmRequestContext("p1", change_id, change.revision, "approve")
    approval.approve(
        approval_ctx, approval.confirmation_token(approval_ctx, owner="owner"),
        "reviewer", owner="owner",
    )
    approved = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    handoff = PmHandoffService(repo, repo.registry, tracker)
    handoff_ctx = PmRequestContext("p1", change_id, approved.revision, "handoff-op")
    handoff_preview = handoff.preview(handoff_ctx, owner="owner")
    handoff.publish(
        handoff_ctx, handoff_preview.preview_token, handoff_preview.preview_token,
        owner="owner",
    )
    application = PmApplication(
        repo, approval, handoff, _ArchiveAdapter(), WorkbookExchange(),
        project_root=tmp_path, flags={"authoring": True, "exchange": True, "assistance": True, "handoff": True},
        archive_journal_root=tmp_path / "journals",
        development_verification=True,
    )
    return application, repo, approved, handoff_preview, procedures


def test_archive_requires_acceptance_then_journals_and_releases_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, repo, approved, handoff_preview, procedures = _ready_application(tmp_path)
    ctx = PmRequestContext("p1", approved.change_id, approved.revision, "archive-op")
    with pytest.raises(PmError) as blocked:
        application.preview_archive(ctx, "owner")
    assert blocked.value.code == "acceptance_incomplete"

    item = handoff_preview.items[0]
    digest = "sha256:" + hashlib.sha256(
        json.dumps(procedures["focused"], separators=(",", ":")).encode()
    ).hexdigest()
    tested_commit = "a" * 40
    repo.registry.record_pm_verification(
        receipt_id="receipt", project_id="p1",
        ticket_identifier=item.deterministic_ticket_id,
        task_revision=item.task_revision, operation_id="verify", run_id="run",
        procedure_id="focused", command_digest=digest,
        tested_commit=tested_commit, return_code=0, timed_out=False,
        cancelled=False, stdout_hash="sha256:out", stderr_hash="sha256:err",
        started_at="2026-10-04T00:00:00+00:00",
        completed_at="2026-10-04T00:00:01+00:00",
        artifact_refs=(), result="passed",
    )
    verification = PmVerificationService(repo.registry, procedures)
    acceptance = verification.preview_acceptance(
        project_id="p1", ticket_identifier=item.deterministic_ticket_id,
        task_revision=item.task_revision, tested_commit=tested_commit,
    )
    verification.accept(
        preview=acceptance, confirmation_token=acceptance.confirmation_token,
        reviewer_label="reviewer",
    )

    preview = application.preview_archive(ctx, "owner")
    candidate = ArchiveCandidate(**preview["candidate"])
    result = application.apply_archive(
        ctx, "owner", candidate, preview["confirmation_token"]
    )
    assert Path(result.archived_path).is_dir()
    journal = json.loads((tmp_path / "journals" / "archive-op.json").read_text(encoding="utf-8"))
    assert journal["state"] == "COMPLETED"
    assert repo.registry.get_pm_workspace("p1", approved.change_id).state == "RELEASED"
    history_ctx = PmRequestContext("p1", approved.change_id)
    archived_change = repo.get_change(history_ctx, owner="owner")
    assert archived_change.change_id == approved.change_id
    assert archived_change.state == "Archived"
    archived_tasks = repo.get_document(
        history_ctx, "tasks", owner="owner"
    )
    assert archived_tasks.editable is False
    assert archived_tasks.unsupported_reason == "archived planning history"
    assert repo.get_wbs(history_ctx, owner="owner").tasks
    with pytest.raises(PmError) as archived_write:
        repo.patch_document(
            PmRequestContext(
                "p1", approved.change_id, archived_change.revision
            ),
            "proposal",
            ({"op": "replace_source", "source": "# changed\n"},),
            owner="owner",
        )
    assert archived_write.value.code == "workspace_repair_required"
    with pytest.raises(PmError) as archived_approval:
        application.approval.approve(
            PmRequestContext(
                "p1", approved.change_id, archived_change.revision
            ),
            "invalid", "reviewer", owner="owner",
        )
    assert archived_approval.value.code == "workspace_repair_required"
    with pytest.raises(PmError) as archived_handoff:
        application.handoff.preview(
            PmRequestContext(
                "p1", approved.change_id, archived_change.revision
            ),
            owner="owner",
        )
    assert archived_handoff.value.code == "workspace_repair_required"
    with pytest.raises(RuntimeError, match="identity is retired"):
        repo.registry.claim_pm_workspace(
            project_id="p1", change_id=approved.change_id,
            workspace_path=Path(candidate.controlled_workspace),
            base_git_sha=repo.registry.get_pm_workspace("p1", approved.change_id).base_git_sha,
            owner="owner",
        )
    assert application.apply_archive(ctx, "owner", candidate, preview["confirmation_token"]) == result
    record = repo.registry.get_pm_workspace("p1", approved.change_id)
    assert record is not None
    assert repo.registry.set_pm_workspace_state("p1", approved.change_id, fence=record.fence, state="ACTIVE")
    replayed = application.apply_archive(
        ctx, "owner", candidate, preview["confirmation_token"]
    )
    assert replayed == result
    assert repo.registry.get_pm_workspace("p1", approved.change_id).state == "RELEASED"

    record = repo.registry.get_pm_workspace("p1", approved.change_id)
    assert record is not None
    assert repo.registry.set_pm_workspace_state(
        "p1", approved.change_id, fence=record.fence, state="ACTIVE"
    )
    journal["state"] = "PREPARED"
    journal.pop("result", None)
    (tmp_path / "journals" / "archive-op.json").write_text(json.dumps(journal), encoding="utf-8")
    import symphony.pm.application as application_module

    original_write_json = application_module._write_json

    def crash_after_release(path, payload):
        if payload.get("state") == "COMPLETED":
            raise KeyboardInterrupt("crash after release before completed journal")
        original_write_json(path, payload)

    monkeypatch.setattr(application_module, "_write_json", crash_after_release)
    with pytest.raises(KeyboardInterrupt):
        application.apply_archive(ctx, "owner", candidate, preview["confirmation_token"])
    assert repo.registry.get_pm_workspace("p1", approved.change_id).state == "RELEASED"
    assert json.loads((tmp_path / "journals" / "archive-op.json").read_text())["state"] == "PREPARED"
    monkeypatch.setattr(application_module, "_write_json", original_write_json)
    recovered = application.apply_archive(ctx, "owner", candidate, preview["confirmation_token"])
    assert recovered.output["reconciled"] is True
    assert repo.registry.get_pm_workspace("p1", approved.change_id).state == "RELEASED"

    record = repo.registry.get_pm_workspace("p1", approved.change_id)
    assert record is not None
    assert repo.registry.set_pm_workspace_state("p1", approved.change_id, fence=record.fence, state="ACTIVE")
    journal["state"] = "PREPARED"
    journal.pop("result", None)
    (tmp_path / "journals" / "archive-op.json").write_text(json.dumps(journal), encoding="utf-8")
    baseline = Path(candidate.controlled_workspace) / "openspec" / "specs" / "requirements" / "spec.md"
    baseline.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(PmError) as tampered:
        application.apply_archive(ctx, "owner", candidate, preview["confirmation_token"])
    assert tampered.value.code == "archive_reconcile"
    assert repo.registry.get_pm_workspace("p1", approved.change_id).state == "ACTIVE"

    baseline.write_text("# Archived baseline\n", encoding="utf-8")
    valid_target = Path(candidate.target_paths[0])
    shutil.rmtree(valid_target)
    valid_target.mkdir(parents=True)
    with pytest.raises(PmError) as empty_target:
        application.apply_archive(ctx, "owner", candidate, preview["confirmation_token"])
    assert empty_target.value.code == "archive_reconcile"
    assert repo.registry.get_pm_workspace("p1", approved.change_id).state == "ACTIVE"
    changed_candidate = ArchiveCandidate(
        candidate.project_id, candidate.change_id, candidate.revision,
        candidate.controlled_workspace, candidate.source_paths,
        (candidate.target_paths[0] + "-different",), candidate.findings,
    )
    with pytest.raises(PmError) as reused:
        application.apply_archive(ctx, "owner", changed_candidate, preview["confirmation_token"])
    assert reused.value.code == "invalid_operation"


def test_approved_task_requires_reviewed_cancellation_with_coverage(
    tmp_path: Path,
) -> None:
    application, repo, approved, _handoff_preview, _procedures = _ready_application(tmp_path)
    original_id = "T-ABCDEFGH"
    with pytest.raises(PmError) as deleted:
        repo.patch_wbs(
            PmRequestContext("p1", approved.change_id, approved.revision),
            ({"op": "delete_task", "task_id": original_id},), owner="owner",
        )
    assert deleted.value.code == "approved_task_requires_cancellation"
    with pytest.raises(PmError) as raw_delete:
        repo.patch_document(
            PmRequestContext("p1", approved.change_id, approved.revision),
            "tasks", ({"op": "replace_source", "source": "# Plan\n<!-- symphony-wbs-format: 1 -->\n"},),
            owner="owner",
        )
    assert raw_delete.value.code == "approved_task_requires_cancellation"
    with pytest.raises(PmError) as proposed_delete:
        repo.patch_documents(
            PmRequestContext("p1", approved.change_id, approved.revision),
            {"tasks": "# Plan\n<!-- symphony-wbs-format: 1 -->\n"},
            owner="owner",
        )
    assert proposed_delete.value.code == "approved_task_requires_cancellation"
    with pytest.raises(PmError) as no_reason:
        repo.patch_wbs(
            PmRequestContext("p1", approved.change_id, approved.revision),
            ({"op": "cancel_task", "task_id": original_id, "reason": ""},),
            owner="owner",
        )
    assert no_reason.value.code == "cancellation_reason_required"

    successor = repo.patch_wbs(
        PmRequestContext("p1", approved.change_id, approved.revision),
        ({"op": "create_task", "task": {
            "kind": "Backend", "title": "Replacement", "order": 20,
            "requirement_refs": ["REQ-1"],
            "description": "Replace the cancelled scope.",
            "acceptance": ["Replacement is observable."],
            "verification": ["Run focused tests."],
            "integration_impact": "Existing API.",
        }},), owner="owner",
    )
    successor_id = successor.impacted_ids[0]
    dependent = repo.patch_wbs(
        PmRequestContext("p1", approved.change_id, successor.revision),
        ({"op": "create_task", "task": {
            "kind": "Backend", "title": "Dependent", "order": 30,
            "dependencies": [{"task_id": original_id, "kind": "requires_code"}],
            "description": "Use the replacement.",
            "acceptance": ["Dependency is enforced."],
            "verification": ["Run focused tests."],
            "integration_impact": "Existing API.",
        }},), owner="owner",
    )
    dependent_id = dependent.impacted_ids[0]
    with pytest.raises(PmError) as gap:
        repo.patch_wbs(
            PmRequestContext("p1", approved.change_id, dependent.revision),
            ({"op": "cancel_task", "task_id": original_id,
              "reason": "Superseded", "successor_refs": []},), owner="owner",
        )
    assert gap.value.code == "cancellation_coverage_gap"
    cancelled = repo.patch_wbs(
        PmRequestContext("p1", approved.change_id, dependent.revision),
        ({"op": "cancel_task", "task_id": original_id,
          "reason": "Superseded by reviewed replacement",
          "successor_refs": [successor_id]},), owner="owner",
    )
    tasks = {
        item.task_id: item for item in repo.get_wbs(
            PmRequestContext("p1", approved.change_id), owner="owner"
        ).tasks
    }
    assert cancelled.approval_invalidated is True
    assert tasks[original_id].cancelled is True
    assert tasks[original_id].executable is False
    assert tasks[original_id].cancellation_reason == "Superseded by reviewed replacement"
    assert tasks[original_id].successor_refs == (successor_id,)
    assert [item.task_id for item in tasks[dependent_id].dependencies] == [successor_id]
    source = repo.get_document(
        PmRequestContext("p1", approved.change_id), "tasks", owner="owner"
    ).source
    assert f"<!-- symphony-task:{original_id} -->\n- Cancelled:" in source
    assert source.count("- [ ]") == 2
    cli = Path("/private/tmp/pm-workspace-compat/openspec-cli/node_modules/.bin/openspec")
    if cli.is_file():
        progress = OpenSpecAdapter(str(cli)).apply_instructions(
            Path(repo._claim(approved.change_id, owner="owner").workspace_path),
            "archive-ready",
        )
        assert progress.total == 2
    revised = repo.get_change(
        PmRequestContext("p1", approved.change_id), owner="owner"
    )
    approval_ctx = PmRequestContext(
        "p1", approved.change_id, revised.revision, "reapprove-cancellation"
    )
    reapproved = application.approval.approve(
        approval_ctx,
        application.approval.confirmation_token(approval_ctx, owner="owner"),
        "reviewer", owner="owner",
    )
    assert reapproved.revision == revised.revision


def test_external_edit_cannot_delete_approved_task_scope(tmp_path: Path) -> None:
    _application, repo, approved, _handoff_preview, _procedures = _ready_application(tmp_path)
    claim = repo.begin_external_edit(approved.change_id, fence=1, owner="owner")
    root = claim.workspace_path / "openspec" / "changes" / "archive-ready"
    (root / "tasks.md").write_text(
        "# Plan\n<!-- symphony-wbs-format: 1 -->\n", encoding="utf-8"
    )
    report = repo.end_external_edit(approved.change_id, fence=1, owner="owner")
    assert not report.valid
    assert "approved_task_requires_cancellation" in {
        item.code for item in report.findings
    }
    assert repo.registry.get_pm_workspace("p1", approved.change_id).state == "REPAIR_REQUIRED"
