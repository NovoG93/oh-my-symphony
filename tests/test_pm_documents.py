from __future__ import annotations

import subprocess
import uuid
import shutil
import json
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from symphony.orchestrator.run_registry import RunRegistry
from symphony.pm.documents import PmDocumentRepository
from symphony.pm.models import PmError, PmRequestContext


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def _repository(tmp_path: Path) -> tuple[PmDocumentRepository, str, Path]:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "user.name", "test")
    _git(project, "config", "user.email", "test@example.com")
    (project / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "fixture")
    workspace_root = tmp_path / "planning-root"
    workspace_root.mkdir()
    workspace = workspace_root / "change"
    _git(project, "worktree", "add", "-b", "planning", str(workspace))
    repo = PmDocumentRepository(
        project_id="p1",
        project_root=project,
        registry=RunRegistry(tmp_path / "state.db"),
        planning_workspace_root=workspace_root,
    )
    change_id = uuid.uuid4().hex
    repo.claim_workspace(change_id, workspace, "owner")
    return repo, change_id, workspace


def test_create_change_snapshot_and_cas_patch(tmp_path: Path):
    repo, change_id, workspace = _repository(tmp_path)
    created = repo.create_change(
        change_id=change_id, locator="invite-flow", title="Invite flow", owner="owner"
    )
    assert created.state == "Draft"
    root = workspace / "openspec" / "changes" / "invite-flow"
    assert (root / ".openspec.yaml").read_text(encoding="utf-8").startswith("schema: symphony-pm\n")
    assert (workspace / "openspec" / "schemas" / "symphony-pm" / "schema.yaml").is_file()
    assert repo.get_document(PmRequestContext("p1", change_id), "spec:requirements", owner="owner").path == "specs/requirements/spec.md"
    ctx = PmRequestContext("p1", change_id, created.revision, "op-1")
    result = repo.patch_document(
        ctx,
        "proposal",
        ({"op": "replace_source", "source": "# Invite flow\n\n## Problem\nClear."},),
        owner="owner",
    )
    assert result.previous_revision == created.revision
    assert result.revision != created.revision
    assert result.approval_invalidated
    with pytest.raises(PmError) as exc:
        repo.patch_document(ctx, "proposal", ({"op": "replace_source", "source": "old"},), owner="owner")
    assert exc.value.code == "revision_conflict"


def test_generated_managed_files_use_explicit_lf_bytes(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    repo.create_change(change_id=change_id, locator="lf-generated", title="LF", owner="owner")
    root = workspace / "openspec" / "changes" / "lf-generated"
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".yaml"}:
            assert b"\r\n" not in path.read_bytes()


def test_create_capability_spec_is_revision_checked_and_has_stable_document_id(tmp_path: Path):
    repo, change_id, workspace = _repository(tmp_path)
    change = repo.create_change(
        change_id=change_id, locator="capability-create", title="Capabilities", owner="owner"
    )
    result = repo.create_spec(
        PmRequestContext("p1", change_id, change.revision, "create-spec"),
        "billing", "Billing behavior", owner="owner",
    )
    document = repo.get_document(
        PmRequestContext("p1", change_id), "spec:billing", owner="owner"
    )
    assert document.document_id == "spec:billing"
    assert document.path == "specs/billing/spec.md"
    assert document.source.startswith("# Billing behavior\n")
    assert result.changed == ("specs/billing/spec.md",)
    with pytest.raises(PmError) as exc:
        repo.create_spec(
            PmRequestContext("p1", change_id, change.revision),
            "second", "Stale", owner="owner",
        )
    assert exc.value.code == "revision_conflict"


