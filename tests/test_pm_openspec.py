from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from symphony.pm.models import ArchiveCandidate, PmError
from symphony.pm.openspec import MAX_OUTPUT_BYTES, OpenSpecAdapter


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
            ("--version",): "1.14.0",
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
            ("--version",): "1.14.0",
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
            ("--version",): "1.14.0",
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
            ("--version",): "1.14.0",
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
            ("--version",): "1.14.0",
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
        "print('1.14.0')\n",
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    started = time.monotonic()
    assert OpenSpecAdapter(str(script), timeout_seconds=5)._run(tmp_path, "--version") == "1.14.0"
    assert time.monotonic() - started < 4
