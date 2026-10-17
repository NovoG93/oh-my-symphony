from __future__ import annotations

import os
import sqlite3
import subprocess
import threading
import uuid
from pathlib import Path

import pytest
import yaml

from symphony.pm import documents as pm_documents
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


# Round9 F3: a sibling change's persistent internal lock is runtime coordination,
# not reviewed plan content. Only a proven same-owner sibling lock is excluded.


def _head(workspace: Path) -> str:
    return subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip()


def _commit_all_except_locks(workspace: Path, message: str = "sibling plan") -> None:
    subprocess.run(
        ["git", "-C", str(workspace), "add", "-A", "--", ".", ":(exclude,glob)**/.symphony-pm.lock"],
        check=True, capture_output=True,
    )
    subprocess.run(["git", "-C", str(workspace), "commit", "-q", "-m", message], check=True, capture_output=True)


def _sibling_setup(tmp_path: Path, *, approve_first: bool):
    """First change leaves its empty lock; returns a ready second change in the same worktree."""
    repo, first_id, workspace = _repository(tmp_path)
    first = _populate(repo, first_id, workspace, "first")
    service = PmApprovalService(repo, repo.registry, lambda _root, _locator: ValidationReport(True))
    if approve_first:
        ctx = PmRequestContext("p1", first_id, first.revision)
        service.approve(ctx, service.confirmation_token(ctx, owner="owner"), "reviewer", owner="owner")
    else:
        # Read-created lock on an unapproved draft; its legitimate documents are committed.
        repo.get_change(PmRequestContext("p1", first_id), owner="owner", locked=True)
        _commit_all_except_locks(workspace)
    lock = workspace / "openspec" / "changes" / "first" / ".symphony-pm.lock"
    assert lock.is_file() and lock.stat().st_size == 0
    second_id = uuid.uuid4().hex
    repo.claim_workspace(second_id, workspace, "owner")
    second = _populate(repo, second_id, workspace, "second")
    return repo, service, workspace, first_id, second_id, second, lock


def _approve(service, repo, change_id: str):
    change = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    ctx = PmRequestContext("p1", change_id, change.revision)
    return service.approve(ctx, service.confirmation_token(ctx, owner="owner"), "reviewer", owner="owner")


def _assert_refused(service, repo, workspace: Path, change_id: str, expected_path: str) -> None:
    before = _head(workspace)
    manifests = workspace / ".symphony" / "pm" / "manifests"
    existing = set(manifests.glob("*.json")) if manifests.exists() else set()
    with pytest.raises(PmError) as exc:
        _approve(service, repo, change_id)
    assert exc.value.code == "dirty_workspace"
    assert exc.value.status == 409
    assert expected_path in exc.value.details["paths"]
    assert _head(workspace) == before
    assert (set(manifests.glob("*.json")) if manifests.exists() else set()) == existing
    staged = subprocess.check_output(["git", "-C", str(workspace), "diff", "--cached", "--name-only"], text=True)
    assert staged == ""


@pytest.mark.parametrize("approve_first", [True, False], ids=["approved-sibling", "draft-sibling"])
def test_round9_sibling_internal_lock_does_not_block_next_approval(tmp_path: Path, approve_first: bool) -> None:
    repo, service, workspace, _first_id, second_id, _second, lock = _sibling_setup(tmp_path, approve_first=approve_first)
    identity = os.lstat(lock)
    before = _head(workspace)
    recovered: list[Path] = []
    original_recover = pm_documents._recover_change_transactions

    def spy(root: Path) -> None:
        recovered.append(root)
        original_recover(root)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pm_documents, "_recover_change_transactions", spy)
        manifest = _approve(service, repo, second_id)
    assert manifest.change_id == second_id
    assert _head(workspace) != before
    tree = subprocess.check_output(["git", "-C", str(workspace), "ls-tree", "-r", "--name-only", "HEAD"], text=True).splitlines()
    assert "openspec/changes/first/.symphony-pm.lock" not in tree
    assert "openspec/changes/second/.symphony-pm.lock" not in tree
    after = os.lstat(lock)
    assert (after.st_dev, after.st_ino, after.st_size, after.st_nlink) == (identity.st_dev, identity.st_ino, 0, 1)
    sibling_root = (workspace / "openspec" / "changes" / "first").resolve()
    assert sibling_root not in recovered, "sibling lookup must not run transaction recovery"
    status = subprocess.check_output(["git", "-C", str(workspace), "status", "--porcelain=v1", "--untracked-files=all"], text=True)
    assert "?? openspec/changes/first/.symphony-pm.lock" in status.splitlines()


