"""Installed-wheel PM journeys. Required Node proof enables the browser run too.

By default this remains opt-in via SYMPHONY_BROWSER_E2E=1. A clean wheel venv
is built once per session; SYMPHONY_PM_DEMO_WHEEL can reuse a wheel file,
including a separately built base-code wheel for the red proof. Both paths
create a clean venv and install only the wheel and its dependencies; neither
installs pm-exchange. All generated projects and hostile config live in pytest
temporary folders, never in an SDLC scratch fixture.
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlparse
from urllib.request import urlopen

import pytest
import yaml

from tests._pm_support import skip_or_fail

pytestmark = pytest.mark.browser_e2e
ROOT = Path(__file__).resolve().parents[1]
REQUIRED = "SYMPHONY_PM_NODE_REQUIRED"
CLEAN_KEYS = {
    "SYMPHONY_LANG", "SYMPHONY_API_TOKEN", "SYMPHONY_TRUSTED_ORIGINS",
    "SYMPHONY_SERVICE_INSTANCE_ID", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_COUNT", "PYTHONPATH", "VIRTUAL_ENV",
}
# D7 labels, independently pinned in both user-facing languages.
LABELS = {
    "workspace": [("New change", "새 변경"), ("Repair workspace", "작업공간 복구"),
                  ("Convert legacy plan", "기존 계획 변환"),
                  ("Edit in external editor", "외부 편집기로 열기"),
                  ("Validate", "검증"), ("Approve revision", "리비전 승인")],
    "idea": [("Edit source", "소스 편집"), ("Save draft", "초안 저장"),
             ("Planning assistant", "기획 도우미"), ("Add decision", "결정 추가")],
    "requirements": [("Add capability", "기능 요구사항 추가"),
                     ("Edit requirements", "요구사항 편집"),
                     ("Add requirement", "요구사항 추가"), ("Add scenario", "시나리오 추가"),
                     ("Waive coverage", "커버리지 면제"), ("Revoke waiver", "면제 취소")],
    "design": [("Storyboard", "스토리보드"), ("Save storyboard", "스토리보드 저장"),
               ("Add storyboard", "스토리보드 추가"), ("Add contract", "계약 추가")],
    "plan": [("Work breakdown", "작업 분해"), ("Dependencies", "의존성"),
             ("Add group", "그룹 추가"), ("Add child", "하위 추가"),
             ("Delete", "삭제"), ("Cancel task", "작업 취소"), ("Undo", "실행 취소"),
             ("Download export", "내보내기 다운로드"), ("Preview import", "가져오기 미리보기"),
             ("Preview approved handoff", "승인된 전달 미리보기"),
             ("Preview archive", "아카이브 미리보기")],
    "code": [("Run verification", "검증 실행"), ("Submit completion evidence", "완료 증거 제출"),
             ("Review Git commit", "Git 커밋 검토"), ("Open run evidence", "실행 증거 열기"),
             ("Accept verified revision", "검증된 리비전 인수")],
}
TABS = {"idea": ("Idea", "아이디어"), "requirements": ("Requirements", "요구사항"),
        "design": ("Design", "설계"), "plan": ("Plan", "계획"), "code": ("Code Files", "코드 파일")}


def _label(text, lang):
    return next((ko for entries in LABELS.values() for en, ko in entries if en == text), text) if lang == "ko" else text


@pytest.fixture(autouse=True)
def environment_unchanged():
    before = dict(os.environ)
    before.pop("PYTEST_CURRENT_TEST", None)
    yield
    after = dict(os.environ)
    after.pop("PYTEST_CURRENT_TEST", None)
    assert after == before


@pytest.fixture(autouse=True)
def clean_environment(environment_unchanged, tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key in CLEAN_KEYS or key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("SYMPHONY_PROJECTS_FILE", str(tmp_path / "unused-projects.json"))


def _unavailable(reason) -> NoReturn:
    # An explicit browser opt-in or required Node run cannot succeed by skipping.
    flag = REQUIRED if os.environ.get(REQUIRED) == "1" else "SYMPHONY_BROWSER_E2E"
    skip_or_fail(reason, flag=flag)


@pytest.fixture(scope="session")
def installed_python(tmp_path_factory):
    if os.environ.get(REQUIRED) != "1" and os.environ.get("SYMPHONY_BROWSER_E2E") != "1":
        _unavailable("Set SYMPHONY_BROWSER_E2E=1 or SYMPHONY_PM_NODE_REQUIRED=1 for installed Chrome proof")
    node = shutil.which("node")
    if not node:
        _unavailable("PM browser demo needs Node.js 20.19.0 or newer on PATH (found: none)")
    result = subprocess.run([node, "-p", "process.version"], capture_output=True, text=True, timeout=10)
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)\s*", result.stdout)
    if result.returncode or not match or tuple(map(int, match.groups())) < (20, 19, 0):
        _unavailable(f"PM browser demo needs Node.js 20.19.0 or newer (found: {result.stdout.strip()})")
    builder = shutil.which("python3.12")
    if not builder:
        _unavailable("Installed PM browser proof needs python3.12")
    folder = tmp_path_factory.mktemp("pm-installed-wheel")
    reused = os.environ.get("SYMPHONY_PM_DEMO_WHEEL")
    if reused:
        wheel = Path(reused).absolute()
        assert wheel.is_file() and wheel.suffix == ".whl", wheel
    else:
        wheels = folder / "wheel"
        wheels.mkdir()
        _command([builder, "-m", "pip", "wheel", ".", "--no-deps", "--wheel-dir", str(wheels)], ROOT, folder / "wheel.log")
        wheel, = wheels.glob("oh_my_symphony-*.whl")
    _command([builder, "-m", "venv", str(folder / "venv")], folder, folder / "venv.log")
    executable = folder / "venv/bin/python"
    _command([str(executable), "-m", "pip", "install", "--force-reinstall", str(wheel)], folder, folder / "install.log")
    assert executable.is_file(), executable
    check = subprocess.run([str(executable), "-c", "import importlib.util, symphony; print(symphony.__file__); assert importlib.util.find_spec('openpyxl') is None"],
                           capture_output=True, text=True, timeout=15, cwd=executable.parent,
                           env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"})
    assert check.returncode == 0, check.stdout + check.stderr
    assert "site-packages" in check.stdout, check.stdout
    print(f"Installed PM wheel Python: {executable}; module: {check.stdout.strip()}; pm-exchange absent")
    help_result = subprocess.run([str(executable.parent / "symphony"), "pm", "demo", "--help"],
                                 cwd=folder, capture_output=True, text=True, timeout=15)
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr
    return executable


def _command(argv, cwd, log):
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=180,
                            env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"})
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    assert result.returncode == 0, f"{argv!r}: {log.read_text()}"


@pytest.fixture
def chrome(installed_python):
    try:
        api = importlib.import_module("playwright.sync_api")
    except ImportError:
        _unavailable("Installed PM browser proof needs Playwright in the test runner")
    with api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except api.Error as exc:
            message = str(exc)
            if any(text in message for text in ["Operation not permitted", "Permission denied", "EPERM"]):
                pytest.fail(f"SANDBOX-RERUN: Chrome launch denied: {message}")
            if "executable doesn't exist" in message.lower() or "not found" in message.lower():
                _unavailable(f"Installed PM browser proof needs installed Google Chrome: {message}")
            raise
        try:
            yield browser
        finally:
            browser.close()


def _environment(tmp_path, **overrides):
    env = {key: value for key, value in os.environ.items()
           if key not in CLEAN_KEYS and not key.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))}
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env.update(HOME=str(home), PYTHONUNBUFFERED="1", OPENSPEC_TELEMETRY="0")
    env.update(overrides)
    return env


@contextmanager
def _server(argv, tmp_path, env, *, url=None):
    log = tmp_path / ("ordinary.log" if url else "demo.log")
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(argv, cwd=tmp_path, env=env, stdout=output, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                assert process.poll() is None, f"Server exited {process.returncode}: {log.read_text()}"
                if url is None:
                    match = re.search(r"^Open: (\S+)$", log.read_text(), re.MULTILINE)
                    if match:
                        url = match.group(1)
                        parsed = urlparse(url)
                        assert parsed.hostname == "127.0.0.1" and parsed.port, url
                if url:
                    try:
                        with urlopen(url.split("/?")[0] + "/", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        pass
                time.sleep(0.05)
            else:
                pytest.fail(f"Server did not become ready: {log.read_text()}")
            yield process, url
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
            try:
                returncode = process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
                pytest.fail(f"SIGINT did not stop server: {log.read_text()}")
            assert returncode == 0, f"SIGINT exit {returncode}: {log.read_text()}"


def _hostile_git(tmp_path):
    hooks = tmp_path / "hostile-hooks"
    hooks.mkdir()
    marker = tmp_path / "hook-ran"
    for name in ["pre-commit", "post-checkout"]:
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 97\n", encoding="utf-8")
        hook.chmod(0o755)
    template = tmp_path / "hostile-template"
    template.mkdir()
    shutil.copytree(hooks, template / "hooks")
    config = tmp_path / "hostile.gitconfig"
    config.write_text(f'[init]\n templateDir = "{template}"\n[core]\n hooksPath = "{hooks}"\n[commit]\n gpgsign = true\n[tag]\n gpgsign = true\n', encoding="utf-8")
    return config, marker


@contextmanager
def _page(chrome, token="", *, preset_english=False):
    context = chrome.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
    denied = []
    dialogs = []

    def route(request_route):
        if urlparse(request_route.request.url).hostname != "127.0.0.1":
            denied.append(request_route.request.url)
            request_route.abort()
        else:
            request_route.continue_()

    context.route("**/*", route)
    page = context.new_page()
    if preset_english:
        # One-shot setup: reloading after a user's choice must not reset storage.
        page.add_init_script("if (!sessionStorage.getItem('pm-test-preset')) { localStorage.setItem('symphony.lang', 'en'); sessionStorage.setItem('pm-test-preset', '1'); }")

    def dialog(item):
        dialogs.append((item.type, item.message))
        item.accept(token if item.type == "prompt" else None)

    page.on("dialog", dialog)
    try:
        yield page, dialogs
    finally:
        context.close()
        assert not denied, f"Non-local browser requests: {denied}"


def _button(page, name, lang):
    return page.get_by_role("button", name=_label(name, lang), exact=True)


def _tab(page, area, lang):
    name = TABS[area][lang == "ko"]
    page.locator(".pm-tabs").get_by_role("button", name=name, exact=True).click()
    page.wait_for_function("document.querySelector('.pm-workspace-host')?.getAttribute('aria-busy') !== 'true'")


def _guides(page, area, lang, matched):
    from playwright.sync_api import expect
    guides = page.locator("details.pm-guide")
    expect(guides).to_have_count(2)
    for index, key in enumerate(["workspace", area]):
        guide = guides.nth(index)
        expect(guide).to_have_attribute("open", "")
        expect(guide.locator("summary")).to_have_text("이 버튼들은 무엇을 하나요?" if lang == "ko" else "What do these buttons do?")
        expected = [entry[lang == "ko"] for entry in LABELS[key]]
        expect(guide.locator("dt")).to_have_text(expected)
        sentences = guide.locator("dd").all_text_contents()
        assert len(sentences) == len(expected) and all(sentences)
        if lang == "ko":
            assert all(re.search(r"[가-힣]", sentence) for sentence in sentences)
        for en, _ in LABELS[key]:
            buttons = _button(page, en, lang)
            if buttons.count():
                expect(buttons.first).to_have_text(_label(en, lang))
                matched.add(en)


def _runs(directory):
    with sqlite3.connect(directory / "project/.symphony/state.db") as database:
        return database.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_installed_demo_r7_journey(tmp_path, installed_python, chrome, lang):
    from playwright.sync_api import expect
    directory = tmp_path / "demo"
    token = "pm-demo-browser-token" if lang == "ko" else ""
    overrides = {}
    marker = None
    if lang == "ko":
        config, marker = _hostile_git(tmp_path)
        overrides.update(SYMPHONY_API_TOKEN=token, SYMPHONY_TRUSTED_ORIGINS="https://evil.example", GIT_CONFIG_GLOBAL=str(config))
    env = _environment(tmp_path, **overrides)
    argv = [str(installed_python.parent / "symphony"), "pm", "demo", "--path", str(directory), "--lang", lang]
    matched = set()
    with _server(argv, tmp_path, env) as (_, url), _page(chrome, token, preset_english=lang == "ko") as (page, dialogs):
        page.goto(url)
        expect(_button(page, "Validate", lang)).to_be_visible()
        assert page.evaluate("window.i18n.lang") == lang
        assert page.evaluate("localStorage.getItem('symphony.lang')") == lang
        assert "lang=" not in page.url
        if token:
            assert len([kind for kind, _ in dialogs if kind == "prompt"]) == 1
        _guides(page, "idea", lang, matched)
        _button(page, "Edit source", lang).click()
        _guides(page, "idea", lang, matched)
        expect(_button(page, "Planning assistant", lang)).to_be_disabled()
        expect(_button(page, "Planning assistant", lang)).to_have_attribute("title", "Off in the demo: it would contact an AI model.")
        _button(page, "Review document", lang).click() if lang == "en" else page.get_by_role("button", name="문서 검토", exact=True).click()
        # R7.1: the real combined validation, including OpenSpec, is ready.
        with page.expect_response(lambda response: response.url.endswith("/validate") and response.request.method == "POST") as validation:
            _button(page, "Validate", lang).click()
        report = validation.value.json()
        assert report["valid"] and report["source"] == "combined"
        expect(page.get_by_role("heading", name="검토 준비 완료" if lang == "ko" else "Ready for review", exact=True)).to_be_visible()
        page.get_by_role("button", name="닫기" if lang == "ko" else "Close", exact=True).click()
        _tab(page, "plan", lang)
        _guides(page, "plan", lang, matched)
        rows = page.locator(".pm-wbs-table tbody tr")
        before = rows.all_text_contents()
        first_id = rows.first.get_attribute("data-task-id")
        rows.first.locator("td").first.click()
        # R7.2–3: add a child under a real group, undo, preserve every row/id.
        with page.expect_response(lambda response: response.url.endswith("/wbs") and response.request.method == "PATCH") as added:
            _button(page, "Add child", lang).click()
        child_id = added.value.json()["impacted_ids"][0]
        assert added.value.request.post_data_json["payload"]["operations"][0]["task"]["parent_id"] == first_id
        expect(rows).to_have_count(len(before) + 1)
        assert first_id and child_id
        with page.expect_response(lambda response: response.url.endswith("/wbs") and response.request.method == "PATCH"):
            _button(page, "Undo", lang).click()
        expect(rows).to_have_count(len(before))
        assert rows.all_text_contents() == before
        # R7.4: approval through the UI creates the hardened local Git commit.
        with page.expect_response(lambda response: response.url.endswith("/approvals") and response.request.method == "POST") as approval:
            _button(page, "Approve revision", lang).click()
        assert approval.value.status == 201
        expect(page.locator(".pm-revision-bar .pm-state")).to_have_text("Approved")
        git = subprocess.run(["git", "-C", str(directory / "planning"), "show", "-s", "--format=%an <%ae>%n%s", "HEAD"], env=env, capture_output=True, text=True, check=True)
        author, subject = git.stdout.strip().split("\n")
        assert author == "Symphony PM Demo <pm-demo@example.invalid>"
        assert subject.startswith("Approve PM plan team-leave-request (")
        _guides(page, "plan", lang, matched)
        # R7.5–6: preview alone does not publish, confirmation writes five Todo.
        _button(page, "Preview approved handoff", lang).click()
        expect(page.get_by_role("dialog")).to_be_visible()
        assert not list((directory / "project/kanban").glob("*.md"))
        with page.expect_response(lambda response: response.url.endswith("/handoffs") and response.request.method == "POST") as published:
            page.get_by_role("button", name="네이티브 티켓 생성" if lang == "ko" else "Create native tickets", exact=True).click()
        assert published.value.status == 201
        tickets = list((directory / "project/kanban").glob("*.md"))
        assert len(tickets) == 5
        assert all(yaml.safe_load(ticket.read_text().split("---", 2)[1])["state"] == "Todo" for ticket in tickets)
        # R7.7: two real downloaded files; XLSX remains unavailable in this venv.
        export_format = page.get_by_label("내보내기 형식" if lang == "ko" else "Export format", exact=True)
        expect(export_format.locator("option[value=xlsx]")).to_be_disabled()
        expect(_button(page, "Preview import", lang)).to_be_disabled()
        for format_name in ["csv", "markdown"]:
            export_format.select_option(format_name)
            with page.expect_download() as download:
                _button(page, "Download export", lang).click()
            target = tmp_path / download.value.suggested_filename
            download.value.save_as(target)
            assert target.stat().st_size > 0
        # R7.8: no completed work means a real, rendered archive refusal.
        with page.expect_response(lambda response: response.url.endswith("/archive/preview")) as archive:
            _button(page, "Preview archive", lang).click()
        assert archive.value.status == 409
        assert archive.value.json()["error"]["code"] == "acceptance_incomplete"
        expect(page.locator(".pm-action-error")).to_contain_text("acceptance_incomplete")
        _inspect_other_guides(page, lang, matched)
        assert _runs(directory) == 0
        page.screenshot(path=str(tmp_path / f"demo-{lang}.png"), full_page=True)
        if lang == "ko":
            # R10.context: use the user's settings language picker, then reload.
            page.goto(url.split("/?")[0] + "/#/settings")
            language = page.locator("select").filter(has=page.locator("option[value=ko]"))
            expect(language).to_have_count(1)
            language.select_option("en")
            page.reload()
            assert page.evaluate("window.i18n.lang") == "en"
            page.goto(url.split("/?")[0] + "/#/artifacts")
            expect(_button(page, "Validate", "en")).to_be_visible()
        assert len([kind for kind, _ in dialogs if kind == "confirm"]) >= 1
    assert list((tmp_path / "home").iterdir()) == []
    if marker:
        assert not marker.exists()
    # These require an active waiver or completed/verified worker evidence;
    # the demo deliberately dispatches no worker. Keep this gap explicit.
    assert {en for entries in LABELS.values() for en, _ in entries} - matched == {
        "Revoke waiver", "Review Git commit", "Open run evidence", "Accept verified revision",
    }


def _inspect_other_guides(page, lang, matched):
    _tab(page, "requirements", lang)
    _guides(page, "requirements", lang, matched)
    _button(page, "Edit requirements", lang).first.click()
    _guides(page, "requirements", lang, matched)
    _tab(page, "design", lang)
    _button(page, "Storyboard", lang).click()
    page.locator(".pm-storyboard-card").first.click()
    _guides(page, "design", lang, matched)
    page.get_by_role("dialog").get_by_role("button", name="취소" if lang == "ko" else "Cancel", exact=True).click()
    # Both UX and API document subviews expose their semantic Add controls.
    for name in (["사용자 경험", "API"] if lang == "ko" else ["UX", "API"]):
        page.locator(".pm-design-stack").get_by_role("button", name=name, exact=True).click()
        _button(page, "Edit source", lang).click()
        _guides(page, "design", lang, matched)
    _tab(page, "code", lang)
    _guides(page, "code", lang, matched)


def test_ordinary_server_without_node_r15_context(tmp_path, installed_python, chrome):
    from playwright.sync_api import expect
    directory = tmp_path / "demo"
    executable = str(installed_python.parent / "symphony")
    env = _environment(tmp_path)
    # Stop before pressing any button: no board tickets can dispatch normally.
    with _server([executable, "pm", "demo", "--path", str(directory), "--lang", "en"], tmp_path, env):
        assert not list((directory / "project/kanban").glob("*.md"))
    assert shutil.which("node", path="/usr/bin:/bin") is None
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    url = f"http://127.0.0.1:{port}/?lang=en#/artifacts"
    env.update(PATH="/usr/bin:/bin", SYMPHONY_PROJECTS_FILE=str(directory / "projects.json"))
    argv = [executable, str(directory / "project/WORKFLOW.md"), "--port", str(port)]
    with _server(argv, tmp_path, env, url=url), _page(chrome) as (page, _):
        page.goto(url)
        expect(_button(page, "Validate", "en")).to_be_visible()
        payload = page.request.get(url.split("/?")[0] + "/api/v1/pm/capabilities").json()
        assert payload["demo"] == {"active": False}
        tool = payload["host_tools"]["node"]
        reason = "Bundled OpenSpec needs Node.js 20.19.0 or newer on PATH (found: none)"
        assert tool["available"] is False and tool["reason"] == reason
        validate = _button(page, "Validate", "en")
        expect(validate).to_be_enabled()
        expect(validate).to_have_attribute("title", reason)
        approve_reason = " ".join(payload["capabilities"]["authoring"]["reasons"])
        assert approve_reason
        expect(_button(page, "Approve revision", "en")).to_be_disabled()
        expect(_button(page, "Approve revision", "en")).to_have_attribute("title", approve_reason + " " + reason)
        card = page.locator("#pm-capability-status")
        expect(card).to_contain_text("node")
        expect(card).to_contain_text(reason)
        expect(page.locator("details.pm-guide").first).not_to_have_attribute("open", "")
        with page.expect_response(lambda response: response.url.endswith("/validate") and response.request.method == "POST") as validation:
            validate.click()
        report = validation.value.json()
        assert any(item["code"] == "capability_unavailable" and item["severity"] == "blocking" for item in report["findings"])
        expect(page.locator(".pm-findings")).to_contain_text("capability_unavailable")
        expect(page.locator(".pm-findings")).to_contain_text(reason)
        page.get_by_role("button", name="Close", exact=True).click()
        _tab(page, "plan", "en")
        archive_reason = " ".join(payload["capabilities"]["handoff"]["reasons"])
        assert archive_reason
        expect(_button(page, "Preview archive", "en")).to_be_disabled()
        expect(_button(page, "Preview archive", "en")).to_have_attribute("title", archive_reason + " " + reason)
        assert _runs(directory) == 0
        page.screenshot(path=str(tmp_path / "ordinary-no-node.png"), full_page=True)
    assert _runs(directory) == 0
