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

Two streams leave a turn. Numbered `ChatMessage` rows go to the transcript,
the JSONL and every subscriber; ephemeral `agent_delta` chunks and cumulative
`agent_snapshot` text go to subscribers only — see `_broadcast_ephemeral`.
The per-session token/turn budget is advisory: crossing a limit warns once,
and the session keeps running, because only the operator can judge when a
conversation is done.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import shlex
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any, Callable, Protocol

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
    ChatProjectActionError,
    ChatProjectAuthorizationError,
    ChatSessionExistsError,
    SymphonyError,
)
from .logging import get_logger
from .projects import ProjectTargetExpectation, project_target_expectation
from .workflow import SUPPORTED_AGENT_KINDS, ServiceConfig

log = get_logger()

CHAT_MODES = ("qa", "edit")
# Kinds whose read-only enforcement chat can genuinely toggle.
MODE_ENFORCED_KINDS = {"claude", "codex"}

TRANSCRIPT_LIMIT = 500
SUBSCRIBER_QUEUE_LIMIT = 200
SNAPSHOT_TAIL = 100

# Every live session owns an agent CLI process, so the cap is about host
# resources and token spend, not bookkeeping.
MAX_SESSIONS = 3
INDEX_VERSION = 1
# Reattach reads the tail of a transcript; a long-lived session's JSONL can
# be large, so only the last slice of the file is ever parsed.
_INDEX_NAME = "index.json"
_REPLAY_TAIL_BYTES = 2 * 1024 * 1024
_MAX_INDEX_BYTES = 1 * 1024 * 1024
_MAX_JSON_SAFE_INTEGER = 9_007_199_254_740_991
_SESSION_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_TITLE_CHARS = 80
MAX_INDEX_ENTRIES = 50
_TOOL_PREVIEW_CHARS = 200
_RAW_PREVIEW_CHARS = 400

# Frame types that are streamed to live subscribers but never numbered,
# kept in the transcript or written to the JSONL: hundreds arrive per turn
# and the terminal `agent_message` repeats the same text verbatim.
EPHEMERAL_TYPES = frozenset({"agent_delta", "agent_snapshot"})

# Default per-session budget. Advisory only — crossing a limit raises a
# warning banner, it never blocks a turn (the operator decides when to stop).
# 0 disables the respective limit.
DEFAULT_MAX_TURNS = 50
DEFAULT_MAX_TOKENS = 1_000_000

QA_PREAMBLE = (
    "You are chatting with the operator of the repository at {path}. "
    "Answer questions about this repository by reading its files. "
    "Q&A mode: do not create, modify or delete any files. "
    "For a software request, describe the tickets you would file (ids, "
    "states, blocked-by DAG) and ask the operator to switch the chat to "
    "edit mode to file them.\n{board}\n"
)
EDIT_PREAMBLE = (
    "You are pair-working with the operator of the repository at {path}. "
    "You may read and modify files in this working tree as requested. "
    "Keep changes minimal and report exactly what you changed.\n{board}\n"
    "{project_setup}\n\n"
)

# A separate Project changes the global registry and may bootstrap a Git
# repository. It is therefore a server-owned, explicitly confirmed action,
# not an ambient shell capability granted to the chat backend.
_PROJECT_SETUP_PREAMBLE = (
    "A separate Symphony project is a control-plane action. When offering an "
    "option to create or adopt one, do NOT run `symphony project`, create its "
    "files yourself, or claim it was registered. Instead include exactly one "
    "machine-readable proposal for that option, after the human explanation:\n"
    "<symphony-project-setup>{\"choice\": 1, \"name\": \"Project name\", "
    "\"path\": \"/absolute/project/path\"}</symphony-project-setup>\n"
    "Use a positive option number, a non-empty name, and an absolute path. "
    "The server shows the choice and creates/registers it only if the operator "
    "selects that number. It reports the authoritative result; do not auto-start "
    "or switch to the new project, and do not file work on its board until the "
    "operator explicitly asks."
)
_PROJECT_SETUP_OPEN = "<symphony-project-setup>"
_PROJECT_SETUP_CLOSE = "</symphony-project-setup>"
_PROJECT_SETUP_MAX_NAME = 100
_PROJECT_SETUP_MAX_PATH = 4096
_PROJECT_SETUP_MAX_PAYLOAD = 8 * 1024
_PROJECT_SETUP_TTL = timedelta(minutes=15)
# Browser-generated capability; it is never placed in snapshots, transcripts,
# prompts, or the workspace. Its SHA-256 digest is process-local only. It
# prevents accidental/raw-chat and cross-origin confirmation, not a hostile
# same-UID process; that threat requires OS/network/process isolation.
_CHAT_CONFIRMATION_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_MAX_PROJECT_ACTIONS_PER_SESSION = 20


# Build-request protocol taught to the chat agent. Rendered with the
# board's ACTUAL active states; the routing paragraph differs between the
# 4-lane default board (chat files the stage-ticket DAG itself) and a
# deep-preset board with an Intake lane (the pipeline decomposes).
_BOARD_PREAMBLE = (
    "This project runs a Symphony kanban board at {board_root} "
    "(active states: {states}). Questions: just answer. Software requests "
    "(build/fix/feature/refactor): confirm scope in at most 2 short turns, "
    "and only if genuinely ambiguous; then file tickets with the validated "
    "CLI — NEVER hand-write ticket markdown files:\n"
    '  ${{SYMPHONY_CLI:-symphony}} board new <ID> "<title>" --state <state> '
    "--request REQ-<n> --blocked-by <ID> --description-file -\n"
    "(SYMPHONY_CLI is exported by the orchestrator; use it when `symphony` "
    "is not on PATH. `${{SYMPHONY_CLI:-symphony}} board update <ID> "
    "--state <state> --add-blocked-by <ID>` edits an existing ticket.)\n"
    "(description on stdin; the CLI validates ids, states and DAG "
    "acyclicity; use the next free REQ-<n> as the request id).\n"
    "{routing}"
    "Each description is a self-contained worker prompt: Goal / Scope "
    "in-out / Acceptance criteria / Evidence expected — succinct. After "
    "filing, reply with the ticket ids and a one-line DAG summary; the "
    "orchestrator picks them up automatically."
)

