from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from symphony.cli.doctor import check_source_repository
from symphony.errors import ProtectedSourceRepository
from symphony.orchestrator import Orchestrator
from symphony.service import main as service_main
from symphony.runtime_safety import (
    protected_source_common_dir,
    workflow_uses_protected_source_repo,
)
from symphony.utils.git_sandbox import resolve_git_common_dir
from symphony.workflow import WorkflowState, build_service_config, load_workflow


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "seed.txt").write_text("seed", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "seed")
    return path


def _write_workflow(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "kanban").mkdir(exist_ok=True)
    workflow = repo / "WORKFLOW.md"
    workflow.write_text(
        "---\n"
        "tracker: {kind: file, board_root: ./kanban}\n"
        "workspace: {root: ./workspaces}\n"
        "hooks: {after_create: ':', before_run: ':', after_run: ':'}\n"
        "agent: {kind: codex}\n"
        "codex: {command: python -m symphony.mock_codex}\n"
        "---\nBody\n",
        encoding="utf-8",
    )
    return workflow


def test_source_checkout_is_identified_by_its_git_common_dir():
    expected = resolve_git_common_dir(Path(__file__).resolve().parents[1])
    assert expected is not None
    assert protected_source_common_dir() == expected


def test_protection_covers_linked_worktrees(monkeypatch, tmp_path: Path):
    source = _init_repo(tmp_path / "source")
    linked = tmp_path / "linked"
    _git(source, "worktree", "add", "-b", "preview", str(linked))
    workflow = _write_workflow(linked)
    monkeypatch.setattr(
        "symphony.runtime_safety.protected_source_common_dir",
        lambda: resolve_git_common_dir(source),
    )

    assert workflow_uses_protected_source_repo(workflow) is True


def test_doctor_rejects_protected_repo_but_allows_other_repo(
    monkeypatch, tmp_path: Path
):
    source = _init_repo(tmp_path / "source")
    project = _init_repo(tmp_path / "project")
    source_workflow = _write_workflow(source)
    project_workflow = _write_workflow(project)
    monkeypatch.setattr(
        "symphony.runtime_safety.protected_source_common_dir",
        lambda: resolve_git_common_dir(source),
    )

    protected_cfg = build_service_config(load_workflow(source_workflow))
    protected = check_source_repository(protected_cfg)
    assert protected.status == "fail"
    assert "symphony project create" in protected.message
    assert "symphony project add" in protected.message
    assert "hub" in protected.message

    project_cfg = build_service_config(load_workflow(project_workflow))
    assert check_source_repository(project_cfg).status == "pass"


@pytest.mark.asyncio
async def test_orchestrator_start_refuses_protected_repo_even_without_doctor(
    monkeypatch, tmp_path: Path
):
    source = _init_repo(tmp_path / "source")
    workflow = _write_workflow(source)
    monkeypatch.setattr(
        "symphony.runtime_safety.protected_source_common_dir",
        lambda: resolve_git_common_dir(source),
    )
    state = WorkflowState(workflow)

    with pytest.raises(ProtectedSourceRepository, match="project create"):
        await Orchestrator(state).start()


def test_non_git_workflow_is_not_treated_as_source(monkeypatch, tmp_path: Path):
    source = _init_repo(tmp_path / "source")
    workflow = _write_workflow(tmp_path / "plain")
    monkeypatch.setattr(
        "symphony.runtime_safety.protected_source_common_dir",
        lambda: resolve_git_common_dir(source),
    )

    assert workflow_uses_protected_source_repo(workflow) is False


def test_service_skip_doctor_cannot_bypass_source_protection(
    monkeypatch, tmp_path: Path, capsys
):
    source = _init_repo(tmp_path / "source")
    workflow = _write_workflow(source)
    monkeypatch.setattr(
        "symphony.runtime_safety.protected_source_common_dir",
        lambda: resolve_git_common_dir(source),
    )

    assert service_main(["start", str(workflow), "--skip-doctor"]) == 1
    error = capsys.readouterr().err
    assert "unsafe workflow repository" in error
    assert "symphony project create" in error
