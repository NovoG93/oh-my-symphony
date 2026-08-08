"""Legacy-board compatibility for the Learn -> Document lane rename.

State names are user config: a pre-rename board whose final active lane is
still called `Learn` must keep loading and keep its operator skip control
(`Orchestrator.skip_document`, plus the deprecated `skip_learn` method
alias) without any migration step.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from symphony.orchestrator import Orchestrator
from symphony.workflow import WorkflowState

LEGACY_WORKFLOW = """---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, "In Progress", Verify, Learn]
  terminal_states: ["Human Review", Done, Archive]

agent:
  kind: claude
---

You are working on {{ issue.identifier }}.
"""

RENAMED_WORKFLOW = LEGACY_WORKFLOW.replace("Learn", "Document")

TICKET = """---
id: T-1
identifier: T-1
title: legacy ticket
state: {state}
priority: 2
created_at: '2026-07-01T00:00:00Z'
updated_at: '2026-07-01T00:00:00Z'
---

Body.
"""


def _board(tmp_path: Path, workflow_text: str, ticket_state: str) -> WorkflowState:
    (tmp_path / "WORKFLOW.md").write_text(workflow_text, encoding="utf-8")
    kanban = tmp_path / "kanban"
    kanban.mkdir()
    (kanban / "T-1.md").write_text(TICKET.format(state=ticket_state), encoding="utf-8")
    return WorkflowState(tmp_path / "WORKFLOW.md")


def test_legacy_learn_board_still_loads(tmp_path: Path) -> None:
    state = _board(tmp_path, LEGACY_WORKFLOW, "Learn")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    assert tuple(cfg.tracker.active_states) == (
        "Todo",
        "In Progress",
        "Verify",
        "Learn",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workflow_text", "lane"),
    [(LEGACY_WORKFLOW, "Learn"), (RENAMED_WORKFLOW, "Document")],
)
async def test_skip_document_moves_idle_lane_ticket_to_human_review(
    tmp_path: Path, workflow_text: str, lane: str
) -> None:
    state = _board(tmp_path, workflow_text, lane)
    orch = Orchestrator(state)

    changed, message = await orch.skip_document("T-1")

    assert changed is True
    assert message == "moved T-1 to Human Review"
    ticket = (tmp_path / "kanban" / "T-1.md").read_text(encoding="utf-8")
    assert "state: Human Review" in ticket
    assert "## Document Skipped" in ticket


@pytest.mark.asyncio
async def test_skip_learn_method_alias_still_works(tmp_path: Path) -> None:
    state = _board(tmp_path, LEGACY_WORKFLOW, "Learn")
    orch = Orchestrator(state)

    changed, _message = await orch.skip_learn("T-1")

    assert changed is True
    ticket = (tmp_path / "kanban" / "T-1.md").read_text(encoding="utf-8")
    assert "state: Human Review" in ticket


@pytest.mark.asyncio
async def test_skip_document_rejects_other_lanes(tmp_path: Path) -> None:
    state = _board(tmp_path, RENAMED_WORKFLOW, "Verify")
    orch = Orchestrator(state)

    changed, message = await orch.skip_document("T-1")

    assert changed is False
    assert "only Document tickets can be skipped" in message
