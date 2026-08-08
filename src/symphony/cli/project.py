"""Manage independent Symphony projects through a central registry."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .. import service
from ..projects import (
    Project,
    ProjectError,
    ProjectRegistry,
    validate_id as _validate_id,
    validate_port as _validate_port,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9999


def _run_git(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ProjectError(f"cannot run git: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ProjectError(detail or f"git {' '.join(args)} failed in {cwd}")
    return result.stdout.strip()


def git_toplevel(candidate: Path) -> Path:
    path = candidate.expanduser().resolve()
    cwd = path if path.is_dir() else path.parent
    return Path(_run_git(cwd, "rev-parse", "--show-toplevel")).resolve()


def git_common_dir(repo: Path) -> Path:
    raw = Path(_run_git(repo, "rev-parse", "--git-common-dir"))
    return (raw if raw.is_absolute() else repo / raw).resolve()


def source_checkout() -> Path:
    """Return the git checkout containing this installed CLI's source."""
    try:
        return git_toplevel(Path(__file__))
    except ProjectError as exc:
        raise ProjectError(
            "cannot locate the oh-my-symphony source checkout; run this command "
            "from an editable/source installation"
        ) from exc


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise ProjectError("name must contain at least one letter or digit")
    return slug[:64].rstrip("-")


def _next_port(projects: list[Project], host: str) -> int:
    used = {project.port for project in projects}
    for port in range(DEFAULT_PORT, 65536):
        if port not in used:
            return port
    raise ProjectError(f"no available registry port for host {host}")


def _resolve_workflow(repo: Path, raw: str) -> Path:
    value = Path(raw).expanduser()
    workflow = (value if value.is_absolute() else repo / value).resolve()
    if not workflow.is_file():
        raise ProjectError(f"workflow file not found: {workflow}")
    return workflow


def _new_project(
    *,
    registry: ProjectRegistry,
    repo: Path,
    name: str,
    project_id: str | None,
    workflow: str,
    host: str,
    port: int | None,
) -> Project:
    projects = registry.load()
    resolved_id = project_id or _slug(name)
    _validate_id(resolved_id)
    resolved_port = _next_port(projects, host) if port is None else port
    _validate_port(resolved_port)
    source = source_checkout()
    if git_common_dir(repo) == git_common_dir(source):
        raise ProjectError(
            f"refusing to register protected Symphony source checkout: {source}"
        )
    project = Project(
        id=resolved_id,
        name=name,
        git_repo=str(repo.resolve()),
        workflow=str(_resolve_workflow(repo, workflow)),
        host=host,
        port=resolved_port,
    )
    registry.add(project)
    return project


def bootstrap_project(source: Path, target: Path, workflow: str = "WORKFLOW.md") -> None:
    """Copy the required file-tracker operator bundle into a new repository."""
    required_files = {
        "tui-open.sh": "tui-open.sh",
        "tui-open.bat": "tui-open.bat",
        "WORKFLOW.file.example.md": workflow,
        "scripts/symphony-setup-worktree.sh": "scripts/symphony-setup-worktree.sh",
        "AGENTS.md": "AGENTS.md",
        "GEMINI.md": "GEMINI.md",
    }
    required_dirs = {
        "docs/symphony-prompts": "docs/symphony-prompts",
        "skills": "skills",
    }
    missing = [name for name in [*required_files, *required_dirs] if not (source / name).exists()]
    if missing:
        raise ProjectError(f"source checkout is missing bootstrap files: {', '.join(missing)}")
    for source_name, target_name in required_files.items():
        destination = target / target_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / source_name, destination)
    for source_name, target_name in required_dirs.items():
        shutil.copytree(source / source_name, target / target_name)
    (target / "kanban").mkdir()
    (target / "kanban" / ".gitkeep").touch()
    (target / ".claude" / "skills").mkdir(parents=True)
    (target / ".claude" / "skills" / "symphony-skill").symlink_to(
        Path("../../skills/symphony-skill"), target_is_directory=True
    )
    if os.name != "nt":
        for relative in ("tui-open.sh", "scripts/symphony-setup-worktree.sh"):
            path = target / relative
            path.chmod(path.stat().st_mode | 0o111)


def cmd_list(args: argparse.Namespace) -> int:
    projects = ProjectRegistry().load()
    if not projects:
        print("(no projects)")
        return 0
    print("ID  NAME  ENDPOINT  REPOSITORY  WORKFLOW")
    for project in projects:
        print(
            f"{project.id}  {project.name}  {project.host}:{project.port}  "
            f"{project.git_repo}  {project.workflow}"
        )
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    repo = git_toplevel(Path(args.repo))
    name = args.name or repo.name
    project = _new_project(
        registry=ProjectRegistry(),
        repo=repo,
        name=name,
        project_id=args.id,
        workflow=args.workflow,
        host=args.host,
        port=args.port,
    )
    print(f"added project {project.id} ({project.git_repo}) at {project.host}:{project.port}")
    return 0


