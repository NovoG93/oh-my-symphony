"""Continuous-improvement heartbeat: runner, registrar, and durable lease.

This module owns the read-only inspection work the orchestrator scheduler
delegates to:

* prove the current baseline without changing the host worktree;
* run fixed argv checks with timeouts, caps, and redaction;
* write machine-owned report sections;
* register failed findings as normal Kanban tickets through the tracker API;
* coordinate concurrent orchestrators through a fakeable advisory lease.

On top of that baseline inspection sits an opt-in set of *improvement modes*
(`continuous_improvement.modes`, all default off) that turn the heartbeat into
an autonomous application-improvement engine:

* ``readiness`` — the original product-readiness checks (implicit default);
* ``blocked_fixes`` — triage Blocked / Human Review tickets into linked fix
  tickets carrying a root-cause note;
* ``security`` — optional dependency/vulnerability scans into patch tickets;
* ``market_research`` / ``feature_improvements`` — an agent turn (supplied by
  the orchestrator as an :data:`AgentRunner`) that proposes improvements.

Every mode's only board write path is a *normal* Kanban ticket created through
the tracker API, so proposals flow through the same pipeline as any other
work. Nothing here dispatches, plans, or executes those tickets.

Keep this module dependency-light: it must not import the orchestrator. The
agent capability the agent-driven modes need is injected as a callable.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import os
import re
import shutil
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:
    from symphony.workflow import ServiceConfig

from ._shell import safe_proc_wait
from .issue import Issue, normalize_state
from .trackers.file import FileBoardTracker
from .workflow.constants import (
    CI_AGENT_MODES,
    CI_MODE_BLOCKED_FIXES,
    CI_MODE_FEATURE_IMPROVEMENTS,
    CI_MODE_MARKET_RESEARCH,
    CI_MODE_READINESS,
    CI_MODE_SECURITY,
    SUPPORTED_CI_MODES,
)

# Lockfile name under `<workflow_dir>/.symphony/`.
LEASE_FILENAME = "continuous_improvement.lock"
# A lease older than this (seconds) is considered abandoned and may be stolen
# — covers an orchestrator that crashed mid-run without releasing.
DEFAULT_LEASE_TTL_SECONDS = 1800.0
DEFAULT_CHECK_TIMEOUT_S = 600.0
DEFAULT_OUTPUT_LIMIT = 12_000
DEFAULT_REPORT_PATH = Path("docs/continuous-improvement/latest.md")
# Durable per-mode cadence bookkeeping. Wall-clock (not monotonic) because a
# weekly market-research cadence has to survive orchestrator restarts.
MODE_STATE_PATH = Path(".symphony/continuous-improvement/mode-state.json")
# Where an operator may override a built-in agent-mode prompt, relative to the
# workflow dir. Mirrors `docs/symphony-prompts/<flavor>/` for stage prompts.
AGENT_PROMPT_DIR = Path("docs/symphony-prompts/ci")
# The agent's single write path in the host worktree: a JSON proposal file
# this module then validates, caps, dedupes, and files as normal tickets.
AGENT_OUTPUT_DIR = Path(".symphony/continuous-improvement/proposals")
# States a proposal is allowed to duplicate into. Anything closed is fair game
# to propose again — the world moved on since it was done.
CLOSED_STATE_KEYS = frozenset(
    {"done", "archive", "archived", "cancelled", "canceled", "closed", "duplicate"}
)
# Label stamped on every ticket the heartbeat files, in addition to the
# long-form `continuous-improvement` label the readiness registrar already
# used. Cheap board filter + the dedupe marker's carrier.
CI_LABEL = "ci"

SECRET_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{8,}|"
    r"(token|api[_-]?key|password|secret)\s*=\s*[^\s]+)"
)


@dataclass(frozen=True)
class CommandExecution:
    argv: tuple[str, ...]
    returncode: int | None
    output: str
    timed_out: bool
    missing: bool
    truncated: bool = False


@dataclass(frozen=True)
class CheckSpec:
    name: str
    argv: tuple[str, ...]
    timeout_s: float = DEFAULT_CHECK_TIMEOUT_S
    optional: bool = False
    not_available_detail: str = ""


@dataclass(frozen=True)
class BaselineProof:
    status: str
    branch: str | None
    sha: str | None
    dirty: bool
    upstream: str | None
    summary: str


@dataclass(frozen=True)
class CheckResult:
    name: str
    command: tuple[str, ...]
    status: str
    summary: str
    output: str = ""
    returncode: int | None = None


@dataclass(frozen=True)
class IssueFinding:
    rubric_item: str
    check_name: str
    command: tuple[str, ...]
    summary: str
    evidence: str
    expected: str
    fix_boundary: str
    verification_commands: tuple[str, ...]
    baseline_branch: str | None
    baseline_sha: str | None
    # Extra labels beyond the registrar's defaults (e.g. `security`).
    labels: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        normalized = _normalize_summary_for_fingerprint(self.summary)
        raw = "\n".join((self.rubric_item, " ".join(self.command), normalized))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class TicketRegistrationResult:
    tickets_created: int = 0
    ticket_ids: tuple[str, ...] = ()
    duplicates: int = 0
    skipped_due_to_cap: int = 0
    unsupported_tracker: bool = False
    skipped_reason: str | None = None


@dataclass(frozen=True)
class _PreparedBaseline:
    proof: BaselineProof
    check_dir: Path
    cleanup_worktree: Path | None = None


@dataclass(frozen=True)
class ImprovementRunResult:
    """Outcome of one heartbeat run, surfaced to the web-API status."""

    tickets_created: int = 0
    verified_branch: str | None = None
    verified_sha: str | None = None
    status: str = "passed"
    skipped_reason: str | None = None
    baseline: BaselineProof | None = None
    checks: tuple[CheckResult, ...] = ()
    ticket_ids: tuple[str, ...] = ()
    started_at: str | None = None
    finished_at: str | None = None
    turns_used: int = 0
    max_turns: int = 0
    # Improvement modes considered/run this heartbeat, in canonical order.
    modes: tuple["ModeOutcome", ...] = ()
    # Request group the proposal tickets of this run were filed under.
    request_id: str | None = None


@dataclass(frozen=True)
class ModeOutcome:
    """One improvement mode's result within a heartbeat run."""

    mode: str
    status: str
    summary: str
    ticket_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImprovementProposal:
    """A board-ready improvement the heartbeat wants a normal worker to do.

    Proposals are *not* executed here. They become ordinary Kanban tickets in
    the board's first active state and flow through the configured pipeline
    (single ticket or stage DAG) like any other request.
    """

    mode: str
    title: str
    goal: str
    scope: str = ""
    acceptance: str = ""
    evidence: str = ""
    priority: int = 2
    # Identifier of a ticket this proposal should unblock (blocked_fixes).
    blocks: str = ""
    labels: tuple[str, ...] = ()

    @property
    def dedupe_key(self) -> str:
        return f"{self.mode}/{_slug(self.title)}"

    @property
    def marker(self) -> str:
        return f"CI Proposal: {self.dedupe_key}"


