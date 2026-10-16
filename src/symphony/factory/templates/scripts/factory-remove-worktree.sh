#!/usr/bin/env bash
set -euo pipefail

workspace="$PWD"
if [ "${OS:-}" = "Windows_NT" ]; then
  workspace="$(cygpath -w "$workspace")"
fi
cd "${SYMPHONY_WORKFLOW_DIR:?SYMPHONY_WORKFLOW_DIR not set}"
exec "${SYMPHONY_PYTHON:?SYMPHONY_PYTHON not set}" - "$workspace" <<'PY'
import os
from pathlib import Path
import subprocess
import sys

host = Path(os.environ["SYMPHONY_WORKFLOW_DIR"]).resolve()
root = Path(os.environ["SYMPHONY_WORKSPACE_ROOT"]).resolve()
workspace = Path(sys.argv[1]).resolve()
workspace.relative_to(root)
if workspace in {host, root} or workspace.parent != root:
    raise RuntimeError("factory cleanup refuses the host or workspace root")


def common_dir(path):
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-common-dir"],
        check=True, capture_output=True, text=True,
    )
    return (path / result.stdout.strip()).resolve()


if common_dir(workspace) != common_dir(host):
    raise RuntimeError("factory cleanup workspace belongs to a different repository")
board = workspace / "kanban"
if board.exists() or board.is_symlink() or board.is_junction():
    if not (board.is_symlink() or board.is_junction()):
        raise RuntimeError("factory cleanup refuses a real kanban directory")
    if board.resolve() != (host / "kanban").resolve():
        raise RuntimeError("factory cleanup board link does not target the host board")
    # Native Windows rmdir removes the junction entry without traversing its target.
    if board.is_junction():
        board.rmdir()
    else:
        board.unlink()
# Git for Windows may traverse directory junctions even when Python would not.
for directory, children, _ in os.walk(workspace, topdown=True, followlinks=False):
    for name in list(children):
        child = Path(directory) / name
        if child.is_junction():
            child.rmdir()
            children.remove(name)
result = subprocess.run(
    ["git", "-C", str(host), "worktree", "remove", "--force", str(workspace)],
    cwd=host, check=False,
)
if result.returncode:
    listing = subprocess.run(
        ["git", "-C", str(host), "worktree", "list", "--porcelain", "-z"],
        check=True, capture_output=True, text=True,
    ).stdout
    registered = any(
        Path(field[len("worktree "):]).resolve() == workspace
        for field in listing.split("\0") if field.startswith("worktree ")
    )
    empty = not workspace.exists() or not any(workspace.iterdir())
    if os.name == "nt" and result.returncode == 255 and empty and not registered:
        print(
            "factory cleanup: Git removed the worktree contents and registration; "
            "the hook holds its empty directory until exit, then Symphony removes it",
            file=sys.stderr,
        )
    else:
        raise subprocess.CalledProcessError(result.returncode, result.args)
PY
