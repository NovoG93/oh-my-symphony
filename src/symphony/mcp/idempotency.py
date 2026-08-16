"""Idempotency for request creation: client_request_id -> issue_id."""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path


class IdempotencyStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS requests ("
            " client_request_id TEXT PRIMARY KEY,"
            " issue_id TEXT NOT NULL,"
            " created_at TEXT NOT NULL)"
        )
        self._conn.commit()

    def get(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT issue_id FROM requests WHERE client_request_id = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def put(self, key: str, issue_id: str) -> None:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR IGNORE INTO requests (client_request_id, issue_id, created_at)"
            " VALUES (?, ?, ?)",
            (key, issue_id, now),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
