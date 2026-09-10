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
