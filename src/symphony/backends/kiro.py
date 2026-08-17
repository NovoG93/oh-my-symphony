"""Kiro CLI backend."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..workflow import KiroConfig
from . import BackendInit
from .per_turn import _has_shell_flag
from .plain_cli import PlainCliBackend
from .usage import (
    ProviderUsageSnapshot,
    UsageProbe,
    UsageWindow,
    USAGE_PROBES,
)


def _is_genuine_kiro_exhaustion(text: str) -> bool:
    """Return True if an error signals Kiro credit or monthly quota exhaustion."""
    lowered = text.lower()
    if (
        "requests per minute" in lowered
        or "tokens per minute" in lowered
        or "rpm" in lowered
        or "tpm" in lowered
    ):
        return False

    exhaustion_keywords = (
        "credits exhausted",
        "insufficient credits",
        "monthly limit reached",
        "out of credits",
        "no remaining credits",
        "credit balance is too low",
        "quota exceeded",
        "provider_usage_exhausted",
        "provider usage exhausted",
    )
    return any(kw in lowered for kw in exhaustion_keywords)


def normalize_kiro_usage(
    raw: dict[str, Any],
    *,
    pool_id: str = "kiro",
) -> ProviderUsageSnapshot:
    """Normalize credit-based Kiro usage into ProviderUsageSnapshot with a monthly window."""
    hard_limit_reached = False
    if isinstance(raw, dict):
        if (
            raw.get("hard_limit_reached") is True
            or raw.get("hardLimitReached") is True
            or raw.get("rateLimitReached") is True
        ):
            hard_limit_reached = True

    used_pct: float | None = None
    if isinstance(raw, dict):
        if "used_percent" in raw:
            try:
                used_pct = float(raw["used_percent"])
            except (ValueError, TypeError):
                pass
        elif "usedPercent" in raw:
            try:
                used_pct = float(raw["usedPercent"])
            except (ValueError, TypeError):
                pass
        elif "used_credits" in raw and "total_credits" in raw:
            try:
                total = float(raw["total_credits"])
                if total > 0:
                    used_pct = (float(raw["used_credits"]) / total) * 100.0
            except (ValueError, TypeError, ZeroDivisionError):
                pass
        elif "used" in raw and "total" in raw:
            try:
                total = float(raw["total"])
                if total > 0:
                    used_pct = (float(raw["used"]) / total) * 100.0
            except (ValueError, TypeError, ZeroDivisionError):
                pass

    windows: dict[str, UsageWindow] = {}
    if used_pct is not None:
        rem_pct = max(0.0, 100.0 - used_pct)
        windows["monthly"] = UsageWindow(
            key="monthly",
            used_percent=used_pct,
            remaining_percent=rem_pct,
        )

    return ProviderUsageSnapshot(
        pool_id=pool_id,
        source="kiro",
        windows=windows,
        hard_limit_reached=hard_limit_reached,
        authoritative=True,
        observed_at=datetime.now(timezone.utc),
    )


class KiroUsageProbe(UsageProbe):
    """Usage probe for Kiro CLI (fails open without programmatic endpoint)."""

    def __init__(
        self,
        *,
        pool_id: str = "kiro",
        cached_snapshot: ProviderUsageSnapshot | None = None,
    ) -> None:
        self.pool_id = pool_id
        self.cached_snapshot = cached_snapshot

    async def fetch_usage(self) -> ProviderUsageSnapshot | None:
        """Return None by default (fail open; no interactive scraping)."""
        return self.cached_snapshot


USAGE_PROBES["kiro"] = KiroUsageProbe


class KiroBackend(PlainCliBackend):
    """Drive `kiro-cli chat --no-interactive` once per Symphony worker turn."""

    def __init__(self, init: BackendInit) -> None:
        cfg = (
            init.resolved_backend_config
            if isinstance(init.resolved_backend_config, KiroConfig)
            else init.cfg.kiro
        )
        super().__init__(
            init,
            agent_name="kiro",
            command=cfg.command,
            turn_timeout_ms=cfg.turn_timeout_ms,
            resume_across_turns=cfg.resume_across_turns,
            continuation_flag="--resume",
        )

    def _check_provider_exhaustion(
        self, text: str
    ) -> tuple[bool, datetime | None]:
        return _is_genuine_kiro_exhaustion(text), None

    def _command_for_turn(self, *, prompt: str, is_continuation: bool) -> str:
        """Keep Kiro options before the positional chat input.

        `kiro-cli chat` accepts the rendered prompt as `[INPUT]`. The default
        command uses `"$(cat)"` as that positional argument, so continuation
        flags must be inserted before it instead of appended after it.
        """
        del prompt  # travels via stdin ($(cat) in the command)
        command = self._command
        if not (
            is_continuation
            and self._resume_across_turns
            and not _has_shell_flag(command, "--resume", "-r")
        ):
            return command
        return _insert_before_prompt_arg(command, "--resume")


def _insert_before_prompt_arg(command: str, flag: str) -> str:
    stripped = command.rstrip()
    for marker in ('"$(cat)"', "$(cat)"):
        if stripped.endswith(marker):
            prefix = stripped[: -len(marker)].rstrip()
            return f"{prefix} {flag} {marker}"
    return f"{stripped} {flag}"
