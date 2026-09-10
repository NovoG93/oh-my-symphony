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
