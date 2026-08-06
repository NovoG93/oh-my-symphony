"""Branch mutations the web UI's Git page can trigger.

Deliberately separate from `git_inspect`, which promises read-only queries
that degrade to empty results. Everything here changes local or remote
state, so failures are reported as a structured `GitOpResult` rather than
swallowed — the operator has to know whether a push or a delete happened.

Two hard rules live in this module rather than the route layer, because
they must hold for every caller:

* a push is never a force push, so a rejected non-fast-forward stays
  rejected instead of overwriting someone else's work;
* a branch delete uses `-d` unless the caller explicitly asks for `-D`.

Ref names must be validated by the caller (`webapi._BRANCH_RE`) before they
reach these functions; the leading-alphanumeric rule also rules out option
injection through refs that look like `--flags`.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_LOCAL_TIMEOUT_S = 10.0
# Pushes and `gh` calls go over the network on someone else's schedule.
_NETWORK_TIMEOUT_S = 120.0
_DETAIL_CHARS = 2000
_REMOTE_NAME_MAX = 100


@dataclass(frozen=True)
class GitOpResult:
    ok: bool
    status: str
    detail: str
    url: str = ""

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "status": self.status,
            "detail": self.detail,
        }
        if self.url:
            payload["url"] = self.url
        return payload


def _run(
    workflow_dir: Path, args: list[str], timeout: float
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args,
            cwd=str(workflow_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _detail(proc: subprocess.CompletedProcess[str]) -> str:
    blob = (proc.stderr or proc.stdout or "").strip()
    return blob[:_DETAIL_CHARS]


def is_valid_remote_name(remote: str) -> bool:
    """Remotes reach the command line, so keep them boring."""
    if not remote or len(remote) > _REMOTE_NAME_MAX:
        return False
    if remote[0] in "-.":
        return False
    return all(ch.isalnum() or ch in "._-" for ch in remote)


def list_remotes(workflow_dir: Path) -> list[str]:
    proc = _run(workflow_dir, ["git", "remote"], _LOCAL_TIMEOUT_S)
    if proc is None or proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def default_remote(workflow_dir: Path) -> str | None:
    remotes = list_remotes(workflow_dir)
    if not remotes:
        return None
    return "origin" if "origin" in remotes else remotes[0]


def gh_available() -> bool:
    return shutil.which("gh") is not None


def branch_on_remote(workflow_dir: Path, remote: str, branch: str) -> bool:
    """True when the remote already advertises `branch` (network call)."""
    proc = _run(
        workflow_dir,
        ["git", "ls-remote", "--heads", remote, f"refs/heads/{branch}"],
        _NETWORK_TIMEOUT_S,
    )
    return proc is not None and proc.returncode == 0 and bool(proc.stdout.strip())


def delete_branch(
    workflow_dir: Path, branch: str, *, force: bool = False
) -> GitOpResult:
    """Delete a local branch; `-d` refuses to drop unmerged work."""
    proc = _run(
        workflow_dir,
        ["git", "branch", "-D" if force else "-d", branch],
        _LOCAL_TIMEOUT_S,
    )
    if proc is None:
        return GitOpResult(False, "git_failed", "git branch delete did not run")
    if proc.returncode != 0:
        detail = _detail(proc)
        lowered = detail.lower()
        if "not fully merged" in lowered:
            return GitOpResult(False, "not_merged", detail)
        if "checked out" in lowered or "used by worktree" in lowered:
            return GitOpResult(False, "checked_out", detail)
        return GitOpResult(False, "git_failed", detail)
    return GitOpResult(True, "deleted", (proc.stdout or "").strip()[:_DETAIL_CHARS])


def push_branch(
    workflow_dir: Path, branch: str, remote: str, *, set_upstream: bool = True
) -> GitOpResult:
    """Push one branch. Never forced — a rejected push stays rejected."""
    args = ["git", "push"]
    if set_upstream:
        args.append("--set-upstream")
    args += [remote, f"refs/heads/{branch}:refs/heads/{branch}"]
    proc = _run(workflow_dir, args, _NETWORK_TIMEOUT_S)
    if proc is None:
        return GitOpResult(
            False, "timeout", f"git push to {remote} timed out or could not start"
        )
    if proc.returncode != 0:
        detail = _detail(proc)
        lowered = detail.lower()
        if "non-fast-forward" in lowered or "rejected" in lowered:
            return GitOpResult(False, "rejected", detail)
        if "authentication" in lowered or "permission denied" in lowered:
            return GitOpResult(False, "auth_failed", detail)
        return GitOpResult(False, "git_failed", detail)
    return GitOpResult(True, "pushed", _detail(proc))


def create_pull_request(
    workflow_dir: Path,
    branch: str,
    target: str,
    title: str,
    body: str,
) -> GitOpResult:
    """Open a PR through the `gh` CLI; the branch must already be pushed."""
    if not gh_available():
        return GitOpResult(
            False,
            "gh_unavailable",
            "the GitHub CLI (gh) is not installed or not on PATH",
        )
    proc = _run(
        workflow_dir,
        [
            "gh", "pr", "create",
            "--head", branch,
            "--base", target,
            "--title", title,
            "--body", body,
        ],
        _NETWORK_TIMEOUT_S,
    )
    if proc is None:
        return GitOpResult(False, "timeout", "gh pr create timed out or could not start")
    if proc.returncode != 0:
        detail = _detail(proc)
        lowered = detail.lower()
        if "already exists" in lowered:
            return GitOpResult(False, "pr_exists", detail, url=_first_url(detail))
        if "known github host" in lowered:
            # Common with a local or self-hosted remote — not an auth problem.
            return GitOpResult(False, "not_a_github_remote", detail)
        if "auth" in lowered and "login" in lowered:
            return GitOpResult(False, "gh_auth_failed", detail)
        return GitOpResult(False, "gh_failed", detail)
    stdout = (proc.stdout or "").strip()
    return GitOpResult(True, "created", stdout[:_DETAIL_CHARS], url=_first_url(stdout))


def _first_url(text: str) -> str:
    for token in text.split():
        if token.startswith("https://") or token.startswith("http://"):
            return token.rstrip(".,")
    return ""
