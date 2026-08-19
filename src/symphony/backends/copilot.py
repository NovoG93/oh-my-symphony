"""GitHub Copilot CLI backend.

Drives `copilot --output-format=json --no-ask-user --allow-all-tools -p ""` once per turn.
"""

from __future__ import annotations

from datetime import datetime
import json
import shlex
import uuid
from typing import Any

from ..errors import TurnFailed
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
from .per_turn import PerTurnCliBackend
from .usage import (
    ProviderUsageSnapshot,
    UsageProbe,
    USAGE_PROBES,
)


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
    """Return True if error text signals provider quota/plan exhaustion rather than RPM."""
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
        "usage_limit",
        "insufficient credits",
        "rate limit reached",
        "rate_limit_reached",
        "out of credits",
        "ai credits exhausted",
        "premium requests exhausted",
        "provider_usage_exhausted",
        "provider usage exhausted",
    )
    return any(kw in lowered for kw in exhaustion_keywords)


class CopilotUsageProbe(UsageProbe):
    """Usage probe for GitHub Copilot."""

    def __init__(
        self,
        *,
        pool_id: str = "copilot",
        cached_snapshot: ProviderUsageSnapshot | None = None,
    ) -> None:
        self.pool_id = pool_id
        self.cached_snapshot = cached_snapshot

    async def fetch_usage(self) -> ProviderUsageSnapshot | None:
        """Return cached snapshot or None (fails open)."""
        return self.cached_snapshot


USAGE_PROBES["copilot"] = CopilotUsageProbe
