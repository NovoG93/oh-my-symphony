"""Bounded host-tool readiness probes, separate from release-gate readiness."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..workflow.config import ServiceConfig
from .models import PmError
from .openspec import resolve_node
from .planning import SUPPORTED_CODEX_VERSION

_CODEX_CACHE: dict[str, str] = {}


def probe_codex() -> dict[str, Any]:
    executable = shutil.which("codex")
    found = None
    version_matches = False
    if executable is not None:
        executable = str(Path(executable).resolve())
        found = _CODEX_CACHE.get(executable)
        version_matches = found == SUPPORTED_CODEX_VERSION
        if not version_matches:
            try:
                result = subprocess.run(
                    [executable, "--version"], capture_output=True, text=True, timeout=5,
                )
                match = re.fullmatch(r"codex-cli (\d+\.\d+\.\d+)", result.stdout.strip())
                found = match[1] if match else None
                version_matches = result.returncode == 0 and found == SUPPORTED_CODEX_VERSION
                if version_matches:
                    _CODEX_CACHE[executable] = SUPPORTED_CODEX_VERSION
            except (OSError, subprocess.TimeoutExpired):
                pass
    reasons = []
    if not version_matches:
        reasons.append(
            f"Planning assistant needs Codex {SUPPORTED_CODEX_VERSION} (found: {found or 'none'})."
        )
    if not (Path.home() / ".codex" / "auth.json").is_file():
        reasons.append(
            "Planning assistant needs a Codex login: ~/.codex/auth.json is missing. Run codex login."
        )
    return {
        "required": SUPPORTED_CODEX_VERSION, "found": found,
        "available": not reasons, "reason": " ".join(reasons) or None,
    }


def probe_node() -> dict[str, Any]:
    try:
        _, version = resolve_node()
    except PmError as exc:
        match = re.search(r"\(found: (.+)\)$", exc.message)
        found = match[1] if match and match[1] != "none" else None
        return {"required": "20.19.0", "found": found, "available": False, "reason": exc.message}
    return {"required": "20.19.0", "found": version, "available": True, "reason": None}


def probe(cfg: ServiceConfig) -> dict[str, dict[str, Any]]:
    tools = {}
    if cfg.pm.assistance:
        tools["codex"] = probe_codex()
    if cfg.pm.enabled and cfg.pm.openspec_executable is None:
        tools["node"] = probe_node()
    return tools
