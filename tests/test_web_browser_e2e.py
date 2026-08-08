from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any, AsyncIterator, cast

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from symphony import chat as chat_module
from symphony.backends import (
    EVENT_OTHER_MESSAGE,
    EVENT_TURN_COMPLETED,
    EVENT_TURN_STARTED,
    BackendInit,
    TurnResult,
)
from symphony.orchestrator import Orchestrator
from symphony.server import build_app
from symphony.workflow import WorkflowState

pytestmark = [
    pytest.mark.browser_e2e,
    pytest.mark.skipif(
        os.environ.get("SYMPHONY_BROWSER_E2E") != "1",
        reason="set SYMPHONY_BROWSER_E2E=1 to run browser E2E",
    ),
]

if os.environ.get("SYMPHONY_BROWSER_E2E") == "1":
    playwright = pytest.importorskip("playwright.async_api")
    async_playwright = playwright.async_playwright
else:
    async_playwright = None


WORKFLOW_TEXT = """---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, "In Progress", Verify, Document]
  terminal_states: ["Human Review", Done, Blocked, Archive]
  state_descriptions:
    Todo: "Triage"
    "In Progress": "Plan + implement"
    Verify: "Review + QA"
    Document: "Docs + wiki write-back"
    "Human Review": "Human confirmation"
    Done: "Complete"
    Blocked: "Blocked"
    Archive: "Archived"

agent:
  kind: codex

prompts:
  stages:
    Todo: ./prompts/stages/todo.md
    "In Progress": ./prompts/stages/in-progress.md
    Verify: ./prompts/stages/verify.md
    Document: ./prompts/stages/document.md
---

QA prompt for {{ issue.identifier }}.
"""


class _StubOrchestrator:
    def __init__(self, workflow_state: WorkflowState) -> None:
        self._workflow_state = workflow_state

    @property
    def workflow_state(self) -> WorkflowState:
        return self._workflow_state

    def snapshot(self) -> dict[str, Any]:
        return {
            "generated_at": "2026-07-02T00:00:00Z",
            "counts": {"running": 0, "retrying": 0},
            "running": [],
            "retrying": [],
            "codex_totals": {
                "input_tokens": 0,
                "cache_input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "seconds_running": 0,
            },
            "rate_limits": None,
        }

    def issue_snapshot(self, _identifier: str) -> dict[str, Any] | None:
        return None

    def recent_runs(
        self, issue_id: str | None = None, limit: int = 50
    ) -> tuple[list[dict[str, Any]], str | None]:
        del issue_id, limit
        return [], None

    def request_refresh(self) -> bool:
        return False

    def continuous_improvement_status(self) -> dict[str, Any]:
        return {
            "turns_used": 0,
            "in_flight": False,
            "current_phase": None,
            "last_result": None,
            "skipped_reason": None,
            "tickets_created": 0,
            "next_due_at": None,
        }

    def find_running_issue_id(self, _identifier: str) -> str | None:
        return None

    def iter_running_issues(self) -> tuple[Any, ...]:
        return ()

    def issue_attention(self, _issue: Any) -> dict[str, str] | None:
        return None


def _ticket(identifier: str, title: str, state: str, priority: int = 2) -> str:
    return f"""---
id: {identifier}
identifier: {identifier}
title: {title}
state: {state}
priority: {priority}
labels:
- e2e
created_at: '2026-07-02T00:00:00Z'
updated_at: '2026-07-02T00:00:00Z'
---

Seed body for {identifier}.
"""


@pytest.fixture()
def board_dir(tmp_path: Path) -> Path:
    (tmp_path / "WORKFLOW.md").write_text(WORKFLOW_TEXT, encoding="utf-8")
    stages = tmp_path / "prompts" / "stages"
    stages.mkdir(parents=True)
    for name in ("todo", "in-progress", "verify", "document"):
        (stages / f"{name}.md").write_text(f"{name} prompt", encoding="utf-8")

    kanban = tmp_path / "kanban"
    kanban.mkdir()
    seeds = (
        ("SEED-REVIEW", "Seed human review card", "Human Review", 2),
        ("SEED-DONE", "Seed done card", "Done", 3),
        ("SEED-BLOCKED", "Seed blocked card", "Blocked", 1),
    )
    for identifier, title, state, priority in seeds:
        (kanban / f"{identifier}.md").write_text(
            _ticket(identifier, title, state, priority),
            encoding="utf-8",
        )
    return tmp_path


