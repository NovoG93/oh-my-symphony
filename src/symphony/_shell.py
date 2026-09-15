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
from typing import Any, Callable

from .logging import get_logger


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

# POSIX-only signal API resolved once so this module imports and type-checks
# on win32 too; every caller guards with a platform check before reaching it.
# On POSIX `os.killpg` always exists, so the runtime behavior is unchanged.
_killpg: Callable[[int, int], None] | None = getattr(os, "killpg", None)

# Bounded so `terminate_process_tree`'s documented bounded-wait contract
# holds for its first Windows step too (taskkill itself can hang when a
# child refuses to die).
_TASKKILL_TIMEOUT_S = 10.0

log = get_logger()

_warned_kill_without_identity = False


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


# Grace given to the child watcher before ``safe_proc_wait`` concludes the
# watcher has stalled on a dead child (a healthy watcher reaps within
# milliseconds of the child exiting).
_WATCHER_GRACE_S = 0.25
# Poll cadence of the untimed wait path: how often it checks whether the
# child is dead-but-unreaped while the watcher has not delivered a
# returncode. /proc reads are cheap on Linux; other platforms fork a ``ps``,
# so they poll less aggressively.
_WATCHER_POLL_INTERVAL_S = 0.5 if sys.platform.startswith("linux") else 2.0


def _child_is_zombie(pid: int) -> bool:
    """Return True when *pid* is an exited-but-unreaped child (a zombie).

    A zombie means the process is dead and nobody has reaped it yet. On a
    healthy event loop the child watcher reaps within milliseconds, so a
    zombie observed while ``proc.wait()`` is still pending means the
    watcher has stalled for this child — the exact failure mode the
    thread-based fallback was written for.
    """
    if pid <= 0:
        return False
    if sys.platform.startswith("linux"):
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except OSError:
            return False
        try:
            state = raw.rsplit(")", 1)[1].split()[0]
        except IndexError:
            return False
        return state == "Z"
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            check=False,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip().upper().startswith("Z")


def _child_is_absent(pid: int) -> bool:
    """Return True when *pid* no longer exists at all (reaped and gone).

    Distinct from ``_child_is_zombie``: an absent pid has no process-table
    entry left. Observed while ``wait()`` is still pending it means the
    child was already reaped outside the watcher (a prior take-over) — the
    watcher can never deliver a returncode for it, so polling is futile.
    """
    if pid <= 0:
        return True
    if sys.platform.startswith("linux"):
        return not Path(f"/proc/{pid}/stat").exists()
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            check=False,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 1 and not result.stdout.strip()


def _reap_blocking(pid: int) -> int | None:
    """Reap *pid* with a blocking ``waitpid``; None when already reaped."""
    try:
        _, status = os.waitpid(pid, 0)
    except ChildProcessError:
        # Already reaped elsewhere (the asyncio child watcher won the race).
        # The status lives in the watcher now; callers recover it through
        # ``proc.wait()``.
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


async def _recover_via_wait(proc: Any, wait: Callable[..., Any]) -> int | None:
    """Pick up the exit code the child watcher reaped (race recovery)."""
    try:
        return await asyncio.wait_for(wait(), timeout=_WATCHER_GRACE_S)
    except asyncio.TimeoutError:
        return proc.returncode


async def _wait_watcher_or_take_over(
    proc: Any, wait: Callable[..., Any], pid: int
) -> int | None:
    """Untimed wait: rely on the watcher, take over only on a proven stall.

    The historic macOS/Textual failure mode is a child watcher that never
    delivers the returncode. To preserve that workaround without racing a
    healthy watcher, we poll for a zombie child (dead, unreaped) and only
    then bypass the watcher. A healthy watcher reaps a dead child in
    milliseconds, so the bypass never fires on healthy systems.
    """
    while True:
        try:
            return await asyncio.wait_for(wait(), timeout=_WATCHER_POLL_INTERVAL_S)
        except asyncio.TimeoutError:
            pass
        if proc.returncode is not None:
            return proc.returncode
        if not _child_is_zombie(pid):
            if _child_is_absent(pid):
                # The pid is gone and the watcher has not delivered a
                # returncode: this child was already reaped (typically by a
                # prior take-over of this same wait) and can never come back.
                # One blocking reap settles it — ECHILD on an already-reaped
                # pid — and returns None promptly instead of polling forever.
                return await asyncio.to_thread(_reap_blocking, pid)
            continue  # still running (or watcher about to deliver) — keep waiting
        # Dead but unreaped: the watcher stalled. One grace round in case it
        # is merely slow, then take over the reap.
        try:
            return await asyncio.wait_for(wait(), timeout=_WATCHER_GRACE_S)
        except asyncio.TimeoutError:
            pass
        if proc.returncode is not None:
            return proc.returncode
        rc = await asyncio.to_thread(_reap_blocking, pid)
        if rc is not None:
            return rc
        return await _recover_via_wait(proc, wait)


