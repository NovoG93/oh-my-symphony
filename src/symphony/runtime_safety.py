"""Prevent Symphony from orchestrating its own source repository.

A source checkout is identified by its canonical Git common directory, so the
main checkout and all linked worktrees are treated as the same repository.
Installed wheels and unrelated repositories have no protected source root.
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import ProtectedSourceRepository
from .utils.git_sandbox import resolve_git_common_dir

PROTECTED_REPOSITORY_MESSAGE = (
    "WORKFLOW.md belongs to the protected oh-my-symphony source repository; "
    "running agents here could modify Symphony itself. Use `symphony project "
    "create` or `symphony project add` with a separate project repository, "
    "then start that project from the Symphony hub."
)


def protected_source_common_dir() -> Path | None:
    """Return this installation's source-checkout common dir, when applicable.

    The ``src/symphony`` layout check avoids mistaking an unrelated project's
    ``.venv/site-packages/symphony`` directory for Symphony's own source tree.
    """
    package_init = Path(__file__).resolve().with_name("__init__.py")
    for candidate in package_init.parents:
        source_init = candidate / "src" / "symphony" / "__init__.py"
        try:
            if source_init.is_file() and source_init.samefile(package_init):
                return resolve_git_common_dir(candidate)
        except OSError:
            continue
    return None


def workflow_uses_protected_source_repo(workflow_path: str | Path) -> bool:
    """Whether ``workflow_path`` is in the same Git repo as Symphony's source."""
    protected = protected_source_common_dir()
    workflow_common = resolve_git_common_dir(Path(workflow_path).resolve().parent)
    if protected is None or workflow_common is None:
        return False
    try:
        return os.path.samefile(protected, workflow_common)
    except OSError:
        return protected.resolve(strict=False) == workflow_common.resolve(strict=False)


def ensure_workflow_repo_is_safe(workflow_path: str | Path) -> None:
    """Raise an operator-facing error when a workflow targets Symphony itself."""
    if workflow_uses_protected_source_repo(workflow_path):
        raise ProtectedSourceRepository(PROTECTED_REPOSITORY_MESSAGE)
