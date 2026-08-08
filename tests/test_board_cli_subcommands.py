"""`symphony board` subcommand coverage beyond the new --root override.

`test_board_cli.py` pinned the cross-agent `new --root` override. This
file walks the rest of the surface:

  * init       seeds a sample ticket; idempotent on rerun.
  * ls         filters by --state (case-insensitive).
  * new        --workflow path picks board_root from WORKFLOW.md.
  * new        rejects unsupported agent-kind via argparse.
  * mv         transitions a ticket between states.
  * mv         non-zero exit when the ticket is missing.
  * show       prints front-matter + body.
  * show       non-zero exit when the ticket is missing.

Each test isolates to a tmp_path so they're hermetic.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from symphony.cli import board as board_cli


def _make_workflow(tmp_path: Path, board_dir: str = "board") -> Path:
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        "\n".join(
            [
                "---",
                "tracker:",
                "  kind: file",
                f"  board_root: ./{board_dir}",
                "---",
                "prompt",
            ]
        ),
        encoding="utf-8",
    )
    return workflow


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_creates_board_dir_and_sample_ticket(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    board = tmp_path / "fresh-board"
    rc = board_cli.main(["init", str(board)])
    assert rc == 0
    assert (board / "DEMO-001.md").exists()
    captured = capsys.readouterr()
    assert "initialized board at" in captured.out


def test_init_is_idempotent_when_sample_already_exists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    board = tmp_path / "fresh-board"
    board_cli.main(["init", str(board)])
    capsys.readouterr()  # discard first
    rc = board_cli.main(["init", str(board)])
    assert rc == 0
    assert "already initialized" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# new + ls + mv + show happy path against a workflow-rooted board
# ---------------------------------------------------------------------------


def test_new_uses_workflow_board_root_when_no_root_override(tmp_path: Path) -> None:
    workflow = _make_workflow(tmp_path, "my-board")
    rc = board_cli.main(
        [
            "new",
            "--workflow",
            str(workflow),
            "TKT-1",
            "first ticket",
            "--priority",
            "1",
            "--labels",
            "alpha,beta",
        ]
    )
    assert rc == 0
    ticket = tmp_path / "my-board" / "TKT-1.md"
    assert ticket.exists()
    content = ticket.read_text(encoding="utf-8")
    assert "TKT-1" in content
    assert "priority: 1" in content
    # comma-split labels propagated.
    assert "alpha" in content and "beta" in content


def test_new_rejects_unknown_agent_kind_via_argparse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    with pytest.raises(SystemExit):
        board_cli.main(
            [
                "new",
                "--workflow",
                str(workflow),
                "TKT-2",
                "x",
                "--agent-kind",
                "totally-not-an-agent",
            ]
        )
    # argparse error went to stderr.
    assert "invalid choice" in capsys.readouterr().err


def test_ls_filters_by_state_case_insensitively(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    board_cli.main(["new", "--workflow", str(workflow), "TKT-A", "a"])
    board_cli.main(
        ["new", "--workflow", str(workflow), "TKT-B", "b", "--state", "In Progress"]
    )
    capsys.readouterr()  # discard new prints

    rc = board_cli.main(["ls", "--workflow", str(workflow), "--state", "in progress"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TKT-B" in out
    assert "TKT-A" not in out


def test_ls_prints_empty_marker_when_no_tickets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path, "empty-board")
    # Create the board dir but no tickets.
    (tmp_path / "empty-board").mkdir()
    rc = board_cli.main(["ls", "--workflow", str(workflow)])
    assert rc == 0
    assert "no tickets" in capsys.readouterr().out


def test_mv_transitions_ticket_and_prints_arrow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    board_cli.main(["new", "--workflow", str(workflow), "TKT-3", "x"])
    capsys.readouterr()
    rc = board_cli.main(["mv", "--workflow", str(workflow), "TKT-3", "In Progress"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "TKT-3 -> In Progress" in out
    ticket = (tmp_path / "board" / "TKT-3.md").read_text(encoding="utf-8")
    assert "state: In Progress" in ticket


def test_mv_returns_nonzero_when_ticket_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    (tmp_path / "board").mkdir()
    rc = board_cli.main(["mv", "--workflow", str(workflow), "DOES-NOT-EXIST", "Done"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err.lower()


def test_show_prints_front_matter_and_body(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    board_cli.main(
        [
            "new",
            "--workflow",
            str(workflow),
            "TKT-4",
            "show me",
            "--priority",
            "2",
            "--labels",
            "x,y",
            "--description",
            "body line one",
        ]
    )
    capsys.readouterr()
    rc = board_cli.main(["show", "--workflow", str(workflow), "TKT-4"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "# TKT-4" in out
    assert "title: show me" in out
    assert "priority: 2" in out
    # Labels list normalized into a comma-joined display.
    assert "x" in out and "y" in out
    # Body printed after a blank line separator.
    assert "body line one" in out


def test_show_returns_nonzero_when_ticket_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    (tmp_path / "board").mkdir()
    rc = board_cli.main(["show", "--workflow", str(workflow), "MISSING"])
    assert rc == 1
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# new — structured creation flags + DAG validation
# ---------------------------------------------------------------------------


def test_new_writes_blocked_by_request_and_repeatable_labels(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    board_cli.main(["new", "--workflow", str(workflow), "DAG-1", "root"])
    rc = board_cli.main(
        [
            "new",
            "--workflow",
            str(workflow),
            "DAG-2",
            "child",
            "--blocked-by",
            "DAG-1",
            "--request",
            "REQ-1",
            "--label",
            "alpha",
            "--label",
            "beta",
        ]
    )
    assert rc == 0
    content = (tmp_path / "board" / "DAG-2.md").read_text(encoding="utf-8")
    assert "- DAG-1" in content
    assert "request: REQ-1" in content
    assert "alpha" in content and "beta" in content


def test_new_reads_description_from_file_and_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _make_workflow(tmp_path)
    desc = tmp_path / "desc.md"
    desc.write_text("Body from file.", encoding="utf-8")
    rc = board_cli.main(
        ["new", "--workflow", str(workflow), "DESC-1", "t", "--description-file", str(desc)]
    )
    assert rc == 0
    assert "Body from file." in (tmp_path / "board" / "DESC-1.md").read_text(
        encoding="utf-8"
    )

    monkeypatch.setattr("sys.stdin", io.StringIO("Body from stdin."))
    rc = board_cli.main(
        ["new", "--workflow", str(workflow), "DESC-2", "t", "--description-file", "-"]
    )
    assert rc == 0
    assert "Body from stdin." in (tmp_path / "board" / "DESC-2.md").read_text(
        encoding="utf-8"
    )


def test_new_rejects_description_and_description_file_together(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    rc = board_cli.main(
        [
            "new",
            "--workflow",
            str(workflow),
            "DESC-3",
            "t",
            "--description",
            "a",
            "--description-file",
            "b.md",
        ]
    )
    assert rc == 1
    assert "not both" in capsys.readouterr().err


def test_new_rejects_unknown_blocked_by_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    rc = board_cli.main(
        ["new", "--workflow", str(workflow), "DAG-3", "x", "--blocked-by", "GHOST-1"]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "unknown blocked_by" in err and "GHOST-1" in err
    assert not (tmp_path / "board" / "DAG-3.md").exists()


def test_new_rejects_duplicate_identifier(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    board_cli.main(["new", "--workflow", str(workflow), "DUP-1", "first"])
    capsys.readouterr()
    rc = board_cli.main(["new", "--workflow", str(workflow), "DUP-1", "second"])
    assert rc == 1
    assert "already exists" in capsys.readouterr().err


def test_new_rejects_state_outside_tracker_states(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    rc = board_cli.main(
        ["new", "--workflow", str(workflow), "ST-1", "x", "--state", "Nope"]
    )
    assert rc == 1
    assert "unknown state" in capsys.readouterr().err


def test_new_rejects_cycle_and_prints_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    board = tmp_path / "board"
    board.mkdir()
    # CYC-1 already (danglingly) depends on CYC-2; creating CYC-2 <- CYC-1
    # would close the loop.
    (board / "CYC-1.md").write_text(
        "---\nid: CYC-1\ntitle: a\nstate: Todo\nblocked_by: [CYC-2]\n---\n",
        encoding="utf-8",
    )
    rc = board_cli.main(
        ["new", "--workflow", str(workflow), "CYC-2", "b", "--blocked-by", "CYC-1"]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "cycle" in err
    assert "CYC-2 -> CYC-1 -> CYC-2" in err
    assert not (board / "CYC-2.md").exists()


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------


def test_graph_prints_topological_indented_dag_with_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    board_cli.main(["new", "--workflow", str(workflow), "G-1", "root"])
    board_cli.main(
        ["new", "--workflow", str(workflow), "G-2", "mid", "--blocked-by", "G-1"]
    )
    board_cli.main(
        ["new", "--workflow", str(workflow), "G-3", "leaf", "--blocked-by", "G-2"]
    )
    board_cli.main(["mv", "--workflow", str(workflow), "G-1", "Cancelled"])
    board = tmp_path / "board"
    (board / "DANG-1.md").write_text(
        "---\nid: DANG-1\ntitle: dangling\nstate: Todo\nblocked_by: [GHOST-9]\n---\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    rc = board_cli.main(["graph", "--workflow", str(workflow)])

    assert rc == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert "G-1 Cancelled root" in lines
    assert "  G-2 Todo mid <- G-1" in lines
    assert "    G-3 Todo leaf <- G-2" in lines
    assert "WARN DANG-1: blocked_by GHOST-9 not on board" in lines
    assert "WARN G-2: blocker G-1 is Cancelled" in lines
    # Topological: blockers print before their dependents.
    assert lines.index("G-1 Cancelled root") < lines.index("  G-2 Todo mid <- G-1")


def test_graph_filters_by_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    board_cli.main(
        ["new", "--workflow", str(workflow), "R-1", "in", "--request", "REQ-7"]
    )
    board_cli.main(["new", "--workflow", str(workflow), "R-2", "out"])
    capsys.readouterr()

    rc = board_cli.main(["graph", "--workflow", str(workflow), "--request", "REQ-7"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "R-1" in out
    assert "R-2" not in out


def test_graph_exits_nonzero_and_prints_cycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    board = tmp_path / "board"
    board.mkdir()
    (board / "CYC-1.md").write_text(
        "---\nid: CYC-1\ntitle: a\nstate: Todo\nblocked_by: [CYC-2]\n---\n",
        encoding="utf-8",
    )
    (board / "CYC-2.md").write_text(
        "---\nid: CYC-2\ntitle: b\nstate: Todo\nblocked_by: [CYC-1]\n---\n",
        encoding="utf-8",
    )

    rc = board_cli.main(["graph", "--workflow", str(workflow)])

    assert rc == 1
    err = capsys.readouterr().err
    assert "dependency cycle" in err
    assert "CYC-1" in err and "CYC-2" in err


# ---------------------------------------------------------------------------
# F-03 — identifier validation (path escape, shell metacharacters, length)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../evil",
        "../escape",
        "sub/dir",
        "back\\slash",
        "A 5; echo pwned",
        "",
        "   ",
        "1-leading-digit",
        "TKT.1",
        "A" * 65,
    ],
)
def test_new_rejects_malformed_identifier(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], bad_id: str
) -> None:
    workflow = _make_workflow(tmp_path)
    rc = board_cli.main(["new", "--workflow", str(workflow), bad_id, "escape"])
    assert rc == 1
    assert "must match" in capsys.readouterr().err


def test_new_path_escape_writes_nothing_outside_board_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exact probe from the review: `board new ../../evil` must not write."""
    board = tmp_path / "nested" / "board"
    board.mkdir(parents=True)
    rc = board_cli.main(["new", "--root", str(board), "../../evil", "escape"])
    assert rc == 1
    assert not (tmp_path / "evil.md").exists()
    assert not (tmp_path / "nested" / "evil.md").exists()
    assert list(board.glob("*.md")) == []
    assert "must match" in capsys.readouterr().err


