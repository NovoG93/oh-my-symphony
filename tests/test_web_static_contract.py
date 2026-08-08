from __future__ import annotations

from pathlib import Path


STATIC_ROOT = Path("src/symphony/web/static")


def _script_bundle() -> str:
    """Every script the board ships, concatenated.

    These are contract tests over what reaches the browser, not over one
    file's contents. The en/ko i18n split moved user-facing copy out of
    `app.js` into the `i18n.js` catalogue while the wiring stayed put, so
    reading `app.js` alone started failing on strings the UI still shows.
    Reading the bundle keeps both kinds of assertion — code structure and
    the copy it renders — meaningful wherever the refactor puts them.
    """
    return "\n".join(
        (STATIC_ROOT / name).read_text(encoding="utf-8")
        for name in ("app.js", "i18n.js")
    )


def test_web_board_defaults_to_active_lanes_with_terminal_group() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert "boardScope: 'active'" in js
    assert "function buildBoardScopeToggle()" in js
    assert "function visibleBoardColumns(columns)" in js
    assert "state.boardScope === 'all' ? columns : activeColumns(columns)" in js
    assert "function buildTerminalSectionEl(groups, live, readOnly)" in js
    assert "function buildAttentionBadge(attention)" in js
    assert "getRuns: ({ issue, limit } = {})" in js
    assert "putContinuousImprovement: (payload)" in js
    assert "getContinuousImprovementStatus: ()" in js
    assert "resetContinuousImprovementTurns: ()" in js
    assert "function buildRunHistorySection(detail)" in js
    assert "api.getRuns({ issue: identifier, limit: 10 })" in js
    assert "Run history" in js
    assert "Default agent" in js
    assert "(wf.agent && wf.agent.kind) ||" in js
    assert "function buildContinuousImprovementCard(wf, ciStatus)" in js
    assert "Continuous improvement" in js
    assert "ci-enabled-toggle" in js
    assert "ci-interval-input" in js
    assert "ci-max-turns-input" in js
    assert "ci-agent-kind-select" in js
    assert "ci-reset-turns" in js
    assert "max_turns_reached" in js
    assert "not_proven" in js
    assert "Not proven" in js
    assert "function buildMobileLaneTabs(columns)" in js
    assert "function isMobileBoardViewport()" in js
    assert "Review and parked" in js
    # Screen readers must still hear "Terminal states"; after the i18n split
    # the label is a catalogue lookup, so assert the wiring and the copy it
    # resolves to rather than the old inline literal.
    assert "'aria-label': t('board.terminalStates')" in js
    assert "'board.terminalStates': 'Terminal states'" in js
    assert ".terminal-section" in css
    assert ".terminal-group" in css
    assert ".terminal-card-list" in css
    assert ".chip-attention" in css
    assert ".drawer-attention" in css
    assert ".drawer-run-history" in css
    assert ".run-history-row" in css
    assert ".ci-status-grid" in css
    assert ".ci-status-pill" in css
    assert ".mobile-lane-tabs" in css
    assert ".mobile-lane-tab.active" in css


def test_web_git_page_contract() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'data-route="git"' in html
    assert "'git'" in js.split("const ROUTES = ", 1)[1].split("\n", 1)[0]
    assert "function renderGitPage(container)" in js
    assert "getGitLog: ({ branch, limit } = {})" in js
    assert "getTaskBranches: () => apiRequest('/git/task-branches')" in js
    assert "getGitCompare: ({ branch, target } = {})" in js
    assert "postGitMerge: (payload)" in js
    assert "function buildTaskBranchesCard(data, compareCard)" in js
    assert "function openMergeModal(row, data)" in js
    assert "function buildGitHistoryCard(diffPanel)" in js
    assert "function buildGitCompareCard(data, diffPanel)" in js
    assert "not_a_git_repo" in js
    assert "use Recover on the board" in js
    assert ".git-body" in css
    assert ".branch-row" in css
    assert ".badge-merged" in css
    assert ".badge-running" in css
    assert ".commit-row" in css
    assert ".ref-chip" in css
    assert ".diffstat-table" in css


def test_web_git_diff_panel_contract() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert "getGitDiff: ({ branch, target, path, commit } = {})" in js
    assert "function buildDiffPanel()" in js
    assert "function splitPatchByFile(patch)" in js
    assert "function buildDiffFileSection(file)" in js
    assert "function diffLineClass(line)" in js
    assert "diffPanel.showCompare(cmp.branch, cmp.target)" in js
    assert "diffPanel.showCommit(commit)" in js
    assert "diffPanel.scrollToFile(f.path)" in js
    assert ".git-diff-panel" in css
    assert ".diff-file-header" in css
    assert ".diff-line.diff-add" in css
    assert ".diff-line.diff-del" in css
    assert ".diff-line.diff-hunk" in css


