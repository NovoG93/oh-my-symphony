"""Tests for the one-shot agent node driver.

The executor's other tests stub `run_agent_node` out, so without this file
the code that actually talks to a backend would ship untested. The fake
backend here implements the `AgentBackend` protocol by duck typing, the
same way the orchestrator's existing tests do.

The property that matters most: a node's output is *data*, not a log line.
Downstream nodes substitute it via `${nodes.<id>.output}`, so anything that
truncates it silently corrupts the DAG's data flow.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from symphony.errors import TurnFailed, TurnTimeout
from symphony.flow.agent_node import MAX_RETAINED_EVENTS, run_agent_node
from symphony.flow.model import NodeDefinition
from symphony.flow import statuses as st


LONG_TEXT = "line of the plan\n" * 400  # comfortably past the 400-char clip


@dataclass
class _FakeBackend:
    """Records lifecycle calls and emits a normalized event stream."""

    init: Any
    text: str = LONG_TEXT
    pids: tuple[int | None, ...] = (4242, 4243)
    fail_with: BaseException | None = None
    hang: bool = False
    extra_events: int = 0
    calls: list[str] = field(default_factory=list)
    _session: str | None = None
    _pid: int | None = None

    @property
    def session_id(self) -> str | None:
        return self._session

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def latest_usage(self) -> dict[str, int]:
        return {"input_tokens": 111, "output_tokens": 222, "total_tokens": 333}

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None:
        return None

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        del event
        return True

    async def start(self) -> None:
        self.calls.append("start")
        self._pid = self.pids[0]

    async def initialize(self) -> dict[str, Any]:
        self.calls.append("initialize")
        return {"agent": "fake"}

    async def start_session(self, *, initial_prompt: str, issue_title: str | None) -> str:
        del initial_prompt, issue_title
        self.calls.append("start_session")
        self._session = "sess-1"
        return self._session

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> Any:
        self.calls.append(f"run_turn(is_continuation={is_continuation})")
        del prompt
        if self.hang:
            await asyncio.sleep(30)
        for index in range(self.extra_events):
            await self.init.on_event(
                {
                    "event": "other_message",
                    "payload": {"n": index},
                    "usage": self.latest_usage,
                    "agent_pid": self.pids[0],
                }
            )
        if self.fail_with is not None:
            raise self.fail_with
        self._pid = self.pids[-1]
        await self.init.on_event(
            {
                "event": "turn_completed",
                # The full text only ever exists here — `TurnResult`
                # truncates it before returning.
                "payload": {"message": self.text},
                "usage": self.latest_usage,
                "agent_pid": self.pids[-1],
            }
        )
        from symphony.backends import TurnResult

        return TurnResult(
            status="turn_completed",
            turn_id="t1",
            last_message=self.text[:400],
        )

    async def stop(self) -> None:
        self.calls.append("stop")
        self._pid = None


def _node(node_id: str = "plan") -> NodeDefinition:
    return NodeDefinition(id=node_id, type=st.NODE_TYPE_AGENT, prompt="hi")


def _run(tmp_path: Path, backend_holder: list[_FakeBackend], **kwargs: Any) -> Any:
    from symphony.workflow import build_service_config
    from symphony.workflow.parser import parse_workflow_text

    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        "---\n"
        "tracker: {kind: file, board_root: ./kanban}\n"
        "agent: {kind: codex, max_concurrent_agents: 1, max_turns: 4}\n"
        "---\n# b\n",
        encoding="utf-8",
    )
    (tmp_path / "kanban").mkdir(exist_ok=True)
    cfg = build_service_config(
        parse_workflow_text(workflow.read_text(encoding="utf-8"), source_path=workflow)
    )

    backend_kwargs = kwargs.pop("backend", {})
    pids: list[int | None] = []

    def factory(init: Any) -> _FakeBackend:
        backend = _FakeBackend(init=init, **backend_kwargs)
        backend_holder.append(backend)
        return backend

    result = asyncio.run(
        run_agent_node(
            cfg=cfg,
            node=_node(),
            prompt="do the thing",
            workspace=tmp_path,
            workspace_root=tmp_path,
            timeout_seconds=kwargs.pop("timeout_seconds", 30),
            on_pid=pids.append,
            build_backend_fn=factory,
            **kwargs,
        )
    )
    return result, pids


def test_lifecycle_runs_in_order_and_is_a_single_shot(tmp_path: Path) -> None:
    backends: list[_FakeBackend] = []
    _run(tmp_path, backends)
    assert backends[0].calls == [
        "start",
        "initialize",
        "start_session",
        # A workflow node is one shot; continuation belongs to the stage loop.
        "run_turn(is_continuation=False)",
        "stop",
    ]


def test_full_output_is_captured_not_the_truncated_preview(tmp_path: Path) -> None:
    backends: list[_FakeBackend] = []
    result, _ = _run(tmp_path, backends)
    assert result.output == LONG_TEXT
    # The value a naive implementation would have returned instead.
    assert len(result.output) > 400
    assert result.session_id == "sess-1"
    assert (result.input_tokens, result.output_tokens) == (111, 222)
    assert result.status == "turn_completed"


def test_pid_changes_are_published_for_the_lease(tmp_path: Path) -> None:
    backends: list[_FakeBackend] = []
    _, pids = _run(tmp_path, backends)
    # The caller writes these to a durable lease so a cancel can reach the
    # right process group; only changes need publishing, and the final
    # `None` says the process is gone.
    assert pids[0] == 4242
    assert 4243 in pids
    assert pids[-1] is None


def test_the_backend_is_stopped_even_when_the_turn_fails(tmp_path: Path) -> None:
    backends: list[_FakeBackend] = []
    with pytest.raises(TurnFailed):
        _run(tmp_path, backends, backend={"fail_with": TurnFailed("model exploded")})
    # A leaked agent CLI would keep writing into a workspace the executor is
    # about to hand to the next node.
    assert backends[0].calls[-1] == "stop"


def test_turn_errors_propagate_unclassified(tmp_path: Path) -> None:
    """Classification belongs to `retries.classify_failure`, not here."""
    backends: list[_FakeBackend] = []
    with pytest.raises(TurnFailed) as excinfo:
        _run(tmp_path, backends, backend={"fail_with": TurnFailed("429 rate limit")})
    assert "429" in str(excinfo.value)


def test_a_hung_turn_times_out_and_stops_the_backend(tmp_path: Path) -> None:
    backends: list[_FakeBackend] = []
    with pytest.raises(TurnTimeout) as excinfo:
        _run(tmp_path, backends, timeout_seconds=1, backend={"hang": True})
    assert excinfo.value.context.get("node_id") == "plan"
    assert "stop" in backends[0].calls


def test_retained_events_are_bounded(tmp_path: Path) -> None:
    backends: list[_FakeBackend] = []
    result, _ = _run(tmp_path, backends, backend={"extra_events": 500})
    # A per-token-streaming backend must not grow the in-memory node result
    # without bound.
    assert len(result.events) == MAX_RETAINED_EVENTS
    # The tail is what diagnostics need, so the final event must survive.
    assert result.events[-1]["event"] == "turn_completed"


def test_caller_supplied_on_event_still_sees_every_event(tmp_path: Path) -> None:
    backends: list[_FakeBackend] = []
    seen: list[str] = []

    async def observer(event: dict[str, Any]) -> None:
        seen.append(str(event.get("event")))

    _run(tmp_path, backends, backend={"extra_events": 5}, on_event=observer)
    # Forwarding is not subject to the retention cap — the ring buffer bounds
    # what is kept, not what is delivered.
    assert len(seen) == 6
    assert seen[-1] == "turn_completed"
