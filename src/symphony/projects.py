"""Persistent registry of independent Symphony projects."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from .errors import SymphonyError
from .workflow import build_service_config, load_workflow

REGISTRY_VERSION = 1
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_LOCK_DEPTH = threading.local()


def _thread_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve()))
    with _LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _exclusive_path_lock(path: Path) -> Iterator[None]:
    """Reentrant thread + cross-process exclusive lock for one registry path."""
    key = os.path.normcase(str(path.resolve()))
    lock = _thread_lock(path)
    with lock:
        depths = getattr(_LOCK_DEPTH, "depths", None)
        if depths is None:
            depths = {}
            _LOCK_DEPTH.depths = depths
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(f".{path.name}.lock")
        handle = lock_path.open("a+b")
        try:
            if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            depths[key] = 1
            try:
                yield
            finally:
                depths.pop(key, None)
                if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class ProjectError(RuntimeError):
    """An operator-correctable project registry error."""


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    git_repo: str
    workflow: str
    host: str
    port: int

    @property
    def repo(self) -> str:
        """Compatibility alias for consumers that call the repository `repo`."""
        return self.git_repo

    @classmethod
    def from_json(cls, value: Any) -> "Project":
        if not isinstance(value, dict):
            raise ProjectError("project entries must be JSON objects")
        try:
            project = cls(
                id=str(value["id"]),
                name=str(value["name"]),
                git_repo=str(value["git_repo"]),
                workflow=str(value["workflow"]),
                host=str(value["host"]),
                port=int(value["port"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectError(f"invalid project entry: {exc}") from exc
        validate_id(project.id)
        validate_port(project.port)
        return project


def projects_file() -> Path:
    override = os.environ.get("SYMPHONY_PROJECTS_FILE")
    return Path(override).expanduser() if override else Path.home() / ".symphony" / "projects.json"


def validate_id(project_id: str) -> None:
    if not _ID_RE.fullmatch(project_id):
        raise ProjectError(
            f"invalid project id {project_id!r}; use lowercase letters, digits, '_' or '-'"
        )


def validate_port(port: int) -> None:
    if not 1 <= port <= 65535:
        raise ProjectError(f"invalid port {port}; expected 1..65535")


def _repo_key(value: str) -> str:
    """Canonical Git identity; linked worktrees count as one project repo."""
    repo = Path(value).expanduser().resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return os.path.normcase(str(repo))
    if result.returncode == 0 and result.stdout.strip():
        common = Path(result.stdout.strip())
        if not common.is_absolute():
            common = repo / common
        repo = common.resolve()
    return os.path.normcase(str(repo))


def validate_unique(projects: list[Project]) -> None:
    seen_ids: set[str] = set()
    seen_repos: set[str] = set()
    seen_ports: set[int] = set()
    for project in projects:
        if project.id in seen_ids:
            raise ProjectError(f"duplicate project id {project.id!r}")
        seen_ids.add(project.id)
        repo_key = _repo_key(project.git_repo)
        if repo_key in seen_repos:
            raise ProjectError(f"repository already registered: {project.git_repo}")
        seen_repos.add(repo_key)
        if project.port in seen_ports:
            raise ProjectError(f"service port already registered: {project.port}")
        seen_ports.add(project.port)


class ProjectRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or projects_file()).expanduser().resolve()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with _exclusive_path_lock(self.path):
            yield

    def _load_unlocked(self) -> list[Project]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectError(f"cannot read project registry {self.path}: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("version") != REGISTRY_VERSION:
            raise ProjectError(
                f"unsupported project registry format in {self.path}; expected version {REGISTRY_VERSION}"
            )
        values = raw.get("projects")
        if not isinstance(values, list):
            raise ProjectError(f"invalid project registry {self.path}: projects must be a list")
        projects = [Project.from_json(value) for value in values]
        validate_unique(projects)
        return projects

    def load(self) -> list[Project]:
        with self.transaction():
            return self._load_unlocked()

    def list(self) -> list[Project]:
        return self.load()

    def _save_unlocked(self, projects: list[Project]) -> None:
        validate_unique(projects)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": REGISTRY_VERSION, "projects": [asdict(p) for p in projects]}
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(self.path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def save(self, projects: list[Project]) -> None:
        with self.transaction():
            self._save_unlocked(projects)

    def get(self, project_id: str) -> Project:
        project = next((item for item in self.load() if item.id == project_id), None)
        if project is None:
            raise ProjectError(f"unknown project {project_id!r}; run `symphony project list`")
        return project

    def add(self, project: Project) -> None:
        with self.transaction():
            projects = self._load_unlocked()
            projects.append(project)
            self._save_unlocked(projects)

    def remove(self, project_id: str) -> Project:
        with self.transaction():
            projects = self._load_unlocked()
            project = next((item for item in projects if item.id == project_id), None)
            if project is None:
                raise ProjectError(f"unknown project {project_id!r}; run `symphony project list`")
            self._save_unlocked([item for item in projects if item.id != project_id])
            return project

    def status(self, project_id: str):
        """Return managed-service status for hub-compatible registry consumers."""
        from . import service

        project = self.get(project_id)
        return service.service_status(project.workflow, port=project.port)

    def start(self, project_id: str) -> int:
        from . import service

        project = self.get(project_id)
        result = service.main(
            ["start", project.workflow, "--host", project.host, "--port", str(project.port)]
        )
        if result != 0:
            return result
        host = "127.0.0.1" if project.host in {"", "0.0.0.0", "::", "[::]"} else project.host
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, project.port), timeout=0.2):
                    return 0
            except OSError:
                time.sleep(0.1)
        return 1

    def stop(self, project_id: str) -> int:
        from . import service

        project = self.get(project_id)
        return service.main(["stop", project.workflow])


# Files required to operate a standalone Symphony project.  Directory bundles
# are merged recursively so adopting a repository never replaces local files.
_BUNDLE_FILES = {
    "tui-open.sh": "tui-open.sh",
    "tui-open.bat": "tui-open.bat",
    "WORKFLOW.file.example.md": "WORKFLOW.md",
    "scripts/symphony-setup-worktree.sh": "scripts/symphony-setup-worktree.sh",
    "AGENTS.md": "AGENTS.md",
    "GEMINI.md": "GEMINI.md",
}
_BUNDLE_DIRS = {
    "docs/symphony-prompts": "docs/symphony-prompts",
    "skills": "skills",
}


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


def _git_toplevel(candidate: Path) -> Path | None:
    try:
        return Path(_run_git(candidate, "rev-parse", "--show-toplevel")).resolve()
    except ProjectError:
        return None


def canonical_project_repo(candidate: str | Path) -> Path:
    """Return an existing Git top-level, otherwise the resolved candidate path."""
    path = Path(candidate).expanduser().resolve()
    if path.exists() and path.is_dir():
        return _git_toplevel(path) or path
    return path


def _git_common_dir(repo: Path) -> Path:
    raw = Path(_run_git(repo, "rev-parse", "--git-common-dir"))
    return (raw if raw.is_absolute() else repo / raw).resolve()


def source_checkout() -> Path:
    """Return the canonical Git checkout containing this installed package."""
    checkout = _git_toplevel(Path(__file__).resolve().parent)
    if checkout is None:
        raise ProjectError(
            "cannot locate the oh-my-symphony source checkout; run this command "
            "from an editable/source installation"
        )
    return checkout


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not slug:
        raise ProjectError("name must contain at least one letter or digit")
    return slug[:64].rstrip("-")


def _next_port(projects: list[Project], host: str) -> int:
    del host  # Ports are process-wide registry identities, independent of bind host.
    used = {project.port for project in projects}
    for port in range(9999, 65536):
        if port not in used:
            return port
    raise ProjectError("no available registry port")


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _workflow_path(repo: Path, value: str) -> Path:
    raw = Path(value).expanduser()
    if raw.is_absolute():
        raise ProjectError(f"workflow path must be relative to project repository: {value}")
    workflow = (repo / raw).resolve()
    if not _inside(workflow, repo) or workflow == repo:
        raise ProjectError(f"workflow path escapes project repository: {value}")
    return workflow


def _validate_source_bundle(source: Path) -> None:
    missing: list[str] = []
    conflicts: list[str] = []
    for source_name in _BUNDLE_FILES:
        path = source / source_name
        if not path.exists():
            missing.append(source_name)
        elif not path.is_file():
            conflicts.append(f"{source_name} is not a file")
    for source_name in _BUNDLE_DIRS:
        path = source / source_name
        if not path.exists():
            missing.append(source_name)
        elif not path.is_dir() or path.is_symlink():
            conflicts.append(f"{source_name} is not a directory")
    if missing:
        raise ProjectError(f"source checkout is missing bootstrap files: {', '.join(missing)}")
    if conflicts:
        raise ProjectError(f"invalid source bootstrap bundle: {', '.join(conflicts)}")


def _ensure_directory(path: Path, created_dirs: list[Path]) -> None:
    """Create absent parents, rejecting every file/symlink directory conflict."""
    missing: list[Path] = []
    cursor = path
    while not cursor.exists() and not cursor.is_symlink():
        missing.append(cursor)
        cursor = cursor.parent
    if cursor.is_symlink() or not cursor.is_dir():
        raise ProjectError(f"project bundle path requires a directory: {cursor}")
    for item in reversed(missing):
        item.mkdir()
        created_dirs.append(item)


def _copy_missing_file(
    source: Path,
    destination: Path,
    created_files: list[Path],
    created_dirs: list[Path],
    tracked_paths: set[Path],
) -> None:
    if destination in tracked_paths and not destination.exists():
        # Preserve an unrelated staged/unstaged deletion instead of restoring it.
        return
    if destination.exists() or destination.is_symlink():
        if destination.is_dir() and not destination.is_symlink():
            raise ProjectError(f"project bundle path requires a file: {destination}")
        return
    _ensure_directory(destination.parent, created_dirs)
    shutil.copy2(source, destination)
    created_files.append(destination)


def _merge_missing_tree(
    source: Path,
    destination: Path,
    created_files: list[Path],
    created_dirs: list[Path],
    tracked_paths: set[Path],
) -> None:
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir():
            raise ProjectError(f"project bundle path requires a directory: {destination}")
    else:
        _ensure_directory(destination, created_dirs)
    for child in source.iterdir():
        if (
            child.name in {".DS_Store", "__pycache__"}
            or child.suffix in {".pyc", ".pyo"}
        ):
            continue
        target = destination / child.name
        if child.is_dir() and not child.is_symlink():
            _merge_missing_tree(child, target, created_files, created_dirs, tracked_paths)
        elif child.is_file():
            _copy_missing_file(child, target, created_files, created_dirs, tracked_paths)
        else:
            raise ProjectError(f"unsupported source bundle entry: {child}")


def _validate_target_conflicts(repo: Path, workflow_path: Path) -> None:
    """Validate the complete merge before writing anything to an existing path."""
    mappings = dict(_BUNDLE_FILES)
    mappings["WORKFLOW.file.example.md"] = str(workflow_path.relative_to(repo))
    for source_name, target_name in mappings.items():
        target = repo / target_name
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                raise ProjectError(f"project bundle path requires a file: {target}")
        parent = target.parent
        while parent != repo:
            if parent.exists() or parent.is_symlink():
                if parent.is_symlink() or not parent.is_dir():
                    raise ProjectError(f"project bundle path requires a directory: {parent}")
                break
            parent = parent.parent
    for target_name in _BUNDLE_DIRS.values():
        target = repo / target_name
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_dir():
                raise ProjectError(f"project bundle path requires a directory: {target}")
    # These operator-owned additions have fixed shapes as well.
    for target in (repo / "kanban", repo / ".claude", repo / ".claude/skills"):
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_dir():
                raise ProjectError(f"project bundle path requires a directory: {target}")
    skill_link = repo / ".claude/skills/symphony-skill"
    if skill_link.exists() or skill_link.is_symlink():
        if not skill_link.is_symlink() and not skill_link.is_dir():
            raise ProjectError(f"project bundle path requires a directory: {skill_link}")
        # Existing symlinks and real skill directories are never overwritten.


def _bootstrap_missing(
    source: Path,
    repo: Path,
    workflow_path: Path,
    project_id: str,
    created_files: list[Path],
    created_dirs: list[Path],
    tracked_paths: set[Path],
) -> None:
    mappings = dict(_BUNDLE_FILES)
    mappings["WORKFLOW.file.example.md"] = str(workflow_path.relative_to(repo))
    for source_name, target_name in mappings.items():
        destination = repo / target_name
        _copy_missing_file(
            source / source_name, destination, created_files, created_dirs, tracked_paths
        )
        if destination == workflow_path and destination in created_files:
            content = destination.read_text(encoding="utf-8")
            content = re.sub(
                r"(?m)^(\s*root:\s*)~/symphony_workspaces\s*$",
                rf"\1~/symphony_workspaces/{project_id}",
                content,
                count=1,
            )
            destination.write_text(content, encoding="utf-8")
        if os.name != "nt" and target_name in {
            "tui-open.sh",
            "scripts/symphony-setup-worktree.sh",
        } and destination in created_files:
            destination.chmod(destination.stat().st_mode | 0o111)
    for source_name, target_name in _BUNDLE_DIRS.items():
        _merge_missing_tree(
            source / source_name,
            repo / target_name,
            created_files,
            created_dirs,
            tracked_paths,
        )
    _ensure_directory(repo / "kanban", created_dirs)
    _copy_missing_file(
        source / "WORKFLOW.file.example.md",
        repo / "kanban/.gitkeep",
        created_files,
        created_dirs,
        tracked_paths,
    )
    # .gitkeep must be empty, not a copy of the workflow template.
    gitkeep = repo / "kanban/.gitkeep"
    if gitkeep in created_files:
        gitkeep.write_text("", encoding="utf-8")
    skills_dir = repo / ".claude/skills"
    _ensure_directory(skills_dir, created_dirs)
    link = skills_dir / "symphony-skill"
    if link not in tracked_paths and not link.exists() and not link.is_symlink():
        link.symlink_to(Path("../../skills/symphony-skill"), target_is_directory=True)
        created_files.append(link)


def _workflow_resources(
    workflow_path: Path, *, strict: bool
) -> tuple[Path | None, Path | None]:
    """Resolve runtime-owned paths through the canonical workflow builder."""
    try:
        config = build_service_config(load_workflow(workflow_path))
    except (SymphonyError, OSError, UnicodeError) as exc:
        if strict:
            raise ProjectError(f"cannot resolve workflow resources: {exc}") from exc
        return None, None
    board = config.tracker.board_root if config.tracker.kind == "file" else None
    return board.resolve() if board is not None else None, config.workspace_root.resolve()


def _validate_resource_ownership(
    workflow_path: Path, repo: Path, projects: list[Project]
) -> None:
    board, workspace = _workflow_resources(workflow_path, strict=True)
    if board is not None and not _inside(board, repo):
        raise ProjectError(f"file tracker board root escapes project repository: {board}")
    for existing in projects:
        other_board, other_workspace = _workflow_resources(
            Path(existing.workflow), strict=False
        )
        if (
            board is not None
            and other_board is not None
            and os.path.normcase(str(other_board)) == os.path.normcase(str(board))
        ):
            raise ProjectError(
                f"file tracker board already owned by project {existing.id!r}: {board}"
            )
        if (
            workspace is not None
            and other_workspace is not None
            and os.path.normcase(str(other_workspace))
            == os.path.normcase(str(workspace))
        ):
            raise ProjectError(
                f"workspace root already owned by project {existing.id!r}: {workspace}"
            )


def _cleanup_created(created_files: list[Path], created_dirs: list[Path]) -> None:
    for path in reversed(created_files):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    for path in reversed(created_dirs):
        try:
            path.rmdir()
        except (FileNotFoundError, OSError):
            pass


def _registered_repo(projects: list[Project], repo: Path) -> Project | None:
    key = _repo_key(str(repo))
    return next((project for project in projects if _repo_key(project.git_repo) == key), None)


def _create_or_adopt_project_locked(
    target: str | Path,
    *,
    source: str | Path,
    name: str | None = None,
    project_id: str | None = None,
    workflow: str = "WORKFLOW.md",
    host: str = "127.0.0.1",
    port: int | None = None,
    registry: ProjectRegistry | None = None,
) -> Project:
    """Create or safely adopt a repository and register it.

    The operation merges only absent Symphony bundle files, commits only files
    it created, and returns an existing record when the repository is already
    registered (the stored record remains authoritative over newly requested
    display metadata). Callers supply the source checkout so CLI and web entry
    points share identical filesystem and Git semantics.
    """
    registry = registry or ProjectRegistry()
    source_path = Path(source).expanduser().resolve()
    candidate = Path(target).expanduser().resolve()
    _validate_source_bundle(source_path)
    if candidate.exists() and not candidate.is_dir():
        raise ProjectError(f"project path is not a directory: {candidate}")

    candidate_existed = candidate.exists()
    existing_toplevel = _git_toplevel(candidate) if candidate_existed else None
    repo = existing_toplevel or candidate

    source_repo = _git_toplevel(source_path)
    if source_repo is None:
        raise ProjectError(f"source checkout is not a Git repository: {source_path}")
    protected = False
    if existing_toplevel is not None:
        protected = _git_common_dir(existing_toplevel) == _git_common_dir(source_repo)
    else:
        ancestor = candidate.parent
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        ancestor_repo = _git_toplevel(ancestor) if ancestor.is_dir() else None
        protected = _inside(candidate, source_repo) or (
            ancestor_repo is not None
            and _git_common_dir(ancestor_repo) == _git_common_dir(source_repo)
        )
    if protected:
        raise ProjectError(f"refusing to register protected Symphony source checkout: {source_repo}")

    workflow_path = _workflow_path(repo, workflow)
    projects = registry.load()
    registered = _registered_repo(projects, repo)
    if registered is not None:
        return registered

    resolved_name = name or repo.name
    resolved_id = project_id or _slug(resolved_name)
    validate_id(resolved_id)
    resolved_port = _next_port(projects, host) if port is None else port
    validate_port(resolved_port)
    if any(item.id == resolved_id for item in projects):
        raise ProjectError(f"duplicate project id {resolved_id!r}")
    if any(item.port == resolved_port for item in projects):
        raise ProjectError(f"service port already registered: {resolved_port}")

    if candidate_existed:
        _validate_target_conflicts(repo, workflow_path)

    created_root = False
    created_git = False
    created_files: list[Path] = []
    created_dirs: list[Path] = []
    committed = False
    try:
        if not repo.exists():
            repo.mkdir(parents=True)
            created_root = True
        if existing_toplevel is None:
            _run_git(repo, "init", "-b", "main")
            created_git = True
        tracked_raw = _run_git(repo, "ls-files", "-z")
        tracked_paths = {
            repo / relative for relative in tracked_raw.split("\0") if relative
        }
        _bootstrap_missing(
            source_path,
            repo,
            workflow_path,
            resolved_id,
            created_files,
            created_dirs,
            tracked_paths,
        )
        if not workflow_path.is_file():
            raise ProjectError(f"workflow file not found: {workflow_path}")
        _validate_resource_ownership(workflow_path, repo, projects)

        relative_files = [str(path.relative_to(repo)) for path in created_files]
        if relative_files:
            # Existing repositories may intentionally ignore operator files.
            # Force-add only paths this operation created; unrelated ignored or
            # modified files are never included in ``relative_files``.
            _run_git(repo, "add", "-f", "--", *relative_files)
            staged = subprocess.run(
                ["git", "-C", str(repo), "diff", "--cached", "--quiet", "--", *relative_files],
                check=False,
            )
            if staged.returncode == 1:
                _run_git(
                    repo,
                    "-c", "user.name=Symphony",
                    "-c", "user.email=symphony@local",
                    "commit", "--only", "-m", "chore: initialize Symphony project",
                    "--", *relative_files,
                )
                committed = True
            elif staged.returncode != 0:
                raise ProjectError(f"cannot inspect staged Symphony files in {repo}")

        project = Project(
            id=resolved_id,
            name=resolved_name,
            git_repo=str(repo.resolve()),
            workflow=str(workflow_path),
            host=host,
            port=resolved_port,
        )
        registry.add(project)
        return project
    except Exception:
        # Once Git accepted the commit, the repository is a published path.
        # A later registry write failure must never erase that durable work.
        if not committed:
            if not created_git and created_files:
                relative_created = [str(path.relative_to(repo)) for path in created_files]
                try:
                    _run_git(repo, "reset", "--", *relative_created)
                except ProjectError:
                    try:
                        _run_git(
                            repo,
                            "rm",
                            "--cached",
                            "-r",
                            "--ignore-unmatch",
                            "--",
                            *relative_created,
                        )
                    except ProjectError:
                        pass
            if created_root:
                shutil.rmtree(repo, ignore_errors=True)
            else:
                _cleanup_created(created_files, created_dirs)
                if created_git:
                    git_path = repo / ".git"
                    if git_path.is_dir():
                        shutil.rmtree(git_path, ignore_errors=True)
                    else:
                        try:
                            git_path.unlink()
                        except FileNotFoundError:
                            pass
        raise


def create_or_adopt_project(
    target: str | Path,
    *,
    source: str | Path,
    name: str | None = None,
    project_id: str | None = None,
    workflow: str = "WORKFLOW.md",
    host: str = "127.0.0.1",
    port: int | None = None,
    registry: ProjectRegistry | None = None,
) -> Project:
    """Serialize and perform one complete project create/adopt transaction."""
    resolved_registry = registry or ProjectRegistry()
    with resolved_registry.transaction():
        return _create_or_adopt_project_locked(
            target,
            source=source,
            name=name,
            project_id=project_id,
            workflow=workflow,
            host=host,
            port=port,
            registry=resolved_registry,
        )
