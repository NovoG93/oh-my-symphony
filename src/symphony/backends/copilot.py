"""GitHub Copilot CLI backend.

Drives `copilot --output-format=json --no-ask-user --allow-all-tools -p ""` once per turn.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import uuid
from typing import Any

from .._shell import resolve_bash, terminate_process_tree
from ..errors import TurnFailed
from ..logging import get_logger
from ..utils.git_sandbox import git_roots_outside
from ..workflow import CopilotConfig
from ..workflow.config import _default_copilot_config
from . import (
    EVENT_PROVIDER_USAGE_EXHAUSTED,
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    BackendInit,
    ProviderCapacityError,
    TurnResult,
    _is_valid_session_id,
)
from .per_turn import MAX_LINE_BYTES, PerTurnCliBackend
from .usage import (
    ProviderUsageSnapshot,
    UsageProbe,
    UsageWindow,
    USAGE_PROBES,
)

log = get_logger()


class CopilotBackend(PerTurnCliBackend):
    """One subprocess per turn; speaks GitHub Copilot CLI JSONL."""

    def __init__(self, init: BackendInit) -> None:
        cfg = (
            init.resolved_backend_config
            if isinstance(init.resolved_backend_config, CopilotConfig)
            else (init.cfg.copilot or _default_copilot_config())
        )
        super().__init__(
            init, agent_name="copilot", turn_timeout_ms=cfg.turn_timeout_ms
        )
        self._copilot = cfg
        self._git_roots = git_roots_outside(init.cwd, init.workspace_root)
        self._copilot_session_id: str | None = None
        self._resume_on_next_turn = False
        self._expected_resume_session_id: str | None = None
        self._resume_session_confirmed = False

    # ------------------------------------------------------------------
    # per-turn hooks
    # ------------------------------------------------------------------

    def _check_provider_exhaustion(
        self, text: str
    ) -> tuple[bool, datetime | None]:
        return _is_genuine_copilot_exhaustion(text), None

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        event_type = event.get("type")
        if not isinstance(event_type, str):
            return False
        return (
            event_type
            in {
                "assistant.message",
                "assistant.message_delta",
                "assistant.message_start",
                "assistant.turn_start",
                "assistant.turn_end",
                "model.call_start",
            }
            or event_type.startswith("tool.")
        )

    @property
    def session_id(self) -> str | None:
        return self._copilot_session_id or self._session_id

    async def resume_session(self, session_id: str) -> bool:
        """Select an exact Copilot session for the next CLI process."""
        if self._closed or not _is_valid_session_id(session_id):
            return False
        self._copilot_session_id = session_id
        self._session_id = session_id
        self._expected_resume_session_id = session_id
        self._resume_session_confirmed = False
        self._resume_on_next_turn = True
        return True

    def _stdin_payload(self, prompt: str) -> str | None:
        del prompt  # travels via -p flag
        return None

    def _command_for_turn(self, *, prompt: str, is_continuation: bool) -> str:
        parts = [
            self._copilot.command,
            "--output-format=json",
            "--no-ask-user",
            "--allow-all-tools",
        ]
        if self._copilot.model:
            parts += ["--model", self._copilot.model]
        if self._copilot.reasoning_effort:
            parts += ["--reasoning-effort", self._copilot.reasoning_effort]

        if not self._copilot.resume_across_turns:
            self._session_id = str(uuid.uuid4())
            self._copilot_session_id = self._session_id
            parts += ["--session-id", self._session_id]
        else:
            sid = self._copilot_session_id or self._session_id
            if not sid:
                sid = str(uuid.uuid4())
                self._session_id = sid
                self._copilot_session_id = sid
            parts += ["--session-id", sid]

        self._resume_on_next_turn = False

        for root in self._git_roots:
            parts += ["--add-dir", root]

        parts += ["-p", prompt]
        return shlex.join(parts)

    async def _complete_turn(self, stdout_text: str, rc: int) -> TurnResult:
        events = self._decode_events(stdout_text)
        final_message = ""
        observed_session_id = None
        turn_error = None

        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type == "assistant.message":
                data = event.get("data")
                if isinstance(data, dict):
                    content = data.get("content")
                    if isinstance(content, str) and content:
                        final_message = content
                    out_tokens = data.get("outputTokens")
                    if isinstance(out_tokens, (int, float)):
                        self._latest_usage["output_tokens"] += int(out_tokens)
                        self._latest_usage["total_tokens"] += int(out_tokens)
            elif event_type == "result":
                sid = event.get("sessionId")
                if isinstance(sid, str) and sid:
                    observed_session_id = sid
                exit_code = event.get("exitCode")
                if isinstance(exit_code, int) and exit_code != 0:
                    turn_error = f"copilot result exitCode {exit_code}"
            elif event_type == "session.error":
                data = event.get("data")
                err_str = str(
                    data.get("message") if isinstance(data, dict) else data or "session error"
                )
                turn_error = f"copilot error: {err_str}"

        if observed_session_id:
            expected = self._expected_resume_session_id
            if expected is not None:
                if observed_session_id != expected:
                    reason = "copilot returned a different recovered session"
                    await self._emit(EVENT_TURN_FAILED, {"reason": reason})
                    raise TurnFailed(reason)
                self._resume_session_confirmed = True
            if observed_session_id != self._copilot_session_id:
                self._copilot_session_id = observed_session_id
                self._session_id = observed_session_id
                await self._emit(
                    EVENT_SESSION_STARTED,
                    {"session_id": observed_session_id, "thread_id": observed_session_id},
                )

        if self._expected_resume_session_id is not None:
            if not self._resume_session_confirmed:
                reason = "copilot did not confirm the requested recovered session"
                await self._emit(EVENT_TURN_FAILED, {"reason": reason})
                raise TurnFailed(reason)
            self._expected_resume_session_id = None
            self._resume_session_confirmed = False

        if turn_error is not None:
            if _is_genuine_copilot_exhaustion(turn_error):
                pool_id = self._usage_pool or self._agent_name
                await self._emit(
                    EVENT_PROVIDER_USAGE_EXHAUSTED,
                    {"pool_id": pool_id, "reason": turn_error},
                )
                raise ProviderCapacityError(pool_id=pool_id, message=turn_error)
            await self._emit(
                EVENT_TURN_FAILED,
                {
                    "reason": turn_error,
                    "exit_code": rc,
                    "stderr_tail": list(self._stderr_tail),
                },
            )
            raise TurnFailed(turn_error)

        response = final_message or stdout_text
        payload = {
            "message": response,
            "result": response,
            "response": response,
            "session_id": self.session_id,
            "events": events,
            "exit_code": rc,
        }
        await self._emit(EVENT_TURN_COMPLETED, payload)
        return TurnResult(
            status=EVENT_TURN_COMPLETED,
            turn_id=self.session_id,
            last_message=response[:400],
        )

    def _decode_events(self, text: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
        return events


def _is_genuine_copilot_exhaustion(text: str) -> bool:
    """Return True if error text signals provider quota/plan exhaustion rather than generic RPM/429."""
    lowered = text.lower()
    if (
        "requests per minute" in lowered
        or "tokens per minute" in lowered
        or "rpm" in lowered
        or "tpm" in lowered
    ):
        return False
    exhaustion_keywords = (
        "quota exceeded",
        "quota_exceeded",
        "usage limit reached",
        "usage limit exceeded",
        "usage_limit",
        "insufficient credits",
        "out of credits",
        "ai credits exhausted",
        "credits exhausted",
        "premium requests exhausted",
        "provider_usage_exhausted",
        "provider usage exhausted",
        "plan limit reached",
        "plan limit exceeded",
    )
    return any(kw in lowered for kw in exhaustion_keywords)


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


def next_month_first_day_utc(now: datetime | None = None) -> datetime:
    """Return the first day of the next month at 00:00:00 UTC."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if now.month == 12:
        return datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)


