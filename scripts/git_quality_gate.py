#!/usr/bin/env python3
"""Local Git hook gates that mirror the CI test environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


GATE_VERSION = "2026-10-21.1"

CORE_FILES = frozenset({
    "pyproject.toml",
    "uv.lock",
    "conftest.py",
    "tests/conftest.py",
    "src/symphony/orchestrator/core.py",
    "src/symphony/orchestrator/attempt.py",
    "src/symphony/orchestrator/worker_exit.py",
    "src/symphony/orchestrator/release_transition.py",
    "src/symphony/workflow/config.py",
    "src/symphony/workflow/builder.py",
    "src/symphony/workflow/constants.py",
    "src/symphony/backends/per_turn.py",
})

NON_CODE_EXTENSIONS = frozenset({
    ".md",
    ".txt",
    ".jsonl",
    ".rst",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".ico",
    ".license",
    ".gitignore",
})

EXPLICIT_TEST_MAPPINGS: dict[str, tuple[str, ...]] = {
    "src/symphony/trackers/jira.py": ("tests/test_tracker_jira.py",),
    "src/symphony/trackers/linear.py": (
        "tests/test_tracker_linear_full.py",
        "tests/test_tracker_linear_archive.py",
    ),
    "src/symphony/trackers/file.py": ("tests/test_tracker_file.py",),
    "src/symphony/trackers/validate.py": ("tests/test_tracker_validate.py",),
    "src/symphony/cli/doctor.py": ("tests/test_doctor.py",),
    "src/symphony/cli/main.py": (
        "tests/test_cli_main_routing.py",
        "tests/test_cli_run_startup.py",
    ),
    "src/symphony/cli/board.py": (
        "tests/test_board_cli.py",
        "tests/test_board_cli_subcommands.py",
    ),
    "src/symphony/cli/release.py": ("tests/test_release_cli.py",),
    "src/symphony/cli/project.py": (
        "tests/test_project_cli_systemd.py",
        "tests/test_projects.py",
    ),
    "src/symphony/tui/app.py": ("tests/test_tui.py",),
    "src/symphony/tui/screens.py": ("tests/test_tui.py",),
    "src/symphony/tui/widgets.py": ("tests/test_tui.py",),
    "src/symphony/intent.py": ("tests/test_chat_intent.py", "tests/test_intent.py"),
    "src/symphony/chat.py": ("tests/test_chat.py", "tests/test_webapi_chat.py"),
    "src/symphony/webapi.py": ("tests/test_webapi.py", "tests/test_webapi_auth.py"),
    "src/symphony/web_policy.py": ("tests/test_web_policy.py",),
    "src/symphony/server.py": ("tests/test_server_routes.py",),
    "src/symphony/service.py": (
        "tests/test_service.py",
        "tests/test_service_systemd.py",
    ),
    "src/symphony/stats.py": ("tests/test_stats_skills.py",),
    "src/symphony/prompt.py": ("tests/test_prompt.py",),
    "src/symphony/prompt_context.py": ("tests/test_prompt_context.py",),
    "src/symphony/progress_md.py": ("tests/test_progress_md.py",),
    "src/symphony/projects.py": ("tests/test_projects.py",),
    "src/symphony/logging.py": ("tests/test_logging.py",),
    "src/symphony/systemd.py": ("tests/test_systemd.py",),
    "src/symphony/backends/codex.py": (
        "tests/test_codex_approvals.py",
        "tests/test_codex_usage.py",
    ),
    "src/symphony/backends/claude_code.py": (
        "tests/test_claude_cache_tokens.py",
        "tests/test_backends.py",
    ),
    "src/symphony/backends/copilot.py": ("tests/test_copilot_backend.py",),
    "src/symphony/backends/pi.py": ("tests/test_backends.py",),
    "src/symphony/backends/prime_agent.py": (
        "tests/test_prime_agent_terminal_completion.py",
        "tests/test_prime_config_preflight.py",
    ),
    "src/symphony/workflow/presets.py": ("tests/test_workflow_presets.py",),
    "src/symphony/workflow/mutate.py": ("tests/test_workflow_mutate.py",),
    "src/symphony/workflow/profiles.py": ("tests/test_workflow_agent_profiles.py",),
}


@dataclass(frozen=True)
class GateCommand:
    label: str
    argv: tuple[str, ...]


def _quote(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in tuple(env):
        if key == "GIT_CONFIG_GLOBAL" or not key.startswith("GIT_"):
            continue
        env.pop(key, None)
    env.setdefault("GIT_CONFIG_GLOBAL", os.devnull)
    return env


def _run(command: GateCommand, *, cwd: Path) -> None:
    print(f"\n==> {command.label}", file=sys.stderr)
    print(f"+ {_quote(command.argv)}", file=sys.stderr)
    subprocess.run(command.argv, cwd=cwd, check=True, env=_subprocess_env())


def _git_output(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env=_subprocess_env(),
    )
    return result.stdout.strip()


def repo_root() -> Path:
    return Path(_git_output(Path.cwd(), "rev-parse", "--show-toplevel"))


def git_path(root: Path, relative_path: str) -> Path:
    resolved = Path(_git_output(root, "rev-parse", "--git-path", relative_path))
    return resolved if resolved.is_absolute() else root / resolved


def staged_files(root: Path) -> tuple[str, ...]:
    output = _git_output(
        root,
        "diff",
        "--cached",
        "--name-only",
        "--diff-filter=ACMR",
    )
    return tuple(line.strip() for line in output.splitlines() if line.strip())


def staged_python_files(root: Path) -> tuple[str, ...]:
    return tuple(line for line in staged_files(root) if line.endswith(".py"))


def select_tests_for_staged(
    root: Path, staged: Sequence[str]
) -> tuple[str, ...] | None:
    """Select tests to run based on staged changes.

    Returns:
        tuple[str, ...]: Targeted test paths to run (can be 1 single test or a few).
                         Empty tuple () means skip pytest (e.g. only docs adapted).
        None:            Run complete pytest (core files adapted, wide-ranging changes,
                         or full test override).
    """
    if os.environ.get("SYMPHONY_SKIP_TESTS") in ("1", "true", "TRUE"):
        return ()

    if os.environ.get("SYMPHONY_TEST_ALL") in ("1", "true", "TRUE"):
        return None

    if not (root / "tests").is_dir():
        return ()

    if not staged:
        return ()

    # Check if all staged files are non-code/documentation
    all_non_code = True
    for f in staged:
        path = Path(f)
        is_doc = (
            f.startswith("docs/")
            or f.startswith("plans/")
            or f.startswith("kanban/")
            or f == ".gitignore"
            or path.suffix.lower() in NON_CODE_EXTENSIONS
        )
        if not is_doc:
            all_non_code = False
            break

    if all_non_code:
        return ()

    # Core/foundational changes trigger complete pytest
    for f in staged:
        norm = f.replace("\\", "/")
        if norm in CORE_FILES:
            return None
        if norm.startswith("WORKFLOW") or norm.endswith("conftest.py"):
            return None

    candidate_tests: set[str] = set()

    for f in staged:
        norm = f.replace("\\", "/")
        # Directly staged test file
        if norm.startswith("tests/test_") and norm.endswith(".py"):
            if (root / norm).exists():
                candidate_tests.add(norm)
            continue

        if not norm.endswith(".py"):
            continue

        # Explicit mapping table
        if norm in EXPLICIT_TEST_MAPPINGS:
            for t_file in EXPLICIT_TEST_MAPPINGS[norm]:
                if (root / t_file).exists():
                    candidate_tests.add(t_file)
            continue

        # Dynamic search in tests directory
        stem = Path(norm).stem
        parent = Path(norm).parent.name
        matched_any = False

        test_direct = f"tests/test_{stem}.py"
        if (root / test_direct).exists():
            candidate_tests.add(test_direct)
            matched_any = True

        test_scoped = f"tests/test_{parent}_{stem}.py"
        if (root / test_scoped).exists():
            candidate_tests.add(test_scoped)
            matched_any = True

        if not matched_any:
            tests_dir = root / "tests"
            for test_path in tests_dir.glob("test_*.py"):
                if stem in test_path.stem:
                    candidate_tests.add(f"tests/{test_path.name}")
                    matched_any = True

        # If a python file in src/ could not be mapped to any test, run complete pytest
        if not matched_any and norm.startswith("src/"):
            return None

    if not candidate_tests:
        return None

    if len(candidate_tests) > 4:
        return None

    return tuple(sorted(candidate_tests))


def find_pytest_python(root: Path) -> str:
    venv_python = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if venv_python.exists() and os.access(venv_python, os.X_OK):
        return str(venv_python)
    return sys.executable


def pre_commit_commands(root: Path, *, python: str) -> tuple[GateCommand, ...]:
    commands: list[GateCommand] = [
        GateCommand("staged whitespace check", ("git", "diff", "--cached", "--check"))
    ]
    python_files = staged_python_files(root)
    if python_files:
        commands.append(
            GateCommand(
                "staged Python syntax check",
                (python, "-m", "py_compile", *python_files),
            )
        )
    staged = staged_files(root)
    selected_tests = select_tests_for_staged(root, staged)
    if selected_tests is None:
        commands.append(
            GateCommand(
                "complete pytest (core or wide-ranging changes adapted)",
                (python, "-m", "pytest", "-q"),
            )
        )
    elif selected_tests:
        if len(selected_tests) == 1:
            label = f"single pytest for adapted component: {selected_tests[0]}"
        else:
            label = f"targeted pytest ({len(selected_tests)} test files)"
        commands.append(
            GateCommand(
                label,
                (python, "-m", "pytest", "-q", *selected_tests),
            )
        )
    return tuple(commands)


def pre_push_commands(*, python: str) -> tuple[GateCommand, ...]:
    return (
        GateCommand("working tree whitespace check", ("git", "diff", "--check")),
        GateCommand("staged whitespace check", ("git", "diff", "--cached", "--check")),
        GateCommand("CI-parity full pytest", (python, "-m", "pytest", "-q")),
    )


def _gate_stamp(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    payload = {
        "gate_version": GATE_VERSION,
        "python": sys.version,
        "pyproject_sha256": hashlib.sha256(pyproject.read_bytes()).hexdigest(),
    }
    return json.dumps(payload, sort_keys=True)


def ensure_ci_venv(root: Path) -> Path:
    venv_dir = git_path(root, "symphony-quality/ci-dev-venv")
    stamp_path = git_path(root, "symphony-quality/ci-dev-venv.stamp")
    python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    stamp = _gate_stamp(root)
    if python.exists() and stamp_path.exists() and stamp_path.read_text() == stamp:
        return python

    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
    _run(GateCommand("upgrade CI-parity pip", (str(python), "-m", "pip", "install", "--upgrade", "pip")), cwd=root)
    _run(GateCommand("install CI-parity dev extra", (str(python), "-m", "pip", "install", "-e", ".[dev]")), cwd=root)
    stamp_path.write_text(stamp, encoding="utf-8")
    return python


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("pre-commit", "pre-push"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    root = repo_root()
    if args.stage == "pre-commit":
        python_bin = find_pytest_python(root)
        commands = pre_commit_commands(root, python=python_bin)
    else:
        ci_python = "<ci-dev-venv-python>" if args.dry_run else str(ensure_ci_venv(root))
        commands = pre_push_commands(python=ci_python)

    for command in commands:
        if args.dry_run:
            print(f"{command.label}: {_quote(command.argv)}")
        else:
            _run(command, cwd=root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