@pytest_asyncio.fixture()
async def web_base_url(board_dir: Path) -> AsyncIterator[str]:
    state = WorkflowState(board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    app = build_app(cast(Orchestrator, _StubOrchestrator(state)))
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield str(client.make_url("/")).rstrip("/")
    finally:
        await client.close()


async def _column_titles(page: Any) -> list[str]:
    return await page.locator(
        ".board-columns > .column .column-header .column-title"
    ).evaluate_all("(nodes) => nodes.map((n) => n.textContent.trim())")


async def _assert_no_document_overflow(page: Any, label: str) -> None:
    dims = await page.evaluate(
        """() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
        })"""
    )
    assert dims["scrollWidth"] <= dims["clientWidth"] + 2, (label, dims)


async def _assert_no_element_overflow(page: Any, selector: str, label: str) -> None:
    dims = await page.locator(selector).evaluate(
        """(node) => ({
            scrollWidth: node.scrollWidth,
            clientWidth: node.clientWidth,
        })"""
    )
    assert dims["scrollWidth"] <= dims["clientWidth"] + 2, (label, dims)


async def _exercise_column_scope(page: Any, web_base_url: str) -> None:
    await page.goto(f"{web_base_url}/#/board", wait_until="networkidle")
    await page.locator(".board-columns > .column").first.wait_for()
    assert await _column_titles(page) == ["Todo", "In Progress", "Verify", "Document"]

    terminal_text = await page.locator(".terminal-section").inner_text()
    assert "Human Review" in terminal_text
    assert "Done" in terminal_text
    assert "Blocked" in terminal_text
    assert "Seed human review card" in terminal_text
    await _assert_no_document_overflow(page, "desktop active")

    await page.get_by_role("button", name="All").click()
    await page.wait_for_function(
        "() => document.querySelectorAll('.board-columns > .column').length === 8"
    )
    assert await _column_titles(page) == [
        "Todo",
        "In Progress",
        "Verify",
        "Document",
        "Human Review",
        "Done",
        "Blocked",
        "Archive",
    ]
    assert await page.locator(".terminal-section").count() == 0
    await page.get_by_role("button", name="Active").click()


async def _exercise_issue_crud(page: Any) -> None:
    title = "UI browser E2E card"
    await page.get_by_role("button", name="+ New Issue").click()
    modal = page.locator(".modal-form").last
    await modal.get_by_label("Title").fill(title)
    await modal.get_by_label("Description").fill(
        "## QA evidence\n\n"
        "| Check | Result |\n"
        "| :--- | :---: |\n"
        "| Markdown table | **PASS** |\n"
        "| Safety | <script>window.__xss=1</script> |"
    )
    await modal.get_by_label("State").select_option("Human Review")
    await modal.get_by_label("Labels").fill("browser, e2e")
    await modal.get_by_label("ID prefix").fill("UIE2E")
    await modal.get_by_role("button", name="Create issue").click()
    await page.locator(".card", has_text=title).wait_for()

    await page.locator(".card", has_text=title).click()
    drawer = page.locator("#drawer-panel")
    await drawer.locator(".description-body .md-table").wait_for()
    assert await drawer.locator(".description-body .md-heading").count() == 1
    assert await drawer.locator(".description-body thead th").all_text_contents() == ["Check", "Result"]
    cells = await drawer.locator(".description-body tbody td").all_text_contents()
    assert cells[:2] == ["Markdown table", "PASS"]
    assert "<script>window.__xss=1</script>" in cells
    assert await drawer.locator(".description-body script").count() == 0
    await drawer.locator(".drawer-title-input").fill(f"{title} updated")
    await drawer.locator(".drawer-title-input").press("Enter")
    await page.locator(".card", has_text=f"{title} updated").wait_for()

    await drawer.get_by_role("button", name="Delete issue").click()
    await page.locator(".modal-form").last.get_by_role("button", name="Delete").click()
    await page.locator(".card", has_text=f"{title} updated").wait_for(
        state="detached"
    )


async def _exercise_settings_layout(page: Any, web_base_url: str) -> None:
    for width in (1440, 1201, 1100, 769, 390):
        await page.set_viewport_size({"width": width, "height": 900})
        await page.goto(f"{web_base_url}/#/settings", wait_until="networkidle")
        await page.locator(".settings-card").first.wait_for()
        assert await page.locator(".settings-body").get_by_role("heading", level=2).all_text_contents() == [
            "Workspace & interface",
            "Workflow setup",
            "Automation",
        ]
        await _assert_no_document_overflow(page, f"settings at {width}px")
        cards = page.locator(".settings-card")
        for index in range(await cards.count()):
            dims = await cards.nth(index).evaluate(
                "node => ({scrollWidth: node.scrollWidth, clientWidth: node.clientWidth})"
            )
            assert dims["scrollWidth"] <= dims["clientWidth"] + 2, (width, index, dims)
        control_widths = await page.locator(
            ".settings-card select, .settings-card input[type=number]"
        ).evaluate_all("nodes => nodes.map(node => node.clientWidth)")
        assert min(control_widths) >= 150, (width, control_widths)


async def _exercise_mobile_layout(page: Any, web_base_url: str) -> None:
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(f"{web_base_url}/#/board", wait_until="networkidle")
    await page.locator(".board-columns > .column").first.wait_for()
    await page.locator(".mobile-lane-tabs").wait_for()
    assert await page.locator(".board-columns > .column").count() == 1
    assert await page.locator(".add-column-ghost").count() == 0
    await page.get_by_role("tab", name="Document").click()
    assert await _column_titles(page) == ["Document"]
    await _assert_no_document_overflow(page, "mobile active")
    await _assert_no_element_overflow(page, "#board-scroll", "mobile lane tabs")


# ---------------------------------------------------------------------------
# Git + Chat pages
#
# Both pages mutate real state, so this half runs against its own board: a
# git repo with a task branch and a local bare remote, and a fake chat
# backend. The fake keeps the run deterministic and free — a real agent CLI
# would spend tokens and could answer differently on every run — while still
# emitting the exact stream-json frames the UI parses.
# ---------------------------------------------------------------------------


CHAT_WORKFLOW_TEXT = """---
tracker:
  kind: file
  board_root: ./kanban
  active_states: [Todo, "In Progress"]
  terminal_states: [Done, Archive]

agent:
  kind: claude
---

QA prompt for {{ issue.identifier }}.
"""

_ANSWER = "Two files: `calc.py` and `README.md`."
_DELTAS = ("Two files: ", "`calc.py` ", "and `README.md`.")


class _FakeChatBackend:
    """Emits the claude stream-json frames the chat page consumes."""

    def __init__(self, init: BackendInit) -> None:
        self.init = init
        self.turns: list[str] = []
        self.stopped = False
        # When set, the turn pauses after the deltas so the test can observe
        # the half-typed bubble before the finished message replaces it.
        self.stream_gate: asyncio.Event | None = None

    async def start(self) -> None:
        return None

    async def initialize(self) -> dict[str, Any]:
        return {}

    async def start_session(
        self, *, initial_prompt: str, issue_title: str | None
    ) -> str:
        del initial_prompt, issue_title
        return "pending"

    async def run_turn(self, *, prompt: str, is_continuation: bool) -> TurnResult:
        del is_continuation
        self.turns.append(prompt)
        await self._emit(EVENT_TURN_STARTED, {})
        for chunk in _DELTAS:
            await self._emit(
                EVENT_OTHER_MESSAGE,
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": chunk},
                    },
                },
            )
        if self.stream_gate is not None:
            await self.stream_gate.wait()
        await self._emit(
            EVENT_OTHER_MESSAGE,
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": _ANSWER}]},
            },
        )
        await self._emit(EVENT_TURN_COMPLETED, {"message": _ANSWER})
        return TurnResult(
            status=EVENT_TURN_COMPLETED, turn_id="t", last_message=_ANSWER
        )

    async def stop(self) -> None:
        self.stopped = True

    async def _emit(self, event: str, payload: dict[str, Any]) -> None:
        await self.init.on_event(
            {
                "event": event,
                "timestamp": "2026-08-06T00:00:00Z",
                "payload": payload,
                "usage": {"total_tokens": 1234},
                "rate_limits": None,
                "agent_pid": 4321,
            }
        )

    @property
    def session_id(self) -> str | None:
        return "agent-sess-1"

    @property
    def pid(self) -> int | None:
        return 4321

    @property
    def latest_usage(self) -> dict[str, int]:
        return {"total_tokens": 1234}

    @property
    def latest_rate_limits(self) -> dict[str, Any] | None:
        return None

    def is_progress_event(self, _event: dict[str, Any]) -> bool:
        return True