def test_round9_sibling_lock_held_by_concurrent_reader_does_not_block_or_change(tmp_path: Path) -> None:
    repo, service, workspace, _first_id, second_id, _second, lock = _sibling_setup(tmp_path, approve_first=True)
    sibling_root = workspace / "openspec" / "changes" / "first"
    held, release = threading.Event(), threading.Event()

    def reader() -> None:
        with pm_documents._change_lock(sibling_root, shared=True):
            held.set()
            release.wait(10)

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        assert held.wait(10)
        _approve(service, repo, second_id)
    finally:
        release.set()
        thread.join(10)
    assert lock.stat().st_size == 0


def test_round9_dirty_sibling_draft_content_still_blocks(tmp_path: Path) -> None:
    repo, first_id, workspace = _repository(tmp_path)
    _populate(repo, first_id, workspace, "first")
    second_id = uuid.uuid4().hex
    repo.claim_workspace(second_id, workspace, "owner")
    _populate(repo, second_id, workspace, "second")
    service = PmApprovalService(repo, repo.registry, lambda _root, _locator: ValidationReport(True))
    _assert_refused(service, repo, workspace, second_id, "openspec/changes/first/proposal.md")


@pytest.mark.parametrize("variant", ["nonempty", "symlink", "hardlink"])
def test_round9_unsafe_sibling_lock_file_still_blocks(tmp_path: Path, variant: str) -> None:
    repo, service, workspace, _first_id, second_id, _second, lock = _sibling_setup(tmp_path, approve_first=True)
    if variant == "nonempty":
        lock.write_bytes(b"x")
    elif variant == "symlink":
        target = tmp_path / "outside.lock"
        target.write_bytes(b"")
        lock.unlink()
        lock.symlink_to(target)
    else:
        os.link(lock, tmp_path / "second-name.lock")
    _assert_refused(service, repo, workspace, second_id, "openspec/changes/first/.symphony-pm.lock")


