"""Operator chat sessions with the configured agent, run against the host repo.

Reuses the agent backend adapters directly (`build_backend`) with
``cwd == workspace_root == workflow_dir`` so `validate_agent_cwd` passes and
the agent converses about — and in edit mode works inside — the operator's own
working tree. Chat runs outside the orchestrator's `DispatchState` slot
accounting on purpose: one chat session must never starve ticket workers
(`max_concurrent_agents` is often 1).

Known benign interaction: `Orchestrator._apply_dispatch_env` mutates
process-global ``os.environ`` (informational ``SYMPHONY_TOKEN_*`` values)
right before it spawns a worker. A chat turn spawning concurrently may
inherit those values; they only inform prompts and budgets, so no isolation
is attempted here.

Modes:
- ``qa``   — question answering; read-only where the backend supports it
             (claude: ``--permission-mode plan``, codex: read-only sandbox).
- ``edit`` — co-working; the agent may modify the host working tree
             (claude: ``acceptEdits``, codex: as configured).
Other backend kinds cannot be forced read-only; the session reports
``mode_enforced: false`` and relies on the preamble alone.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .backends import (
    EVENT_OTHER_MESSAGE,
    EVENT_SESSION_STARTED,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_FAILED,
    EVENT_TURN_STARTED,
    AgentBackend,
    BackendInit,
    build_backend,
)
from .errors import (
    ChatBusyError,
    ChatNoSessionError,
    ChatSessionExistsError,
    SymphonyError,
)
from .logging import get_logger
from .workflow import SUPPORTED_AGENT_KINDS, ServiceConfig

log = get_logger()

CHAT_MODES = ("qa", "edit")
# Kinds whose read-only enforcement chat can genuinely toggle.
MODE_ENFORCED_KINDS = {"claude", "codex"}

TRANSCRIPT_LIMIT = 500
SUBSCRIBER_QUEUE_LIMIT = 200
SNAPSHOT_TAIL = 100
_TOOL_PREVIEW_CHARS = 200
_RAW_PREVIEW_CHARS = 400

QA_PREAMBLE = (
    "You are chatting with the operator of the repository at {path}. "
    "Answer questions about this repository by reading its files. "
    "Q&A mode: do not create, modify or delete any files. "
    "If the operator asks you to file a board ticket, explain that they "
    "should switch the chat to edit mode first.\n{board}\n"
)
EDIT_PREAMBLE = (
    "You are pair-working with the operator of the repository at {path}. "
    "You may read and modify files in this working tree as requested. "
    "Keep changes minimal and report exactly what you changed.\n{board}\n"
)

_BOARD_PREAMBLE = (
    "This project runs a Symphony kanban board at {board_root}: one markdown "
    "file per ticket, YAML front matter with id, identifier, title, state, "
    "priority (0-4), labels, created_at, updated_at, then the description as "
    "the body. Active states: {states}. To file a new issue for the "
    "orchestrator, create {board_root}/<IDENTIFIER>.md with `state: Todo`, a "
    "clear title and acceptance criteria in the body, using a new identifier "
    "that does not collide with existing ticket files; Symphony picks it up "
    "automatically."
)



# Prepended to the first message after a mode switch — with claude the
# conversation is resumed, so the original preamble's rules stick unless
# explicitly revoked.
QA_MODE_NOTICE = (
    "[Chat mode changed to Q&A: from now on, do not create, modify or "
    "delete any files.]\n\n"
)
EDIT_MODE_NOTICE = (
    "[Chat mode changed to edit: you may now create and modify files in "
    "this working tree as requested, including filing kanban tickets.]\n\n"
)


def _board_preamble(cfg: ServiceConfig) -> str:
    if cfg.tracker.kind != "file" or cfg.tracker.board_root is None:
        return ""
    return _BOARD_PREAMBLE.format(
        board_root=cfg.tracker.board_root,
        states=", ".join(cfg.tracker.active_states),
    )

_PERMISSION_MODE_RE = re.compile(r"\s--permission-mode(?:[ =]\S+)?")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _preview(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _claude_command_for_mode(
    command: str, mode: str, resume_session_id: str | None = None
) -> str:
    """Strip any configured --permission-mode, then append the chat one.

    Append wins even if a quoted remnant survived the strip: the claude CLI
    keeps the last value of a repeated single-value option. `--resume` is
    injected the same way so a rebuilt backend rejoins the prior session on
    its first (non-continuation) turn; the backend's own resume logic appends
    a later `--resume` on turns 2+, which again wins by position.
    """
    base = _PERMISSION_MODE_RE.sub("", command).strip()
    base += " --permission-mode " + ("plan" if mode == "qa" else "acceptEdits")
    if resume_session_id:
        base += f" --resume {shlex.quote(resume_session_id)}"
    return base


def cfg_for_mode(
    cfg: ServiceConfig,
    mode: str,
    agent_kind: str,
    resume_session_id: str | None = None,
) -> tuple[ServiceConfig, bool]:
    """Derive a chat-mode ServiceConfig variant; returns (cfg, mode_enforced)."""
    if agent_kind != cfg.agent.kind:
        cfg = replace(cfg, agent=replace(cfg.agent, kind=agent_kind))
    if agent_kind == "claude":
        command = _claude_command_for_mode(
            cfg.claude.command, mode, resume_session_id
        )
        return replace(cfg, claude=replace(cfg.claude, command=command)), True
    if agent_kind == "codex":
        if mode == "qa":
            codex = replace(
                cfg.codex,
                thread_sandbox="read-only",
                turn_sandbox_policy="read-only",
            )
            return replace(cfg, codex=codex), True
        return cfg, True
    return cfg, False


@dataclass
class ChatMessage:
    """One transcript row == one WS frame == one JSONL line."""

    seq: int
    type: str
    text: str
    timestamp: str
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "type": self.type,
            "text": self.text,
            "timestamp": self.timestamp,
            "meta": self.meta,
        }


@dataclass
class ChatSession:
    session_id: str
    mode: str
    agent_kind: str
    mode_enforced: bool
    created_at: str
    backend: AgentBackend | None = None
    backend_turns: int = 0  # turns run on the current backend instance
    turn_count: int = 0
    pending_mode_notice: bool = False
    last_agent_text: str = ""
    transcript: list[ChatMessage] = field(default_factory=list)


class _TranscriptWriter:
    """Non-blocking JSONL appender (StatsStore pattern, single worker)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="symphony-chat"
        )
        self._failed_logged = False

    def append(self, row: dict[str, Any]) -> None:
        line = json.dumps(row, ensure_ascii=False)
        try:
            self._executor.submit(self._write_line, line)
        except RuntimeError:
            pass  # executor shut down (interpreter exit) — drop the row

    def _write_line(self, line: str) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception as exc:
            if not self._failed_logged:
                self._failed_logged = True
                log.warning(
                    "chat_transcript_write_failed",
                    path=str(self.path),
                    error=str(exc),
                )

    def close(self) -> None:
        self._executor.shutdown(wait=False)


