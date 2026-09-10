"""Render and install systemd *user* units for Symphony orchestrators.

The unit's ``ExecStart`` mirrors :func:`symphony.service.build_orchestrator_command`
so a systemd-managed orchestrator behaves exactly like the detached subprocess
the engine launches on hosts without a user systemd manager.  Every path that
touches the filesystem honours ``SYMPHONY_SYSTEMD_UNIT_DIR`` so tests and
sandboxes never touch the operator's real ``~/.config/systemd/user``.
"""

from __future__ import annotations

import re
from pathlib import Path

UNIT_MARKER = "# Managed by `symphony service install` — customize via drop-in overrides, not by editing this file."


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "symphony"


def unit_name_for(workflow_path: str | Path) -> str:
    """Deterministic unit name for a workflow file (parent directory based)."""
    return f"symphony-{_slug(Path(workflow_path).resolve().parent.name)}.service"