_DEFAULT_ROUTING = (
    "SIMPLE task: one ticket in {first_state} with goal + acceptance "
    "criteria. COMPLEX task (new app, multi-file feature, unclear domain): "
    "a stage-ticket DAG chained via --blocked-by under one --request — "
    "research -> plan -> adversarial plan-review -> build ticket(s) -> qa "
    "-> document, titled accordingly (e.g. 'REQ-3 research: ...').\n"
)
_DEEP_ROUTING = (
    "This board runs the deep pipeline: file ONE Intake ticket per request "
    "and let the pipeline decompose it.\n"
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
    "this working tree as requested, including filing board tickets with "
    "`${SYMPHONY_CLI:-symphony} board new`. A separate project still requires "
    "the explicit server-owned project-setup proposal below.]\n\n"
    + _PROJECT_SETUP_PREAMBLE
    + "\n\n"
)


def _board_preamble(cfg: ServiceConfig) -> str:
    if cfg.tracker.kind != "file" or cfg.tracker.board_root is None:
        return ""
    states = cfg.tracker.active_states
    # An Intake lane marks a deep-preset (or deep-shaped custom) board:
    # the pipeline decomposes there, so chat files one Intake ticket.
    deep = any(s.strip().lower() == "intake" for s in states)
    routing = (
        _DEEP_ROUTING
        if deep
        else _DEFAULT_ROUTING.format(first_state=states[0] if states else "Todo")
    )
    return _BOARD_PREAMBLE.format(
        board_root=cfg.tracker.board_root,
        states=", ".join(states),
        routing=routing,
    )

_PERMISSION_MODE_RE = re.compile(r"\s--permission-mode(?:[ =]\S+)?")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _project_setup_expiry() -> str:
    return (datetime.now(timezone.utc) + _PROJECT_SETUP_TTL).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _project_setup_is_expired(expires_at: str) -> bool:
    """Malformed or legacy unbounded actions fail closed."""

    try:
        expiry = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return True
    return datetime.now(timezone.utc) >= expiry


def _project_setup_error(exc: Exception) -> str:
    """Bound and redact an error before it reaches a durable Chat transcript."""

    # Import lazily: the orchestrator package re-exports core, whose imports
    # ultimately include chat during normal service startup.
    from .orchestrator.diagnostics import redact_text

    return redact_text(exc, maximum=800) or "project setup failed"


def _confirmation_token_hash(value: object) -> str | None:
    """Hash one browser-held confirmation capability without retaining it."""

    token = value.strip() if isinstance(value, str) else ""
    if not _CHAT_CONFIRMATION_TOKEN_RE.fullmatch(token):
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


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
class ProjectSetupAction:
    """One explicit, server-owned project setup choice emitted by Chat."""

    action_id: str
    choice: int
    name: str
    path: str
    operation: str = "unknown"
    status: str = "pending"
    project: dict[str, Any] | None = None
    error: str | None = None
    choice_active: bool = True
    expires_at: str = field(default_factory=_project_setup_expiry)
    task: asyncio.Task[None] | None = field(default=None, repr=False, compare=False)
    target_expectation: ProjectTargetExpectation | None = field(
        default=None, repr=False, compare=False
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "choice": self.choice,
            "name": self.name,
            "path": self.path,
            "operation": self.operation,
            "status": self.status,
            "project": self.project,
            "error": self.error,
            "choice_active": self.choice_active,
            "expires_at": self.expires_at,
        }