@pytest.fixture()
def chat_backends(monkeypatch: pytest.MonkeyPatch) -> list[_FakeChatBackend]:
    built: list[_FakeChatBackend] = []

    def _build(init: BackendInit) -> _FakeChatBackend:
        backend = _FakeChatBackend(init)
        built.append(backend)
        return backend

    monkeypatch.setattr(chat_module, "build_backend", _build)
    return built


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(cwd),
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        },
    )


@pytest.fixture()
def git_board_dir(tmp_path: Path) -> Path:
    root = tmp_path / "gitboard"
    root.mkdir()
    (root / "WORKFLOW.md").write_text(CHAT_WORKFLOW_TEXT, encoding="utf-8")
    kanban = root / "kanban"
    kanban.mkdir()
    (kanban / "E2E-1.md").write_text(
        _ticket("E2E-1", "Seed task branch card", "Todo"), encoding="utf-8"
    )
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init board")
    _git(root, "checkout", "-q", "-b", "symphony/E2E-1")
    (root / "feature.py").write_text("print('hi')\n", encoding="utf-8")
    _git(root, "add", "feature.py")
    _git(root, "commit", "-q", "-m", "E2E-1: feature")
    _git(root, "checkout", "-q", "main")
    _git(root, "init", "-q", "--bare", str(tmp_path / "origin.git"))
    _git(root, "remote", "add", "origin", str(tmp_path / "origin.git"))
    return root


