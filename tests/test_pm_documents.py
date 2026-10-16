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


def _interrupted_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> tuple[PmDocumentRepository, str, Path, Path]:
    """Leave a journal + staged effects on disk, as a live writer would mid-transaction."""
    from tests.test_pm_approval import _populate

    repo, change_id, workspace = _repository(tmp_path)
    change = _populate(repo, change_id, workspace, f"live-{kind}")
    root = (workspace / "openspec" / "changes" / f"live-{kind}").resolve()
    ctx = PmRequestContext("p1", change_id, change.revision, f"live-{kind}")
    if kind == "semantic":
        monkeypatch.setenv("SYMPHONY_PM_SEMANTIC_FAULT", "after_document")
        with pytest.raises(RuntimeError):
            repo.patch_document(
                ctx, "ux",
                ({"op": "create_semantic", "kind": "storyboard", "title": "Live frame"},),
                owner="owner",
            )
        monkeypatch.delenv("SYMPHONY_PM_SEMANTIC_FAULT")
        journal = root / ".symphony-semantic-journal.json"
        assert (root / ".symphony-semantic-identity.next").is_file()
    else:
        proposal = repo.get_document(PmRequestContext("p1", change_id), "proposal", owner="owner")
        design = repo.get_document(PmRequestContext("p1", change_id), "design", owner="owner")
        monkeypatch.setenv("SYMPHONY_PM_MULTI_FAULT", "after_document_1")
        with pytest.raises(RuntimeError):
            repo.patch_documents(
                ctx,
                {"proposal": proposal.source + "\nLive note.\n", "design": design.source + "\nLive note.\n"},
                owner="owner",
            )
        monkeypatch.delenv("SYMPHONY_PM_MULTI_FAULT")
        journal = root / ".symphony-multi-document-journal.json"
    assert journal.is_file()
    return repo, change_id, root, journal


@pytest.mark.parametrize("kind", ["semantic", "multi"])
def test_reader_does_not_roll_forward_a_journal_while_the_change_lock_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    # A journal is indistinguishable from a crash only once its writer has let
    # go of the change lock; a reader thread must wait for that instead of
    # replaying stage files out from under the live writer.
    import threading

    from symphony.pm.documents import _change_lock
    from symphony.pm.routes import _with_thread_repository

    repo, change_id, root, journal = _interrupted_transaction(tmp_path, monkeypatch, kind)
    outcome: dict[str, object] = {}

    def read() -> None:
        try:
            outcome["change"] = _with_thread_repository(
                repo, lambda thread_repo: thread_repo.get_change(PmRequestContext("p1", change_id), owner="owner"),
            )
        except BaseException as exc:  # surfaced by the assertions below
            outcome["error"] = exc

    reader = threading.Thread(target=read, daemon=True)
    with _change_lock(root):
        reader.start()
        # Bounded observation window: without the lock the reader finishes the
        # roll-forward well inside it; with the lock it can only be blocked.
        reader.join(timeout=0.5)
        assert reader.is_alive(), outcome
        assert journal.is_file()
    reader.join(timeout=30)
    assert not reader.is_alive()
    assert "error" not in outcome, outcome
    assert not journal.exists()


def test_lock_holding_reader_recovers_without_self_deadlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Approval and end_external_edit read the change while already holding its
    # lock; recovery there must reuse the held lock, not flock a second fd.
    import threading

    from symphony.pm.documents import _change_lock
    from symphony.pm.routes import _with_thread_repository

    repo, change_id, root, journal = _interrupted_transaction(tmp_path, monkeypatch, "semantic")
    outcome: dict[str, object] = {}

    def locked_read() -> None:
        try:
            with _change_lock(root):
                outcome["change"] = _with_thread_repository(
                    repo, lambda thread_repo: thread_repo.get_change(PmRequestContext("p1", change_id), owner="owner"),
                )
        except BaseException as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=locked_read, daemon=True)
    worker.start()
    worker.join(timeout=30)
    assert not worker.is_alive(), "get_change deadlocked under its own change lock"
    assert "error" not in outcome, outcome
    assert not journal.exists()
    frames = [item for item in outcome["change"].semantic_index if item.kind == "storyboard"]  # type: ignore[attr-defined]
    assert any(item.title == "Live frame" for item in frames)


