"""Unit-naming, rendering, and installation helpers for systemd user units."""

from __future__ import annotations

import subprocess
from pathlib import Path

from symphony import systemd


def test_unit_name_for_slugifies_workflow_dir(tmp_path: Path) -> None:
    wf = tmp_path / "workmate-ai" / "WORKFLOW.md"
    assert systemd.unit_name_for(wf) == "symphony-workmate-ai.service"


def test_unit_name_for_sanitizes_weird_chars(tmp_path: Path) -> None:
    wf = tmp_path / "My Project (v2)" / "WORKFLOW.md"
    assert systemd.unit_name_for(wf) == "symphony-my-project-v2.service"


def test_unit_name_is_stable_for_nested_paths(tmp_path: Path) -> None:
    wf = tmp_path / "a" / "b" / "sync" / "WORKFLOW.md"
    assert systemd.unit_name_for(wf) == "symphony-sync.service"


def test_render_unit_contains_required_directives(tmp_path: Path) -> None:
    wf = tmp_path / "proj" / "WORKFLOW.md"
    text = systemd.render_unit(wf, host="0.0.0.0", port=10000, python="/venv/bin/python")
    assert "[Unit]" in text and "[Service]" in text and "[Install]" in text
    assert "Restart=always" in text
    assert "WantedBy=default.target" in text
    assert f"ExecStart=/venv/bin/python -m symphony.cli {wf.resolve()}" in text
    assert "--host 0.0.0.0 --port 10000" in text
    assert f"WorkingDirectory={wf.resolve().parent}" in text
    assert systemd.UNIT_MARKER in text


def test_render_unit_escapes_spaces_in_paths(tmp_path: Path) -> None:
    wf = tmp_path / "my proj" / "WORKFLOW.md"
    text = systemd.render_unit(wf, host="127.0.0.1", port=1234, python="/p/python")
    assert 'ExecStart=/p/python -m symphony.cli "' in text  # systemd quoting for spaces


def test_unit_dir_honours_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(tmp_path / "units"))
    assert systemd.unit_dir() == (tmp_path / "units")


def test_unit_dir_default_is_user_config(monkeypatch) -> None:
    monkeypatch.delenv("SYMPHONY_SYSTEMD_UNIT_DIR", raising=False)
    assert str(systemd.unit_dir()).endswith("/.config/systemd/user")


def test_is_available_false_when_systemctl_missing(monkeypatch) -> None:
    monkeypatch.setattr(systemd.shutil, "which", lambda _name: None)
    assert systemd.is_available() is False


def _ok() -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, "", "")


def test_ensure_unit_writes_and_enables(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(tmp_path))
    calls: list[list[str]] = []
    monkeypatch.setattr(
        systemd.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _ok()
    )
    result = systemd.ensure_unit(
        tmp_path / "proj" / "WORKFLOW.md", host="0.0.0.0", port=10000, python="/p/python"
    )
    assert result.action == "installed"
    unit = tmp_path / "symphony-proj.service"
    assert unit.exists() and "Restart=always" in unit.read_text()
    assert ["systemctl", "--user", "daemon-reload"] in calls
    assert ["systemctl", "--user", "enable", "symphony-proj.service"] in calls


def test_ensure_unit_is_a_noop_when_unchanged(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(tmp_path))
    monkeypatch.setattr(systemd.subprocess, "run", lambda *a, **k: _ok())
    wf = tmp_path / "proj" / "WORKFLOW.md"
    systemd.ensure_unit(wf, host="0.0.0.0", port=10000, python="/p/python")
    before = (tmp_path / "symphony-proj.service").stat().st_mtime_ns
    result = systemd.ensure_unit(wf, host="0.0.0.0", port=10000, python="/p/python")
    assert result.action == "unchanged"
    assert (tmp_path / "symphony-proj.service").stat().st_mtime_ns == before


def test_find_existing_unit_for_workflow(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(tmp_path))
    wf = tmp_path / "proj" / "WORKFLOW.md"
    (tmp_path / "workmate-orchestrator.service").write_text(
        "[Service]\nExecStart=/p/python -m symphony.cli %s --host 0.0.0.0 --port 10000\n"
        % wf.resolve()
    )
    found = systemd.find_unit_for_workflow(wf)
    assert found is not None and found.name == "workmate-orchestrator.service"


def test_find_unit_for_workflow_returns_none_when_absent(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(tmp_path))
    assert systemd.find_unit_for_workflow(tmp_path / "x" / "WORKFLOW.md") is None


def test_ensure_unit_adopts_hand_made_unit_via_drop_in(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(tmp_path))
    monkeypatch.setattr(systemd.subprocess, "run", lambda *a, **k: _ok())
    wf = tmp_path / "proj" / "WORKFLOW.md"
    hand_made = tmp_path / "workmate-orchestrator.service"
    hand_made.write_text(
        "[Service]\nExecStart=/p/python -m symphony.cli %s --host 0.0.0.0 --port 9999\n"
        % wf.resolve()
    )

    result = systemd.ensure_unit(wf, host="0.0.0.0", port=10000, python="/p/python")

    assert result.action == "overridden"
    assert result.unit_name == "workmate-orchestrator.service"
    assert not (tmp_path / "symphony-proj.service").exists()
    override = tmp_path / "workmate-orchestrator.service.d" / "override.conf"
    assert override.exists() and "--port 10000" in override.read_text()
