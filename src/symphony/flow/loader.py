"""Discover workflow YAML files and compile them on demand.

Discovery is intentionally shallow and path-confined: workflows are
executable code (they can run agents and shell commands), so the set of
files that can become one is a directory an operator can review, not
anything reachable from it. `..` segments, symlinks pointing outside the
directory, and absolute escapes are all refused.

Compilation results are cached by (path, mtime, size). A board with fifty
tickets would otherwise recompile the same definition fifty times per tick,
and compilation reads prompt files off disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..errors import WorkflowDefinitionInvalid, WorkflowDefinitionNotFound
from .compiler import compile_workflow
from .model import CompiledWorkflow
from .schema import decode_workflow


WORKFLOW_SUFFIXES = (".yaml", ".yml")


@dataclass(frozen=True)
class WorkflowFile:
    """One discovered file and whether it currently compiles."""

    name: str
    path: Path
    valid: bool
    error: str | None = None
    workflow_hash: str | None = None
    node_count: int = 0

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": str(self.path),
            "valid": self.valid,
            "error": self.error,
            "workflow_hash": self.workflow_hash,
            "node_count": self.node_count,
        }


class WorkflowLoader:
    """Reads `.symphony/workflows/*.yaml` for one repository."""

    def __init__(
        self,
        directory: Path,
        *,
        workflow_dir: Path,
        max_parallel_nodes: int | None = None,
    ) -> None:
        self._directory = directory
        # Root for `prompt_file` resolution and path confinement — the
        # directory holding WORKFLOW.md, i.e. the repository root.
        self._workflow_dir = workflow_dir
        self._max_parallel_nodes = max_parallel_nodes
        self._cache: dict[Path, tuple[tuple[float, int], CompiledWorkflow]] = {}

    @property
    def directory(self) -> Path:
        return self._directory

    def discover(self) -> list[Path]:
        """Workflow files in the configured directory, sorted by name."""
        if not self._directory.is_dir():
            return []
        found: list[Path] = []
        for entry in sorted(self._directory.iterdir()):
            if not entry.is_file() or entry.suffix.lower() not in WORKFLOW_SUFFIXES:
                continue
            # A symlink out of the directory would let a workflow be swapped
            # without the reviewed file changing.
            if not _is_within(entry.resolve(), self._directory.resolve()):
                continue
            found.append(entry)
        return found

    def list_workflows(self) -> list[WorkflowFile]:
        """Every discovered file with its current validation state.

        Never raises: an invalid file is reported as invalid so the CLI and
        settings page can show all files at once, including broken ones.
        """
        entries: list[WorkflowFile] = []
        for path in self.discover():
            name = path.stem
            try:
                compiled = self.load(name)
            except WorkflowDefinitionInvalid as exc:
                # `str(exc)` appends the whole diagnostics tuple repr, which is
                # unreadable in a doctor line or a settings page. The message
                # already carries the first three rendered diagnostics.
                entries.append(
                    WorkflowFile(name=name, path=path, valid=False, error=exc.message)
                )
                continue
            entries.append(
                WorkflowFile(
                    name=name,
                    path=path,
                    valid=True,
                    workflow_hash=compiled.workflow_hash,
                    node_count=len(compiled.nodes),
                )
            )
        return entries

    def path_for(self, name: str) -> Path:
        """Resolve a workflow name to its file, refusing path traversal."""
        if not name or "/" in name or "\\" in name or name.startswith("."):
            raise WorkflowDefinitionNotFound(
                "workflow name must be a plain file stem", workflow=name
            )
        for suffix in WORKFLOW_SUFFIXES:
            candidate = self._directory / f"{name}{suffix}"
            if candidate.is_file():
                return candidate
        raise WorkflowDefinitionNotFound(
            f"no workflow named {name!r} under {self._directory}", workflow=name
        )

    def load(self, name: str) -> CompiledWorkflow:
        """Compile one workflow by name, using the mtime/size cache."""
        path = self.path_for(name)
        stat = path.stat()
        stamp = (stat.st_mtime, stat.st_size)
        cached = self._cache.get(path)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        compiled = self.compile_text(path.read_text(encoding="utf-8"), source_path=path)
        if compiled.name != name:
            raise WorkflowDefinitionInvalid(
                f"workflow in {path.name} declares name {compiled.name!r}; "
                f"the file stem {name!r} must match so selection is unambiguous",
                source=str(path),
                diagnostics=(),
            )
        self._cache[path] = (stamp, compiled)
        return compiled

    def compile_text(self, text: str, *, source_path: Path) -> CompiledWorkflow:
        """Validate YAML that may not be on disk yet (the web validator)."""
        definition = decode_workflow(text, source_path=source_path)
        return compile_workflow(
            definition,
            workflow_dir=self._workflow_dir,
            max_parallel_nodes=self._max_parallel_nodes,
        )

    def invalidate(self) -> None:
        self._cache.clear()


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True