def test_exchange_snapshot_read_does_not_observe_a_half_applied_write(
    tmp_path: Path,
) -> None:
    # Export and import preview read the change on a worker thread; a loop
    # writer holding the change lock mid-commit must not leak a mixture of
    # old and new documents (or documents that disagree with the revision)
    # into the trusted export base.
    import threading

    from tests.test_pm_approval import _populate

    from symphony.pm.documents import _change_lock, _revision_hash
    from symphony.pm.routes import _read_change_snapshot

    repo, change_id, workspace = _repository(tmp_path)
    _populate(repo, change_id, workspace, "snapshot")
    root = (workspace / "openspec" / "changes" / "snapshot").resolve()
    proposal = (root / "proposal.md").read_text(encoding="utf-8") + "\nWriter note.\n"
    design = (root / "design.md").read_text(encoding="utf-8") + "\nWriter note.\n"
    outcome: dict[str, object] = {}

    def read() -> None:
        try:
            outcome["change"] = _read_change_snapshot(
                repo, PmRequestContext("p1", change_id), "owner"
            )
        except BaseException as exc:  # surfaced by the assertions below
            outcome["error"] = exc

    reader = threading.Thread(target=read, daemon=True)
    with _change_lock(root):
        (root / "proposal.md").write_text(proposal, encoding="utf-8")
        reader.start()
        # Bounded window: an unlocked reader finishes well inside it and sees
        # the new proposal beside the old design.
        reader.join(timeout=0.5)
        assert reader.is_alive(), outcome
        (root / "design.md").write_text(design, encoding="utf-8")
    reader.join(timeout=30)
    assert not reader.is_alive()
    assert "error" not in outcome, outcome
    change = outcome["change"]
    sources = {item.document_id: item.source for item in change.documents}  # type: ignore[attr-defined]
    assert sources["proposal"] == proposal
    assert sources["design"] == design
    assert change.revision == _revision_hash(root)  # type: ignore[attr-defined]


@pytest.mark.skipif(__import__("os").name == "nt", reason="msvcrt has no shared lock")
def test_exchange_snapshot_readers_share_the_change_lock(tmp_path: Path) -> None:
    import threading

    from tests.test_pm_approval import _populate

    from symphony.pm.documents import _change_lock
    from symphony.pm.routes import _read_change_snapshot

    repo, change_id, workspace = _repository(tmp_path)
    _populate(repo, change_id, workspace, "shared")
    root = (workspace / "openspec" / "changes" / "shared").resolve()
    outcome: dict[str, object] = {}

    def read() -> None:
        try:
            outcome["change"] = _read_change_snapshot(
                repo, PmRequestContext("p1", change_id), "owner"
            )
        except BaseException as exc:
            outcome["error"] = exc

    reader = threading.Thread(target=read, daemon=True)
    with _change_lock(root, shared=True):
        reader.start()
        reader.join(timeout=30)
        assert not reader.is_alive(), "a concurrent reader serialized behind a shared lock"
    assert "error" not in outcome, outcome


def test_change_lock_refuses_to_upgrade_a_held_shared_lock(tmp_path: Path) -> None:
    from symphony.pm.documents import _change_lock

    with _change_lock(tmp_path, shared=True):
        with _change_lock(tmp_path, shared=True):
            pass
        with pytest.raises(RuntimeError):
            with _change_lock(tmp_path):
                pass
    with _change_lock(tmp_path):
        with _change_lock(tmp_path, shared=True):
            pass


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


def _record_projection_approval(repo, change, tmp_path, *, approval_id="projection-approval", **overrides):
    manifest = tmp_path / f"{approval_id}.json"
    manifest.write_text("{}\n", encoding="utf-8")
    payload = dict(
        approval_id=approval_id, project_id=change.project_id, change_id=change.change_id,
        scope_hash=change.scope_hash, revision_hash=change.revision,
        manifest_ref=str(manifest), reviewer_label="reviewer", approved_at="2026-10-11T00:00:00Z",
    )
    repo.registry.record_pm_approval(**{**payload, **overrides})


