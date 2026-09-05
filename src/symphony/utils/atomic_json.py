"""Small helpers for resilient, workflow-scoped JSON state files."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any


_REPLACE_ATTEMPTS = 4
_REPLACE_RETRY_DELAY_SECONDS = 0.025
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def state_file_name(workflow_path: str | Path, base_name: str) -> str:
    """Return the state filename associated with ``workflow_path``.

    ``WORKFLOW.md`` is the long-standing single-workflow filename, so its
    state files retain their old names for backwards compatibility.  A
    differently named workflow in the same directory gets a deterministic,
    filesystem-safe suffix to prevent the two orchestrators sharing EMA or
    Done-counter state.
    """

    workflow = Path(workflow_path)
    base = Path(base_name).name
    if workflow.name == "WORKFLOW.md":
        return f"{base}.json"

    stem = _SAFE_NAME.sub("-", workflow.stem).strip("-._") or "workflow"
    return f"{base}.{stem}.json"


def write_json_atomic(path: str | Path, payload: Any) -> None:
    """Write JSON to ``path`` with a same-directory replace.

    Windows may briefly reject replacing a file held by an antivirus scanner
    or another reader.  Retry that specific failure a bounded number of times;
    all temporary files are removed when serialization, writing, or replace
    ultimately fails.
    """

    destination = Path(path)
    temporary_name: str | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(payload, temporary, sort_keys=True, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())

        assert temporary_name is not None
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(temporary_name, destination)
                temporary_name = None
                return
            except PermissionError:
                if attempt + 1 == _REPLACE_ATTEMPTS:
                    raise
                time.sleep(_REPLACE_RETRY_DELAY_SECONDS)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
