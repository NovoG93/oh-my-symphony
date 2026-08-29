"""Tests for scripts/hello.py utility script."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "hello.py"


def test_hello_script_executes_directly() -> None:
    assert SCRIPT_PATH.exists(), f"Expected script at {SCRIPT_PATH}"
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == "hello from symphony\n"
    assert result.stderr == ""


def test_hello_script_import_and_main(capsys) -> None:
    assert SCRIPT_PATH.exists(), f"Expected script at {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("hello", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Importing should not print anything directly
    captured = capsys.readouterr()
    assert captured.out == ""

    # Invoking main() prints the expected greeting
    assert hasattr(module, "main")
    module.main()
    captured = capsys.readouterr()
    assert captured.out == "hello from symphony\n"