def _project_setup_operation(expectation: ProjectTargetExpectation) -> str:
    if not expectation.path_exists:
        return "create"
    return "adopt" if expectation.git_common_dir is not None else "initialize"


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate JSON members at every object depth."""

    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _project_setup_spec(text: str) -> tuple[str, ProjectSetupAction | None]:
    """Extract one strictly shaped server-owned project proposal from agent text.

    Ordinary prose remains ordinary chat. Only the documented marker becomes a
    selectable action, so a later bare numeric reply cannot turn arbitrary
    model output into a filesystem mutation.
    """

    # Do not run a dot-star regex over backend-controlled output: repeated
    # unmatched opening tags otherwise cause quadratic rescans.
    if text.count(_PROJECT_SETUP_OPEN) != 1 or text.count(_PROJECT_SETUP_CLOSE) != 1:
        return text, None
    open_start = text.find(_PROJECT_SETUP_OPEN)
    start = open_start + len(_PROJECT_SETUP_OPEN)
    end = text.find(_PROJECT_SETUP_CLOSE)
    if end < start:
        return text, None
    payload = text[start:end].strip()
    if len(payload) > _PROJECT_SETUP_MAX_PAYLOAD:
        return text, None
    try:
        value = json.loads(payload, object_pairs_hook=_strict_json_object)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return text, None
    if not isinstance(value, dict) or set(value) != {"choice", "name", "path"}:
        return text, None
    choice = value.get("choice")
    name = value.get("name")
    raw_path = value.get("path")
    if (
        not isinstance(choice, int)
        or isinstance(choice, bool)
        or not 1 <= choice <= 99
        or not isinstance(name, str)
        or not 0 < len(name.strip()) <= _PROJECT_SETUP_MAX_NAME
        or not isinstance(raw_path, str)
        or not 0 < len(raw_path.strip()) <= _PROJECT_SETUP_MAX_PATH
    ):
        return text, None
    try:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute() or (
            candidate.exists() and not candidate.is_dir()
        ):
            return text, None
        # A nested directory in an existing Git checkout is not a distinct
        # Project: the shared creation service canonicalizes it to the repo
        # root. Capture the topology too, so confirmation cannot silently turn
        # a proposed new directory into adoption of a later Git checkout.
        target_expectation = project_target_expectation(candidate)
        path = str(target_expectation.repo)
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return text, None
    resolved_name = name.strip()
    # A fresh, server-generated nonce lets an operator receive a new card for
    # an identical proposal after an earlier card expires or fails.
    action_id = "project-" + uuid.uuid4().hex
    visible = (
        text[:open_start] + text[end + len(_PROJECT_SETUP_CLOSE) :]
    ).strip()
    return visible, ProjectSetupAction(
        action_id=action_id,
        choice=choice,
        name=resolved_name,
        path=path,
        operation=_project_setup_operation(target_expectation),
        target_expectation=target_expectation,
    )


def _project_setup_target_at_confirmation(
    expectation: ProjectTargetExpectation,
) -> Path:
    """Refuse a root or Git-identity change after the operator saw the card."""

    current = project_target_expectation(expectation.repo)
    if current != expectation:
        raise ChatProjectActionError(
            "project target changed; request a new project setup proposal"
        )
    return current.repo


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
    # Set when a reattach could not restore the agent's context, so the
    # next message has to reintroduce the repository and the rules.
    pending_preamble: bool = False
    last_agent_text: str = ""
    transcript: list[ChatMessage] = field(default_factory=list)
    # Sessions run concurrently, so the turn lock, the in-flight task, the
    # sequence counter and the JSONL writer all belong to the session rather
    # than the manager.
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    turn_task: asyncio.Task[None] | None = None
    turn_failure_broadcast: bool = False
    writer: "_TranscriptWriter | None" = None
    seq: int = 0
    # Persisted so a session survives a server restart: the backend's own
    # session id is what `--resume` needs, and the title labels the tab.
    agent_session_id: str | None = None
    title: str = ""
    updated_at: str = ""
    # Advisory budget (0 == unlimited). `token_base` carries the tokens
    # accrued on previous backend instances: a backend reports its own usage
    # cumulatively, and a mode switch rebuilds it, so the running total is
    # base + whatever the current backend last reported.
    max_turns: int = DEFAULT_MAX_TURNS
    max_tokens: int = DEFAULT_MAX_TOKENS
    used_tokens: int = 0
    token_base: int = 0
    budget_warned: bool = False
    # This SHA-256 digest originates from a browser-held random capability.
    # It is deliberately absent from transcript/index/workspace persistence.
    confirmation_token_hash: str | None = None
    # These choices are created only from strict project-setup markers in an
    # edit-mode response. They are process-local: restart/reattach intentionally
    # drops them rather than trusting the agent-writable transcript/index.
    project_setup_actions: dict[str, ProjectSetupAction] = field(default_factory=dict)

    def budget(self) -> dict[str, Any]:
        return {
            "max_turns": self.max_turns,
            "max_tokens": self.max_tokens,
            "turn_count": self.turn_count,
            "used_tokens": self.used_tokens,
            "exceeded": self.budget_exceeded(),
        }

    def budget_exceeded(self) -> bool:
        return bool(
            (self.max_turns and self.turn_count >= self.max_turns)
            or (self.max_tokens and self.used_tokens >= self.max_tokens)
        )

    def budget_reason(self) -> str:
        parts = []
        if self.max_turns and self.turn_count >= self.max_turns:
            parts.append(f"{self.turn_count}/{self.max_turns} turns")
        if self.max_tokens and self.used_tokens >= self.max_tokens:
            parts.append(f"{self.used_tokens:,}/{self.max_tokens:,} tokens")
        return " and ".join(parts)


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

    def flush(self) -> None:
        """Block until queued rows are on disk (blocking — call off-loop).

        A reattach replays this file, so a session that is stopping has to
        finish writing before its transcript is read back.
        """
        self._executor.shutdown(wait=True)

    def close(self) -> None:
        self._executor.shutdown(wait=False)


def _default_project_creator(
    name: str,
    path: Path,
    *,
    expected_target: ProjectTargetExpectation | None = None,
) -> Any:
    """Use the same guarded domain operation as CLI and project management."""

    from .projects import ProjectRegistry, create_or_adopt_project, source_checkout

    return create_or_adopt_project(
        target=path,
        source=source_checkout(),
        registry=ProjectRegistry(),
        name=name,
        expected_target=expected_target,
    )


class ProjectSetupCreator(Protocol):
    """Control-plane creator bound to the canonical root shown on the card."""

    def __call__(
        self,
        name: str,
        path: Path,
        *,
        expected_target: ProjectTargetExpectation,
    ) -> Any: ...


class ChatManager:
    """Up to `MAX_SESSIONS` operator chat sessions against the host repo.

    Sessions are independent: each owns its turn lock, sequence counter and
    JSONL transcript, so a long turn in one never blocks another. Metadata
    is mirrored into `.symphony/chat/index.json` after every state change,
    which is what lets a session be reattached after a server restart —
    the live backend dies with the process, but its agent-side session id
    and transcript survive on disk.

    "Active" only exists for the legacy singular REST alias: it is the most
    recently started or reattached session.
    """

    def __init__(
        self,
        config_provider: Callable[[], ServiceConfig],
        request_refresh: Callable[[], object] | None = None,
        project_creator: ProjectSetupCreator | None = None,
    ) -> None:
        self._config_provider = config_provider
        # Called after each turn so board tickets the chat agent files (edit mode
        # writes straight into the file board) dispatch on the next tick
        # instead of waiting out the poll interval.
        self._request_refresh = request_refresh
        # Project setup is deliberately injected at the control-plane boundary.
        # Backends never receive a shell capability for global registry writes.
        self._project_creator = project_creator or _default_project_creator
        self._sessions: dict[str, ChatSession] = {}
        self._active_id: str | None = None
        # queue -> focused session id. Numbered frames go to every
        # subscriber; ephemeral token deltas only to the socket actually
        # displaying that session, so a background session cannot flood a
        # bounded queue and evict real messages.
        self._subscribers: dict[asyncio.Queue[dict[str, Any] | None], str | None] = {}
        self._closed = False

    # ------------------------------------------------------------------
    # accessors
    # ------------------------------------------------------------------

    @property
    def active_session(self) -> ChatSession | None:
        return self._sessions.get(self._active_id) if self._active_id else None

    @property
    def active_session_id(self) -> str | None:
        return self._active_id

    @property
    def live_count(self) -> int:
        return len(self._sessions)

    def session(self, session_id: str) -> ChatSession | None:
        return self._sessions.get(session_id)

    # ------------------------------------------------------------------
    # session lifecycle
    # ------------------------------------------------------------------

    async def start_session(
        self,
        mode: str,
        agent_kind: str | None = None,
        max_turns: int | None = None,
        max_tokens: int | None = None,
        confirmation_token: str | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise ChatNoSessionError("chat manager is shut down")
        if len(self._sessions) >= MAX_SESSIONS:
            raise ChatSessionExistsError(
                f"chat session limit reached ({MAX_SESSIONS}); stop one first"
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
            max_turns=DEFAULT_MAX_TURNS if max_turns is None else max_turns,
            max_tokens=DEFAULT_MAX_TOKENS if max_tokens is None else max_tokens,
            confirmation_token_hash=_confirmation_token_hash(confirmation_token),
        )
        # A session without a browser-held capability remains usable for chat,
        # but it cannot confirm a global project mutation.
        session.writer = _TranscriptWriter(self._transcript_path(session_id))
        self._sessions[session_id] = session
        self._active_id = session_id
        try:
            await self._build_backend(cfg, session)
        except BaseException:
            self._forget_live(session)
            raise
        self._broadcast(
            session,
            "session_status",
            f"session started — {kind} agent, {mode} mode",
            meta={"mode": mode, "agent_kind": kind},
        )
        self._save_index()
        return self.snapshot(session_id)

    async def reattach(
        self, session_id: str, confirmation_token: str | None = None
    ) -> dict[str, Any]:
        """Bring a session recorded in the index back to life.

        The session index and transcript share the editable workflow tree, so
        reattach restores only display history. It starts a configured Q&A
        backend without an agent-side resume; the operator must explicitly
        switch back to edit mode after reattach.
        """
        if self._closed:
            raise ChatNoSessionError("chat manager is shut down")
        live = self._sessions.get(session_id)
        if live is not None:
            return self.snapshot(session_id)
        if len(self._sessions) >= MAX_SESSIONS:
            raise ChatSessionExistsError(
                f"chat session limit reached ({MAX_SESSIONS}); stop one first"
            )
        entry = await asyncio.to_thread(self._find_index_entry, session_id)
        if entry is None:
            raise ChatNoSessionError(f"unknown chat session {session_id!r}")
        cfg = self._config_provider()
        kind = cfg.agent.kind
        if kind not in SUPPORTED_AGENT_KINDS:
            raise SymphonyError(f"unsupported agent kind {kind!r}")
        session = ChatSession(
            session_id=session_id,
            mode="qa",
            agent_kind=kind,
            mode_enforced=kind in MODE_ENFORCED_KINDS,
            created_at=str(entry.get("created_at") or _utc_iso()),
            turn_count=_as_int(entry.get("turn_count")),
            max_turns=_as_int(entry.get("max_turns"), DEFAULT_MAX_TURNS),
            max_tokens=_as_int(entry.get("max_tokens"), DEFAULT_MAX_TOKENS),
            used_tokens=_as_int(entry.get("used_tokens")),
            title=str(entry.get("title") or ""),
            confirmation_token_hash=_confirmation_token_hash(confirmation_token),
        )
        # Chat transcripts and indexes share the editable workflow tree. A
        # completed server process never restores mode, backend, resume, or
        # project-action authority from either source.
        session.pending_preamble = True
        transcript = await asyncio.to_thread(
            _load_transcript, self._transcript_path(session_id)
        )
        session.transcript = transcript
        session.seq = max((m.seq for m in transcript), default=0)
        session.writer = _TranscriptWriter(self._transcript_path(session_id))
        self._sessions[session_id] = session
        self._active_id = session_id
        try:
            await self._build_backend(cfg, session)
        except BaseException:
            self._forget_live(session)
            raise
        self._broadcast(
            session,
            "session_status",
            "session reattached — Q&A mode restored; switch to edit to make changes",
            meta={"reattached": True, "context_preserved": False},
        )
        self._save_index()
        return self.snapshot(session_id)

    async def stop_session(
        self, session_id: str | None = None, forget: bool = False
    ) -> None:
        session = self._resolve(session_id)
        task = session.turn_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        session.turn_task = None
        # Project setup is a protected control-plane mutation. Do not close its
        # transcript writer while its domain call can still commit; the task is
        # intentionally shielded from request cancellation in confirmation.
        for action in session.project_setup_actions.values():
            if action.task is not None:
                await asyncio.shield(action.task)
        if session.backend is not None:
            try:
                await session.backend.stop()
            except Exception as exc:
                log.warning("chat_backend_stop_failed", error=str(exc))
        self._broadcast(session, "session_status", "session stopped", meta={})
        self._save_index()
        if session.writer is not None:
            await asyncio.to_thread(session.writer.flush)
        self._forget_live(session)
        if forget:
            # Drops the index entry only — the JSONL transcript stays put as
            # an audit trail of what the agent was asked to do.
            self._drop_index_entry(session.session_id)

    async def set_mode(
        self, mode: str, session_id: str | None = None
    ) -> dict[str, Any]:
        session = self._resolve(session_id)
        mode = _check_mode(mode)
        if session.turn_lock.locked():
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
        self._close_project_setup_choice_windows(session, reason="a mode change")
        session.backend = None
        session.backend_turns = 0
        # A restarted backend has no memory of the original safety and board
        # rules. Give it the full preamble instead of the terse mode notice.
        session.pending_preamble = not context_preserved
        session.pending_mode_notice = context_preserved
        await self._build_backend(cfg, session, resume_session_id=resume_id)
        self._broadcast(
            session,
            "session_status",
            f"mode changed to {mode}"
            + ("" if context_preserved else " — conversation context reset"),
            meta={"mode": mode, "context_preserved": context_preserved},
        )
        self._save_index()
        return {
            "mode": mode,
            "context_preserved": context_preserved,
            "mode_enforced": session.mode_enforced,
        }

    async def send_message(
        self, text: str, session_id: str | None = None
    ) -> dict[str, Any]:
        session = self._resolve(session_id)
        if session.turn_lock.locked():
            raise ChatBusyError("a turn is already running")
        text = text.strip()
        if not text:
            raise SymphonyError("message text is required")
        cfg = self._config_provider()
        preamble = QA_PREAMBLE if session.mode == "qa" else EDIT_PREAMBLE
        prompt = text
        if session.turn_count == 0 or session.pending_preamble:
            prompt = (
                preamble.format(
                    path=cfg.workflow_path.parent,
                    board=_board_preamble(cfg),
                    project_setup=(
                        _PROJECT_SETUP_PREAMBLE if session.mode == "edit" else ""
                    ),
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
        session.pending_preamble = False
        # Any ordinary message ends the bare-numeric selection window. The
        # visible action card remains explicitly confirmable until its expiry;
        # this prevents a later unrelated ``1`` from mutating the registry.
        self._close_project_setup_choice_windows(session, reason="an ordinary message")
        # Counted at send time, not on completion: the turn's tokens are
        # already committed, and `turn_completed` carries a budget snapshot
        # that would otherwise be one turn stale. Must follow the preamble
        # decision above, which keys off `turn_count == 0`.
        session.turn_count += 1
        if not session.title:
            session.title = _preview(text, _TITLE_CHARS)
        self._broadcast(session, "user_message", text)
        session.turn_task = asyncio.create_task(self._run_turn(session, prompt))
        return self.snapshot(session.session_id)

    async def close(self) -> None:
        """`app.on_shutdown` hook: stop every session, wake subscribers."""
        self._closed = True
        for session_id in list(self._sessions):
            try:
                await self.stop_session(session_id)
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

    def subscribe(
        self, focus_session_id: str | None = None
    ) -> asyncio.Queue[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=SUBSCRIBER_QUEUE_LIMIT
        )
        self._subscribers[queue] = focus_session_id
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any] | None]) -> None:
        self._subscribers.pop(queue, None)

    def set_focus(
        self, queue: asyncio.Queue[dict[str, Any] | None], session_id: str | None
    ) -> None:
        """Point a subscriber at the session whose deltas it wants."""
        if queue in self._subscribers:
            self._subscribers[queue] = session_id

    def snapshot(self, session_id: str | None = None) -> dict[str, Any]:
        session = (
            self._sessions.get(session_id) if session_id else self.active_session
        )
        if session is None:
            return {"active": False}
        self._prune_project_setup_actions(session)
        return {
            **_session_meta(session),
            "active": True,
            "transcript_tail": [
                m.as_dict() for m in session.transcript[-SNAPSHOT_TAIL:]
            ],
            "project_setup_actions": [
                action.as_dict() for action in session.project_setup_actions.values()
            ],
        }

    def list_sessions(self) -> dict[str, Any]:
        """Live sessions plus creation choices and resumable sessions on disk."""
        cfg = self._config_provider()
        live = [_session_meta(s) for s in self._sessions.values()]
        resumable = [
            entry
            for entry in self._read_index()
            if entry["session_id"] not in self._sessions
        ]
        return {
            "active_id": self._active_id,
            "max_sessions": MAX_SESSIONS,
            "default_agent_kind": cfg.agent.kind,
            "supported_agent_kinds": sorted(SUPPORTED_AGENT_KINDS),
            "sessions": live,
            "resumable": resumable,
        }

    # ------------------------------------------------------------------
    # server-owned project setup choices
    # ------------------------------------------------------------------

    def _close_project_setup_choice_windows(
        self, session: ChatSession, *, reason: str
    ) -> None:
        """Stop treating bare numbers as project confirmations."""

        for action in session.project_setup_actions.values():
            if not action.choice_active:
                continue
            action.choice_active = False
            self._broadcast(
                session,
                "project_setup_status",
                f"numeric selection closed for {action.name} after {reason}",
                meta={"project_setup": action.as_dict()},
            )

    def _expire_project_setup_actions(self, session: ChatSession) -> None:
        """Make passive expiry visible without trusting persisted action rows."""

        for action in session.project_setup_actions.values():
            if action.status in {"pending", "failed"} and _project_setup_is_expired(
                action.expires_at
            ):
                action.status = "expired"
                action.choice_active = False

    def _prune_project_setup_actions(
        self, session: ChatSession, *, reserve: int = 0
    ) -> list[str]:
        """Bound live action state without evicting live confirmations."""

        self._expire_project_setup_actions(session)
        removed: list[str] = []
        target = max(_MAX_PROJECT_ACTIONS_PER_SESSION - reserve, 0)
        while len(session.project_setup_actions) > target:
            stale_id = next(
                (
                    action_id
                    for action_id, action in session.project_setup_actions.items()
                    if action.status in {"succeeded", "expired"}
                ),
                None,
            )
            if stale_id is None:
                # Pending/running cards remain an explicit operator choice.
                # A new proposal is refused until one is resolved or expires.
                return removed
            del session.project_setup_actions[stale_id]
            removed.append(stale_id)
        return removed

    def project_setup_for_choice(
        self, choice_text: str, session_id: str | None = None
    ) -> ProjectSetupAction | None:
        """Return the active server-issued action selected by a bare number."""

        session = self._resolve(session_id)
        if session.mode != "edit":
            return None
        self._expire_project_setup_actions(session)
        try:
            choice = int(choice_text)
        except (TypeError, ValueError):
            return None
        if str(choice) != choice_text.strip():
            return None
        matches = [
            action
            for action in session.project_setup_actions.values()
            if (
                action.choice == choice
                and action.choice_active
                and action.status in {"pending", "failed"}
                and not _project_setup_is_expired(action.expires_at)
            )
        ]
        return matches[0] if len(matches) == 1 else None

    async def confirm_project_setup(
        self,
        action_id: str,
        session_id: str | None = None,
        confirmation_token: str | None = None,
    ) -> dict[str, Any]:
        """Create/adopt a proposed project once the operator selects it.

        The backing task is shielded from a disconnected HTTP request: project
        setup may have already committed a Git repository, so abandoning it
        midway would leave the transcript falsely pending.
        """

        session = self._resolve(session_id)
        action = session.project_setup_actions.get(action_id)
        if action is None:
            raise ChatProjectActionError(f"unknown project setup action {action_id!r}")
        if session.mode != "edit":
            raise ChatProjectActionError("project setup requires an edit-mode session")
        token_hash = _confirmation_token_hash(confirmation_token)
        if (
            session.confirmation_token_hash is None
            or token_hash is None
            or not hmac.compare_digest(session.confirmation_token_hash, token_hash)
        ):
            raise ChatProjectAuthorizationError(
                "project setup requires confirmation from its originating browser"
            )
        if action.status == "succeeded":
            return action.as_dict()
        # The first confirmation established authority before expiry. A retry
        # racing that in-flight task must await its exact outcome rather than
        # relabeling a potentially committed setup as expired.
        if action.task is not None:
            await asyncio.shield(action.task)
            return action.as_dict()
        if action.status == "expired" or _project_setup_is_expired(action.expires_at):
            action.status = "expired"
            action.choice_active = False
            self._broadcast(
                session,
                "project_setup_expired",
                f"project setup expired for {action.name}",
                meta={"project_setup": action.as_dict()},
            )
            raise ChatProjectActionError("project setup action has expired")
        action.status = "running"
        action.choice_active = False
        action.error = None
        self._broadcast(
            session,
            "project_setup_status",
            f"creating and registering {action.name}",
            meta={"project_setup": action.as_dict()},
        )
        action.task = asyncio.create_task(
            self._run_project_setup(session, action),
            name=f"symphony-chat-project-setup-{action.action_id}",
        )
        await asyncio.shield(action.task)
        return action.as_dict()

    async def _run_project_setup(
        self, session: ChatSession, action: ProjectSetupAction
    ) -> None:
        try:
            def create_checked() -> Any:
                expectation = action.target_expectation
                if expectation is None:
                    raise ChatProjectActionError(
                        "project setup target binding is unavailable; request a new proposal"
                    )
                target = _project_setup_target_at_confirmation(expectation)
                return self._project_creator(
                    action.name, target, expected_target=expectation
                )

            project = await asyncio.to_thread(create_checked)
        except Exception as exc:
            action.status = "failed"
            action.error = _project_setup_error(exc)
            log.warning(
                "chat_project_setup_failed",
                action_id=action.action_id,
                path=action.path,
                error=action.error,
            )
            self._broadcast(
                session,
                "project_setup_failed",
                f"could not create or register {action.name}",
                meta={"project_setup": action.as_dict()},
            )
        else:
            action.status = "succeeded"
            action.choice_active = False
            action.project = self._project_setup_payload(project)
            self._broadcast(
                session,
                "project_setup_completed",
                f"created and registered {action.name}",
                meta={"project_setup": action.as_dict()},
            )
        finally:
            action.task = None
            self._save_index()

    @staticmethod
    def _project_setup_payload(project: Any) -> dict[str, Any]:
        """Return the non-secret project facts a Chat confirmation may show."""

        return {
            "id": str(getattr(project, "id", "")),
            "name": str(getattr(project, "name", "")),
            "repo_path": str(getattr(project, "git_repo", "")),
            "workflow_path": str(getattr(project, "workflow", "")),
            "host": str(getattr(project, "host", "")),
            "port": getattr(project, "port", None),
        }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _resolve(self, session_id: str | None) -> ChatSession:
        if session_id is None:
            if self.active_session is None:
                raise ChatNoSessionError("no active chat session")
            return self.active_session
        session = self._sessions.get(session_id)
        if session is None:
            raise ChatNoSessionError(f"no live chat session {session_id!r}")
        return session

    def _record_agent_message(
        self,
        session: ChatSession,
        text: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Persist visible model text and any strict server-owned action."""

        visible = text
        proposal: ProjectSetupAction | None = None
        if session.mode == "edit":
            visible, proposal = _project_setup_spec(text)
        removed: list[str] = []
        if proposal is not None and proposal.action_id not in session.project_setup_actions:
            self._expire_project_setup_actions(session)
            duplicate_choice = any(
                action.choice == proposal.choice
                and (
                    (action.status == "pending" and action.choice_active)
                    or action.status == "running"
                )
                for action in session.project_setup_actions.values()
            )
            if duplicate_choice:
                proposal = None
                visible = (
                    f"{visible}\n\n"
                    "Project setup could not be safely prepared: that option number "
                    "is already in use."
                ).strip()
            else:
                removed = self._prune_project_setup_actions(session, reserve=1)
                if (
                    session.confirmation_token_hash is not None
                    and len(session.project_setup_actions)
                    < _MAX_PROJECT_ACTIONS_PER_SESSION
                ):
                    session.project_setup_actions[proposal.action_id] = proposal
                else:
                    proposal = None
                    unavailable = "Project setup could not be safely prepared."
                    visible = f"{visible}\n\n{unavailable}".strip()
        # The explanation must precede its control in the live stream just as
        # it does in the model response and accessibility reading order.
        if visible:
            self._broadcast(session, "agent_message", visible, meta=meta)
        for action_id in removed:
            self._broadcast(
                session,
                "project_setup_removed",
                "",
                meta={"project_setup_action_id": action_id},
            )
        if proposal is not None:
            self._broadcast(
                session,
                "project_setup_action",
                "",
                meta={"project_setup": proposal.as_dict()},
            )
        # Keep the raw backend message for terminal-event de-duplication. The
        # visible string intentionally has the protocol marker removed.
        session.last_agent_text = text

    def _forget_live(self, session: ChatSession) -> None:
        """Drop a session from the live map and release its writer."""
        self._sessions.pop(session.session_id, None)
        if session.writer is not None:
            session.writer.close()
            session.writer = None
        if self._active_id == session.session_id:
            self._active_id = next(reversed(self._sessions), None)

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
        # The outgoing backend's usage counter dies with it; freeze what it
        # reported so the next one's cumulative totals add on top.
        session.token_base = session.used_tokens
        workflow_dir = cfg.workflow_path.parent
        backend = build_backend(
            BackendInit(
                cfg=mode_cfg,
                cwd=workflow_dir,
                workspace_root=workflow_dir,
                # Bound per session: with several backends alive at once the
                # callback cannot look up "the" current session.
                on_event=partial(self._on_backend_event, session),
            )
        )
        await backend.start()
        await backend.initialize()
        await backend.start_session(initial_prompt="", issue_title=None)
        session.backend = backend

    async def _run_turn(self, session: ChatSession, prompt: str) -> None:
        async with session.turn_lock:
            backend = session.backend
            if backend is None:
                self._broadcast(
                    session, "turn_failed", "no backend for session", meta={}
                )
                return
            is_first = session.backend_turns == 0
            session.turn_failure_broadcast = False
            try:
                await backend.run_turn(
                    prompt=prompt, is_continuation=not is_first
                )
            except asyncio.CancelledError:
                raise
            except SymphonyError as exc:
                if not session.turn_failure_broadcast:
                    self._broadcast(
                        session,
                        "turn_failed",
                        f"{exc.code}: {exc.message}",
                        meta={"code": exc.code},
                    )
                log.warning("chat_turn_failed", code=exc.code, error=exc.message)
            except Exception as exc:
                if not session.turn_failure_broadcast:
                    self._broadcast(session, "turn_failed", str(exc), meta={})
                log.warning("chat_turn_failed", error=str(exc))
            finally:
                session.backend_turns += 1
                # Backends that never emit `session_started` (or emit it
                # only once) still expose the id a reattach needs.
                agent_session_id = backend.session_id
                if (
                    isinstance(agent_session_id, str)
                    and agent_session_id
                    and agent_session_id != "pending"
                ):
                    session.agent_session_id = agent_session_id
                self._warn_if_over_budget(session)
                self._save_index()
                if self._request_refresh is not None:
                    try:
                        self._request_refresh()
                    except Exception as exc:
                        log.warning("chat_refresh_failed", error=str(exc))

    def _warn_if_over_budget(self, session: ChatSession) -> None:
        """Advisory only: warn once per crossing, never block the next turn."""
        if not session.budget_exceeded():
            session.budget_warned = False
            return
        if session.budget_warned:
            return
        session.budget_warned = True
        self._broadcast(
            session,
            "session_status",
            f"chat budget reached — {session.budget_reason()}; "
            "the session keeps running, stop it when you are done",
            meta={"budget": session.budget()},
        )

    async def _on_backend_event(
        self, session: ChatSession, envelope: dict[str, Any]
    ) -> None:
        if self._sessions.get(session.session_id) is not session:
            return  # the session was stopped while its backend drained
        event = envelope.get("event")
        payload = envelope.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if event == EVENT_TURN_STARTED:
            self._broadcast(
                session,
                "turn_started",
                "",
                meta={"agent_pid": envelope.get("agent_pid")},
            )
        elif event == EVENT_SESSION_STARTED:
            agent_session_id = payload.get("session_id")
            if isinstance(agent_session_id, str) and agent_session_id:
                # This is what a later reattach resumes from.
                session.agent_session_id = agent_session_id
                self._save_index()
            self._broadcast(
                session,
                "session_status",
                "",
                meta={"agent_session_id": agent_session_id},
            )
        elif event == EVENT_TURN_COMPLETED:
            usage = envelope.get("usage") or {}
            self._accumulate_usage(session, usage)
            message = _terminal_agent_message(payload)
            if message and message != session.last_agent_text:
                self._record_agent_message(session, message)
            self._broadcast(
                session,
                "turn_completed",
                "",
                meta={"usage": usage, "budget": session.budget()},
            )
        elif event == EVENT_TURN_FAILED:
            session.turn_failure_broadcast = True
            reason = str(
                payload.get("reason") or payload.get("error") or "turn failed"
            )
            self._broadcast(session, "turn_failed", reason, meta={})
        elif event == EVENT_OTHER_MESSAGE:
            for type_, text, meta in _summarize_frame(session.agent_kind, payload):
                if type_ in EPHEMERAL_TYPES:
                    self._broadcast_ephemeral(session, type_, text, meta)
                    continue
                if type_ == "agent_message":
                    self._record_agent_message(session, text, meta)
                    continue
                self._broadcast(session, type_, text, meta=meta)
        # remaining events (malformed, notifications, approvals) stay internal

    @staticmethod
    def _accumulate_usage(session: ChatSession, usage: dict[str, Any]) -> None:
        """Backends report their own usage cumulatively — add on the base."""
        try:
            total = int(usage.get("total_tokens") or 0)
        except (TypeError, ValueError):
            return
        session.used_tokens = session.token_base + max(total, 0)

    def _broadcast(
        self,
        session: ChatSession,
        type_: str,
        text: str,
        meta: dict[str, Any] | None = None,
    ) -> ChatMessage:
        session.seq += 1
        session.updated_at = _utc_iso()
        msg = ChatMessage(
            seq=session.seq,
            type=type_,
            text=text,
            timestamp=session.updated_at,
            meta=meta or {},
        )
        session.transcript.append(msg)
        if len(session.transcript) > TRANSCRIPT_LIMIT:
            del session.transcript[: len(session.transcript) - TRANSCRIPT_LIMIT]
        row = msg.as_dict()
        if session.writer is not None:
            session.writer.append(row)
        self._push({**row, "session_id": session.session_id})
        return msg

    def _broadcast_ephemeral(
        self,
        session: ChatSession,
        type_: str,
        text: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Live-only frame: no seq, no transcript row, no JSONL line.

        Token deltas arrive by the hundred per turn. Numbering them would
        evict the whole transcript (`TRANSCRIPT_LIMIT`) and bloat the JSONL
        with text the terminal `agent_message` already repeats verbatim. A
        client that reconnects mid-turn simply misses the typing animation
        and still receives the finished message. Only sockets focused on
        this session get them, so a background session cannot flood a
        bounded queue and push real messages out of it.
        """
        self._push(
            {
                "seq": None,
                "type": type_,
                "text": text,
                "timestamp": _utc_iso(),
                "meta": meta or {},
                "session_id": session.session_id,
            },
            focused_only=True,
        )

    def _push(self, row: dict[str, Any], focused_only: bool = False) -> None:
        session_id = row.get("session_id")
        for queue, focus in list(self._subscribers.items()):
            # An unfocused subscriber (a client that predates multi-session)
            # follows whichever session is active.
            wanted = focus if focus is not None else self._active_id
            if focused_only and wanted != session_id:
                continue
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

    # ------------------------------------------------------------------
    # on-disk session index
    # ------------------------------------------------------------------

    def _chat_dir(self) -> Path:
        return self._config_provider().workflow_path.parent / ".symphony" / "chat"

    def _transcript_path(self, session_id: str) -> Path:
        return self._chat_dir() / f"{session_id}.jsonl"

    def _read_index(self) -> list[dict[str, Any]]:
        path = self._chat_dir() / _INDEX_NAME
        try:
            if path.stat().st_size > _MAX_INDEX_BYTES:
                return []
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, RecursionError, ValueError, json.JSONDecodeError):
            return []
        rows = data.get("sessions") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
        return [
            row
            for row in rows
            if (
                isinstance(row, dict)
                and isinstance(row.get("session_id"), str)
                and _SESSION_ID_RE.fullmatch(row["session_id"])
            )
        ]

    def _find_index_entry(self, session_id: str) -> dict[str, Any] | None:
        for entry in self._read_index():
            if entry["session_id"] == session_id:
                return entry
        return None

    def _save_index(self) -> None:
        """Mirror the live sessions into the index (never fatal on I/O error)."""
        entries = {row["session_id"]: row for row in self._read_index()}
        for session in self._sessions.values():
            entries[session.session_id] = _index_entry(session)
        self._write_index(entries.values())

    def _drop_index_entry(self, session_id: str) -> None:
        entries = [
            row for row in self._read_index() if row["session_id"] != session_id
        ]
        self._write_index(entries)

    def _write_index(self, entries: Any) -> None:
        rows = sorted(
            entries,
            key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
            reverse=True,
        )[:MAX_INDEX_ENTRIES]
        path = self._chat_dir() / _INDEX_NAME
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(
                    {"version": INDEX_VERSION, "sessions": rows}, ensure_ascii=False
                ),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("chat_index_write_failed", path=str(path), error=str(exc))


def _check_mode(raw: str) -> str:
    mode = (raw or "").strip().lower()
    if mode not in CHAT_MODES:
        raise SymphonyError(f"mode must be one of {', '.join(CHAT_MODES)}")
    return mode


def _as_int(raw: Any, default: int = 0) -> int:
    if isinstance(raw, bool):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if 0 <= value <= _MAX_JSON_SAFE_INTEGER else default


def _session_meta(session: ChatSession) -> dict[str, Any]:
    """Everything about a session except its (potentially large) transcript."""
    return {
        "session_id": session.session_id,
        "mode": session.mode,
        "agent_kind": session.agent_kind,
        "mode_enforced": session.mode_enforced,
        "busy": session.turn_lock.locked(),
        "turn_count": session.turn_count,
        "created_at": session.created_at,
        "updated_at": session.updated_at or session.created_at,
        "title": session.title,
        "budget": session.budget(),
    }


def _index_entry(session: ChatSession) -> dict[str, Any]:
    """The persisted shape — enough to rebuild and resume the session."""
    return {
        "session_id": session.session_id,
        "mode": session.mode,
        "agent_kind": session.agent_kind,
        "agent_session_id": session.agent_session_id,
        "turn_count": session.turn_count,
        "used_tokens": session.used_tokens,
        "max_turns": session.max_turns,
        "max_tokens": session.max_tokens,
        "created_at": session.created_at,
        "updated_at": session.updated_at or session.created_at,
        "title": session.title,
    }


def _load_transcript(path: Path) -> list[ChatMessage]:
    """Replay the tail of a session's JSONL for a reattach.

    Only the last `_REPLAY_TAIL_BYTES` are parsed: a long conversation's
    transcript can be tens of megabytes and the UI only ever shows the tail.
    Rows without a sequence number were never persisted, so anything
    unreadable is simply skipped.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > _REPLAY_TAIL_BYTES:
                fh.seek(size - _REPLAY_TAIL_BYTES)
                fh.readline()  # discard the partial line the seek landed in
            blob = fh.read()
    except OSError:
        return []
    messages: list[ChatMessage] = []
    for line in blob.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, RecursionError, ValueError):
            continue
        seq = row.get("seq") if isinstance(row, dict) else None
        if (
            not isinstance(seq, int)
            or isinstance(seq, bool)
            or not 0 <= seq <= _MAX_JSON_SAFE_INTEGER
        ):
            continue
        meta = row.get("meta")
        messages.append(
            ChatMessage(
                seq=seq,
                type=str(row.get("type") or ""),
                text=str(row.get("text") or ""),
                timestamp=str(row.get("timestamp") or ""),
                meta=meta if isinstance(meta, dict) else {},
            )
        )
    return messages[-TRANSCRIPT_LIMIT:]


