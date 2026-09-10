"""`symphony doctor` service-unit and linger checks (Linux + systemd hosts only)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from symphony import systemd as systemd_module
from symphony.cli import doctor
from symphony.workflow import ServiceConfig, build_service_config, load_workflow


def _ok(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def _cfg(tmp_path: Path) -> ServiceConfig:
    wf_dir = tmp_path / "proj"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf = wf_dir / "WORKFLOW.md"
    wf.write_text("---\ntracker: {kind: file}\n---\nbody\n", encoding="utf-8")
    return build_service_config(load_workflow(wf))


def test_service_unit_warns_when_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(tmp_path / "units"))
    cfg = _cfg(tmp_path)

    result = doctor.check_service_unit(cfg)

    assert result.name == "service.unit"
    assert result.status == "warn"
    assert "unit missing" in result.message
    assert "symphony service install" in result.message


def test_service_unit_passes_when_installed_and_enabled(
    monkeypatch, tmp_path: Path
) -> None:
    units = tmp_path / "units"
    units.mkdir()
    cfg = _cfg(tmp_path)
    (units / "symphony-proj.service").write_text(
        "[Service]\nExecStart=/p/python -m symphony.cli "
        f"{cfg.workflow_path} --host 0.0.0.0 --port 10000\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(units))
    monkeypatch.setattr(
        systemd_module.shutil, "which", lambda name: f"/usr/bin/{name}"
    )
    monkeypatch.setattr(
        systemd_module.subprocess, "run", lambda cmd, **kw: _ok("enabled\n")
    )

    result = doctor.check_service_unit(cfg)

    assert result.status == "pass"
    assert result.message == "unit installed and enabled (symphony-proj.service)"


def test_service_unit_warns_when_installed_but_not_enabled(
    monkeypatch, tmp_path: Path
) -> None:
    units = tmp_path / "units"
    units.mkdir()
    cfg = _cfg(tmp_path)
    (units / "symphony-proj.service").write_text(
        "[Service]\nExecStart=/p/python -m symphony.cli "
        f"{cfg.workflow_path} --host 0.0.0.0 --port 10000\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(units))
    monkeypatch.setattr(
        systemd_module.shutil, "which", lambda name: f"/usr/bin/{name}"
    )
    monkeypatch.setattr(
        systemd_module.subprocess, "run", lambda cmd, **kw: _ok("disabled\n")
    )

    result = doctor.check_service_unit(cfg)

    assert result.status == "warn"
    assert "not enabled" in result.message
    assert "systemctl --user enable symphony-proj.service" in result.message


def test_service_linger_warns_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        doctor.subprocess, "run", lambda cmd, **kw: _ok("Linger=no\n")
    )
    monkeypatch.setenv("USER", "symphony")

    result = doctor.check_service_linger()

    assert result.name == "service.linger"
    assert result.status == "warn"
    assert "linger disabled" in result.message
    assert "loginctl enable-linger" in result.message


def test_service_unit_checks_omitted_without_systemd(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(tmp_path / "units"))
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(doctor.systemd, "is_available", lambda: False)

    assert doctor.service_unit_checks(cfg) == []

    monkeypatch.setattr(doctor.systemd, "is_available", lambda: True)
    names = [r.name for r in doctor.service_unit_checks(cfg)]
    assert names == ["service.unit", "service.linger"]
