"""Manual Human Review completion uses the normal host delivery gates."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import symphony.orchestrator.core as core
from symphony.orchestrator import Orchestrator
from symphony.errors import SymphonyError
from symphony.workspace import HISTORY_LOCAL_ONLY, HistoryGateResult, WorkspaceManager
from symphony.workflow import WorkflowState


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    board = tmp_path / "kanban"
    board.mkdir()
    ticket = board / "REVIEW-1.md"
    ticket.write_text(
        "---\nid: REVIEW-1\nidentifier: REVIEW-1\ntitle: Review me\n"
        "state: Human Review\n---\n\nReady for confirmation.\n",
        encoding="utf-8",
    )
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        "---\ntracker:\n  kind: file\n  board_root: ./kanban\n"
        "  active_states: [Todo, Doing]\n"
        "  terminal_states: [Human Review, Done, Blocked]\n"
        "workspace:\n  root: ./workspaces\n"
        "agent:\n  auto_merge_push_target: false\n"
        "wiki:\n  sweep_every_n: 0\n---\nWork on the ticket.\n",
        encoding="utf-8",
    )
    state = WorkflowState(workflow)
    cfg, error = state.reload()
    assert error is None and cfg is not None
    orch = Orchestrator(state)
    manager = WorkspaceManager(cfg.workspace_root, cfg.hooks)
    orch._workspace_manager = manager
    path = manager.path_for("REVIEW-1")
    path.mkdir(parents=True)
    hook = AsyncMock(return_value=True)
    remove = AsyncMock(side_effect=lambda p: p.rmdir())
    monkeypatch.setattr(manager, "after_done_best_effort", hook)
    monkeypatch.setattr(manager, "remove", remove)
    history = AsyncMock(return_value=HistoryGateResult(HISTORY_LOCAL_ONLY))
    merge = AsyncMock(return_value=None)
    monkeypatch.setattr(core, "finalize_delivery_history", history)
    monkeypatch.setattr(core, "auto_merge_on_done_best_effort", merge)
    yield SimpleNamespace(
        orch=orch,
        cfg=cfg,
        state=state,
        path=path,
        ticket=ticket,
        hook=hook,
        remove=remove,
        history=history,
        merge=merge,
    )
    if orch._run_registry is not None:
        orch._run_registry.close()


def stored(h):
    return h.orch._tracker_call_fetch_issue_full_by_id(h.cfg, "REVIEW-1")


@pytest.mark.asyncio
async def test_confirm_done_serializes_delivery_and_finishes_once(handoff):
    h = handoff
    results = await asyncio.gather(
        h.orch.confirm_done("REVIEW-1"), h.orch.confirm_done("REVIEW-1")
    )

    assert sorted(result["changed"] for result in results) == [False, True]
    assert all(result["state"] == "Done" for result in results)
    assert stored(h).state == "Done"
    h.history.assert_awaited_once()
    h.merge.assert_awaited_once()
    h.hook.assert_awaited_once()
    h.remove.assert_awaited_once()
    assert h.orch._done_count == 0  # sweep is disabled by this workflow


@pytest.mark.asyncio
async def test_confirm_done_history_failure_preserves_review_and_workspace(handoff):
    h = handoff
    h.history.return_value = HistoryGateResult("commit_failed", detail="test failure")

    with pytest.raises(ValueError, match="history gate failed"):
        await h.orch.confirm_done("REVIEW-1")

    assert stored(h).state == "Human Review"
    assert h.path.exists()
    h.merge.assert_not_awaited()
    h.hook.assert_not_awaited()
    assert h.orch._done_count == 0


@pytest.mark.asyncio
async def test_confirm_done_merge_failure_blocks_issue_and_preserves_workspace(handoff):
    h = handoff
    h.merge.return_value = SimpleNamespace(
        ok=False, status="conflict", detail="conflict"
    )

    with pytest.raises(ValueError, match="merge gate failed"):
        await h.orch.confirm_done("REVIEW-1")

    assert stored(h).state == "Blocked"
    assert h.path.exists()
    h.hook.assert_not_awaited()
    assert h.orch._done_count == 0


@pytest.mark.asyncio
async def test_confirm_done_rejects_running_worker_before_delivery(handoff, monkeypatch):
    h = handoff
    monkeypatch.setattr(h.orch, "find_running_issue_id", lambda _identifier: "run-1")

    with pytest.raises(ValueError, match="running worker"):
        await h.orch.confirm_done("REVIEW-1")

    assert stored(h).state == "Human Review"
    h.history.assert_not_awaited()
    h.merge.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_done_rejects_lost_lease_before_merge(handoff, monkeypatch):
    h = handoff
    registry = h.orch._release_registry_required(h.cfg)
    monkeypatch.setattr(registry, "heartbeat", lambda **_: False)

    with pytest.raises(SymphonyError, match="lost its run lease"):
        await h.orch.confirm_done("REVIEW-1")

    assert stored(h).state == "Human Review"
    h.merge.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_done_stale_after_history_does_not_merge(handoff):
    h = handoff

    async def change_lane(*_args, **_kwargs):
        client = core.build_tracker_client(h.cfg)
        try:
            client.transition("REVIEW-1", "Blocked")
        finally:
            client.close()
        return HistoryGateResult(HISTORY_LOCAL_ONLY)

    h.history.side_effect = change_lane
    with pytest.raises(ValueError, match="no longer in Human Review"):
        await h.orch.confirm_done("REVIEW-1")

    assert stored(h).state == "Blocked"
    h.merge.assert_not_awaited()
    h.remove.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_done_stale_during_merge_does_not_overwrite_or_remove(handoff):
    h = handoff

    async def change_lane(*_args, **_kwargs):
        client = core.build_tracker_client(h.cfg)
        try:
            client.transition("REVIEW-1", "Blocked")
        finally:
            client.close()
        return None

    h.merge.side_effect = change_lane
    with pytest.raises(ValueError, match="no longer in Human Review"):
        await h.orch.confirm_done("REVIEW-1")

    assert stored(h).state == "Blocked"
    assert h.path.exists()
    h.remove.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_done_rechecks_release_identity_after_history(handoff):
    h = handoff

    async def add_release_label(*_args, **_kwargs):
        content = h.ticket.read_text(encoding="utf-8")
        h.ticket.write_text(
            content.replace("state: Human Review", "state: Human Review\nlabels: [app-release]"),
            encoding="utf-8",
        )
        return HistoryGateResult(HISTORY_LOCAL_ONLY)

    h.history.side_effect = add_release_label
    with pytest.raises(ValueError, match="release evidence"):
        await h.orch.confirm_done("REVIEW-1")

    assert stored(h).state == "Human Review"
    h.merge.assert_not_awaited()
    h.remove.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_done_detects_lease_loss_during_history(handoff, monkeypatch):
    h = handoff
    registry = h.orch._release_registry_required(h.cfg)

    async def lose_lease(*_args, **_kwargs):
        monkeypatch.setattr(registry, "heartbeat", lambda **_: False)
        return HistoryGateResult(HISTORY_LOCAL_ONLY)

    h.history.side_effect = lose_lease
    with pytest.raises(SymphonyError, match="lost its run lease"):
        await h.orch.confirm_done("REVIEW-1")

    assert stored(h).state == "Human Review"
    h.merge.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_done_block_hook_policy_preserves_workspace(handoff):
    h = handoff
    object.__setattr__(h.cfg.agent, "after_done_failure_policy", "block")
    h.hook.return_value = False

    result = await h.orch.confirm_done("REVIEW-1")

    assert result["state"] == "Done"
    assert stored(h).state == "Done"
    assert h.path.exists()
    h.remove.assert_not_awaited()