@dataclass(frozen=True)
class AgentTask:
    """One read-mostly agent turn requested by an agent-driven mode."""

    mode: str
    prompt: str
    cwd: Path
    output_path: Path


# Supplied by the orchestrator (which owns backend construction) so this
# module never imports it. Returns the agent's last message; the real payload
# is the JSON proposal file the prompt tells the agent to write.
AgentRunner = Callable[[AgentTask], Awaitable[str]]


# The scheduler passes the live config, the resolved workflow dir, and a
# `report_phase` callback the runner uses to publish coarse progress
# (e.g. "checking", "verifying") into the status dict. Injectable so tests
# swap in a fake that records the call and returns a canned result.
ImprovementRunner = Callable[
    ["ServiceConfig", Path, Callable[[str], None]], Awaitable[ImprovementRunResult]
]


async def default_improvement_runner(
    cfg: "ServiceConfig",
    workflow_dir: Path,
    report_phase: Callable[[str], None],
    *,
    agent_runner: AgentRunner | None = None,
) -> ImprovementRunResult:
    """Default runner. The orchestrator binds `agent_runner` with a partial so
    the 3-positional `ImprovementRunner` signature (and every test fake that
    implements it) stays unchanged."""
    return await run_continuous_improvement(
        cfg, workflow_dir, report_phase, agent_runner=agent_runner
    )


def _utc_iso_z() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def redact_output(output: str) -> str:
    return SECRET_RE.sub("[REDACTED]", output)


async def _read_stream(stream: Any, limit: int) -> tuple[str, bool]:
    if stream is None:
        return "", False
    chunks: list[bytes] = []
    total_read = 0
    total_stored = 0
    truncated = False
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        total_read += len(chunk)
        if total_stored < limit:
            remaining = limit - total_stored
            chunks.append(chunk[:remaining])
            total_stored += min(len(chunk), remaining)
        if total_read > limit:
            truncated = True
    text = b"".join(chunks).decode("utf-8", errors="replace")
    return text, truncated


