from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time

import pytest

from symphony.pm import host_tools, openspec
from symphony.workflow import build_service_config, parse_workflow_text
from tests._pm_support import fake_node
from tests.test_pm_routes import _authoring_client


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
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "projects.json"))
    monkeypatch.setattr(host_tools, "_CODEX_CACHE", {})
    monkeypatch.setattr(openspec, "_NODE_CACHE", {})


def _config(tmp_path, **pm):
    return build_service_config(parse_workflow_text(
        f"---\npm: {json.dumps(pm)}\n---\nPrompt\n", tmp_path / "WORKFLOW.md",
    ))


def _codex(directory, output="codex-cli 0.160.0", returncode=0):
    directory.mkdir(exist_ok=True)
    executable = directory / "codex"
    executable.write_text(
        f"#!/bin/sh\nprintf '%s\\n' '{output}'\nexit {returncode}\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _login(tmp_path):
    auth = tmp_path / ".codex" / "auth.json"
    auth.parent.mkdir(exist_ok=True)
    auth.write_text("{}\n", encoding="utf-8")
    return auth


def test_matching_codex_is_cached_but_login_is_checked_each_request(tmp_path, monkeypatch):
    executable = _codex(tmp_path)
    auth = _login(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert host_tools.probe_codex() == {
        "required": "0.160.0", "found": "0.160.0", "available": True, "reason": None,
    }
    assert host_tools._CODEX_CACHE == {str(executable.resolve()): "0.160.0"}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("cached Codex reprobed"))
    auth.unlink()
    result = host_tools.probe_codex()
    assert result["available"] is False
    assert result["found"] == "0.160.0"
    assert result["reason"] == (
        "Planning assistant needs a Codex login: ~/.codex/auth.json is missing. Run codex login."
    )
    _login(tmp_path)
    assert host_tools.probe_codex()["available"] is True


def test_wrong_codex_is_reprobed_and_upgrade_recovers(tmp_path, monkeypatch):
    executable = _codex(tmp_path, "codex-cli 0.162.1")
    _login(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    for _ in range(2):
        assert host_tools.probe_codex() == {
            "required": "0.160.0", "found": "0.162.1", "available": False,
            "reason": "Planning assistant needs Codex 0.160.0 (found: 0.162.1).",
        }
        assert host_tools._CODEX_CACHE == {}
    _codex(tmp_path)
    assert host_tools.probe_codex()["available"] is True
    assert host_tools._CODEX_CACHE == {str(executable.resolve()): "0.160.0"}


def test_missing_codex_and_login_report_both_without_spawning(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("missing Codex spawned"))
    assert host_tools.probe_codex() == {
        "required": "0.160.0", "found": None, "available": False,
        "reason": (
            "Planning assistant needs Codex 0.160.0 (found: none). "
            "Planning assistant needs a Codex login: ~/.codex/auth.json is missing. Run codex login."
        ),
    }


def test_wrong_codex_and_missing_login_report_each_failure(tmp_path, monkeypatch):
    _codex(tmp_path, "codex-cli 0.162.1")
    monkeypatch.setenv("PATH", str(tmp_path))
    result = host_tools.probe_codex()
    assert result["reason"] == (
        "Planning assistant needs Codex 0.160.0 (found: 0.162.1). "
        "Planning assistant needs a Codex login: ~/.codex/auth.json is missing. Run codex login."
    )
    assert result["available"] is False


@pytest.mark.parametrize(("output", "returncode"), [
    ("codex-cli 0.160.0 extra", 0), ("0.160.0", 0), ("", 0), ("codex-cli 0.160.0", 1),
])
def test_codex_version_requires_exact_successful_output(tmp_path, monkeypatch, output, returncode):
    _codex(tmp_path, output, returncode)
    _login(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert host_tools.probe_codex()["available"] is False
    assert host_tools._CODEX_CACHE == {}


@pytest.mark.parametrize("failure", [OSError("cannot execute"), subprocess.TimeoutExpired("codex", 5)])
def test_codex_probe_failure_uses_five_second_timeout_and_can_retry(tmp_path, monkeypatch, failure):
    executable = _codex(tmp_path)
    _login(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    real_run = subprocess.run
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if len(calls) == 1:
            raise failure
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    result = host_tools.probe_codex()
    assert result["reason"] == "Planning assistant needs Codex 0.160.0 (found: none)."
    assert host_tools._CODEX_CACHE == {}
    assert host_tools.probe_codex()["available"] is True
    assert len(calls) == 2
    assert all(argv == [str(executable.resolve()), "--version"] for argv, _ in calls)
    assert all(kwargs["timeout"] == 5 for _, kwargs in calls)


def test_codex_cache_uses_resolved_path_and_detects_changed_path(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    alias = tmp_path / "alias"
    original = _codex(first)
    _codex(second, "codex-cli 0.162.1")
    alias.mkdir()
    (alias / "codex").symlink_to(original)
    _login(tmp_path)
    monkeypatch.setenv("PATH", str(first))
    assert host_tools.probe_codex()["available"] is True
    monkeypatch.setenv("PATH", str(alias))
    real_run = subprocess.run
    with monkeypatch.context() as cache_check:
        cache_check.setattr(subprocess, "run", lambda *a, **k: pytest.fail("resolved path reprobed"))
        assert host_tools.probe_codex()["available"] is True
    monkeypatch.setenv("PATH", str(second))
    assert subprocess.run is real_run
    assert host_tools.probe_codex()["found"] == "0.162.1"
    assert len(host_tools._CODEX_CACHE) == 1


def test_node_probe_uses_shared_success_cache(tmp_path, monkeypatch):
    fake_node(tmp_path, "v20.19.0")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert openspec.resolve_node() == ("/x/node", "v20.19.0")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("shared Node cache missed"))
    assert host_tools.probe_node() == {
        "required": "20.19.0", "found": "v20.19.0", "available": True, "reason": None,
    }


def test_node_probe_reports_old_and_missing_node_then_recovers(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    missing = host_tools.probe_node()
    assert missing == {
        "required": "20.19.0", "found": None, "available": False,
        "reason": "Bundled OpenSpec needs Node.js 20.19.0 or newer on PATH (found: none)",
    }
    fake_node(tmp_path, "v20.17.0")
    old = host_tools.probe_node()
    assert old["found"] == "v20.17.0"
    assert old["available"] is False
    assert old["reason"].endswith("(found: v20.17.0)")
    assert openspec._NODE_CACHE == {}
    fake_node(tmp_path, "v22.22.3")
    assert host_tools.probe_node()["available"] is True


def test_no_requested_flags_spawn_no_probes(tmp_path, monkeypatch):
    monkeypatch.setattr(host_tools, "probe_codex", lambda: pytest.fail("Codex flag is off"))
    monkeypatch.setattr(host_tools, "probe_node", lambda: pytest.fail("PM is off"))
    assert host_tools.probe(_config(tmp_path)) == {}


@pytest.mark.parametrize("flag", ["authoring", "exchange", "assistance", "handoff"])
def test_probe_fields_follow_requested_flags_and_executable_override(tmp_path, monkeypatch, flag):
    codex = {"required": "0.160.0", "found": None, "available": False, "reason": "codex reason"}
    node = {"required": "20.19.0", "found": None, "available": False, "reason": "node reason"}
    monkeypatch.setattr(host_tools, "probe_codex", lambda: codex)
    monkeypatch.setattr(host_tools, "probe_node", lambda: node)
    assert host_tools.probe(_config(tmp_path, **{flag: True})) == (
        {"codex": codex, "node": node} if flag == "assistance" else {"node": node}
    )
    monkeypatch.setattr(host_tools, "probe_node", lambda: pytest.fail("explicit OpenSpec probed Node"))
    assert host_tools.probe(_config(tmp_path, openspec_executable="openspec", **{flag: True})) == (
        {"codex": codex} if flag == "assistance" else {}
    )


@pytest.mark.timing
@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["codex", "node"])
async def test_hanging_probe_times_out_off_loop_and_health_stays_live(tmp_path, monkeypatch, tool):
    # Build the Git-backed fixture while the real Git is still on PATH.
    client, _ = await _authoring_client(tmp_path)
    marker = tmp_path / "probe-started"
    tools = tmp_path / "tools"
    tools.mkdir()
    hanging = tools / tool
    hanging.write_text(
        f"#!{sys.executable}\nimport time\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('started')\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    hanging.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools))
    other = "probe_node" if tool == "codex" else "probe_codex"
    monkeypatch.setattr(host_tools, other, lambda: {
        "required": "test", "found": "test", "available": True, "reason": None,
    })
    request = None
    try:
        started = time.monotonic()
        request = asyncio.create_task(client.get("/api/v1/pm/capabilities"))
        for _ in range(100):
            if marker.exists():
                break
            await asyncio.sleep(0.01)
        assert marker.exists(), "probe subprocess did not start"
        heartbeat_start = time.monotonic()
        heartbeat = await asyncio.wait_for(client.get("/api/v1/health"), timeout=1)
        assert heartbeat.status == 200
        assert time.monotonic() - heartbeat_start < 1
        response = await asyncio.wait_for(request, timeout=6)
        elapsed = time.monotonic() - started
        assert response.status == 200, await response.text()
        result = (await response.json())["host_tools"][tool]
        assert result["available"] is False
        assert "found: none" in result["reason"]
        assert 4.5 <= elapsed < 6.5, f"probe returned after {elapsed:.2f}s"
        cache = host_tools._CODEX_CACHE if tool == "codex" else openspec._NODE_CACHE
        assert cache == {}
    finally:
        if request is not None and not request.done():
            await asyncio.gather(request, return_exceptions=True)
        await client.close()
