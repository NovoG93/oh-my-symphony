from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from symphony.pm.approval import PmApprovalService
from symphony.pm.models import PmError, PmRequestContext, ValidationReport
from tests.test_pm_documents import _repository


def _populate(repo, change_id: str, workspace: Path, locator: str = "approved"):
    created = repo.create_change(change_id=change_id, locator=locator, title="Approved", owner="owner")
    identities = {item.kind: item.semantic_id for item in created.semantic_index}
    root = workspace / "openspec" / "changes" / locator
    identity_path = root / "symphony.yaml"
    identity = yaml.safe_load(identity_path.read_text(encoding="utf-8"))
    identity.pop("work_item_ids", None)
    identity_path.write_text(yaml.safe_dump(identity, sort_keys=False), encoding="utf-8")
    (root / "proposal.md").write_text(
        f"""# Proposal

## Problem
Operators need observable behavior.
## Intended users
Product operators.
## Desired outcome
The behavior succeeds visibly.
## Scope
Implement the approved behavior.
## Non-goals
No unrelated delivery changes.
## Constraints
Keep existing boundaries.
## Success criteria
Focused verification passes.
## Assumptions
The registered project is available.
## Open decisions
### Decision: Use existing seams
<!-- symphony:decision={identities['decision']} -->
- Owner: Product owner
- Impact: Low
- Status: Accepted
- Reason: The existing boundary is already verified.
## Affected capabilities
- requirements
""",
        encoding="utf-8",
    )
    (root / "ux.md").write_text(
        f"# UX\n\n### Storyboard: Primary journey\n<!-- symphony:storyboard={identities['storyboard']} -->\n- Screen/route: Workspace\n- Actor/goal: Operator reviews\n- Initial state: Draft\n- UI/action/response: Review and save\n- Success/alternate/failure: Saved or error\n",
        encoding="utf-8",
    )
    (root / "design.md").write_text(
        f"# Design\n\n### Contract: Existing API\n<!-- symphony:contract={identities['contract']} -->\n- Owner/boundary: PM API\n- Auth/schema/errors/version/idempotency: Existing contract\n- Retry/partial failure: Fail closed\n- Verification: Focused tests\n",
        encoding="utf-8",
    )
    specs = root / "specs" / "requirements"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / "spec.md").write_text(
        "# Requirements\n\n### Requirement: Observable behavior\n"
        f"<!-- symphony:req={identities['requirement']} -->\n\n#### Scenario: Behavior succeeds\n"
        f"<!-- symphony:scenario={identities['scenario']} -->\n- GIVEN a valid request\n- WHEN it runs\n- THEN it succeeds\n",
        encoding="utf-8",
    )
    (root / "tasks.md").write_text(
        """# Plan
<!-- symphony-wbs-format: 1 -->
<!-- symphony-task:T-ABCDEFGH -->
- [ ] Implement approved scope
```yaml symphony-task
id: T-ABCDEFGH
parent_id: null
order: 10
type: Backend
depends_on: []
requirements: [{requirement_id}]
storyboards: [{storyboard_id}]
design_refs: [{contract_id}]
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
""".format(
            requirement_id=identities["requirement"],
            storyboard_id=identities["storyboard"],
            contract_id=identities["contract"],
        ),
        encoding="utf-8",
    )
    return repo.get_change(PmRequestContext("p1", change_id), owner="owner")


