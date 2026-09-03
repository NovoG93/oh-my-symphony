"""Antigravity CLI backend (`agy`)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from ..logging import get_logger
from ..workflow import AgyConfig
from . import BackendInit
from .plain_cli import PlainCliBackend
from .usage import (
    ProviderUsageSnapshot,
    UsageProbe,
    UsageWindow,
    USAGE_PROBES,
)

log = get_logger()


def _parse_resets_at(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        try:
            ts = float(val)
            if ts > 1e11:  # milliseconds
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def normalize_agy_usage(
    raw: dict[str, Any],
    *,
    pool_id: str = "agy",
) -> ProviderUsageSnapshot:
    """Normalize raw AGY quota output preserving model/provider-specific buckets."""
    hard_limit_reached = False
    if isinstance(raw, dict):
        if (
            raw.get("hard_limit_reached") is True
            or raw.get("hardLimitReached") is True
            or raw.get("rateLimitReached") is True
        ):
            hard_limit_reached = True
        rl_type = raw.get("rateLimitReachedType") or raw.get("rate_limit_reached_type")
        if rl_type is not None and str(rl_type).lower() not in ("none", "false", "soft", ""):
            hard_limit_reached = True

    envelope_groups = None
    if isinstance(raw, dict):
        command = raw.get("command")
        if isinstance(command, dict) and isinstance(command.get("data"), dict):
            groups = command["data"].get("groups")
            if isinstance(groups, list):
                envelope_groups = groups
    buckets_dict: dict[str, Any] = {}
    if isinstance(raw, dict):
        if "buckets" in raw and isinstance(raw["buckets"], dict):
            buckets_dict = raw["buckets"]
        elif "quotas" in raw and isinstance(raw["quotas"], dict):
            buckets_dict = raw["quotas"]
        elif "models" in raw and isinstance(raw["models"], dict):
            buckets_dict = raw["models"]
        else:
            buckets_dict = raw

    non_window_keys = {
        "buckets",
        "quotas",
        "models",
        "hard_limit_reached",
        "hardLimitReached",
        "rateLimitReached",
        "rateLimitReachedType",
        "rate_limit_reached_type",
        "status",
        "source",
        "pool_id",
        "command",
    }

    windows: dict[str, UsageWindow] = {}
    if envelope_groups is not None:
        for group in envelope_groups:
            if not isinstance(group, dict) or not isinstance(group.get("buckets"), list):
                continue
            identity = " ".join(
                str(group.get(k, ""))
                for k in ("key", "id", "name", "label", "title", "type")
            ).lower()
            if "gemini" in identity:
                group_key = "gemini"
            elif "claude" in identity or "gpt" in identity or "third" in identity:
                group_key = "third_party"
            else:
                continue
            for bucket in group["buckets"]:
                if not isinstance(bucket, dict):
                    continue
                window_raw = bucket.get("window") or bucket.get("period")
                if not isinstance(window_raw, str):
                    continue
                period = window_raw.strip().lower().replace("-", "").replace("_", "")
                if period in {"5h", "5hour", "fivehour"}:
                    period_key = "five_hour"
                elif period in {"weekly", "week", "7d"}:
                    period_key = "weekly"
                else:
                    continue
                remaining_raw = bucket.get("remaining_fraction")
                if isinstance(remaining_raw, bool) or not isinstance(
                    remaining_raw, (int, float)
                ):
                    continue
                remaining = float(remaining_raw) * 100.0
                if not 0 <= remaining <= 100:
                    continue
                key = f"{group_key}_{period_key}"
                windows[key] = UsageWindow(
                    key=key, used_percent=100.0 - remaining,
                    remaining_percent=remaining,
                    resets_at=_parse_resets_at(
                        bucket.get("reset_time") or bucket.get("resets_at")
                    ),
                    group_key=group_key, period_key=period_key,
                )
        if windows:
            return ProviderUsageSnapshot(
                pool_id=pool_id, source="agy", windows=windows,
                hard_limit_reached=hard_limit_reached, authoritative=True,
                observed_at=datetime.now(timezone.utc),
            )
    for key, val in buckets_dict.items():
        if key in non_window_keys or not isinstance(val, dict):
            continue

        used_raw = val.get("used_percent") if "used_percent" in val else val.get("usedPercent")
        used_pct: float | None = None
        if used_raw is not None:
            try:
                used_pct = float(used_raw)
            except (ValueError, TypeError):
                used_pct = None
            if used_pct is not None and not math.isfinite(used_pct):
                used_pct = None
        elif "used" in val and "limit" in val:
            try:
                limit_val = float(val["limit"])
                used_val = float(val["used"])
                if math.isfinite(limit_val) and math.isfinite(used_val) and limit_val > 0:
                    derived_used = (used_val / limit_val) * 100.0
                    if math.isfinite(derived_used):
                        used_pct = derived_used
            except (ValueError, TypeError, ZeroDivisionError):
                used_pct = None

        rem_raw = (
            val.get("remaining_percent")
            if "remaining_percent" in val
            else val.get("remainingPercent")
        )
        rem_pct: float | None = None
        if rem_raw is not None:
            try:
                rem_pct = float(rem_raw)
            except (ValueError, TypeError):
                rem_pct = None
            if rem_pct is not None and not math.isfinite(rem_pct):
                rem_pct = None
        elif used_pct is not None:
            rem_pct = max(0.0, 100.0 - used_pct)

        resets_at = _parse_resets_at(
            val.get("resets_at") if "resets_at" in val else val.get("resetsAt")
        )

        window_key = str(key)
        if used_pct is None and rem_pct is None:
            continue
        windows[window_key] = UsageWindow(
            key=window_key,
            used_percent=used_pct,
            remaining_percent=rem_pct,
            resets_at=resets_at,
        )

    return ProviderUsageSnapshot(
        pool_id=pool_id,
        source="agy",
        windows=windows,
        hard_limit_reached=hard_limit_reached,
        authoritative=True,
        observed_at=datetime.now(timezone.utc),
    )


class AgyUsageProbe(UsageProbe):
    """Authoritative read-only usage probe for Antigravity (AGY)."""

    def __init__(
        self,
        *,
        command: str = "agy -p /quota --output-format json",
        cwd: Path | None = None,
        pool_id: str = "agy",
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.pool_id = pool_id

    async def fetch_usage(self) -> ProviderUsageSnapshot | None:
        """Execute read-only quota command and return normalized snapshot (fail open)."""
        try:
            proc = await asyncio.create_subprocess_shell(
                self.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
            )
            stdout_bytes, _ = await proc.communicate()
            if proc.returncode != 0:
                log.warning(
                    "agy_usage_probe_failed_exit_code",
                    pool_id=self.pool_id,
                    rc=proc.returncode,
                )
                return None

            raw_str = stdout_bytes.decode("utf-8", errors="replace").strip()
            if not raw_str:
                return None
            parsed = json.loads(raw_str)
            if not isinstance(parsed, dict):
                return None
            status = parsed.get("status")
            if status is not None and str(status).upper() not in {"SUCCESS", "OK"}:
                return None
            snapshot = normalize_agy_usage(parsed, pool_id=self.pool_id)
            # A structurally recognized but unusable response is a parse
            # failure.  Returning None lets the manager retain its stale
            # snapshot and keeps scheduling fail-open.
            # Empty or malformed telemetry is a parse failure. Explicit hard
            # limits remain meaningful even when no provider windows exist.
            if not snapshot.windows and not snapshot.hard_limit_reached:
                return None
            return snapshot
        except Exception as exc:
            log.warning("agy_usage_probe_failed", pool_id=self.pool_id, error=str(exc))
            return None


USAGE_PROBES["agy"] = AgyUsageProbe


class AgyBackend(PlainCliBackend):
    """Drive `agy --print "$(cat)"` once per Symphony worker turn."""

    def __init__(self, init: BackendInit) -> None:
        cfg = (
            init.resolved_backend_config
            if isinstance(init.resolved_backend_config, AgyConfig)
            else init.cfg.agy
        )
        super().__init__(
            init,
            agent_name="agy",
            command=cfg.command,
            turn_timeout_ms=cfg.turn_timeout_ms,
            resume_across_turns=cfg.resume_across_turns,
            unattended_flags=("--dangerously-skip-permissions",),
            continuation_flag="--continue",
        )