def test_web_git_branch_actions_contract() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert "getGitRemoteStatus: () => apiRequest('/git/remote-status')" in js
    assert "postGitBranchDelete: (payload)" in js
    assert "postGitPush: (payload)" in js
    assert "postGitPullRequest: (payload)" in js
    assert "async function pushTaskBranch(branch, remote)" in js
    assert "function openPushTargetModal(branch, remote)" in js
    assert "function openDeleteBranchModal(row, data)" in js
    assert "function openPullRequestModal(row, data)" in js
    assert "state.gitRemote = remoteStatus;" in js
    assert "confirm: confirmInput.value.trim()" in js
    assert "The GitHub CLI (gh) is not on PATH" in js
    assert "No git remote configured" in js
    assert ".git-push-target" in css


def test_web_chat_page_contract() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'data-route="chat"' in html
    assert (
        "const ROUTES = ['board', 'stats', 'workflow', 'git', 'chat', 'preview', 'settings']"
        in js
    )
    assert "function renderChatPage(container)" in js
    assert "getChatSession: () => apiRequest('/chat/session')" in js
    assert "createChatSession: (payload)" in js
    assert "patchChatSession: (payload)" in js
    assert "deleteChatSession: ()" in js
    assert "postChatMessage: (payload)" in js
    assert "function connectChatSocket(view)" in js
    assert "new WebSocket(`${proto}://${location.host}/api/v1/chat/ws${query}`)" in js
    assert "function closeChatSocket()" in js
    assert "function buildChatMessageNode(msg)" in js
    assert "read-only not enforced" in js
    assert ".chat-transcript" in css
    assert ".chat-bubble" in css
    assert ".chat-mode-toggle" in css
    assert ".chat-composer" in css


def test_web_chat_token_streaming_contract() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert "function appendChatDelta(view, text)" in js
    assert "function finalizeChatLive(view, finalText)" in js
    assert "if (msg.type === 'agent_delta')" in js
    assert "requestAnimationFrame(" in js
    assert (
        "if (msg.type === 'agent_message' && finalizeChatLive(view, msg.text)) return;"
        in js
    )
    assert ".chat-bubble-live" in css
    assert "white-space: pre-wrap" in css


def test_web_chat_multi_session_contract() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert "getChatSessions: () => apiRequest('/chat/sessions')" in js
    assert "createChatSession2: (payload)" in js
    assert "reattachChatSession: (id)" in js
    assert "deleteChatSessionById: (id, { forget } = {})" in js
    assert "postChatMessageTo: (id, payload)" in js
    assert "async function refreshChatSessions(view)" in js
    assert "async function selectChatSession(view, sessionId)" in js
    assert "function renderChatSessionBar(view)" in js
    assert "function buildChatResumeControl(view, resumable, atLimit)" in js
    assert "function openNewChatSessionModal(view)" in js
    assert "function focusChatSocket(sessionId)" in js
    assert "JSON.stringify({ type: 'focus', session_id: sessionId || null })" in js
    assert "?session=${encodeURIComponent(chatState.currentId)}" in js
    assert ".chat-session-bar" in css
    assert ".chat-tab.active" in css
    assert ".chat-tab-dot.busy" in css


def test_web_chat_budget_contract() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert "function buildChatBudgetChip(budget)" in js
    assert (
        "if (snap.budget) view.controls.appendChild(buildChatBudgetChip(snap.budget));"
        in js
    )
    assert "chatState.snapshot.budget = msg.meta.budget;" in js
    assert ".chat-budget-chip" in css
    assert ".chat-budget-chip.over" in css


def test_web_chat_font_controls_contract() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert "const CHAT_FONT_KEY = 'symphony.chatFontSize'" in js
    assert "function loadChatFontSize()" in js
    assert "function bumpChatFont(view, delta)" in js
    assert "function buildFontControls(view)" in js
    assert ".chat-font-controls" in css
    assert "font-size: inherit" in css


def test_web_settings_lane_preset_contract() -> None:
    js = _script_bundle()

    assert "getLanePresets: () => apiRequest('/workflow/presets')" in js
    assert "applyLanePreset: (name)" in js
    assert "function buildLanePresetCard(presets, wf)" in js
    assert "body.appendChild(buildLanePresetCard(lanePresets, wf));" in js
    assert "'settings.lanePreset': 'Lane preset'" in js
    assert "'common.apply': 'Apply'" in js


