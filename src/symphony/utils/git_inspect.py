"""Read-only git queries backing the web UI's Git page.

Synchronous subprocess wrappers — web handlers call them through
`asyncio.to_thread`. Git failures never raise: a missing repo, bad ref or
timeout degrades to an empty result, matching the web API's read-degradation
principle. Callers must validate ref names (`webapi._BRANCH_RE`) before
passing them here; the leading-alphanumeric rule also rules out option
injection via refs that look like `--flags`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

from ..workflow.constants import SYMPHONY_BRANCH_PREFIX

_GIT_TIMEOUT_S = 10.0
# Unit separator — cannot appear in ref names, authors or subjects.
_SEP = "\x1f"
_LOG_FORMAT = f"%H{_SEP}%h{_SEP}%an{_SEP}%aI{_SEP}%D{_SEP}%s"

MAX_LOG_LIMIT = 200
DEFAULT_LOG_LIMIT = 50
COMPARE_COMMIT_CAP = 100
MAX_PATCH_CHARS = 200_000

# Ignored files can change what an application launches even though ordinary
# Git diff/status queries omit them. These roots are the narrow exception for
# Symphony's own control data and reproducible dependency/tool caches. Product
# output roots such as dist/, build/, target/, and .next/ are intentionally not
# exempt.
_RELEASE_CONTROL_ROOTS = frozenset(
    {"kanban", ".locks", "log", ".symphony", ".oneshot"}
)
_RELEASE_DEPENDENCY_CACHE_PARTS = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        ".tox",
        ".nox",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".pyright",
        ".cache",
    }
)


def _run_git(
    workflow_dir: Path, *args: str
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(workflow_dir),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _run_git_bytes(
    workflow_dir: Path, *args: str
) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(workflow_dir),
            capture_output=True,
            text=False,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _lines(proc: subprocess.CompletedProcess[str] | None) -> list[str]:
    if proc is None or proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()]


def is_git_repo(workflow_dir: Path) -> bool:
    proc = _run_git(workflow_dir, "rev-parse", "--git-dir")
    return proc is not None and proc.returncode == 0


def list_branches(workflow_dir: Path) -> list[str]:
    proc = _run_git(workflow_dir, "branch", "--format=%(refname:short)")
    return [line.strip() for line in _lines(proc)]


def current_branch(workflow_dir: Path) -> str | None:
    """Checked-out branch name, or None when detached / not a repo."""
    proc = _run_git(workflow_dir, "symbolic-ref", "--short", "HEAD")
    if proc is None or proc.returncode != 0:
        return None
    name = proc.stdout.strip()
    return name or None


def ref_exists(workflow_dir: Path, ref: str) -> bool:
    proc = _run_git(
        workflow_dir, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"
    )
    return proc is not None and proc.returncode == 0


def resolve_commit(workflow_dir: Path, ref: str) -> str | None:
    """Return the host-resolved full commit SHA for ``ref``.

    Release validation must bind worker evidence to a host fact rather than
    trusting a SHA copied into JSON.  Keep the query read-only and use the
    same failure-to-``None`` convention as the other inspection helpers.
    """
    proc = _run_git(
        workflow_dir, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"
    )
    if proc is None or proc.returncode != 0:
        return None
    sha = proc.stdout.strip().lower()
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        return None
    return sha


def resolve_local_branch_commit(workflow_dir: Path, branch: str) -> str | None:
    """Resolve only an actual local branch, never a commit-ish/reflog expression."""
    if not branch or branch != branch.strip() or branch.startswith("-"):
        return None
    full_ref = f"refs/heads/{branch}"
    valid = _run_git(workflow_dir, "check-ref-format", full_ref)
    if valid is None or valid.returncode != 0:
        return None
    return resolve_commit(workflow_dir, full_ref)


def changed_paths_since(
    workflow_dir: Path, base_commit_sha: str
) -> tuple[str, ...] | None:
    """Return workspace paths that can differ from an exact release base.

    This includes ignored untracked runtime files because they may influence
    the launched application. Only Symphony control data and conventional
    dependency/tool cache roots are omitted; generated product roots such as
    ``dist``, ``build``, ``target``, and ``.next`` remain visible.
    """
    normalized_sha = base_commit_sha.strip().lower()
    if len(normalized_sha) != 40 or any(
        char not in "0123456789abcdef" for char in normalized_sha
    ):
        return None
    tracked = _run_git_bytes(
        workflow_dir,
        "diff",
        "--name-only",
        "-z",
        normalized_sha,
        "--",
    )
    untracked = _run_git_bytes(
        workflow_dir,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
    )
    ignored = _run_git_bytes(
        workflow_dir,
        "status",
        "--porcelain=v1",
        "-z",
        "--ignored=matching",
        "--untracked-files=normal",
        "--",
    )
    if (
        tracked is None
        or tracked.returncode != 0
        or untracked is None
        or untracked.returncode != 0
        or ignored is None
        or ignored.returncode != 0
    ):
        return None
    try:
        tracked_paths = {
            raw.decode("utf-8")
            for raw in tracked.stdout.split(b"\0")
            if raw
        }
        workspace_only_paths = {
            raw.decode("utf-8")
            for raw in untracked.stdout.split(b"\0")
            if raw
        }
        workspace_only_paths.update(
            raw[3:].decode("utf-8")
            for raw in ignored.stdout.split(b"\0")
            if raw.startswith(b"!! ")
        )
    except UnicodeDecodeError:
        return None
    return tuple(
        sorted(
            tracked_paths
            | {
                path
                for path in workspace_only_paths
                if not _is_release_infrastructure_path(PurePosixPath(path))
            }
        )
    )


def _is_release_infrastructure_path(path: PurePosixPath) -> bool:
    if not path.parts:
        return False
    return path.parts[0] in _RELEASE_CONTROL_ROOTS or any(
        part in _RELEASE_DEPENDENCY_CACHE_PARTS for part in path.parts
    )


def is_git_stageable_path(
    workflow_dir: Path, repo_relative_path: str
) -> bool | None:
    """Whether a release evidence path is tracked or can be staged.

    ``False`` means an untracked path is ignored. ``None`` means Git could not
    establish the status, which release validation must treat as an error.
    """
    tracked = _run_git(
        workflow_dir,
        "ls-files",
        "--error-unmatch",
        "--",
        repo_relative_path,
    )
    if tracked is None:
        return None
    if tracked.returncode == 0:
        return True
    if tracked.returncode != 1:
        return None
    ignored = _run_git(
        workflow_dir,
        "check-ignore",
        "--quiet",
        "--no-index",
        "--",
        repo_relative_path,
    )
    if ignored is None:
        return None
    if ignored.returncode == 0:
        return False
    if ignored.returncode == 1:
        return True
    return None


def read_commit_blob(
    workflow_dir: Path, commit_sha: str, repo_relative_path: str
) -> bytes | None:
    """Read one regular file from an exact commit without touching the checkout.

    The commit must already be a host-resolved full SHA.  Paths use repository
    POSIX syntax and are rejected before they can become part of Git's
    ``<tree>:<path>`` revision syntax.  ``ls-tree`` also rejects symlink and
    submodule entries, so callers never follow a checkout symlink by accident.
    """
    normalized_sha = commit_sha.strip().lower()
    if len(normalized_sha) != 40 or any(
        char not in "0123456789abcdef" for char in normalized_sha
    ):
        return None
    if (
        not repo_relative_path
        or repo_relative_path.startswith(("/", "-"))
        or "\\" in repo_relative_path
        or ":" in repo_relative_path
        or any(char.isspace() and char not in {" "} for char in repo_relative_path)
    ):
        return None
    posix_path = PurePosixPath(repo_relative_path)
    if (
        str(posix_path) != repo_relative_path
        or any(part in {"", ".", ".."} for part in posix_path.parts)
    ):
        return None

    tree = _run_git_bytes(
        workflow_dir,
        "ls-tree",
        "-z",
        normalized_sha,
        "--",
        repo_relative_path,
    )
    if tree is None or tree.returncode != 0:
        return None
    records = [record for record in tree.stdout.split(b"\0") if record]
    if len(records) != 1 or b"\t" not in records[0]:
        return None
    metadata, raw_name = records[0].split(b"\t", 1)
    try:
        mode, object_type, object_sha = metadata.decode("ascii").split(" ", 2)
        expected_name = repo_relative_path.encode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError, ValueError):
        return None
    if (
        raw_name != expected_name
        or mode not in {"100644", "100755"}
        or object_type != "blob"
    ):
        return None
    blob = _run_git_bytes(workflow_dir, "cat-file", "blob", object_sha)
    if blob is None or blob.returncode != 0:
        return None
    return blob.stdout


def _parse_refs(decorations: str) -> list[str]:
    """`%D` decorations -> plain ref names ("HEAD -> dev, origin/dev")."""
    refs: list[str] = []
    for part in decorations.split(","):
        name = part.strip()
        if name.startswith("HEAD -> "):
            name = name[len("HEAD -> ") :]
        if name and name != "HEAD":
            refs.append(name)
    return refs


def _parse_log_line(line: str) -> dict[str, object] | None:
    fields = line.split(_SEP)
    if len(fields) != 6:
        return None
    sha, short_sha, author, date, decorations, subject = fields
    return {
        "sha": sha,
        "short_sha": short_sha,
        "author": author,
        "date": date,
        "refs": _parse_refs(decorations),
        "subject": subject,
    }


def _log(workflow_dir: Path, revspec: list[str], limit: int) -> list[dict[str, object]]:
    proc = _run_git(
        workflow_dir,
        "log",
        f"--format={_LOG_FORMAT}",
        "-n",
        str(limit),
        *revspec,
        "--",
    )
    entries = []
    for line in _lines(proc):
        entry = _parse_log_line(line)
        if entry is not None:
            entries.append(entry)
    return entries


def commit_log(
    workflow_dir: Path, ref: str | None = None, limit: int = DEFAULT_LOG_LIMIT
) -> list[dict[str, object]]:
    """Newest-first commits for one ref, or across all branches when None."""
    limit = max(1, min(int(limit), MAX_LOG_LIMIT))
    revspec = [ref] if ref else ["--all", "--date-order"]
    return _log(workflow_dir, revspec, limit)


def ahead_behind(
    workflow_dir: Path, branch: str, target: str
) -> tuple[int, int] | None:
    """(ahead, behind) of `branch` relative to `target`, None on failure."""
    proc = _run_git(
        workflow_dir, "rev-list", "--left-right", "--count", f"{target}...{branch}"
    )
    lines = _lines(proc)
    if not lines:
        return None
    parts = lines[0].split()
    if len(parts) != 2:
        return None
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return ahead, behind


def is_merged(workflow_dir: Path, branch: str, target: str) -> bool:
    proc = _run_git(workflow_dir, "merge-base", "--is-ancestor", branch, target)
    return proc is not None and proc.returncode == 0


def list_task_branches(workflow_dir: Path, target: str | None) -> list[dict[str, object]]:
    """`symphony/*` branches with merge status relative to `target`.

    `identifier` is the branch name minus the prefix. `ticket` / `running`
    enrichment happens in the web layer, which owns the tracker and the
    orchestrator. ahead/behind/merged are None/False when `target` is
    missing or unresolvable.
    """
    proc = _run_git(
        workflow_dir,
        "for-each-ref",
        f"refs/heads/{SYMPHONY_BRANCH_PREFIX}",
        f"--format=%(refname:short){_SEP}%(objectname:short){_SEP}"
        f"%(committerdate:iso8601-strict){_SEP}%(subject)",
    )
    target_ok = bool(target) and ref_exists(workflow_dir, target)
    branches: list[dict[str, object]] = []
    for line in _lines(proc):
        fields = line.split(_SEP)
        if len(fields) != 4:
            continue
        branch, short_sha, date, subject = fields
        counts = (
            ahead_behind(workflow_dir, branch, target)  # type: ignore[arg-type]
            if target_ok
            else None
        )
        branches.append(
            {
                "branch": branch,
                "identifier": branch[len(SYMPHONY_BRANCH_PREFIX) :],
                "merged": (
                    is_merged(workflow_dir, branch, target)  # type: ignore[arg-type]
                    if target_ok
                    else False
                ),
                "ahead": counts[0] if counts else None,
                "behind": counts[1] if counts else None,
                "last_commit": {
                    "short_sha": short_sha,
                    "date": date,
                    "subject": subject,
                },
            }
        )
    return branches


def _numstat(workflow_dir: Path, branch: str, target: str) -> dict[str, object]:
    # Three-dot: the diff a merge of `branch` would actually apply.
    proc = _run_git(workflow_dir, "diff", "--numstat", f"{target}...{branch}")
    files: list[dict[str, object]] = []
    insertions = deletions = 0
    for line in _lines(proc):
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        raw_ins, raw_del, path = parts
        binary = raw_ins == "-" or raw_del == "-"
        file_ins = 0 if binary else int(raw_ins)
        file_del = 0 if binary else int(raw_del)
        insertions += file_ins
        deletions += file_del
        files.append(
            {
                "path": path,
                "insertions": file_ins,
                "deletions": file_del,
                "binary": binary,
            }
        )
    return {
        "files": files,
        "total": {
            "files": len(files),
            "insertions": insertions,
            "deletions": deletions,
        },
    }


def _capped_patch(proc: subprocess.CompletedProcess[str] | None) -> dict[str, object]:
    if proc is None or proc.returncode != 0:
        return {"patch": "", "truncated": False}
    patch = proc.stdout
    if len(patch) > MAX_PATCH_CHARS:
        return {"patch": patch[:MAX_PATCH_CHARS], "truncated": True}
    return {"patch": patch, "truncated": False}


def diff_patch(
    workflow_dir: Path, branch: str, target: str, path: str | None = None
) -> dict[str, object]:
    """Unified diff a merge of `branch` into `target` would apply (three-dot)."""
    args = ["diff", "--no-color", f"{target}...{branch}"]
    if path:
        args += ["--", path]
    return _capped_patch(_run_git(workflow_dir, *args))


def commit_patch(workflow_dir: Path, ref: str) -> dict[str, object]:
    """Full `git show` output (header + patch) for one commit."""
    return _capped_patch(_run_git(workflow_dir, "show", "--no-color", ref))


def compare_refs(
    workflow_dir: Path, branch: str, target: str
) -> dict[str, object]:
    """Merge preview of `branch` into `target`; both refs must exist."""
    counts = ahead_behind(workflow_dir, branch, target)
    commits = _log(workflow_dir, [f"{target}..{branch}"], COMPARE_COMMIT_CAP + 1)
    truncated = len(commits) > COMPARE_COMMIT_CAP
    return {
        "branch": branch,
        "target": target,
        "ahead": counts[0] if counts else None,
        "behind": counts[1] if counts else None,
        "merged": is_merged(workflow_dir, branch, target),
        "commits": commits[:COMPARE_COMMIT_CAP],
        "commits_truncated": truncated,
        "stat": _numstat(workflow_dir, branch, target),
    }
