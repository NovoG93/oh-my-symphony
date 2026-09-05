import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import symphony.intent as intent_module
from symphony.errors import ChatIntentActionError
from symphony.intent import (
    IntentAction,
    IntentFiler,
    IntentParseError,
    IntentProposal,
    parse_intent_marker,
)
from symphony.workflow.config import TrackerConfig
from symphony.trackers.file import FileBoardTracker


def _proposal(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "slug": "repair-widget",
        "title": "Repair widget",
        "track": "micro",
        "intent": (
            "## Problem\nThe widget fails.\n\n"
            "## Success criteria\n- [ ] Widget works.\n\n"
            "## Out of scope\nA redesign."
        ),
    }
    value.update(overrides)
    return value


def _marked(payload: object) -> str:
    return "before\n<symphony-intent>\n" + json.dumps(payload) + "\n</symphony-intent>\nafter"


def test_parse_marker_removes_only_machine_wrapper() -> None:
    visible, proposal = parse_intent_marker(_marked(_proposal()))
    assert visible == "before\n\nafter"
    assert proposal == IntentProposal(**_proposal())


def test_parse_marker_without_marker_is_unchanged() -> None:
    text = "ordinary agent response"
    assert parse_intent_marker(text) == (text, None)


@pytest.mark.parametrize(
    "payload",
    [
        '{"slug":"repair-widget","slug":"other","title":"x","track":"micro","intent":"x"}',
        {**_proposal(), "unexpected": True},
        {**_proposal(), "slug": "../escape"},
        {**_proposal(), "track": "huge"},
        {**_proposal(), "intent": "## Problem\nx\n## Out of scope\ny"},
    ],
)
def test_parse_marker_rejects_unsafe_or_invalid_payload(payload: object) -> None:
    with pytest.raises(IntentParseError):
        parse_intent_marker(_marked(payload))


def test_parse_marker_rejects_multiple_markers() -> None:
    with pytest.raises(IntentParseError):
        parse_intent_marker(_marked(_proposal()) + _marked(_proposal()))


@pytest.mark.parametrize(
    "text",
    [
        "<symphony-intent>{}</symphony-intent>",
        "<symphony-intent>{}",
        "{}</symphony-intent>",
        "<symphony-intent><symphony-intent>{}</symphony-intent></symphony-intent>",
    ],
)
def test_parse_marker_rejects_unmatched_nested_or_extra_markers(text: str) -> None:
    with pytest.raises(IntentParseError):
        parse_intent_marker(text)


@pytest.mark.parametrize(
    "overrides",
    [
        {"slug": None},
        {"title": None},
        {"track": []},
        {"intent": None},
        {"title": "   "},
        {"title": "x" * 201},
        {"title": "line\nbreak"},
    ],
)
def test_parse_marker_rejects_missing_or_non_string_and_unsafe_fields(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(IntentParseError):
        parse_intent_marker(_marked(_proposal(**overrides)))


@pytest.mark.parametrize("heading", ["Problem", "Success criteria", "Out of scope"])
def test_parse_marker_requires_each_heading(heading: str) -> None:
    body = _proposal()["intent"]
    assert isinstance(body, str)
    body = body.replace(f"## {heading}", f"### {heading}")
    with pytest.raises(IntentParseError):
        parse_intent_marker(_marked(_proposal(intent=body)))


@pytest.mark.parametrize("criteria", ["- [x] done", "- a prose criterion"])
def test_parse_marker_requires_unchecked_success_criterion(criteria: str) -> None:
    body = _proposal()["intent"]
    assert isinstance(body, str)
    body = body.replace("- [ ] Widget works.", criteria)
    with pytest.raises(IntentParseError):
        parse_intent_marker(_marked(_proposal(intent=body)))


def test_parse_marker_enforces_body_byte_limit() -> None:
    body = "## Problem\n" + ("é" * (32 * 1024)) + (
        "\n\n## Success criteria\n- [ ] Works.\n\n## Out of scope\nNone."
    )
    with pytest.raises(IntentParseError):
        parse_intent_marker(_marked(_proposal(intent=body)))


def test_action_id_ttl_boundary_and_snapshot_excludes_task() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    action = IntentAction.from_proposal(IntentProposal(**_proposal()), now=now)
    assert action.action_id.startswith("intent-")
    assert len(action.action_id) == len("intent-") + 32
    assert action.is_expired(now=now + timedelta(minutes=30))
    action.task = object()
    assert "task" not in action.snapshot()


def test_file_filer_allocates_request_and_contained_artifact(tmp_path: Path) -> None:
    board = tmp_path / "kanban"
    tracker = TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=("Intake", "Build"),
        terminal_states=("Done",),
        board_root=board,
    )
    proposal = IntentProposal(**_proposal())
    action = IntentAction.from_proposal(proposal)
    ticket = IntentFiler(tmp_path, tracker).file(action, session_id="session-1")
    assert ticket["identifier"] == "REQ-1"
    assert ticket["request"] == proposal.slug
    ticket_text = (board / "REQ-1.md").read_text(encoding="utf-8")
    assert "state: Intake" in ticket_text
    assert proposal.intent in ticket_text
    artifact = tmp_path / ".sdlc" / "work" / proposal.slug / "intent.md"
    assert artifact.is_file()
    assert proposal.intent in artifact.read_text(encoding="utf-8")


