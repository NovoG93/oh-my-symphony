from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from symphony.chat import ChatManager, ChatSession
from symphony.errors import ChatIntentActionError, ChatIntentAuthorizationError
from symphony.intent import IntentAction, IntentProposal
from symphony.workflow.config import TrackerConfig
from symphony.webapi import _check_intent_action_id
from symphony.workflow.mutate import WorkflowMutationError


TOKEN = "t" * 64


def test_action_id_validation_is_strict() -> None:
    assert _check_intent_action_id("intent-" + "a" * 32).startswith("intent-")
    with pytest.raises(WorkflowMutationError):
        _check_intent_action_id("intent-" + "A" * 32)
    with pytest.raises(WorkflowMutationError):
        _check_intent_action_id("project-" + "a" * 32)


def _manager(tmp_path: Path) -> ChatManager:
    cfg = SimpleNamespace(
        workflow_path=tmp_path / "WORKFLOW.md",
        tracker=SimpleNamespace(kind="file", board_root=tmp_path / "kanban"),
    )
    return ChatManager(lambda: cfg)


def _session() -> ChatSession:
    return ChatSession("20260905-120000-abcdef", "edit", "claude", True, "2026-09-05T12:00:00Z")


def _action(slug: str = "fix-widget") -> IntentAction:
    return IntentAction.from_proposal(IntentProposal(slug, "Fix widget", "micro", "## Problem\nX\n## Success criteria\n- [ ] Y\n## Out of scope\nZ"))


