from __future__ import annotations

import dataclasses
import asyncio
import json
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

from symphony.continuous_improvement import (
    CHECK_PYTHON,
    DEFAULT_AGENT_PROMPTS,
    AgentTask,
    BaselineProof,
    CheckResult,
    CheckSpec,
    CommandExecution,
    ImprovementRunResult,
    IssueFinding,
    _AGENT_PROMPT_FILES,
    agent_prompt_template,
    any_mode_due,
    due_modes,
    load_mode_state,
    mode_state_path,
    parse_agent_proposals,
    prove_baseline,
    register_findings,
    render_report,
    run_argv,
    run_continuous_improvement,
    run_predefined_check,
    save_mode_state,
    security_check_specs,
    write_report,
)
from symphony.workflow import build_service_config, load_workflow


class _Stream:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def read(self, _size: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class _Proc:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.pid = None
        self.stdout = _Stream([b"token=sk-secret-value\n"])
        self.stderr = _Stream([b"failure line\n"])
        self.killed = False

    def kill(self) -> None:
        self.killed = True


def _workflow(tmp_path: Path, *, tracker_kind: str = "file"):
    board = tmp_path / "kanban"
    board.mkdir()
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        textwrap.dedent(
            f"""\
            ---
            tracker:
              kind: {tracker_kind}
              board_root: ./kanban
              project_slug: demo
              active_states: [Todo, In Progress]
              terminal_states: [Done, Archive]
            agent:
              kind: codex
            continuous_improvement:
              enabled: true
              interval_ms: 60000
              max_turns: 4
              ticket_prefix: CI
              max_tickets_per_run: 1
              agent_kind: opencode
            ---

            Prompt.
            """
        ),
        encoding="utf-8",
    )
    return build_service_config(load_workflow(workflow))


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.mark.asyncio
async def test_run_argv_uses_exec_args_caps_and_redacts_output(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    async def fake_factory(*argv: str, **kwargs: Any) -> _Proc:
        calls.append((argv, kwargs))
        return _Proc(returncode=1)

    async def fake_wait(proc: _Proc, *, timeout: float | None = None) -> int | None:
        return proc.returncode

    result = await run_argv(
        ("python", "-m", "pytest", "-q"),
        tmp_path,
        timeout_s=1,
        output_limit=24,
        proc_factory=fake_factory,
        proc_wait=fake_wait,
    )

    assert calls[0][0] == ("python", "-m", "pytest", "-q")
    assert "shell" not in calls[0][1]
    assert result.returncode == 1
    assert result.truncated is True
    assert "sk-secret-value" not in result.output
    assert "[REDACTED]" in result.output


@pytest.mark.asyncio
async def test_run_argv_timeout_kills_and_reports_not_proven(tmp_path: Path) -> None:
    proc = _Proc(returncode=None)  # type: ignore[arg-type]
    waits = [None, -9]

    async def fake_factory(*_argv: str, **_kwargs: Any) -> _Proc:
        return proc

    async def fake_wait(_proc: _Proc, *, timeout: float | None = None) -> int | None:
        return waits.pop(0)

    result = await run_argv(
        ("python", "-m", "pytest", "-q"),
        tmp_path,
        timeout_s=0.01,
        proc_factory=fake_factory,
        proc_wait=fake_wait,
    )

    assert result.timed_out is True
    assert proc.killed is True
    assert result.returncode is None


@pytest.mark.asyncio
async def test_run_argv_cancellation_kills_child(tmp_path: Path) -> None:
    proc = _Proc(returncode=None)  # type: ignore[arg-type]
    started = asyncio.Event()
    wait_calls = 0

    async def fake_factory(*_argv: str, **_kwargs: Any) -> _Proc:
        return proc

    async def fake_wait(_proc: _Proc, *, timeout: float | None = None) -> int | None:
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            started.set()
            await asyncio.Event().wait()
        return -9

    task = asyncio.create_task(
        run_argv(
            ("python", "-m", "pytest", "-q"),
            tmp_path,
            timeout_s=60,
            proc_factory=fake_factory,
            proc_wait=fake_wait,
        )
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed is True


@pytest.mark.asyncio
async def test_run_predefined_check_normalizes_result_states(tmp_path: Path) -> None:
    async def failed(_argv, _cwd, **_kwargs):
        return CommandExecution(("python", "-m", "pytest", "-q"), 2, "red", False, False)

    async def timed_out(_argv, _cwd, **_kwargs):
        return CommandExecution(("python", "-m", "pytest", "-q"), None, "", True, False)

    failed_result = await run_predefined_check(
        CheckSpec("pytest", ("python", "-m", "pytest", "-q")),
        tmp_path,
        run_argv_func=failed,
    )
    timeout_result = await run_predefined_check(
        CheckSpec("pytest", ("python", "-m", "pytest", "-q")),
        tmp_path,
        run_argv_func=timed_out,
    )

    assert failed_result.status == "failed"
    assert timeout_result.status == "not_proven"
    assert "red" in failed_result.summary


@pytest.mark.asyncio
async def test_failed_check_summary_keeps_distinct_failure_evidence(
    tmp_path: Path,
) -> None:
    async def first_failure(_argv, _cwd, **_kwargs):
        return CommandExecution(
            ("python", "-m", "pytest", "-q"),
            1,
            "FAILED tests/test_a.py::test_one\n",
            False,
            False,
        )

    async def second_failure(_argv, _cwd, **_kwargs):
        return CommandExecution(
            ("python", "-m", "pytest", "-q"),
            1,
            "FAILED tests/test_b.py::test_two\n",
            False,
            False,
        )

    spec = CheckSpec("pytest", ("python", "-m", "pytest", "-q"))
    first = await run_predefined_check(spec, tmp_path, run_argv_func=first_failure)
    second = await run_predefined_check(spec, tmp_path, run_argv_func=second_failure)
    base = BaselineProof("passed", "dev", "abc123", False, "none", "clean")
    first_finding = IssueFinding(
        rubric_item=first.name,
        check_name=first.name,
        command=first.command,
        summary=first.summary,
        evidence=first.output,
        expected="pytest exits 0",
        fix_boundary="tests",
        verification_commands=("python -m pytest -q",),
        baseline_branch=base.branch,
        baseline_sha=base.sha,
    )
    second_finding = dataclasses.replace(
        first_finding, summary=second.summary, evidence=second.output
    )

    assert "tests/test_a.py::test_one" in first.summary
    assert "tests/test_b.py::test_two" in second.summary
    assert first_finding.fingerprint != second_finding.fingerprint


@pytest.mark.asyncio
async def test_baseline_dirty_status_is_not_proven(tmp_path: Path) -> None:
    async def fake_run(argv, _cwd, **_kwargs):
        outputs = {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): "dev\n",
            ("git", "rev-parse", "HEAD"): "abc123\n",
            ("git", "status", "--porcelain"): " M src/app.py\n",
        }
        return CommandExecution(tuple(argv), 0, outputs[tuple(argv)], False, False)

    baseline = await prove_baseline(tmp_path, run_argv_func=fake_run)

    assert baseline.status == "not_proven"
    assert baseline.branch == "dev"
    assert baseline.sha == "abc123"
    assert "dirty" in baseline.summary


@pytest.mark.asyncio
async def test_baseline_target_branch_mismatch_is_not_proven(tmp_path: Path) -> None:
    async def fake_run(argv, _cwd, **_kwargs):
        outputs = {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "feature\n"),
            ("git", "rev-parse", "HEAD"): (0, "abc123\n"),
            ("git", "rev-parse", "--verify", "dev"): (0, "abc123\n"),
        }
        rc, output = outputs[tuple(argv)]
        return CommandExecution(tuple(argv), rc, output, False, False)

    baseline = await prove_baseline(
        tmp_path,
        target_branch="dev",
        run_argv_func=fake_run,
    )

    assert baseline.status == "not_proven"
    assert baseline.branch == "feature"
    assert "target branch 'dev'" in baseline.summary