def test_file_filer_rejects_expired_action_without_board_write(tmp_path: Path) -> None:
    tracker = TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=("Todo",),
        terminal_states=("Done",),
        board_root=tmp_path / "kanban",
    )
    action = IntentAction.from_proposal(IntentProposal(**_proposal()))
    action.expires_at = action.expires_at.replace(year=2000)
    with pytest.raises(ChatIntentActionError):
        IntentFiler(tmp_path, tracker).file(action)
    assert not (tmp_path / "kanban" / "REQ-1.md").exists()


@pytest.mark.parametrize(
    "kind,active_states",
    [("linear", ("Todo",)), ("file", ())],
)
def test_file_filer_rejects_unsupported_or_empty_workflow(
    tmp_path: Path, kind: str, active_states: tuple[str, ...]
) -> None:
    tracker = TrackerConfig(
        kind=kind,
        endpoint="",
        api_key="",
        project_slug="",
        active_states=active_states,
        terminal_states=("Done",),
        board_root=tmp_path / "kanban",
    )
    action = IntentAction.from_proposal(IntentProposal(**_proposal()))
    with pytest.raises(ChatIntentActionError):
        IntentFiler(tmp_path, tracker).file(action)


@pytest.mark.parametrize("slug", ["../escape", "/absolute", "nested/path"])
def test_file_filer_rejects_direct_unsafe_action_slug(tmp_path: Path, slug: str) -> None:
    tracker = TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=("Todo",),
        terminal_states=("Done",),
        board_root=tmp_path / "kanban",
    )
    proposal = IntentProposal(slug, "Safe title", "micro", _proposal()["intent"])
    action = IntentAction.from_proposal(proposal)
    with pytest.raises(ChatIntentActionError):
        IntentFiler(tmp_path, tracker).file(action)
    assert not (tmp_path / "kanban" / "REQ-1.md").exists()


def test_file_filer_rejects_symlinked_artifact_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "work").symlink_to(outside, target_is_directory=True)
    tracker = TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=("Todo",),
        terminal_states=("Done",),
        board_root=tmp_path / "kanban",
    )
    action = IntentAction.from_proposal(IntentProposal(**_proposal()))
    with pytest.raises(ChatIntentActionError):
        IntentFiler(tmp_path, tracker).file(action)
    assert not (tmp_path / "kanban" / "REQ-1.md").exists()
    assert not (outside / "repair-widget" / "intent.md").exists()


def _file_tracker(tmp_path: Path) -> TrackerConfig:
    return TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=("Todo",),
        terminal_states=("Done",),
        board_root=tmp_path / "kanban",
    )


def test_artifact_write_failure_creates_no_request_ticket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    action = IntentAction.from_proposal(IntentProposal(**_proposal()))

    def fail_write(_path: Path, _content: str) -> None:
        raise OSError("simulated artifact replacement failure")

    monkeypatch.setattr(intent_module, "_write_text_atomic", fail_write)
    with pytest.raises(OSError):
        IntentFiler(tmp_path, _file_tracker(tmp_path)).file(action)
    assert not (tmp_path / "kanban" / "REQ-1.md").exists()


def test_ticket_failure_leaves_retryable_artifact_and_retry_allocates_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    action = IntentAction.from_proposal(IntentProposal(**_proposal()))
    original = FileBoardTracker.create_validated
    calls = 0

    def fail_once(self: FileBoardTracker, **kwargs: object) -> tuple[str, Path]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("simulated ticket allocation failure")
        return original(self, **kwargs)

    monkeypatch.setattr(FileBoardTracker, "create_validated", fail_once)
    filer = IntentFiler(tmp_path, _file_tracker(tmp_path))
    with pytest.raises(OSError):
        filer.file(action, session_id="session")
    artifact = tmp_path / ".sdlc" / "work" / "repair-widget" / "intent.md"
    assert artifact.is_file()
    assert not (tmp_path / "kanban" / "REQ-1.md").exists()

    ticket = filer.file(action, session_id="session")
    assert ticket["identifier"] == "REQ-1"
    assert len(list((tmp_path / "kanban").glob("REQ-*.md"))) == 1
    assert "action=intent-" in artifact.read_text(encoding="utf-8")
