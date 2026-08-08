"""Persistent registry of independent Symphony projects."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REGISTRY_VERSION = 1
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


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

    def load(self) -> list[Project]:
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

    def list(self) -> list[Project]:
        return self.load()

    def save(self, projects: list[Project]) -> None:
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

    def get(self, project_id: str) -> Project:
        project = next((item for item in self.load() if item.id == project_id), None)
        if project is None:
            raise ProjectError(f"unknown project {project_id!r}; run `symphony project list`")
        return project

    def add(self, project: Project) -> None:
        projects = self.load()
        projects.append(project)
        self.save(projects)

    def remove(self, project_id: str) -> Project:
        projects = self.load()
        project = next((item for item in projects if item.id == project_id), None)
        if project is None:
            raise ProjectError(f"unknown project {project_id!r}; run `symphony project list`")
        self.save([item for item in projects if item.id != project_id])
        return project

    def status(self, project_id: str):
        """Return managed-service status for hub-compatible registry consumers."""
        from . import service

        project = self.get(project_id)
        return service.service_status(project.workflow, port=project.port)

    def start(self, project_id: str) -> int:
        from . import service

        project = self.get(project_id)
        return service.main(
            ["start", project.workflow, "--host", project.host, "--port", str(project.port)]
        )

    def stop(self, project_id: str) -> int:
        from . import service

        project = self.get(project_id)
        return service.main(["stop", project.workflow])