def test_write_report_preserves_operator_notes(tmp_path: Path) -> None:
    report = tmp_path / "latest.md"
    report.write_text(
        textwrap.dedent(
            """\
            # Continuous improvement

            operator note

            <!-- ci:auto:summary:start -->
            old
            <!-- ci:auto:summary:end -->
            """
        ),
        encoding="utf-8",
    )
    result = ImprovementRunResult(
        tickets_created=1,
        verified_branch="dev",
        verified_sha="abc123",
        baseline=BaselineProof("passed", "dev", "abc123", False, "none", "clean"),
        checks=(CheckResult("pytest", ("python", "-m", "pytest", "-q"), "passed", "ok"),),
        ticket_ids=("CI-1",),
        started_at="2026-07-05T00:00:00Z",
        finished_at="2026-07-05T00:01:00Z",
        turns_used=1,
        max_turns=4,
    )

    write_report(report, result)
    text = report.read_text(encoding="utf-8")

    assert "operator note" in text
    assert "- Result: passed" in text
    assert "| pytest | passed | ok |" in text
    assert "- CI-1" in text


def test_register_findings_creates_caps_dedupes_and_stamps_agent(tmp_path: Path) -> None:
    cfg = _workflow(tmp_path)
    first = IssueFinding(
        rubric_item="pytest",
        check_name="pytest",
        command=("python", "-m", "pytest", "-q"),
        summary="unit tests failed",
        evidence="FAILED tests/test_demo.py",
        expected="pytest exits 0",
        fix_boundary="tests or source touched by the failure",
        verification_commands=("python -m pytest -q",),
        baseline_branch="dev",
        baseline_sha="abc123",
    )
    second = dataclasses.replace(first, summary="ruff failed")

    result = register_findings(cfg, tmp_path, (first, second))

    assert result.tickets_created == 1
    assert result.ticket_ids == ("CI-1",)
    assert result.skipped_due_to_cap == 1
    text = (tmp_path / "kanban" / "CI-1.md").read_text(encoding="utf-8")
    assert "CI Fingerprint: " in text
    assert "kind: opencode" in text

    duplicate = register_findings(cfg, tmp_path, (first,))
    assert duplicate.tickets_created == 0
    assert duplicate.duplicates == 1
    assert len(list((tmp_path / "kanban").glob("CI-*.md"))) == 1