def test_approval_creates_local_planning_commit_and_manifest(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = _populate(repo, change_id, workspace)
    service = PmApprovalService(
        repo, repo.registry, lambda _root, _locator: ValidationReport(True),
        verification_procedures={"focused": ("/usr/bin/true",)},
    )
    ctx = PmRequestContext("p1", change_id, change.revision, "approve-1")
    before = subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip()
    manifest = service.approve(ctx, service.confirmation_token(ctx, owner="owner"), "reviewer", owner="owner")
    after = subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip()
    assert after != before
    assert Path(repo.registry.get_active_pm_approval("p1", change_id)["manifest_ref"]).exists()  # type: ignore[index]
    assert manifest.scope_hash == change.scope_hash
    assert manifest.reviewer_label == "reviewer"
    assert set(manifest.required_procedures["T-ABCDEFGH"]) == {"focused"}
    tree = subprocess.check_output(
        ["git", "-C", str(workspace), "ls-tree", "-r", "--name-only", "HEAD"], text=True
    ).splitlines()
    assert "openspec/changes/approved/.symphony-pm.lock" not in tree


def test_approval_rejects_unrelated_dirty_file_without_commit(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = _populate(repo, change_id, workspace, "dirty")
    (workspace / "unrelated.txt").write_text("do not commit\n", encoding="utf-8")
    service = PmApprovalService(repo, repo.registry, lambda _root, _locator: ValidationReport(True))
    ctx = PmRequestContext("p1", change_id, change.revision)
    before = subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip()
    with pytest.raises(PmError) as exc:
        service.approve(ctx, service.confirmation_token(ctx, owner="owner"), "reviewer", owner="owner")
    assert exc.value.code == "dirty_workspace"
    assert subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip() == before


@pytest.mark.parametrize("name,payload", [
    (".env", b"FAKE_TOKEN=not-a-secret\n"),
    ("unrelated.bin", b"\x00unreviewed\xff"),
])
def test_approval_never_stages_unknown_files_inside_change_root(
    tmp_path: Path, name: str, payload: bytes
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = _populate(repo, change_id, workspace, "unknown-change-file")
    unknown = workspace / "openspec" / "changes" / "unknown-change-file" / name
    unknown.write_bytes(payload)
    service = PmApprovalService(
        repo, repo.registry, lambda _root, _locator: ValidationReport(True)
    )
    ctx = PmRequestContext("p1", change_id, change.revision)
    before = subprocess.check_output(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True
    ).strip()

    with pytest.raises(PmError) as exc:
        service.approve(
            ctx, service.confirmation_token(ctx, owner="owner"),
            "reviewer", owner="owner",
        )

    assert exc.value.code == "dirty_workspace"
    assert unknown.read_bytes() == payload
    assert subprocess.check_output(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True
    ).strip() == before
    assert subprocess.run(
        ["git", "-C", str(workspace), "cat-file", "-e", f"HEAD:openspec/changes/unknown-change-file/{name}"],
        check=False, capture_output=True,
    ).returncode != 0


def test_approval_blocks_unknown_symlink_inside_change_root(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = _populate(repo, change_id, workspace, "unknown-change-link")
    outside = tmp_path / "outside.txt"
    outside.write_text("preserve me\n", encoding="utf-8")
    link = workspace / "openspec" / "changes" / "unknown-change-link" / "evidence-link"
    link.symlink_to(outside)
    service = PmApprovalService(
        repo, repo.registry, lambda _root, _locator: ValidationReport(True)
    )
    ctx = PmRequestContext("p1", change_id, change.revision)

    with pytest.raises(PmError) as exc:
        service.approve(
            ctx, service.confirmation_token(ctx, owner="owner"),
            "reviewer", owner="owner",
        )

    assert exc.value.code == "dirty_workspace"
    assert link.is_symlink()
    assert outside.read_text(encoding="utf-8") == "preserve me\n"


def test_approval_rejects_external_edit_and_unresolved_requirements(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = _populate(repo, change_id, workspace, "blocked")
    service = PmApprovalService(repo, repo.registry, lambda _root, _locator: ValidationReport(True))
    ctx = PmRequestContext("p1", change_id, change.revision)
    repo.begin_external_edit(change_id, fence=1, owner="owner")
    with pytest.raises(PmError) as exc:
        service.approve(ctx, service.confirmation_token(ctx, owner="owner"), "reviewer", owner="owner")
    assert exc.value.code == "scope_locked"

    repo.registry.set_pm_workspace_state("p1", change_id, fence=1, state="ACTIVE")
    spec = workspace / "openspec" / "changes" / "blocked" / "specs" / "requirements" / "spec.md"
    spec.write_text("# Requirements\n\nNo matching definition.\n", encoding="utf-8")
    changed = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    changed_ctx = PmRequestContext("p1", change_id, changed.revision)
    with pytest.raises(PmError) as exc:
        service.approve(changed_ctx, service.confirmation_token(changed_ctx, owner="owner"), "reviewer", owner="owner")
    assert exc.value.code == "unresolved_reference"


def test_requirement_reference_matching_is_exact_not_substring(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = _populate(repo, change_id, workspace, "exact-ref")
    spec = workspace / "openspec" / "changes" / "exact-ref" / "specs" / "requirements" / "spec.md"
    spec.write_text("# Requirements\n\n### Requirement: Other\n<!-- symphony:req=REQ-10 -->\n\n#### Scenario: Other works\n- GIVEN\n- WHEN\n- THEN\n", encoding="utf-8")
    change = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    service = PmApprovalService(repo, repo.registry, lambda _root, _locator: ValidationReport(True))
    ctx = PmRequestContext("p1", change_id, change.revision)
    with pytest.raises(PmError) as exc:
        service.approve(ctx, service.confirmation_token(ctx, owner="owner"), "reviewer", owner="owner")
    assert exc.value.code == "unresolved_reference"


def test_requirement_id_in_prose_without_supported_scenario_is_not_a_definition(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = _populate(repo, change_id, workspace, "prose-ref")
    spec = workspace / "openspec" / "changes" / "prose-ref" / "specs" / "requirements" / "spec.md"
    spec.write_text("# Notes\n\nREQ-1 is mentioned in prose only.\n", encoding="utf-8")
    change = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    service = PmApprovalService(repo, repo.registry, lambda _root, _locator: ValidationReport(True))
    ctx = PmRequestContext("p1", change_id, change.revision)
    with pytest.raises(PmError) as exc:
        service.approve(ctx, service.confirmation_token(ctx, owner="owner"), "reviewer", owner="owner")
    assert exc.value.code == "unresolved_reference"


@pytest.mark.parametrize("example", [
    "```markdown\n<!-- symphony:req=REQ-1 -->\n#### Scenario: Example\n<!-- symphony:scenario=SC-1 -->\n```\n",
    "> <!-- symphony:req=REQ-1 -->\n> #### Scenario: Example\n> <!-- symphony:scenario=SC-1 -->\n",
    "````markdown\n```\n<!-- symphony:req=REQ-1 -->\n#### Scenario: Example\n<!-- symphony:scenario=SC-1 -->\n````\n",
])
def test_fenced_or_quoted_requirement_examples_do_not_define_requirements(
    tmp_path: Path, example: str,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = _populate(repo, change_id, workspace, "example-ref")
    spec = workspace / "openspec" / "changes" / "example-ref" / "specs" / "requirements" / "spec.md"
    spec.write_text("# Documentation example\n\n" + example, encoding="utf-8")
    change = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    service = PmApprovalService(repo, repo.registry, lambda _root, _locator: ValidationReport(True))
    ctx = PmRequestContext("p1", change_id, change.revision)
    with pytest.raises(PmError) as exc:
        service.approve(ctx, service.confirmation_token(ctx, owner="owner"), "reviewer", owner="owner")
    assert exc.value.code == "unresolved_reference"


def test_current_approval_projects_until_real_revision_edit(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = _populate(repo, change_id, workspace)
    service = PmApprovalService(repo, repo.registry, lambda _root, _locator: ValidationReport(True))
    ctx = PmRequestContext("p1", change_id, change.revision)
    manifest = service.approve(ctx, service.confirmation_token(ctx, owner="owner"), "reviewer", owner="owner")
    approved = repo.get_change(ctx, owner="owner", locked=True)
    assert (approved.state, approved.approval_id) == ("Approved", manifest.approval_id)
    assert (approved.scope_hash, approved.revision) == (manifest.scope_hash, manifest.revision)

    document = repo.get_document(ctx, "proposal", owner="owner")
    edited = repo.patch_document(
        PmRequestContext("p1", change_id, approved.revision, "approval-edit"),
        "proposal", ({"op": "replace_source", "source": document.source + "\nReview note: updated scope.\n"},),
        owner="owner",
    )
    assert edited.revision != approved.revision
    current = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    assert (current.state, current.approval_id) == ("Validated", None)
    active = repo.registry.get_active_pm_approval("p1", change_id)
    assert active is not None and active["revision_hash"] == approved.revision
