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
    assert "const ROUTES = ['board', 'stats', 'workflow', 'git', 'chat', 'settings']" in js
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
    assert "if (msg.type === 'agent_message' && finalizeChatLive(view, msg.text)) return;" in js
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
    assert "if (snap.budget) view.controls.appendChild(buildChatBudgetChip(snap.budget));" in js
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


def test_web_governed_run_api_contract() -> None:
    js = _script_bundle()

    assert "getGovernedRun: (runId) => apiRequest(`/runs/${encodeURIComponent(runId)}`)" in js
    assert "resumeGovernedRun: (runId)" in js
    assert "abandonGovernedRun: (runId)" in js
    assert "cancelGovernedRun: (runId)" in js
    assert "resolveApproval: (approvalId, payload)" in js


def test_web_governed_run_panel_contract() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert "function openRunPanel(runId)" in js
    assert "function buildRunPanelContent(detail)" in js
    assert "function buildRunNodesSection(detail)" in js
    assert "function buildRunNodeRow(node)" in js
    assert "function buildRunNodeDetail(node)" in js
    assert "function buildNodeGitSummary(git)" in js
    assert "function buildRunArtifactRow(artifact)" in js
    # The staleness guard from buildRunHistorySection: a slow response for a
    # panel the operator already closed must never paint over the new one.
    assert "if (state.runPanelId !== runId) return;" in js
    # Server order is the executed topological order; nothing re-sorts it.
    assert "for (const node of nodes) rows.appendChild(buildRunNodeRow(node));" in js
    assert "href: `${API_BASE}/artifacts/${encodeURIComponent(artifact.artifact_id)}`," in js
    assert ".run-panel-summary" in css
    assert ".run-node-row" in css
    assert ".run-node-output" in css
    assert ".run-artifact-row" in css


def test_web_governed_card_badges_contract() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert "function buildGovernedBadges(liveEntry)" in js
    assert "function governedLiveInfo(liveEntry)" in js
    assert "for (const badge of buildGovernedBadges(liveEntry)) badges.appendChild(badge);" in js
    # Gate and attention must differ by word/symbol, not colour alone.
    assert "t('governed.badgeGate')" in js
    assert "'governed.badgeGate': '⏸ gate'" in js
    assert "t('governed.badgeAttention')" in js
    assert "'governed.badgeAttention': '⚠ attention'" in js
    assert "t('governed.badgeProgress'" in js
    assert "'governed.badgeProgress': '[{completed}/{total}]'" in js
    assert ".chip-gate" in css
    assert ".chip-run-attention" in css


def test_web_governed_approval_gate_contract() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    assert "function buildApprovalPanel(detail, approval)" in js
    assert "function buildApprovalEvidence(detail, approval)" in js
    assert "async function resolveGate(approval, decision, commentInput)" in js
    # The version rendered is the version sent, so a gate that moved loses the
    # compare-and-set instead of being overwritten.
    assert "expected_version: approval.version," in js
    # A conflict reloads and re-asks; it never marks the gate resolved.
    assert "if (err instanceof ApiError && err.status === 409) {" in js
    assert "showRunPanelError(t('governed.gateConflict', { message: err.message }));" in js
    assert "if (decision === 'rejected' && !comment) {" in js
    assert "t('governed.commentRequired')" in js
    assert "'governed.commentRequired': 'A comment is required to reject a gate.'" in js
    assert ".run-approval-actions" in css


def test_web_governed_actions_and_error_rendering_contract() -> None:
    js = _script_bundle()
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")

    # `actions` is authoritative — the UI never derives buttons from status.
    assert "for (const action of detail.actions || []) {" in js
    assert "function buildRunActionsRow(detail)" in js
    assert "function buildConfirmedActionControl(detail, action, handler, label)" in js
    assert "async function runGovernedAction(detail, handler)" in js
    assert "function buildRunAttentionSection(detail)" in js
    assert "t('governed.attentionReason'" in js
    assert "'governed.attentionReason': 'Reason: {reason}'" in js
    # Every mutation renders the server's code and message (PRD §24.6).
    assert "function apiErrorText(err)" in js
    assert "if (err instanceof ApiError) return t('governed.errorWithCode', { code: err.code, message: err.message });" in js
    assert "showRunPanelError(apiErrorText(err));" in js
    # Added polling reuses the existing drag/edit hold.
    assert "function shouldHoldRender()" in js
    assert "if (!shouldHoldRender() && !state.runPanelHold) await refreshRunPanel();" in js
    assert ".run-attention" in css
