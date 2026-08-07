"""Per-node git provenance: what the workspace looked like before and after.

Take a `snapshot` before dispatching a node, another when it finishes, and
`provenance_between` turns the pair into the row the ledger stores — which
commit the node started from, which it ended on, whether it committed at
all, and which paths it touched.

Same contract as `symphony.utils.git_inspect`: **nothing here raises.** A
workspace that is not a repo, a git binary that is missing, a timeout, a
corrupt object — all degrade to `None`/empty. Provenance is evidence about
a node, not a precondition for it; failing a run that otherwise succeeded
because `git status` timed out would be strictly worse than recording an
incomplete story.

Deliberately two-dot (`before..after`), unlike `git_inspect._numstat`'s
three-dot. That module previews *a merge*, so it asks "what would this
branch add relative to the merge base". This one audits *a node*, so it
asks "what changed between these two states of one branch" — a merge base
would silently hide work the node did on top of a moved base.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_GIT_TIMEOUT_S = 15.0

# Enough to see what a node did; past this the diffstat is for a human
# reading `git show`, not for a JSON blob in SQLite.
MAX_CHANGED_PATHS = 500

# Heads are fed back into a git argument, so they must look like object
# names — never a `--flag`, never a ref expression.
_SHA_RE = re.compile(r"\A[0-9a-fA-F]{4,64}\Z")


@dataclass(frozen=True)
class GitSnapshot:
    head: str | None
    branch: str | None
    dirty: bool


@dataclass(frozen=True)
class GitProvenance:
    head_before: str | None
    head_after: str | None
    branch: str | None
    created_commits: bool
    changed_paths: tuple[str, ...]
    diffstat: dict[str, Any] | None


def _run_git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _stdout(proc: subprocess.CompletedProcess[str] | None) -> str:
    if proc is None or proc.returncode != 0:
        return ""
    return proc.stdout


def _is_object_name(value: str | None) -> bool:
    return bool(value) and _SHA_RE.match(value or "") is not None


def snapshot(workspace: Path) -> GitSnapshot:
    """Current HEAD / branch / dirtiness, all-None-ish when git says nothing."""
    head = _stdout(_run_git(workspace, "rev-parse", "HEAD")).strip() or None
    if head is not None and not _is_object_name(head):
        head = None
    branch = _stdout(_run_git(workspace, "symbolic-ref", "--short", "HEAD")).strip()
    porcelain = _run_git(workspace, "status", "--porcelain", "--untracked-files=all")
    # A failed `status` is reported as clean rather than dirty: an unknown
    # workspace state should not be recorded as evidence of a change.
    dirty = bool(_stdout(porcelain).strip())
    return GitSnapshot(head=head, branch=branch or None, dirty=dirty)


def _porcelain_paths(workspace: Path) -> tuple[str, ...]:
    """Paths git currently reports as changed, renames counted once.

    `-z` instead of the default quoting so paths with spaces or non-ASCII
    survive intact. Under `-z` a rename emits the destination record first
    and the source path as the *next* NUL-separated record, which is why
    the loop can skip forward.
    """
    proc = _run_git(workspace, "status", "--porcelain", "-z", "--untracked-files=all")
    raw = _stdout(proc)
    if not raw:
        return ()
    records = raw.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        status, path = record[:2], record[3:]
        if "R" in status or "C" in status:
            index += 1  # consume the origin path
        if path:
            paths.append(path)
    return tuple(paths[:MAX_CHANGED_PATHS])


def _numstat_two_dot(workspace: Path, before: str, after: str) -> dict[str, Any] | None:
    """`git diff --numstat before..after`, or None when git gave us nothing."""
    proc = _run_git(workspace, "diff", "--numstat", f"{before}..{after}")
    raw = _stdout(proc)
    if proc is None or proc.returncode != 0:
        return None
    files: list[dict[str, Any]] = []
    insertions = deletions = 0
    total_files = 0
    for line in raw.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        raw_ins, raw_del, path = parts
        binary = raw_ins == "-" or raw_del == "-"
        try:
            file_ins = 0 if binary else int(raw_ins)
            file_del = 0 if binary else int(raw_del)
        except ValueError:
            continue
        insertions += file_ins
        deletions += file_del
        total_files += 1
        if len(files) < MAX_CHANGED_PATHS:
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
            "files": total_files,
            "insertions": insertions,
            "deletions": deletions,
        },
        # Totals always cover every file; only the per-file list is capped.
        "truncated": total_files > len(files),
    }


def provenance_between(
    workspace: Path, before: GitSnapshot, after: GitSnapshot
) -> GitProvenance:
    """Fold two snapshots into the provenance record for one node.

    Two shapes, because a node produces work in two different ways:

    * it committed — `head_before != head_after`, and the diffstat between
      those two commits is the exact scope of what it did;
    * it edited without committing — the heads match but the tree is
      dirty, so `git status` supplies the paths. Uncommitted work is still
      work, and losing it from the ledger is how a resumed run quietly
      re-does (or clobbers) it.
    """
    created_commits = (
        before.head is not None
        and after.head is not None
        and before.head != after.head
    )
    branch = after.branch or before.branch

    if created_commits and _is_object_name(before.head) and _is_object_name(after.head):
        diffstat = _numstat_two_dot(workspace, str(before.head), str(after.head))
        changed = _changed_from_diffstat(diffstat)
        if not changed and after.dirty:
            changed = _porcelain_paths(workspace)
        return GitProvenance(
            head_before=before.head,
            head_after=after.head,
            branch=branch,
            created_commits=True,
            changed_paths=changed,
            diffstat=diffstat,
        )

    changed = _porcelain_paths(workspace) if after.dirty or before.dirty else ()
    return GitProvenance(
        head_before=before.head,
        head_after=after.head,
        branch=branch,
        created_commits=created_commits,
        changed_paths=changed,
        diffstat=None,
    )


def _changed_from_diffstat(diffstat: dict[str, Any] | None) -> tuple[str, ...]:
    if not diffstat:
        return ()
    entries = diffstat.get("files")
    if not isinstance(entries, list):
        return ()
    paths = [
        str(entry["path"])
        for entry in entries
        if isinstance(entry, dict) and entry.get("path")
    ]
    return tuple(paths[:MAX_CHANGED_PATHS])