def test_new_rejects_malformed_blocked_by_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workflow = _make_workflow(tmp_path)
    board_cli.main(["new", "--workflow", str(workflow), "TKT-1", "root"])
    capsys.readouterr()
    rc = board_cli.main(
        [
            "new",
            "--workflow",
            str(workflow),
            "TKT-2",
            "child",
            "--blocked-by",
            "../../evil",
        ]
    )
    assert rc == 1
    assert "must match" in capsys.readouterr().err


def test_tracker_create_rejects_malformed_identifier_directly(tmp_path: Path) -> None:
    """Defence in depth: the tracker refuses even when the CLI is bypassed."""
    from symphony.errors import BoardDependencyError
    from symphony.trackers.file import FileBoardTracker

    board = tmp_path / "board"
    board.mkdir()
    tracker = FileBoardTracker(board_cli._tracker_from_root(board))
    with pytest.raises(BoardDependencyError):
        tracker.create(identifier="../../evil", title="escape")
    assert not (tmp_path / "evil.md").exists()


def test_tracker_update_fields_rejects_malformed_identifier(tmp_path: Path) -> None:
    from symphony.errors import BoardDependencyError
    from symphony.trackers.file import FileBoardTracker

    board = tmp_path / "board"
    board.mkdir()
    tracker = FileBoardTracker(board_cli._tracker_from_root(board))
    tracker.create(identifier="TKT-1", title="ok")
    with pytest.raises(BoardDependencyError):
        tracker.update_fields("../TKT-1", title="hijacked")
