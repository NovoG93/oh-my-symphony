from __future__ import annotations

import json
import os
import sys
import subprocess
import time
from pathlib import Path

import pytest

from symphony.pm.models import ArchiveCandidate, PmError
from symphony.pm.openspec import MAX_OUTPUT_BYTES, OpenSpecAdapter
import symphony.pm.openspec as openspec_module
from tests._pm_support import fake_node, skip_or_fail


@pytest.fixture(autouse=True)
def isolated_node_cache(monkeypatch):
    monkeypatch.setattr(openspec_module, "_NODE_CACHE", {})


class FakeOpenSpec(OpenSpecAdapter):
    def __init__(self, responses: dict[tuple[str, ...], object]):
        super().__init__("openspec")
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    def _run(
        self, project_root: Path, *args: str, json_output: bool = True,
        allow_nonzero: bool = False,
    ) -> str:
        del project_root, json_output, allow_nonzero
        self.calls.append(args)
        value = self.responses[args]
        return value if isinstance(value, str) else json.dumps(value)


def _schema(project: Path) -> None:
    path = project / "openspec" / "schemas" / "symphony-pm" / "schema.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("name: symphony-pm\n", encoding="utf-8")


def test_exact_profile_uses_status_for_artifacts_and_instructions_for_tasks(
    tmp_path: Path,
) -> None:
    _schema(tmp_path)
    adapter = FakeOpenSpec(
        {
            ("--version",): "1.14.2",
            ("schema", "validate", "symphony-pm", "--json"): {
                "valid": True,
                "issues": [],
            },
            ("status", "--change", "demo", "--json"): {
                "artifacts": [{"id": "tasks", "status": "done"}],
                "isPlanningComplete": True,
            },
            (
                "instructions",
                "apply",
                "--change",
                "demo",
                "--json",
            ): {
                "progress": {"total": 1, "complete": 0, "remaining": 1},
                "tasks": [{"id": "1", "description": "work"}],
            },
        }
    )

    assert adapter.capabilities(tmp_path).available
    assert adapter.status(tmp_path, "demo").planning_complete
    assert adapter.apply_instructions(tmp_path, "demo").total == 1
    assert any(call[0] == "status" for call in adapter.calls)
    assert any(call[:2] == ("instructions", "apply") for call in adapter.calls)


def test_wrong_version_and_unknown_json_shapes_fail_closed(tmp_path: Path) -> None:
    _schema(tmp_path)
    wrong = FakeOpenSpec({("--version",): "1.13.0"})
    assert wrong.capabilities(tmp_path).available is False

    adapter = FakeOpenSpec(
        {
            ("--version",): "1.14.2",
            ("schema", "validate", "symphony-pm", "--json"): {"valid": True},
            ("status", "--change", "demo", "--json"): {"unexpected": True},
        }
    )
    with pytest.raises(PmError, match="unexpected OpenSpec status"):
        adapter.status(tmp_path, "demo")


def test_validate_parses_real_114_item_envelope(tmp_path: Path) -> None:
    _schema(tmp_path)
    adapter = FakeOpenSpec(
        {
            ("--version",): "1.14.2",
            ("schema", "validate", "symphony-pm", "--json"): {"valid": True, "issues": []},
            ("validate", "demo", "--type", "change", "--strict", "--json", "--no-interactive"): {
                "items": [{"id": "demo", "type": "change", "valid": True, "issues": [], "durationMs": 13}],
                "summary": {"totals": {"items": 1, "passed": 1, "failed": 0}},
                "version": "1.0",
            },
        }
    )
    report = adapter.validate(tmp_path, "demo")
    assert report.valid is True
    assert report.findings == ()


def test_validate_preserves_structured_findings_from_invalid_envelope(tmp_path: Path) -> None:
    _schema(tmp_path)
    adapter = FakeOpenSpec(
        {
            ("--version",): "1.14.2",
            ("schema", "validate", "symphony-pm", "--json"): {"valid": True, "issues": []},
            ("validate", "demo", "--type", "change", "--strict", "--json", "--no-interactive"): {
                "items": [{"id": "demo", "type": "change", "valid": False, "issues": [{"code": "missing", "message": "proposal is missing"}]}],
                "summary": {"totals": {"items": 1, "passed": 0, "failed": 1}},
                "version": "1.0",
            },
        }
    )
    report = adapter.validate(tmp_path, "demo")
    assert report.valid is False
    assert [(item.code, item.message) for item in report.findings] == [
        ("missing", "proposal is missing")
    ]


