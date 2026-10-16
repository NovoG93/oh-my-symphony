from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from symphony.pm.coverage import PmCoverageService
from symphony.pm.models import ChangeView, SemanticItem, WbsTask
from tests.test_pm_verification import _fixture


def test_coverage_is_conservative_across_all_required_links(tmp_path: Path) -> None:
    _repo, sha, run_id, registry, _store, service = _fixture(tmp_path, required=("pass",))
    receipt = service.run_procedure(
        operation_id="coverage-pass", project_id="p1", ticket_identifier="PM-1",
        task_revision="r1", procedure_id="pass", run_id=run_id,
    )
    preview = service.preview_acceptance(
        project_id="p1", ticket_identifier="PM-1", task_revision="r1",
        tested_commit=sha,
    )
    service.accept(
        preview=preview, confirmation_token=preview.confirmation_token,
        reviewer_label="reviewer",
    )
    change = ChangeView(
        "p1", "c1", "change", "rev", "scope", "Validated", "", "",
        semantic_index=(
            SemanticItem("SC-PARTIAL", "scenario", "Partial", "spec:requirements", "specs/requirements/spec.md", 1),
            SemanticItem("SC-FULL", "scenario", "Full", "spec:requirements", "specs/requirements/spec.md", 2),
            SemanticItem("SC-UNLINKED", "scenario", "Unlinked", "spec:requirements", "specs/requirements/spec.md", 3),
            SemanticItem("SC-WAIVED", "scenario", "Waived", "spec:requirements", "specs/requirements/spec.md", 4),
        ),
    )
    tasks = (
        WbsTask("T-ABCDEFGH", None, 10, "Backend", "First", requirement_refs=("SC-PARTIAL", "SC-FULL")),
        WbsTask("T-SECOND000", None, 20, "Frontend", "Second", requirement_refs=("SC-PARTIAL",)),
    )
    registry.create_pm_coverage_waiver({
        "waiver_id": "waiver-1", "operation_id": "waiver-op",
        "project_id": "p1", "change_id": "c1", "semantic_id": "SC-WAIVED",
        "reason": "Hardware-only path", "reviewer": "reviewer",
        "revision": "rev", "scope_hash": "scope",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    report = PmCoverageService(registry).build(change, tasks)
    by_id = {item.semantic_id: item for item in report.entities}
    partial = by_id["SC-PARTIAL"]
    assert partial.states["linked"].satisfied
    assert partial.states["implemented"].qualified_count == 1
    assert partial.states["implemented"].total_count == 2
    assert not partial.states["implemented"].satisfied
    assert not partial.states["tested"].satisfied
    assert not partial.states["accepted"].satisfied
    full = by_id["SC-FULL"]
    assert all(full.states[name].satisfied for name in ("specified", "linked", "implemented", "tested", "accepted"))
    assert full.task_links[0].receipt_ids == (receipt.receipt_id,)
    assert full.task_links[0].test_provenance == "host_executed"
    unlinked = by_id["SC-UNLINKED"]
    assert unlinked.states["specified"].satisfied
    assert not any(unlinked.states[name].satisfied for name in ("linked", "implemented", "tested", "accepted", "waived"))
    waived = by_id["SC-WAIVED"]
    assert waived.states["waived"].satisfied
    assert not any(waived.states[name].satisfied for name in ("linked", "implemented", "tested", "accepted"))