async def safe_proc_wait(proc: Any, *, timeout: float | None = None) -> int | None:
    """Wait for an asyncio subprocess to exit — watcher-first, race-free.

    Background: Python 3.12 + asyncio + Textual on macOS sometimes leaves the
    child watcher unable to deliver the returncode for processes spawned via
    ``asyncio.create_subprocess_exec`` — ``await proc.wait()`` then never
    returns. The original workaround reaped with a raw ``os.waitpid`` in a
    worker thread, but that races the child watcher: when the thread wins,
    asyncio logs "… exit status already read: will report returncode 255"
    and corrupts ``proc.returncode`` to 255 — one line per spawned process
    in the journal (the engine spam) and a wrong exit code for callers.

    The watcher owns the reap. This helper waits on ``proc.wait()`` and only
    bypasses the watcher when the child is demonstrably dead-but-unreaped
    (a zombie) while no returncode was delivered — i.e. the watcher has
    provably stalled. On a healthy loop the raw ``waitpid`` never runs.

    `timeout` is in seconds. Returns the exit code, or ``None`` on timeout
    while the child is still running (caller is responsible for sending
    SIGKILL and retrying). The timed path may overshoot `timeout` by a
    small bounded grace (``_WATCHER_GRACE_S`` plus one wait/re-check round)
    used to tell a still-running child from a stalled watcher; it never
    waits for the child itself past that. With ``timeout=None`` the wait is
    unbounded but still takes over the reap when the watcher stalls on a
    dead child; if the child was already reaped by a prior take-over (or
    elsewhere), a later untimed wait returns ``None`` promptly instead of
    polling a pid that no longer exists.

    Windows note: ``os.waitpid`` and the ``WIF*`` helpers are POSIX-only, so
    on Windows we delegate to the asyncio child transport's own ``wait()``
    — the SIGCHLD race the fallback targets does not exist there.
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

    wait = getattr(proc, "wait", None)
    if wait is None:
        # Not spawned through asyncio (plain Popen): no watcher tracks the
        # child, so a worker-thread waitpid is the only reaper and cannot
        # race anything.
        if timeout is None:
            return await asyncio.to_thread(_reap_blocking, pid)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_reap_blocking, pid), timeout=timeout
            )
        except asyncio.TimeoutError:
            return None

    if timeout is None:
        return await _wait_watcher_or_take_over(proc, wait, pid)

    try:
        return await asyncio.wait_for(wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass

    # The watcher did not deliver within `timeout`. Give it a short grace
    # (the child may have exited just at the deadline), then distinguish:
    # still running -> keep the timeout contract; dead-but-unreaped -> the
    # watcher stalled, take over the reap.
    await asyncio.sleep(_WATCHER_GRACE_S)
    if proc.returncode is not None:
        return proc.returncode
    if not _child_is_zombie(pid):
        return None
    try:
        return await asyncio.wait_for(wait(), timeout=_WATCHER_GRACE_S)
    except asyncio.TimeoutError:
        pass
    if proc.returncode is not None:
        return proc.returncode
    rc = await asyncio.to_thread(_reap_blocking, pid)
    if rc is not None:
        return rc
    return await _recover_via_wait(proc, wait)


def _signal_process_group(pid: int, sig: int) -> bool:
    """Signal the child's process group; fall back to the single pid.

    Requires the child to have been spawned with ``start_new_session=True``
    so it leads its own group — otherwise ``killpg`` raises and we fall back
    to signalling only the direct child (the pre-R2 behavior).

    POSIX-only: every caller reaches here behind a ``sys.platform`` gate,
    and the ``_killpg`` binding is only ``None`` on win32.
    """
    assert _killpg is not None
    try:
        _killpg(pid, sig)
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
    except PermissionError:
        # macOS: once a SIGKILLed process turns into a zombie, killpg(pid, 0)
        # raises EPERM even though the (unreaped) process group still exists.
        # The ps probe below filters zombies authoritatively, so fall through
        # instead of reporting an ambiguous "unknown" that can never confirm
        # a successful kill.
        pass
    except OSError:
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


def _taskkill_tree(pid: int, *, force: bool = True) -> bool:
    """Windows process-tree termination via ``taskkill /PID <pid> /T [/F]``.

    ``/T`` walks the whole descendant tree, so a ``bash -lc <agent cli>``
    wrapper and the real agent CLI grandchildren go down together — the
    Windows equivalent of SIGKILLing a POSIX process group. Output is
    discarded and every failure mode (missing taskkill, dead pid, access
    denied, taskkill hanging past ``_TASKKILL_TIMEOUT_S``) collapses to
    ``False`` so callers can fall back to their own ladders.
    """
    cmd = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        cmd.append("/F")
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=_TASKKILL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        return False
    return completed.returncode == 0


def kill_process_group(pid: int, *, identity: str | None = None) -> bool:
    """Sync best-effort SIGKILL of a process group by pid.

    For force-eject paths that hold only a recorded pid (no proc object to
    reap through). The killed children are reaped by the asyncio child
    watcher or become inherited zombies until process exit — still strictly
    better than a live agent CLI burning tokens in a reused worktree.

    ``identity`` is an optional :func:`process_identity` fingerprint
    captured when the pid was recorded. When provided and the live pid's
    fingerprint no longer matches (the process died and the OS reused its
    pid), the kill is skipped: signalling would hit an unrelated process
    tree. Without an identity the kill proceeds as before — recorded pids
    predate the fingerprint machinery — but the hazard is logged once per
    process. Callers that can capture an identity should pass it; wiring
    the orchestrator call sites to do so is tracked for the core.py lane.
    """
    global _warned_kill_without_identity
    if identity is not None and process_identity(pid) != identity:
        log.warning("kill_process_group_identity_mismatch", pid=pid)
        return False
    if identity is None and not _warned_kill_without_identity:
        _warned_kill_without_identity = True
        get_logger().warning(
            "kill_process_group_without_identity",
            pid=pid,
            hint="pid reuse could kill an unrelated process tree; "
            "capture process_identity() when the pid is recorded",
        )
    if sys.platform == "win32":
        # No process groups — taskkill /T is the closest tree-wide analog.
        return _taskkill_tree(pid)
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

    On Windows there are no process groups or signals; the tree is taken
    down with ``taskkill /T /F`` first (itself bounded at
    ``_TASKKILL_TIMEOUT_S`` — ``_taskkill_tree`` converts a hang into
    ``False``), with the single-process terminate/kill ladder kept as a
    fallback for taskkill-less hosts.
    """
    if proc.returncode is not None:
        return proc.returncode
    pid = proc.pid
    if sys.platform == "win32":
        if pid is not None:
            # `_taskkill_tree` never raises (OSError/timeout collapse to
            # False internally), so there is nothing to guard here.
            await asyncio.to_thread(_taskkill_tree, pid)
        try:
            proc.terminate()
        except OSError:
            # ProcessLookupError (already gone) or PermissionError
            # (already terminated but not yet reaped) — both are fine;
            # safe_proc_wait below is the source of truth.
            pass
        rc = await safe_proc_wait(proc, timeout=term_timeout)
        if rc is None and proc.returncode is None:
            try:
                proc.kill()
            except OSError:
                pass
            rc = await safe_proc_wait(proc, timeout=kill_timeout)
        return proc.returncode if proc.returncode is not None else rc

    if pid is None:
        # No pid to signal — single-process ladder.
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