def test_intent_reply_requires_one_unambiguous_live_action(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = _session()
    one = _action()
    session.intent_actions[one.action_id] = one
    manager._sessions[session.session_id] = session
    assert manager.intent_for_reply("approve", session.session_id) is one
    assert manager.intent_for_reply("approve fix-widget", session.session_id) is one
    second = _action("other")
    session.intent_actions[second.action_id] = second
    assert manager.intent_for_reply("approve", session.session_id) is None


def test_expired_and_superseded_actions_are_not_selectable(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = _session()
    expired = _action()
    expired.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    old = _action("old")
    old.status = "superseded"
    session.intent_actions.update({expired.action_id: expired, old.action_id: old})
    manager._sessions[session.session_id] = session
    assert manager.intent_for_reply("approve", session.session_id) is None
    assert expired.status == "expired"


def test_marker_becomes_process_local_action_and_is_removed_from_transcript(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = _session()
    from symphony.chat import _confirmation_token_hash
    session.confirmation_token_hash = _confirmation_token_hash(TOKEN)
    manager._sessions[session.session_id] = session
    marker = "Proposal prose\n<symphony-intent>" + json.dumps({
        "slug": "fix-widget",
        "title": "Fix widget",
        "track": "micro",
        "intent": "## Problem\nX\n## Success criteria\n- [ ] Y\n## Out of scope\nZ",
    }) + "</symphony-intent>"
    manager._record_agent_message(session, marker)
    assert len(session.intent_actions) == 1
    visible = next(row for row in session.transcript if row.type == "agent_message")
    assert visible.text == "Proposal prose"
    assert "symphony-intent" not in visible.text


def test_malformed_marker_never_creates_authority(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = _session()
    manager._sessions[session.session_id] = session
    manager._record_agent_message(session, "<symphony-intent>{bad}</symphony-intent>")
    assert session.intent_actions == {}
    assert "approval is unavailable" in session.transcript[-1].text.lower()


def test_new_marker_supersedes_pending_proposal(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = _session()
    from symphony.chat import _confirmation_token_hash
    session.confirmation_token_hash = _confirmation_token_hash(TOKEN)
    manager._sessions[session.session_id] = session
    first = _action("first")
    session.intent_actions[first.action_id] = first
    marker = "<symphony-intent>" + json.dumps({
        "slug": "second",
        "title": "Second",
        "track": "micro",
        "intent": "## Problem\nX\n## Success criteria\n- [ ] Y\n## Out of scope\nZ",
    }) + "</symphony-intent>"
    manager._record_agent_message(session, marker)
    assert first.status == "superseded"
    assert manager.intent_for_reply("approve", session.session_id).slug == "second"


def test_pruning_removes_terminal_actions_but_keeps_live_action(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = _session()
    live = _action("live")
    live.status = "running"
    session.intent_actions[live.action_id] = live
    for index in range(19):
        old = _action(f"old-{index}")
        old.status = "approved"
        session.intent_actions[old.action_id] = old
    manager._sessions[session.session_id] = session
    manager._record_agent_message(session, "<symphony-intent>" + json.dumps({
        "slug": "new", "title": "New", "track": "micro",
        "intent": "## Problem\nX\n## Success criteria\n- [ ] Y\n## Out of scope\nZ",
    }) + "</symphony-intent>")
    assert live.action_id in session.intent_actions
    assert len(session.intent_actions) <= 20
    assert "intent_removed" in [row.type for row in session.transcript]


@pytest.mark.asyncio
async def test_confirmation_requires_browser_capability(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = _session()
    action = _action()
    session.intent_actions[action.action_id] = action
    session.confirmation_token_hash = "not-the-token"
    manager._sessions[session.session_id] = session
    with pytest.raises(ChatIntentAuthorizationError):
        await manager.confirm_intent(action.action_id, session.session_id, confirmation_token=TOKEN)


@pytest.mark.asyncio
async def test_confirmation_files_request_and_artifact_using_current_config(tmp_path: Path) -> None:
    board = tmp_path / "kanban"
    tracker = TrackerConfig(kind="file", endpoint="", api_key="", project_slug="", active_states=("Intake",), terminal_states=("Done",), board_root=board)
    cfg = SimpleNamespace(workflow_path=tmp_path / "WORKFLOW.md", tracker=tracker)
    refreshed = 0

    def refresh() -> None:
        nonlocal refreshed
        refreshed += 1

    manager = ChatManager(lambda: cfg, request_refresh=refresh)
    session = _session()
    action = _action("current-config")
    session.intent_actions[action.action_id] = action
    from symphony.chat import _confirmation_token_hash
    session.confirmation_token_hash = _confirmation_token_hash(TOKEN)
    manager._sessions[session.session_id] = session
    result = await manager.confirm_intent(action.action_id, session.session_id, confirmation_token=TOKEN)
    assert result["status"] == "approved"
    assert (board / "REQ-1.md").is_file()
    assert (tmp_path / ".sdlc" / "work" / "current-config" / "intent.md").is_file()
    assert refreshed == 1


@pytest.mark.asyncio
async def test_concurrent_confirmation_shares_one_filing_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path)
    session = _session()
    action = _action()
    session.intent_actions[action.action_id] = action
    from symphony.chat import _confirmation_token_hash
    session.confirmation_token_hash = _confirmation_token_hash(TOKEN)
    manager._sessions[session.session_id] = session
    calls = 0

    async def fake_run(_session: ChatSession, current: IntentAction) -> None:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        current.status = "approved"
        current.ticket = {"id": "REQ-1"}

    monkeypatch.setattr(manager, "_run_intent", fake_run)
    first, second = await asyncio.gather(
        manager.confirm_intent(action.action_id, session.session_id, confirmation_token=TOKEN),
        manager.confirm_intent(action.action_id, session.session_id, confirmation_token=TOKEN),
    )
    assert calls == 1
    assert first == second
    assert first["ticket"]["id"] == "REQ-1"


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_filing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path)
    session = _session()
    action = _action()
    session.intent_actions[action.action_id] = action
    from symphony.chat import _confirmation_token_hash
    session.confirmation_token_hash = _confirmation_token_hash(TOKEN)
    manager._sessions[session.session_id] = session
    entered = asyncio.Event()
    release = asyncio.Event()

    async def fake_run(_session: ChatSession, current: IntentAction) -> None:
        entered.set()
        await release.wait()
        current.status = "approved"
        current.ticket = {"id": "REQ-2"}

    monkeypatch.setattr(manager, "_run_intent", fake_run)
    cancelled = asyncio.create_task(manager.confirm_intent(action.action_id, session.session_id, confirmation_token=TOKEN))
    await entered.wait()
    waiter = asyncio.create_task(manager.confirm_intent(action.action_id, session.session_id, confirmation_token=TOKEN))
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    release.set()
    result = await waiter
    assert result["ticket"]["id"] == "REQ-2"


@pytest.mark.asyncio
async def test_shared_failure_is_reported_to_late_waiter_and_can_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path)
    session = _session()
    action = _action()
    session.intent_actions[action.action_id] = action
    from symphony.chat import _confirmation_token_hash
    session.confirmation_token_hash = _confirmation_token_hash(TOKEN)
    manager._sessions[session.session_id] = session
    attempts = 0

    async def fake_run(_session: ChatSession, current: IntentAction) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            current.status = "failed"
            current.error = "safe failure"
        else:
            current.status = "approved"
            current.ticket = {"id": "REQ-3"}
        current.task = None

    monkeypatch.setattr(manager, "_run_intent", fake_run)
    with pytest.raises(ChatIntentActionError):
        await asyncio.gather(
            manager.confirm_intent(action.action_id, session.session_id, confirmation_token=TOKEN),
            manager.confirm_intent(action.action_id, session.session_id, confirmation_token=TOKEN),
        )
    assert action.status == "failed"
    result = await manager.confirm_intent(action.action_id, session.session_id, confirmation_token=TOKEN)
    assert result["status"] == "approved"


@pytest.mark.asyncio
async def test_filing_failure_is_bounded_and_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _manager(tmp_path)
    session = _session()
    action = _action("retryable")
    session.intent_actions[action.action_id] = action
    from symphony.chat import _confirmation_token_hash
    session.confirmation_token_hash = _confirmation_token_hash(TOKEN)
    manager._sessions[session.session_id] = session
    attempts = 0

    def fail_once(*_args: object, **_kwargs: object) -> dict[str, str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("secret-path=" + "x" * 5000)
        return {"id": "REQ-4"}

    monkeypatch.setattr("symphony.chat.file_intent_request", fail_once)
    with pytest.raises(ChatIntentActionError):
        await manager.confirm_intent(action.action_id, session.session_id, confirmation_token=TOKEN)
    assert action.status == "failed"
    assert action.error is not None and len(action.error) <= 800
    result = await manager.confirm_intent(action.action_id, session.session_id, confirmation_token=TOKEN)
    assert result["status"] == "approved"