def test_archive_preview_preserves_already_date_prefixed_locator(tmp_path: Path) -> None:
    _schema(tmp_path)
    locator = "2026-10-01-demo"
    change = tmp_path / "openspec" / "changes" / locator
    change.mkdir(parents=True)
    (change / "tasks.md").write_text("done\n", encoding="utf-8")
    adapter = FakeOpenSpec(
        {
            ("--version",): "1.14.2",
            ("schema", "validate", "symphony-pm", "--json"): {"valid": True, "issues": []},
        }
    )
    candidate = adapter.archive_preview(tmp_path, locator)
    assert Path(candidate.target_paths[0]).name == locator


def test_archive_accepts_real_date_prefixed_result_shape(tmp_path: Path) -> None:
    change = tmp_path / "openspec" / "changes" / "demo"
    change.mkdir(parents=True)
    (change / "tasks.md").write_text("done\n", encoding="utf-8")
    candidate = ArchiveCandidate(
        project_id=str(tmp_path), change_id="c1", revision="", controlled_workspace=str(tmp_path), source_paths=(str(change / "tasks.md"),), target_paths=(str(tmp_path / "openspec" / "changes" / "archive" / "2026-10-04-demo"),)
    )
    from symphony.pm.openspec import _tree_hash
    candidate = ArchiveCandidate(
        candidate.project_id, candidate.change_id, _tree_hash(change),
        candidate.controlled_workspace, candidate.source_paths, candidate.target_paths,
    )
    archived = tmp_path / "openspec" / "changes" / "archive" / "2026-10-04-demo"

    class ArchiveFake(OpenSpecAdapter):
        def _run_json(self, project_root: Path, *args: str):
            assert args == ("archive", "demo", "--yes", "--json")
            archived.parent.mkdir()
            change.rename(archived)
            return {
                "archive": {"change": "demo", "archivedAs": archived.name, "path": str(archived), "specsUpdated": []},
                "root": {"path": str(project_root), "source": "nearest"},
            }

    adapter = ArchiveFake()
    result = adapter.archive(candidate, tmp_path)
    assert result.archived_path == str(archived)


def test_cli_output_is_bounded_while_process_is_running(tmp_path: Path) -> None:
    script = tmp_path / "noisy"
    script.write_text(
        "#!/usr/bin/env python3\nimport sys\nsys.stdout.write('x' * %d)\n" % (MAX_OUTPUT_BYTES + 1),
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    adapter = OpenSpecAdapter(str(script), timeout_seconds=5)
    with pytest.raises(PmError, match="output exceeded"):
        adapter._run(tmp_path, "--version")


def test_cli_cleanup_kills_descendant_holding_output_pipes(tmp_path: Path) -> None:
    script = tmp_path / "forking"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "print('1.14.2')\n",
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    started = time.monotonic()
    assert OpenSpecAdapter(str(script), timeout_seconds=5)._run(tmp_path, "--version") == "1.14.2"
    assert time.monotonic() - started < 4


def test_missing_node_does_not_spawn_and_reports_exact_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("missing Node spawned"))
    # Construction is lazy, including when the host has no Node.
    adapter = OpenSpecAdapter()
    with pytest.raises(PmError) as caught:
        adapter._run(tmp_path, "--version")
    assert caught.value.code == "capability_unavailable"
    assert caught.value.status == 503
    assert caught.value.message == (
        "Bundled OpenSpec needs Node.js 20.19.0 or newer on PATH (found: none)"
    )


def test_old_node_failure_is_not_cached_and_upgrade_needs_no_restart(tmp_path, monkeypatch):
    executable = fake_node(tmp_path, "v20.17.0")
    monkeypatch.setenv("PATH", str(tmp_path))
    for _ in range(2):
        with pytest.raises(PmError, match=r"\(found: v20\.17\.0\)"):
            openspec_module.resolve_node()
        assert openspec_module._NODE_CACHE == {}
    fake_node(tmp_path, "v20.19.0")
    assert openspec_module.resolve_node() == ("/x/node", "v20.19.0")
    assert openspec_module._NODE_CACHE == {str(executable): ("/x/node", "v20.19.0")}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("success reprobed"))
    assert openspec_module.resolve_node() == ("/x/node", "v20.19.0")


@pytest.mark.parametrize("version", ["v20.18.9", "v19.99.0", "v20.19.0-rc.1"])
def test_node_below_floor_is_rejected(tmp_path, monkeypatch, version):
    fake_node(tmp_path, version)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(PmError) as caught:
        openspec_module.resolve_node()
    assert caught.value.message.endswith(f"(found: {version})")
    assert caught.value.status == 503


