from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from symphony.orchestrator.run_registry import RunRegistry
from symphony.pm import demo, openspec
from symphony.pm.approval import PmApprovalService
from symphony.pm.documents import PmDocumentRepository
from symphony.pm.models import PmError, PmRequestContext
from symphony.pm.routes import _openspec_readiness
from symphony.workflow import WorkflowState
from tests._pm_support import fake_node, skip_or_fail


@pytest.fixture(autouse=True)
def environment_unchanged():
    before = dict(os.environ)
    before.pop("PYTEST_CURRENT_TEST", None)
    yield
    after = dict(os.environ)
    after.pop("PYTEST_CURRENT_TEST", None)
    assert after == before


@pytest.fixture(autouse=True)
def clean_environment(environment_unchanged, tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key in {
            "SYMPHONY_LANG",
            "SYMPHONY_API_TOKEN",
            "SYMPHONY_TRUSTED_ORIGINS",
            "SYMPHONY_SERVICE_INSTANCE_ID",
            "GIT_CONFIG_GLOBAL",
        } or key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_")):
            monkeypatch.delenv(key)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "unused-projects.json"))
    monkeypatch.setattr(openspec, "_NODE_CACHE", {})


@pytest.fixture
def supported_node():
    try:
        openspec.resolve_node()
    except PmError as exc:
        skip_or_fail(exc.message, flag="SYMPHONY_PM_NODE_REQUIRED")


def _create(tmp_path, lang="en", name="demo"):
    inputs = demo.preflight_demo(tmp_path / name, lang)
    directory = demo.claim_demo_directory(inputs)
    return demo.create_demo(directory, inputs.lang)


@contextmanager
def _repository(workspace):
    project = workspace.directory / "project"
    registry = RunRegistry(project / ".symphony" / "state.db")
    try:
        repository = PmDocumentRepository(
            project_id=demo.PROJECT_ID,
            project_root=project,
            registry=registry,
            planning_workspace_root=workspace.planning_path,
        )
        yield repository, registry
    finally:
        registry.close()


def _git(project, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(project), *args], text=True, capture_output=True, check=check
    )


@pytest.mark.parametrize("lang", ["xx", "ko-KR", "EN", ""])
def test_bad_language_refuses_before_tools_or_writes(tmp_path, monkeypatch, lang):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(
        demo.tempfile,
        "mkdtemp",
        lambda **kwargs: pytest.fail("temp folder created during preflight"),
    )
    target = tmp_path / "demo"
    with pytest.raises(demo.DemoError, match="--lang must be en or ko") as error:
        demo.preflight_demo(target, lang)
    assert error.value.exit_code == 2
    assert not target.exists()