# ---------------------------------------------------------------------------
# backend frame -> chat message summarization
# ---------------------------------------------------------------------------


def _assistant_text(message: object) -> str:
    """Return visible assistant text without exposing thinking blocks."""
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "".join(parts).strip()
        if text:
            return text
    text = message.get("text")
    return text.strip() if isinstance(text, str) else ""


def _terminal_agent_message(payload: dict[str, Any]) -> str:
    """Normalize terminal answer shapes used by every chat backend."""
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    for candidate in reversed(messages):
        text = _assistant_text(candidate)
        if text:
            return text
    return ""


def _summarize_frame(
    agent_kind: str, payload: dict[str, Any]
) -> list[tuple[str, str, dict[str, Any]]]:
    if agent_kind == "claude":
        return _summarize_claude_frame(payload)
    if agent_kind == "codex":
        return _summarize_codex_frame(payload)
    if agent_kind in {"pi", "prime-agent"}:
        return _summarize_pi_frame(payload)
    raw = _preview(json.dumps(payload, ensure_ascii=False), _RAW_PREVIEW_CHARS)
    return [("tool_activity", "event", {"detail": raw})] if raw != "{}" else []


def _summarize_claude_frame(
    payload: dict[str, Any]
) -> list[tuple[str, str, dict[str, Any]]]:
    """stream-json `stream_event` / `assistant` / `user` frames -> messages."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    kind = payload.get("type")
    if kind == "stream_event":
        return _claude_text_delta(payload)
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


def _claude_text_delta(
    payload: dict[str, Any]
) -> list[tuple[str, str, dict[str, Any]]]:
    """`--include-partial-messages` deltas -> ephemeral typing chunks.

    The flag is already part of `DEFAULT_CLAUDE_COMMAND`, so these frames
    reach chat without touching the backend; before this they were parsed as
    an `assistant` frame, found no content list and were dropped. Only
    `text_delta` is streamed: `thinking_delta` and `input_json_delta` would
    leak reasoning and half-built tool arguments into the bubble.

      {"type":"stream_event","event":{"type":"content_block_delta",
       "index":0,"delta":{"type":"text_delta","text":"He"}}}
    """
    event = payload.get("event")
    if not isinstance(event, dict) or event.get("type") != "content_block_delta":
        return []
    delta = event.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return []
    text = delta.get("text")
    if not isinstance(text, str) or not text:
        return []
    return [("agent_delta", text, {"index": event.get("index")})]


def _summarize_pi_frame(
    payload: dict[str, Any],
) -> list[tuple[str, str, dict[str, Any]]]:
    """Pi/Prime Agent cumulative JSON events -> safe chat frames.

    `message_update` repeats the full assistant message so it is a snapshot,
    not a delta. Thinking blocks and lifecycle echoes intentionally stay out
    of both the UI and the persisted transcript.
    """
    kind = payload.get("type")
    if kind in {"message_update", "message_end"}:
        text = _assistant_text(payload.get("message"))
        if not text:
            return []
        type_ = "agent_snapshot" if kind == "message_update" else "agent_message"
        return [(type_, text, {})]
    if kind == "tool_execution_start":
        name = str(payload.get("toolName") or payload.get("tool") or "tool")
        detail = _preview(
            json.dumps(payload.get("args") or {}, ensure_ascii=False),
            _TOOL_PREVIEW_CHARS,
        )
        return [("tool_activity", name, {"detail": detail})]
    if kind == "tool_execution_end":
        name = str(payload.get("toolName") or payload.get("tool") or "tool")
        detail = _preview(str(payload.get("result") or ""), _TOOL_PREVIEW_CHARS)
        return [("tool_activity", f"{name} result", {"detail": detail})]
    return []


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
    """Codex agent deltas and `item/completed` echoes -> chat messages."""
    if payload.get("type") == "agent_delta":
        text = payload.get("text")
        if isinstance(text, str) and text:
            return [("agent_delta", text, {"item_id": payload.get("item_id")})]
        return []
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