def test_register_findings_reports_unsupported_tracker(tmp_path: Path) -> None:
    cfg = _workflow(tmp_path)
    cfg = dataclasses.replace(
        cfg, tracker=dataclasses.replace(cfg.tracker, kind="jira", board_root=None)
    )
    finding = IssueFinding(
        rubric_item="pytest",
        check_name="pytest",
        command=("python", "-m", "pytest", "-q"),
        summary="unit tests failed",
        evidence="FAILED",
        expected="pytest exits 0",
        fix_boundary="tests",
        verification_commands=("python -m pytest -q",),
        baseline_branch="dev",
        baseline_sha="abc123",
    )

    result = register_findings(cfg, tmp_path, (finding,))

    assert result.unsupported_tracker is True
    assert result.skipped_reason == "unsupported_tracker"


@pytest.mark.asyncio
async def test_run_continuous_improvement_writes_report_and_registers_failed_check(
    tmp_path: Path,
) -> None:
    cfg = _workflow(tmp_path)
    phases: list[str] = []

    async def fake_run(argv, _cwd, **_kwargs):
        key = tuple(argv)
        outputs = {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "dev\n"),
            ("git", "rev-parse", "HEAD"): (0, "abc123\n"),
            ("git", "status", "--porcelain"): (0, ""),
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                128,
                "no upstream",
            ),
            (CHECK_PYTHON, "-m", "pytest", "-q"): (1, "FAILED tests/test_demo.py\n"),
            (CHECK_PYTHON, "-m", "ruff", "check", "src", "tests"): (0, "ok\n"),
            (CHECK_PYTHON, "-m", "pyright"): (0, "0 errors\n"),
        }
        rc, output = outputs[key]
        return CommandExecution(key, rc, output, False, False)

    result = await run_continuous_improvement(
        cfg,
        tmp_path,
        phases.append,
        run_argv_func=fake_run,
    )

    assert phases == ["baseline", "checks", "report", "registrar"]
    assert result.tickets_created == 1
    assert result.verified_branch == "dev"
    assert result.verified_sha == "abc123"
    assert (tmp_path / "docs" / "continuous-improvement" / "latest.md").exists()
    assert (tmp_path / "kanban" / "CI-1.md").exists()
    assert "FAILED tests/test_demo.py" in render_report(result)


@pytest.mark.asyncio
async def test_run_continuous_improvement_required_check_not_proven_marks_run(
    tmp_path: Path,
) -> None:
    cfg = _workflow(tmp_path)

    async def fake_run(argv, _cwd, **_kwargs):
        key = tuple(argv)
        outputs = {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "dev\n", False),
            ("git", "rev-parse", "HEAD"): (0, "abc123\n", False),
            ("git", "status", "--porcelain"): (0, "", False),
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                128,
                "no upstream",
                False,
            ),
            (CHECK_PYTHON, "-m", "pytest", "-q"): (None, "", True),
            (CHECK_PYTHON, "-m", "ruff", "check", "src", "tests"): (0, "ok\n", False),
            (CHECK_PYTHON, "-m", "pyright"): (0, "0 errors\n", False),
        }
        rc, output, timed_out = outputs[key]
        return CommandExecution(key, rc, output, timed_out, False)

    result = await run_continuous_improvement(
        cfg,
        tmp_path,
        lambda _phase: None,
        run_argv_func=fake_run,
    )

    assert result.status == "not_proven"
    assert result.tickets_created == 0