def normalize_copilot_quota(
    raw: dict[str, Any],
    *,
    pool_id: str = "copilot",
) -> ProviderUsageSnapshot:
    """Normalize Copilot JSON-RPC account.getQuota response into a ProviderUsageSnapshot."""
    res = raw.get("result") if isinstance(raw, dict) and "result" in raw else raw
    if not isinstance(res, dict):
        res = {}

    snapshots = (
        res.get("quotaSnapshots")
        or res.get("quota_snapshots")
        or res.get("quotas")
        or res
    )
    if not isinstance(snapshots, dict):
        snapshots = {}

    premium = (
        snapshots.get("premium_interactions")
        or snapshots.get("premiumInteractions")
        or snapshots.get("premium_requests")
        or snapshots.get("premiumRequests")
        or snapshots.get("monthly")
        or snapshots
    )

    hard_limit_reached = False
    if isinstance(raw, dict):
        if (
            raw.get("hard_limit_reached") is True
            or raw.get("hardLimitReached") is True
            or raw.get("rateLimitReached") is True
        ):
            hard_limit_reached = True

    used_pct: float | None = None
    rem_pct: float | None = None
    resets_at: datetime | None = None

    if isinstance(premium, dict):
        if premium.get("hasQuota") is False or premium.get("has_quota") is False:
            hard_limit_reached = True

        rem_raw = (
            premium.get("remainingPercentage")
            if "remainingPercentage" in premium
            else premium.get("remaining_percentage")
            if "remaining_percentage" in premium
            else premium.get("remainingPercent")
            if "remainingPercent" in premium
            else premium.get("remaining_percent")
        )
        if rem_raw is not None:
            try:
                rem_pct = float(rem_raw)
                used_pct = max(0.0, min(100.0, round(100.0 - rem_pct, 4)))
            except (ValueError, TypeError):
                rem_pct = None
                used_pct = None
        else:
            used_raw = (
                premium.get("usedPercent")
                if "usedPercent" in premium
                else premium.get("used_percent")
            )
            if used_raw is not None:
                try:
                    used_pct = float(used_raw)
                    rem_pct = max(0.0, min(100.0, round(100.0 - used_pct, 4)))
                except (ValueError, TypeError):
                    used_pct = None
                    rem_pct = None
            elif "usedRequests" in premium and "entitlementRequests" in premium:
                try:
                    ent = float(premium["entitlementRequests"])
                    used_req = float(premium["usedRequests"])
                    if ent > 0:
                        used_pct = max(0.0, min(100.0, round((used_req / ent) * 100.0, 4)))
                        rem_pct = max(0.0, min(100.0, round(100.0 - used_pct, 4)))
                except (ValueError, TypeError, ZeroDivisionError):
                    pass

        if rem_pct is not None and rem_pct <= 0.0:
            hard_limit_reached = True

        reset_val = (
            premium.get("resetDate")
            or premium.get("reset_date")
            or premium.get("resetsAt")
            or premium.get("resets_at")
        )
        resets_at = _parse_resets_at(reset_val)
        if resets_at is None:
            resets_at = next_month_first_day_utc()

    windows: dict[str, UsageWindow] = {}
    if used_pct is not None or rem_pct is not None or resets_at is not None:
        windows["monthly"] = UsageWindow(
            key="monthly",
            used_percent=used_pct,
            remaining_percent=rem_pct,
            resets_at=resets_at,
        )

    return ProviderUsageSnapshot(
        pool_id=pool_id,
        source="copilot",
        windows=windows,
        hard_limit_reached=hard_limit_reached,
        authoritative=True,
        observed_at=datetime.now(timezone.utc),
    )


