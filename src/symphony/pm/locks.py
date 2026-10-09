"""Cross-process per-change authority lock shared by PM mutations and launch."""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager

from ..orchestrator.run_registry import RunRegistry


@contextmanager
def change_authority_lock(
    registry: RunRegistry, project_id: str, change_id: str
):
    root = registry.path.parent / "pm-locks"
    root.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(f"{project_id}:{change_id}".encode()).hexdigest()
    handle = (root / f"{name}.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
