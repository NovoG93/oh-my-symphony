"""Gemini CLI backend.

Drives `gemini -p "" --yolo` once per turn. Current Gemini CLI releases expose
plain stdout rather than Symphony-friendly JSON/session flags, so Symphony
mints and keeps a local session UUID for telemetry while treating each Gemini
CLI invocation as a one-shot turn. If an older/custom command returns JSON, the
backend still parses its response/stats best-effort.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any

from ..workflow import GeminiConfig
from . import (
    EVENT_PROVIDER_USAGE_EXHAUSTED,
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    BackendInit,
    ProviderCapacityError,
    TurnResult,
)
from .per_turn import PerTurnCliBackend, _has_shell_flag
from .usage import (
    ProviderUsageSnapshot,
    UsageProbe,
    UsageWindow,
    USAGE_PROBES,
)


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


def _parse_gemini_exhaustion(text: str) -> tuple[bool, datetime | None]:
    """Check whether text signals genuine Gemini quota exhaustion and extract reset info."""
    lowered = text.lower()
    if (
        "requests per minute" in lowered
        or "tokens per minute" in lowered
        or "rpm" in lowered
        or "tpm" in lowered
    ):
        return False, None

    exhaustion_keywords = (
        "quota exceeded",
        "quota_exceeded",
        "resource has been exhausted",
        "resourceexhausted",
        "insufficient_quota",
        "rate limit exceeded for quota",
        "quota limit reached",
        "reached your quota",
        "hit your quota",
        "provider_usage_exhausted",
        "provider usage exhausted",
    )
    if not any(kw in lowered for kw in exhaustion_keywords):
        return False, None

    resets_at: datetime | None = None
    iso_match = re.search(r"resets?\s+at\s+([\d\-T:+Z]+)", text, re.IGNORECASE)
    if iso_match:
        resets_at = _parse_resets_at(iso_match.group(1))

    if resets_at is None:
        sec_match = re.search(
            r"retry\s+after\s+(\d+)\s*(?:seconds?|s\b)", text, re.IGNORECASE
        )
        if sec_match:
            try:
                secs = int(sec_match.group(1))
                resets_at = datetime.now(timezone.utc) + timedelta(seconds=secs)
            except (ValueError, OverflowError):
                pass
        else:
            min_match = re.search(
                r"resets?\s+in\s+(\d+)\s*(?:minutes?|m\b)", text, re.IGNORECASE
            )
            if min_match:
                try:
                    mins = int(min_match.group(1))
                    resets_at = datetime.now(timezone.utc) + timedelta(minutes=mins)
                except (ValueError, OverflowError):
                    pass

    return True, resets_at


def normalize_gemini_usage(
    raw: dict[str, Any],
    *,
    pool_id: str = "gemini",
) -> ProviderUsageSnapshot:
    """Normalize raw Gemini quota output into ProviderUsageSnapshot."""
    hard_limit_reached = False
    if isinstance(raw, dict):
        if (
            raw.get("hard_limit_reached") is True
            or raw.get("hardLimitReached") is True
            or raw.get("rateLimitReached") is True
        ):
            hard_limit_reached = True

    windows_dict: dict[str, Any] = {}
    if isinstance(raw, dict):
        if "windows" in raw and isinstance(raw["windows"], dict):
            windows_dict = raw["windows"]
        elif "quotas" in raw and isinstance(raw["quotas"], dict):
            windows_dict = raw["quotas"]
        else:
            windows_dict = raw

    non_window_keys = {
        "windows",
        "quotas",
        "hard_limit_reached",
        "hardLimitReached",
        "rateLimitReached",
        "pool_id",
        "source",
    }

    windows: dict[str, UsageWindow] = {}
    for key, val in windows_dict.items():
        if key in non_window_keys or not isinstance(val, dict):
            continue

        used_raw = val.get("used_percent") if "used_percent" in val else val.get("usedPercent")
        used_pct: float | None = None
        if used_raw is not None:
            try:
                used_pct = float(used_raw)
            except (ValueError, TypeError):
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
        elif used_pct is not None:
            rem_pct = max(0.0, 100.0 - used_pct)

        resets_at = _parse_resets_at(
            val.get("resets_at") if "resets_at" in val else val.get("resetsAt")
        )

        windows[str(key)] = UsageWindow(
            key=str(key),
            used_percent=used_pct,
            remaining_percent=rem_pct,
            resets_at=resets_at,
        )

    return ProviderUsageSnapshot(
        pool_id=pool_id,
        source="gemini",
        windows=windows,
        hard_limit_reached=hard_limit_reached,
        authoritative=True,
        observed_at=datetime.now(timezone.utc),
    )


class GeminiUsageProbe(UsageProbe):
    """Usage probe for Gemini CLI (fails open without stable machine-readable endpoint)."""

    def __init__(
        self,
        *,
        pool_id: str = "gemini",
        cached_snapshot: ProviderUsageSnapshot | None = None,
    ) -> None:
        self.pool_id = pool_id
        self.cached_snapshot = cached_snapshot

    async def fetch_usage(self) -> ProviderUsageSnapshot | None:
        """Return None by default (fail open; no pseudo-TTY scraping of /stats)."""
        return self.cached_snapshot


USAGE_PROBES["gemini"] = GeminiUsageProbe


class GeminiBackend(PerTurnCliBackend):
    """One subprocess per turn; parses plain text or best-effort JSON output."""

    def __init__(self, init: BackendInit) -> None:
        cfg = (
            init.resolved_backend_config
            if isinstance(init.resolved_backend_config, GeminiConfig)
            else init.cfg.gemini
        )
        super().__init__(
            init, agent_name="gemini", turn_timeout_ms=cfg.turn_timeout_ms
        )
        self._gemini = cfg

    # ------------------------------------------------------------------
    # per-turn hooks
    # ------------------------------------------------------------------

    def _check_provider_exhaustion(
        self, text: str
    ) -> tuple[bool, datetime | None]:
        return _parse_gemini_exhaustion(text)

    def _command_for_turn(self, *, prompt: str, is_continuation: bool) -> str:
        del prompt, is_continuation  # prompt travels via stdin; no resume flag
        cmd = self._gemini.command
        if _has_shell_flag(cmd, "-y", "--yolo"):
            return cmd
        return f"{cmd} --yolo"

    async def _complete_turn(self, stdout_text: str, rc: int) -> TurnResult:
        is_exhausted, resets_at = _parse_gemini_exhaustion(stdout_text)
        if is_exhausted:
            pool_id = self._usage_pool or "gemini"
            await self._emit(
                EVENT_PROVIDER_USAGE_EXHAUSTED,
                {
                    "pool_id": pool_id,
                    "reason": stdout_text,
                    "resets_at": resets_at.isoformat() if resets_at else None,
                },
            )
            raise ProviderCapacityError(
                pool_id=pool_id, resets_at=resets_at, message=stdout_text
            )

        parsed = self._parse_json_output(stdout_text)
        if parsed is not None:
            sid = parsed.get("session_id")
            if isinstance(sid, str) and sid:
                old_sid = self._session_id
                self._session_id = sid
                if sid != old_sid:
                    await self._emit(
                        EVENT_SESSION_STARTED,
                        {"session_id": sid, "thread_id": sid},
                    )
            response = parsed.get("response")
            last_message = response if isinstance(response, str) else stdout_text
            stats = parsed.get("stats") if isinstance(parsed.get("stats"), dict) else {}
            self._update_usage_from_stats(stats)
        else:
            last_message = stdout_text
            stats = {}
        payload = {
            "message": last_message,
            "result": last_message,
            "response": last_message,
            "session_id": self._session_id,
            "stats": stats,
            "exit_code": rc,
        }
        await self._emit(EVENT_TURN_COMPLETED, payload)
        return TurnResult(
            status=EVENT_TURN_COMPLETED,
            turn_id=self._session_id,
            last_message=last_message[:400],
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _parse_json_output(self, text: str) -> dict[str, Any] | None:
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _update_usage_from_stats(self, stats: Any) -> None:
        if not isinstance(stats, dict):
            return
        models = stats.get("models")
        if not isinstance(models, dict):
            return
        input_tokens = 0
        output_tokens = 0
        for model in models.values():
            if not isinstance(model, dict):
                continue
            tokens = model.get("tokens")
            if not isinstance(tokens, dict):
                continue
            input_tokens += int(tokens.get("input") or 0)
            input_tokens += int(tokens.get("cached") or 0)
            output_tokens += int(tokens.get("candidates") or 0)
            output_tokens += int(tokens.get("thoughts") or 0)
            output_tokens += int(tokens.get("tool") or 0)
        self._latest_usage["input_tokens"] += input_tokens
        self._latest_usage["output_tokens"] += output_tokens
        self._latest_usage["total_tokens"] += input_tokens + output_tokens
