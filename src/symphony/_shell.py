"""Shell resolution — pick a bash binary that can actually run Symphony's hooks.

On Windows, ``where bash`` often returns:

    C:\\Windows\\System32\\bash.exe          # WSL launcher
    C:\\Program Files\\Git\\usr\\bin\\bash.exe   # Git Bash (MSYS)
    C:\\Users\\<u>\\AppData\\Local\\Microsoft\\WindowsApps\\bash.exe   # WSL alias

The WSL launcher is the wrong choice for Symphony: WSL mounts Windows drives
at ``/mnt/c/...`` (not ``/c/...``), can't transparently invoke Windows ``.exe``
files in user hooks, and runs in a separate Linux filesystem from the
workspace cwd. We want MSYS ``bash`` (Git Bash), which speaks ``/c/...``,
inherits the Windows cwd verbatim, and runs Windows binaries directly.

This helper centralizes the lookup so every hook + backend uses the same
binary. Set ``SYMPHONY_BASH`` to override.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import os
import shutil
import signal
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any


# Common Git for Windows install locations. Scoop and Winget installs land
# under ``%USERPROFILE%\scoop\apps\git\current\bin\bash.exe`` and
# ``%LOCALAPPDATA%\Programs\Git\bin\bash.exe`` respectively — those are
# picked up by the ``shutil.which`` fallback below if the user's PATH is set
# correctly. Extend this tuple if you need to support those installs without
# requiring PATH config.
_WIN_GIT_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
)

_WSL_LAUNCHER_FRAGMENTS = (
    r"\windows\system32\bash.exe",
    r"\microsoft\windowsapps\bash.exe",
)


def _is_wsl_launcher(path: str) -> bool:
    p = path.lower()
    return any(frag in p for frag in _WSL_LAUNCHER_FRAGMENTS)


@lru_cache(maxsize=1)
def resolve_bash() -> str:
    """Return the bash executable to use for hooks and backend subprocesses.

    Result is cached for the process lifetime — set ``SYMPHONY_BASH`` before
    the first call (typically before importing symphony) if you need to
    override. Tests that toggle the env var mid-process must call
    ``resolve_bash.cache_clear()`` between toggles.

    A ``SYMPHONY_BASH`` override pointing at the Windows WSL launcher is
    rejected (the whole reason this helper exists is to avoid that binary);
    we fall through to the default detection so a misconfigured override
    can't silently re-introduce the bug. Other override values are returned
    as-is and validated by ``doctor.check_shell``.
    """
    override = os.environ.get("SYMPHONY_BASH")
    if override:
        if _is_wsl_launcher(override):
            # Misconfiguration — fall through to default detection rather
            # than honor a value we know will not work for hooks.
            pass
        else:
            return override

    if sys.platform != "win32":
        return "bash"

    for candidate in _WIN_GIT_BASH_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate

    found = shutil.which("bash")
    if found and not _is_wsl_launcher(found):
        return found

    # Last resort: bare ``bash`` so spawn-time error is reproducible from
    # the doctor's ``check_shell`` failure (instead of failing at the first
    # hook dispatch with an opaque ``FileNotFoundError``).
    return "bash"


async def safe_proc_wait(proc: Any, *, timeout: float | None = None) -> int | None:
    """Reap an asyncio subprocess without depending on the child watcher.

    Background: Python 3.12 + asyncio + Textual on macOS sometimes leaves the
    child watcher unable to observe SIGCHLD for processes spawned via
    ``asyncio.create_subprocess_exec``. The visible symptom is a zombie
    ``<defunct>`` child plus an ``await proc.wait()`` that never returns.

    Workaround: do the wait in a worker thread via ``os.waitpid``. The thread
    blocks in the kernel until the child exits — independent of any asyncio
    watcher state — and yields back to the loop the moment the kernel hands
    over the exit status.

    `timeout` is in seconds. Returns the exit code, or ``None`` on timeout
    (caller is responsible for sending SIGKILL and retrying).

    Windows note: ``os.waitpid`` and the ``WIF*`` helpers are POSIX-only, so
    on Windows we delegate to the asyncio child transport's own ``wait()``
    — the SIGCHLD race the workaround targets does not exist there.
    """
    if proc.returncode is not None:
        return proc.returncode

    if sys.platform == "win32":
        try:
            if timeout is None:
                return await proc.wait()
            return await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    pid = proc.pid
    if pid is None:
        return None

    async def _asyncio_returncode_fallback() -> int | None:
        if proc.returncode is not None:
            return proc.returncode
        wait = getattr(proc, "wait", None)
        if wait is None:
            return None
        try:
            return await asyncio.wait_for(wait(), timeout=0.05)
        except asyncio.TimeoutError:
            return proc.returncode

    def _blocking_wait() -> int | None:
        try:
            _, status = os.waitpid(pid, 0)
        except ChildProcessError:
            # Already reaped (asyncio watcher won the race, or never
            # registered). Either way, nothing more to do.
            return None
        except OSError as exc:
            if exc.errno == errno.ECHILD:
                return None
            raise
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -os.WTERMSIG(status)
        return None

    if timeout is None:
        rc = await asyncio.to_thread(_blocking_wait)
        if rc is not None:
            return rc
        return await _asyncio_returncode_fallback()
    try:
        rc = await asyncio.wait_for(asyncio.to_thread(_blocking_wait), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    if rc is not None:
        return rc
    return await _asyncio_returncode_fallback()


def _signal_process_group(pid: int, sig: int) -> bool:
    """Signal the child's process group; fall back to the single pid.

    Requires the child to have been spawned with ``start_new_session=True``
    so it leads its own group — otherwise ``killpg`` raises and we fall back
    to signalling only the direct child (the pre-R2 behavior).
    """
    try:
        os.killpg(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        try:
            os.kill(pid, sig)
            return True
        except OSError:
            return False


@lru_cache(maxsize=1)
def _host_boot_token() -> str | None:
    """Return a stable token for the current OS boot when available."""
    if sys.platform.startswith("linux"):
        try:
            return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        except OSError:
            return None
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "kern.boottime"],
                capture_output=True,
                check=False,
                text=True,
                timeout=1,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() or None
    return None


def process_identity(pid: int) -> str | None:
    """Fingerprint one process incarnation, not merely its reusable pid."""
    if pid <= 0:
        return None
    boot = _host_boot_token()
    if not boot:
        return None
    started: str | None = None
    if sys.platform.startswith("linux"):
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            # Field 2 is parenthesized and may contain spaces. Fields after its
            # final ')' start at process state; starttime is field 22 => index 19.
            fields = raw.rsplit(")", 1)[1].split()
            started = fields[19]
        except (OSError, IndexError):
            return None
    elif sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["ps", "-o", "lstart=", "-p", str(pid)],
                capture_output=True,
                check=False,
                text=True,
                timeout=1,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode == 0:
            started = result.stdout.strip() or None
    if not started:
        return None
    return hashlib.sha256(f"{boot}\0{pid}\0{started}".encode()).hexdigest()


def process_group_exists(pid: int) -> bool | None:
    """Return whether a process group has a non-zombie member."""
    if pid <= 0 or sys.platform == "win32":
        return None
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return None
    try:
        group_flag = "--pgroup" if sys.platform.startswith("linux") else "-g"
        result = subprocess.run(
            ["ps", "-o", "stat=", group_flag, str(pid)],
            capture_output=True,
            check=False,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode not in (0, 1):
        return None
    statuses = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not statuses:
        return False if result.returncode == 1 else None
    return any(not status.upper().startswith("Z") for status in statuses)


def kill_process_group(pid: int) -> bool:
    """Sync best-effort SIGKILL of a process group by pid.

    For force-eject paths that hold only a recorded pid (no proc object to
    reap through). The killed children are reaped by the asyncio child
    watcher or become inherited zombies until process exit — still strictly
    better than a live agent CLI burning tokens in a reused worktree.
    """
    if sys.platform == "win32":
        return False
    return _signal_process_group(pid, signal.SIGKILL)


async def terminate_process_tree(
    proc: Any, *, term_timeout: float = 2.0, kill_timeout: float = 5.0
) -> int | None:
    """SIGTERM -> wait -> SIGKILL -> wait, addressing the whole process group.

    Backends spawn ``bash -lc <agent cli>``; signalling only the bash
    wrapper (``proc.terminate()``) orphans the actual agent CLI and its
    children, which keep running and burning tokens. Both waits are bounded
    so a caller can never hang on an unreapable child; returns the exit
    code, or ``None`` if the process could not be reaped in time.
    """
    if proc.returncode is not None:
        return proc.returncode
    pid = proc.pid
    if sys.platform == "win32" or pid is None:
        # No POSIX process groups — single-process ladder.
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        rc = await safe_proc_wait(proc, timeout=term_timeout)
        if rc is None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            rc = await safe_proc_wait(proc, timeout=kill_timeout)
        return proc.returncode if proc.returncode is not None else rc

    _signal_process_group(pid, signal.SIGTERM)
    rc = await safe_proc_wait(proc, timeout=term_timeout)
    if rc is None and proc.returncode is None:
        _signal_process_group(pid, signal.SIGKILL)
        rc = await safe_proc_wait(proc, timeout=kill_timeout)
    return proc.returncode if proc.returncode is not None else rc
