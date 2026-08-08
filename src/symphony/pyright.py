"""Portable Pyright command used by developers and CI.

The package entry point intentionally starts Pyright with the interpreter that
is running this wrapper.  Pyright otherwise does not reliably discover
third-party packages from that environment when it is launched through
``python -m pyright``.
"""

from __future__ import annotations

from collections.abc import Sequence
import subprocess
import sys


def _without_pythonpath(args: Sequence[str]) -> list[str]:
    """Remove caller-provided interpreter overrides.

    ``--pythonpath`` belongs to this wrapper: allowing a second value would
    make Pyright reject the command, or let a caller analyze against a
    different environment than the one running the wrapper.
    """
    forwarded: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--pythonpath":
            index += 1
            if index < len(args) and not args[index].startswith("-"):
                index += 1
            continue
        if arg.startswith("--pythonpath="):
            index += 1
            continue
        forwarded.append(arg)
        index += 1
    return forwarded


def build_command(args: Sequence[str] = ()) -> list[str]:
    """Build the subprocess argv for Pyright using this Python interpreter."""
    interpreter = sys.executable
    if not interpreter:
        raise RuntimeError("the Python interpreter path is unavailable")
    return [
        interpreter,
        "-m",
        "pyright",
        "--pythonpath",
        interpreter,
        *_without_pythonpath(args),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Run Pyright and return its exit status unchanged."""
    args = sys.argv[1:] if argv is None else argv
    return subprocess.run(build_command(args), check=False).returncode


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
