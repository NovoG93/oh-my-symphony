"""Subprocess proofs for the disposable demo CLI and its security boundary."""

from __future__ import annotations

import importlib
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from symphony.pm import openspec
from symphony.pm.models import PmError
from tests._pm_support import skip_or_fail

pm = importlib.import_module("symphony.cli.pm")
ROOT = Path(__file__).resolve().parents[1]
CLEAN_KEYS = {
    "SYMPHONY_LANG", "SYMPHONY_API_TOKEN", "SYMPHONY_TRUSTED_ORIGINS",
    "SYMPHONY_SERVICE_INSTANCE_ID", "GIT_CONFIG_GLOBAL",
}
WRAPPER = """
import importlib, sys, webbrowser, pytest
pm = importlib.import_module('symphony.cli.pm')
mp = pytest.MonkeyPatch()
def forbidden(*args, **kwargs):
    raise AssertionError('the demo must never open a browser or start dispatch')
mp.setattr(webbrowser, 'open', forbidden)
mp.setattr(pm.Orchestrator, 'start', forbidden)
"""


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
        if key in CLEAN_KEYS or key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "unused-projects.json"))
    monkeypatch.setattr(openspec, "_NODE_CACHE", {})


@pytest.fixture
def supported_node():
    try:
        openspec.resolve_node()
    except PmError as exc:
        skip_or_fail(exc.message, flag="SYMPHONY_PM_NODE_REQUIRED")


def _environment(tmp_path, **overrides):
    result = {
        key: value for key, value in os.environ.items()
        if key not in CLEAN_KEYS and not key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))
    }
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    temp = tmp_path / "temp"
    temp.mkdir(exist_ok=True)
    result.update(HOME=str(home), TMPDIR=str(temp), PYTHONPATH=str(ROOT / "src"))
    result.update(overrides)
    return result


def _command(args, injection=""):
    if injection:
        code = WRAPPER + injection + "\ntry:\n    raise SystemExit(pm.main(sys.argv[1:]))\nfinally:\n    mp.undo()"
        return [sys.executable, "-c", code, *args]
    return [sys.executable, "-m", "symphony", "pm", *args]


def _run(tmp_path, args, injection="", **env):
    return subprocess.run(
        _command(args, injection), env=_environment(tmp_path, **env),
        cwd=tmp_path, capture_output=True, text=True, timeout=90,
    )


