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