@pytest.mark.parametrize("case", ["missing", "scope", "revision", "project", "change", "superseded", "active-stale"])
def test_approval_projection_ignores_noncurrent_records(tmp_path: Path, case: str) -> None:
    repo, change_id, _ = _repository(tmp_path)
    change = repo.create_change(change_id=change_id, locator="projection", title="Projection", owner="owner")
    overrides = {
        "scope": {"scope_hash": "sha256:stale-scope"},
        "revision": {"revision_hash": "sha256:stale-revision"},
        "project": {"project_id": "other-project"},
        "change": {"change_id": "other-change"},
    }
    if case != "missing":
        _record_projection_approval(repo, change, tmp_path, **overrides.get(case, {}))
    if case == "superseded":
        repo.registry._connect().execute(
            "UPDATE pm_approvals SET superseded_by = ? WHERE approval_id = ?",
            ("replacement", "projection-approval"),
        )
        repo.registry._connect().commit()
    elif case == "active-stale":
        _record_projection_approval(repo, change, tmp_path, approval_id="active-stale", revision_hash="sha256:stale")
        active = repo.registry.get_active_pm_approval("p1", change_id)
        assert active is not None and active["approval_id"] == "active-stale"
    current = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
    assert (current.state, current.approval_id) == (change.state, None)
    assert (current.revision, current.scope_hash) == (change.revision, change.scope_hash)