@contextmanager
def _running(tmp_path, args, *, patch_browser=True, **env):
    if os.name == "nt":
        pytest.skip("POSIX SIGINT serving proof is unavailable on Windows")
    # The wrapper makes opening a browser or starting dispatch fatal in the child.
    proc = subprocess.Popen(
        _command(args, "\n" if patch_browser else ""),
        env=_environment(tmp_path, **env), cwd=tmp_path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    lines = []
    pending = queue.Queue()

    def read_lines():
        assert proc.stdout is not None
        for line in proc.stdout:
            pending.put(line.rstrip("\n"))
        pending.put(None)

    reader = threading.Thread(target=read_lines, daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + 90
        while True:
            line = pending.get(timeout=max(0.01, deadline - time.monotonic()))
            if line is None:
                assert proc.stderr is not None
                pytest.fail(f"demo exited {proc.wait()}: {proc.stderr.read()}")
            lines.append(line)
            if line == "Agents never start in the demo. Press Ctrl-C to stop.":
                break
        if env.get("SYMPHONY_API_TOKEN"):
            lines.append(pending.get(timeout=10))
        if env.get("SYMPHONY_TRUSTED_ORIGINS"):
            lines.append(pending.get(timeout=10))
        yield proc, lines
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        reader.join(timeout=5)
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()


def _request(url, *, headers=None, method="GET"):
    req = Request(url, headers=headers or {}, method=method)
    try:
        with urlopen(req, timeout=15) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def _address(lines):
    return lines[2].removeprefix("Open: ")


def _tree(directory):
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("lang,configured", [("en", "ko"), ("ko", "en"), (None, "ko"), (None, None)])
def test_subprocess_serves_language_and_stops_zero(tmp_path, supported_node, lang, configured):
    directory = tmp_path / "demo"
    args = ["demo", "--path", str(directory)]
    if lang:
        args += ["--lang", lang]
    env = {"SYMPHONY_LANG": configured} if configured else {}
    expected = lang or configured or "en"
    with _running(tmp_path, args, patch_browser=False, **env) as (proc, lines):
        assert lines == [
            f"PM Workspace demo ({expected}) ready", f"Folder: {directory}",
            lines[2], "Agents never start in the demo. Press Ctrl-C to stop.",
        ]
        address = _address(lines)
        assert address.startswith("http://127.0.0.1:")
        assert address.endswith(f"/?lang={expected}#/artifacts")
        assert _request(address)[0] == 200
        base = address.split("/?")[0]
        status, body = _request(base + "/api/v1/pm/capabilities")
        assert status == 200 and json.loads(body)["demo"] == {"active": True}
        source = directory / "planning/openspec/changes/team-leave-request/proposal.md"
        assert ("휴가" in source.read_text()) == (expected == "ko")
        assert list((tmp_path / "home").iterdir()) == []
        assert not (directory / "projects.json").exists()
        assert list((directory / "project/kanban").glob("*.md")) == []
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=30) == 0
    before = _tree(directory)
    again = _run(tmp_path, args)
    assert again.returncode == 2 and "not empty" in again.stderr
    assert _tree(directory) == before


def test_subprocess_preserves_token_and_removes_hostile_origins(tmp_path, supported_node):
    with _running(
        tmp_path, ["demo", "--path", str(tmp_path / "demo")],
        SYMPHONY_API_TOKEN="test-demo-token", SYMPHONY_TRUSTED_ORIGINS="https://evil.example",
    ) as (_, lines):
        assert lines[4:] == [
            "SYMPHONY_API_TOKEN is set: the page asks for it once per browser tab.",
            "SYMPHONY_TRUSTED_ORIGINS is ignored: the demo accepts only local addresses.",
        ]
        base = _address(lines).split("/?")[0]
        assert _request(base + "/api/v1/pm/capabilities")[0] == 401
        auth = {"Authorization": "Bearer test-demo-token"}
        assert _request(base + "/api/v1/pm/capabilities", headers=auth)[0] == 200
        status, body = _request(
            base + "/api/v1/pm/changes/" + "a" * 32 + "/validate",
            headers={**auth, "Origin": "https://evil.example"}, method="POST",
        )
        assert status == 403 and json.loads(body)["error"]["code"] == "forbidden_origin"
        status, body = _request(base + "/api/v1/pm/capabilities", headers={**auth, "Host": "evil.example"})
        assert status == 403 and json.loads(body)["error"]["code"] == "forbidden_host"


def test_two_subprocess_demos_have_distinct_ports(tmp_path, supported_node):
    with _running(tmp_path, ["demo", "--path", str(tmp_path / "one")]) as (_, first):
        with _running(tmp_path, ["demo", "--path", str(tmp_path / "two")]) as (_, second):
            assert _address(first) != _address(second)
            assert _request(_address(first))[0] == _request(_address(second))[0] == 200


def test_no_path_creates_prefixed_temp_only_after_preflight(tmp_path, supported_node):
    with _running(tmp_path, ["demo"]) as (_, lines):
        directory = Path(lines[1].removeprefix("Folder: "))
        assert directory.parent == (tmp_path / "temp").resolve()
        assert directory.name.startswith("symphony-pm-demo-")
        assert _request(_address(lines))[0] == 200


@pytest.mark.parametrize("args", [[], ["demo", "--port", "1234"]])
def test_subprocess_usage_errors_create_nothing(tmp_path, args):
    result = _run(tmp_path, args)
    assert result.returncode == 2 and "usage:" in result.stderr
    assert not list((tmp_path / "temp").glob("symphony-pm-demo-*"))


def test_invalid_language_refuses_with_one_line_without_folder(tmp_path):
    result = _run(tmp_path, ["demo", "--lang", "fr"])
    assert result.returncode == 2 and not result.stdout
    assert result.stderr.splitlines() == ["--lang must be en or ko"]
    assert not list((tmp_path / "temp").glob("symphony-pm-demo-*"))


@pytest.mark.parametrize("failure", ["git", "node"])
def test_missing_tools_without_path_leave_no_folder(tmp_path, failure):
    injection = f"""
import symphony.pm.demo as demo
real_which = demo.shutil.which
mp.setattr(demo.shutil, 'which', lambda name: None if name == {failure!r} else real_which(name))
"""
    result = _run(tmp_path, ["demo"], injection)
    assert result.returncode == 2 and failure in result.stderr.lower()
    assert not list((tmp_path / "temp").glob("symphony-pm-demo-*"))


@pytest.mark.parametrize("kind", ["file", "nonempty", "protected"])
def test_subprocess_path_refusals_leave_tree_unchanged(tmp_path, supported_node, kind):
    directory = tmp_path / "demo"
    if kind == "file":
        directory.write_text("keep me")
    elif kind == "nonempty":
        directory.mkdir()
        (directory / "keep").write_text("keep me")
    else:
        directory = ROOT / "refused-demo-cli"
        assert not directory.exists()
    before = _tree(tmp_path)
    result = _run(tmp_path, ["demo", "--path", str(directory)])
    assert result.returncode == 2 and not result.stdout
    assert _tree(tmp_path) == before
    if kind == "protected":
        assert not directory.exists() and "protected" in result.stderr


@pytest.mark.parametrize("explicit_path", [False, True])
def test_subprocess_bind_failure_precedes_directory_writes(tmp_path, supported_node, explicit_path):
    args = ["demo", "--path", str(tmp_path / "demo")] if explicit_path else ["demo"]
    result = _run(tmp_path, args, """
def fail_bind():
    raise OSError('injected bind failure')
mp.setattr(pm, 'bind_demo_socket', fail_bind)
""")
    assert result.returncode == 1 and "injected bind failure" in result.stderr
    assert not (tmp_path / "demo").exists()
    assert not list((tmp_path / "temp").glob("symphony-pm-demo-*"))


def test_subprocess_server_failure_exits_one_keeps_directory(tmp_path, supported_node):
    result = _run(tmp_path, ["demo", "--path", str(tmp_path / "demo")], """
async def fail_server(app, sock):
    raise OSError('injected server failure')
mp.setattr(pm, 'start_demo_server', fail_server)
""")
    assert result.returncode == 1 and "injected server failure" in result.stderr
    assert not result.stdout and (tmp_path / "demo/project/WORKFLOW.md").exists()


def test_subprocess_preflight_interrupt_exits_130_without_claim(tmp_path):
    result = _run(tmp_path, ["demo"], """
def interrupt(*args):
    raise KeyboardInterrupt
mp.setattr(pm, 'preflight_demo', interrupt)
""")
    assert result.returncode == 130 and not result.stdout
    assert result.stderr.splitlines() == ["Interrupted; no demo folder was claimed"]
    assert not list((tmp_path / "temp").glob("symphony-pm-demo-*"))


def test_subprocess_validation_findings_exit_one_keep_directory(tmp_path, supported_node):
    result = _run(tmp_path, ["demo", "--path", str(tmp_path / "demo")], """
from symphony.pm.models import PmFinding
def fail_validation(directory, lang):
    (directory / 'partial').write_text('retained')
    raise pm.DemoError('demo validation failed', findings=(PmFinding('blocking_demo', 'injected finding'),))
mp.setattr(pm, 'create_demo', fail_validation)
""")
    assert result.returncode == 1
    assert "blocking_demo: injected finding" in result.stderr
    assert not result.stdout and (tmp_path / "demo/partial").read_text() == "retained"


def test_subprocess_tampered_loaded_config_exits_one_without_serving(tmp_path, supported_node):
    result = _run(tmp_path, ["demo", "--path", str(tmp_path / "demo")], """
import yaml
real_create = pm.create_demo
def tamper(directory, lang):
    workspace = real_create(directory, lang)
    text = workspace.workflow_path.read_text()
    _, config, body = text.split('---', 2)
    data = yaml.safe_load(config)
    data['pm']['planning_workspace_root'] = str(directory.parent / 'outside')
    workspace.workflow_path.write_text('---\\n' + yaml.safe_dump(data) + '---' + body)
    return workspace
mp.setattr(pm, 'create_demo', tamper)
""")
    assert result.returncode == 1 and not result.stdout
    assert "demo_config_outside: the demo configuration points outside the demo folder" in result.stderr
    assert "pm.planning_workspace_root" in result.stderr
    assert (tmp_path / "demo/project/WORKFLOW.md").exists()


def test_subprocess_creation_interrupt_exits_130_keeps_partial(tmp_path, supported_node):
    directory = tmp_path / "demo"
    result = _run(tmp_path, ["demo", "--path", str(directory)], """
def interrupt(directory, lang):
    (directory / 'partial').write_text('retained')
    raise KeyboardInterrupt
mp.setattr(pm, 'create_demo', interrupt)
""")
    assert result.returncode == 130 and not result.stdout
    assert result.stderr.strip() == f"Interrupted; the partial demo is kept at {directory}"
    assert (directory / "partial").read_text() == "retained"


def test_inprocess_interrupt_closes_bound_socket(tmp_path, monkeypatch, capsys):
    from symphony.pm.demo import DemoPreflight
    from symphony.pm.demo_server import bind_demo_socket

    directory = tmp_path / "demo"
    sock = bind_demo_socket()
    monkeypatch.setattr(pm, "preflight_demo", lambda *_: DemoPreflight(directory, "en"))
    monkeypatch.setattr(pm, "bind_demo_socket", lambda: sock)

    def interrupt(*_):
        raise KeyboardInterrupt

    monkeypatch.setattr(pm, "create_demo", interrupt)
    assert pm.main(["demo", "--path", str(directory)]) == 130
    assert capsys.readouterr().err.strip() == f"Interrupted; the partial demo is kept at {directory}"
    assert directory.is_dir() and sock.fileno() == -1