def test_fresh_change_is_recognized_by_real_openspec_profile_when_available(tmp_path: Path):
    executable = shutil.which("openspec")
    compatibility_cli = Path("/tmp/pm-workspace-compat/openspec-cli/node_modules/.bin/openspec")
    if executable is None and compatibility_cli.is_file():
        executable = str(compatibility_cli)
    if executable is None:
        pytest.skip("OpenSpec 1.14 compatibility CLI is unavailable")
    repo, change_id, workspace = _repository(tmp_path)
    repo.create_change(
        change_id=change_id, locator="real-profile", title="Real profile", owner="owner"
    )
    completed = subprocess.run(
        [executable, "status", "--change", "real-profile", "--json"],
        cwd=workspace, check=False, capture_output=True, text=True,
        env={**__import__("os").environ, "OPENSPEC_TELEMETRY": "0"},
    )
    assert completed.returncode == 0, completed.stderr
    payload = __import__("json").loads(completed.stdout)
    assert {item["id"] for item in payload["artifacts"]} >= {"proposal", "specs", "ux", "design", "tasks"}


def test_external_edit_suspends_app_writes_and_reindexes(tmp_path: Path):
    repo, change_id, workspace = _repository(tmp_path)
    created = repo.create_change(
        change_id=change_id, locator="external-edit", title="External", owner="owner"
    )
    claim = repo.begin_external_edit(change_id, fence=1, owner="owner")
    assert claim.state == "EXTERNAL_EDIT"
    with pytest.raises(PmError) as exc:
        repo.patch_document(
            PmRequestContext("p1", change_id, created.revision),
            "proposal",
            ({"op": "replace_source", "source": "blocked"},),
            owner="owner",
        )
    assert exc.value.code == "scope_locked"
    (workspace / "openspec" / "changes" / "external-edit" / "proposal.md").write_text(
        "# externally edited\n", encoding="utf-8"
    )
    report = repo.end_external_edit(change_id, fence=1, owner="owner")
    assert report.valid is False
    assert repo.registry.get_pm_workspace("p1", change_id).state == "REPAIR_REQUIRED"  # type: ignore[union-attr]


