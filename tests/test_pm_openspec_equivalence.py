"""R14: compare every product call with an unbundled OpenSpec 1.14.0 CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from symphony.pm import demo, openspec
from symphony.pm.models import PmError
from tests._pm_support import skip_or_fail


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLI = ROOT / "scripts/openspec-bundle/node_modules/.bin/openspec"
REQUIRED = "SYMPHONY_PM_EQUIVALENCE_REQUIRED"


@pytest.fixture(autouse=True)
def environment_unchanged():
    before = dict(os.environ)
    before.pop("PYTEST_CURRENT_TEST", None)
    yield
    after = dict(os.environ)
    after.pop("PYTEST_CURRENT_TEST", None)
    assert after == before


@pytest.fixture(autouse=True)
def clean_environment(environment_unchanged, tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key in {
            "SYMPHONY_LANG", "SYMPHONY_API_TOKEN", "SYMPHONY_TRUSTED_ORIGINS",
            "SYMPHONY_SERVICE_INSTANCE_ID", "GIT_CONFIG_GLOBAL",
        } or key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_")):
            monkeypatch.delenv(key)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "unused-projects.json"))
    monkeypatch.setattr(openspec, "_NODE_CACHE", {})


def run_cli(command: list[str], root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*command, *args], cwd=root, capture_output=True, text=True, timeout=30,
        env={**os.environ, "OPENSPEC_TELEMETRY": "0"},
    )


@pytest.fixture
def commands(tmp_path):
    override = os.environ.get("SYMPHONY_PM_OPENSPEC")
    executable = shutil.which(override if override is not None else str(DEFAULT_CLI))
    if executable is None:
        reason = f"R14 needs an executable OpenSpec 1.14.0 CLI: {override or DEFAULT_CLI}"
        if override is not None:
            pytest.fail(reason + " (SYMPHONY_PM_OPENSPEC is set; no fallback)")
        skip_or_fail(reason + "; run npm ci --prefix scripts/openspec-bundle", flag=REQUIRED)
    try:
        node, _ = openspec.resolve_node()
    except PmError as exc:
        skip_or_fail(exc.message, flag=REQUIRED)
    if shutil.which("git") is None:
        skip_or_fail("R14 demo fixtures need git on PATH", flag=REQUIRED)
    bundled = [node, str(openspec.BUNDLE_CLI)]
    reference = [executable]
    for command in (bundled, reference):
        result = run_cli(command, tmp_path, "--version")
        assert result.returncode == 0, result.stderr
        assert result.stdout == "1.14.0\n"
    return bundled, reference


def normalise(stdout: str, root: Path) -> str:
    stdout = stdout.replace(str(root), "<ROOT>")
    return re.sub(r'("durationMs"\s*:\s*)\d+', r'\g<1>0', stdout)


def tree_bytes(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None
        for path in root.rglob("*")
    }


@pytest.mark.parametrize("fixture", ["en", "ko", "no-scenario"])
def test_r14_product_calls_and_archive_trees_are_equivalent(tmp_path, commands, fixture):
    inputs = demo.preflight_demo(tmp_path / "demo", "ko" if fixture == "ko" else "en")
    directory = demo.claim_demo_directory(inputs)
    workspace = demo.create_demo(directory, inputs.lang)
    change = Path("openspec") / "changes" / demo.LOCATOR
    if fixture == "no-scenario":
        (workspace.planning_path / change / "specs/requirements/spec.md").write_text(
            "## ADDED Requirements\n\n### Requirement: Leave request\n\n"
            "The system SHALL store a leave request.\n", encoding="utf-8",
        )
    roots = (tmp_path / "bundled planning", tmp_path / "reference planning")
    for root in roots:
        shutil.copytree(workspace.planning_path / "openspec", root / "openspec")
    before = tree_bytes(roots[0] / "openspec")
    assert before == tree_bytes(roots[1] / "openspec")
    invalid = fixture == "no-scenario"
    # These are the six argv shapes in OpenSpecAdapter; `show` is not a product call.
    calls = (
        ("--version",),
        ("schema", "validate", "symphony-pm", "--json"),
        ("status", "--change", demo.LOCATOR, "--json"),
        ("instructions", "apply", "--change", demo.LOCATOR, "--json"),
        ("validate", demo.LOCATOR, "--type", "change", "--strict", "--json", "--no-interactive"),
        ("archive", demo.LOCATOR, "--yes", "--json"),
    )
    for args in calls:
        bundled, reference = (
            run_cli(command, root, *args) for command, root in zip(commands, roots, strict=True)
        )
        assert bundled.returncode == reference.returncode, (args, bundled, reference)
        assert normalise(bundled.stdout, roots[0]) == normalise(reference.stdout, roots[1]), args
        expected = 1 if invalid and args[0] in {"validate", "archive"} else 0
        assert bundled.returncode == expected, (args, bundled.stdout, bundled.stderr)
        if args[0] == "validate":
            item = json.loads(bundled.stdout)["items"][0]
            assert item["valid"] is not invalid
            if invalid:
                assert any("scenario" in issue["message"].lower() for issue in item["issues"])
        if args[0] == "archive":
            payload = json.loads(bundled.stdout)
            if invalid:
                assert tree_bytes(roots[0] / "openspec") == before
            else:
                archived = Path(payload["archive"]["path"])
                assert archived.is_dir()
                assert archived.parent == roots[0] / "openspec/changes/archive"
                assert not (roots[0] / change).exists()
                assert (roots[0] / "openspec/specs/requirements/spec.md").is_file()
                assert (roots[0] / "openspec/specs/team-calendar/spec.md").is_file()
    assert tree_bytes(roots[0] / "openspec") == tree_bytes(roots[1] / "openspec")