def test_missing_git_refuses_before_any_creation(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(
        demo.tempfile,
        "mkdtemp",
        lambda **kwargs: pytest.fail("temp folder created during preflight"),
    )
    with pytest.raises(demo.DemoError, match="git on PATH") as error:
        demo.preflight_demo()
    assert error.value.exit_code == 2
    assert list(tmp_path.iterdir()) == [tmp_path / "home"]


@pytest.mark.parametrize("version", [None, "v20.17.0"])
def test_missing_or_old_node_refuses_without_a_folder(tmp_path, monkeypatch, version):
    tools = tmp_path / "bin"
    tools.mkdir()
    git = shutil.which("git")
    assert git is not None
    (tools / "git").symlink_to(git)
    if version is not None:
        fake_node(tools, version)
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setattr(
        demo.tempfile,
        "mkdtemp",
        lambda **kwargs: pytest.fail("temp folder created during preflight"),
    )
    with pytest.raises(demo.DemoError, match="Node.js 20.19.0 or newer") as error:
        demo.preflight_demo(tmp_path / "demo")
    assert error.value.exit_code == 2
    assert not (tmp_path / "demo").exists()
    assert openspec._NODE_CACHE == {}


@pytest.mark.parametrize("kind", ["file", "nonempty", "protected", "symlink"])
def test_path_refusals_leave_original_bytes(
    tmp_path, monkeypatch, supported_node, kind
):
    target = tmp_path / "target"
    if kind == "file":
        target.write_text("keep me\n", encoding="utf-8")
    elif kind == "nonempty":
        target.mkdir()
        (target / "original.txt").write_text("keep me\n", encoding="utf-8")
    else:
        source = tmp_path / "source"
        source.mkdir()
        (source / ".git").mkdir()
        monkeypatch.setattr(
            demo.runtime_safety, "protected_source_common_dir", lambda: source / ".git"
        )
        if kind == "symlink":
            target.symlink_to(source, target_is_directory=True)
            target = target / "new-demo"
        else:
            target = source / "new-demo"
    before = {
        str(p.relative_to(tmp_path)): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    reason = {"file": "not a directory", "nonempty": "not empty"}.get(
        kind, "protected Symphony source checkout"
    )
    with pytest.raises(demo.DemoError, match=reason) as error:
        demo.preflight_demo(target)
    assert error.value.exit_code == 2
    assert {
        str(p.relative_to(tmp_path)): p.read_bytes()
        for p in tmp_path.rglob("*")
        if p.is_file()
    } == before
    assert not (target / "project").exists()


def test_linked_protected_worktree_is_refused(tmp_path, monkeypatch, supported_node):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "--template=", "-q")
    _git(
        source,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--allow-empty",
        "-qm",
        "fixture",
    )
    worktree = tmp_path / "worktree"
    _git(source, "worktree", "add", "-qb", "linked", str(worktree))
    monkeypatch.setattr(
        demo.runtime_safety, "protected_source_common_dir", lambda: source / ".git"
    )
    target = worktree / "demo"
    with pytest.raises(
        demo.DemoError, match="protected Symphony source checkout"
    ) as error:
        demo.preflight_demo(target)
    assert error.value.exit_code == 2
    assert not target.exists()


def test_preflight_language_precedence_and_no_temp_until_claim(
    tmp_path, monkeypatch, supported_node
):
    monkeypatch.setattr(
        demo.tempfile,
        "mkdtemp",
        lambda **kwargs: pytest.fail("temp folder created during preflight"),
    )
    assert demo.preflight_demo().lang == "en"
    monkeypatch.setenv("SYMPHONY_LANG", "ko")
    assert demo.preflight_demo().lang == "ko"
    assert demo.preflight_demo(lang="en").lang == "en"
    target = tmp_path / "nested" / "demo"
    result = demo.preflight_demo(target)
    assert result.path == target
    assert not target.exists()


def test_default_claim_uses_temp_prefix_only_at_claim(
    tmp_path, monkeypatch, supported_node
):
    calls = []
    target = tmp_path / "temporary"

    def mkdtemp(*, prefix):
        calls.append(prefix)
        target.mkdir()
        return str(target)

    monkeypatch.setattr(demo.tempfile, "mkdtemp", mkdtemp)
    inputs = demo.preflight_demo()
    assert calls == []
    assert demo.claim_demo_directory(inputs) == target
    assert calls == ["symphony-pm-demo-"]
    assert list(target.iterdir()) == [target / "project"]


def test_claim_race_exactly_one_winner(tmp_path, monkeypatch, supported_node):
    target = tmp_path / "demo"
    target.mkdir()
    inputs = demo.preflight_demo(target)
    barrier = threading.Barrier(2)
    original = os.mkdir

    def competing_mkdir(path, *args, **kwargs):
        if Path(path) == target / "project":
            barrier.wait(timeout=5)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(demo.os, "mkdir", competing_mkdir)

    def claim():
        try:
            return demo.claim_demo_directory(inputs)
        except demo.DemoError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sum(isinstance(result, Path) for result in results) == 1
    errors = [result for result in results if isinstance(result, demo.DemoError)]
    assert len(errors) == 1 and errors[0].exit_code == 2
    assert list(target.iterdir()) == [target / "project"]
    assert list((target / "project").iterdir()) == []


def test_claim_rechecks_nonempty_path_after_preflight(tmp_path, supported_node):
    target = tmp_path / "demo"
    inputs = demo.preflight_demo(target)
    target.mkdir()
    (target / "keep.txt").write_text("keep\n", encoding="utf-8")
    with pytest.raises(demo.DemoError, match="not empty") as error:
        demo.claim_demo_directory(inputs)
    assert error.value.exit_code == 2
    assert list(target.iterdir()) == [target / "keep.txt"]
    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def _approve(workspace, repository, registry):
    cfg, error = WorkflowState(workspace.workflow_path).reload()
    assert cfg is not None and error is None
    calls = []
    adapter = openspec.OpenSpecAdapter()

    def validator(root, locator):
        calls.append((root, locator))
        return _openspec_readiness(adapter, root, locator)

    service = PmApprovalService(
        repository,
        registry,
        external_validator=validator,
        verification_procedures=cfg.pm.verification_procedures,
        manual_verification_procedures=cfg.pm.manual_verification_procedures,
    )
    change = repository.get_change(
        PmRequestContext(demo.PROJECT_ID, workspace.change_id), owner=demo.OWNER
    )
    ctx = PmRequestContext(demo.PROJECT_ID, workspace.change_id, change.revision)
    result = service.approve(
        ctx,
        service.confirmation_token(ctx, owner=demo.OWNER),
        "Demo reviewer",
        owner=demo.OWNER,
    )
    assert calls == [(workspace.planning_path, demo.LOCATOR)]
    assert result.change_id == workspace.change_id
    assert (
        _git(workspace.planning_path, "log", "-1", "--format=%an <%ae>").stdout.strip()
        == "Symphony PM Demo <pm-demo@example.invalid>"
    )
    assert _git(workspace.planning_path, "log", "-1", "--format=%s").stdout.startswith(
        "Approve PM plan team-leave-request ("
    )


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_exact_demo_content_combined_validation_and_real_approval(
    tmp_path, supported_node, lang
):
    workspace = _create(tmp_path, lang)
    assert workspace.validation.valid
    assert workspace.validation.source == "combined"
    assert workspace.validation.findings == ()
    with _repository(workspace) as (repository, registry):
        changes = repository.list_changes(owner=demo.OWNER)
        assert len(changes) == 1
        change = changes[0]
        assert change.locator == demo.LOCATOR and change.state == "Validated"
        assert change.approval_id is None
        assert set(change.affected_capabilities) == {"requirements", "team-calendar"}
        items = change.semantic_index
        assert {
            kind: sum(item.kind == kind for item in items)
            for kind in (
                "decision",
                "requirement",
                "scenario",
                "storyboard",
                "contract",
            )
        } == {
            "decision": 2,
            "requirement": 3,
            "scenario": 5,
            "storyboard": 3,
            "contract": 2,
        }
        docs = {document.document_id: document.source for document in change.documents}
        assert set(docs) == {
            "proposal",
            "ux",
            "design",
            "tasks",
            "spec:requirements",
            "spec:team-calendar",
        }
        assert len(re.findall(r"^## ", docs["proposal"], re.M)) == 10
        assert (
            "- Status: Accepted" in docs["proposal"]
            and "- Status: Unresolved" in docs["proposal"]
        )
        assert (
            docs["design"].count("flowchart ") == 1
            and docs["design"].count("erDiagram") == 1
        )
        assert (
            "POST /api/leave-requests" in docs["design"]
            and "POST /api/leave-requests/{id}/decision" in docs["design"]
        )
        assert len(change.storyboard_frames) == 3
        assert all(
            frame.requirement_refs and frame.design_refs
            for frame in change.storyboard_frames
        )
        for document_id in ("spec:requirements", "spec:team-calendar"):
            assert "## ADDED Requirements" in docs[document_id]
            for block in docs[document_id].split("### Requirement:")[1:]:
                assert "#### Scenario:" in block
                assert re.search(r"\b(?:SHALL|MUST)\b", block)
        tasks = repository.get_wbs(
            PmRequestContext(demo.PROJECT_ID, change.change_id), owner=demo.OWNER
        ).tasks
        assert sum(not task.executable for task in tasks) == 2
        leaves = [task for task in tasks if task.executable]
        assert len(leaves) == 5
        dependencies = [
            dependency for task in leaves for dependency in task.dependencies
        ]
        assert (
            sum(dependency.kind == "requires_code" for dependency in dependencies) == 4
        )
        assert (
            sum(
                dependency.kind == "order_only" and bool(dependency.justification)
                for dependency in dependencies
            )
            == 1
        )
        for task in leaves:
            assert task.verification_policy is not None
            assert [
                (selection.procedure_id, selection.kind)
                for selection in task.verification_policy
            ] == (
                [("demo-check", "native"), ("demo-review", "manual")]
                if task.kind == "Frontend"
                else [("demo-check", "native")]
            )
        assert all("{{ID:" not in document.source for document in change.documents)
        assert set(document.path for document in change.documents) == {
            "proposal.md",
            "ux.md",
            "design.md",
            "tasks.md",
            "specs/requirements/spec.md",
            "specs/team-calendar/spec.md",
        }
        assert ("직원" in docs["proposal"]) == (lang == "ko")
        _approve(workspace, repository, registry)
    assert list((tmp_path / "home").iterdir()) == []
    assert list((workspace.directory / "project" / "kanban").iterdir()) == []
    assert not (workspace.directory / "projects.json").exists()


def test_hostile_git_config_and_empty_home_cannot_run_hooks_or_sign(
    tmp_path, monkeypatch, supported_node
):
    marker = tmp_path / "marker"
    template = tmp_path / "tpl"
    template_hooks = template / "hooks"
    global_hooks = tmp_path / "ghooks"
    for root in (template_hooks, global_hooks):
        root.mkdir(parents=True)
        for name in ("pre-commit", "post-checkout"):
            script = root / name
            script.write_text(
                f"#!/bin/sh\nprintf '%s\\n' '{name}' >> '{marker}'\nexit 1\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
    global_config = tmp_path / "hostile.gitconfig"
    for key, value in (
        ("init.templateDir", str(template)),
        ("core.hooksPath", str(global_hooks)),
        ("commit.gpgsign", "true"),
        ("tag.gpgsign", "true"),
        ("gpg.program", "/usr/bin/false"),
    ):
        subprocess.run(
            ["git", "config", "--file", str(global_config), key, value], check=True
        )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    original_config = global_config.read_bytes()
    workspace = _create(tmp_path)
    project = workspace.directory / "project"
    assert list((project / ".git" / "hooks").glob("*")) == []
    assert list((workspace.directory / "git-hooks").iterdir()) == []
    assert _git(project, "config", "--local", "core.hooksPath").stdout.strip() == str(
        workspace.directory / "git-hooks"
    )
    for setting in ("commit.gpgsign", "tag.gpgsign"):
        assert _git(project, "config", "--local", setting).stdout.strip() == "false"
    with _repository(workspace) as (repository, registry):
        _approve(workspace, repository, registry)
    assert not marker.exists()
    assert global_config.read_bytes() == original_config
    assert list((tmp_path / "home").iterdir()) == []
    # The same hostile configuration is effective without demo-local overrides.
    control = tmp_path / "control"
    control.mkdir()
    _git(control, "init", "-q")
    result = _git(
        control,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--allow-empty",
        "-qm",
        "control",
        check=False,
    )
    assert result.returncode != 0
    assert marker.read_text(encoding="utf-8") == "pre-commit\n"


@pytest.mark.skipif(
    sys.platform == "win32", reason="NTFS forbids ':' in demo folder names"
)
def test_special_character_workflow_paths_reload_and_validate(tmp_path, supported_node):
    workspace = _create(tmp_path, "ko", "demo dir: #1 \\ x")
    config, error = WorkflowState(workspace.workflow_path).reload()
    assert config is not None and error is None
    assert config.workspace_root == workspace.directory / "worker-workspaces"
    assert config.pm.planning_workspace_root == workspace.planning_path
    assert config.tracker.board_root == workspace.directory / "project" / "kanban"
    assert config.tui.language == "ko"
    assert config.agent.kind == "codex"
    assert config.agent.auto_merge_on_done is False
    assert config.agent.auto_merge_push_target is False
    assert config.pm.assistance is False
    assert config.pm.openspec_executable is None
    assert "-m symphony.mock_codex" in config.codex.command
    assert workspace.validation.valid and workspace.validation.findings == ()
    demo.check_demo_paths(config, workspace.directory)


@pytest.mark.parametrize(
    "key", ["tracker.board_root", "workspace.root", "pm.planning_workspace_root"]
)
def test_demo_config_outside_is_refused_and_files_kept(tmp_path, supported_node, key):
    workspace = _create(tmp_path)
    state = WorkflowState(workspace.workflow_path)
    config, error = state.reload()
    assert config is not None and error is None
    outside = tmp_path / "outside"
    if key == "tracker.board_root":
        config = replace(config, tracker=replace(config.tracker, board_root=outside))
    elif key == "workspace.root":
        config = replace(config, workspace_root=outside)
    else:
        # Exercise a tampered WORKFLOW through the same reload as the CLI.
        source = workspace.workflow_path.read_text(encoding="utf-8")
        workspace.workflow_path.write_text(
            source.replace(str(workspace.planning_path), str(outside)), encoding="utf-8"
        )
        config, error = state.reload()
        assert error is None
    with pytest.raises(demo.DemoError, match="demo_config_outside") as refusal:
        demo.check_demo_paths(config, workspace.directory)
    assert refusal.value.exit_code == 1
    assert (
        str(refusal.value)
        == f"demo_config_outside: the demo configuration points outside the demo folder ({key} = {outside})"
    )
    assert workspace.workflow_path.exists()
    assert workspace.planning_path.exists()
    assert not outside.exists()


def test_missing_placeholder_target_is_explicit_and_keeps_partial_demo(
    tmp_path, monkeypatch, supported_node
):
    resources = tmp_path / "resources"
    shutil.copytree(demo.RESOURCE_ROOT / "en", resources / "en")
    design = resources / "en" / "design.md"
    design.write_text(
        design.read_text(encoding="utf-8").replace(
            "<!-- symphony:contract={{ID:submit_leave}} -->", ""
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(demo, "RESOURCE_ROOT", resources)
    inputs = demo.preflight_demo(tmp_path / "demo")
    directory = demo.claim_demo_directory(inputs)
    with pytest.raises(
        demo.DemoError, match="no semantic target for placeholder submit_leave"
    ) as error:
        demo.create_demo(directory, inputs.lang)
    assert error.value.exit_code == 1
    assert (directory / "project" / "WORKFLOW.md").is_file()
    assert (directory / "planning").is_dir()


def test_korean_without_shall_must_fails_combined_strict_validation(
    tmp_path, monkeypatch, supported_node
):
    resources = tmp_path / "resources"
    shutil.copytree(demo.RESOURCE_ROOT / "ko", resources / "ko")
    requirements = resources / "ko" / "spec-requirements.md"
    requirements.write_text(
        re.sub(r"\b(?:SHALL|MUST)\b", "", requirements.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    monkeypatch.setattr(demo, "RESOURCE_ROOT", resources)
    inputs = demo.preflight_demo(tmp_path / "demo", "ko")
    directory = demo.claim_demo_directory(inputs)
    with pytest.raises(demo.DemoError, match="demo validation failed") as error:
        demo.create_demo(directory, inputs.lang)
    assert error.value.exit_code == 1
    assert any(
        finding.severity == "blocking" and "SHALL" in finding.message
        for finding in error.value.findings
    )
    assert (directory / "project" / "WORKFLOW.md").exists()
    assert (
        directory
        / "planning"
        / "openspec"
        / "changes"
        / demo.LOCATOR
        / "specs"
        / "requirements"
        / "spec.md"
    ).exists()
