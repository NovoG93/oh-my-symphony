"""`symphony project add|create` auto-install the orchestrator unit (default ON)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from symphony import systemd as systemd_module
from symphony.cli import project as project_cli


def _ok() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, "", "")


def init_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(path)], check=True, capture_output=True
    )
    (path / "WORKFLOW.md").write_text(
        "---\ntracker: {kind: file}\n---\n", encoding="utf-8"
    )
    return path


def source_bundle(path: Path) -> Path:
    init_repo(path)
    for name in ("tui-open.sh", "tui-open.bat", "AGENTS.md", "GEMINI.md"):
        (path / name).write_text(name + "\n", encoding="utf-8")
    (path / "WORKFLOW.file.example.md").write_text("workflow\n", encoding="utf-8")
    (path / "scripts").mkdir()
    (path / "scripts/symphony-setup-worktree.sh").write_text(
        "#!/bin/sh\n", encoding="utf-8"
    )
    (path / "docs/symphony-prompts").mkdir(parents=True)
    (path / "docs/symphony-prompts/base.md").write_text("prompt\n", encoding="utf-8")
    (path / "skills/demo").mkdir(parents=True)
    (path / "skills/demo/SKILL.md").write_text("skill\n", encoding="utf-8")
    return path


def _prepare(monkeypatch, tmp_path: Path, *, source: Path) -> Path:
    units = tmp_path / "units"
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "projects.json"))
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(units))
    monkeypatch.setattr(project_cli, "source_checkout", lambda: source)
    monkeypatch.setattr(systemd_module, "is_available", lambda: True)
    monkeypatch.setattr(systemd_module, "run_systemctl", lambda *a, **k: _ok())
    return units


def test_create_installs_unit_by_default(monkeypatch, tmp_path: Path, capsys) -> None:
    source = source_bundle(tmp_path / "symphony-source")
    units = _prepare(monkeypatch, tmp_path, source=source)

    rc = project_cli.main(["create", "Demo App", "--id", "demo", "--port", "10010"])

    captured = capsys.readouterr()
    assert rc == 0
    unit = units / "symphony-demo.service"
    assert unit.exists()
    assert "service unit: installed" in captured.out
    assert "--port 10010" in unit.read_text()


def test_create_no_service_flag_skips_install(monkeypatch, tmp_path: Path, capsys) -> None:
    source = source_bundle(tmp_path / "symphony-source")
    units = _prepare(monkeypatch, tmp_path, source=source)

    rc = project_cli.main(
        ["create", "Demo App", "--id", "demo", "--port", "10010", "--no-service"]
    )

    captured = capsys.readouterr()
    assert rc == 0
    assert not units.exists()
    assert "service unit" not in captured.out


def test_add_installs_unit_for_existing_repo(monkeypatch, tmp_path: Path, capsys) -> None:
    source = source_bundle(tmp_path / "symphony-source")
    repo = init_repo(tmp_path / "alpha")
    units = _prepare(monkeypatch, tmp_path, source=source)

    rc = project_cli.main(["add", str(repo), "--id", "alpha", "--port", "10002"])

    captured = capsys.readouterr()
    assert rc == 0
    unit = units / "symphony-alpha.service"
    assert unit.exists()
    assert "service unit: installed" in captured.out
    assert f"{repo.resolve() / 'WORKFLOW.md'}" in unit.read_text()
