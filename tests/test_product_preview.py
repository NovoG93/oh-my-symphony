from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest

from symphony.product_preview import ProductPreviewError, ProductPreviewManager
from symphony.workflow import build_service_config, load_workflow


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.mark.asyncio
async def test_preview_uses_service_workflow_repo_and_preserves_dirty_host(
    tmp_path: Path,
):
    """Each registered project service naturally previews its own workflow repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "dev")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "kanban").mkdir()
    app = repo / "todo-app"
    app.mkdir()
    (app / "index.html").write_text("merged product", encoding="utf-8")
    workflow = repo / "WORKFLOW.md"
    workflow.write_text(
        "---\n"
        "tracker:\n  kind: file\n  board_root: ./kanban\n"
        "agent:\n  kind: claude\n  auto_merge_target_branch: dev\n"
        "preview:\n  enabled: true\n  cwd: todo-app\n"
        f"  command: '{sys.executable} -m http.server ${{PORT}} --bind ${{HOST}}'\n"
        "  health_path: /\n---\nBody\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "product")
    dirty = repo / "operator-note.txt"
    dirty.write_text("preserve me", encoding="utf-8")

    cfg = build_service_config(load_workflow(workflow))
    manager = ProductPreviewManager()
    try:
        status = await manager.start(cfg)
        assert status["healthy"] is True
        assert status["target_branch"] == "dev"
        assert status["target_sha"]
        with urlopen(status["url"], timeout=2) as response:
            assert b"merged product" in response.read()
        assert dirty.read_text(encoding="utf-8") == "preserve me"
    finally:
        await manager.close()
    assert not (repo / ".symphony" / "preview" / "worktree").exists()


@pytest.mark.asyncio
async def test_preview_stop_is_idempotent():
    manager = ProductPreviewManager()
    status = await manager.stop()
    assert status["phase"] == "stopped"
    assert status["running"] is False


@pytest.mark.asyncio
async def test_preview_status_rechecks_health_while_process_stays_running(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "dev")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "kanban").mkdir()
    app = repo / "app"
    app.mkdir()
    health_status = tmp_path / "health-status"
    health_status.write_text("200", encoding="utf-8")
    (app / "server.py").write_text(
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "import pathlib, sys\n"
        "status = pathlib.Path(sys.argv[2])\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        self.send_response(int(status.read_text()))\n"
        "        self.end_headers()\n"
        "    def log_message(self, *args): pass\n"
        "HTTPServer((sys.argv[3], int(sys.argv[1])), Handler).serve_forever()\n",
        encoding="utf-8",
    )
    workflow = repo / "WORKFLOW.md"
    workflow.write_text(
        "---\ntracker:\n  kind: file\n  board_root: ./kanban\n"
        "agent:\n  kind: claude\n  auto_merge_target_branch: dev\n"
        "preview:\n  cwd: app\n"
        f"  command: '{sys.executable} server.py ${{PORT}} {health_status} ${{HOST}}'\n"
        "---\nBody\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "product")
    manager = ProductPreviewManager()
    try:
        status = await manager.start(build_service_config(load_workflow(workflow)))
        assert status["healthy"] is True
        health_status.write_text("404", encoding="utf-8")
        status = await manager.status()
        assert status["running"] is True
        assert status["healthy"] is False
        assert status["ready"] is False
        assert status["phase"] == "unhealthy"
        health_status.write_text("200", encoding="utf-8")
        status = await manager.status()
        assert status["healthy"] is True
        assert status["ready"] is True
        assert status["phase"] == "healthy"
        assert status["last_error"] is None
        health_status.write_text("404", encoding="utf-8")
        status = await manager.status()
        assert status["phase"] == "unhealthy"
        status = await manager.stop()
        assert status["phase"] == "stopped"
        assert status["last_error"] is None
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_preview_start_failure_removes_owned_worktree(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "dev")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "kanban").mkdir()
    workflow = repo / "WORKFLOW.md"
    workflow.write_text(
        "---\ntracker:\n  kind: file\n  board_root: ./kanban\n"
        "agent:\n  kind: claude\n  auto_merge_target_branch: dev\n"
        "preview:\n  enabled: true\n  cwd: missing-app\n"
        "  command: python3 -m http.server ${PORT} --bind ${HOST}\n---\nBody\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "product")
    cfg = build_service_config(load_workflow(workflow))
    manager = ProductPreviewManager()
    with pytest.raises(ProductPreviewError, match="preview.cwd"):
        await manager.start(cfg)
    assert not (repo / ".symphony" / "preview" / "worktree").exists()
    worktrees = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout
    assert ".symphony/preview/worktree" not in worktrees
    await manager.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group behavior")
@pytest.mark.asyncio
async def test_preview_kills_descendants_when_session_leader_exits(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "dev")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "kanban").mkdir()
    app = repo / "app"
    app.mkdir()
    pid_file = tmp_path / "descendant.pid"
    (app / "forker.py").write_text(
        "import os, pathlib, sys, time\n"
        "child = os.fork()\n"
        "if child: os._exit(0)\n"
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    workflow = repo / "WORKFLOW.md"
    workflow.write_text(
        "---\ntracker:\n  kind: file\n  board_root: ./kanban\n"
        "agent:\n  kind: claude\n  auto_merge_target_branch: dev\n"
        "preview:\n  enabled: true\n  cwd: app\n"
        f"  command: '{sys.executable} forker.py {pid_file} ${{HOST}}'\n"
        "  startup_timeout_ms: 1000\n---\nBody\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "forker")
    manager = ProductPreviewManager()
    with pytest.raises(ProductPreviewError):
        await manager.start(build_service_config(load_workflow(workflow)))
    assert pid_file.exists()
    descendant = int(pid_file.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(descendant, 0)
        except ProcessLookupError:
            break
        await __import__("asyncio").sleep(0.05)
    else:
        pytest.fail(f"preview descendant {descendant} survived cleanup")