async def run_argv(
    argv: tuple[str, ...],
    cwd: Path,
    *,
    timeout_s: float,
    output_limit: int = DEFAULT_OUTPUT_LIMIT,
    proc_factory: Callable[..., Awaitable[Any]] = asyncio.create_subprocess_exec,
    proc_wait: Callable[..., Awaitable[int | None]] = safe_proc_wait,
) -> CommandExecution:
    try:
        proc = await proc_factory(
            *argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return CommandExecution(
            argv, None, f"command not found: {argv[0]}", False, True
        )
    stdout_task = asyncio.create_task(_read_stream(proc.stdout, output_limit))
    stderr_task = asyncio.create_task(_read_stream(proc.stderr, output_limit))
    try:
        returncode = await proc_wait(proc, timeout=timeout_s)
        timed_out = returncode is None
        if timed_out:
            proc.kill()
            await proc_wait(proc, timeout=5)
        stdout, stdout_truncated = await stdout_task
        stderr, stderr_truncated = await stderr_task
    except asyncio.CancelledError:
        proc.kill()
        try:
            await proc_wait(proc, timeout=5)
        finally:
            for task in (stdout_task, stderr_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise
    raw_output = stdout + stderr
    truncated = stdout_truncated or stderr_truncated or len(raw_output) > output_limit
    output = redact_output(raw_output[:output_limit])
    return CommandExecution(argv, returncode, output, timed_out, False, truncated)


def _first_output_line(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:240]
    return ""


def _failed_check_summary(command: str, execution: CommandExecution) -> str:
    detail = _first_output_line(execution.output)
    if detail:
        return f"{command} exited {execution.returncode}: {detail}"
    return f"{command} exited {execution.returncode}"


async def run_predefined_check(
    spec: CheckSpec,
    cwd: Path,
    *,
    run_argv_func: Callable[..., Awaitable[CommandExecution]] = run_argv,
) -> CheckResult:
    execution = await run_argv_func(spec.argv, cwd, timeout_s=spec.timeout_s)
    command = " ".join(spec.argv)
    if execution.missing:
        status = "not_available" if spec.optional else "not_proven"
        return CheckResult(spec.name, spec.argv, status, execution.output)
    if execution.timed_out:
        status = "not_available" if spec.optional else "not_proven"
        return CheckResult(spec.name, spec.argv, status, f"{command} timed out")
    if execution.returncode == 0:
        return CheckResult(spec.name, spec.argv, "passed", "ok", execution.output, 0)
    return CheckResult(
        spec.name,
        spec.argv,
        "failed",
        _failed_check_summary(command, execution),
        execution.output,
        execution.returncode,
    )


async def _git_branch_and_sha(
    cwd: Path,
    *,
    run_argv_func: Callable[..., Awaitable[CommandExecution]],
) -> tuple[CommandExecution, CommandExecution]:
    branch = await run_argv_func(
        ("git", "rev-parse", "--abbrev-ref", "HEAD"),
        cwd,
        timeout_s=30,
    )
    if branch.returncode != 0 or branch.missing or branch.timed_out:
        return branch, CommandExecution(("git", "rev-parse", "HEAD"), None, "", False, True)
    sha = await run_argv_func(("git", "rev-parse", "HEAD"), cwd, timeout_s=30)
    return branch, sha


async def prove_baseline(
    workflow_dir: Path,
    *,
    target_branch: str = "",
    run_argv_func: Callable[..., Awaitable[CommandExecution]] = run_argv,
) -> BaselineProof:
    branch, sha = await _git_branch_and_sha(
        workflow_dir, run_argv_func=run_argv_func
    )
    if branch.returncode != 0 or branch.missing or branch.timed_out:
        return BaselineProof("not_proven", None, None, False, None, branch.output)
    if sha.returncode != 0 or sha.missing or sha.timed_out:
        return BaselineProof(
            "not_proven", branch.output.strip(), None, False, None, sha.output
        )
    current_branch = branch.output.strip()
    current_sha = sha.output.strip()
    target = target_branch.strip()
    if target:
        resolved_target = await run_argv_func(
            ("git", "rev-parse", "--verify", target),
            workflow_dir,
            timeout_s=30,
        )
        if (
            resolved_target.returncode != 0
            or resolved_target.missing
            or resolved_target.timed_out
        ):
            return BaselineProof(
                "not_proven",
                current_branch,
                current_sha,
                False,
                None,
                f"configured target branch {target!r} cannot be resolved",
            )
        if current_branch != target:
            return BaselineProof(
                "not_proven",
                current_branch,
                current_sha,
                False,
                None,
                "current branch "
                f"{current_branch!r} is not configured target branch {target!r}",
            )
    status = await run_argv_func(
        ("git", "status", "--porcelain"), workflow_dir, timeout_s=30
    )
    if status.returncode != 0 or status.missing or status.timed_out:
        return BaselineProof(
            "not_proven", current_branch, current_sha, False, None, status.output
        )
    dirty = bool(status.output.strip())
    if dirty:
        return BaselineProof(
            "not_proven",
            current_branch,
            current_sha,
            True,
            None,
            "dirty worktree blocks baseline proof",
        )
    upstream = await run_argv_func(
        ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"),
        workflow_dir,
        timeout_s=30,
    )
    upstream_text = "none"
    if upstream.returncode == 0:
        upstream_text = upstream.output.strip()
        ahead = await run_argv_func(
            ("git", "rev-list", "--left-right", "--count", "HEAD...@{u}"),
            workflow_dir,
            timeout_s=30,
        )
        if ahead.returncode != 0 or ahead.timed_out or ahead.missing:
            return BaselineProof(
                "not_proven",
                current_branch,
                current_sha,
                False,
                upstream_text,
                "upstream configured but not reachable",
            )
        upstream_text = f"{upstream_text} ({ahead.output.strip()})"
    return BaselineProof(
        "passed",
        current_branch,
        current_sha,
        False,
        upstream_text,
        "clean",
    )


def _worktree_path(workflow_dir: Path, target_branch: str) -> Path:
    safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "-", target_branch).strip("-")
    return (
        workflow_dir
        / ".symphony"
        / "continuous-improvement"
        / "worktrees"
        / f"{safe_target or 'target'}-{os.getpid()}-{time.time_ns()}"
    )


async def _prepare_baseline(
    workflow_dir: Path,
    target_branch: str,
    *,
    run_argv_func: Callable[..., Awaitable[CommandExecution]],
) -> _PreparedBaseline:
    target = target_branch.strip()
    branch, sha = await _git_branch_and_sha(
        workflow_dir, run_argv_func=run_argv_func
    )
    if branch.returncode != 0 or branch.missing or branch.timed_out:
        return _PreparedBaseline(
            BaselineProof("not_proven", None, None, False, None, branch.output),
            workflow_dir,
        )
    current_branch = branch.output.strip()
    current_sha = sha.output.strip() if sha.returncode == 0 else None
    if sha.returncode != 0 or sha.missing or sha.timed_out:
        return _PreparedBaseline(
            BaselineProof(
                "not_proven", current_branch, None, False, None, sha.output
            ),
            workflow_dir,
        )
    if not target or current_branch == target:
        return _PreparedBaseline(
            await prove_baseline(workflow_dir, run_argv_func=run_argv_func),
            workflow_dir,
        )

    resolved_target = await run_argv_func(
        ("git", "rev-parse", "--verify", target),
        workflow_dir,
        timeout_s=30,
    )
    if resolved_target.returncode != 0 or resolved_target.missing or resolved_target.timed_out:
        return _PreparedBaseline(
            BaselineProof(
                "not_proven",
                current_branch,
                current_sha,
                False,
                None,
                f"configured target branch {target!r} cannot be resolved",
            ),
            workflow_dir,
        )

    check_dir = _worktree_path(workflow_dir, target)
    check_dir.parent.mkdir(parents=True, exist_ok=True)
    added = await run_argv_func(
        ("git", "worktree", "add", "--detach", str(check_dir), target),
        workflow_dir,
        timeout_s=120,
    )
    if added.returncode != 0 or added.missing or added.timed_out:
        return _PreparedBaseline(
            BaselineProof(
                "not_proven",
                current_branch,
                current_sha,
                False,
                None,
                f"could not create temporary worktree for {target!r}: {added.output}",
            ),
            workflow_dir,
        )

    proof = await prove_baseline(check_dir, run_argv_func=run_argv_func)
    if proof.status == "passed":
        proof = BaselineProof(
            proof.status,
            target,
            proof.sha,
            proof.dirty,
            proof.upstream,
            f"clean temporary worktree for {target}",
        )
    return _PreparedBaseline(proof, check_dir, check_dir)


async def _cleanup_baseline(
    prepared: _PreparedBaseline,
    workflow_dir: Path,
    *,
    run_argv_func: Callable[..., Awaitable[CommandExecution]],
) -> None:
    if prepared.cleanup_worktree is None:
        return
    await run_argv_func(
        ("git", "worktree", "remove", "--force", str(prepared.cleanup_worktree)),
        workflow_dir,
        timeout_s=120,
    )


def _normalize_summary_for_fingerprint(summary: str) -> str:
    summary = re.sub(r"/(?:private/)?tmp/[^\s]+", "<tmp>", summary)
    summary = re.sub(r"\b\d{4}-\d{2}-\d{2}T[^\s]+", "<timestamp>", summary)
    summary = re.sub(r"\bpid=\d+\b", "pid=<pid>", summary)
    return summary.strip().lower()


def _finding_from_check(check: CheckResult, baseline: BaselineProof) -> IssueFinding:
    return IssueFinding(
        rubric_item=check.name,
        check_name=check.name,
        command=check.command,
        summary=check.summary,
        evidence=check.output,
        expected=f"{' '.join(check.command)} exits 0",
        fix_boundary=f"Fix the product-readiness failure reported by {check.name}.",
        verification_commands=(" ".join(check.command),),
        baseline_branch=baseline.branch,
        baseline_sha=baseline.sha,
    )


def _ticket_body(finding: IssueFinding) -> str:
    command = " ".join(finding.command)
    verification = "\n".join(finding.verification_commands)
    return textwrap.dedent(
        f"""\
        ## Continuous improvement finding

        - Rubric item: {finding.rubric_item}
        - Failing check: `{command}`
        - Baseline: branch `{finding.baseline_branch or 'unknown'}` @ `{finding.baseline_sha or 'unknown'}`

        ### Failure summary

        {finding.summary}

        ### Evidence

        ```
        {finding.evidence}
        ```

        ### Expected behavior

        {finding.expected}

        ### Proposed fix boundary

        {finding.fix_boundary}

        ### Verification

        Re-run before closing:

        ```
        {verification}
        ```

        CI Fingerprint: {finding.fingerprint}
        """
    ).strip() + "\n"


def register_findings(
    cfg: "ServiceConfig",
    workflow_dir: Path,
    findings: tuple[IssueFinding, ...],
    *,
    request: str | None = None,
) -> TicketRegistrationResult:
    ci = cfg.continuous_improvement
    if not findings:
        return TicketRegistrationResult()
    if cfg.tracker.kind != "file" or cfg.tracker.board_root is None:
        return TicketRegistrationResult(
            unsupported_tracker=True, skipped_reason="unsupported_tracker"
        )
    tracker = FileBoardTracker(cfg.tracker)
    active = tracker.fetch_candidate_issues()
    existing = {
        match.group(1)
        for issue in active
        for match in re.finditer(
            r"CI Fingerprint:\s*([a-f0-9]{16})", issue.description or ""
        )
    }
    created: list[str] = []
    duplicates = 0
    for finding in findings:
        if finding.fingerprint in existing:
            duplicates += 1
            continue
        if len(created) >= ci.max_tickets_per_run:
            continue
        title = f"CI: {finding.summary}"[:120]
        identifier, _ = tracker.create_with_next_identifier(
            ci.ticket_prefix,
            title=title,
            state=cfg.tracker.active_states[0] if cfg.tracker.active_states else "Todo",
            priority=1,
            labels=_merge_labels(
                ("continuous-improvement", CI_LABEL, "bug"), finding.labels
            ),
            description=_ticket_body(finding),
            agent_kind=ci.agent_kind or None,
            request=request,
        )
        created.append(identifier)
        existing.add(finding.fingerprint)
    skipped_due_to_cap = max(0, len(findings) - duplicates - len(created))
    return TicketRegistrationResult(
        tickets_created=len(created),
        ticket_ids=tuple(created),
        duplicates=duplicates,
        skipped_due_to_cap=skipped_due_to_cap,
    )


# ---------------------------------------------------------------------------
# improvement modes (opt-in; see docs/continuous-improvement/rubric.md)
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")[:60]


def _merge_labels(base: tuple[str, ...], extra: tuple[str, ...]) -> list[str]:
    out = list(base)
    for label in extra:
        cleaned = label.strip().lower()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def mode_state_path(workflow_dir: Path) -> Path:
    return workflow_dir / MODE_STATE_PATH


def load_mode_state(workflow_dir: Path) -> dict[str, float]:
    """`{mode: last-run epoch seconds}`; unreadable state means "never ran"."""
    try:
        raw = json.loads(mode_state_path(workflow_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for mode, value in raw.items():
        if mode in SUPPORTED_CI_MODES and isinstance(value, (int, float)):
            out[mode] = float(value)
    return out


def save_mode_state(workflow_dir: Path, state: dict[str, float]) -> None:
    path = mode_state_path(workflow_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def due_modes(
    cfg: "ServiceConfig", state: dict[str, float], now: float
) -> tuple[str, ...]:
    """Modes whose per-mode cadence floor has elapsed.

    The orchestrator keeps one heartbeat timer (`interval_ms`); each mode then
    gates itself on `interval_hours` so an expensive weekly market-research
    turn can share a 30-minute readiness heartbeat.
    """
    ci = cfg.continuous_improvement
    out: list[str] = []
    for mode in ci.resolved_modes():
        interval_s = max(ci.interval_hours_for(mode), 0.0) * 3600.0
        last = state.get(mode)
        if interval_s <= 0 or last is None or (now - last) >= interval_s:
            out.append(mode)
    return tuple(out)


def any_mode_due(
    cfg: "ServiceConfig", workflow_dir: Path, *, clock: Callable[[], float] = time.time
) -> bool:
    """Cheap scheduler-side pre-check: is any enabled mode past its floor?

    Lets the orchestrator postpone a heartbeat whose every mode is still
    cooling down (a weekly market-research-only board) without burning a turn.
    """
    return bool(due_modes(cfg, load_mode_state(workflow_dir), clock()))


def _tracker_or_none(cfg: "ServiceConfig") -> FileBoardTracker | None:
    if cfg.tracker.kind != "file" or cfg.tracker.board_root is None:
        return None
    return FileBoardTracker(cfg.tracker)


def open_issues(tracker: FileBoardTracker) -> list[Issue]:
    """Every ticket that is not in a closed state (Blocked counts as open)."""
    return [
        issue
        for issue in tracker.scan_all()
        if normalize_state(issue.state) not in CLOSED_STATE_KEYS
    ]


def next_request_id(issues: list[Issue], *, today: str) -> str:
    """`REQ-CI-<YYYYMMDD>-<n>`, first free n for today across the board."""
    base = f"REQ-CI-{today}"
    used = {issue.request for issue in issues if issue.request}
    index = 1
    while f"{base}-{index}" in used:
        index += 1
    return f"{base}-{index}"


def _proposal_body(proposal: ImprovementProposal, *, request: str) -> str:
    """Chat-intake description format: Goal / Scope / Acceptance / Evidence.

    Composed line by line rather than from a dedent()ed literal: the
    interpolated values are themselves multi-line, and a single unindented
    continuation line would defeat `textwrap.dedent`'s common-prefix scan and
    leak the template's indentation into the ticket body.
    """
    scope = (
        proposal.scope.strip()
        or "Only what the goal requires; no drive-by refactors."
    )
    acceptance = proposal.acceptance.strip() or (
        "The goal is met and verified with the project's own test/lint commands."
    )
    evidence_links = [
        f"- Source: continuous improvement, `{proposal.mode}` mode",
        f"- Report: `{DEFAULT_REPORT_PATH.as_posix()}` (section: modes)",
        f"- Request group: `{request}`",
    ]
    if proposal.blocks:
        evidence_links.append(f"- Unblocks: `{proposal.blocks}`")
    sections = [
        "## Goal",
        proposal.goal.strip(),
        "## Scope",
        scope,
        "## Acceptance criteria",
        acceptance,
        "## Evidence",
        "\n".join(evidence_links),
        proposal.evidence.strip() or "(no further evidence supplied)",
        proposal.marker,
    ]
    return "\n\n".join(sections) + "\n"


def register_proposals(
    cfg: "ServiceConfig",
    proposals: tuple[ImprovementProposal, ...],
    *,
    request: str,
    tracker: FileBoardTracker | None = None,
    existing: list[Issue] | None = None,
) -> TicketRegistrationResult:
    """File proposals as normal tickets: capped, deduped, request-grouped.

    De-duplication is two-layered: the `CI Proposal:` marker in the body (an
    exact re-proposal) and the normalized title of any open ticket (a human
    already filed the same thing).
    """
    if not proposals:
        return TicketRegistrationResult()
    board = tracker or _tracker_or_none(cfg)
    if board is None:
        return TicketRegistrationResult(
            unsupported_tracker=True, skipped_reason="unsupported_tracker"
        )
    ci = cfg.continuous_improvement
    issues = open_issues(board) if existing is None else existing
    seen_markers = {
        match.group(1)
        for issue in issues
        for match in re.finditer(
            r"CI Proposal:\s*(\S+)", issue.description or ""
        )
    }
    seen_titles = {_slug(issue.title) for issue in issues}
    state = cfg.tracker.active_states[0] if cfg.tracker.active_states else "Todo"
    created: list[str] = []
    duplicates = 0
    cap = max(1, ci.max_improvement_tickets_per_run)
    for proposal in proposals:
        if proposal.dedupe_key in seen_markers or _slug(proposal.title) in seen_titles:
            duplicates += 1
            continue
        if len(created) >= cap:
            continue
        identifier, _ = board.create_with_next_identifier(
            ci.ticket_prefix,
            title=proposal.title[:120],
            state=state,
            priority=proposal.priority,
            labels=_merge_labels(
                ("continuous-improvement", CI_LABEL, proposal.mode), proposal.labels
            ),
            description=_proposal_body(proposal, request=request),
            agent_kind=ci.agent_kind or None,
            request=request,
        )
        created.append(identifier)
        seen_markers.add(proposal.dedupe_key)
        seen_titles.add(_slug(proposal.title))
        if proposal.blocks:
            _link_blocker(board, source=proposal.blocks, fix=identifier)
    skipped_due_to_cap = max(0, len(proposals) - duplicates - len(created))
    return TicketRegistrationResult(
        tickets_created=len(created),
        ticket_ids=tuple(created),
        duplicates=duplicates,
        skipped_due_to_cap=skipped_due_to_cap,
    )


def _link_blocker(tracker: FileBoardTracker, *, source: str, fix: str) -> None:
    """Make `source` blocked by the freshly filed `fix` ticket.

    Additive and self-cancelling: a missing source, an existing edge, or the
    degenerate self-edge leaves the board untouched. The reverse edge can
    never close a cycle because `fix` was created moments ago with no
    blockers of its own.
    """
    if source == fix:
        return
    issue = tracker.fetch_issue_full_by_id(source)
    if issue is None:
        return
    current = [b.identifier or b.id for b in issue.blocked_by]
    current = [item for item in current if item]
    if fix in current:
        return
    tracker.update_fields(source, blocked_by=[*current, fix])


# --- blocked_fixes ---------------------------------------------------------

_BLOCKER_SECTION_RE = re.compile(
    r"^##\s+(Blocker|Blocked RCA|QA Failure|Review Findings|Budget Exceeded)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_TRIAGE_STATE_KEYS = ("blocked", "human review")


def _root_cause_note(issue: Issue) -> str:
    """Last blocker-ish section of the ticket body, capped for a ticket quote."""
    body = issue.description or ""
    matches = list(_BLOCKER_SECTION_RE.finditer(body))
    if not matches:
        return "(no blocker section on the source ticket)"
    last = matches[-1]
    tail = body[last.end():]
    next_heading = re.search(r"^##\s+", tail, re.MULTILINE)
    section = tail[: next_heading.start()] if next_heading else tail
    return redact_output(section.strip()[:1500]) or "(empty blocker section)"


def collect_blocked_fix_proposals(
    cfg: "ServiceConfig",
    *,
    tracker: FileBoardTracker | None = None,
) -> tuple[tuple[ImprovementProposal, ...], str]:
    """Turn stuck tickets into fix proposals. Returns `(proposals, summary)`.

    A source ticket that is already blocked by an open ticket is skipped —
    something is already tracking its unblock.
    """
    board = tracker or _tracker_or_none(cfg)
    if board is None:
        return (), "unsupported tracker"
    issues = open_issues(board)
    open_ids = {issue.identifier for issue in issues}
    stuck = [
        issue
        for issue in issues
        if normalize_state(issue.state) in _TRIAGE_STATE_KEYS
    ]
    proposals: list[ImprovementProposal] = []
    for issue in stuck:
        blockers = [b.identifier or b.id for b in issue.blocked_by]
        if any(blocker in open_ids for blocker in blockers if blocker):
            continue
        note = _root_cause_note(issue)
        proposals.append(
            ImprovementProposal(
                mode=CI_MODE_BLOCKED_FIXES,
                title=f"CI fix: unblock {issue.identifier} — {issue.title}"[:120],
                goal=(
                    f"Resolve the root cause keeping `{issue.identifier}` "
                    f"({issue.state}) stuck, then hand it back to the pipeline."
                ),
                scope=(
                    f"In: the root cause of `{issue.identifier}`.\n"
                    "Out: unrelated refactors, and the source ticket's own "
                    "remaining workflow — it resumes normally once unblocked."
                ),
                acceptance=(
                    f"`{issue.identifier}` can leave its stuck state, with the "
                    "fix proven by the project's own verification commands."
                ),
                evidence=(
                    f"Root cause note from `{issue.identifier}`:\n\n"
                    f"```text\n{note}\n```"
                ),
                priority=1,
                blocks=issue.identifier,
                labels=("bug",),
            )
        )
    summary = (
        f"{len(stuck)} stuck ticket(s); {len(proposals)} without an open fix"
        if stuck
        else "no Blocked or Human Review tickets"
    )
    return tuple(proposals), summary


# --- security --------------------------------------------------------------


def security_check_specs(root: Path) -> tuple[CheckSpec, ...]:
    """Ecosystem-detected, optional-by-construction vulnerability scans.

    Optional means a missing scanner is `not_available`, never `failed`: an
    unavailable tool must not manufacture a security ticket.
    """
    specs: list[CheckSpec] = []
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
        specs.append(
            CheckSpec(
                "pip_audit",
                (CHECK_PYTHON, "-m", "pip_audit", "--progress-spinner", "off"),
                optional=True,
                not_available_detail="pip-audit is not installed",
            )
        )
    if (root / "package.json").exists():
        specs.append(
            CheckSpec(
                "npm_audit",
                ("npm", "audit", "--audit-level=high"),
                optional=True,
                not_available_detail="npm is not installed",
            )
        )
    return tuple(specs)


def _security_finding(check: CheckResult, baseline: BaselineProof) -> IssueFinding:
    return IssueFinding(
        rubric_item=f"security/{check.name}",
        check_name=check.name,
        command=check.command,
        summary=check.summary,
        evidence=check.output,
        expected=f"{' '.join(check.command)} reports no actionable advisories",
        fix_boundary=(
            "Patch or pin the affected dependencies. Prefer the smallest "
            "upgrade that clears the advisory; note any that cannot be "
            "upgraded and why."
        ),
        verification_commands=(" ".join(check.command),),
        baseline_branch=baseline.branch,
        baseline_sha=baseline.sha,
        labels=("security",),
    )


# --- agent-driven modes ----------------------------------------------------

_AGENT_PROMPT_FILES = {
    CI_MODE_MARKET_RESEARCH: "market-research.md",
    CI_MODE_FEATURE_IMPROVEMENTS: "feature-improvements.md",
}

_PROMPT_RULES = """\
Rules
- Read only. Do NOT modify any file in this repository except the output file.
- Do NOT create, edit or move board tickets — the heartbeat files them for you.
- Skip anything already covered by the open tickets listed above.
- At most {max_proposals} proposals; zero is a valid, and often correct, answer.

Output
Write JSON to {output_path} (and nothing else), shaped:
{"proposals": [{"title": "...", "goal": "...", "scope": "...",
"acceptance": "...", "evidence": "...", "priority": 1}]}
- title: imperative, <= 100 chars. evidence: URLs and/or repo paths.
- priority: 1 high, 2 normal, 3 low.
Then reply with one line: how many proposals you wrote.
"""

DEFAULT_AGENT_PROMPTS = {
    CI_MODE_MARKET_RESEARCH: """\
Continuous improvement — market research for this application.

{app_context}

Task
1. Survey what comparable products and the wider ecosystem now do that this
   app does not — current trends, expected features, deprecated practices.
2. Keep only gaps that are concrete, valuable to this app's users, and
   buildable inside this repository.

"""
    + _PROMPT_RULES,
    CI_MODE_FEATURE_IMPROVEMENTS: """\
Continuous improvement — feature and code-health review of this application.

{app_context}

Task
1. Inspect the product surface (UX, docs, error paths) and code health
   (duplication, dead code, missing tests, rough edges) of this repository.
2. Keep only improvements a single normal ticket can deliver end to end.

"""
    + _PROMPT_RULES,
}


def _render_prompt(template: str, values: dict[str, str]) -> str:
    """Token replace, not `str.format` — the templates contain JSON braces."""
    for key, value in values.items():
        template = template.replace("{" + key + "}", value)
    return template


def agent_prompt_template(workflow_dir: Path, mode: str) -> str:
    """Operator override from `docs/symphony-prompts/ci/`, else the built-in."""
    filename = _AGENT_PROMPT_FILES.get(mode)
    if filename:
        override = workflow_dir / AGENT_PROMPT_DIR / filename
        try:
            text = override.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text:
            return text
    return DEFAULT_AGENT_PROMPTS.get(mode, "")


def build_app_context(
    workflow_dir: Path, issues: list[Issue], *, readme_limit: int = 2000
) -> str:
    """Succinct "what is this app" block: README head, wiki index, open board."""
    parts: list[str] = []
    for name in ("README.md", "readme.md"):
        candidate = workflow_dir / name
        if candidate.exists():
            try:
                head = candidate.read_text(encoding="utf-8")[:readme_limit]
            except OSError:
                head = ""
            if head.strip():
                parts.append(f"README (head)\n{head.strip()}")
            break
    wiki_index = workflow_dir / "docs" / "llm-wiki" / "INDEX.md"
    if wiki_index.exists():
        try:
            parts.append(
                "Wiki index\n"
                + wiki_index.read_text(encoding="utf-8")[:1500].strip()
            )
        except OSError:
            pass
    titles = [f"- {issue.identifier}: {issue.title}" for issue in issues[:40]]
    parts.append("Open board tickets\n" + ("\n".join(titles) or "(none)"))
    return "\n\n".join(parts)


def parse_agent_proposals(
    mode: str, *, output_path: Path, reply: str
) -> tuple[ImprovementProposal, ...]:
    """Read the agent's JSON proposal file (falling back to its reply text)."""
    payload = _load_json_object(output_path, reply)
    raw_proposals = payload.get("proposals") if isinstance(payload, dict) else None
    if not isinstance(raw_proposals, list):
        return ()
    out: list[ImprovementProposal] = []
    for raw in raw_proposals:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        goal = str(raw.get("goal") or "").strip()
        if not title or not goal:
            continue
        priority = raw.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, int):
            priority = 2
        out.append(
            ImprovementProposal(
                mode=mode,
                title=title[:120],
                goal=redact_output(goal[:2000]),
                scope=redact_output(str(raw.get("scope") or "").strip()[:2000]),
                acceptance=redact_output(
                    str(raw.get("acceptance") or "").strip()[:2000]
                ),
                evidence=redact_output(str(raw.get("evidence") or "").strip()[:2000]),
                priority=min(max(priority, 1), 3),
            )
        )
    return tuple(out)


def _load_json_object(output_path: Path, reply: str) -> dict[str, Any]:
    try:
        return json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        pass
    match = re.search(r"\{.*\}", reply or "", re.DOTALL)
    if match is None:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def run_agent_mode(
    cfg: "ServiceConfig",
    workflow_dir: Path,
    mode: str,
    agent_runner: AgentRunner,
    *,
    issues: list[Issue] | None = None,
) -> tuple[tuple[ImprovementProposal, ...], str]:
    """One agent turn for an agent-driven mode. Returns `(proposals, summary)`."""
    template = agent_prompt_template(workflow_dir, mode)
    if not template:
        return (), f"no prompt template for {mode}"
    board = _tracker_or_none(cfg)
    board_issues = issues if issues is not None else (open_issues(board) if board else [])
    cap = max(1, cfg.continuous_improvement.max_improvement_tickets_per_run)
    output_path = workflow_dir / AGENT_OUTPUT_DIR / f"{mode}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_path.unlink()
    except FileNotFoundError:
        pass
    prompt = _render_prompt(
        template,
        {
            "app_context": build_app_context(workflow_dir, board_issues),
            "output_path": str(output_path),
            "max_proposals": str(cap),
        },
    )
    reply = await agent_runner(
        AgentTask(mode=mode, prompt=prompt, cwd=workflow_dir, output_path=output_path)
    )
    proposals = parse_agent_proposals(mode, output_path=output_path, reply=reply or "")
    return proposals, f"{len(proposals)} proposal(s) from the agent turn"


def _table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _evidence_blocks(checks: tuple[CheckResult, ...]) -> str:
    blocks: list[str] = []
    for check in checks:
        output = check.output.strip()
        if not output:
            continue
        command = " ".join(check.command) if check.command else check.name
        blocks.append(
            textwrap.dedent(
                f"""\
                ### {check.name}

                Command: `{command}`

                ```text
                {output}
                ```
                """
            ).strip()
        )
    return "\n\n".join(blocks) or "(none)"


def _report_sections(result: ImprovementRunResult) -> dict[str, str]:
    baseline = result.baseline or BaselineProof(
        "not_proven", None, None, False, None, "missing"
    )
    checks = result.checks or ()
    check_rows = ["| Check | Result | Detail |", "| --- | --- | --- |"]
    if checks:
        for check in checks:
            check_rows.append(
                f"| {_table_cell(check.name)} | {check.status} | "
                f"{_table_cell(check.summary)} |"
            )
    else:
        check_rows.append("| (none) | - | - |")
    tickets = "\n".join(f"- {ticket}" for ticket in result.ticket_ids) or "(none)"
    mode_rows = ["| Mode | Result | Detail | Tickets |", "| --- | --- | --- | --- |"]
    if result.modes:
        for outcome in result.modes:
            mode_rows.append(
                f"| {_table_cell(outcome.mode)} | {outcome.status} | "
                f"{_table_cell(outcome.summary)} | "
                f"{_table_cell(', '.join(outcome.ticket_ids) or '-')} |"
            )
    else:
        mode_rows.append("| (none) | - | - | - |")
    return {
        "summary": (
            f"- Result: {result.status}\n"
            f"- Tickets created: {result.tickets_created}\n"
            f"- Skipped reason: {result.skipped_reason or 'none'}"
        ),
        "baseline": (
            f"- Branch: {baseline.branch or '(unknown)'}\n"
            f"- SHA: {baseline.sha or '(unknown)'}\n"
            f"- Dirty: {baseline.dirty}\n"
            f"- Upstream: {baseline.upstream or '(none)'}\n"
            f"- Result: {baseline.status}\n"
            f"- Summary: {baseline.summary}"
        ),
        "checks": "\n".join(check_rows),
        "modes": "\n".join(mode_rows)
        + (
            f"\n\nRequest group: `{result.request_id}`"
            if result.request_id
            else ""
        ),
        "evidence": _evidence_blocks(checks),
        "tickets": tickets,
        "meta": (
            f"- Started at: {result.started_at or '(unknown)'}\n"
            f"- Finished at: {result.finished_at or '(unknown)'}\n"
            f"- Turns used / max turns: {result.turns_used} / {result.max_turns}\n"
            f"- Skipped reason: {result.skipped_reason or 'none'}"
        ),
    }


def _replace_section(text: str, name: str, body: str) -> str:
    start = f"<!-- ci:auto:{name}:start -->"
    end = f"<!-- ci:auto:{name}:end -->"
    replacement = f"{start}\n{body}\n{end}"
    if start in text and end in text:
        before, rest = text.split(start, 1)
        _, after = rest.split(end, 1)
        return before + replacement + after
    return text.rstrip() + "\n\n" + replacement + "\n"


def render_report(result: ImprovementRunResult) -> str:
    text = "# Continuous improvement - latest run\n"
    for name, body in _report_sections(result).items():
        text = _replace_section(text, name, body)
    return text


def write_report(path: Path, result: ImprovementRunResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else render_report(result)
    for name, body in _report_sections(result).items():
        text = _replace_section(text, name, body)
    path.write_text(text, encoding="utf-8")


def _check_interpreter() -> str:
    """The Python the default checks run under.

    Checks are spawned as an argv list with no shell, so a bare ``python``
    only resolves if a real executable of that name is on PATH. macOS ships
    none, and most Linux distros ship only ``python3``; a shell *alias* does
    not apply to a subprocess. The literal ``"python"`` therefore produced
    `command not found` for every check, and because that is reported as
    ``not_proven`` rather than ``failed``, continuous improvement quietly
    proved nothing and opened no tickets instead of failing loudly.

    ``sys.executable`` also pins the checks to the environment Symphony was
    installed into, which is where its `pytest`, `ruff` and `pyright` live.
    """
    return sys.executable or shutil.which("python3") or "python"


CHECK_PYTHON = _check_interpreter()

DEFAULT_CHECKS = (
    CheckSpec("pytest", (CHECK_PYTHON, "-m", "pytest", "-q")),
    CheckSpec("ruff", (CHECK_PYTHON, "-m", "ruff", "check", "src", "tests")),
    CheckSpec("pyright", (CHECK_PYTHON, "-m", "pyright")),
)


async def run_continuous_improvement(
    cfg: "ServiceConfig",
    workflow_dir: Path,
    report_phase: Callable[[str], None],
    *,
    run_argv_func: Callable[..., Awaitable[CommandExecution]] = run_argv,
    agent_runner: AgentRunner | None = None,
    clock: Callable[[], float] = time.time,
) -> ImprovementRunResult:
    """Run every improvement mode that is enabled *and* due, then report.

    Check-based modes (`readiness`, `security`) share one proven baseline and
    one registrar pass. Triage and agent-driven modes produce proposals that
    are filed together under a single request group. With no `modes:`
    configured this is exactly the original readiness heartbeat.
    """
    started_at = _utc_iso_z()
    ci = cfg.continuous_improvement
    mode_state = load_mode_state(workflow_dir)
    now_epoch = clock()
    due = due_modes(cfg, mode_state, now_epoch)
    outcomes: list[ModeOutcome] = []
    checks: list[CheckResult] = []
    registration = TicketRegistrationResult()
    baseline: BaselineProof | None = None
    proposals: list[ImprovementProposal] = []
    proposal_modes: list[str] = []
    request_id: str | None = None

    if CI_MODE_READINESS in due or CI_MODE_SECURITY in due:
        report_phase("baseline")
        prepared = await _prepare_baseline(
            workflow_dir,
            cfg.agent.auto_merge_target_branch,
            run_argv_func=run_argv_func,
        )
        baseline = prepared.proof
        try:
            if baseline.status == "passed":
                findings: list[IssueFinding] = []
                if CI_MODE_READINESS in due:
                    report_phase("checks")
                    for spec in DEFAULT_CHECKS:
                        checks.append(
                            await run_predefined_check(
                                spec, prepared.check_dir, run_argv_func=run_argv_func
                            )
                        )
                    checks.extend(
                        [
                            CheckResult(
                                "browser_qa", (), "not_available", "not configured"
                            ),
                            CheckResult(
                                "db_probe", (), "not_available", "not configured"
                            ),
                        ]
                    )
                    findings.extend(
                        _finding_from_check(c, baseline)
                        for c in checks
                        if c.status == "failed"
                    )
                    outcomes.append(
                        _check_outcome(CI_MODE_READINESS, tuple(checks))
                    )
                if CI_MODE_SECURITY in due:
                    report_phase("security")
                    security_checks: list[CheckResult] = []
                    for spec in security_check_specs(prepared.check_dir):
                        security_checks.append(
                            await run_predefined_check(
                                spec, prepared.check_dir, run_argv_func=run_argv_func
                            )
                        )
                    checks.extend(security_checks)
                    findings.extend(
                        _security_finding(c, baseline)
                        for c in security_checks
                        if c.status == "failed"
                    )
                    outcomes.append(
                        _check_outcome(CI_MODE_SECURITY, tuple(security_checks))
                    )
                report_phase("report")
                registration = register_findings(cfg, workflow_dir, tuple(findings))
                report_phase("registrar")
            else:
                for mode in (CI_MODE_READINESS, CI_MODE_SECURITY):
                    if mode in due:
                        outcomes.append(
                            ModeOutcome(mode, "not_proven", baseline.summary)
                        )
        finally:
            await asyncio.shield(
                _cleanup_baseline(
                    prepared, workflow_dir, run_argv_func=run_argv_func
                )
            )

    if CI_MODE_BLOCKED_FIXES in due:
        report_phase(CI_MODE_BLOCKED_FIXES)
        triaged, summary = collect_blocked_fix_proposals(cfg)
        proposals.extend(triaged)
        proposal_modes.append(CI_MODE_BLOCKED_FIXES)
        outcomes.append(
            ModeOutcome(CI_MODE_BLOCKED_FIXES, "passed", summary)
        )

    for mode in CI_AGENT_MODES:
        if mode not in due:
            continue
        report_phase(mode)
        if agent_runner is None:
            outcomes.append(
                ModeOutcome(mode, "not_available", "no agent runner available")
            )
            continue
        try:
            agent_proposals, summary = await run_agent_mode(
                cfg, workflow_dir, mode, agent_runner
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — one mode must not kill the run
            outcomes.append(ModeOutcome(mode, "not_proven", str(exc)[:240]))
            continue
        proposals.extend(agent_proposals)
        proposal_modes.append(mode)
        outcomes.append(ModeOutcome(mode, "passed", summary))

    proposal_registration = TicketRegistrationResult()
    if proposals:
        report_phase("proposals")
        board = _tracker_or_none(cfg)
        if board is None:
            proposal_registration = TicketRegistrationResult(
                unsupported_tracker=True, skipped_reason="unsupported_tracker"
            )
        else:
            request_id = next_request_id(
                open_issues(board), today=time.strftime("%Y%m%d", time.gmtime())
            )
            proposal_registration = register_proposals(
                cfg, tuple(proposals), request=request_id, tracker=board
            )
            outcomes = _attribute_tickets(
                outcomes, tuple(proposals), proposal_registration, proposal_modes
            )

    for mode in due:
        mode_state[mode] = now_epoch
    if due:
        save_mode_state(workflow_dir, mode_state)

    status = _run_status(baseline, tuple(checks), tuple(outcomes))
    ticket_ids = registration.ticket_ids + proposal_registration.ticket_ids
    result = ImprovementRunResult(
        tickets_created=(
            registration.tickets_created + proposal_registration.tickets_created
        ),
        verified_branch=baseline.branch if baseline else None,
        verified_sha=baseline.sha if baseline else None,
        status=status,
        skipped_reason=(
            registration.skipped_reason
            or proposal_registration.skipped_reason
            or (None if due else "no_modes_due")
        ),
        baseline=baseline,
        checks=tuple(checks),
        ticket_ids=ticket_ids,
        started_at=started_at,
        finished_at=_utc_iso_z(),
        max_turns=ci.max_turns,
        modes=tuple(outcomes),
        request_id=request_id,
    )
    write_report(workflow_dir / DEFAULT_REPORT_PATH, result)
    return result


def _check_outcome(mode: str, checks: tuple[CheckResult, ...]) -> ModeOutcome:
    if not checks:
        return ModeOutcome(mode, "not_available", "no checks configured")
    failed = [c.name for c in checks if c.status == "failed"]
    not_proven = [c.name for c in checks if c.status == "not_proven"]
    if failed:
        return ModeOutcome(mode, "failed", f"failed: {', '.join(failed)}")
    if not_proven:
        return ModeOutcome(mode, "not_proven", f"not proven: {', '.join(not_proven)}")
    return ModeOutcome(mode, "passed", f"{len(checks)} check(s) clean")


def _attribute_tickets(
    outcomes: list[ModeOutcome],
    proposals: tuple[ImprovementProposal, ...],
    registration: TicketRegistrationResult,
    proposal_modes: list[str],
) -> list[ModeOutcome]:
    """Map created ticket ids back onto the mode that proposed them.

    `register_proposals` files in proposal order and skips duplicates, so the
    created ids line up with the proposals that survived de-duplication.
    """
    filed = {
        proposal.mode: []
        for proposal in proposals
        if proposal.mode in proposal_modes
    }
    remaining = list(registration.ticket_ids)
    for proposal in proposals:
        if not remaining:
            break
        filed.setdefault(proposal.mode, []).append(remaining.pop(0))
    return [
        (
            dataclasses.replace(
                outcome,
                ticket_ids=tuple(filed.get(outcome.mode, ())),
                summary=(
                    f"{outcome.summary}; "
                    f"{len(filed.get(outcome.mode, ()))} ticket(s) filed"
                ),
            )
            if outcome.mode in proposal_modes
            else outcome
        )
        for outcome in outcomes
    ]


def _run_status(
    baseline: BaselineProof | None,
    checks: tuple[CheckResult, ...],
    outcomes: tuple[ModeOutcome, ...],
) -> str:
    if baseline is not None and baseline.status == "not_proven":
        return "not_proven"
    if any(c.status == "failed" for c in checks):
        return "failed"
    if any(c.status == "not_proven" for c in checks):
        return "not_proven"
    if any(o.status == "not_proven" for o in outcomes):
        return "not_proven"
    return "passed"


@runtime_checkable
class Lease(Protocol):
    """Cross-process advisory lock. All methods must be non-blocking."""

    def acquire(self) -> bool:
        """Try to take the lease. Return True on success, False if held."""
        ...

    def refresh(self) -> None:
        """Renew the lease timestamp during a long-running hold."""
        ...

    def release(self) -> None:
        """Release the lease. Idempotent; safe to call if never acquired."""
        ...


def lease_path_for(workflow_dir: Path) -> Path:
    return workflow_dir / ".symphony" / LEASE_FILENAME


class FileLease:
    """Lockfile-backed :class:`Lease` under the workflow dir.

    The file holds ``{"pid": ..., "acquired_at": <epoch>}``. Acquisition uses
    an exclusive create so two processes racing the empty state cannot both
    win; a lease older than ``ttl_seconds`` is treated as abandoned and
    stolen. This is advisory (best-effort), which is all the heartbeat needs
    — the durable turn counter and idle-board check are the real guards.
    """

    def __init__(
        self,
        path: Path,
        *,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._ttl = ttl_seconds
        self._now = now
        self._held = False
        self._token = f"{os.getpid()}:{id(self)}:{self._now()}"

    def _payload(self) -> dict[str, object]:
        return {
            "pid": os.getpid(),
            "acquired_at": self._now(),
            "token": self._token,
        }

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(self._payload()),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    def _owns_current_file(self) -> bool:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        return data.get("token") == self._token

    def _is_stale(self) -> bool:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            acquired_at = float(data.get("acquired_at", 0.0))
        except (OSError, ValueError, TypeError):
            # Unreadable/corrupt lockfile — treat as abandoned.
            return True
        return (self._now() - acquired_at) >= self._ttl

    def acquire(self) -> bool:
        if self._held:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(
                    self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
                )
            except FileExistsError:
                if not self._is_stale():
                    return False
                try:
                    self._path.unlink()
                except FileNotFoundError:
                    pass
                continue
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._payload(), fh)
                self._held = True
                return True
        return False

    def refresh(self) -> None:
        if not self._held:
            return
        if not self._owns_current_file():
            self._held = False
            return
        self._write()

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        if not self._owns_current_file():
            return
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
