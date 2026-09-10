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


def _systemd_record(workflow: Path, unit_name: str):
    return service_module.ServiceRecord(
        workflow_path=workflow.resolve(),
        workflow_dir=workflow.parent.resolve(),
        host="127.0.0.1",
        port=10000,
        orchestrator_pid=None,
        log_path=workflow.parent / "log" / "symphony.log",
        started_at="2026-09-10T00:00:00Z",
        orchestrator_command=[],
        service_instance_id=None,
        backend="systemd",
        unit_name=unit_name,
    )


def test_start_uses_systemd_backend_when_available(monkeypatch, tmp_path: Path, capsys) -> None:
    units = tmp_path / "units"
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(units))
    monkeypatch.setattr(systemd_module, "is_available", lambda: True)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        systemd_module.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _ok()
    )
    monkeypatch.setattr(service_module, "_run_doctor_or_print", lambda *a, **k: True)
    wf = _workflow(tmp_path)

    rc = service_main(["start", str(wf), "--host", "0.0.0.0", "--port", "10000"])

    captured = capsys.readouterr()
    unit_name = systemd_module.unit_name_for(wf)
    assert rc == 0
    assert ["systemctl", "--user", "restart", unit_name] in calls
    record = service_module.load_record(wf)
    assert record is not None
    assert record.backend == "systemd"
    assert record.unit_name == unit_name
    assert f"unit={unit_name}" in captured.out


def test_start_falls_back_to_detached_without_systemd(
    monkeypatch, tmp_path: Path
) -> None:
    units = tmp_path / "units"
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(units))
    monkeypatch.setattr(systemd_module, "is_available", lambda: False)
    spawned: list[list[str]] = []
    monkeypatch.setattr(
        service_module, "_popen_detached", lambda cmd, **kw: spawned.append(cmd) or 4242
    )
    monkeypatch.setattr(service_module, "_wait_until", lambda *a, **k: True)
    monkeypatch.setattr(service_module, "_run_doctor_or_print", lambda *a, **k: True)
    wf = _workflow(tmp_path)

    rc = service_main(["start", str(wf), "--port", "10000"])

    assert rc == 0
    assert len(spawned) == 1
    record = service_module.load_record(wf)
    assert record is not None
    assert record.backend == "detached"
    assert record.orchestrator_pid == 4242
    assert not units.exists()


def test_start_no_systemd_flag_forces_detached_backend(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(systemd_module, "is_available", lambda: True)
    spawned: list[list[str]] = []
    monkeypatch.setattr(
        service_module, "_popen_detached", lambda cmd, **kw: spawned.append(cmd) or 4242
    )
    monkeypatch.setattr(service_module, "_wait_until", lambda *a, **k: True)
    monkeypatch.setattr(service_module, "_run_doctor_or_print", lambda *a, **k: True)
    wf = _workflow(tmp_path)

    rc = service_main(["start", str(wf), "--port", "10000", "--no-systemd"])

    assert rc == 0
    assert len(spawned) == 1
    record = service_module.load_record(wf)
    assert record is not None and record.backend == "detached"


def test_stop_stops_systemd_unit_from_record(monkeypatch, tmp_path: Path, capsys) -> None:
    units = tmp_path / "units"
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(units))
    monkeypatch.setattr(systemd_module, "is_available", lambda: True)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        systemd_module.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or _ok()
    )
    wf = _workflow(tmp_path)
    service_module.save_record(_systemd_record(wf, "symphony-proj.service"))

    rc = service_main(["stop", str(wf)])

    captured = capsys.readouterr()
    assert rc == 0
    assert ["systemctl", "--user", "stop", "symphony-proj.service"] in calls
    assert service_module.load_record(wf) is None
    assert "unit=symphony-proj.service" in captured.out


def test_status_reports_systemd_unit_state(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(systemd_module, "is_unit_active", lambda name: True)
    monkeypatch.setattr(
        service_module, "is_symphony_workflow_reachable", lambda *a, **k: False
    )
    monkeypatch.setattr(service_module, "is_process_running", lambda pid: False)
    wf = _workflow(tmp_path)
    service_module.save_record(_systemd_record(wf, "symphony-proj.service"))

    rc = service_main(["status", str(wf)])

    captured = capsys.readouterr()
    assert rc == 0
    assert "unit=symphony-proj.service" in captured.out
    assert "state=active (running)" in captured.out


def _register(registry, tmp_path: Path, project_id: str, port: int):
    from symphony.projects import Project

    repo = tmp_path / project_id
    repo.mkdir()
    (repo / "WORKFLOW.md").write_text(
        "---\ntracker: {kind: file}\n---\nbody\n", encoding="utf-8"
    )
    registry.add(
        Project(
            id=project_id,
            name=project_id.title(),
            git_repo=str(repo),
            workflow=str(repo / "WORKFLOW.md"),
            host="0.0.0.0",
            port=port,
        )
    )


def test_install_all_installs_units_for_every_registered_project(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from symphony.projects import ProjectRegistry

    units = tmp_path / "units"
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(units))
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "projects.json"))
    monkeypatch.setattr(systemd_module, "is_available", lambda: True)
    monkeypatch.setattr(systemd_module, "run_systemctl", lambda *a, **k: _ok())
    registry = ProjectRegistry()
    _register(registry, tmp_path, "alpha", 10001)
    _register(registry, tmp_path, "beta", 10002)

    rc = service_main(["install-all"])

    captured = capsys.readouterr()
    assert rc == 0
    assert (units / "symphony-alpha.service").exists()
    assert (units / "symphony-beta.service").exists()
    assert "alpha: installed" in captured.out
    assert "beta: installed" in captured.out


def test_install_all_reports_failure_and_exits_1(monkeypatch, tmp_path: Path, capsys) -> None:
    from symphony.projects import ProjectRegistry

    units = tmp_path / "units"
    monkeypatch.setenv("SYMPHONY_SYSTEMD_UNIT_DIR", str(units))
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "projects.json"))
    monkeypatch.setattr(systemd_module, "is_available", lambda: True)
    _register(ProjectRegistry(), tmp_path, "alpha", 10001)

    def _boom(*a, **k):
        raise systemd_module.SystemdError("systemctl exploded")

    monkeypatch.setattr(systemd_module, "ensure_unit", _boom)

    rc = service_main(["install-all"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "systemctl exploded" in captured.err
    assert "alpha" in captured.err


def test_start_reports_unit_when_already_running(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(systemd_module, "is_available", lambda: True)
    monkeypatch.setattr(systemd_module, "is_unit_active", lambda name: True)
    monkeypatch.setattr(systemd_module, "run_systemctl", lambda *a, **k: _ok())
    wf = _workflow(tmp_path)
    service_module.save_record(_systemd_record(wf, "symphony-proj.service"))

    rc = service_main(["start", str(wf), "--port", "10000"])

    captured = capsys.readouterr()
    assert rc == 0
    assert "already running unit=symphony-proj.service" in captured.out
