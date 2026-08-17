"""Normalized provider quota and usage telemetry model.

Provides provider-independent usage snapshot structures, probe interface,
and the USAGE_PROBES registry for usage-aware agent profiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class UsageWindow:
    key: str
    used_percent: float | None
    remaining_percent: float | None
    resets_at: datetime | None = None


@dataclass(frozen=True)
class ProviderUsageSnapshot:
    pool_id: str
    source: str
    windows: dict[str, UsageWindow] = field(default_factory=dict)
    hard_limit_reached: bool = False
    # Only authoritative telemetry may block scheduling.
    authoritative: bool = True
    observed_at: datetime | None = None
    stale: bool = False


@runtime_checkable
class UsageProbe(Protocol):
    async def fetch_usage(self) -> ProviderUsageSnapshot | None: ...


# Registry mapping source name -> UsageProbe class/factory.
# Missing or unsupported probes return None (fail open).
USAGE_PROBES: dict[str, type[UsageProbe]] = {}


def get_usage_probe(source: str) -> type[UsageProbe] | None:
    """Retrieve the probe class for a given usage pool source, or None if unsupported (fail open)."""
    if source == "codex" and "codex" not in USAGE_PROBES:
        from .codex import CodexUsageProbe

        USAGE_PROBES["codex"] = CodexUsageProbe
    return USAGE_PROBES.get(source)

