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
from pathlib import Path

from ..workflow.constants import SYMPHONY_BRANCH_PREFIX

_GIT_TIMEOUT_S = 10.0
# Unit separator — cannot appear in ref names, authors or subjects.
_SEP = "\x1f"
_LOG_FORMAT = f"%H{_SEP}%h{_SEP}%an{_SEP}%aI{_SEP}%D{_SEP}%s"

MAX_LOG_LIMIT = 200
DEFAULT_LOG_LIMIT = 50
COMPARE_COMMIT_CAP = 100


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
