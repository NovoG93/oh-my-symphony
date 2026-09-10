"""`symphony service install|uninstall|unit-path` and systemd-backend plumbing."""

from __future__ import annotations

import subprocess
from pathlib import Path

from symphony import service as service_module  # noqa: F401  (monkeypatch anchor)
from symphony import systemd as systemd_module
from symphony.service import main as service_main


def _ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout, "")


def _workflow(tmp_path: Path) -> Path:
    wf_dir = tmp_path / "proj"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf = wf_dir / "WORKFLOW.md"
    wf.write_text("---\ntracker: {kind: file}\n---\nbody\n", encoding="utf-8")
    return wf


def test_service_install_writes_and_enables_unit(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    units = tmp_path / "units"
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(units))
    monkeypatch.setattr(systemd_module, "is_available", lambda: True)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        systemd_module.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _ok()
    )
    wf = _workflow(tmp_path)

    rc = service_main(["install", str(wf), "--host", "0.0.0.0", "--port", "10000"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "installed symphony-proj.service at" in captured.out
    unit = units / "symphony-proj.service"
    assert unit.exists() and systemd_module.UNIT_MARKER in unit.read_text()
    assert ["systemctl", "--user", "daemon-reload"] in calls
    assert ["systemctl", "--user", "enable", "symphony-proj.service"] in calls


def test_service_install_without_systemd_fails_with_fallback_hint(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    units = tmp_path / "units"
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(units))
    monkeypatch.setattr(systemd_module, "is_available", lambda: False)
    wf = _workflow(tmp_path)

    rc = service_main(["install", str(wf), "--port", "10000"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "systemd user manager not available" in captured.err
    assert "detached" in captured.err
    assert not units.exists()


def test_service_install_dry_run_prints_unit_without_writing(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    units = tmp_path / "units"
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(units))
    wf = _workflow(tmp_path)

    rc = service_main(["install", "--dry-run", str(wf), "--port", "10000"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "ExecStart=" in captured.out
    assert "Restart=always" in captured.out
    assert not units.exists()


def test_service_uninstall_removes_managed_unit_and_drop_in(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    units = tmp_path / "units"
    units.mkdir()
    wf = _workflow(tmp_path)
    unit = units / "symphony-proj.service"
    unit.write_text(
        systemd_module.UNIT_MARKER
        + "\n[Service]\nExecStart=/p/python -m symphony.cli "
        + f"{wf.resolve()} --host 0.0.0.0 --port 10000\n",
        encoding="utf-8",
    )
    drop_in = units / "symphony-proj.service.d"
    drop_in.mkdir()
    (drop_in / "override.conf").write_text("[Service]\n", encoding="utf-8")
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(units))
    monkeypatch.setattr(systemd_module, "is_available", lambda: True)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        systemd_module.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _ok()
    )

    rc = service_main(["uninstall", str(wf)])

    captured = capsys.readouterr()
    assert rc == 0
    assert "uninstalled symphony-proj.service" in captured.out
    assert not unit.exists()
    assert not drop_in.exists()
    assert ["systemctl", "--user", "disable", "--now", "symphony-proj.service"] in calls


def test_service_unit_path_prints_managed_path(monkeypatch, tmp_path: Path, capsys) -> None:
    units = tmp_path / "units"
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(units))
    wf = _workflow(tmp_path)

    rc = service_main(["unit-path", str(wf)])

    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out.strip() == str(units / "symphony-proj.service")
