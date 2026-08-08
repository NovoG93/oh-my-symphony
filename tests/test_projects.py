from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from symphony.cli import project as project_cli
from symphony.projects import Project, ProjectError, ProjectRegistry


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def init_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    (path / "WORKFLOW.md").write_text("---\ntracker: {kind: file}\n---\n", encoding="utf-8")
    return path


def source_bundle(path: Path) -> Path:
    init_repo(path)
    for name in ("tui-open.sh", "tui-open.bat", "AGENTS.md", "GEMINI.md"):
        (path / name).write_text(name + "\n", encoding="utf-8")
    (path / "WORKFLOW.file.example.md").write_text("workflow\n", encoding="utf-8")
    (path / "scripts").mkdir()
    (path / "scripts/symphony-setup-worktree.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (path / "docs/symphony-prompts").mkdir(parents=True)
    (path / "docs/symphony-prompts/base.md").write_text("prompt\n", encoding="utf-8")
    (path / "skills/demo").mkdir(parents=True)
    (path / "skills/demo/SKILL.md").write_text("skill\n", encoding="utf-8")
    return path


def project(repo: Path, *, id: str = "one", port: int = 9999) -> Project:
    return Project(id, id.title(), str(repo), str(repo / "WORKFLOW.md"), "127.0.0.1", port)


def test_registry_json_v1_uses_environment_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state" / "projects.json"
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(path))
    repo = init_repo(tmp_path / "repo")
    registry = ProjectRegistry()
    registry.add(project(repo))

    assert registry.load() == [project(repo)]
    assert json.loads(path.read_text()) == {"version": 1, "projects": [{
        "id": "one", "name": "One", "git_repo": str(repo),
        "workflow": str(repo / "WORKFLOW.md"), "host": "127.0.0.1", "port": 9999,
    }]}


@pytest.mark.parametrize("collision", ["id", "repo", "port"])
def test_registry_rejects_duplicate_identity_repo_or_port(tmp_path: Path, collision: str) -> None:
    first_repo = init_repo(tmp_path / "one")
    second_repo = init_repo(tmp_path / "two")
    registry = ProjectRegistry(tmp_path / "projects.json")
    registry.add(project(first_repo))
    duplicate = Project(
        "one" if collision == "id" else "two",
        "Two",
        str(first_repo if collision == "repo" else second_repo),
        str(second_repo / "WORKFLOW.md"),
        "0.0.0.0",
        9999 if collision == "port" else 10000,
    )
    with pytest.raises(ProjectError):
        registry.add(duplicate)


def test_add_resolves_git_top_level_and_rejects_source_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = init_repo(tmp_path / "repo")
    nested = repo / "nested"
    nested.mkdir()
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "projects.json"))
    source = init_repo(tmp_path / "source")
    monkeypatch.setattr(project_cli, "source_checkout", lambda: source)

    assert project_cli.main(["add", str(nested), "--id", "app"]) == 0
    assert ProjectRegistry().get("app").git_repo == str(repo.resolve())

    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "other.json"))
    assert project_cli.main(["add", str(source), "--id", "source"]) == 1
    assert "protected Symphony source checkout" in capsys.readouterr().err


def test_create_bootstraps_sibling_repo_and_initial_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = source_bundle(tmp_path / "symphony-source")
    registry_path = tmp_path / "projects.json"
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(registry_path))
    monkeypatch.setattr(project_cli, "source_checkout", lambda: source)

    assert project_cli.main(["create", "Demo App", "--id", "demo", "--port", "10010"]) == 0
    target = tmp_path / "demo"
    record = ProjectRegistry().get("demo")
    assert Path(record.git_repo) == target
    assert (target / "WORKFLOW.md").read_text() == "workflow\n"
    assert (target / "docs/symphony-prompts/base.md").is_file()
    assert (target / "skills/demo/SKILL.md").is_file()
    assert (target / "scripts/symphony-setup-worktree.sh").is_file()
    assert (target / "kanban/.gitkeep").is_file()
    assert git(target, "branch", "--show-current") == "main"
    assert git(target, "rev-list", "--count", "HEAD") == "1"
    assert not git(target, "status", "--porcelain")


def test_lifecycle_commands_delegate_to_service_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = init_repo(tmp_path / "repo")
    registry_path = tmp_path / "projects.json"
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(registry_path))
    ProjectRegistry().add(project(repo, id="app", port=10001))
    calls: list[list[str]] = []
    monkeypatch.setattr(project_cli.service, "main", lambda argv: calls.append(argv) or 0)

    assert project_cli.main(["start", "app", "--replace"]) == 0
    assert project_cli.main(["stop", "app", "--force"]) == 0
    assert project_cli.main(["status", "app"]) == 0
    assert calls == [
        ["start", str(repo / "WORKFLOW.md"), "--host", "127.0.0.1", "--port", "10001", "--replace"],
        ["stop", str(repo / "WORKFLOW.md"), "--timeout", "10.0", "--force"],
        ["status", str(repo / "WORKFLOW.md"), "--port", "10001"],
    ]
