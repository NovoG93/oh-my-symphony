"""Git write targets and failure classification (`symphony.utils.git_sandbox`).

The worktree tests build a real repo and a real linked worktree rather than
faking the layout: the whole point of the module is that git splits its
writable state in a way that is easy to get wrong from memory, so the
assertions are only worth anything against git's actual on-disk output.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from symphony.utils.git_sandbox import (
    REMOTE_REJECTED,
    SANDBOX_WRITE_DENIED,
    UNKNOWN_FAILURE,
    classify_history_failure,
    is_linked_worktree,
    resolve_git_common_dir,
    resolve_git_dir,
    writable_git_roots,
)

_HAS_GIT = shutil.which("git") is not None
requires_git = pytest.mark.skipif(not _HAS_GIT, reason="git CLI required")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        env={
            "HOME": str(cwd),
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "PATH": os.environ.get("PATH", ""),
        },
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed.txt").write_text("seed")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-qm", "seed")
    return repo


# --- path resolution --------------------------------------------------------


@requires_git
def test_plain_repo_has_one_git_root(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    assert resolve_git_dir(repo) == (repo / ".git").resolve()
    assert resolve_git_common_dir(repo) == (repo / ".git").resolve()
    assert is_linked_worktree(repo) is False
    assert writable_git_roots(repo) == [str((repo / ".git").resolve())]


@requires_git
def test_linked_worktree_splits_admin_dir_from_object_database(
    tmp_path: Path,
) -> None:
    """The regression this module exists for.

    `git add` inside a worktree locks the index under the per-worktree admin
    dir but writes blobs to the *shared* object database. Granting only the
    admin dir is what produced `failed to insert into database`.
    """
    repo = _repo(tmp_path)
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(worktree), "-b", "symphony/T-1")

    git_dir = resolve_git_dir(worktree)
    common_dir = resolve_git_common_dir(worktree)

    assert git_dir == (repo / ".git" / "worktrees" / "wt").resolve()
    assert common_dir is not None
    assert common_dir == (repo / ".git").resolve()
    assert git_dir != common_dir
    assert is_linked_worktree(worktree) is True

    # The object database lives under the common dir, so both must be granted.
    objects = Path(
        _git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "objects")
        .stdout.strip()
    ).resolve()
    assert objects.is_relative_to(common_dir)
    assert writable_git_roots(worktree) == sorted([str(git_dir), str(common_dir)])


@requires_git
def test_resolves_from_a_nested_subdirectory(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    nested = repo / "a" / "b"
    nested.mkdir(parents=True)

    assert resolve_git_common_dir(nested) == (repo / ".git").resolve()


def test_outside_a_repo_yields_nothing(tmp_path: Path) -> None:
    lonely = tmp_path / "not-a-repo"
    lonely.mkdir()

    assert resolve_git_dir(lonely) is None
    assert resolve_git_common_dir(lonely) is None
    assert is_linked_worktree(lonely) is False
    assert writable_git_roots(lonely) == []


def test_garbled_gitdir_pointer_degrades_to_nothing(tmp_path: Path) -> None:
    """A broken `.git` pointer must not crash a backend launch."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".git").write_text("not a gitdir pointer at all\n")

    assert resolve_git_dir(ws) is None
    assert writable_git_roots(ws) == []


# --- failure classification -------------------------------------------------


def test_object_database_denial_is_a_sandbox_problem() -> None:
    """Verbatim stderr from the incident that motivated the host-side gate."""
    stderr = (
        "error: unable to create temporary file: Operation not permitted\n"
        "error: docs/changelog/changelog-2026-08-06.md: failed to insert "
        "into database\n"
        "fatal: updating files failed\n"
    )

    assert classify_history_failure(stderr) == SANDBOX_WRITE_DENIED


def test_generic_refusal_counts_only_with_local_write_context() -> None:
    with_context = (
        "error: cannot open .git/objects/pack: Operation not permitted"
    )
    without_context = "error: Operation not permitted"

    assert classify_history_failure(with_context) == SANDBOX_WRITE_DENIED
    assert classify_history_failure(without_context) == UNKNOWN_FAILURE


def test_ssh_auth_failure_is_never_a_sandbox_problem() -> None:
    """`permission denied` from the remote must not trigger a local retry."""
    stderr = (
        "git@github.com: Permission denied (publickey).\n"
        "fatal: Could not read from remote repository.\n"
    )

    assert classify_history_failure(stderr) == REMOTE_REJECTED


def test_rejected_push_is_a_remote_failure() -> None:
    stderr = (
        " ! [rejected]  main -> main (non-fast-forward)\n"
        "error: failed to push some refs to 'origin'\n"
    )

    assert classify_history_failure(stderr) == REMOTE_REJECTED


def test_empty_and_unrecognised_output_stay_unknown() -> None:
    assert classify_history_failure("") == UNKNOWN_FAILURE
    assert classify_history_failure("nothing to commit, working tree clean") == (
        UNKNOWN_FAILURE
    )
