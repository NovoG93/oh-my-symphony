from __future__ import annotations

import os
import subprocess
import sysconfig
from pathlib import Path
from types import SimpleNamespace

import pytest

import symphony.pyright as pyright_wrapper


def test_build_command_pins_pythonpath_to_running_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interpreter = r"C:\Python\python.exe"
    monkeypatch.setattr(pyright_wrapper.sys, "executable", interpreter)

    command = pyright_wrapper.build_command(
        [
            "--outputjson",
            "--pythonpath",
            "/wrong/python",
            "--pythonpath=/also/wrong",
            "--pythonpath",
            "--project",
            "pyrightconfig.json",
        ]
    )

    assert command == [
        interpreter,
        "-m",
        "pyright",
        "--pythonpath",
        interpreter,
        "--outputjson",
        "--project",
        "pyrightconfig.json",
    ]


def test_main_propagates_pyright_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], bool]] = []

    def fake_run(command: list[str], *, check: bool) -> SimpleNamespace:
        calls.append((command, check))
        return SimpleNamespace(returncode=17)

    monkeypatch.setattr(pyright_wrapper.subprocess, "run", fake_run)

    assert pyright_wrapper.main(["--outputjson"]) == 17
    assert calls == [
        (
            pyright_wrapper.build_command(["--outputjson"]),
            False,
        )
    ]


def test_installed_console_script_runs_project_type_check() -> None:
    scripts_dir = Path(sysconfig.get_path("scripts"))
    candidates = (
        scripts_dir / "symphony-pyright",
        scripts_dir / "symphony-pyright.exe",
        scripts_dir / "symphony-pyright-script.py",
    )
    script = next((path for path in candidates if path.is_file()), None)
    if script is None:
        pytest.skip("symphony-pyright is not installed in this interpreter")

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [str(script)],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 errors" in result.stdout