def test_archived_approval_projection_is_immutable_and_readonly(tmp_path: Path, monkeypatch) -> None:
    import symphony.pm.documents as documents_module

    repo, change_id, workspace = _repository(tmp_path)
    change = repo.create_change(change_id=change_id, locator="projection-history", title="History", owner="owner")
    _record_projection_approval(repo, change, tmp_path)
    active = workspace / "openspec/changes/projection-history"
    archived = workspace / "openspec/changes/archive/2026-10-11-projection-history"
    archived.parent.mkdir(parents=True)
    active.rename(archived)
    assert repo.registry.set_pm_workspace_state("p1", change_id, fence=1, state="RELEASED")
    before = {path.relative_to(archived): path.read_bytes() for path in archived.rglob("*") if path.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("Archived reads must not lock history or look up active approval")

    monkeypatch.setattr(documents_module, "_change_lock", forbidden)
    monkeypatch.setattr(repo.registry, "get_active_pm_approval", forbidden)
    history = repo.get_change(PmRequestContext("p1", change_id), owner="owner", locked=True)
    assert (history.state, history.approval_id) == ("Archived", None)
    assert history.next_action == "history" and all(not doc.editable for doc in history.documents)
    assert {path.relative_to(archived): path.read_bytes() for path in archived.rglob("*") if path.is_file()} == before


def test_R32_03_git_status_does_not_refresh_index(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("same bytes\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    index = tmp_path / ".git/index"
    before = index.stat().st_mtime_ns
    __import__("time").sleep(.02)
    tracked.touch()
    assert __import__("symphony.pm.documents", fromlist=["_git"])._git(tmp_path, "status", "--porcelain=v1", "--untracked-files=all") == "A  tracked.txt"
    after = index.stat().st_mtime_ns
    print("INDEX_MTIME", before, after)
    assert after == before, "read-only Git status wrote the index (optional locking enabled)"


def test_R32_05_atomic_replacement_preserves_existing_mode(tmp_path):
    path = tmp_path / "proposal.md"
    path.write_text("old")
    path.chmod(0o644)
    __import__("symphony.pm.documents", fromlist=["_atomic_write"])._atomic_write(path, "new")
    actual = path.stat().st_mode & 0o777
    print("REPLACEMENT_MODE", oct(actual))
    assert actual == 0o644



def test_snapshot_holds_stable_authority_until_all_files_are_read(tmp_path, monkeypatch):
    import threading
    import symphony.pm.documents as documents
    from symphony.pm.locks import change_authority_lock
    from symphony.pm.routes import _read_change_snapshot

    repo, change_id, workspace = _repository(tmp_path)
    repo.create_change(change_id=change_id, locator="archive-race", title="Archive race", owner="owner")
    resolved, resume, archived = threading.Event(), threading.Event(), threading.Event()
    original = documents._resolve_change_root
    outcomes = {}

    def pause_reader(*args, **kwargs):
        result = original(*args, **kwargs)
        if threading.current_thread().name == "snapshot-reader":
            resolved.set()
            assert resume.wait(2)
        return result

    def read():
        try:
            outcomes["view"] = _read_change_snapshot(repo, PmRequestContext("p1", change_id), "owner")
        except BaseException as exc:
            outcomes["error"] = exc

    def archive():
        registry = RunRegistry(repo.registry.path)
        try:
            with change_authority_lock(registry, "p1", change_id):
                root = workspace / "openspec/changes/archive-race"
                target = root.parent / "archive/2026-10-14-archive-race"
                target.parent.mkdir()
                root.rename(target)
                assert registry.set_pm_workspace_state("p1", change_id, fence=1, state="RELEASED")
                archived.set()
        except BaseException as exc:
            outcomes["archive_error"] = exc
        finally:
            registry.close()

    monkeypatch.setattr(documents, "_resolve_change_root", pause_reader)
    reader = threading.Thread(target=read, name="snapshot-reader")
    writer = threading.Thread(target=archive)
    reader.start()
    assert resolved.wait(2)
    writer.start()
    try:
        assert not archived.wait(.1), "archive moved a change while snapshot held its resolved root"
    finally:
        resume.set()
        reader.join(2)
        writer.join(2)
    assert not reader.is_alive() and not writer.is_alive()
    assert "error" not in outcomes and "archive_error" not in outcomes, outcomes
    assert outcomes["view"].state == "Draft"
    assert repo.get_change(PmRequestContext("p1", change_id), owner="owner").state == "Archived"
    repo.registry.close()


def test_atomic_write_keeps_new_files_private_and_failure_preserves_old_bytes_and_mode(tmp_path, monkeypatch):
    import symphony.pm.documents as documents

    path = tmp_path / "proposal.md"
    documents._atomic_write(path, "private")
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    def fail_replace(*_args):
        raise OSError("injected replace failure")
    monkeypatch.setattr(documents.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace"):
        documents._atomic_write(path, "replacement")
    assert path.read_text() == "private"
    assert path.stat().st_mode & 0o777 == 0o644
    assert sorted(item.name for item in tmp_path.iterdir()) == ["proposal.md"]


def test_authority_lock_reentry_is_same_thread_only_and_shared_upgrade_is_refused(tmp_path):
    import threading
    from symphony.pm.locks import change_authority_lock

    registry = RunRegistry(tmp_path / "state.db")
    entered = threading.Event()
    started = threading.Event()
    def competitor():
        other = RunRegistry(registry.path)
        try:
            started.set()
            with change_authority_lock(other, "p1", "c1"):
                entered.set()
        finally:
            other.close()
    with change_authority_lock(registry, "p1", "c1"):
        with change_authority_lock(registry, "p1", "c1"):
            with change_authority_lock(registry, "p1", "c1", shared=True):
                pass
        thread = threading.Thread(target=competitor)
        thread.start()
        assert started.wait(2) and not entered.wait(.05)
    thread.join(2)
    assert entered.is_set() and not thread.is_alive()
    with change_authority_lock(registry, "p1", "c1", shared=True):
        with pytest.raises(RuntimeError, match="cannot be upgraded"):
            with change_authority_lock(registry, "p1", "c1"):
                pass
    registry.close()


@pytest.mark.parametrize("fault", [None, "after_document", "after_identity"])
def test_semantic_edit_and_journal_recovery_preserve_document_and_identity_modes(tmp_path, monkeypatch, fault):
    repo, change_id, workspace = _repository(tmp_path)
    created = repo.create_change(change_id=change_id, locator="semantic-modes", title="Semantic modes", owner="owner")
    root = workspace / "openspec/changes/semantic-modes"
    document = root / "ux.md"
    identity = root / "symphony.yaml"
    document.chmod(0o644)
    identity.chmod(0o640)
    context = PmRequestContext("p1", change_id, created.revision, "semantic-mode-write")
    operation = ({"op": "create_semantic", "kind": "storyboard", "title": "Mode preserving frame"},)
    try:
        if fault:
            monkeypatch.setenv("SYMPHONY_PM_SEMANTIC_FAULT", fault)
            with pytest.raises(RuntimeError, match="semantic fault"):
                repo.patch_document(context, "ux", operation, owner="owner")
            monkeypatch.delenv("SYMPHONY_PM_SEMANTIC_FAULT")
        else:
            repo.patch_document(context, "ux", operation, owner="owner")
        recovered = repo.get_change(PmRequestContext("p1", change_id), owner="owner")
        assert "Mode preserving frame" in next(item.source for item in recovered.documents if item.document_id == "ux")
        assert document.stat().st_mode & 0o777 == 0o644
        assert identity.stat().st_mode & 0o777 == 0o640
        assert not list(root.glob(".symphony-semantic-*"))
    finally:
        repo.registry.close()
