"""Drive exactly one backend turn for one governed-workflow agent node.

This is deliberately *not* the multi-turn stage loop in
`orchestrator/core.py`. A governed workflow node is one shot: prompt in,
full text out (PRD §8.3). The stage loop's job — deciding whether the
agent should keep going, rewinding phases, rebuilding backends between
kanban states — belongs to the executor and the DAG, not to a node.

What this module owns:

- the fixed backend lifecycle (`start` -> `initialize` -> `start_session`
  -> `run_turn` -> `stop`), with `stop()` in a `finally` so a crashed or
  cancelled node never leaks an agent CLI into a workspace the executor
  is about to hand to the next node;
- capturing the *full* assistant text rather than the truncated preview;
- keeping the caller's record of process ownership current through
  `on_pid`, so the run's process lease can terminate the right process
  group at any moment.

Errors are NOT classified here. `TurnTimeout` / `TurnFailed` /
`TurnCancelled` / `TurnInputRequired` propagate untouched so
`flow/retries.py:classify_failure` — the single place that decides retry
behaviour — sees the real exception type.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..backends import AgentBackend, BackendInit, TurnResult, build_backend
from ..errors import TurnTimeout
from ..logging import get_logger
from ..workflow import ServiceConfig
from .model import NodeDefinition


log = get_logger()

# Ring-buffer bound on retained events. A chatty backend (opencode streams
# per-token frames; pi emits retry/compaction signals) can produce tens of
# thousands of events in one turn, and the executor only ever uses the tail
# for diagnostics. Retaining them all would grow the node result — which is
# held in memory until the artifact writer persists it — without bound.
MAX_RETAINED_EVENTS = 200

# Payload keys that may carry the assistant's final text, in priority order.
# `message` is the cross-backend contract key (plain_cli.py, gemini.py,
# opencode.py all set it); `result`/`response` are the same string under the
# aliases those backends also publish; `text`/`summary` cover codex, whose
# `turn_completed` payload is the raw JSON-RPC turn object rather than a
# normalized dict. First non-empty wins.
_TEXT_KEYS: tuple[str, ...] = ("message", "result", "response", "text", "summary")


@dataclass(frozen=True)
class AgentNodeResult:
    """Everything the executor needs from one agent node attempt."""

    output: str
    session_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    status: str
    events: tuple[dict[str, Any], ...]


async def run_agent_node(
    *,
    cfg: ServiceConfig,
    node: NodeDefinition,
    prompt: str,
    workspace: Path,
    workspace_root: Path,
    timeout_seconds: int,
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_pid: Callable[[int | None], None] | None = None,
    build_backend_fn: Callable[[BackendInit], AgentBackend] | None = None,
) -> AgentNodeResult:
    """Run one turn of `cfg.agent.kind` against `prompt` in `workspace`.

    `cfg` already carries the node's resolved backend kind — backend
    precedence (node override -> ticket override -> triage -> service
    default, PRD §8.3) is resolved by the caller, so this function never
    second-guesses which backend to build.

    Raises `TurnTimeout` when the whole lifecycle exceeds
    `timeout_seconds`, and propagates every backend turn error unchanged.
    """
    factory = build_backend_fn or build_backend
    collector = _TurnCollector(on_event=on_event, on_pid=on_pid)
    backend = factory(
        BackendInit(
            cfg=cfg,
            cwd=workspace,
            workspace_root=workspace_root,
            on_event=collector.handle,
            client_tools=[],
        )
    )
    collector.bind(backend)

    try:
        turn = await asyncio.wait_for(
            _drive(backend=backend, prompt=prompt, collector=collector),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        # `wait_for` already cancelled `_drive`, which unwinds through its
        # own `finally` and stops the backend. Stop again anyway: `stop()`
        # is idempotent on every backend, and a shielded/hung inner stop
        # must not leave the child alive just because we timed out.
        await _stop_quietly(backend, collector)
        raise TurnTimeout(
            "agent node turn timed out",
            node_id=node.id,
            timeout_seconds=timeout_seconds,
        ) from exc

    usage = _safe_usage(backend)
    return AgentNodeResult(
        # `TurnResult.last_message` is only the fallback: the per-turn
        # backends truncate it to 400 characters before returning
        # (plain_cli.py:59 `last_message=stdout_text[:400]`, gemini.py:76
        # `last_message=last_message[:400]`, opencode.py:158
        # `last_message=response[:400]`). A node's output is a real
        # artifact — a plan, a review, a diff summary — and downstream
        # nodes substitute it via `${nodes.<id>.output}`, so silently
        # clipping it at 400 chars would corrupt the DAG's data flow. The
        # untruncated text is only ever available in the `turn_completed`
        # event payload, which is why the collector exists.
        output=collector.text or turn.last_message,
        session_id=_safe_session_id(backend),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        status=turn.status,
        events=collector.events,
    )


async def _drive(
    *,
    backend: AgentBackend,
    prompt: str,
    collector: "_TurnCollector",
) -> TurnResult:
    """The fixed lifecycle. `stop()` runs on every exit path."""
    try:
        await backend.start()
        collector.publish_pid()
        await backend.initialize()
        await backend.start_session(initial_prompt=prompt, issue_title=None)
        # v1: a node is one shot, so a turn is never a continuation. Nodes
        # that need prior context declare `context: continue`, which the
        # executor implements by reusing a session — not by flipping this
        # flag inside a single node's turn.
        return await backend.run_turn(prompt=prompt, is_continuation=False)
    finally:
        await _stop_quietly(backend, collector)


async def _stop_quietly(backend: AgentBackend, collector: "_TurnCollector") -> None:
    """Stop the backend and surrender pid ownership, never raising.

    A failure to stop must not mask the real turn error, but it also must
    not be silent: an unstopped backend is a live agent CLI in a workspace
    the executor believes is free.
    """
    try:
        await backend.stop()
    except Exception as exc:  # noqa: BLE001 - deliberate: see docstring
        log.warning("agent_node_stop_failed", error=str(exc))
    finally:
        collector.clear_pid()


class _TurnCollector:
    """Fan-out event sink: forwards, accumulates text, tracks the pid."""

    def __init__(
        self,
        *,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None,
        on_pid: Callable[[int | None], None] | None,
    ) -> None:
        self._on_event = on_event
        self._on_pid = on_pid
        self._backend: AgentBackend | None = None
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_RETAINED_EVENTS)
        self._chunks: list[str] = []
        self._last_pid: int | None = None
        self._pid_published = False

    def bind(self, backend: AgentBackend) -> None:
        self._backend = backend

    @property
    def text(self) -> str:
        """Full assistant text: every `turn_completed` payload, joined.

        A turn normally completes once, so this is usually a single chunk.
        Backends that emit more than one completion frame (a resumed
        stream, an auto-retry that re-completes) contribute in order
        rather than clobbering each other.
        """
        return "\n".join(self._chunks)

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._events)

    async def handle(self, event: dict[str, Any]) -> None:
        self._events.append(event)
        if event.get("event") == "turn_completed":
            chunk = _first_text(event.get("payload"))
            if chunk:
                self._chunks.append(chunk)
        # Per-turn backends spawn a fresh child for every turn and publish
        # it via `agent_pid` on `turn_started`, so the pid the caller holds
        # after `start()` is stale from that moment on. Re-publishing on
        # every event that carries one keeps the process lease pointed at
        # the group a cancel actually has to kill.
        if event.get("agent_pid") is not None:
            self._notify_pid(_coerce_pid(event.get("agent_pid")))
        # Forwarding happens last: a caller callback that raises must not
        # cost us the text or the pid update we just extracted.
        if self._on_event is not None:
            await self._on_event(event)

    def publish_pid(self) -> None:
        self._notify_pid(_coerce_pid(_safe_attr(self._backend, "pid")))

    def clear_pid(self) -> None:
        self._notify_pid(None)

    def _notify_pid(self, pid: int | None) -> None:
        if self._on_pid is None:
            return
        # Every event carries `agent_pid`, so a chatty turn would otherwise
        # re-assert the same pid hundreds of times. The caller writes this
        # to a durable process lease; only *changes* are worth a write.
        if self._pid_published and pid == self._last_pid:
            return
        self._last_pid = pid
        self._pid_published = True
        try:
            self._on_pid(pid)
        except Exception as exc:  # noqa: BLE001 - lease bookkeeping is advisory
            log.warning("agent_node_on_pid_failed", error=str(exc))


def _first_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in _TEXT_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _coerce_pid(value: object) -> int | None:
    """Mirror `orchestrator/core.py:_normalize_agent_pid` — bools are not pids."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _safe_attr(obj: object, name: str) -> object:
    """Read a backend property without letting its getter break the turn."""
    if obj is None:
        return None
    try:
        return getattr(obj, name, None)
    except Exception:  # noqa: BLE001 - a property getter is backend code
        return None


def _safe_session_id(backend: AgentBackend) -> str | None:
    value = _safe_attr(backend, "session_id")
    return value if isinstance(value, str) and value else None


def _safe_usage(backend: AgentBackend) -> dict[str, int]:
    value = _safe_attr(backend, "latest_usage")
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(v, int)}
