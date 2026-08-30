"""Normalized provider quota and usage telemetry model.

Provides provider-independent usage snapshot structures, probe interface,
and the USAGE_PROBES registry for usage-aware agent profiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class UsageWindow:
    key: str
    used_percent: float | None
    remaining_percent: float | None
    resets_at: datetime | None = None


@dataclass(frozen=True)
class ProviderCreditInfo:
    has_credits: bool
    unlimited: bool
    balance: str | None = None


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
    credits: ProviderCreditInfo | None = None


@runtime_checkable
class UsageProbe(Protocol):
    async def fetch_usage(self) -> ProviderUsageSnapshot | None: ...


# Source name aliases mapped to their canonical probe source.
USAGE_SOURCE_ALIASES = {
    "github-copilot": "copilot",
}

# Registry mapping source name -> UsageProbe class/factory.
# Missing or unsupported probes return None (fail open).
USAGE_PROBES: dict[str, type[UsageProbe]] = {}


def get_usage_probe(source: str) -> type[UsageProbe] | None:
    """Retrieve the probe class for a given usage pool source, or None if unsupported (fail open)."""
    source = USAGE_SOURCE_ALIASES.get(source, source)

    if source == "codex" and "codex" not in USAGE_PROBES:
        from .codex import CodexUsageProbe

        USAGE_PROBES["codex"] = CodexUsageProbe
    elif source == "claude" and "claude" not in USAGE_PROBES:
        from .claude_code import ClaudeUsageProbe

        USAGE_PROBES["claude"] = ClaudeUsageProbe
    elif source == "agy" and "agy" not in USAGE_PROBES:
        from .agy import AgyUsageProbe

        USAGE_PROBES["agy"] = AgyUsageProbe
    elif source == "gemini" and "gemini" not in USAGE_PROBES:
        from .gemini import GeminiUsageProbe

        USAGE_PROBES["gemini"] = GeminiUsageProbe
    elif source == "kiro" and "kiro" not in USAGE_PROBES:
        from .kiro import KiroUsageProbe

        USAGE_PROBES["kiro"] = KiroUsageProbe
    elif (source in ("opencode", "opencode-go")) and ("opencode-go" not in USAGE_PROBES):
        from .opencode import OpenCodeGoUsageProbe

        USAGE_PROBES["opencode-go"] = OpenCodeGoUsageProbe
        USAGE_PROBES["opencode"] = OpenCodeGoUsageProbe
    elif source == "copilot" and "copilot" not in USAGE_PROBES:
        from .copilot import CopilotUsageProbe

        USAGE_PROBES["copilot"] = CopilotUsageProbe

    return USAGE_PROBES.get(source)