class CopilotUsageProbe(UsageProbe):
    """Authoritative quota probe using Copilot CLI's internal JSON-RPC server mode."""

    def __init__(
        self,
        *,
        command: str = "copilot --server --stdio --no-auto-update --log-level error",
        cwd: Path | None = None,
        pool_id: str = "copilot",
        cached_snapshot: ProviderUsageSnapshot | None = None,
    ) -> None:
        self.command = command
        self.cwd = cwd
        self.pool_id = pool_id
        self.cached_snapshot = cached_snapshot

    async def fetch_usage(self) -> ProviderUsageSnapshot | None:
        """Query account.getQuota via LSP-framed JSON-RPC server and return normalized snapshot."""
        if self.cached_snapshot is not None:
            return self.cached_snapshot
        try:
            return await self._probe_standalone()
        except Exception as exc:
            log.warning("copilot_usage_probe_failed", pool_id=self.pool_id, error=str(exc))
            return None

    async def _probe_standalone(self) -> ProviderUsageSnapshot | None:
        cmd = self.command
        parts = shlex.split(cmd)
        if "--server" not in parts:
            cmd = f"{cmd} --server --stdio --no-auto-update --log-level error"

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                resolve_bash(),
                "-lc",
                cmd,
                cwd=str(self.cwd) if self.cwd else None,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=MAX_LINE_BYTES,
                start_new_session=os.name == "posix",
            )
            if proc.stdin is None or proc.stdout is None:
                return None

            req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "account.getQuota",
                "params": {},
            }
            body = json.dumps(req).encode("utf-8")
            header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
            proc.stdin.write(header + body)
            await proc.stdin.drain()

            quota_result = None
            for _ in range(10):
                msg = await self._read_lsp_message(proc.stdout, timeout=5.0)
                if msg is None:
                    break
                if msg.get("id") == 1 or "result" in msg:
                    quota_result = msg
                    break

            if quota_result is not None:
                return normalize_copilot_quota(quota_result, pool_id=self.pool_id)
            return None
        except Exception as exc:
            log.warning(
                "copilot_usage_probe_standalone_failed",
                pool_id=self.pool_id,
                error=str(exc),
            )
            return None
        finally:
            if proc is not None:
                try:
                    if proc.stdin and not proc.stdin.is_closing():
                        proc.stdin.close()
                except Exception:
                    pass
                try:
                    await terminate_process_tree(proc)
                except Exception:
                    pass

    @staticmethod
    async def _read_lsp_message(
        stdout: asyncio.StreamReader, timeout: float = 5.0
    ) -> dict[str, Any] | None:
        content_length: int | None = None
        # Read header lines
        while True:
            line_bytes = await asyncio.wait_for(stdout.readline(), timeout=timeout)
            if not line_bytes:
                return None
            line_str = line_bytes.decode("utf-8", errors="replace").strip()
            if not line_str:
                if content_length is not None:
                    break
                continue
            lower = line_str.lower()
            if lower.startswith("content-length:"):
                try:
                    content_length = int(line_str.split(":", 1)[1].strip())
                except ValueError:
                    return None

        if content_length is None or content_length <= 0:
            return None

        if hasattr(stdout, "readexactly"):
            body_bytes = await asyncio.wait_for(
                stdout.readexactly(content_length), timeout=timeout
            )
        else:
            body_bytes = await asyncio.wait_for(
                stdout.read(content_length), timeout=timeout
            )
        body_str = body_bytes.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body_str)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
        return None


USAGE_PROBES["copilot"] = CopilotUsageProbe
