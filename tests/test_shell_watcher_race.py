"""Regression tests for the child-watcher reap race behind ``safe_proc_wait``.

Background: ``safe_proc_wait`` used to reap asyncio-tracked children with a raw
``os.waitpid(pid, 0)`` in a worker thread.  That races the event loop's child
watcher (``_PidfdChildWatcher`` on Linux, ``_ThreadedChildWatcher`` elsewhere):
when the worker thread wins, the watcher's own ``waitpid`` raises
``ChildProcessError``, asyncio logs ``... will report returncode 255`` into the
journal on every single probe/teardown, and ``proc.returncode`` is corrupted to
255 even though the true exit code was reaped by the worker thread.

The contract these tests pin:

* ``safe_proc_wait`` must never reap behind a functioning child watcher —
  the watcher owns the reap, and the true exit code must be returned.
* When the watcher is broken/stalled on a dead child (the macOS/Textual
  failure mode the helper exists for), ``safe_proc_wait`` must still take
  over the reap and return the true exit code — without hanging forever.
* ``terminate_process_tree`` must preserve timeout/kill semantics and report
  the true exit code (never the fabricated 255).

The slow-watcher patch below is the deterministic seam for the race: it makes
the watcher lose every time, so the pre-fix code reliably triggers the warning
and the 255 corruption instead of depending on scheduling luck.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Iterator

import pytest

from symphony._shell import safe_proc_wait, terminate_process_tree

REDACT_FRAGMENT = "will report returncode 255"


def _watcher_miss_the_reap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the loop's child watcher slow enough to always lose the race."""
    loop = asyncio.get_running_loop()
    watcher = getattr(loop, "_watcher", None)
    if watcher is None:
        pytest.skip("no per-loop child watcher on this platform")
    assert watcher is not None  # narrow for type checkers
    name = type(watcher).__name__
    if name == "_ThreadedChildWatcher":
        # The watcher's dedicated thread blocks in waitpid; delay its entry
        # so the worker thread in safe_proc_wait reaps first.
        original = watcher._do_waitpid

        def slow_do_waitpid(loop_, expected_pid, callback, args):
            time.sleep(0.3)
            return original(loop_, expected_pid, callback, args)

        monkeypatch.setattr(watcher, "_do_waitpid", slow_do_waitpid)
    elif name == "_PidfdChildWatcher":
        # _do_wait runs on the loop, so it cannot sleep without deadlocking
        # the test itself; defer the reap one timer tick instead.
        original = watcher._do_wait
        deferred: list[bool] = []

        def defer_do_wait(pid, pidfd, callback, args):
            if deferred:
                return
            deferred.append(True)
            asyncio.get_running_loop().call_later(
                0.3, original, pid, pidfd, callback, args
            )

        monkeypatch.setattr(watcher, "_do_wait", defer_do_wait)
    else:  # pragma: no cover - future watcher implementations
        pytest.skip(f"unexpected child watcher: {name}")


@pytest.fixture
def asyncio_warning_log() -> Iterator[list[str]]:
    """Capture warnings emitted on the ``asyncio`` logger (the journal source)."""
    records: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record.getMessage())
    logger = logging.getLogger("asyncio")
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


async def _spawn(
    command: str, *, start_new_session: bool = False
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        "bash",
        "-lc",
        command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=start_new_session,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX waitpid semantics")
async def test_safe_proc_wait_never_reaps_behind_healthy_watcher(
    monkeypatch: pytest.MonkeyPatch, asyncio_warning_log: list[str]
) -> None:
    """The child watcher owns the reap; the worker-thread waitpid race is gone.

    Pre-fix this is deterministically RED: the worker thread reaps the child
    before the delayed watcher wakes, so asyncio logs ``... will report
    returncode 255`` and corrupts ``proc.returncode`` to 255.
    """
    _watcher_miss_the_reap(monkeypatch)
    proc = await _spawn("exit 7")

    rc = await safe_proc_wait(proc, timeout=5.0)
    # Let a losing watcher finish its reap attempt and deliver its verdict.
    await asyncio.sleep(0.6)

    assert rc == 7
    assert proc.returncode == 7
    assert not [m for m in asyncio_warning_log if REDACT_FRAGMENT in m]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX waitpid semantics")
async def test_safe_proc_wait_takes_over_when_watcher_never_registers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The macOS/Textual workaround must survive: a watcher that never sees
    the child must not hang ``safe_proc_wait`` forever — it takes over the
    reap and returns the true exit code."""
    loop = asyncio.get_running_loop()
    watcher = getattr(loop, "_watcher", None)
    if watcher is None:
        pytest.skip("no per-loop child watcher on this platform")
    monkeypatch.setattr(watcher, "add_child_handler", lambda *a, **k: None)

    proc = await _spawn("exit 7")
    started = time.monotonic()
    rc = await safe_proc_wait(proc)

    assert rc == 7
    assert time.monotonic() - started < 5.0


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX waitpid semantics")
async def test_terminate_process_tree_reports_true_exit_code(
    monkeypatch: pytest.MonkeyPatch, asyncio_warning_log: list[str]
) -> None:
    """Teardown must never report the watcher's fabricated 255.

    Pre-fix this is deterministically RED: the child exits while the delayed
    watcher is still sleeping, the worker thread reaps the true status, and
    the watcher then overwrites ``proc.returncode`` with 255.
    """
    _watcher_miss_the_reap(monkeypatch)
    proc = await _spawn("exit 7", start_new_session=True)

    rc = await terminate_process_tree(proc)
    await asyncio.sleep(0.6)

    # The child dies either by SIGTERM (-15) or by its own ``exit 7`` —
    # whichever is faster. Either way the reported code must be the true
    # one, never the watcher's fabricated 255.
    assert rc is not None and rc != 255
    assert proc.returncode == rc
    assert not [m for m in asyncio_warning_log if REDACT_FRAGMENT in m]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX waitpid semantics")
async def test_safe_proc_wait_timeout_preserved_for_running_child() -> None:
    """A still-running child past the timeout returns None (caller escalates
    the kill ladder) — and does not hang in grace handling."""
    proc = await _spawn("sleep 30", start_new_session=True)
    try:
        started = time.monotonic()
        rc = await safe_proc_wait(proc, timeout=0.2)
        assert rc is None
        assert time.monotonic() - started < 1.5
    finally:
        await terminate_process_tree(proc)
