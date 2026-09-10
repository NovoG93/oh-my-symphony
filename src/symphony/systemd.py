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


def ensure_unit(
    workflow_path: str | Path,
    *,
    host: str,
    port: int,
    python: str,
    enable: bool = True,
) -> EnsureResult:
    """Idempotently install (updating via a drop-in) the unit for a workflow."""
    target = unit_dir() / unit_name_for(workflow_path)
    text = render_unit(workflow_path, host=host, port=port, python=python)
    action = "unchanged"
    if target.exists() and target.read_text(encoding="utf-8") == text:
        pass
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
