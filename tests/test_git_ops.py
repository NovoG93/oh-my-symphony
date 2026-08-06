"""Unit tests for the mutating git helpers (`symphony.utils.git_ops`)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from symphony.utils import git_ops

_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env={**_ENV, "HOME": str(cwd)},
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    (work / "a.txt").write_text("a\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "checkout", "-q", "-b", "symphony/T-1")
    (work / "b.txt").write_text("b\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "T-1")
    _git(work, "checkout", "-q", "main")
    return work


def test_remote_name_validation_rejects_option_lookalikes() -> None:
    assert git_ops.is_valid_remote_name("origin")
    assert git_ops.is_valid_remote_name("my-remote_2.0")
    assert not git_ops.is_valid_remote_name("")
    assert not git_ops.is_valid_remote_name("--upload-pack=evil")
    assert not git_ops.is_valid_remote_name(".hidden")
    assert not git_ops.is_valid_remote_name("has space")
    assert not git_ops.is_valid_remote_name("x" * 200)


def test_delete_branch_refuses_unmerged_without_force(repo: Path) -> None:
    result = git_ops.delete_branch(repo, "symphony/T-1")
    assert result.ok is False
    assert result.status == "not_merged"

    forced = git_ops.delete_branch(repo, "symphony/T-1", force=True)
    assert forced.ok is True and forced.status == "deleted"


def test_delete_branch_refuses_the_checked_out_branch(repo: Path) -> None:
    result = git_ops.delete_branch(repo, "main", force=True)
    assert result.ok is False
    assert result.status == "checked_out"


def test_push_is_never_forced(repo: Path, tmp_path: Path) -> None:
    """A diverged remote must reject the push instead of being overwritten."""
    bare = tmp_path / "origin.git"
    _git(repo, "init", "-q", "--bare", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    assert git_ops.list_remotes(repo) == ["origin"]
    assert git_ops.default_remote(repo) == "origin"

    first = git_ops.push_branch(repo, "main", "origin")
    assert first.ok is True and first.status == "pushed"
    assert git_ops.branch_on_remote(repo, "origin", "main") is True

    # Rewrite history locally, then try to push over the remote's commit.
    (repo / "a.txt").write_text("rewritten\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--amend", "-m", "rewritten")
    rejected = git_ops.push_branch(repo, "main", "origin")
    assert rejected.ok is False
    assert rejected.status == "rejected"


def test_create_pull_request_reports_missing_gh(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_ops.shutil, "which", lambda _name: None)
    result = git_ops.create_pull_request(repo, "symphony/T-1", "main", "t", "b")
    assert result.ok is False
    assert result.status == "gh_unavailable"


def test_first_url_extracts_the_pr_link() -> None:
    assert (
        git_ops._first_url("Created https://github.com/o/r/pull/7.")
        == "https://github.com/o/r/pull/7"
    )
    assert git_ops._first_url("no link here") == ""
