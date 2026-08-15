"""Structured JSON-lines audit logging for mutating operations."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_REDACT_KEYS = ("token", "key", "secret", "password", "authorization", "credential")


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: ("[REDACTED]" if any(r in k.lower() for r in _REDACT_KEYS) else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def audit(path: Path, entry: dict) -> None:
    """Append a redacted JSON record to the audit log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **_redact(entry),
    }
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
