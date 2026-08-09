"""Command-line surface for machine release validation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from symphony.cli import release

from tests.test_release_contracts import _git, _write_valid_release


def _repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release@example.test")
    (repo / "app.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "app.txt")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "branch", "symphony/APP-1")
    board = repo / "kanban"
    board.mkdir()
    workflow = repo / "WORKFLOW.md"
    workflow.write_text(
        """---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Build, Verify, Document]
  terminal_states: [Done, Blocked]
workspace: { root: ./workspaces }
agent:
  kind: codex
  auto_merge_target_branch: main
codex: { command: codex app-server }
---
Release workflow.
""",
        encoding="utf-8",
    )
    _git(repo, "add", "WORKFLOW.md")
    _git(repo, "commit", "-m", "release workflow")
    _write_valid_release(repo)
    workspace = tmp_path / "workspace"
    _git(
        repo,
        "worktree",
        "add",
        "-q",
        "-b",
        "symphony/RELEASE-CLI-WORKSPACE",
        str(workspace),
        "main",
    )
    shutil.copytree(repo / "docs", workspace / "docs", dirs_exist_ok=True)
    (workspace / "kanban").symlink_to(board, target_is_directory=True)
    return repo, workflow, workspace


def test_release_check_json_passes_current_target(
    tmp_path: Path, capsys
) -> None:
    repo, workflow, workspace = _repo(tmp_path)

    rc = release.main(
        [
            "check",
            str(workflow),
            "--ticket",
            "VERIFY-1",
            "--workspace",
            str(workspace),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["passed"] is True
    assert payload["target_sha"] == _git(repo, "rev-parse", "main")


def test_release_check_is_nonzero_for_stale_evidence(
    tmp_path: Path, capsys
) -> None:
    repo, workflow, workspace = _repo(tmp_path)
    evidence_path = workspace / "docs" / "VERIFY-1" / "qa" / "release-evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["target_sha"] = "0" * 40
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    rc = release.main(
        [
            "check",
            str(workflow),
            "--ticket",
            "VERIFY-1",
            "--workspace",
            str(workspace),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["passed"] is False
    assert any("target_sha" in item for item in payload["evidence_errors"])
