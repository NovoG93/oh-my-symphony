"""Tests for the operator chat session manager (`symphony.chat`)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from symphony import chat as chat_module
from symphony.backends import (
    EVENT_OTHER_MESSAGE,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_STARTED,
    BackendInit,
    TurnResult,
)
from symphony.chat import (
    ChatManager,
    _claude_command_for_mode,
    cfg_for_mode,
)
from symphony.errors import (
    ChatBusyError,
    ChatNoSessionError,
    ChatSessionExistsError,
    TurnTimeout,
)
from symphony.workflow import ServiceConfig, WorkflowState

CLAUDE_COMMAND = (
    "claude -p --output-format stream-json --verbose "
    '--permission-mode acceptEdits --add-dir "$SYMPHONY_WORKFLOW_DIR"'
)

WORKFLOW_TEXT = f"""---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, Doing]
  terminal_states: [Done, Archive]

agent:
  kind: claude

claude:
  command: '{CLAUDE_COMMAND}'
---

You are working on {{{{ issue.identifier }}}}.
"""


def _cfg(tmp_path: Path) -> ServiceConfig:
    (tmp_path / "WORKFLOW.md").write_text(WORKFLOW_TEXT, encoding="utf-8")
    (tmp_path / "kanban").mkdir(exist_ok=True)
    state = WorkflowState(tmp_path / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    return cfg


class _FakeBackend:
    def __init__(self, init: BackendInit) -> None:
        self.init = init
        self.turns: list[tuple[str, bool]] = []
        self.stopped = False
        self.gate: asyncio.Event | None = None
        self.raise_on_turn: Exception | None = None
        self._session_id: str | None = None

    async def start(self) -> None:
        pass

    async def initialize(self) -> dict[str, Any]:
        return {}

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
        return "pending"

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> TurnResult:
        self.turns.append((prompt, is_continuation))
        if self.gate is not None:
            await self.gate.wait()
        if self.raise_on_turn is not None:
            raise self.raise_on_turn
        self._session_id = "sess-1"
        await self._emit(EVENT_TURN_STARTED, {})
        await self._emit(
            EVENT_OTHER_MESSAGE,
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"f": "a.py"}},
                        {"type": "text", "text": "reading the file"},
                    ]
                },
            },
        )
        await self._emit(
            EVENT_TURN_COMPLETED, {"message": f"answer {len(self.turns)}"}
        )
        return TurnResult(
            status=EVENT_TURN_COMPLETED, turn_id="t", last_message="answer"
        )

    async def stop(self) -> None:
        self.stopped = True

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        await self.init.on_event(
            {
                "event": event,
                "timestamp": "2026-08-06T00:00:00Z",
                "payload": payload,
                "usage": {"total_tokens": 7},
                "rate_limits": None,
                "agent_pid": 123,
            }
        )

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def pid(self) -> int | None:
        return 123

    @property
    def latest_usage(self) -> dict[str, int]:
        return {"total_tokens": 7}

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None:
        return None

    def is_progress_event(self, event: dict[str, Any]) -> bool:
        return True


@pytest.fixture()
def fake_backends(monkeypatch: pytest.MonkeyPatch) -> list[_FakeBackend]:
    built: list[_FakeBackend] = []

    def _build(init: BackendInit) -> _FakeBackend:
        backend = _FakeBackend(init)
        built.append(backend)
        return backend

    monkeypatch.setattr(chat_module, "build_backend", _build)
    return built


async def _wait_turn(manager: ChatManager) -> None:
    task = manager._turn_task
    assert task is not None
    await task


# ---------------------------------------------------------------------------
# session lifecycle
# ---------------------------------------------------------------------------


async def test_start_session_rejects_duplicates_and_stop_clears(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    snapshot = await manager.start_session("qa")
    assert snapshot["active"] is True
    assert snapshot["mode"] == "qa"
    assert snapshot["agent_kind"] == "claude"
    assert snapshot["mode_enforced"] is True

    with pytest.raises(ChatSessionExistsError):
        await manager.start_session("qa")

    await manager.stop_session()
    assert manager.snapshot() == {"active": False}
    assert fake_backends[0].stopped is True
    with pytest.raises(ChatNoSessionError):
        await manager.stop_session()


async def test_backend_runs_in_workflow_dir_with_mode_command(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    init = fake_backends[0].init
    assert init.cwd == tmp_path
    assert init.workspace_root == tmp_path
    assert "--permission-mode plan" in init.cfg.claude.command
    assert "acceptEdits" not in init.cfg.claude.command
    await manager.stop_session()


async def test_send_message_preamble_and_continuation(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    backend = fake_backends[0]

    await manager.send_message("what does this repo do?")
    await _wait_turn(manager)
    prompt, is_continuation = backend.turns[0]
    assert prompt.endswith("what does this repo do?")
    assert "do not create, modify or delete" in prompt
    assert is_continuation is False

    await manager.send_message("second question")
    await _wait_turn(manager)
    prompt, is_continuation = backend.turns[1]
    assert prompt == "second question"
    assert is_continuation is True

    types = [m.type for m in manager._session.transcript]  # type: ignore[union-attr]
    assert "user_message" in types
    assert "turn_started" in types
    assert "agent_message" in types
    assert "tool_activity" in types
    assert "turn_completed" in types
    await manager.stop_session()


async def test_send_while_busy_raises(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    backend = fake_backends[0]
    backend.gate = asyncio.Event()

    await manager.send_message("slow one")
    await asyncio.sleep(0)  # let the turn task grab the lock
    assert manager.snapshot()["busy"] is True
    with pytest.raises(ChatBusyError):
        await manager.send_message("too soon")
    backend.gate.set()
    await _wait_turn(manager)
    await manager.stop_session()


async def test_turn_failure_is_broadcast(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    fake_backends[0].raise_on_turn = TurnTimeout("turn timed out")

    await manager.send_message("hello")
    await _wait_turn(manager)
    session = manager._session
    assert session is not None
    failed = [m for m in session.transcript if m.type == "turn_failed"]
    assert failed and "turn_timeout" in failed[0].text
    # Session survives a failed turn.
    assert manager.snapshot()["active"] is True
    await manager.stop_session()


async def test_set_mode_rebuilds_with_resume_and_resets_continuation(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    await manager.send_message("q1")
    await _wait_turn(manager)

    result = await manager.set_mode("edit")
    assert result["mode"] == "edit"
    assert result["context_preserved"] is True
    assert fake_backends[0].stopped is True
    new_backend = fake_backends[1]
    assert "--permission-mode acceptEdits" in new_backend.init.cfg.claude.command
    assert "--resume sess-1" in new_backend.init.cfg.claude.command

    await manager.send_message("now edit something")
    await _wait_turn(manager)
    _, is_continuation = new_backend.turns[0]
    assert is_continuation is False  # fresh backend instance
    await manager.stop_session()


async def test_subscribers_receive_fanout_and_close_sends_sentinel(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    await manager.start_session("qa")
    q1 = manager.subscribe()
    q2 = manager.subscribe()

    await manager.send_message("ping")
    await _wait_turn(manager)
    first = q1.get_nowait()
    assert first["type"] == "user_message"
    assert first["text"] == "ping"
    assert q2.get_nowait()["type"] == "user_message"

    await manager.close()
    # Drain until the shutdown sentinel arrives.
    sentinel_seen = False
    while not q1.empty():
        if q1.get_nowait() is None:
            sentinel_seen = True
    assert sentinel_seen


async def test_transcript_jsonl_written(
    tmp_path: Path, fake_backends: list[_FakeBackend]
) -> None:
    cfg = _cfg(tmp_path)
    manager = ChatManager(lambda: cfg)
    snapshot = await manager.start_session("qa")
    await manager.send_message("persist me")
    await _wait_turn(manager)

    path = (
        tmp_path / ".symphony" / "chat" / f"{snapshot['session_id']}.jsonl"
    )
    for _ in range(50):
        if path.exists() and "persist me" in path.read_text(encoding="utf-8"):
            break
        await asyncio.sleep(0.05)
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert any(r["type"] == "user_message" and r["text"] == "persist me" for r in rows)
    await manager.stop_session()


# ---------------------------------------------------------------------------
# mode config derivation
# ---------------------------------------------------------------------------


def test_claude_command_for_mode_strips_and_appends() -> None:
    qa = _claude_command_for_mode(CLAUDE_COMMAND, "qa")
    assert qa.count("--permission-mode") == 1
    assert "--permission-mode plan" in qa
    assert "acceptEdits" not in qa
    assert '--add-dir "$SYMPHONY_WORKFLOW_DIR"' in qa

    edit = _claude_command_for_mode("claude -p", "edit")
    assert edit == "claude -p --permission-mode acceptEdits"

    eq_form = _claude_command_for_mode(
        "claude -p --permission-mode=bypassPermissions --verbose", "qa"
    )
    assert "bypassPermissions" not in eq_form
    assert "--verbose" in eq_form

    resumed = _claude_command_for_mode("claude -p", "qa", "sess-9")
    assert resumed.endswith("--resume sess-9")


def test_cfg_for_mode_per_kind(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    claude_cfg, enforced = cfg_for_mode(cfg, "qa", "claude")
    assert enforced is True
    assert "--permission-mode plan" in claude_cfg.claude.command

    codex_qa, enforced = cfg_for_mode(cfg, "qa", "codex")
    assert enforced is True
    assert codex_qa.agent.kind == "codex"
    assert codex_qa.codex.thread_sandbox == "read-only"
    assert codex_qa.codex.turn_sandbox_policy == "read-only"

    codex_edit, enforced = cfg_for_mode(cfg, "edit", "codex")
    assert enforced is True
    assert codex_edit.codex.thread_sandbox == cfg.codex.thread_sandbox

    gemini_cfg, enforced = cfg_for_mode(cfg, "qa", "gemini")
    assert enforced is False
    assert gemini_cfg.agent.kind == "gemini"
    assert gemini_cfg.gemini == cfg.gemini
