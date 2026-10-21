"""Claude Code CLI backend.

Drives `claude -p --output-format stream-json --verbose` once per turn. The
underlying CLI does not have a persistent app-server; sessions are tracked by
ID, so this backend spawns a fresh subprocess each turn and uses
`--resume <session-id>` for continuity.

Stream-JSON event shape (one line of JSON per event):

  {"type":"system","subtype":"init","session_id":"...","model":"...",...}
  {"type":"assistant","message":{...,"usage":{...}},"session_id":"..."}
  {"type":"user","message":{"content":[{"type":"tool_result",...}]},...}
  {"type":"result","subtype":"success","is_error":false,
   "usage":{"input_tokens":N,"output_tokens":N,...},"result":"...",
   "total_cost_usd":N,"session_id":"...","duration_ms":N,...}

Errors surface as `result` events with `is_error:true` and a `subtype`
indicating the failure mode (e.g. `error_max_turns`).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
import shlex
from typing import Any, Sequence

from ..errors import TurnFailed
from ..logging import get_logger
from ..utils.git_sandbox import GIT_ROOTS_ENV_VAR, git_roots_outside
from ..workflow import ClaudeConfig
from . import (
    EVENT_OTHER_MESSAGE,
    EVENT_PROVIDER_USAGE_EXHAUSTED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    BackendInit,
    ProviderCapacityError,
    TurnResult,
)
from .usage import (
    ProviderUsageSnapshot,
    UsageProbe,
    UsageWindow,
    USAGE_PROBES,
)
from .per_turn import JsonlStreamBackend



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


def _is_genuine_claude_exhaustion(text: str) -> bool:
    """Return True if an error signals subscription/plan quota exhaustion rather than RPM/429."""
    lowered = text.lower()
    if (
        "requests per minute" in lowered
        or "tokens per minute" in lowered
        or "rpm" in lowered
        or "tpm" in lowered
    ):
        return False

    exhaustion_keywords = (
        "usage limit",
        "usage_limit",
        "quota exceeded",
        "quota_exceeded",
        "credit balance is too low",
        "insufficient_quota",
        "rate_limit_reached",
        "rate limit reached",
        "reached your usage limit",
        "plan limit",
        "plan_limit",
        "capacity exceeded",
        "hit your limit",
        "provider_usage_exhausted",
        "provider usage exhausted",
    )
    return any(kw in lowered for kw in exhaustion_keywords)


def normalize_claude_usage(
    raw: dict[str, Any],
    *,
    pool_id: str = "claude",
) -> ProviderUsageSnapshot:
    """Normalize raw Claude rate limits dictionary into ProviderUsageSnapshot."""
    hard_limit_reached = False
    if isinstance(raw, dict):
        if (
            raw.get("hard_limit_reached") is True
            or raw.get("hardLimitReached") is True
            or raw.get("rateLimitReached") is True
        ):
            hard_limit_reached = True
        err = str(raw.get("error") or raw.get("message") or "")
        if err and _is_genuine_claude_exhaustion(err):
            hard_limit_reached = True

    limits_dict: dict[str, Any] = {}
    if isinstance(raw, dict):
        if "rate_limits" in raw and isinstance(raw["rate_limits"], dict):
            limits_dict = raw["rate_limits"]
        elif "rateLimits" in raw and isinstance(raw["rateLimits"], dict):
            limits_dict = raw["rateLimits"]
        else:
            limits_dict = raw

    non_window_keys = {
        "rate_limits",
        "rateLimits",
        "hard_limit_reached",
        "hardLimitReached",
        "rateLimitReached",
        "error",
        "message",
        "pool_id",
        "source",
    }

    windows: dict[str, UsageWindow] = {}
    for raw_key, val in limits_dict.items():
        if raw_key in non_window_keys or not isinstance(val, dict):
            continue

        k_low = str(raw_key).lower()
        if k_low in ("five_hour", "fivehour", "5h", "5_hour"):
            window_key = "five_hour"
        elif k_low in ("seven_day", "sevenday", "7d", "7_day", "weekly"):
            window_key = "weekly"
        else:
            window_key = str(raw_key)

        used_raw = (
            val.get("used_percentage")
            if "used_percentage" in val
            else val.get("used_percent")
            if "used_percent" in val
            else val.get("usedPercent")
        )
        used_pct: float | None = None
        if used_raw is not None:
            try:
                used_pct = float(used_raw)
            except (ValueError, TypeError):
                used_pct = None

        rem_raw = (
            val.get("remaining_percentage")
            if "remaining_percentage" in val
            else val.get("remaining_percent")
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

        windows[window_key] = UsageWindow(
            key=window_key,
            used_percent=used_pct,
            remaining_percent=rem_pct,
            resets_at=resets_at,
        )

    return ProviderUsageSnapshot(
        pool_id=pool_id,
        source="claude",
        windows=windows,
        hard_limit_reached=hard_limit_reached,
        authoritative=True,
        observed_at=datetime.now(timezone.utc),
    )


normalize_claude_rate_limits = normalize_claude_usage


class ClaudeUsageProbe(UsageProbe):
    """Passive/cached usage probe for Claude Code (fails open on cold start)."""

    def __init__(
        self,
        *,
        pool_id: str = "claude",
        cached_snapshot: ProviderUsageSnapshot | None = None,
    ) -> None:
        self.pool_id = pool_id
        self.cached_snapshot = cached_snapshot

    async def fetch_usage(self) -> ProviderUsageSnapshot | None:
        """Return cached subscription telemetry snapshot or None (fail open)."""
        return self.cached_snapshot


USAGE_PROBES["claude"] = ClaudeUsageProbe



def _inject_model(command: str, model: str) -> str:
    """Inject ``--model <model>`` right after a literal ``claude`` token.

    Injecting after the token rather than appending keeps pipelines and
    redirections in operator-authored commands intact. Non-``claude``
    commands (wrapper scripts) are returned unchanged.
    """
    if not model:
        return command
    stripped = command.lstrip()
    if not (stripped == "claude" or stripped.startswith(("claude ", "claude\t"))):
        return command
    leading_ws = command[: len(command) - len(stripped)]
    rest = stripped[len("claude") :]
    flag = f"--model {shlex.quote(model)}"
    return f"{leading_ws}claude {flag}{rest}"


def _inject_add_dirs(command: str, dirs: Sequence[str]) -> str:
    """Inject ``--add-dir <path>`` right after a literal ``claude`` token.

    Injecting after the token rather than appending keeps pipelines and
    redirections in operator-authored commands intact. Non-``claude``
    commands (wrapper scripts) are returned unchanged; they get the same
    list through :data:`GIT_ROOTS_ENV_VAR` instead.
    """
    if not dirs:
        return command
    stripped = command.lstrip()
    if not (stripped == "claude" or stripped.startswith(("claude ", "claude\t"))):
        return command
    leading_ws = command[: len(command) - len(stripped)]
    rest = stripped[len("claude") :]
    flags = " ".join(f"--add-dir {shlex.quote(d)}" for d in dirs)
    return f"{leading_ws}claude {flags}{rest}"


class ClaudeCodeBackend(JsonlStreamBackend):
    """One subprocess per turn; speaks Claude Code stream-json."""

    _resume_flag = "--resume"

    def __init__(self, init: BackendInit) -> None:
        cfg = (
            init.resolved_backend_config
            if isinstance(init.resolved_backend_config, ClaudeConfig)
            else init.cfg.claude
        )
        super().__init__(init, agent_name="claude", turn_timeout_ms=cfg.turn_timeout_ms)
        self._claude = cfg
        # Resolved once: the worktree layout cannot change mid-run, and
        # run_turn spawns a fresh subprocess every turn.
        self._git_roots = git_roots_outside(init.cwd, init.workspace_root)
        if self._git_roots:
            log.info("claude_git_roots_granted", roots=self._git_roots)
        self._latest_usage = {
            "input_tokens": 0,
            "cache_input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        }
        self._latest_rate_limits: dict[str, Any] | None = None
        self._usage_manager = getattr(init, "usage_manager", None)
        self._usage_pool = getattr(init, "usage_pool", None) or (
            init.selection.kind if init.selection is not None else "claude"
        )

    # ------------------------------------------------------------------
    # AgentBackend lifecycle
    # ------------------------------------------------------------------

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None:
        return dict(self._latest_rate_limits) if self._latest_rate_limits is not None else None

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        """Only count claude `assistant` stream-json frames as progress.

        The catch-all `EVENT_OTHER_MESSAGE` path also carries `user` frames
        whose payload is a `tool_result` echo. Resetting the stall timer
        on every echo masked a real 18-minute model stall in OLV-002
        (commit 499e787). Restrict the predicate to real assistant output;
        the orchestrator additionally treats lifecycle events and OUTPUT
        token deltas as progress so this filter only narrows the catch-all
        OTHER_MESSAGE bucket.
        """
        return event.get("type") == "assistant"

    async def initialize(self) -> dict[str, Any]:
        return {"agent": "claude_code"}

    # ------------------------------------------------------------------
    # per-turn hooks
    # ------------------------------------------------------------------

    def _git_roots_env(self) -> dict[str, str]:
        if not self._git_roots:
            return {}
        return {GIT_ROOTS_ENV_VAR: os.pathsep.join(self._git_roots)}

    def _command_for_turn(self, *, prompt: str, is_continuation: bool) -> str:
        del prompt  # travels via stdin
        cmd = _inject_model(self._claude.command, self._claude.model)
        cmd = _inject_add_dirs(cmd, self._git_roots)
        return cmd + self._resume_args(
            is_continuation=is_continuation,
            resume_across_turns=self._claude.resume_across_turns,
        )

    async def _handle_stream_event(
        self, msg: dict[str, Any]
    ) -> dict[str, Any] | None:
        kind = msg.get("type")
        if kind == "system" and msg.get("subtype") == "init":
            sid = msg.get("session_id")
            if isinstance(sid, str) and sid:
                await self._observe_session_id(sid)
            if "rate_limits" in msg and isinstance(msg["rate_limits"], dict):
                self._latest_rate_limits = msg["rate_limits"]
                if self._usage_manager is not None:
                    snap = normalize_claude_usage(
                        msg, pool_id=self._usage_pool or "claude"
                    )
                    self._usage_manager.set_snapshot(
                        self._usage_pool or "claude", snap
                    )
        elif kind == "assistant":
            # Mid-stream `usage` deltas are ignored; the terminal
            # `result` event is the source of truth for accumulation.
            last_text = _extract_text(msg.get("message") or {})
            if last_text:
                self._last_message = last_text[:400]
            await self._emit(EVENT_OTHER_MESSAGE, msg)
        elif kind == "user":
            await self._emit(EVENT_OTHER_MESSAGE, msg)
        elif kind == "result":
            self._update_usage_absolute(msg.get("usage") or {})
            sid = msg.get("session_id")
            if isinstance(sid, str) and sid:
                await self._observe_session_id(sid)
            if "rate_limits" in msg and isinstance(msg["rate_limits"], dict):
                self._latest_rate_limits = msg["rate_limits"]
                if self._usage_manager is not None:
                    snap = normalize_claude_usage(
                        msg, pool_id=self._usage_pool or "claude"
                    )
                    self._usage_manager.set_snapshot(
                        self._usage_pool or "claude", snap
                    )
            return msg
        else:
            await self._emit(EVENT_OTHER_MESSAGE, msg)
        return None

    async def _complete_stream_turn(
        self,
        proc: asyncio.subprocess.Process,
        terminal: dict[str, Any] | None,
        rc: int | None,
    ) -> TurnResult:
        # Claude reports failure through the `result` event, not the exit
        # status; the status only decorates the no-result diagnostic.
        del rc
        if terminal is None:
            # Stream ended without a `result` event — treat as failure.
            stderr_blob = self._stderr_blob()
            err_msg = (
                f"claude exited with no result event (rc={proc.returncode})"
                + (f"; stderr: {stderr_blob}" if stderr_blob else "")
            )
            if _is_genuine_claude_exhaustion(err_msg):
                pool_id = self._usage_pool or "claude"
                await self._emit(
                    EVENT_PROVIDER_USAGE_EXHAUSTED,
                    {"pool_id": pool_id, "reason": err_msg},
                )
                raise ProviderCapacityError(pool_id=pool_id, message=err_msg)
            await self._emit(
                EVENT_TURN_FAILED,
                {"reason": err_msg, "stderr_tail": list(self._stderr_tail)},
            )
            raise TurnFailed(err_msg)

        if _is_error_result(terminal):
            reason = _error_result_message(terminal)
            if _is_genuine_claude_exhaustion(reason):
                pool_id = self._usage_pool or "claude"
                await self._emit(
                    EVENT_PROVIDER_USAGE_EXHAUSTED,
                    {"pool_id": pool_id, "reason": reason},
                )
                raise ProviderCapacityError(pool_id=pool_id, message=reason)
            payload = {
                **terminal,
                "reason": reason,
                "stderr_tail": list(self._stderr_tail),
            }
            await self._emit(EVENT_TURN_FAILED, payload)
            raise TurnFailed(reason)

        await self._require_resume_confirmation()

        if "rate_limits" in terminal and isinstance(terminal["rate_limits"], dict):
            self._latest_rate_limits = terminal["rate_limits"]
            if self._usage_manager is not None:
                snap = normalize_claude_usage(
                    terminal, pool_id=self._usage_pool or "claude"
                )
                self._usage_manager.set_snapshot(self._usage_pool or "claude", snap)

        message = str(terminal.get("result") or "").strip() or self._last_message
        self._last_message = message[:400]
        await self._emit(
            EVENT_TURN_COMPLETED,
            {**terminal, "message": message},
        )
        return TurnResult(
            status=EVENT_TURN_COMPLETED,
            turn_id=str(terminal.get("session_id") or self._session_id or ""),
            last_message=self._last_message,
        )

    def _update_usage_absolute(self, usage: dict[str, Any]) -> None:
        # Each `result` event reports usage for that one turn — accumulate.
        if not isinstance(usage, dict):
            return
        in_t = int(usage.get("input_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_create = int(usage.get("cache_creation_input_tokens") or 0)
        out_t = int(usage.get("output_tokens") or 0)
        cache_t = cache_read + cache_create
        self._latest_usage["input_tokens"] += in_t
        self._latest_usage["cache_input_tokens"] += cache_t
        self._latest_usage["output_tokens"] += out_t
        self._latest_usage["total_tokens"] += in_t + cache_t + out_t


def _extract_text(message: dict[str, Any]) -> str:
    """Pull the last text block out of a Claude assistant message."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    for block in reversed(content):
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                return text
    return ""


def _is_error_result(event: dict[str, Any]) -> bool:
    subtype = str(event.get("subtype") or "").lower()
    value = event.get("is_error")
    if isinstance(value, bool):
        if value:
            return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes"}:
            return True
    elif value:
        return True

    if subtype.startswith("error"):
        return True
    if subtype == "success":
        return False
    return False


def _error_result_message(event: dict[str, Any]) -> str:
    """Return a human-actionable reason for Claude result failures."""
    for key in ("error", "message", "result", "api_error_message", "api_error_status"):
        value = event.get(key)
        if isinstance(value, dict):
            text = _extract_text(value)
        else:
            text = str(value or "").strip()
        if text and text.lower() != "success":
            return text

    subtype = str(event.get("subtype") or "").strip()
    if subtype.lower().startswith("error"):
        return subtype
    return "claude turn failed"