class ChatManager:
    """Single operator chat session against the host repo."""

    def __init__(
        self,
        config_provider: Callable[[], ServiceConfig],
        request_refresh: Callable[[], object] | None = None,
    ) -> None:
        self._config_provider = config_provider
        # Called after each turn so board tickets the agent files (edit mode
        # writes straight into the file board) dispatch on the next tick
        # instead of waiting out the poll interval.
        self._request_refresh = request_refresh
        self._session: ChatSession | None = None
        self._turn_lock = asyncio.Lock()
        self._turn_task: asyncio.Task[None] | None = None
        self._turn_failure_broadcast = False
        self._subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self._seq = 0
        self._writer: _TranscriptWriter | None = None
        self._closed = False

    # ------------------------------------------------------------------
    # session lifecycle
    # ------------------------------------------------------------------

    async def start_session(
        self, mode: str, agent_kind: str | None = None
    ) -> dict[str, Any]:
        if self._closed:
            raise ChatNoSessionError("chat manager is shut down")
        if self._session is not None:
            raise ChatSessionExistsError(
                "a chat session is already active; stop it first"
            )
        mode = _check_mode(mode)
        cfg = self._config_provider()
        kind = (agent_kind or cfg.agent.kind).strip()
        if kind not in SUPPORTED_AGENT_KINDS:
            raise SymphonyError(f"unsupported agent kind {kind!r}")
        session_id = (
            datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-")
            + uuid.uuid4().hex[:6]
        )
        session = ChatSession(
            session_id=session_id,
            mode=mode,
            agent_kind=kind,
            mode_enforced=kind in MODE_ENFORCED_KINDS,
            created_at=_utc_iso(),
        )
        self._session = session
        self._writer = _TranscriptWriter(
            cfg.workflow_path.parent / ".symphony" / "chat" / f"{session_id}.jsonl"
        )
        try:
            await self._build_backend(cfg, session)
        except BaseException:
            self._session = None
            self._close_writer()
            raise
        self._broadcast(
            "session_status",
            f"session started — {kind} agent, {mode} mode",
            meta={"mode": mode, "agent_kind": kind},
        )
        return self.snapshot()

    async def stop_session(self) -> None:
        session = self._session
        if session is None:
            raise ChatNoSessionError("no active chat session")
        task = self._turn_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._turn_task = None
        if session.backend is not None:
            try:
                await session.backend.stop()
            except Exception as exc:
                log.warning("chat_backend_stop_failed", error=str(exc))
        self._broadcast("session_status", "session stopped", meta={})
        self._session = None
        self._close_writer()

    async def set_mode(self, mode: str) -> dict[str, Any]:
        session = self._require_session()
        mode = _check_mode(mode)
        if self._turn_lock.locked():
            raise ChatBusyError("a turn is running; wait before changing mode")
        if mode == session.mode:
            return {
                "mode": mode,
                "context_preserved": True,
                "mode_enforced": session.mode_enforced,
            }
        cfg = self._config_provider()
        resume_id: str | None = None
        context_preserved = False
        old_backend = session.backend
        if session.agent_kind == "claude" and old_backend is not None:
            sid = old_backend.session_id
            if sid and sid != "pending":
                resume_id = sid
                context_preserved = True
        if old_backend is not None:
            try:
                await old_backend.stop()
            except Exception as exc:
                log.warning("chat_backend_stop_failed", error=str(exc))
        session.mode = mode
        session.backend = None
        session.backend_turns = 0
        session.pending_mode_notice = True
        await self._build_backend(cfg, session, resume_session_id=resume_id)
        self._broadcast(
            "session_status",
            f"mode changed to {mode}"
            + ("" if context_preserved else " — conversation context reset"),
            meta={"mode": mode, "context_preserved": context_preserved},
        )
        return {
            "mode": mode,
            "context_preserved": context_preserved,
            "mode_enforced": session.mode_enforced,
        }

    async def send_message(self, text: str) -> dict[str, Any]:
        session = self._require_session()
        if self._turn_lock.locked():
            raise ChatBusyError("a turn is already running")
        text = text.strip()
        if not text:
            raise SymphonyError("message text is required")
        cfg = self._config_provider()
        preamble = QA_PREAMBLE if session.mode == "qa" else EDIT_PREAMBLE
        prompt = text
        if session.turn_count == 0:
            prompt = (
                preamble.format(
                    path=cfg.workflow_path.parent, board=_board_preamble(cfg)
                )
                + text
            )
        elif session.pending_mode_notice:
            # A resumed conversation keeps obeying the original preamble's
            # rules; revoke or grant them explicitly on the first message
            # after a mode switch.
            notice = QA_MODE_NOTICE if session.mode == "qa" else EDIT_MODE_NOTICE
            prompt = notice + text
        session.pending_mode_notice = False
        self._broadcast("user_message", text)
        self._turn_task = asyncio.create_task(self._run_turn(session, prompt))
        return self.snapshot()

    async def close(self) -> None:
        """`app.on_shutdown` hook: stop the session, wake subscribers."""
        self._closed = True
        if self._session is not None:
            try:
                await self.stop_session()
            except Exception as exc:
                log.warning("chat_close_failed", error=str(exc))
        for queue in list(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._subscribers.clear()

    # ------------------------------------------------------------------
    # subscriptions + snapshot
    # ------------------------------------------------------------------

    def subscribe(self) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=SUBSCRIBER_QUEUE_LIMIT
        )
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        self._subscribers.discard(queue)

    def snapshot(self) -> dict[str, Any]:
        session = self._session
        if session is None:
            return {"active": False}
        return {
            "active": True,
            "session_id": session.session_id,
            "mode": session.mode,
            "agent_kind": session.agent_kind,
            "mode_enforced": session.mode_enforced,
            "busy": self._turn_lock.locked(),
            "turn_count": session.turn_count,
            "created_at": session.created_at,
            "transcript_tail": [
                m.as_dict() for m in session.transcript[-SNAPSHOT_TAIL:]
            ],
        }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _require_session(self) -> ChatSession:
        if self._session is None:
            raise ChatNoSessionError("no active chat session")
        return self._session

    async def _build_backend(
        self,
        cfg: ServiceConfig,
        session: ChatSession,
        resume_session_id: str | None = None,
    ) -> None:
        mode_cfg, enforced = cfg_for_mode(
            cfg, session.mode, session.agent_kind, resume_session_id
        )
        session.mode_enforced = enforced
        workflow_dir = cfg.workflow_path.parent
        backend = build_backend(
            BackendInit(
                cfg=mode_cfg,
                cwd=workflow_dir,
                workspace_root=workflow_dir,
                on_event=self._on_backend_event,
            )
        )
        await backend.start()
        await backend.initialize()
        await backend.start_session(initial_prompt="", issue_title=None)
        session.backend = backend

    async def _run_turn(self, session: ChatSession, prompt: str) -> None:
        async with self._turn_lock:
            backend = session.backend
            if backend is None:
                self._broadcast("turn_failed", "no backend for session", meta={})
                return
            is_first = session.backend_turns == 0
            self._turn_failure_broadcast = False
            try:
                await backend.run_turn(
                    prompt=prompt, is_continuation=not is_first
                )
            except asyncio.CancelledError:
                raise
            except SymphonyError as exc:
                if not self._turn_failure_broadcast:
                    self._broadcast(
                        "turn_failed",
                        f"{exc.code}: {exc.message}",
                        meta={"code": exc.code},
                    )
                log.warning("chat_turn_failed", code=exc.code, error=exc.message)
            except Exception as exc:
                if not self._turn_failure_broadcast:
                    self._broadcast("turn_failed", str(exc), meta={})
                log.warning("chat_turn_failed", error=str(exc))
            finally:
                session.backend_turns += 1
                session.turn_count += 1
                if self._request_refresh is not None:
                    try:
                        self._request_refresh()
                    except Exception as exc:
                        log.warning("chat_refresh_failed", error=str(exc))

    async def _on_backend_event(self, envelope: dict[str, Any]) -> None:
        session = self._session
        if session is None:
            return
        event = envelope.get("event")
        payload = envelope.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if event == EVENT_TURN_STARTED:
            self._broadcast(
                "turn_started", "", meta={"agent_pid": envelope.get("agent_pid")}
            )
        elif event == EVENT_SESSION_STARTED:
            self._broadcast(
                "session_status",
                "",
                meta={"agent_session_id": payload.get("session_id")},
            )
        elif event == EVENT_TURN_COMPLETED:
            message = str(payload.get("message") or "").strip()
            if message and message != session.last_agent_text:
                self._broadcast("agent_message", message)
                session.last_agent_text = message
            self._broadcast(
                "turn_completed", "", meta={"usage": envelope.get("usage") or {}}
            )
        elif event == EVENT_TURN_FAILED:
            self._turn_failure_broadcast = True
            reason = str(
                payload.get("reason") or payload.get("error") or "turn failed"
            )
            self._broadcast("turn_failed", reason, meta={})
        elif event == EVENT_OTHER_MESSAGE:
            for type_, text, meta in _summarize_frame(session.agent_kind, payload):
                if type_ == "agent_message":
                    session.last_agent_text = text
                self._broadcast(type_, text, meta=meta)
        # remaining events (malformed, notifications, approvals) stay internal

    def _broadcast(
        self, type_: str, text: str, meta: dict[str, Any] | None = None
    ) -> ChatMessage:
        self._seq += 1
        msg = ChatMessage(
            seq=self._seq,
            type=type_,
            text=text,
            timestamp=_utc_iso(),
            meta=meta or {},
        )
        session = self._session
        if session is not None:
            session.transcript.append(msg)
            if len(session.transcript) > TRANSCRIPT_LIMIT:
                del session.transcript[: len(session.transcript) - TRANSCRIPT_LIMIT]
        if self._writer is not None:
            self._writer.append(msg.as_dict())
        row = msg.as_dict()
        for queue in list(self._subscribers):
            if queue.full():
                # Drop-oldest: a slow websocket must not stall the turn.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(row)
            except asyncio.QueueFull:
                pass
        return msg

    def _close_writer(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None


def _check_mode(raw: str) -> str:
    mode = (raw or "").strip().lower()
    if mode not in CHAT_MODES:
        raise SymphonyError(f"mode must be one of {', '.join(CHAT_MODES)}")
    return mode


# ---------------------------------------------------------------------------
# backend frame -> chat message summarization
# ---------------------------------------------------------------------------


def _summarize_frame(
    agent_kind: str, payload: dict[str, Any]
) -> list[tuple[str, str, dict[str, Any]]]:
    if agent_kind == "claude":
        return _summarize_claude_frame(payload)
    if agent_kind == "codex":
        return _summarize_codex_frame(payload)
    raw = _preview(json.dumps(payload, ensure_ascii=False), _RAW_PREVIEW_CHARS)
    return [("tool_activity", "event", {"detail": raw})] if raw != "{}" else []


def _summarize_claude_frame(
    payload: dict[str, Any]
) -> list[tuple[str, str, dict[str, Any]]]:
    """stream-json `assistant` / `user` frames -> chat messages."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    kind = payload.get("type")
    message = payload.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return out
    if kind == "assistant":
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = str(block.get("text") or "").strip()
                if text:
                    out.append(("agent_message", text, {"partial": True}))
            elif block.get("type") == "tool_use":
                name = str(block.get("name") or "tool")
                detail = _preview(
                    json.dumps(block.get("input") or {}, ensure_ascii=False),
                    _TOOL_PREVIEW_CHARS,
                )
                out.append(("tool_activity", name, {"detail": detail}))
    elif kind == "user":
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                out.append(
                    (
                        "tool_activity",
                        "result",
                        {"detail": _tool_result_preview(block)},
                    )
                )
    return out


def _tool_result_preview(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return _preview(content, _TOOL_PREVIEW_CHARS)
    if isinstance(content, list):
        parts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return _preview(" ".join(p for p in parts if p), _TOOL_PREVIEW_CHARS)
    return ""


def _summarize_codex_frame(
    payload: dict[str, Any]
) -> list[tuple[str, str, dict[str, Any]]]:
    """codex `item/completed` echoes -> chat messages."""
    if payload.get("type") == "assistant" and isinstance(
        payload.get("message"), str
    ):
        text = payload["message"].strip()
        return [("agent_message", text, {"partial": True})] if text else []
    item = payload.get("item")
    if isinstance(item, dict):
        itype = str(item.get("type") or "item")
        detail = _preview(
            json.dumps(item, ensure_ascii=False), _TOOL_PREVIEW_CHARS
        )
        return [("tool_activity", itype, {"detail": detail})]
    return []
