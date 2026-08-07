"""Governed-run tracker surface: run-summary upsert + orchestrator transition.

Covers the two capabilities a governed workflow run needs from the file
tracker: an idempotent marker-delimited run summary in the ticket body, and
a coarse board-state write that the orchestrator (not the agent) owns.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from symphony.trackers.file import FileBoardTracker, parse_ticket_file
from symphony.workflow import TrackerConfig


def _tracker(root: Path, **kwargs) -> TrackerConfig:
    return TrackerConfig(
        kind="file",
        endpoint="",
        api_key="",
        project_slug="",
        active_states=kwargs.get("active", ("Todo", "In Progress")),
        terminal_states=kwargs.get("terminal", ("Done", "Cancelled")),
        board_root=root.resolve(),
    )


def _seed(root: Path, *, state: str = "Todo", body: str = "Original body.") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "DEV-1.md"
    path.write_text(
        textwrap.dedent(
            f"""\
            ---
            id: DEV-1
            identifier: DEV-1
            title: Governed ticket
            state: {state}
            created_at: 2026-05-08T10:00:00Z
            updated_at: 2026-05-08T10:00:00Z
            ---
            {body}
            """
        ),
        encoding="utf-8",
    )
    return path


def _body(path: Path) -> str:
    return parse_ticket_file(path)[1]


# --- upsert_run_summary ----------------------------------------------------


def test_run_summary_written_then_replaced_in_place(tmp_path):
    root = tmp_path / "board"
    path = _seed(root)
    fbt = FileBoardTracker(_tracker(root))

    assert fbt.upsert_run_summary(
        "DEV-1",
        run_id="run-abc",
        workflow_name="ticket-default",
        result="running",
        artifact_dir=".symphony/artifacts/run-abc/",
        branch="symphony/DEV-1",
    )
    body = _body(path)
    assert body.count("<!-- symphony-run:run-abc:start -->") == 1
    assert "- Result: running" in body
    assert "- Branch: `symphony/DEV-1`" in body
    assert body.startswith("Original body.")

    assert fbt.upsert_run_summary(
        "DEV-1",
        run_id="run-abc",
        workflow_name="ticket-default",
        result="succeeded",
        artifact_dir=".symphony/artifacts/run-abc/",
        branch="symphony/DEV-1",
    )
    body = _body(path)
    assert body.count("<!-- symphony-run:run-abc:start -->") == 1
    assert body.count("<!-- symphony-run:run-abc:end -->") == 1
    assert body.count("## Symphony Run") == 1
    assert "- Result: succeeded" in body
    assert "- Result: running" not in body
    assert body.startswith("Original body.")


def test_run_summary_replay_is_byte_identical(tmp_path):
    root = tmp_path / "board"
    path = _seed(root)
    fbt = FileBoardTracker(_tracker(root))
    kwargs = dict(
        run_id="run-abc",
        workflow_name="ticket-default",
        result="succeeded",
        artifact_dir=".symphony/artifacts/run-abc/",
    )
    fbt.upsert_run_summary("DEV-1", **kwargs)
    first = path.read_text(encoding="utf-8")
    fbt.upsert_run_summary("DEV-1", **kwargs)
    assert path.read_text(encoding="utf-8") == first


def test_two_run_ids_keep_separate_sections_in_write_order(tmp_path):
    root = tmp_path / "board"
    path = _seed(root)
    fbt = FileBoardTracker(_tracker(root))

    fbt.upsert_run_summary(
        "DEV-1",
        run_id="run-first",
        workflow_name="ticket-default",
        result="succeeded",
        artifact_dir=".symphony/artifacts/run-first/",
    )
    fbt.upsert_run_summary(
        "DEV-1",
        run_id="run-second",
        workflow_name="quick-fix",
        result="needs_attention",
        artifact_dir=".symphony/artifacts/run-second/",
    )
    body = _body(path)
    assert body.count("## Symphony Run") == 2
    assert body.index("run-first") < body.index("run-second")

    # Re-writing the first run must not reorder or duplicate the second.
    fbt.upsert_run_summary(
        "DEV-1",
        run_id="run-first",
        workflow_name="ticket-default",
        result="cancelled",
        artifact_dir=".symphony/artifacts/run-first/",
    )
    body = _body(path)
    assert body.count("<!-- symphony-run:run-first:start -->") == 1
    assert body.count("<!-- symphony-run:run-second:start -->") == 1
    assert body.index("run-first") < body.index("run-second")
    assert "- Result: cancelled" in body
    assert "- Result: needs_attention" in body


def test_marker_inside_fenced_block_is_not_treated_as_a_marker(tmp_path):
    root = tmp_path / "board"
    fenced = textwrap.dedent(
        """\
        Docs sample:

        ```markdown
        <!-- symphony-run:run-abc:start -->
        ## Symphony Run
        <!-- symphony-run:run-abc:end -->
        ```
        """
    ).rstrip()
    path = _seed(root, body=fenced)
    fbt = FileBoardTracker(_tracker(root))

    fbt.upsert_run_summary(
        "DEV-1",
        run_id="run-abc",
        workflow_name="ticket-default",
        result="succeeded",
        artifact_dir=".symphony/artifacts/run-abc/",
    )
    body = _body(path)
    # The fenced sample is untouched and a real section was appended after it.
    assert "```markdown" in body
    assert body.count("<!-- symphony-run:run-abc:start -->") == 2
    assert "- Result: succeeded" in body
    assert body.index("```") < body.index("- Result: succeeded")

    # A second write replaces only the real section, never the fenced sample.
    fbt.upsert_run_summary(
        "DEV-1",
        run_id="run-abc",
        workflow_name="ticket-default",
        result="rejected",
        artifact_dir=".symphony/artifacts/run-abc/",
    )
    body = _body(path)
    assert body.count("<!-- symphony-run:run-abc:start -->") == 2
    assert body.count("- Result:") == 1
    assert "- Result: rejected" in body


@pytest.mark.parametrize(
    "run_id",
    ["", "run abc", "run:abc", "bad --> out", "run/abc", "x" * 65],
)
def test_invalid_run_id_raises_value_error(tmp_path, run_id):
    root = tmp_path / "board"
    path = _seed(root)
    fbt = FileBoardTracker(_tracker(root))
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        fbt.upsert_run_summary(
            "DEV-1",
            run_id=run_id,
            workflow_name="ticket-default",
            result="succeeded",
            artifact_dir=".symphony/artifacts/x/",
        )
    assert path.read_text(encoding="utf-8") == before


def test_run_summary_returns_false_for_unknown_identifier(tmp_path):
    root = tmp_path / "board"
    _seed(root)
    fbt = FileBoardTracker(_tracker(root))
    assert (
        fbt.upsert_run_summary(
            "NOPE-9",
            run_id="run-abc",
            workflow_name="ticket-default",
            result="succeeded",
            artifact_dir=".symphony/artifacts/run-abc/",
        )
        is False
    )


def test_run_summary_extra_lines_and_optional_branch(tmp_path):
    root = tmp_path / "board"
    path = _seed(root)
    fbt = FileBoardTracker(_tracker(root))
    fbt.upsert_run_summary(
        "DEV-1",
        run_id="run-abc",
        workflow_name="ticket-default",
        result="succeeded",
        artifact_dir=".symphony/artifacts/run-abc/",
        extra_lines=("- Approvals: 1", "- Nodes: 4"),
    )
    body = _body(path)
    assert "- Branch:" not in body
    assert "- Approvals: 1" in body
    assert body.index("- Approvals: 1") < body.index("- Nodes: 4")
    assert body.index("- Nodes: 4") < body.index("<!-- symphony-run:run-abc:end -->")


# --- governed_transition ---------------------------------------------------


def test_governed_transition_to_same_state_is_a_no_op(tmp_path):
    root = tmp_path / "board"
    path = _seed(root, state="In Progress")
    fbt = FileBoardTracker(_tracker(root))
    before = path.read_text(encoding="utf-8")

    assert fbt.governed_transition("DEV-1", "In Progress") is False
    assert path.read_text(encoding="utf-8") == before
    assert "updated_at: 2026-05-08T10:00:00Z" in path.read_text(encoding="utf-8")


def test_governed_transition_strips_warning_and_sets_state_in_one_write(
    tmp_path, monkeypatch
):
    root = tmp_path / "board"
    body = textwrap.dedent(
        """\
        Original body.

        ## Conflict

        Branch symphony/DEV-1 diverged from main.

        ## Notes

        Keep me.
        """
    ).rstrip()
    path = _seed(root, state="Done", body=body)
    fbt = FileBoardTracker(_tracker(root))

    writes: list[Path] = []
    real_write = FileBoardTracker._mutate_ticket

    def counting(self, identifier, mutate, **kwargs):
        writes.append(Path(identifier))
        return real_write(self, identifier, mutate, **kwargs)

    monkeypatch.setattr(FileBoardTracker, "_mutate_ticket", counting)

    assert fbt.governed_transition("DEV-1", "In Progress") is True
    assert len(writes) == 1  # one lock acquisition, not two

    front, new_body = parse_ticket_file(path)
    assert front["state"] == "In Progress"
    assert "2026-05-08T10:00:00" not in str(front["updated_at"])
    assert "## Conflict" not in new_body
    assert "diverged from main" not in new_body
    assert "## Notes" in new_body
    assert "Keep me." in new_body
    assert new_body.startswith("Original body.")


def test_governed_transition_keeps_run_summary_when_stripping_warning(tmp_path):
    root = tmp_path / "board"
    path = _seed(root, state="Done")
    fbt = FileBoardTracker(_tracker(root))
    fbt.upsert_run_summary(
        "DEV-1",
        run_id="run-abc",
        workflow_name="ticket-default",
        result="needs_attention",
        artifact_dir=".symphony/artifacts/run-abc/",
    )
    # A warning appended *before* the run summary would otherwise swallow the
    # summary's start marker and break the next upsert's idempotency.
    marker = "<!-- symphony-run:run-abc:start -->"
    head = _body(path).split(marker, 1)[0]
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            marker, "## Conflict\n\nStale warning.\n\n" + marker
        ),
        encoding="utf-8",
    )

    assert fbt.governed_transition("DEV-1", "Todo") is True
    body = _body(path)
    assert "## Conflict" not in body
    assert "Stale warning." not in body
    assert body.count(marker) == 1

    fbt.upsert_run_summary(
        "DEV-1",
        run_id="run-abc",
        workflow_name="ticket-default",
        result="succeeded",
        artifact_dir=".symphony/artifacts/run-abc/",
    )
    body = _body(path)
    assert body.count(marker) == 1
    assert body.count("## Symphony Run") == 1
    assert "- Result: succeeded" in body
    assert head.strip() in body


def test_governed_transition_to_terminal_state_keeps_body(tmp_path):
    root = tmp_path / "board"
    path = _seed(root, state="In Progress", body="Original body.\n\n## Conflict\n\nX.")
    fbt = FileBoardTracker(_tracker(root))

    assert fbt.governed_transition("DEV-1", "Done") is True
    front, body = parse_ticket_file(path)
    assert front["state"] == "Done"
    # Warning strip is gated on entering an active state, as in update_state.
    assert "## Conflict" in body


def test_governed_transition_returns_false_for_unknown_identifier(tmp_path):
    root = tmp_path / "board"
    _seed(root)
    fbt = FileBoardTracker(_tracker(root))
    assert fbt.governed_transition("NOPE-9", "Done") is False
