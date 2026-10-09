from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

from symphony.pm.approval import PmApprovalService
from symphony.pm.models import PmError, PmRequestContext, ValidationReport
from symphony.pm.openspec import OpenSpecAdapter
from tests.test_pm_approval import _populate
from tests.test_pm_documents import _repository


def test_fresh_change_mints_unique_permanent_semantic_ids(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = repo.create_change(
        change_id=change_id, locator="semantic-ids", title="Semantic IDs", owner="owner"
    )

    ids = [item.semantic_id for item in change.semantic_index]
    assert len(ids) == 5
    assert len(set(ids)) == 5
    assert all(re.fullmatch(r"(?:REQ|SC|SB|API|DEC)-[0-9A-F]{8}", item) for item in ids)
    root = workspace / "openspec" / "changes" / "semantic-ids"
    assert "XXXXXXXX" not in "".join(
        path.read_text(encoding="utf-8")
        for path in root.rglob("*.md")
    )
    ledger = yaml.safe_load((root / "symphony.yaml").read_text(encoding="utf-8"))["semantic_ids"]
    assert set(ledger["active"]) == set(ids)
    assert ledger["retired"] == []


def test_raw_edit_preserves_rename_but_rejects_duplicate_retype_and_new_id(
    tmp_path: Path,
) -> None:
    repo, change_id, _ = _repository(tmp_path)
    change = repo.create_change(
        change_id=change_id, locator="semantic-edit", title="Semantic edit", owner="owner"
    )
    document = repo.get_document(PmRequestContext("p1", change_id), "spec:requirements", owner="owner")
    requirement = next(item for item in change.semantic_index if item.kind == "requirement")
    renamed = document.source.replace("Requirement: Observable behavior", "Requirement: Renamed behavior")
    result = repo.patch_document(
        PmRequestContext("p1", change_id, change.revision),
        "spec:requirements", ({"op": "replace_source", "source": renamed},), owner="owner",
    )
    current = repo.get_document(PmRequestContext("p1", change_id), "spec:requirements", owner="owner")
    assert requirement.semantic_id in current.source

    duplicate = current.source + f"\n### Requirement: Duplicate\n<!-- symphony:req={requirement.semantic_id} -->\n"
    with pytest.raises(PmError) as exc:
        repo.patch_document(
            PmRequestContext("p1", change_id, result.revision),
            "spec:requirements", ({"op": "replace_source", "source": duplicate},), owner="owner",
        )
    assert exc.value.code == "duplicate_id"

    retyped = current.source.replace(
        f"symphony:req={requirement.semantic_id}",
        f"symphony:scenario={requirement.semantic_id}",
    )
    with pytest.raises(PmError) as exc:
        repo.patch_document(
            PmRequestContext("p1", change_id, result.revision),
            "spec:requirements", ({"op": "replace_source", "source": retyped},), owner="owner",
        )
    assert exc.value.code == "semantic_id_retyped"

    invented = current.source + "\n#### Scenario: Invented\n<!-- symphony:scenario=SC-DEADBEEF -->\n"
    with pytest.raises(PmError) as exc:
        repo.patch_document(
            PmRequestContext("p1", change_id, result.revision),
            "spec:requirements", ({"op": "replace_source", "source": invented},), owner="owner",
        )
    assert exc.value.code == "semantic_id_replacement"


def test_modified_delta_inherits_accepted_baseline_ids_and_allows_rename(
    tmp_path: Path,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    first = repo.create_change(
        change_id=change_id, locator="first", title="First", owner="owner"
    )
    requirement = next(item.semantic_id for item in first.semantic_index if item.kind == "requirement")
    scenario = next(item.semantic_id for item in first.semantic_index if item.kind == "scenario")
    baseline = workspace / "openspec" / "specs" / "requirements" / "spec.md"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(
        f"# Requirements\n\n## Requirements\n\n### Requirement: Accepted title\n"
        f"<!-- symphony:req={requirement} -->\nAccepted.\n\n"
        f"#### Scenario: Accepted scenario\n<!-- symphony:scenario={scenario} -->\n"
        "- WHEN used\n- THEN works\n",
        encoding="utf-8",
    )
    record = repo.registry.get_pm_workspace("p1", change_id)
    assert record is not None
    repo.registry.set_pm_workspace_state("p1", change_id, fence=record.fence, state="RELEASED")
    second_id = __import__("uuid").uuid4().hex
    repo.claim_workspace(second_id, workspace, "owner")
    second = repo.create_change(
        change_id=second_id, locator="second", title="Second", owner="owner"
    )
    modified = (
        "# Requirements\n\n## MODIFIED Requirements\n\n"
        f"### Requirement: Renamed accepted title\n<!-- symphony:req={requirement} -->\nRevised.\n\n"
        f"#### Scenario: Accepted scenario\n<!-- symphony:scenario={scenario} -->\n"
        "- WHEN used\n- THEN revised behavior works\n"
    )
    result = repo.patch_document(
        PmRequestContext("p1", second_id, second.revision), "spec:requirements",
        ({"op": "replace_source", "source": modified},), owner="owner",
    )
    changed = repo.get_change(PmRequestContext("p1", second_id), owner="owner")
    assert result.revision == changed.revision
    assert {requirement, scenario} <= {item.semantic_id for item in changed.semantic_index}
    identity = yaml.safe_load(
        (workspace / "openspec" / "changes" / "second" / "symphony.yaml").read_text()
    )
    assert {item["semantic_id"] for item in identity["semantic_ids"]["inherited"]} == {requirement, scenario}


def test_storyboard_projection_and_typed_edit_preserve_stable_id(tmp_path: Path) -> None:
    repo, change_id, _workspace = _repository(tmp_path)
    change = repo.create_change(
        change_id=change_id, locator="storyboard-edit", title="Storyboard", owner="owner"
    )
    frame = change.storyboard_frames[0]
    assert frame.legacy_combined
    result = repo.patch_document(
        PmRequestContext("p1", change_id, change.revision), "ux",
        ({"op": "set_storyboard_fields", "storyboard_id": frame.storyboard_id, "fields": {
            "ui": "Card", "user_action": "Select", "system_response": "Open drawer",
            "success": "Opened", "alternate": "Already open", "failure": "Announced",
        }},), owner="owner",
    )
    refreshed = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    edited = refreshed.storyboard_frames[0]
    assert result.revision == refreshed.revision
    assert edited.storyboard_id == frame.storyboard_id
    assert not edited.legacy_combined
    assert (edited.ui, edited.user_action, edited.system_response) == ("Card", "Select", "Open drawer")


def test_real_openspec_second_archive_rename_preserves_req_scenario_ids(
    tmp_path: Path,
) -> None:
    executable = shutil.which("openspec")
    compatibility = Path("/private/tmp/pm-workspace-compat/openspec-cli/node_modules/.bin/openspec")
    if executable is None and compatibility.is_file():
        executable = str(compatibility)
    if executable is None:
        pytest.skip("OpenSpec 1.14 CLI is unavailable")
    adapter = OpenSpecAdapter(executable)
    repo, change_id, workspace = _repository(tmp_path)
    first = repo.create_change(change_id=change_id, locator="first", title="First", owner="owner")
    requirement = next(item.semantic_id for item in first.semantic_index if item.kind == "requirement")
    scenario = next(item.semantic_id for item in first.semantic_index if item.kind == "scenario")
    first_source = (
        "# Requirements\n\n## ADDED Requirements\n\n"
        f"### Requirement: Accepted title\n<!-- symphony:req={requirement} -->\n"
        "The system SHALL retain accepted behavior.\n\n"
        f"#### Scenario: Accepted scenario\n<!-- symphony:scenario={scenario} -->\n"
        "- WHEN used\n- THEN accepted behavior is visible\n"
    )
    repo.patch_document(PmRequestContext("p1", change_id, first.revision), "spec:requirements", ({"op": "replace_source", "source": first_source},), owner="owner")
    assert adapter.validate(workspace, "first").valid
    adapter.archive(adapter.archive_preview(workspace, "first"), workspace)
    baseline = workspace / "openspec" / "specs" / "requirements" / "spec.md"
    record = repo.registry.get_pm_workspace("p1", change_id)
    assert record is not None
    repo.registry.set_pm_workspace_state("p1", change_id, fence=record.fence, state="RELEASED")
    second_id = __import__("uuid").uuid4().hex
    repo.claim_workspace(second_id, workspace, "owner")
    second = repo.create_change(change_id=second_id, locator="second", title="Rename", owner="owner")
    second_source = (
        "# Requirements\n\n## RENAMED Requirements\n\n"
        "- FROM: `### Requirement: Accepted title`\n"
        "- TO: `### Requirement: Renamed accepted title`\n\n"
        "## MODIFIED Requirements\n\n"
        f"### Requirement: Renamed accepted title\n<!-- symphony:req={requirement} -->\n"
        "The system SHALL retain revised accepted behavior.\n\n"
        f"#### Scenario: Accepted scenario\n<!-- symphony:scenario={scenario} -->\n"
        "- WHEN used\n- THEN revised accepted behavior is visible\n"
    )
    repo.patch_document(PmRequestContext("p1", second_id, second.revision), "spec:requirements", ({"op": "replace_source", "source": second_source},), owner="owner")
    assert adapter.validate(workspace, "second").valid
    adapter.archive(adapter.archive_preview(workspace, "second"), workspace)
    archived = baseline.read_text(encoding="utf-8")
    assert "Requirement: Renamed accepted title" in archived
    assert requirement in archived and scenario in archived


def test_deleted_semantic_id_is_retired_and_cannot_be_reused(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = repo.create_change(
        change_id=change_id, locator="semantic-retire", title="Retire", owner="owner"
    )
    document = repo.get_document(PmRequestContext("p1", change_id), "ux", owner="owner")
    storyboard = next(item for item in change.semantic_index if item.kind == "storyboard")
    removed = "# UX and storyboard\n\n## Actors and journeys\nContent.\n"
    result = repo.patch_document(
        PmRequestContext("p1", change_id, change.revision), "ux",
        ({"op": "replace_source", "source": removed},), owner="owner",
    )
    identity = yaml.safe_load(
        (workspace / "openspec" / "changes" / "semantic-retire" / "symphony.yaml").read_text(encoding="utf-8")
    )
    assert storyboard.semantic_id in identity["semantic_ids"]["retired"]
    with pytest.raises(PmError) as exc:
        repo.patch_document(
            PmRequestContext("p1", change_id, result.revision), "ux",
            ({"op": "replace_source", "source": document.source},), owner="owner",
        )
    assert exc.value.code == "duplicate_id"


def test_owned_decisions_and_all_reference_families_block_semantic_readiness(
    tmp_path: Path,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = _populate(repo, change_id, workspace, "semantic-ready")
    storyboard_id = next(item.semantic_id for item in change.semantic_index if item.kind == "storyboard")
    root = workspace / "openspec" / "changes" / "semantic-ready"
    proposal = root / "proposal.md"
    proposal.write_text(
        proposal.read_text(encoding="utf-8")
        .replace("- Impact: Low", "- Impact: High")
        .replace("- Status: Accepted", "- Status: Unresolved"),
        encoding="utf-8",
    )
    tasks = root / "tasks.md"
    tasks.write_text(
        tasks.read_text(encoding="utf-8")
        .replace(re.search(r"requirements: \[([^]]+)\]", tasks.read_text(encoding="utf-8")).group(0), f"requirements: [{storyboard_id}]")
        .replace("storyboards:", "storyboards: [SB-MISSING]\n# prior-storyboards:")
        .replace("design_refs:", "design_refs: [API-MISSING]\n# prior-design_refs:"),
        encoding="utf-8",
    )

    report = repo.validate(PmRequestContext("p1", change_id), owner="owner")
    assert "blocking_decision" in {item.code for item in report.findings}
    unresolved = [item for item in report.findings if item.code == "unresolved_reference"]
    assert {(item.item_id, item.field, item.path) for item in unresolved} >= {
        ("T-ABCDEFGH", "requirement_refs", (storyboard_id,)),
        ("T-ABCDEFGH", "storyboard_refs", ("SB-MISSING",)),
        ("T-ABCDEFGH", "design_refs", ("API-MISSING",)),
    }


def test_accepted_low_impact_decision_requires_reason_and_index_navigates(
    tmp_path: Path,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    _populate(repo, change_id, workspace, "decision-reason")
    root = workspace / "openspec" / "changes" / "decision-reason"
    proposal = root / "proposal.md"
    proposal.write_text(
        re.sub(r"(?m)^- Reason:.*$", "- Reason:", proposal.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    refreshed = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    assert "decision_reason_required" in {item.code for item in refreshed.findings}
    assert {item.kind for item in refreshed.semantic_index} == {
        "requirement", "scenario", "storyboard", "contract", "decision"
    }
    requirement = next(item for item in refreshed.semantic_index if item.kind == "requirement")
    assert requirement.document_id == "spec:requirements"
    assert requirement.path == "specs/requirements/spec.md"
    assert requirement.line > 0


def test_fenced_decision_example_cannot_override_actual_blocking_metadata(
    tmp_path: Path,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    _populate(repo, change_id, workspace, "fenced-decision")
    proposal = workspace / "openspec" / "changes" / "fenced-decision" / "proposal.md"
    source = proposal.read_text(encoding="utf-8").replace(
        "- Impact: Low\n- Status: Accepted\n- Reason: The existing boundary is already verified.",
        "- Impact: High\n- Status: Unresolved\n- Reason:\n\n"
        "```markdown\n- Impact: Low\n- Status: Accepted\n- Reason: Example only\n```",
    )
    proposal.write_text(source, encoding="utf-8")
    changed = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    assert "blocking_decision" in {item.code for item in changed.findings}
    service = PmApprovalService(
        repo, repo.registry, lambda _root, _locator: ValidationReport(True)
    )
    ctx = PmRequestContext("p1", change_id, changed.revision)
    with pytest.raises(PmError) as exc:
        service.approve(
            ctx, service.confirmation_token(ctx, owner="owner"), "reviewer", owner="owner"
        )
    assert exc.value.code == "validation_failed"


def test_duplicate_actual_decision_metadata_is_ambiguous(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    _populate(repo, change_id, workspace, "duplicate-decision")
    proposal = workspace / "openspec" / "changes" / "duplicate-decision" / "proposal.md"
    proposal.write_text(
        proposal.read_text(encoding="utf-8").replace(
            "- Impact: Low", "- Impact: High\n- Impact: Low"
        ),
        encoding="utf-8",
    )
    report = repo.validate(PmRequestContext("p1", change_id), owner="owner")
    ambiguous = [item for item in report.findings if item.code == "decision_metadata_ambiguous"]
    assert [(item.field, item.severity) for item in ambiguous] == [("impact", "blocking")]


@pytest.mark.parametrize("settled", ["None.", "All settled."])
def test_explicitly_zero_open_decisions_is_valid(
    tmp_path: Path, settled: str
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = _populate(repo, change_id, workspace, "no-open-decisions")
    proposal = repo.get_document(
        PmRequestContext("p1", change_id), "proposal", owner="owner"
    )
    source = re.sub(
        r"(?s)(## Open decisions\n).*?(?=\n## Affected capabilities)",
        rf"\1\n{settled}\n",
        proposal.source,
    )
    result = repo.patch_document(
        PmRequestContext("p1", change_id, change.revision), "proposal",
        ({"op": "replace_source", "source": source},), owner="owner",
    )
    refreshed = repo.get_change(
        PmRequestContext("p1", change_id, result.revision), owner="owner"
    )
    assert not any(
        item.code in {"owned_decision_required", "idea_field_required"}
        and item.field == "open_decisions"
        for item in refreshed.findings
    )
    assert refreshed.decisions == ()


def test_structured_requirement_creation_atomically_mints_initial_scenario(
    tmp_path: Path,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = _populate(repo, change_id, workspace, "add-requirement")
    result = repo.patch_document(
        PmRequestContext("p1", change_id, change.revision), "spec:requirements",
        ({"op": "create_semantic", "kind": "requirement", "title": "Second behavior"},),
        owner="owner",
    )
    assert len(result.impacted_ids) == 2
    assert result.impacted_ids[0].startswith("REQ-")
    assert result.impacted_ids[1].startswith("SC-")
    refreshed = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    assert set(result.impacted_ids) <= {item.semantic_id for item in refreshed.semantic_index}
    assert "requirement_scenario_required" not in {item.code for item in refreshed.findings}
    identity = yaml.safe_load(
        (workspace / "openspec" / "changes" / "add-requirement" / "symphony.yaml").read_text(encoding="utf-8")
    )
    assert set(result.impacted_ids) <= set(identity["semantic_ids"]["active"])


def test_external_edit_cannot_swap_semantic_identity_kinds(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = _populate(repo, change_id, workspace, "external-retype")
    requirement = next(item.semantic_id for item in change.semantic_index if item.kind == "requirement")
    scenario = next(item.semantic_id for item in change.semantic_index if item.kind == "scenario")
    repo.begin_external_edit(change_id, fence=1, owner="owner")
    root = workspace / "openspec" / "changes" / "external-retype"
    spec = root / "specs" / "requirements" / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8")
        .replace(f"symphony:req={requirement}", "symphony:req=TEMP")
        .replace(f"symphony:scenario={scenario}", f"symphony:scenario={requirement}")
        .replace("symphony:req=TEMP", f"symphony:req={scenario}"),
        encoding="utf-8",
    )
    tasks = root / "tasks.md"
    tasks.write_text(
        tasks.read_text(encoding="utf-8").replace(requirement, scenario),
        encoding="utf-8",
    )
    report = repo.end_external_edit(change_id, fence=1, owner="owner")
    assert report.valid is False
    mismatches = [item for item in report.findings if item.code == "semantic_id_kind_mismatch"]
    assert {item.item_id for item in mismatches} == {requirement, scenario}
    assert repo.registry.get_pm_workspace("p1", change_id).state == "REPAIR_REQUIRED"  # type: ignore[union-attr]


@pytest.mark.parametrize("phase", ["after_document", "after_identity"])
def test_semantic_document_and_identity_transaction_recovers_after_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    locator = f"recover-{phase.replace('_', '-')}"
    change = repo.create_change(
        change_id=change_id, locator=locator, title="Recover", owner="owner"
    )
    monkeypatch.setenv("SYMPHONY_PM_SEMANTIC_FAULT", phase)
    with pytest.raises(RuntimeError, match=phase):
        repo.patch_document(
            PmRequestContext("p1", change_id, change.revision),
            "ux",
            ({"op": "create_semantic", "kind": "storyboard", "title": "Recovery frame"},),
            owner="owner",
        )
    monkeypatch.delenv("SYMPHONY_PM_SEMANTIC_FAULT")

    recovered = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    frames = [item for item in recovered.semantic_index if item.kind == "storyboard"]
    assert len(frames) == 2
    assert any(item.title == "Recovery frame" for item in frames)
    identity = yaml.safe_load(
        (workspace / "openspec" / "changes" / locator / "symphony.yaml").read_text(encoding="utf-8")
    )
    assert {item.semantic_id for item in frames} <= set(identity["semantic_ids"]["active"])
    assert not list((workspace / "openspec" / "changes" / locator).glob(".symphony-semantic-*"))
