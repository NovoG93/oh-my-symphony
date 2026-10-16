"""Run the required PM frontend regressions through the default pytest suite."""
from pathlib import Path
import shutil
import subprocess


def test_pm_ui_node_regressions():
    node = shutil.which('node')
    assert node, 'PM frontend verification requires Node.js on PATH'
    result = subprocess.run(
        [node, '--test', 'tests/pm_ui.test.mjs'],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
