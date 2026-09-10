"""Render and install systemd *user* units for Symphony orchestrators.

The unit's ``ExecStart`` mirrors :func:`symphony.service.build_orchestrator_command`
so a systemd-managed orchestrator behaves exactly like the detached subprocess
the engine launches on hosts without a user systemd manager.  Every path that
touches the filesystem honours ``SYMPHONY_SYSTEMD_UNIT_DIR`` so tests and
sandboxes never touch the operator's real ``~/.config/systemd/user``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

UNIT_MARKER = "# Managed by `symphony service install` — customize via drop-in overrides, not by editing this file."


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "symphony"


def unit_name_for(workflow_path: str | Path) -> str:
    """Deterministic unit name for a workflow file (parent directory based)."""
    return f"symphony-{_slug(Path(workflow_path).resolve().parent.name)}.service"


def _systemd_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_@./:+-]+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_unit(
    workflow_path: str | Path,
    *,
    host: str,
    port: int,
    python: str,
    description: str | None = None,
) -> str:
    """Full unit text. ExecStart mirrors service.build_orchestrator_command()."""
    wf = Path(workflow_path).resolve()
    desc = description or f"Symphony orchestrator ({wf.parent.name})"
    return "\n".join(
        [
            UNIT_MARKER,
            "[Unit]",
            f"Description={desc}",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={_systemd_quote(str(wf.parent))}",
            "ExecStart="
            + " ".join(
                [
                    _systemd_quote(python),
                    "-m",
                    "symphony.cli",
                    _systemd_quote(str(wf)),
                    "--host",
                    _systemd_quote(host),
                    "--port",
                    str(port),
                ]
            ),
            "Restart=always",
            "RestartSec=3",
            "KillMode=mixed",
            "Environment=PYTHONUNBUFFERED=1",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def unit_dir() -> Path:
    """Directory holding user units (``SYMPHONY_SYSTEMD_UNIT_DIR`` overrides)."""
    override = os.environ.get("SYMPHONY_SYSTEMD_UNIT_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "systemd" / "user"


def is_available() -> bool:
    """True when a usable *user* systemd manager exists on this host."""
    if not shutil.which("systemctl"):
        return False
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.stdout.strip() in {"running", "degraded"}


class SystemdError(RuntimeError):
    """A systemctl invocation failed, or could not run at all."""


@dataclass(frozen=True)
class EnsureResult:
    action: str  # installed | unchanged | overridden
    unit_path: Path
    unit_name: str


def run_systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ``systemctl --user <args>``; raise SystemdError on failure."""
    command = ["systemctl", "--user", *args]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemdError(f"`{' '.join(command)}` failed to run: {exc}") from exc
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or (
            f"exit code {proc.returncode}"
        )
        raise SystemdError(f"`{' '.join(command)}` failed: {detail}")
    return proc


def override_path_for(unit_path: str | Path) -> Path:
    """Drop-in override path for a unit file (``<unit>.d/override.conf``)."""
    unit = Path(unit_path)
    return unit.with_name(f"{unit.name}.d") / "override.conf"


def _override_text(unit_text: str) -> str:
    """Drop-in content: the [Service] body of a rendered unit plus the marker."""
    service_lines: list[str] = []
    in_service = False
    for line in unit_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_service = stripped == "[Service]"
            continue
        if in_service and stripped:
            service_lines.append(line)
    return "\n".join([UNIT_MARKER, "[Service]", *service_lines, ""])


def _write_override(unit_path: Path, unit_text: str) -> str:
    """Create/replace the drop-in; returns ``overridden`` or ``unchanged``."""
    override = override_path_for(unit_path)
    new_text = _override_text(unit_text)
    if override.exists() and override.read_text(encoding="utf-8") == new_text:
        return "unchanged"
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text(new_text, encoding="utf-8")
    return "overridden"


def find_unit_for_workflow(workflow_path: str | Path) -> Path | None:
    """The installed unit, if any, whose ``ExecStart`` serves this workflow.

    Hand-made units (e.g. ``workmate-orchestrator.service``) count: adoption
    layers a drop-in over them instead of creating a competing unit.
    """
    needle = str(Path(workflow_path).resolve())
    directory = unit_dir()
    if not directory.is_dir():
        return None
    for unit in sorted(directory.glob("*.service")):
        try:
            text = unit.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if line.strip().startswith("ExecStart=") and needle in line:
                return unit
    return None


def ensure_unit(
    workflow_path: str | Path,
    *,
    host: str,
    port: int,
    python: str,
    enable: bool = True,
) -> EnsureResult:
    """Idempotently install (updating via a drop-in) the unit for a workflow."""
    managed_name = unit_name_for(workflow_path)
    target = unit_dir() / managed_name
    text = render_unit(workflow_path, host=host, port=port, python=python)
    adopted = find_unit_for_workflow(workflow_path)
    if adopted is not None and adopted.name != managed_name:
        # A unit that already serves this workflow exists under another name:
        # extend it via a drop-in instead of creating a second, competing unit.
        target = adopted
        action = _write_override(target, text)
    elif target.exists() and target.read_text(encoding="utf-8") == text:
        action = "unchanged"
    elif target.exists():
        # The managed unit exists but settings changed: layer a drop-in so
        # operator edits in the base file survive the update.
        action = _write_override(target, text)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        action = "installed"
    if action != "unchanged":
        run_systemctl("daemon-reload")
        if enable:
            run_systemctl("enable", target.name)
    return EnsureResult(action=action, unit_path=target, unit_name=target.name)


def unit_path_for(workflow_path: str | Path) -> Path:
    """The unit that serves this workflow: an adopted one, else the managed path."""
    adopted = find_unit_for_workflow(workflow_path)
    if adopted is not None:
        return adopted
    return unit_dir() / unit_name_for(workflow_path)


@dataclass(frozen=True)
class UninstallResult:
    unit_path: Path
    unit_name: str
    removed_unit: bool
    drop_in_removed: bool
    disable_error: str | None = None


def uninstall_unit(workflow_path: str | Path) -> UninstallResult:
    """Disable and remove the unit serving a workflow, plus its drop-ins.

    A unit installed by :func:`ensure_unit` is removed entirely.  A hand-made
    unit adopted via drop-in keeps its base file: only the managed override is
    removed so operator customizations survive an uninstall.
    """
    target = unit_path_for(workflow_path)
    disable_error: str | None = None
    if target.exists() or target.is_symlink():
        try:
            run_systemctl("disable", "--now", target.name, check=False)
        except SystemdError as exc:
            disable_error = str(exc)
    managed = False
    if target.is_file():
        try:
            managed = UNIT_MARKER in target.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            managed = False
    drop_in_dir = override_path_for(target).parent
    drop_in_removed = drop_in_dir.is_dir()
    if drop_in_removed:
        shutil.rmtree(drop_in_dir)
    if managed:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
    return UninstallResult(
        unit_path=target,
        unit_name=target.name,
        removed_unit=managed,
        drop_in_removed=drop_in_removed,
        disable_error=disable_error,
    )
