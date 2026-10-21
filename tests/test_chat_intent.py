"""Intent proposals and approval: the single human gate of the chat cycle."""

from __future__ import annotations

import json
from typing import Any

from symphony.intent import (
    parse_intent_marker,
    render_intent_markdown,
    render_ticket_description,
    scan_tripwires,
)

INTENT_BODY = (
    "## Problem\n\nOperators cannot see failed turns.\n\n"
    "## Evidence\n\n- log lines show worker_exit with error [verified: log]\n\n"
    "## Success criteria\n\n- [ ] the board card shows the last error\n"
    "- [ ] `pytest tests/test_board.py` passes\n\n"
    "## Out of scope\n\n- TUI changes\n\n"
    "## Constraints\n\n- no new dependency\n\n"
    "## Open questions\n\n- none\n"
)


def _marker(**overrides: Any) -> str:
    payload = {
        "slug": "show-last-error",
        "title": "Show the last error on the board card",
        "track": "full",
        "intent": INTENT_BODY,
    }
    payload.update(overrides)
    return "<symphony-intent>" + json.dumps(payload) + "</symphony-intent>"


def test_parse_intent_marker_scans_tripwires_and_forces_full_track() -> None:
    clean = scan_tripwires(INTENT_BODY)
    assert clean == ()

    risky = INTENT_BODY.replace(
        "## Constraints\n\n- no new dependency\n",
        "## Constraints\n\n- run `ALTER TABLE users` first\n- rotate the auth token\n",
    )
    hits = scan_tripwires(risky)
    assert [hit.label for hit in hits] == ["migration/schema", "security paths"]
    assert hits[0].lines == ("- run `ALTER TABLE users` first",)

    _visible, action = parse_intent_marker(_marker(track="micro", intent=risky))
    assert action is not None
    assert action.track == "full", "a trip-wire hit must force the full track"
    assert [hit["label"] for hit in action.as_dict()["tripwires"]] == [
        "migration/schema",
        "security paths",
    ]

    ticket = render_ticket_description(action, approved_at="2026-09-12T00:00:00Z", session_id="s")
    assert "## Trip-wires" in ticket
    assert "- migration/schema: `- run `ALTER TABLE users` first`" in ticket
    assert ticket.index("## Trip-wires") < ticket.index("## Track")
    record = render_intent_markdown(
        action, approved_at="2026-09-12T00:00:00Z", session_id="s", ticket_identifier="REQ-9"
    )
    assert "## Trip-wires" in record and "security paths" in record

    _visible, clean_action = parse_intent_marker(_marker(track="micro"))
    assert clean_action is not None and clean_action.track == "micro"
    assert clean_action.as_dict()["tripwires"] == []
    assert "- none (heuristic scan" in render_ticket_description(
        clean_action, approved_at="2026-09-12T00:00:00Z", session_id="s"
    )
