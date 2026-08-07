"""Agent backend abstraction.

Symphony was originally hardwired to the Codex app-server JSON-RPC protocol.
This package introduces an `AgentBackend` Protocol so the orchestrator can
drive any coding-agent CLI (Codex, Claude Code, Gemini, AGY/Antigravity, Kiro,
OpenCode, Pi) behind one interface.

Each backend owns its own subprocess lifecycle. The Codex backend keeps the
single long-running app-server connection that speaks JSON-RPC over stdio.
The Claude, Gemini, AGY, Kiro, OpenCode, and Pi backends spawn one subprocess
per turn — Claude uses `claude -p --output-format stream-json`, Gemini uses
`gemini -p` one-shot, AGY uses `agy --print -`, Kiro uses
`kiro-cli chat --no-interactive`, OpenCode uses `opencode run --format json`,
and Pi uses `pi --mode json -p ""`.

Normalized event vocabulary is shared across backends (see `events.py` style
constants below). Per-turn backends emit `turn_started` immediately after
publishing each live child so the orchestrator can replace the process-group
identifier before any CLI output. The orchestrator only consumes these
normalized event names plus an `AgentEvent`-shaped dict, so it never sees
backend-specific protocol details.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, cast, runtime_checkable

from ..errors import ConfigValidationError
from ..workflow import ServiceConfig


# Normalized event vocabulary — every backend emits these strings only.
EVENT_SESSION_STARTED = "session_started"
EVENT_STARTUP_FAILED = "startup_failed"
EVENT_TURN_STARTED = "turn_started"
EVENT_TURN_COMPLETED = "turn_completed"
EVENT_TURN_FAILED = "turn_failed"
EVENT_TURN_CANCELLED = "turn_cancelled"
EVENT_TURN_ENDED_WITH_ERROR = "turn_ended_with_error"
EVENT_TURN_INPUT_REQUIRED = "turn_input_required"
EVENT_APPROVAL_AUTO_APPROVED = "approval_auto_approved"
EVENT_APPROVAL_DENIED = "approval_denied"
EVENT_UNSUPPORTED_TOOL_CALL = "unsupported_tool_call"
EVENT_NOTIFICATION = "notification"
EVENT_OTHER_MESSAGE = "other_message"
EVENT_MALFORMED = "malformed"
# Pi-flavoured signals — backend may translate native events to these so the
# orchestrator can log/observe them with cross-backend semantics.
EVENT_COMPACTION = "compaction"           # context compaction started/ended
EVENT_AGENT_RETRY = "agent_retry"         # backend-internal auto-retry

# R7 — stream robustness knobs shared by the line-streaming backends.
# A stream that is nothing but garbage should fail with a parse error, not
# degrade into the generic "no terminal event" message; sparse bad lines
# (any valid line resets the streak) stay tolerated.
MALFORMED_LINE_LIMIT = 10
# Post-stream reap bound: a child that closes stdout but lingers used to
# hang the turn forever on an untimed safe_proc_wait.
POST_STREAM_REAP_TIMEOUT_S = 10.0


EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class TurnResult:
    status: str
    turn_id: str | None
    last_message: str = ""
    error: str | None = None


@dataclass
class ToolDescriptor:
    name: str
    description: str
    schema: dict[str, Any]


@dataclass
class BackendInit:
    """Constructor inputs every backend needs.

    Keeping construction parameter list as a dataclass keeps the factory and
    tests honest — adding a new field forces every backend to acknowledge it.
    """

    cfg: ServiceConfig
    cwd: Path
    workspace_root: Path
    on_event: EventCallback
    client_tools: list[ToolDescriptor] = field(default_factory=list)


@runtime_checkable
class AgentBackend(Protocol):
    """Lifecycle contract for a coding-agent CLI driver.

    Order of calls from the orchestrator:
        await b.start()
        await b.initialize()
        await b.start_session(initial_prompt=..., issue_title=...)
        for each turn:
            await b.run_turn(prompt=..., is_continuation=...)
        await b.stop()

    Backends MUST emit normalized events through `on_event` for at least:
    - `session_started` once a session id is known,
    - `turn_started` with `agent_pid` once each per-turn child is live,
    - `turn_completed` / `turn_failed` / `turn_cancelled` per turn outcome.

    Token + rate-limit telemetry is reported by the latest_* properties so the
    orchestrator can roll up totals without reaching into protocol payloads.
    """

    async def start(self) -> None: ...

    async def initialize(self) -> dict[str, Any]: ...

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str: ...

    async def run_turn(
        self, *, prompt: str, is_continuation: bool
    ) -> TurnResult: ...

    async def stop(self) -> None: ...

    @property
    def session_id(self) -> str | None: ...

    @property
    def pid(self) -> int | None: ...

    @property
    def latest_usage(self) -> dict[str, int]: ...

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None: ...

    def is_progress_event(self, event: dict[str, Any]) -> bool: ...


class BaseAgentBackend:
    """Shared default behaviour for concrete backends.

    Concrete backends inherit this so any future cross-cutting default
    (currently just `is_progress_event`) lives in one place rather than
    being copy-pasted into each driver.

    Backends still match the `AgentBackend` Protocol structurally — the
    base class is purely additive and does not constrain construction.
    """

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        """Return True when an event should reset the stall-progress timer.

        Default is conservative: every event counts as progress so a new
        backend doesn't accidentally trigger spurious stall cancellations.
        Backends that produce keepalive / tool-echo events between real
        model output (e.g. claude stream-json `user` frames carrying
        `tool_result` echoes) override this to filter them out — see
        `ClaudeCodeBackend.is_progress_event` for the canonical example.
        """
        del event
        return True


@dataclass(frozen=True)
class BackendCapabilities:
    """What a backend can actually do, declared rather than inferred.

    Before this existed, behavioural differences between drivers lived as
    `if kind == "claude"` checks scattered across call sites. Governed
    workflows make that untenable: a node declaring `context: continue`
    must be rejected at preflight with a precise reason, not discovered to
    misbehave at turn three.

    Every field answers a question the workflow engine asks:

    - `session_resume` — may a node use `context: continue`?
    - `process_cancel` — can a cancel actually stop the child process tree?
    - `streaming_usage` — does token usage arrive during a turn, or only at
      the end?
    - `structured_output` — can the backend be asked for parseable output?
      (Reserved for Phase 3 conditions; no backend reports this yet.)
    - `node_skills` — can per-node skill/tool sets be injected?
    - `tool_policy` — can tool access be restricted per node?
    - `enforce_read_only_workspace` — can the *process* be prevented from
      writing to the workspace? Not "does the node promise not to write".
    """

    session_resume: bool = False
    process_cancel: bool = False
    streaming_usage: bool = False
    structured_output: bool = False
    node_skills: bool = False
    tool_policy: bool = False
    enforce_read_only_workspace: bool = False


# Static half of the capability matrix — the facts that do not depend on
# configuration. `resume_across_turns` is per-backend config, so
# `capabilities_for` layers it on top.
#
# On `enforce_read_only_workspace`: every entry is False in this release,
# and that is deliberate rather than pending. Codex and Claude *can* be
# launched with sandbox/permission modes that would enforce it, but the
# workflow executor does not yet pass those flags. PRD §9.3 is explicit
# that a declaration alone is never sufficient isolation, so until the
# executor actually hands the backend a read-only mode, read nodes degrade
# to the exclusive workspace lock. Flipping a flag here without wiring the
# flag through would silently permit concurrent writers.
_STATIC_CAPABILITIES: dict[str, BackendCapabilities] = {
    "codex": BackendCapabilities(
        process_cancel=True,
        streaming_usage=True,
        tool_policy=True,
    ),
    "claude": BackendCapabilities(
        process_cancel=True,
        streaming_usage=True,
        tool_policy=True,
    ),
    # Current Gemini CLI releases expose no resume flag at all, so session
    # resume is False regardless of what config asks for.
    "gemini": BackendCapabilities(process_cancel=True),
    "agy": BackendCapabilities(process_cancel=True),
    "kiro": BackendCapabilities(process_cancel=True),
    "opencode": BackendCapabilities(process_cancel=True),
    "pi": BackendCapabilities(process_cancel=True),
}

# Backends whose session id survives across turns only when the workflow
# config enables it. Gemini is absent: it has no resume mechanism to enable.
_RESUME_CONFIG_ATTR: dict[str, str] = {
    "claude": "claude",
    "agy": "agy",
    "kiro": "kiro",
    "opencode": "opencode",
    "pi": "pi",
}


def capabilities_for(cfg: ServiceConfig, kind: str | None = None) -> BackendCapabilities:
    """Capabilities of one backend kind under the current configuration.

    `kind` defaults to the service's configured backend. Unknown kinds get
    the all-False default rather than raising: preflight reports unknown
    kinds with a better message than this function could.
    """
    resolved = (kind or cfg.agent.kind or "").strip().lower()
    static = _STATIC_CAPABILITIES.get(resolved)
    if static is None:
        return BackendCapabilities()
    if resolved == "codex":
        # The codex app server is one long-lived process that owns the
        # thread, so turns 2+ always rejoin it — there is no config toggle.
        return replace(static, session_resume=True)
    attr = _RESUME_CONFIG_ATTR.get(resolved)
    if attr is None:
        return static
    backend_cfg = getattr(cfg, attr, None)
    return replace(
        static, session_resume=bool(getattr(backend_cfg, "resume_across_turns", False))
    )


def missing_capabilities(
    required: frozenset[str], capabilities: BackendCapabilities
) -> tuple[str, ...]:
    """Required capability names this backend does not provide."""
    return tuple(
        name
        for name in sorted(required)
        if not bool(getattr(capabilities, name, False))
    )


def build_backend(init: BackendInit) -> AgentBackend:
    """Factory: pick a concrete backend by `agent.kind`.

    Each concrete backend inherits `is_progress_event` from
    `BaseAgentBackend` and structurally satisfies `AgentBackend`. Pyright
    does not always trace Protocol membership through a non-Protocol base
    class for methods that are only inherited (no override), so we `cast`
    here to declare the structural compatibility explicitly. Runtime
    duck-typing through `isinstance(..., AgentBackend)` still works
    thanks to `@runtime_checkable`.
    """
    kind = init.cfg.agent.kind
    if kind == "codex":
        from .codex import CodexAppServerBackend

        return cast(AgentBackend, CodexAppServerBackend(init))
    if kind == "claude":
        from .claude_code import ClaudeCodeBackend

        return cast(AgentBackend, ClaudeCodeBackend(init))
    if kind == "gemini":
        from .gemini import GeminiBackend

        return cast(AgentBackend, GeminiBackend(init))
    if kind == "agy":
        from .agy import AgyBackend

        return cast(AgentBackend, AgyBackend(init))
    if kind == "kiro":
        from .kiro import KiroBackend

        return cast(AgentBackend, KiroBackend(init))
    if kind == "opencode":
        from .opencode import OpenCodeBackend

        return cast(AgentBackend, OpenCodeBackend(init))
    if kind == "pi":
        from .pi import PiBackend

        return cast(AgentBackend, PiBackend(init))
    raise ConfigValidationError(
        "unknown agent.kind "
        f"{kind!r}; expected agy, codex, claude, gemini, kiro, opencode, or pi"
    )