@pytest.mark.parametrize("newline", ["\r\n", "mixed"])
def test_managed_wbs_preserves_external_newline_bytes(
    tmp_path: Path, newline: str,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    from tests.test_pm_approval import _populate
    _populate(repo, change_id, workspace, "newline-bytes")
    path = workspace / "openspec" / "changes" / "newline-bytes" / "tasks.md"
    source = path.read_bytes().decode()
    external = source.replace("\n", "\r\n") if newline == "\r\n" else source.replace("\n", "\r\n", 4)
    repo.begin_external_edit(change_id, fence=1, owner="owner")
    path.write_bytes(external.encode())
    report = repo.end_external_edit(change_id, fence=1, owner="owner")
    if newline == "\r\n":
        assert not report.valid
        assert path.read_bytes() == external.encode()
        return
    assert report.valid
    current = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    before = path.read_bytes()
    result = repo.patch_wbs(
        PmRequestContext("p1", change_id, current.revision), (), owner="owner"
    )
    assert path.read_bytes() == before
    assert result.revision == current.revision


def test_multi_document_apply_is_one_revision_and_preserves_unselected_bytes(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    from tests.test_pm_approval import _populate
    change = _populate(repo, change_id, workspace, "multi-doc")
    proposal = repo.get_document(PmRequestContext("p1", change_id), "proposal", owner="owner")
    design = repo.get_document(PmRequestContext("p1", change_id), "design", owner="owner")
    ux_path = workspace / "openspec" / "changes" / "multi-doc" / "ux.md"
    ux_before = ux_path.read_bytes()
    result = repo.patch_documents(
        PmRequestContext("p1", change_id, change.revision, "multi-op"),
        {
            "proposal": proposal.source.replace("Reliable behavior", "Reliable revised behavior"),
            "design": design.source.replace("Existing service", "Existing bounded service"),
        }, owner="owner",
    )
    assert len(result.changed) == 2
    assert result.revision != change.revision
    assert ux_path.read_bytes() == ux_before


@pytest.mark.parametrize(
    "phase", ["after_document_1", "after_document_2", "after_identity"]
)
def test_multi_document_apply_recovers_after_each_published_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    from tests.test_pm_approval import _populate
    change = _populate(repo, change_id, workspace, "multi-recovery")
    proposal = repo.get_document(PmRequestContext("p1", change_id), "proposal", owner="owner")
    design = repo.get_document(PmRequestContext("p1", change_id), "design", owner="owner")
    proposed = {
        "proposal": proposal.source + "\nRecovered proposal note.\n",
        "design": design.source + "\nRecovered design note.\n",
    }
    # Both documents must really change, or recovery could pass by doing nothing.
    assert proposed["proposal"] != proposal.source
    assert proposed["design"] != design.source
    monkeypatch.setenv("SYMPHONY_PM_MULTI_FAULT", phase)
    with pytest.raises(RuntimeError, match="multi-document fault"):
        repo.patch_documents(
            PmRequestContext("p1", change_id, change.revision, "multi-crash"), proposed,
            owner="owner",
        )
    monkeypatch.delenv("SYMPHONY_PM_MULTI_FAULT")
    recovered = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    by_id = {item.document_id: item.source for item in recovered.documents}
    assert by_id["proposal"] == proposed["proposal"]
    assert by_id["design"] == proposed["design"]
    assert recovered.revision != change.revision


def test_workspace_claim_rejects_protected_checkout(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    repo = PmDocumentRepository(
        project_id="p1",
        project_root=project,
        registry=RunRegistry(tmp_path / "state.db"),
        planning_workspace_root=tmp_path,
    )
    with pytest.raises(PmError) as exc:
        repo.claim_workspace(uuid.uuid4().hex, project, "owner")
    assert exc.value.code == "protected_checkout"


def test_concurrent_same_revision_writers_have_one_success_and_one_conflict(
    tmp_path: Path,
) -> None:
    repo, change_id, _ = _repository(tmp_path)
    created = repo.create_change(
        change_id=change_id, locator="concurrent", title="Concurrent", owner="owner"
    )
    ctx = PmRequestContext("p1", change_id, created.revision)

    def write(value: str) -> str:
        isolated = PmDocumentRepository(
            project_id="p1",
            project_root=tmp_path / "project",
            registry=RunRegistry(tmp_path / "state.db"),
            planning_workspace_root=tmp_path / "planning-root",
        )
        try:
            return isolated.patch_document(
                ctx,
                "proposal",
                ({"op": "replace_source", "source": value},),
                owner="owner",
            ).revision
        except PmError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, ("first", "second")))
    assert sum(item.startswith("sha256:") for item in outcomes) == 1
    assert outcomes.count("revision_conflict") == 1


def test_change_identity_symlink_escape_is_not_followed(tmp_path: Path):
    repo, change_id, workspace = _repository(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "symphony.yaml").write_text(
        f"change_id: {change_id}\nplanning_format_version: 1\n",
        encoding="utf-8",
    )
    changes = workspace / "openspec" / "changes"
    changes.mkdir(parents=True)
    (changes / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PmError) as exc:
        repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    assert exc.value.code == "change_not_found"


def test_claim_requires_a_registered_worktree_of_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    other = tmp_path / "other"
    root = tmp_path / "planning-root"
    for path in (project, other):
        path.mkdir()
        _git(path, "init")
        _git(path, "config", "user.name", "test")
        _git(path, "config", "user.email", "test@example.com")
        (path / "README.md").write_text("fixture\n", encoding="utf-8")
        _git(path, "add", "README.md")
        _git(path, "commit", "-m", "fixture")
    root.mkdir()
    independent = root / "independent"
    other.rename(independent)
    repo = PmDocumentRepository(
        project_id="p1",
        project_root=project,
        registry=RunRegistry(tmp_path / "state.db"),
        planning_workspace_root=root,
    )
    with pytest.raises(PmError) as exc:
        repo.claim_workspace(uuid.uuid4().hex, independent, "owner")
    assert exc.value.code == "unsafe_workspace"


def test_patch_requires_revision_and_scope_hash_tracks_inputs_not_completion(
    tmp_path: Path,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    repo.create_change(
        change_id=change_id, locator="scope-hash", title="Scope", owner="owner"
    )
    with pytest.raises(PmError) as exc:
        repo.patch_document(
            PmRequestContext("p1", change_id),
            "proposal",
            ({"op": "replace_source", "source": "changed"},),
            owner="owner",
        )
    assert exc.value.code == "revision_conflict"

    tasks = workspace / "openspec" / "changes" / "scope-hash" / "tasks.md"
    source = """# Plan
<!-- symphony-wbs-format: 1 -->
<!-- symphony-task:T-ABCDEFGH -->
- [ ] Implement scope
```yaml symphony-task
id: T-ABCDEFGH
parent_id: null
order: 10
type: Backend
depends_on: []
```
### Scope
Implement it.
### Acceptance criteria
- It works.
### Verification
- Run tests.
<!-- /symphony-task -->
"""
    tasks.write_text(source, encoding="utf-8")
    unchecked = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    tasks.write_text(source.replace("- [ ]", "- [x]"), encoding="utf-8")
    completed = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    assert completed.scope_hash == unchecked.scope_hash
    schema = workspace / "openspec" / "schemas" / "symphony-pm"
    schema.mkdir(parents=True, exist_ok=True)
    (schema / "schema.yaml").write_text("name: changed\n", encoding="utf-8")
    assert repo.get_change(PmRequestContext("p1", change_id), owner="owner").scope_hash != unchecked.scope_hash


def test_symlinked_changes_directory_is_never_written(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    openspec = workspace / "openspec"
    openspec.mkdir()
    (openspec / "changes").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PmError) as exc:
        repo.create_change(
            change_id=change_id, locator="escape", title="Escape", owner="owner"
        )
    assert exc.value.code == "unsafe_path"
    assert list(outside.iterdir()) == []


def test_nondefault_authoring_limits_block_oversized_plan(tmp_path: Path) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    limited = PmDocumentRepository(
        project_id="p1",
        project_root=repo.project_root,
        registry=repo.registry,
        planning_workspace_root=repo.planning_workspace_root,
        max_nodes=1,
        max_edges=20_000,
        max_text_chars=16_384,
    )
    created = limited.create_change(
        change_id=change_id, locator="small-plan", title="Small plan", owner="owner"
    )
    source = """# Plan
<!-- symphony-wbs-format: 1 -->
<!-- symphony-task:T-AAAAAAAA -->
- [ ] First
```yaml symphony-task
id: T-AAAAAAAA
parent_id: null
order: 10
type: Backend
depends_on: []
```
### Scope
First.
### Acceptance criteria
- Done.
### Verification
- Test.
### Integration impact
None.
<!-- /symphony-task -->
<!-- symphony-task:T-BBBBBBBB -->
- [ ] Second
```yaml symphony-task
id: T-BBBBBBBB
parent_id: null
order: 20
type: Backend
depends_on: []
```
### Scope
Second.
### Acceptance criteria
- Done.
### Verification
- Test.
### Integration impact
None.
<!-- /symphony-task -->
"""
    with pytest.raises(PmError) as exc:
        limited.patch_document(
            PmRequestContext("p1", change_id, created.revision), "tasks",
            ({"op": "replace_source", "source": source},), owner="owner",
        )
    assert exc.value.code == "unsupported_format"
    assert exc.value.details["findings"] == ["node_limit_exceeded"]
    assert (workspace / "openspec" / "changes" / "small-plan" / "tasks.md").read_text(encoding="utf-8") != source


def test_work_item_ids_are_server_minted_retired_and_exactly_restorable(
    tmp_path: Path,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = repo.create_change(
        change_id=change_id, locator="work-identity", title="Work identity",
        owner="owner",
    )
    task = {"kind": "Feature", "title": "Server task", "order": 10}
    created = repo.patch_wbs(
        PmRequestContext("p1", change_id, change.revision),
        ({"op": "create_task", "task": task},), owner="owner",
    )
    task_id = created.impacted_ids[0]
    assert task_id.startswith("T-")
    renamed = repo.patch_wbs(
        PmRequestContext("p1", change_id, created.revision),
        ({"op": "set_field", "task_id": task_id, "field": "title", "value": "Renamed"},),
        owner="owner",
    )
    selected = repo.get_wbs(
        PmRequestContext("p1", change_id), owner="owner"
    ).tasks[0]
    snapshot = json.loads(json.dumps(asdict(selected)))
    deleted = repo.patch_wbs(
        PmRequestContext("p1", change_id, renamed.revision),
        ({"op": "delete_task", "task_id": task_id},), owner="owner",
    )
    repo = PmDocumentRepository(
        project_id="p1", project_root=repo.project_root,
        registry=RunRegistry(tmp_path / "state.db"),
        planning_workspace_root=workspace.parent,
    )
    with pytest.raises(PmError, match="same entity"):
        repo.patch_wbs(
            PmRequestContext("p1", change_id, deleted.revision),
            ({"op": "restore_task", "task_id": task_id,
              "task": {**snapshot, "title": "Different entity"}},),
            owner="owner",
        )
    restored = repo.patch_wbs(
        PmRequestContext("p1", change_id, deleted.revision),
        ({"op": "restore_task", "task_id": task_id, "task": snapshot},),
        owner="owner",
    )
    assert restored.revision != deleted.revision
    assert repo.get_wbs(
        PmRequestContext("p1", change_id), owner="owner"
    ).tasks[0].title == "Renamed"
    with pytest.raises(PmError) as client_id:
        repo.patch_wbs(
            PmRequestContext("p1", change_id, restored.revision),
            ({"op": "add_task", "task_id": "T-CLIENT001",
              "task": {**task, "task_id": "T-CLIENT001"}},), owner="owner",
        )
    assert client_id.value.code == "server_minted_id_required"


def test_raw_and_external_edits_cannot_replace_server_work_item_identity(
    tmp_path: Path,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    change = repo.create_change(
        change_id=change_id, locator="raw-identity", title="Raw identity", owner="owner"
    )
    created = repo.patch_wbs(
        PmRequestContext("p1", change_id, change.revision),
        ({"op": "create_task", "task": {"kind": "Feature", "title": "Original", "order": 10}},),
        owner="owner",
    )
    task_id = created.impacted_ids[0]
    document = repo.get_document(
        PmRequestContext("p1", change_id), "tasks", owner="owner"
    )
    attacked = document.source.replace(task_id, "T-ATTACK01")
    with pytest.raises(PmError) as raw:
        repo.patch_document(
            PmRequestContext("p1", change_id, created.revision), "tasks",
            ({"op": "replace_source", "source": attacked},), owner="owner",
        )
    assert raw.value.code == "work_item_id_replacement"

    claim = repo.begin_external_edit(change_id, fence=1, owner="owner")
    assert claim.state == "EXTERNAL_EDIT"
    path = workspace / "openspec" / "changes" / "raw-identity" / "tasks.md"
    path.write_text(document.source.replace("Original", "External rename"), encoding="utf-8")
    first_report = repo.end_external_edit(change_id, fence=1, owner="owner")
    assert "work_item_id_replacement" not in {item.code for item in first_report.findings}
    record = repo.registry.get_pm_workspace("p1", change_id)
    assert record is not None
    assert repo.registry.set_pm_workspace_state(
        "p1", change_id, fence=record.fence, state="ACTIVE"
    )
    claim = repo.begin_external_edit(change_id, fence=1, owner="owner")
    assert claim.state == "EXTERNAL_EDIT"
    path.write_text(path.read_text().replace(task_id, "T-ATTACK02"), encoding="utf-8")
    report = repo.end_external_edit(change_id, fence=1, owner="owner")
    assert not report.valid
    assert "work_item_id_replacement" in {item.code for item in report.findings}
    assert repo.registry.get_pm_workspace("p1", change_id).state == "REPAIR_REQUIRED"  # type: ignore[union-attr]


def test_dirty_paths_preserve_leading_character_for_tracked_changes(
    tmp_path: Path,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    repo.create_change(
        change_id=change_id, locator="dirty-path", title="Dirty path", owner="owner"
    )
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "planning base")
    identity = workspace / "openspec" / "changes" / "dirty-path" / "symphony.yaml"
    identity.write_text(identity.read_text() + "note: changed\n", encoding="utf-8")
    dirty = repo._claim(change_id, owner="owner").dirty_paths
    assert "openspec/changes/dirty-path/symphony.yaml" in dirty
    assert not any(item.startswith("penspec/") for item in dirty)


def test_same_change_repair_rebinds_without_reusing_retired_identity(
    tmp_path: Path,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    repo.create_change(
        change_id=change_id, locator="repair-id", title="Repair", owner="owner"
    )
    _git(workspace, "add", ".")
    _git(workspace, "commit", "-m", "planning identity")
    repaired = tmp_path / "planning-root" / "repaired"
    _git(repo.project_root, "worktree", "add", "-b", "repair", str(repaired), "planning")
    rebound = repo.claim_workspace(
        change_id, repaired, "owner", replace_existing=True
    )
    assert rebound.fence == 2
    assert rebound.workspace_path == repaired.resolve()
    assert repo.get_change(
        PmRequestContext("p1", change_id), owner="owner"
    ).locator == "repair-id"


def test_duplicate_active_change_identity_is_rejected_as_ambiguous(
    tmp_path: Path,
) -> None:
    repo, change_id, workspace = _repository(tmp_path)
    repo.create_change(
        change_id=change_id, locator="identity-one", title="Identity one",
        owner="owner",
    )
    changes = workspace / "openspec" / "changes"
    shutil.copytree(changes / "identity-one", changes / "identity-two")
    with pytest.raises(PmError) as ambiguous:
        repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    assert ambiguous.value.code == "ambiguous_change_identity"


def test_structured_field_batch_parses_the_full_wbs_only_before_and_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import symphony.pm.documents as documents
    import symphony.pm.wbs as wbs

    repo, change_id, _ = _repository(tmp_path)
    created = repo.create_change(
        change_id=change_id, locator="batch-save", title="Batch save", owner="owner"
    )
    operations = [
        {
            "op": "create_task",
            "task": {
                "parent_id": None, "order": index, "kind": "Feature",
                "title": f"Task {index}", "description": f"Task {index}.",
                "dependencies": [], "requirement_refs": [],
                "storyboard_refs": [], "design_refs": [],
                "acceptance": ["Observable."], "verification": ["focused"],
                "integration_impact": "None.", "risk": "", "notes": "",
                "executable": True,
            },
        }
        for index in range(5)
    ]
    seeded = repo.patch_wbs(
        PmRequestContext("p1", change_id, created.revision), operations, owner="owner"
    )
    task_ids = list(seeded.impacted_ids)
    full_parses: list[int] = []
    original = wbs.parse_wbs

    def counting(source: str):
        if source.count("<!-- symphony-task:") > 1:
            full_parses.append(len(source))
        return original(source)

    monkeypatch.setattr(documents, "parse_wbs", counting)
    monkeypatch.setattr(wbs, "parse_wbs", counting)
    result = repo.patch_wbs(
        PmRequestContext("p1", change_id, seeded.revision),
        [
            {"op": "set_field", "task_id": task_id, "field": "notes", "value": "batched"}
            for task_id in task_ids
        ],
        owner="owner",
    )
    assert result.revision != seeded.revision
    assert len(full_parses) == 2
    monkeypatch.undo()
    wbs_view = repo.get_wbs(PmRequestContext("p1", change_id), owner="owner")
    assert {task.task_id: task.notes for task in wbs_view.tasks if task.task_id in task_ids} == {
        task_id: "batched" for task_id in task_ids
    }
