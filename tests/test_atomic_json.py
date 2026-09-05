"""Tests for workflow-scoped state filenames and atomic JSON writes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from symphony.utils.atomic_json import state_file_name, write_json_atomic


def test_state_file_name_preserves_canonical_legacy_names() -> None:
    assert state_file_name("/repo/WORKFLOW.md", "token_ema") == "token_ema.json"
    assert state_file_name("/repo/WORKFLOW.md", "done_count") == "done_count.json"


def test_state_file_name_namespaces_sibling_workflows_deterministically() -> None:
    first = state_file_name("/repo/WORKFLOW.claude.md", "token_ema")
    second = state_file_name("/repo/WORKFLOW.claude.md", "token_ema")
    assert first == second
    assert first == "token_ema.WORKFLOW.claude.json"
    assert "/" not in first


def test_state_file_name_sanitizes_special_characters_without_leaking_directories() -> None:
    result = state_file_name("/repo/a weird/workflow;prod!.md", "done_count")
    assert result == "done_count.workflow-prod.json"
    assert not Path(result).is_absolute()
    assert "/" not in result


def test_write_json_atomic_replaces_and_leaves_no_temp_file(tmp_path: Path) -> None:
    destination = tmp_path / "state.json"
    write_json_atomic(destination, {"count": 2})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"count": 2}
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_write_json_atomic_creates_missing_parent(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "state.json"
    write_json_atomic(destination, {"new": True})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"new": True}


def test_write_json_atomic_replaces_existing_target(tmp_path: Path) -> None:
    destination = tmp_path / "state.json"
    destination.write_text('{"old": true}', encoding="utf-8")
    write_json_atomic(destination, {"new": True})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"new": True}


def test_write_json_atomic_retries_windows_replacement_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "state.json"
    real_replace = os.replace
    attempts = 0

    def contended_replace(source: str, target: str | os.PathLike[str]) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("simulated Windows sharing violation")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", contended_replace)
    monkeypatch.setattr("symphony.utils.atomic_json.time.sleep", lambda _: None)
    write_json_atomic(destination, {"ready": True})

    assert attempts == 3
    assert json.loads(destination.read_text(encoding="utf-8")) == {"ready": True}
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_write_json_atomic_cleans_up_after_replacement_contention_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "state.json"
    destination.write_text('{"preserve": true}', encoding="utf-8")

    def always_contended(source: str, target: str | os.PathLike[str]) -> None:
        raise PermissionError("simulated Windows sharing violation")

    monkeypatch.setattr(os, "replace", always_contended)
    monkeypatch.setattr("symphony.utils.atomic_json.time.sleep", lambda _: None)
    with pytest.raises(PermissionError):
        write_json_atomic(destination, {"ready": False})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"preserve": True}
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_write_json_atomic_propagates_non_permission_replace_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "state.json"

    def broken_replace(source: str, target: str | os.PathLike[str]) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError, match="disk failure"):
        write_json_atomic(destination, {"ready": False})
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_write_json_atomic_cleans_up_after_serialization_failure(tmp_path: Path) -> None:
    destination = tmp_path / "state.json"
    with pytest.raises(TypeError):
        write_json_atomic(destination, {"bad": object()})
    assert not destination.exists()
    assert list(tmp_path.glob(".state.json.*.tmp")) == []