def test_round9_tracked_modified_or_renamed_sibling_lock_still_blocks(tmp_path: Path) -> None:
    repo, service, workspace, _first_id, second_id, _second, lock = _sibling_setup(tmp_path, approve_first=True)
    lock.write_bytes(b"tracked")
    subprocess.run(["git", "-C", str(workspace), "add", "-f", "--", str(lock)], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-q", "-m", "track lock"], check=True)
    lock.write_bytes(b"")
    _assert_refused(service, repo, workspace, second_id, "openspec/changes/first/.symphony-pm.lock")

    subprocess.run(["git", "-C", str(workspace), "checkout", "--", str(lock)], check=True)
    subprocess.run(["git", "-C", str(workspace), "rm", "-q", "--cached", "--", str(lock)], check=True)
    lock.unlink()
    notes = workspace / "openspec" / "changes" / "first" / "notes.txt"
    notes.write_bytes(b"")
    subprocess.run(["git", "-C", str(workspace), "add", "--", str(notes)], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-q", "-m", "notes"], check=True)
    subprocess.run(["git", "-C", str(workspace), "mv", "--", str(notes), str(lock)], check=True)
    before = _head(workspace)
    with pytest.raises(PmError) as exc:
        _approve(service, repo, second_id)
    assert exc.value.code == "dirty_workspace"
    assert any(".symphony-pm.lock" in path for path in exc.value.details["paths"])
    assert _head(workspace) == before


@pytest.mark.parametrize("variant", ["foreign-owner", "external-edit", "released", "registry-failure"])
def test_round9_unproven_sibling_claim_still_blocks(tmp_path: Path, variant: str) -> None:
    repo, service, workspace, first_id, second_id, _second, _lock = _sibling_setup(tmp_path, approve_first=True)
    record = repo.registry.get_pm_workspace("p1", first_id)
    assert record is not None
    with pytest.MonkeyPatch.context() as patch:
        if variant == "foreign-owner":
            repo.claim_workspace(first_id, workspace, "someone-else", replace_existing=True)
        elif variant == "external-edit":
            repo.registry.set_pm_workspace_state("p1", first_id, fence=record.fence, state="EXTERNAL_EDIT")
        elif variant == "released":
            repo.registry.set_pm_workspace_state("p1", first_id, fence=record.fence, state="RELEASED")
        else:
            original = repo.registry.get_pm_workspace

            def failing(project_id: str, change_id: str):
                if change_id == first_id:
                    raise sqlite3.OperationalError("unable to open database file")
                return original(project_id, change_id)

            patch.setattr(repo.registry, "get_pm_workspace", failing)
        _assert_refused(service, repo, workspace, second_id, "openspec/changes/first/.symphony-pm.lock")


@pytest.mark.parametrize("variant", ["unclaimed", "duplicate", "malformed", "duplicate-key"])
def test_round9_unknown_or_ambiguous_sibling_identity_still_blocks(tmp_path: Path, variant: str) -> None:
    repo, service, workspace, first_id, second_id, _second, _lock = _sibling_setup(tmp_path, approve_first=True)
    other = workspace / "openspec" / "changes" / "other"
    other.mkdir()
    identity = {
        "unclaimed": f"change_id: {uuid.uuid4().hex}\n",
        "duplicate": f"change_id: {first_id}\n",
        "malformed": "change_id: [unterminated\n",
        "duplicate-key": f"change_id: {first_id}\nchange_id: {first_id}\n",
    }[variant]
    (other / "symphony.yaml").write_text(identity, encoding="utf-8")
    _commit_all_except_locks(workspace, "other identity")
    (other / ".symphony-pm.lock").write_bytes(b"")
    _assert_refused(service, repo, workspace, second_id, "openspec/changes/other/.symphony-pm.lock")
    if variant == "duplicate":
        # An ambiguous identity also withdraws the original sibling's proof.
        with pytest.raises(PmError) as exc:
            _approve(service, repo, second_id)
        assert "openspec/changes/first/.symphony-pm.lock" in exc.value.details["paths"]


def test_round9_sibling_claimed_for_another_worktree_still_blocks(tmp_path: Path) -> None:
    repo, second_id, workspace = _repository(tmp_path)
    project = tmp_path / "project"
    elsewhere = tmp_path / "planning-root" / "elsewhere"
    subprocess.run(["git", "-C", str(project), "worktree", "add", "-q", "-b", "elsewhere", str(elsewhere)], check=True)
    first_id = uuid.uuid4().hex
    repo.claim_workspace(first_id, elsewhere, "owner")
    repo.create_change(change_id=first_id, locator="first", title="First", owner="owner")
    copied = workspace / "openspec" / "changes" / "first"
    copied.mkdir(parents=True)
    (copied / "symphony.yaml").write_bytes((elsewhere / "openspec" / "changes" / "first" / "symphony.yaml").read_bytes())
    _commit_all_except_locks(workspace, "copied identity")
    (copied / ".symphony-pm.lock").write_bytes(b"")
    _populate(repo, second_id, workspace, "second")
    service = PmApprovalService(repo, repo.registry, lambda _root, _locator: ValidationReport(True))
    _assert_refused(service, repo, workspace, second_id, "openspec/changes/first/.symphony-pm.lock")


def test_round9_symlinked_sibling_directory_or_identity_still_blocks(tmp_path: Path) -> None:
    repo, service, workspace, _first_id, second_id, _second, _lock = _sibling_setup(tmp_path, approve_first=True)
    changes = workspace / "openspec" / "changes"
    (changes / "linked").symlink_to(changes / "first", target_is_directory=True)
    _assert_refused(service, repo, workspace, second_id, "openspec/changes/linked")
    (changes / "linked").unlink()
    identity = changes / "first" / "symphony.yaml"
    real = tmp_path / "identity.yaml"
    real.write_bytes(identity.read_bytes())
    identity.unlink()
    identity.symlink_to(real)
    _assert_refused(service, repo, workspace, second_id, "openspec/changes/first/.symphony-pm.lock")


def test_round9_default_change_root_resolution_still_recovers(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    _populate(repo, change_id, workspace, "first")
    recovered: list[Path] = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pm_documents, "_recover_change_transactions", recovered.append)
        root, identity = pm_documents._resolve_change_root(workspace, change_id)
    assert recovered == [root]
    assert identity["change_id"] == change_id