@pytest_asyncio.fixture()
async def git_web_base_url(
    git_board_dir: Path, chat_backends: list[_FakeChatBackend]
) -> AsyncIterator[str]:
    del chat_backends  # ordering only: patch the backend before the server runs
    state = WorkflowState(git_board_dir / "WORKFLOW.md")
    cfg, err = state.reload()
    assert err is None and cfg is not None
    app = build_app(cast(Orchestrator, _StubOrchestrator(state)))
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield str(client.make_url("/")).rstrip("/")
    finally:
        await client.close()


async def _exercise_git_actions(page: Any, base_url: str, board: Path) -> None:
    await page.goto(f"{base_url}/#/git", wait_until="networkidle")
    row = page.locator(".branch-row", has_text="symphony/E2E-1")
    await row.wait_for()
    assert "E2E-1 · Todo" in await row.inner_text()

    # Push reaches the bare remote.
    await row.get_by_role("button", name="Push").click()
    await page.locator(".toast", has_text="Pushed symphony/E2E-1").wait_for()
    remote_heads = subprocess.run(
        ["git", "ls-remote", "--heads", "origin"],
        cwd=str(board),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "refs/heads/symphony/E2E-1" in remote_heads

    # An unmerged branch pre-checks Force; clearing it must be refused, and
    # the refusal stays inside the modal so the choice can be corrected.
    await page.locator(".branch-row", has_text="symphony/E2E-1").get_by_role(
        "button", name="Delete"
    ).click()
    modal = page.locator(".modal-form").last
    assert "is NOT merged" in await modal.inner_text()
    force = modal.locator("#git-delete-force")
    assert await force.is_checked()
    await force.uncheck()
    await modal.get_by_role("button", name="Delete branch").click()
    await modal.locator(".modal-error", has_text="not merged into main").wait_for()

    await force.check()
    await modal.get_by_role("button", name="Delete branch").click()
    await page.locator(".toast", has_text="Deleted symphony/E2E-1").wait_for()
    await page.locator(".branch-row", has_text="symphony/E2E-1").wait_for(
        state="detached"
    )

    # Pushing the shared target demands its name typed back.
    await page.get_by_role("button", name="Push target").click()
    target_modal = page.locator(".modal-form").last
    await target_modal.locator("input.input").fill("wrong")
    await target_modal.get_by_role("button", name="Push", exact=True).click()
    await target_modal.locator(".modal-error", has_text="requires confirm").wait_for()
    await target_modal.locator("input.input").fill("main")
    await target_modal.get_by_role("button", name="Push", exact=True).click()
    await page.locator(".toast", has_text="Pushed main").wait_for()


async def _exercise_chat_session(
    page: Any, base_url: str, backends: list[_FakeChatBackend]
) -> None:
    await page.goto(f"{base_url}/#/chat", wait_until="networkidle")
    await page.locator(".chat-session-bar").wait_for()
    assert await page.locator(".chat-tab").count() == 0

    # A budget of one turn so the advisory warning is reachable in one send.
    await page.get_by_role("button", name="+ New").click()
    modal = page.locator(".modal-form").last
    await modal.get_by_label("Mode").select_option("qa")
    await modal.get_by_label("Warn after turns (0 = no limit)").fill("1")
    await modal.get_by_role("button", name="Start session").click()
    await page.locator(".chat-tab").first.wait_for()
    assert "claude" in await page.locator(".chat-controls").inner_text()
    assert "0/1 turns" in await page.locator(".chat-budget-chip").inner_text()

    gate = asyncio.Event()
    backends[-1].stream_gate = gate

    await page.locator(".chat-input").fill("what files are here?")
    await page.get_by_role("button", name="Send", exact=True).click()
    await page.locator(".chat-user .chat-bubble").wait_for()

    # Mid-turn: the answer is still arriving token by token.
    live = page.locator(".chat-bubble-live")
    await live.wait_for()
    await page.wait_for_function(
        "() => (document.querySelector('.chat-bubble-live')||{}).textContent"
        f" === {''.join(_DELTAS)!r}"
    )
    assert await page.locator(".chat-input").is_disabled()

    gate.set()
    await live.wait_for(state="detached")
    bubbles = page.locator(".chat-agent .chat-bubble")
    assert await bubbles.count() == 1
    finished = await bubbles.first.inner_text()
    assert "calc.py" in finished and "README.md" in finished
    # The finished message is markdown, not the raw delta text.
    assert await bubbles.first.locator("code").count() >= 1

    # Budget is advisory: the chip goes red, the composer stays usable.
    await page.locator(".chat-budget-chip.over").wait_for()
    await page.locator(".chat-status", has_text="chat budget reached").wait_for()
    assert not await page.locator(".chat-input").is_disabled()


async def _exercise_chat_multi_session(page: Any) -> None:
    await page.get_by_role("button", name="+ New").click()
    modal = page.locator(".modal-form").last
    await modal.get_by_label("Mode").select_option("edit")
    await modal.get_by_role("button", name="Start session").click()
    await page.wait_for_function(
        "() => document.querySelectorAll('.chat-tab').length === 2"
    )
    # The second session starts empty; the first one's transcript is intact.
    assert await page.locator(".chat-agent .chat-bubble").count() == 0
    await page.locator(".chat-tab").first.click()
    await page.locator(".chat-agent .chat-bubble").first.wait_for()
    assert await page.locator(".chat-tab.active").count() == 1


async def _exercise_chat_reattach(page: Any) -> None:
    await page.locator(".chat-tab").first.click()
    await page.locator(".chat-agent .chat-bubble").first.wait_for()
    await page.get_by_role("button", name="Stop").click()
    await page.locator(".chat-resume-select").wait_for()

    options = page.locator(".chat-resume-select option")
    await page.wait_for_function(
        "() => document.querySelectorAll('.chat-resume-select option').length >= 2"
    )
    value = await options.nth(1).get_attribute("value")
    await page.locator(".chat-resume-select").select_option(value)
    await page.locator(".toast", has_text="Session reattached").wait_for()
    # The conversation comes back from the JSONL, not from memory.
    await page.locator(".chat-agent .chat-bubble").first.wait_for()
    assert "calc.py" in await page.locator(".chat-agent .chat-bubble").first.inner_text()


async def test_web_git_and_chat_browser_e2e(
    git_web_base_url: str,
    git_board_dir: Path,
    chat_backends: list[_FakeChatBackend],
) -> None:
    assert async_playwright is not None
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium unavailable: {exc}")
        page = await browser.new_page(viewport={"width": 1440, "height": 960})
        page_errors: list[str] = []
        console_errors: list[str] = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text)
            if msg.type == "error"
            else None,
        )
        try:
            await _exercise_git_actions(page, git_web_base_url, git_board_dir)
            await _exercise_chat_session(page, git_web_base_url, chat_backends)
            await _exercise_chat_multi_session(page)
            await _exercise_chat_reattach(page)
            # Unlike the board flow, this one deliberately drives rejected
            # requests (unmerged delete, mistyped push confirmation, snapshot
            # of a just-stopped session). The browser logs each as a resource
            # error, so only real exceptions and other console output fail.
            assert page_errors == []
            unexpected = [
                text for text in console_errors if "Failed to load resource" not in text
            ]
            assert unexpected == []
        finally:
            await browser.close()


async def test_web_board_browser_e2e(web_base_url: str) -> None:
    assert async_playwright is not None
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright Chromium unavailable: {exc}")
        page = await browser.new_page(viewport={"width": 1440, "height": 960})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on(
            "console",
            lambda msg: errors.append(msg.text) if msg.type == "error" else None,
        )
        try:
            await _exercise_column_scope(page, web_base_url)
            await _exercise_issue_crud(page)
            await _exercise_settings_layout(page, web_base_url)
            await _exercise_mobile_layout(page, web_base_url)
            assert errors == []
        finally:
            await browser.close()
