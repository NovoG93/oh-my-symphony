"""Loopback-only lifecycle manager for the integrated product preview."""
from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import shlex
import socket
import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from ._shell import terminate_process_tree
from .workflow import ServiceConfig


class ProductPreviewError(RuntimeError):
    """A safe, operator-facing preview lifecycle failure."""


class ProductPreviewManager:
    """Own one preview process and one detached target-branch checkout."""

    def __init__(self, *, max_log_lines: int = 400) -> None:
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._readers: list[asyncio.Task[None]] = []
        self._logs: deque[dict[str, str]] = deque(maxlen=max_log_lines)
        self._phase = "stopped"
        self._healthy = False
        self._last_error: str | None = None
        self._port: int | None = None
        self._url: str | None = None
        self._health_url: str | None = None
        self._target_branch: str | None = None
        self._target_sha: str | None = None
        self._started_at: str | None = None
        self._repo: Path | None = None
        self._checkout: Path | None = None
        self._pgid: int | None = None

    async def start(self, cfg: ServiceConfig) -> dict[str, Any]:
        async with self._lock:
            if self._process is not None and self._process.returncode is None:
                return await self._status_unlocked(cfg)
            await self._start_unlocked(cfg)
            return await self._status_unlocked(cfg)

    async def restart(self, cfg: ServiceConfig) -> dict[str, Any]:
        async with self._lock:
            await self._stop_unlocked(remove_checkout=True)
            await self._start_unlocked(cfg)
            return await self._status_unlocked(cfg)

    async def stop(self, cfg: ServiceConfig | None = None) -> dict[str, Any]:
        async with self._lock:
            await self._stop_unlocked(remove_checkout=True)
            return await self._status_unlocked(cfg)

    async def status(self, cfg: ServiceConfig | None = None) -> dict[str, Any]:
        async with self._lock:
            return await self._status_unlocked(cfg)

    async def close(self) -> None:
        async with self._lock:
            await self._stop_unlocked(remove_checkout=True)

    async def _start_unlocked(self, cfg: ServiceConfig) -> None:
        preview = cfg.preview
        if not preview.enabled:
            raise ProductPreviewError("Product Preview is disabled in WORKFLOW.md")
        argv = shlex.split(preview.command, posix=os.name != "nt")
        if not argv:
            raise ProductPreviewError("preview.command is empty")
        self._phase = "preparing"
        self._healthy = False
        self._last_error = None
        self._health_url = None
        workflow_dir = cfg.workflow_path.parent.resolve()
        try:
            repo_text = await self._git(workflow_dir, "rev-parse", "--show-toplevel")
            repo = Path(repo_text.strip()).resolve()
            branch = cfg.agent.auto_merge_target_branch.strip()
            if not branch:
                branch = (await self._git(repo, "branch", "--show-current")).strip()
            if not branch:
                raise ProductPreviewError("preview target branch is not configured")
            sha = (await self._git(repo, "rev-parse", "--verify", f"{branch}^{{commit}}")).strip()
            checkout = workflow_dir / ".symphony" / "preview" / "worktree"
            checkout.parent.mkdir(parents=True, exist_ok=True)
            await self._remove_worktree(repo, checkout)
            await self._git(repo, "worktree", "add", "--detach", str(checkout), sha)
            # From this point the checkout is ours and every later failure must
            # roll it back; record ownership before validating cwd/spawning.
            self._repo, self._checkout = repo, checkout
            workdir = (checkout / preview.cwd).resolve()
            if not workdir.is_relative_to(checkout.resolve()) or not workdir.is_dir():
                raise ProductPreviewError(
                    f"preview.cwd does not exist inside target checkout: {preview.cwd}"
                )
            port = self._allocate_port()
            host = "127.0.0.1"
            expanded = [
                token.replace("${PORT}", str(port)).replace("${HOST}", host)
                for token in argv
            ]
            env = os.environ.copy()
            env.update(
                {
                    "PORT": str(port),
                    "HOST": host,
                    "SYMPHONY_PREVIEW_SHA": sha,
                }
            )
            kwargs: dict[str, Any] = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            self._phase = "starting"
            self._process = await asyncio.create_subprocess_exec(
                *expanded,
                cwd=workdir,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **kwargs,
            )
            self._pgid = self._process.pid if os.name != "nt" else None
            self._readers = [
                asyncio.create_task(self._read_stream(self._process.stdout, "stdout")),
                asyncio.create_task(self._read_stream(self._process.stderr, "stderr")),
            ]
            self._port, self._target_branch, self._target_sha = port, branch, sha
            self._url = f"http://{host}:{port}{preview.url_path}"
            self._health_url = f"http://{host}:{port}{preview.health_path}"
            self._started_at = datetime.now(timezone.utc).isoformat()
            deadline = time.monotonic() + preview.startup_timeout_ms / 1000
            health_url = self._health_url
            while time.monotonic() < deadline:
                if self._process.returncode is not None:
                    raise ProductPreviewError(
                        f"preview process exited with code {self._process.returncode}"
                    )
                if await self._probe(health_url):
                    # A second probe plus a live leader check reduces the free-
                    # port race certifying an unrelated listener.
                    await asyncio.sleep(0.05)
                    if self._process.returncode is None and await self._probe(health_url):
                        self._healthy = True
                        self._phase = "healthy"
                        return
                await asyncio.sleep(0.1)
            raise ProductPreviewError(f"preview health check timed out: {health_url}")
        except Exception as exc:
            self._last_error = str(exc)
            await self._terminate_process()
            self._health_url = None
            if self._repo is not None and self._checkout is not None:
                with contextlib.suppress(Exception):
                    await self._remove_worktree(self._repo, self._checkout)
                self._repo = None
                self._checkout = None
            self._phase = "failed"
            raise ProductPreviewError(str(exc)) from exc

    async def _status_unlocked(self, cfg: ServiceConfig | None) -> dict[str, Any]:
        proc = self._process
        running = proc is not None and proc.returncode is None
        if proc is not None and not running and self._phase not in {"failed", "stopped"}:
            self._phase = "failed"
            self._healthy = False
            self._last_error = f"preview process exited with code {proc.returncode}"
        elif running and self._health_url is not None:
            # Readiness is live state, not a startup latch.  The web UI polls
            # this method, so re-probing here detects a serving process that
            # remains alive while its product endpoint becomes unhealthy.
            self._healthy = await self._probe(self._health_url)
            if self._healthy:
                self._phase = "healthy"
                self._last_error = None
            else:
                self._phase = "unhealthy"
                self._last_error = f"preview health check failed: {self._health_url}"
        return {
            "phase": self._phase,
            "running": running,
            "healthy": bool(running and self._healthy),
            "ready": bool(running and self._healthy),
            "url": self._url,
            "port": self._port,
            "pid": proc.pid if running and proc is not None else None,
            "target_branch": self._target_branch,
            "target_sha": self._target_sha,
            "started_at": self._started_at,
            "last_error": self._last_error,
            "logs": list(self._logs),
        }

    async def _stop_unlocked(self, *, remove_checkout: bool) -> None:
        if self._process is not None and self._process.returncode is None:
            self._phase = "stopping"
        await self._terminate_process()
        # Stopping is an explicit recovery action. Do not let a transient live
        # health failure make the now-stopped preview render as failed; retain
        # only a cleanup error discovered during this stop operation.
        self._last_error = None
        if remove_checkout and self._repo is not None and self._checkout is not None:
            try:
                await self._remove_worktree(self._repo, self._checkout)
                self._repo = None
                self._checkout = None
            except Exception as exc:
                self._last_error = f"preview checkout cleanup failed: {exc}"
        self._phase = "stopped"
        self._healthy = False
        self._health_url = None
        self._process = None

    async def _terminate_process(self) -> None:
        proc = self._process
        pgid = self._pgid
        if proc is not None and proc.returncode is None:
            await terminate_process_tree(proc)
        # The session leader may exit after forking the real server. The
        # existing helper intentionally returns early for an exited leader,
        # so explicitly finish the owned process group on POSIX.
        if pgid is not None and os.name != "nt":
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGTERM)
            await asyncio.sleep(0.05)
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGKILL)
        self._pgid = None
        for task in self._readers:
            if not task.done():
                task.cancel()
        if self._readers:
            await asyncio.gather(*self._readers, return_exceptions=True)
        self._readers = []

    async def _read_stream(
        self, stream: asyncio.StreamReader | None, stream_name: str
    ) -> None:
        if stream is None:
            return
        while line := await stream.readline():
            self._logs.append(
                {"stream": stream_name, "line": line.decode(errors="replace").rstrip()}
            )

    async def _remove_worktree(self, repo: Path, checkout: Path) -> None:
        await self._git(repo, "worktree", "remove", "--force", str(checkout), check=False)
        await self._git(repo, "worktree", "prune", check=False)
        if checkout.exists():
            raise ProductPreviewError(f"refusing to delete unmanaged path: {checkout}")

    @staticmethod
    async def _git(cwd: Path, *args: str, check: bool = True) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        if check and proc.returncode != 0:
            raise ProductPreviewError(err.decode(errors="replace").strip() or "git failed")
        return out.decode(errors="replace")

    @staticmethod
    def _allocate_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @staticmethod
    async def _probe(url: str) -> bool:
        def get() -> bool:
            try:
                with urlopen(url, timeout=0.5) as response:  # noqa: S310 - loopback URL
                    return 200 <= int(response.status) < 400
            except Exception:
                return False
        return await asyncio.to_thread(get)