@pytest.mark.asyncio
async def test_run_continuous_improvement_uses_temp_worktree_for_target_branch(
    tmp_path: Path,
) -> None:
    cfg = _workflow(tmp_path)
    cfg = dataclasses.replace(
        cfg, agent=dataclasses.replace(cfg.agent, auto_merge_target_branch="dev")
    )
    check_cwds: list[Path] = []
    worktree_paths: list[Path] = []

    async def fake_run(argv, cwd, **_kwargs):
        key = tuple(argv)
        cwd_path = Path(cwd)
        if key[:4] == ("git", "worktree", "add", "--detach"):
            worktree_paths.append(Path(key[4]))
            return CommandExecution(key, 0, "", False, False)
        if key[:4] == ("git", "worktree", "remove", "--force"):
            assert worktree_paths and Path(key[4]) == worktree_paths[0]
            return CommandExecution(key, 0, "", False, False)
        if cwd_path == tmp_path:
            outputs = {
                ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "feature\n"),
                ("git", "rev-parse", "HEAD"): (0, "featureabc\n"),
                ("git", "rev-parse", "--verify", "dev"): (0, "devabc\n"),
            }
            rc, output = outputs[key]
            return CommandExecution(key, rc, output, False, False)
        outputs = {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "HEAD\n"),
            ("git", "rev-parse", "HEAD"): (0, "devabc\n"),
            ("git", "status", "--porcelain"): (0, ""),
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                128,
                "no upstream",
            ),
            (CHECK_PYTHON, "-m", "pytest", "-q"): (0, "ok\n"),
            (CHECK_PYTHON, "-m", "ruff", "check", "src", "tests"): (0, "ok\n"),
            (CHECK_PYTHON, "-m", "pyright"): (0, "0 errors\n"),
        }
        rc, output = outputs[key]
        if key[0] == CHECK_PYTHON:
            check_cwds.append(cwd_path)
        return CommandExecution(key, rc, output, False, False)

    result = await run_continuous_improvement(
        cfg,
        tmp_path,
        lambda _phase: None,
        run_argv_func=fake_run,
    )

    assert result.status == "passed"
    assert result.verified_branch == "dev"
    assert result.verified_sha == "devabc"
    assert worktree_paths
    assert check_cwds and all(path == worktree_paths[0] for path in check_cwds)


@pytest.mark.asyncio
async def test_run_continuous_improvement_real_git_target_worktree_e2e(
    tmp_path: Path,
) -> None:
    (tmp_path / "kanban").mkdir()
    (tmp_path / "src" / "demo_pkg").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "demo_pkg" / "__init__.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    (tmp_path / "tests" / "test_failure.py").write_text(
        "def test_failure():\n    assert False, 'e2e heartbeat failure'\n",
        encoding="utf-8",
    )
    (tmp_path / "pyrightconfig.json").write_text(
        json.dumps({"include": ["src"], "typeCheckingMode": "basic"}),
        encoding="utf-8",
    )
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        textwrap.dedent(
            """\
            ---
            tracker:
              kind: file
              board_root: ./kanban
              project_slug: e2e
              active_states: [Todo, In Progress]
              terminal_states: [Done, Archive]
            agent:
              kind: codex
              auto_merge_target_branch: dev
            continuous_improvement:
              enabled: true
              interval_ms: 60000
              max_turns: 2
              ticket_prefix: CI
              max_tickets_per_run: 1
              agent_kind: opencode
            ---

            E2E prompt.
            """
        ),
        encoding="utf-8",
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "ci-e2e@example.test")
    _git(tmp_path, "config", "user.name", "CI E2E")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "initial dev baseline")
    _git(tmp_path, "branch", "-M", "dev")
    _git(tmp_path, "switch", "-q", "-c", "feature")
    cfg = build_service_config(load_workflow(workflow))
    phases: list[str] = []

    result = await run_continuous_improvement(cfg, tmp_path, phases.append)

    ticket_text = (tmp_path / "kanban" / "CI-1.md").read_text(encoding="utf-8")
    report = (tmp_path / "docs" / "continuous-improvement" / "latest.md").read_text(
        encoding="utf-8"
    )
    worktree_root = tmp_path / ".symphony" / "continuous-improvement" / "worktrees"
    remaining_worktrees = list(worktree_root.iterdir()) if worktree_root.exists() else []
    assert result.status == "failed"
    assert result.tickets_created == 1
    assert result.verified_branch == "dev"
    assert phases == ["baseline", "checks", "report", "registrar"]
    assert "e2e heartbeat failure" in ticket_text
    assert "e2e heartbeat failure" in report
    assert "kind: opencode" in ticket_text
    assert "CI Fingerprint:" in ticket_text
    assert "clean temporary worktree for dev" in report
    assert _git(tmp_path, "rev-parse", "--abbrev-ref", "HEAD") == "feature"
    assert remaining_worktrees == []


# --------------------------------------------------------------------------
# improvement modes (opt-in): cadence, triage, agent-driven proposals
# --------------------------------------------------------------------------