def test_node_cache_is_keyed_by_which_path(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    fake_node(first, "v22.22.3")
    fake_node(second, "v20.17.0")
    monkeypatch.setenv("PATH", str(first))
    assert openspec_module.resolve_node() == ("/x/node", "v22.22.3")
    monkeypatch.setenv("PATH", str(second))
    with pytest.raises(PmError, match="found: v20.17.0"):
        openspec_module.resolve_node()


@pytest.mark.parametrize(("returncode", "output"), [
    (1, '["/x/node", "v22.22.3"]'),
    (0, '["relative/node", "v22.22.3"]'),
    (0, '[true, "v22.22.3"]'),
    (0, '["/x/node", 22]'),
    (0, '"not an array"'),
    (0, '["/x/node"]'),
    (0, 'not JSON'),
])
def test_invalid_node_probe_is_not_cached(tmp_path, monkeypatch, returncode, output):
    fake_node(tmp_path, "v22.22.3")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], returncode, stdout=output, stderr="",
    ))
    with pytest.raises(PmError) as caught:
        openspec_module.resolve_node()
    assert caught.value.code == "capability_unavailable"
    assert caught.value.status == 503
    assert openspec_module._NODE_CACHE == {}


@pytest.mark.parametrize("failure", [OSError("cannot execute"), subprocess.TimeoutExpired("node", 5)])
def test_failed_node_probe_can_recover(tmp_path, monkeypatch, failure):
    fake_node(tmp_path, "v22.22.3")
    monkeypatch.setenv("PATH", str(tmp_path))
    calls = 0
    real_run = subprocess.run

    def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise failure
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(PmError, match="found: none"):
        openspec_module.resolve_node()
    assert openspec_module._NODE_CACHE == {}
    assert openspec_module.resolve_node() == ("/x/node", "v22.22.3")
    assert calls == 2


def test_bundled_run_uses_absolute_execpath_and_caches_across_adapters(tmp_path, monkeypatch):
    try:
        expected = openspec_module.resolve_node()
    except PmError as exc:
        skip_or_fail(exc.message, flag="SYMPHONY_PM_NODE_REQUIRED")
    openspec_module._NODE_CACHE.clear()
    probes = []
    commands = []
    real_run = subprocess.run
    real_popen = subprocess.Popen

    def run(command, **kwargs):
        probes.append((command, kwargs))
        return real_run(command, **kwargs)

    def popen(command, **kwargs):
        commands.append((command, kwargs))
        return real_popen(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", popen)
    for _ in range(2):
        assert OpenSpecAdapter()._run(tmp_path, "--version") == "1.14.2"
    assert len(probes) == 1
    assert probes[0][0] == [
        openspec_module.shutil.which("node"), "-p",
        "JSON.stringify([process.execPath, process.version])",
    ]
    assert probes[0][1]["cwd"] == openspec_module.BUNDLE_ROOT
    assert probes[0][1]["timeout"] == 5
    assert Path(expected[0]).is_absolute()
    # subprocess.run also uses Popen; select just the actual OpenSpec invocations.
    runs = [(argv, kwargs) for argv, kwargs in commands if argv[-1] == "--version"]
    assert len(runs) == 2
    for argv, kwargs in runs:
        assert argv == [expected[0], str(openspec_module.BUNDLE_CLI), "--version"]
        assert kwargs["cwd"] == tmp_path
        assert kwargs["env"]["OPENSPEC_TELEMETRY"] == "0"


def test_explicit_executable_bypasses_node_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(openspec_module, "resolve_node", lambda: pytest.fail("explicit Node lookup"))
    commands = []
    real_popen = subprocess.Popen

    def popen(command, **kwargs):
        commands.append(command)
        return real_popen(command, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    args = ["-c", "print('1.14.2')"]
    assert OpenSpecAdapter(sys.executable)._run(tmp_path, *args) == "1.14.2"
    assert commands == [[sys.executable, *args]]


def test_real_old_node_is_rejected_when_requested(tmp_path, monkeypatch):
    executable = os.environ.get("SYMPHONY_PM_OLD_NODE")
    if not executable:
        skip_or_fail("Set SYMPHONY_PM_OLD_NODE to the real Node v20.17.0 binary",
                     flag="SYMPHONY_PM_NODE_REQUIRED")
    assert Path(executable).is_file(), "SYMPHONY_PM_OLD_NODE must name an existing binary"
    monkeypatch.setenv("PATH", str(Path(executable).parent))
    with pytest.raises(PmError) as caught:
        openspec_module.resolve_node()
    assert caught.value.message == (
        "Bundled OpenSpec needs Node.js 20.19.0 or newer on PATH (found: v20.17.0)"
    )
