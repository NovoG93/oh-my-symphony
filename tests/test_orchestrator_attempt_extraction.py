"""Tests for the attempt-runner module boundary."""

from __future__ import annotations

import asyncio

from symphony.orchestrator import Orchestrator
from symphony.orchestrator import attempt as attempt_module


def test_core_attempt_entrypoint_delegates_to_extracted_runner(monkeypatch) -> None:
    orchestrator = object.__new__(Orchestrator)
    issue = object()
    cfg = object()
    calls: list[tuple[object, object, object, object]] = []

    async def _runner(orch, received_issue, received_attempt, received_cfg):
        calls.append((orch, received_issue, received_attempt, received_cfg))

    monkeypatch.setattr(attempt_module, "run_agent_attempt", _runner)

    asyncio.run(orchestrator._run_agent_attempt(issue, 3, cfg))

    assert calls == [(orchestrator, issue, 3, cfg)]