def _init_git_repo(target: Path) -> None:
    try:
        result = subprocess.run(
            ["git", "init", "-b", "main", str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ProjectError(f"cannot run git: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ProjectError(detail or f"git init failed for {target}")


def cmd_create(args: argparse.Namespace) -> int:
    source = source_checkout()
    project_id = args.id or _slug(args.name)
    _validate_id(project_id)
    target = (
        Path(args.path).expanduser().resolve()
        if args.path
        else (source.parent / project_id).resolve()
    )
    if target.exists():
        raise ProjectError(f"new project path already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        _init_git_repo(target)
        bootstrap_project(source, target, workflow=args.workflow)
        _run_git(target, "add", "-A")
        _run_git(
            target,
            "-c",
            "user.name=Symphony",
            "-c",
            "user.email=symphony@local",
            "commit",
            "-m",
            "chore: initialize Symphony project",
        )
        project = _new_project(
            registry=ProjectRegistry(),
            repo=git_toplevel(target),
            name=args.name,
            project_id=project_id,
            workflow=args.workflow,
            host=args.host,
            port=args.port,
        )
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    print(f"created project {project.id} at {project.git_repo} ({project.host}:{project.port})")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    project = ProjectRegistry().remove(args.id)
    print(f"removed project {project.id}; repository left untouched at {project.git_repo}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    project = ProjectRegistry().get(args.id)
    argv = [
        "start",
        project.workflow,
        "--host",
        project.host,
        "--port",
        str(project.port),
    ]
    if args.replace:
        argv.append("--replace")
    if args.skip_doctor:
        argv.append("--skip-doctor")
    return service.main(argv)


def cmd_stop(args: argparse.Namespace) -> int:
    project = ProjectRegistry().get(args.id)
    argv = ["stop", project.workflow, "--timeout", str(args.timeout)]
    if args.force:
        argv.append("--force")
    return service.main(argv)


def _status(project: Project) -> int:
    return service.main(["status", project.workflow, "--port", str(project.port)])


def cmd_status(args: argparse.Namespace) -> int:
    registry = ProjectRegistry()
    if args.id is not None:
        return _status(registry.get(args.id))
    projects = registry.load()
    if not projects:
        print("(no projects)")
        return 0
    result = 0
    for project in projects:
        print(f"[{project.id}] {project.name}")
        result = max(result, _status(project))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="symphony project",
        description="Register and run independent Symphony project services.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", aliases=["ls"], help="list registered projects").set_defaults(func=cmd_list)

    def add_registration_options(command: argparse.ArgumentParser) -> None:
        command.add_argument("--id", default=None, help="stable lowercase project id")
        command.add_argument("--workflow", default="WORKFLOW.md", help="workflow path relative to repository")
        command.add_argument("--host", default=DEFAULT_HOST)
        command.add_argument("--port", type=int, default=None, help="service port (default: next unused from 9999)")

    add = sub.add_parser("add", help="register an existing git repository")
    add.add_argument("repo", help="path inside the existing repository")
    add.add_argument("--name", default=None)
    add_registration_options(add)
    add.set_defaults(func=cmd_add)

    create = sub.add_parser("create", help="create and bootstrap a sibling git repository")
    create.add_argument("name", help="display name")
    create.add_argument("--path", default=None, help="target path (default: sibling of Symphony source)")
    add_registration_options(create)
    create.set_defaults(func=cmd_create)

    remove = sub.add_parser("remove", aliases=["rm"], help="unregister without deleting files")
    remove.add_argument("id")
    remove.set_defaults(func=cmd_remove)

    start = sub.add_parser("start", help="start a project's managed service")
    start.add_argument("id")
    start.add_argument("--replace", action="store_true")
    start.add_argument("--skip-doctor", action="store_true")
    start.set_defaults(func=cmd_start)

    stop = sub.add_parser("stop", help="stop a project's managed service")
    stop.add_argument("id")
    stop.add_argument("--timeout", type=float, default=10.0)
    stop.add_argument("--force", action="store_true")
    stop.set_defaults(func=cmd_stop)

    status = sub.add_parser("status", help="show one or every project's service status")
    status.add_argument("id", nargs="?", default=None)
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ProjectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
