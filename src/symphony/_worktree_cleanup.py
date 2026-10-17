"""Private cleanup shared by the shipped factory and advanced workflow hooks."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def _common_dir(path: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--git-common-dir"],
        check=True, stdout=subprocess.PIPE, text=True,
    )
    return (path / result.stdout.strip()).resolve()


def _registered(host: Path, workspace: Path) -> bool:
    listing = subprocess.run(
        ["git", "-C", str(host), "worktree", "list", "--porcelain", "-z"],
        check=True, stdout=subprocess.PIPE, text=True,
    ).stdout
    return any(
        Path(field[len("worktree "):]).resolve() == workspace
        for field in listing.split("\0") if field.startswith("worktree ")
    )


def cleanup(workspace: Path, *, restore_board_tracking: bool = False) -> None:
    host = Path(os.environ["SYMPHONY_WORKFLOW_DIR"]).resolve()
    root = Path(os.environ["SYMPHONY_WORKSPACE_ROOT"]).resolve()
    if workspace.is_symlink() or workspace.is_junction():
        raise RuntimeError("cleanup refuses a linked worktree root")
    workspace = workspace.resolve()
    if workspace in {host, root} or workspace.parent != root:
        raise RuntimeError("cleanup refuses the host, workspace root, or an outside path")

    registered = _registered(host, workspace)
    if not workspace.exists():
        if workspace.is_symlink() or workspace.is_junction() or registered:
            raise RuntimeError("cleanup refuses a missing registered worktree or dangling link")
        return
    if not registered or _common_dir(workspace) != _common_dir(host):
        raise RuntimeError("cleanup workspace is not a registered worktree of the host repository")

    names = (os.environ.get("SYMPHONY_BOARD_ROOT_NAME") or "kanban").split()
    boards: list[Path] = []
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or relative.drive or ".." in relative.parts or not relative.parts:
            raise RuntimeError("cleanup refuses an invalid board path")
        board = workspace / relative
        if board == workspace or not board.parent.resolve().is_relative_to(workspace):
            raise RuntimeError("cleanup refuses a board path outside the worktree")
        if board.exists() or board.is_symlink() or board.is_junction():
            if not (board.is_symlink() or board.is_junction()):
                raise RuntimeError("cleanup refuses a real board directory")
            source = host / relative
            if not board.exists() or not source.is_dir() or board.resolve() != source.resolve():
                raise RuntimeError("cleanup board link does not target the host board")
            if board not in boards:
                boards.append(board)

    # Scan without entering junctions, before mutating any of the validated entries.
    junctions: list[Path] = []

    def scan_error(error: OSError) -> None:
        raise error

    for directory, children, _ in os.walk(
        workspace, topdown=True, followlinks=False, onerror=scan_error,
    ):
        for name in list(children):
            child = Path(directory) / name
            if child.is_junction():
                if child not in boards:
                    junctions.append(child)
                children.remove(name)

    if restore_board_tracking:
        issue_id = os.environ.get("SYMPHONY_ISSUE_ID") or workspace.name
        for name in names:
            subprocess.run(
                ["git", "-C", str(host), "update-index", "--no-skip-worktree", "--",
                 f"{name}/{issue_id}.md"],
                check=False, stderr=subprocess.DEVNULL,
            )
    for board in boards:
        if board.is_junction():
            board.rmdir()
        else:
            board.unlink()
    # Git for Windows may traverse directory junctions even when Python would not.
    for junction in junctions:
        junction.rmdir()
    result = subprocess.run(
        ["git", "-C", str(host), "worktree", "remove", "--force", str(workspace)],
        cwd=host, check=False,
    )
    if result.returncode:
        registered = _registered(host, workspace)
        empty = not workspace.exists() or not any(workspace.iterdir())
        if os.name == "nt" and result.returncode == 255 and empty and not registered:
            print(
                "cleanup: Git removed the worktree contents and registration; "
                "the hook holds its empty directory until exit, then Symphony removes it",
                file=sys.stderr,
            )
        else:
            raise subprocess.CalledProcessError(result.returncode, result.args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--restore-board-tracking", action="store_true")
    args = parser.parse_args()
    cleanup(args.workspace, restore_board_tracking=args.restore_board_tracking)


if __name__ == "__main__":
    main()