def test_web_settings_stage_contracts_hint_contract() -> None:
    """F-06: a board whose lanes disable the evidence floor must say so."""
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert "function buildStageContractsRow(wf)" in js
    assert "agent.stage_contracts_enabled !== false" in js
    assert "buildStageContractsRow(wf)," in js
    assert "'settings.stageContracts': 'Stage contracts'" in js
    assert "'settings.stageContractsOff'" in js
    assert ".form-hint-warn" in css


def test_web_board_renders_dependency_and_request_chips() -> None:
    """F-14: the API returned blocked_by/request; the board ignored both."""
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert "function blockedByIds(issue)" in js
    assert "function parseIdList(value)" in js
    assert "class: 'chip-blocked'" in js
    assert "class: 'chip-request'" in js
    assert ".chip-blocked" in css
    assert ".chip-request" in css
    # Create modal + drawer can both set them, through the validating API.
    assert "blocked_by: parseIdList(blockedByInput.value)" in js
    assert "request: requestInput.value.trim()" in js
    assert "commitField(\n        detail.identifier, 'blocked_by', ids," in js
    assert "field(t('common.blockedBy'), blockedByInput)" in js
    assert "'common.blockedBy': 'Blocked by'" in js
    assert "'board.blockedByPlaceholder'" in js


def test_web_markdown_renders_human_readable_tables() -> None:
    """LLM-authored ticket Markdown must render as safe, readable HTML nodes."""
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert "function renderMarkdown(source)" in js
    assert "function parseTableAt(lines, index)" in js
    assert "function parseTableAlignments(line)" in js
    assert "el('table', { class: 'md-table' }" in js
    assert "class: `md-table-cell md-align-${alignments[index] || 'left'}`" in js
    assert ".md-table-wrap" in css
    assert ".md-table-cell" in css


def test_web_product_preview_page_contract() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'data-route="preview"' in html
    assert 'data-i18n="nav.preview"' in html
    assert "getPreview: () => apiRequest('/preview')" in js
    assert (
        "startPreview: () => apiRequest('/preview/start', { method: 'POST', body: '{}' })"
        in js
    )
    assert (
        "stopPreview: () => apiRequest('/preview/stop', { method: 'POST', body: '{}' })"
        in js
    )
    assert (
        "restartPreview: () => apiRequest('/preview/restart', { method: 'POST', body: '{}' })"
        in js
    )
    assert "function renderPreviewPage(container)" in js
    assert "function paintPreviewPage(body, data)" in js
    assert "function safePreviewUrl(value)" in js
    assert (
        "previewPollTimer = setTimeout(() => refreshPreviewPage(body, false), 3000)"
        in js
    )
    assert "role: 'log'" in js
    assert "'aria-live': 'polite'" in js
    assert "el('iframe'" in js
    assert "title: t('preview.iframeTitle')" in js
    assert "rel: 'noopener noreferrer'" in js
    assert "data.release_gate || {}" in js
    assert "Array.isArray(data.acceptance)" in js
    assert "'preview.title': 'Product Preview'" in js
    assert "'preview.title': '제품 프리뷰'" in js
    assert ".preview-command-deck" in css
    assert ".preview-status.running" in css
    assert ".preview-frame" in css
    assert ".preview-log-output" in css
    assert "@media (max-width: 560px)" in css


def test_web_project_switcher_and_management_contract() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'id="project-selector"' in html
    assert 'data-i18n-attr="aria-label:projects.selectorLabel"' in html
    assert 'id="project-current-path"' in html
    assert 'id="project-workflow-path"' in html
    assert 'id="project-board-path"' in html
    assert "getProjects: () => apiRequest('/projects')" in js
    assert "createOrAdoptProject: (payload)" in js
    assert "openProject: (id)" in js
    assert "method: 'POST', body: '{}'" in js
    assert "modal.setAttribute('aria-labelledby', titleId)" in js
    assert "role: 'alert', 'aria-live': 'assertive'" in js
    assert "window.location.assign(opened.url)" in js
    assert "function openManageProjectsDialog()" in js
    assert "'projects.boardPath': 'Issues are stored here'" in js
    assert "'projects.boardPath': '이슈 저장 위치'" in js
    assert ".project-selector" in css
    assert ".project-paths" in css
    assert "direction: rtl" in css
    assert "unicode-bidi: plaintext" in css
    assert "function setProjectPath(element, value)" in js
    assert "element.dataset.fullPath = value || '';" in js
    assert ".project-paths dd:focus-visible::after" in css
    assert "content: attr(data-full-path)" in css
    assert ".project-selector { font-size: 0;" not in css
