"""Manual completion must exercise the host lifecycle, not only edit a card."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp.test_utils import TestClient, TestServer

import symphony.orchestrator.core as core
from symphony.orchestrator import Orchestrator
from symphony.orchestrator.run_registry import RunRegistry
from symphony.errors import SymphonyError
from symphony.server import build_app
from symphony.workspace import HISTORY_LOCAL_ONLY, HistoryGateResult, WorkspaceManager
from symphony.workflow import WorkflowState


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    board = tmp_path / "kanban"
    board.mkdir()
    ticket = board / "REVIEW-1.md"
    ticket.write_text(
        "---\nid: REVIEW-1\nidentifier: REVIEW-1\ntitle: Review me\n"
        "state: Human Review\n---\n\nReady for confirmation.\n", encoding="utf-8"
    )
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        "---\ntracker:\n  kind: file\n  board_root: ./kanban\n"
        "  active_states: [Todo, Doing]\n"
        "  terminal_states: [Human Review, Done, Blocked]\n"
        "workspace:\n  root: ./workspaces\n"
        "agent:\n  auto_merge_push_target: false\n"
        "wiki:\n  sweep_every_n: 2\n---\nWork on the ticket.\n",
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
    notify = Mock()
    monkeypatch.setattr(core, "finalize_delivery_history", history)
    monkeypatch.setattr(core, "auto_merge_on_done_best_effort", merge)
    monkeypatch.setattr(core, "_notify_state_transition", notify)
    yield SimpleNamespace(
        orch=orch, cfg=cfg, state=state, path=path, ticket=ticket,
        hook=hook, remove=remove, history=history, merge=merge, notify=notify,
    )
    if orch._run_registry is not None:
        orch._run_registry.close()


def stored(h):
    return h.orch._tracker_call_fetch_issue_full_by_id(h.cfg, "REVIEW-1")


async def test_confirm_done_serializes_and_runs_lifecycle_once(handoff):
    h = handoff
    results = await asyncio.gather(
        h.orch.confirm_done("REVIEW-1"), h.orch.confirm_done("REVIEW-1")
    )
    assert sorted(r["changed"] for r in results) == [False, True]
    assert all(r["state"] == "Done" for r in results)
    assert stored(h).state == "Done"
    h.history.assert_awaited_once()
    h.merge.assert_awaited_once()
    h.hook.assert_awaited_once()
    h.remove.assert_awaited_once()
    assert h.orch._done_count == 1
    assert h.notify.call_args.args[2] == "Done"
    h.notify.assert_called_once()


@pytest.mark.parametrize("missing", ["manager", "workspace", "worker"])
async def test_confirm_done_refuses_unavailable_pipeline(handoff, monkeypatch, missing):
    h = handoff
    if missing == "manager":
        h.orch._workspace_manager = None
    elif missing == "workspace":
        h.path.rmdir()
    else:
        monkeypatch.setattr(h.orch, "find_running_issue_id", lambda _: "REVIEW-1")
    with pytest.raises(ValueError):
        await h.orch.confirm_done("REVIEW-1")
    assert stored(h).state == "Human Review"
    h.history.assert_not_awaited()
    h.merge.assert_not_awaited()
    h.hook.assert_not_awaited()


@pytest.mark.parametrize("status", ["commit_failed", "push_failed"])
async def test_confirm_done_history_failure_preserves_review(handoff, status):
    h = handoff
    h.history.return_value = HistoryGateResult(status, detail="test failure")
    with pytest.raises(ValueError, match="history"):
        await h.orch.confirm_done("REVIEW-1")
    assert stored(h).state == "Human Review"
    assert h.path.exists()
    h.merge.assert_not_awaited()
    h.hook.assert_not_awaited()
    assert h.orch._done_count == 0


async def test_confirm_done_merge_failure_reports_blocked(handoff):
    h = handoff
    h.merge.return_value = SimpleNamespace(ok=False, status="conflict", detail="conflict")
    with pytest.raises(ValueError, match="merge"):
        await h.orch.confirm_done("REVIEW-1")
    assert stored(h).state == "Blocked"
    assert h.path.exists()
    h.hook.assert_not_awaited()
    assert h.orch._done_count == 0
    assert [c.args[2] for c in h.notify.call_args_list] == ["Blocked"]


@pytest.mark.timing
async def test_confirm_done_renews_lease_during_slow_delivery(handoff, monkeypatch):
    h = handoff
    cfg = replace(h.cfg, poll_interval_ms=10)
    monkeypatch.setattr(h.state, "current", lambda: cfg)
    registry = h.orch._release_registry_required(cfg)
    monkeypatch.setattr(registry, "_lease_ttl", timedelta(milliseconds=80))
    peer = RunRegistry(registry.path, lease_ttl=timedelta(milliseconds=80))

    async def slow_history(*args, **kwargs):
        await asyncio.sleep(0.2)
        acquired = peer.acquire_run(
            stored(h), workspace_path=h.path, attempt=None,
            attempt_kind="manual-confirmation", agent_kind="operator",
        )
        assert acquired is None, "a peer stole the manual delivery lease"
        return HistoryGateResult(HISTORY_LOCAL_ONLY)

    h.history.side_effect = slow_history
    try:
        assert (await h.orch.confirm_done("REVIEW-1"))["state"] == "Done"
    finally:
        peer.close()


async def test_confirm_done_stops_on_lost_lease(handoff, monkeypatch):
    h = handoff
    registry = h.orch._release_registry_required(h.cfg)

    async def lose_lease(*args, **kwargs):
        monkeypatch.setattr(registry, "heartbeat", lambda **_: False)
        return HistoryGateResult(HISTORY_LOCAL_ONLY)

    h.history.side_effect = lose_lease
    with pytest.raises(SymphonyError, match="lease"):
        await h.orch.confirm_done("REVIEW-1")
    assert stored(h).state == "Human Review"
    h.merge.assert_not_awaited()
    h.hook.assert_not_awaited()


async def test_confirm_done_lost_lease_during_refresh_never_records_history(handoff, monkeypatch):
    h = handoff
    registry = h.orch._release_registry_required(h.cfg)
    fetch = h.orch._tracker_call_fetch_issue_full_by_id
    calls = 0

    def lose_during_refresh(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            monkeypatch.setattr(registry, "heartbeat", lambda **_: False)
        return fetch(*args)

    monkeypatch.setattr(h.orch, "_tracker_call_fetch_issue_full_by_id", lose_during_refresh)
    with pytest.raises(SymphonyError, match="lease"):
        await h.orch.confirm_done("REVIEW-1")
    h.history.assert_not_awaited()
    h.merge.assert_not_awaited()
    assert stored(h).state == "Human Review"


@pytest.mark.parametrize("change", ["Done", "Blocked", "release"])
async def test_confirm_done_revalidates_after_acquiring_lease(handoff, monkeypatch, change):
    h = handoff
    registry = h.orch._release_registry_required(h.cfg)
    acquire = registry.acquire_run

    def acquire_after_peer_change(*args, **kwargs):
        run_id = acquire(*args, **kwargs)
        replacement = (
            "state: Human Review\nlabels: [app-release]" if change == "release"
            else f"state: {change}"
        )
        h.ticket.write_text(h.ticket.read_text().replace(
            "state: Human Review", replacement
        ), encoding="utf-8")
        return run_id

    monkeypatch.setattr(registry, "acquire_run", acquire_after_peer_change)
    if change == "Done":
        assert (await h.orch.confirm_done("REVIEW-1"))["changed"] is False
    else:
        with pytest.raises(ValueError):
            await h.orch.confirm_done("REVIEW-1")
    h.history.assert_not_awaited()
    h.merge.assert_not_awaited()
    h.hook.assert_not_awaited()


@pytest.mark.parametrize("kind", ["label", "verifier", "finalizer", "evidence"])
async def test_confirm_done_cannot_bypass_release_authority(handoff, monkeypatch, kind):
    h = handoff
    if kind == "label":
        h.ticket.write_text(h.ticket.read_text().replace(
            "state: Human Review", "state: Human Review\nlabels: [app-release]"
        ), encoding="utf-8")
    else:
        registry = h.orch._release_registry_required(h.cfg)
        method = {
            "verifier": "get_release_gate_for_verifier",
            "finalizer": "get_release_gate",
            "evidence": "get_release_evidence_identity",
        }[kind]
        monkeypatch.setattr(registry, method, lambda _: object())
    with pytest.raises(ValueError, match="release"):
        await h.orch.confirm_done("REVIEW-1")
    assert stored(h).state == "Human Review"
    h.history.assert_not_awaited()
    h.merge.assert_not_awaited()


@pytest.mark.parametrize("policy", ["warn", "block"])
async def test_confirm_done_honors_hook_failure_policy(handoff, monkeypatch, policy):
    h = handoff
    cfg = replace(h.cfg, agent=replace(h.cfg.agent, after_done_failure_policy=policy))
    monkeypatch.setattr(h.state, "current", lambda: cfg)
    h.hook.return_value = False
    result = await h.orch.confirm_done("REVIEW-1")
    assert result["state"] == "Done"
    assert h.path.exists() == (policy == "block")
    if policy == "block":
        assert "workspace preserved" in h.orch._issue_debug[stored(h).id].last_error


@pytest.mark.parametrize("route", ["post", "patch"])
async def test_http_confirmation_runs_orchestrator_pipeline(handoff, route):
    h = handoff
    async with TestClient(TestServer(build_app(h.orch))) as client:
        if route == "post":
            response = await client.post("/api/v1/REVIEW-1/confirm-done")
        else:
            response = await client.patch(
                "/api/v1/issues/REVIEW-1", json={"state": "Done", "title": "Approved"}
            )
        assert response.status == 200, await response.text()
        if route == "patch":
            assert (await response.json())["updated"] == ["state", "title"]
            assert stored(h).title == "Approved"
    assert stored(h).state == "Done"
    h.hook.assert_awaited_once()
    h.remove.assert_awaited_once()
    assert h.orch._done_count == 1


@pytest.mark.parametrize("route", ["post", "patch"])
async def test_http_confirmation_failure_is_not_success(handoff, route):
    h = handoff
    h.history.return_value = HistoryGateResult("commit_failed")
    async with TestClient(TestServer(build_app(h.orch))) as client:
        if route == "post":
            response = await client.post("/api/v1/REVIEW-1/confirm-done")
        else:
            response = await client.patch("/api/v1/issues/REVIEW-1", json={"state": "Done"})
        assert response.status == 409
    assert stored(h).state == "Human Review"
    assert h.path.exists()