def _modes_workflow(
    tmp_path: Path,
    modes: str,
    *,
    extra_lines: tuple[str, ...] = (),
) -> Any:
    # Rendered inside a dedent()ed literal, so extra keys carry its indent.
    extra = ("\n" + " " * 12).join(extra_lines)
    (tmp_path / "kanban").mkdir(exist_ok=True)
    workflow = tmp_path / "WORKFLOW.md"
    workflow.write_text(
        textwrap.dedent(
            f"""\
            ---
            tracker:
              kind: file
              board_root: ./kanban
              project_slug: demo
              active_states: [Todo, In Progress]
              terminal_states: [Done, Archive, Blocked, Human Review]
            agent:
              kind: codex
            continuous_improvement:
              enabled: true
              interval_ms: 60000
              modes: [{modes}]
            {extra}
            ---

            Prompt.
            """
        ),
        encoding="utf-8",
    )
    return build_service_config(load_workflow(workflow))


def _write_ticket(tmp_path: Path, identifier: str, front: str, body: str = "") -> None:
    (tmp_path / "kanban" / f"{identifier}.md").write_text(
        f"---\nid: {identifier}\nidentifier: {identifier}\n{front}---\n\n{body}",
        encoding="utf-8",
    )


def test_due_modes_honors_per_mode_interval(tmp_path: Path) -> None:
    cfg = _modes_workflow(
        tmp_path,
        "readiness, market_research",
        extra_lines=("  mode_interval_hours:", "    market_research: 10"),
    )
    now = 1_000_000.0

    # Never run: everything is due.
    assert due_modes(cfg, {}, now) == ("readiness", "market_research")
    # market_research ran an hour ago; readiness has a zero-hour floor.
    recent = {"readiness": now - 60, "market_research": now - 3600}
    assert due_modes(cfg, recent, now) == ("readiness",)
    # ...and comes back once its 10h floor elapses.
    stale = {"readiness": now - 60, "market_research": now - 11 * 3600}
    assert due_modes(cfg, stale, now) == ("readiness", "market_research")


def test_mode_state_roundtrips_and_survives_corruption(tmp_path: Path) -> None:
    save_mode_state(tmp_path, {"readiness": 12.5, "bogus": 1.0})
    assert load_mode_state(tmp_path) == {"readiness": 12.5}

    mode_state_path(tmp_path).write_text("not json", encoding="utf-8")
    assert load_mode_state(tmp_path) == {}


def test_any_mode_due_gates_the_scheduler(tmp_path: Path) -> None:
    cfg = _modes_workflow(
        tmp_path,
        "market_research",
        extra_lines=("  mode_interval_hours:", "    market_research: 10"),
    )
    assert any_mode_due(cfg, tmp_path, clock=lambda: 1_000.0) is True
    save_mode_state(tmp_path, {"market_research": 1_000.0})
    assert any_mode_due(cfg, tmp_path, clock=lambda: 1_000.0) is False
    assert any_mode_due(cfg, tmp_path, clock=lambda: 1_000.0 + 11 * 3600) is True


@pytest.mark.asyncio
async def test_blocked_fixes_files_linked_fix_ticket(tmp_path: Path) -> None:
    cfg = _modes_workflow(tmp_path, "blocked_fixes")
    _write_ticket(
        tmp_path,
        "TASK-1",
        "title: Ship the importer\nstate: Blocked\npriority: 2\nlabels: []\n",
        "## Blocker\n\nThe importer needs a DB credential nobody has.\n",
    )

    result = await run_continuous_improvement(
        cfg, tmp_path, lambda _phase: None, clock=lambda: 100.0
    )

    assert result.tickets_created == 1
    (fix_id,) = result.ticket_ids
    fix_text = (tmp_path / "kanban" / f"{fix_id}.md").read_text(encoding="utf-8")
    source_text = (tmp_path / "kanban" / "TASK-1.md").read_text(encoding="utf-8")
    assert "unblock TASK-1" in fix_text
    assert "DB credential nobody has" in fix_text
    # Headings must sit at column 0 — interpolated multi-line values used to
    # defeat dedent() and leak the template's indentation into the body.
    for heading in ("## Goal", "## Scope", "## Acceptance criteria", "## Evidence"):
        assert f"\n{heading}\n" in fix_text
    assert result.request_id is not None
    assert f"request: {result.request_id}" in fix_text
    # The source ticket now waits on the fix — a normal DAG edge, not a
    # parallel execution path.
    assert f"- {fix_id}" in source_text.split("blocked_by:")[1]

    # Second run: the source is blocked by an open fix, so nothing new.
    again = await run_continuous_improvement(
        cfg, tmp_path, lambda _phase: None, clock=lambda: 200.0
    )
    assert again.tickets_created == 0


