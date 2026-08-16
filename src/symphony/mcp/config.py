"""Configuration for the symphony-mcp gateway (env-driven; YAML optional later)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    host: str = "0.0.0.0"
    port: int = 8080
    symphony_base_url: str = "http://127.0.0.1:9999"
    timeout_seconds: float = 30.0
    token: str | None = None
    allowed_projects: frozenset[str] = field(default_factory=frozenset)
    allow_control: bool = False
    audit_log: Path = field(
        default_factory=lambda: Path("~/.local/state/symphony-mcp/audit.jsonl").expanduser()
    )
    idempotency_db: Path = field(
        default_factory=lambda: Path("~/.local/state/symphony-mcp/idempotency.sqlite3").expanduser()
    )


def _read_token() -> str | None:
    token = os.environ.get("SYMPHONY_MCP_TOKEN")
    if token:
        return token
    token_file = Path(
        os.environ.get("SYMPHONY_MCP_TOKEN_FILE", "~/.config/symphony-mcp/token")
    ).expanduser()
    if token_file.exists():
        value = token_file.read_text().strip()
        return value or None
    return None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load() -> Settings:
    allowed = frozenset(
        p.strip()
        for p in os.environ.get("SYMPHONY_MCP_ALLOWED_PROJECTS", "").split(",")
        if p.strip()
    )
    return Settings(
        host=os.environ.get("SYMPHONY_MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("SYMPHONY_MCP_PORT", "8080")),
        symphony_base_url=os.environ.get("SYMPHONY_BASE_URL", "http://127.0.0.1:9999"),
        timeout_seconds=float(os.environ.get("SYMPHONY_MCP_TIMEOUT", "30")),
        token=_read_token(),
        allowed_projects=allowed,
        allow_control=_env_bool("SYMPHONY_MCP_ALLOW_CONTROL", default=False),
    )
