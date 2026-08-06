"""Tests for the read-only git query module behind the Git page."""

from __future__ import annotations

import subprocess
from pathlib import Path

from symphony.utils import git_inspect


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(cwd),
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        },
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _make_task_branch(repo: Path, ident: str) -> None:
    _git(repo, "checkout", "-q", "-b", f"symphony/{ident}", "main")
    (repo / f"{ident}.py").write_text("print('hi')\n")
    _git(repo, "add", f"{ident}.py")
    _git(repo, "commit", "-q", "-m", f"{ident}: feature")
    _git(repo, "checkout", "-q", "main")


def test_commit_log_parses_fields_newest_first(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "second commit")

    entries = git_inspect.commit_log(repo)
    assert [e["subject"] for e in entries] == ["second commit", "init"]
    head = entries[0]
    assert head["author"] == "t"
    assert isinstance(head["sha"], str) and len(head["sha"]) == 40
    assert head["sha"].startswith(head["short_sha"])
    assert "main" in head["refs"]
    assert "T" in head["date"]  # ISO-8601 author date


def test_commit_log_single_ref_vs_all(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _make_task_branch(repo, "T-1")

    main_only = git_inspect.commit_log(repo, ref="main")
    assert [e["subject"] for e in main_only] == ["init"]
    all_branches = git_inspect.commit_log(repo)
    assert {e["subject"] for e in all_branches} == {"init", "T-1: feature"}


def test_commit_log_clamps_limit(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "second commit")

    assert len(git_inspect.commit_log(repo, limit=1)) == 1
    assert len(git_inspect.commit_log(repo, limit=0)) == 1
    assert len(git_inspect.commit_log(repo, limit=10_000)) == 2


def test_non_repo_degrades_to_empty(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert git_inspect.is_git_repo(plain) is False
    assert git_inspect.commit_log(plain) == []
    assert git_inspect.list_branches(plain) == []
    assert git_inspect.current_branch(plain) is None
    assert git_inspect.list_task_branches(plain, "main") == []


def test_ref_exists_and_current_branch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    assert git_inspect.is_git_repo(repo) is True
    assert git_inspect.current_branch(repo) == "main"
    assert git_inspect.ref_exists(repo, "main") is True
    assert git_inspect.ref_exists(repo, "no-such-branch") is False


def test_list_task_branches_maps_identifier_and_merge_state(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _make_task_branch(repo, "T-1")
    _make_task_branch(repo, "T-2")
    _git(repo, "merge", "-q", "--no-ff", "symphony/T-2", "-m", "merge T-2")

    rows = {r["identifier"]: r for r in git_inspect.list_task_branches(repo, "main")}
    assert set(rows) == {"T-1", "T-2"}

    unmerged = rows["T-1"]
    assert unmerged["branch"] == "symphony/T-1"
    assert unmerged["merged"] is False
    assert unmerged["ahead"] == 1
    assert unmerged["last_commit"]["subject"] == "T-1: feature"
    assert unmerged["last_commit"]["short_sha"]

    merged = rows["T-2"]
    assert merged["merged"] is True
    assert merged["ahead"] == 0


def test_list_task_branches_without_target_degrades(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _make_task_branch(repo, "T-1")

    rows = git_inspect.list_task_branches(repo, "no-such-branch")
    assert rows[0]["merged"] is False
    assert rows[0]["ahead"] is None
    assert rows[0]["behind"] is None
    assert git_inspect.list_task_branches(repo, None)[0]["ahead"] is None


def test_compare_refs_counts_commits_and_numstat(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _make_task_branch(repo, "T-1")
    # Advance main so the branch is also behind.
    (repo / "main.txt").write_text("m\n")
    _git(repo, "add", "main.txt")
    _git(repo, "commit", "-q", "-m", "main moves on")

    result = git_inspect.compare_refs(repo, "symphony/T-1", "main")
    assert result["ahead"] == 1
    assert result["behind"] == 1
    assert result["merged"] is False
    assert [c["subject"] for c in result["commits"]] == ["T-1: feature"]
    assert result["commits_truncated"] is False
    stat = result["stat"]
    assert stat["total"] == {"files": 1, "insertions": 1, "deletions": 0}
    assert stat["files"][0]["path"] == "T-1.py"
    assert stat["files"][0]["binary"] is False


def test_compare_refs_flags_binary_files(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "symphony/T-9", "main")
    (repo / "blob.bin").write_bytes(bytes(range(256)))
    _git(repo, "add", "blob.bin")
    _git(repo, "commit", "-q", "-m", "T-9: binary")
    _git(repo, "checkout", "-q", "main")

    result = git_inspect.compare_refs(repo, "symphony/T-9", "main")
    binary_rows = [f for f in result["stat"]["files"] if f["binary"]]
    assert [f["path"] for f in binary_rows] == ["blob.bin"]
    assert binary_rows[0]["insertions"] == 0
