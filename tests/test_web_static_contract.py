from __future__ import annotations

from pathlib import Path


STATIC_ROOT = Path("src/symphony/web/static")


def test_web_board_defaults_to_active_lanes_with_terminal_group() -> None:
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
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
    assert "'aria-label': 'Terminal states'" in js
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
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    css = (STATIC_ROOT / "style.css").read_text(encoding="utf-8")
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'data-route="git"' in html
    assert "const ROUTES = ['board', 'stats', 'workflow', 'git', 'settings']" in js
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
    js = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
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
