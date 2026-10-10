"""Capability skips that become failures when PM proof is required."""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import NoReturn

import pytest


def fake_node(tmp_path: Path, version: str) -> Path:
    """A PATH-local probe executable, independent of the host's Node version."""
    executable = tmp_path / "node"
    executable.write_text(
        "#!/bin/sh\nprintf '%s\\n' '" + json.dumps(["/x/node", version]) + "'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def skip_or_fail(reason: str, *, flag: str) -> NoReturn:
    if os.environ.get(flag) == "1":
        pytest.fail(reason)
    pytest.skip(reason)
