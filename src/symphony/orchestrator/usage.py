"""Provider usage manager and scheduler evaluation.

Provides ProviderUsageManager for caching and evaluating provider quota snapshots
against configured UsagePoolConfig thresholds, with fail-open semantics, cache TTL,
and automatic recovery upon reset.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from enum import Enum
import time
from typing import Callable

from ..logging import get_logger
from ..backends.usage import (
    ProviderUsageSnapshot,
    UsageProbe,
    get_usage_probe,
)
from ..workflow.config import UsagePoolConfig

log = get_logger()


class UsageDecision(str, Enum):
    """Decision outcome of evaluating a usage pool."""

    READY = "ready"
    WAIT_PROVIDER_USAGE = "waiting_provider_usage"


READY = UsageDecision.READY
WAIT_PROVIDER_USAGE = UsageDecision.WAIT_PROVIDER_USAGE

DEFAULT_CACHE_TTL_S: float = 60.0


class ProviderUsageManager:
    """Manages cached provider usage snapshots and evaluates quota eligibility."""

    def __init__(
        self,
        *,
        cache_ttl_s: float = DEFAULT_CACHE_TTL_S,
        probes: dict[str, UsageProbe] | None = None,
        probe_factory: Callable[[str], UsageProbe | None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.snapshots: dict[str, ProviderUsageSnapshot] = {}
        self.cache_ttl_s: float = cache_ttl_s
        self._last_fetched: dict[str, float] = {}
        self._probes: dict[str, UsageProbe] = dict(probes or {})
        self._probe_factory = probe_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def snapshot(self, pool_id: str) -> ProviderUsageSnapshot | None:
        """Return the current cached snapshot for a pool, or None if unknown."""
        return self.snapshots.get(pool_id)

    def set_snapshot(self, pool_id: str, snapshot: ProviderUsageSnapshot) -> None:
        """Directly store or update a snapshot for a pool (e.g. from notifications or tests)."""
        self.snapshots[pool_id] = snapshot
        self._last_fetched[pool_id] = time.monotonic()

    def get_probe(self, source: str) -> UsageProbe | None:
        """Resolve a probe instance for a given source."""
        if source in self._probes:
            return self._probes[source]
        if self._probe_factory is not None:
            probe = self._probe_factory(source)
            if probe is not None:
                return probe
        probe_cls = get_usage_probe(source)
        if probe_cls is not None:
            try:
                probe = probe_cls()
                self._probes[source] = probe
                return probe
            except Exception as exc:
                log.warning("probe_instantiation_failed", source=source, error=str(exc))
                return None
        return None

    def set_probe(self, source: str, probe: UsageProbe) -> None:
        """Register a probe instance for a given source."""
        self._probes[source] = probe

    async def refresh(
        self, pool_id: str, source: str | None = None
    ) -> ProviderUsageSnapshot | None:
        """Probe the provider for fresh telemetry and update the cache."""
        source_name = source or pool_id
        probe = self.get_probe(source_name)
        now_mono = time.monotonic()

        if probe is None:
            # Missing or unsupported probe -> fail open
            self._last_fetched[pool_id] = now_mono
            return self.snapshots.get(pool_id)

        try:
            result = await probe.fetch_usage()
            self._last_fetched[pool_id] = now_mono
            if result is not None:
                self.snapshots[pool_id] = result
                return result
            else:
                # Probe returned None (no telemetry) -> retain last known, mark stale
                if pool_id in self.snapshots:
                    self.snapshots[pool_id] = replace(
                        self.snapshots[pool_id], stale=True
                    )
                return self.snapshots.get(pool_id)
        except Exception as exc:
            self._last_fetched[pool_id] = now_mono
            log.warning(
                "usage_probe_failed",
                pool_id=pool_id,
                source=source_name,
                error=str(exc),
            )
            if pool_id in self.snapshots:
                self.snapshots[pool_id] = replace(self.snapshots[pool_id], stale=True)
            return self.snapshots.get(pool_id)

    async def refresh_if_needed(
        self, pool_id: str, source: str, *, force: bool = False
    ) -> ProviderUsageSnapshot | None:
        """Refresh usage snapshot if cache has expired or reset_at has passed."""
        now_mono = time.monotonic()
        last = self._last_fetched.get(pool_id)
        expired = last is None or (now_mono - last >= self.cache_ttl_s)

        # Also check if any window has passed reset_at
        snap = self.snapshots.get(pool_id)
        reset_passed = False
        if snap is not None:
            now_dt = self._clock()
            for window in snap.windows.values():
                if window.resets_at is not None and window.resets_at <= now_dt:
                    reset_passed = True
                    break

        if force or expired or reset_passed:
            return await self.refresh(pool_id, source)
        return snap

    def evaluate(
        self,
        pool_id: str,
        pool: UsagePoolConfig,
    ) -> UsageDecision:
        """Evaluate quota eligibility for a pool against its configured caps."""
        snapshot = self.snapshots.get(pool_id)

        if snapshot is None:
            return UsageDecision.READY

        if snapshot.stale:
            return UsageDecision.READY

        if not snapshot.authoritative:
            return UsageDecision.READY

        now = self._clock()

        # Check if reset_at has passed for hard limit or windows
        if snapshot.hard_limit_reached:
            all_resets_passed = False
            if snapshot.windows:
                resets = [
                    w.resets_at
                    for w in snapshot.windows.values()
                    if w.resets_at is not None
                ]
                if resets and all(reset_dt <= now for reset_dt in resets):
                    all_resets_passed = True

            if not all_resets_passed:
                return UsageDecision.WAIT_PROVIDER_USAGE

        for window_name, cap in pool.caps.items():
            actual = snapshot.windows.get(window_name)
            if actual is not None and actual.used_percent is not None:
                if actual.resets_at is not None and actual.resets_at <= now:
                    # Reset time has passed -> window usage is no longer blocking
                    continue
                if actual.used_percent >= cap:
                    return UsageDecision.WAIT_PROVIDER_USAGE

        return UsageDecision.READY


def format_wait_reason(
    pool_id: str,
    pool: UsagePoolConfig,
    snapshot: ProviderUsageSnapshot | None,
) -> str:
    """Format a human-readable reason for waiting on provider usage."""
    if snapshot is not None:
        if snapshot.hard_limit_reached:
            resets_info = ""
            for w in snapshot.windows.values():
                if w.resets_at is not None:
                    resets_info = f"; resets at {w.resets_at.isoformat()}"
                    break
            return f"{pool_id} provider hard usage limit reached{resets_info}"

        for window_name, cap in pool.caps.items():
            actual = snapshot.windows.get(window_name)
            if actual is not None and actual.used_percent is not None:
                if actual.used_percent >= cap:
                    resets_info = (
                        f"; resets at {actual.resets_at.isoformat()}"
                        if actual.resets_at is not None
                        else ""
                    )
                    return f"{pool_id} {window_name} usage cap reached ({actual.used_percent}% >= {cap}%){resets_info}"

    return f"{pool_id} usage cap reached"