@pytest.mark.asyncio
async def test_market_research_caps_dedupes_and_groups_agent_proposals(
    tmp_path: Path,
) -> None:
    cfg = _modes_workflow(
        tmp_path,
        "market_research",
        extra_lines=("  max_improvement_tickets_per_run: 2",),
    )
    (tmp_path / "README.md").write_text("# Demo app\n\nIt demos.\n", encoding="utf-8")
    _write_ticket(
        tmp_path,
        "TASK-9",
        "title: Add dark mode\nstate: Todo\npriority: 2\nlabels: []\n",
    )
    tasks: list[AgentTask] = []

    async def fake_agent(task: AgentTask) -> str:
        tasks.append(task)
        task.output_path.write_text(
            json.dumps(
                {
                    "proposals": [
                        {
                            "title": "Add dark mode",
                            "goal": "already on the board",
                            "priority": 2,
                        },
                        {
                            "title": "Support passkey sign-in",
                            "goal": "Competitors ship passkeys.",
                            "scope": "In: auth. Out: SSO.",
                            "acceptance": "A user can register a passkey.",
                            "evidence": "https://example.test/passkeys",
                            "priority": 1,
                        },
                        {
                            "title": "Publish an OpenAPI schema",
                            "goal": "Integrators expect one.",
                            "priority": 2,
                        },
                        {
                            "title": "Rewrite everything in Rust",
                            "goal": "over the cap",
                            "priority": 3,
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        return "wrote 4 proposals"

    result = await run_continuous_improvement(
        cfg,
        tmp_path,
        lambda _phase: None,
        agent_runner=fake_agent,
        clock=lambda: 100.0,
    )

    assert [task.mode for task in tasks] == ["market_research"]
    assert "Demo app" in tasks[0].prompt
    assert "TASK-9: Add dark mode" in tasks[0].prompt
    assert str(tasks[0].output_path) in tasks[0].prompt
    # 4 proposals: 1 duplicate title, cap 2, so 1 dropped.
    assert result.tickets_created == 2
    assert result.request_id is not None and result.request_id.startswith("REQ-CI-")
    bodies = [
        (tmp_path / "kanban" / f"{ticket}.md").read_text(encoding="utf-8")
        for ticket in result.ticket_ids
    ]
    assert any("passkey" in body.lower() for body in bodies)
    assert all(f"request: {result.request_id}" in body for body in bodies)
    assert all("- ci" in body for body in bodies)
    assert all("CI Proposal: market_research/" in body for body in bodies)
    assert not any("Rust" in body for body in bodies)

    # Re-proposing the same thing is a no-op thanks to the marker.
    repeat = await run_continuous_improvement(
        cfg,
        tmp_path,
        lambda _phase: None,
        agent_runner=fake_agent,
        clock=lambda: 200.0,
    )
    assert repeat.tickets_created == 0


@pytest.mark.asyncio
async def test_agent_mode_without_runner_is_not_available(tmp_path: Path) -> None:
    cfg = _modes_workflow(tmp_path, "feature_improvements")

    result = await run_continuous_improvement(
        cfg, tmp_path, lambda _phase: None, clock=lambda: 100.0
    )

    assert result.tickets_created == 0
    assert [(o.mode, o.status) for o in result.modes] == [
        ("feature_improvements", "not_available")
    ]


@pytest.mark.asyncio
async def test_agent_mode_failure_does_not_kill_the_run(tmp_path: Path) -> None:
    cfg = _modes_workflow(tmp_path, "feature_improvements")

    async def exploding_agent(_task: AgentTask) -> str:
        raise RuntimeError("backend exploded")

    result = await run_continuous_improvement(
        cfg,
        tmp_path,
        lambda _phase: None,
        agent_runner=exploding_agent,
        clock=lambda: 100.0,
    )

    assert result.status == "not_proven"
    assert result.modes[0].status == "not_proven"
    assert "backend exploded" in result.modes[0].summary


def test_agent_prompt_override_wins_over_builtin(tmp_path: Path) -> None:
    override = tmp_path / "docs" / "symphony-prompts" / "ci" / "market-research.md"
    override.parent.mkdir(parents=True)
    override.write_text("custom research prompt\n", encoding="utf-8")

    assert agent_prompt_template(tmp_path, "market_research") == (
        "custom research prompt"
    )
    assert "market research" in agent_prompt_template(
        tmp_path / "elsewhere", "market_research"
    )


def test_shipped_ci_prompts_match_builtin_defaults() -> None:
    """The docs copies are the operator-visible source of the built-ins."""
    repo_root = Path(__file__).resolve().parents[1]
    for mode, filename in _AGENT_PROMPT_FILES.items():
        shipped = (repo_root / "docs" / "symphony-prompts" / "ci" / filename).read_text(
            encoding="utf-8"
        )
        assert shipped.strip() == DEFAULT_AGENT_PROMPTS[mode].strip()


def test_parse_agent_proposals_falls_back_to_reply_and_skips_junk(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "nope.json"
    proposals = parse_agent_proposals(
        "market_research",
        output_path=missing,
        reply='noise {"proposals": [{"title": "T", "goal": "G", "priority": 9}, '
        '{"title": "", "goal": "no title"}, "junk"]} trailing',
    )

    assert len(proposals) == 1
    assert proposals[0].title == "T"
    # Priority is clamped into the board's 1-3 range.
    assert proposals[0].priority == 3


@pytest.mark.asyncio
async def test_security_mode_files_patch_ticket_from_scan(tmp_path: Path) -> None:
    cfg = _modes_workflow(tmp_path, "security")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    phases: list[str] = []

    async def fake_run(argv, _cwd, **_kwargs):
        key = tuple(argv)
        outputs = {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "dev\n"),
            ("git", "rev-parse", "HEAD"): (0, "abc123\n"),
            ("git", "status", "--porcelain"): (0, ""),
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                128,
                "no upstream",
            ),
            (CHECK_PYTHON, "-m", "pip_audit", "--progress-spinner", "off"): (
                1,
                "requests 2.0.0 GHSA-xxxx\n",
            ),
        }
        rc, output = outputs[key]
        return CommandExecution(key, rc, output, False, False)

    result = await run_continuous_improvement(
        cfg, tmp_path, phases.append, run_argv_func=fake_run, clock=lambda: 100.0
    )

    assert phases == ["baseline", "security", "report", "registrar"]
    assert result.status == "failed"
    assert result.tickets_created == 1
    ticket = (tmp_path / "kanban" / "CI-1.md").read_text(encoding="utf-8")
    assert "GHSA-xxxx" in ticket
    assert "- security" in ticket


def test_security_specs_are_optional_and_ecosystem_detected(tmp_path: Path) -> None:
    assert security_check_specs(tmp_path) == ()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    specs = security_check_specs(tmp_path)
    assert [spec.name for spec in specs] == ["npm_audit"]
    assert all(spec.optional for spec in specs)


@pytest.mark.asyncio
async def test_readiness_only_run_records_mode_outcome(tmp_path: Path) -> None:
    """Default (no `modes:`) still runs readiness — and now reports it."""
    cfg = _workflow(tmp_path)

    async def fake_run(argv, _cwd, **_kwargs):
        key = tuple(argv)
        outputs = {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "dev\n"),
            ("git", "rev-parse", "HEAD"): (0, "abc123\n"),
            ("git", "status", "--porcelain"): (0, ""),
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): (
                128,
                "no upstream",
            ),
            (CHECK_PYTHON, "-m", "pytest", "-q"): (0, "ok\n"),
            (CHECK_PYTHON, "-m", "ruff", "check", "src", "tests"): (0, "ok\n"),
            (CHECK_PYTHON, "-m", "pyright"): (0, "0 errors\n"),
        }
        rc, output = outputs[key]
        return CommandExecution(key, rc, output, False, False)

    result = await run_continuous_improvement(
        cfg, tmp_path, lambda _phase: None, run_argv_func=fake_run, clock=lambda: 5.0
    )

    assert [(o.mode, o.status) for o in result.modes] == [("readiness", "passed")]
    assert load_mode_state(tmp_path) == {"readiness": 5.0}
    assert "| readiness | passed |" in render_report(result)


# ---------------------------------------------------------------------------
# F-16 — cadence is stamped only for modes that produced a real result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cadence_is_not_stamped_for_a_mode_that_could_not_run(
    tmp_path: Path,
) -> None:
    """`not_available` (no agent runner) must retry on the next heartbeat.

    Stamping it made a weekly `market_research` that never ran wait another
    week — the mode silently disappeared for seven days.
    """
    cfg = _modes_workflow(
        tmp_path,
        "market_research",
        extra_lines=("  mode_interval_hours:", "    market_research: 168"),
    )

    result = await run_continuous_improvement(
        cfg, tmp_path, lambda _phase: None, clock=lambda: 1_000.0
    )

    outcomes = {o.mode: o.status for o in result.modes}
    assert outcomes["market_research"] == "not_available"
    assert load_mode_state(tmp_path) == {}
    assert any_mode_due(cfg, tmp_path, clock=lambda: 1_100.0) is True


@pytest.mark.asyncio
async def test_cadence_is_not_stamped_when_the_agent_turn_raises(
    tmp_path: Path,
) -> None:
    cfg = _modes_workflow(tmp_path, "market_research")

    async def _boom(_task: AgentTask) -> str:
        raise RuntimeError("backend exploded")

    result = await run_continuous_improvement(
        cfg,
        tmp_path,
        lambda _phase: None,
        clock=lambda: 1_000.0,
        agent_runner=_boom,
    )

    outcomes = {o.mode: o.status for o in result.modes}
    assert outcomes["market_research"] == "not_proven"
    assert load_mode_state(tmp_path) == {}


@pytest.mark.asyncio
async def test_cadence_is_stamped_for_a_real_result(tmp_path: Path) -> None:
    cfg = _modes_workflow(tmp_path, "blocked_fixes")

    result = await run_continuous_improvement(
        cfg, tmp_path, lambda _phase: None, clock=lambda: 1_000.0
    )

    outcomes = {o.mode: o.status for o in result.modes}
    assert outcomes["blocked_fixes"] == "passed"
    assert load_mode_state(tmp_path) == {"blocked_fixes": 1_000.0}


# ---------------------------------------------------------------------------
# F-17 — blocked_fixes hands the source back once the fix is Done
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_source_returns_to_the_pipeline_when_its_fix_completes(
    tmp_path: Path,
) -> None:
    cfg = _modes_workflow(tmp_path, "blocked_fixes")
    _write_ticket(
        tmp_path,
        "TASK-1",
        "title: Ship the importer\nstate: Blocked\npriority: 2\nlabels: []\n",
        "## Blocker\n\nThe importer needs a DB credential nobody has.\n",
    )

    first = await run_continuous_improvement(
        cfg, tmp_path, lambda _phase: None, clock=lambda: 100.0
    )
    (fix_id,) = first.ticket_ids

    # The fix ticket completes.
    fix_path = tmp_path / "kanban" / f"{fix_id}.md"
    fix_path.write_text(
        fix_path.read_text(encoding="utf-8").replace("state: Todo", "state: Done"),
        encoding="utf-8",
    )

    await run_continuous_improvement(
        cfg, tmp_path, lambda _phase: None, clock=lambda: 200.0
    )

    source_text = (tmp_path / "kanban" / "TASK-1.md").read_text(encoding="utf-8")
    assert "state: Todo" in source_text, (
        "the source ticket stayed Blocked forever after its fix completed"
    )
    assert "## Unblocked" in source_text
    assert fix_id in source_text


@pytest.mark.asyncio
async def test_blocked_source_stays_put_while_its_fix_is_open(tmp_path: Path) -> None:
    cfg = _modes_workflow(tmp_path, "blocked_fixes")
    _write_ticket(
        tmp_path,
        "TASK-1",
        "title: Ship the importer\nstate: Blocked\npriority: 2\nlabels: []\n",
        "## Blocker\n\nStuck.\n",
    )

    await run_continuous_improvement(
        cfg, tmp_path, lambda _phase: None, clock=lambda: 100.0
    )
    await run_continuous_improvement(
        cfg, tmp_path, lambda _phase: None, clock=lambda: 200.0
    )

    source_text = (tmp_path / "kanban" / "TASK-1.md").read_text(encoding="utf-8")
    assert "state: Blocked" in source_text


def test_blocked_source_reopen_ignores_non_ci_blockers(tmp_path: Path) -> None:
    """Only the loop blocked_fixes opened is closed here."""
    from symphony.continuous_improvement import reopen_resolved_blocked_sources

    cfg = _modes_workflow(tmp_path, "blocked_fixes")
    _write_ticket(
        tmp_path,
        "DONE-1",
        "title: An operator ticket\nstate: Done\npriority: 2\nlabels: []\n",
    )
    _write_ticket(
        tmp_path,
        "TASK-1",
        "title: Stuck\nstate: Blocked\npriority: 2\nlabels: []\n"
        "blocked_by:\n  - DONE-1\n",
    )

    reopened, _ = reopen_resolved_blocked_sources(cfg)

    assert reopened == ()
    assert "state: Blocked" in (
        tmp_path / "kanban" / "TASK-1.md"
    ).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# F-29 / F-30 — request-id reuse and 60-char slug collisions
# ---------------------------------------------------------------------------


def test_next_request_id_does_not_reuse_a_closed_groups_id() -> None:
    from symphony.continuous_improvement import next_request_id

    closed = _issue_with_request("CI-1", "Done", "REQ-CI-20260101-1")
    assert (
        next_request_id([closed], today="20260101") == "REQ-CI-20260101-2"
    ), "a closed request group's id was handed to a different batch"


def test_title_dedupe_requires_the_same_length_not_just_a_prefix() -> None:
    from symphony.continuous_improvement import _slug, _title_key

    long_prefix = "Improve the continuous improvement proposal deduplication path"
    a = f"{long_prefix} for market research"
    b = f"{long_prefix} for security scanning and readiness"

    assert _slug(a) == _slug(b), "fixture no longer shares a 60-char prefix"
    assert _title_key(a) != _title_key(b)


def _issue_with_request(identifier: str, state: str, request: str):
    from datetime import datetime, timezone

    from symphony.issue import Issue

    return Issue(
        id=identifier,
        identifier=identifier,
        title=identifier,
        description="",
        priority=2,
        state=state,
        request=request,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
