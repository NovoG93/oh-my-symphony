"""Phase 5 End-to-End Validation & Acceptance Tests for Named Agent Profiles (TASK-8).

Validates:
- §20 full acceptance config parsing, stage resolution, and backend config overlay:
    Research -> Claude / fable
    Plan     -> Codex / sol (high reasoning)
    Build    -> Claude / sonnet
    Review   -> Codex / sol (high reasoning)
    QA       -> Codex / luna (medium reasoning)
- Backward compatibility regression: workflows with only agent.kind + stage_kinds
- Multi-model single-backend workflows (different profiles under the same kind)
- Mixed-backend workflows combining Claude and Codex
- Symphony doctor preflight verification on the acceptance configuration
- Precedence hierarchy and migration path compatibility
"""

from __future__ import annotations

from pathlib import Path
import pytest

from symphony.backends.claude_code import _inject_model
from symphony.cli.doctor import check_agent_profiles
from symphony.errors import ConfigValidationError
from symphony.workflow.builder import build_service_config
from symphony.workflow.config import (
    AgentProfileConfig,
    AgentSelection,
    ClaudeConfig,
    CodexConfig,
    ServiceConfig,
)
from symphony.workflow.parser import parse_workflow_text
from symphony.workflow.profiles import ResolvedAgentConfig, resolve_agent_config


def _parse_workflow(text: str) -> ServiceConfig:
    stripped = text.strip()
    if not stripped.startswith("---"):
        text = f"---\n{stripped}\n---\n"
    definition = parse_workflow_text(text, source_path=Path("/tmp/WORKFLOW.md"))
    return build_service_config(definition)


# ---------------------------------------------------------------------------
# Section 20 Acceptance Configuration
# ---------------------------------------------------------------------------

SECTION_20_ACCEPTANCE_YAML = """
tracker:
  kind: file
  board_root: ./kanban

agent:
  kind: claude

  stage_profiles:
    Research: fable-planner
    Plan: sol-planner
    Build: sonnet-builder
    Review: sol-reviewer
    QA: luna-qa

agent_profiles:
  fable-planner:
    kind: claude
    model: fable

  sol-planner:
    kind: codex
    model: sol
    reasoning_effort: high

  sonnet-builder:
    kind: claude
    model: sonnet

  sol-reviewer:
    kind: codex
    model: sol
    reasoning_effort: high

  luna-qa:
    kind: codex
    model: luna
    reasoning_effort: medium

codex:
  command: codex app-server

claude:
  command: claude -p --output-format stream-json --verbose
"""


def test_section_20_acceptance_config_parsing_and_resolution() -> None:
    """Validate §20 acceptance configuration parses and resolves exactly as specified."""
    cfg = _parse_workflow(SECTION_20_ACCEPTANCE_YAML)

    # 1. Verify parsed profile model
    assert len(cfg.agent_profiles) == 5
    assert set(cfg.agent_profiles.keys()) == {
        "fable-planner",
        "sol-planner",
        "sonnet-builder",
        "sol-reviewer",
        "luna-qa",
    }
    assert cfg.agent.kind == "claude"
    assert cfg.agent.default_profile is None
    assert cfg.agent.stage_profiles == {
        "research": "fable-planner",
        "plan": "sol-planner",
        "build": "sonnet-builder",
        "review": "sol-reviewer",
        "qa": "luna-qa",
    }

    # 2. Stage 1: Research -> Claude / fable
    sel_research = cfg.selection_for_state("Research")
    assert sel_research == AgentSelection(kind="claude", profile="fable-planner")
    res_research = resolve_agent_config(cfg, sel_research)
    assert res_research.kind == "claude"
    assert res_research.profile_name == "fable-planner"
    assert isinstance(res_research.claude, ClaudeConfig)
    assert res_research.claude.model == "fable"
    assert res_research.claude.command == "claude -p --output-format stream-json --verbose"
    claude_research_cmd = _inject_model(res_research.claude.command, res_research.claude.model)
    assert "--model fable" in claude_research_cmd

    # 3. Stage 2: Plan -> Codex / sol (high reasoning)
    sel_plan = cfg.selection_for_state("Plan")
    assert sel_plan == AgentSelection(kind="codex", profile="sol-planner")
    res_plan = resolve_agent_config(cfg, sel_plan)
    assert res_plan.kind == "codex"
    assert res_plan.profile_name == "sol-planner"
    assert isinstance(res_plan.codex, CodexConfig)
    assert res_plan.codex.model == "sol"
    assert res_plan.codex.reasoning_effort == "high"
    assert res_plan.codex.command == "codex app-server"

    # 4. Stage 3: Build -> Claude / sonnet
    sel_build = cfg.selection_for_state("Build")
    assert sel_build == AgentSelection(kind="claude", profile="sonnet-builder")
    res_build = resolve_agent_config(cfg, sel_build)
    assert res_build.kind == "claude"
    assert res_build.profile_name == "sonnet-builder"
    assert isinstance(res_build.claude, ClaudeConfig)
    assert res_build.claude.model == "sonnet"
    assert res_build.claude.command == "claude -p --output-format stream-json --verbose"
    claude_build_cmd = _inject_model(res_build.claude.command, res_build.claude.model)
    assert "--model sonnet" in claude_build_cmd

    # 5. Stage 4: Review -> Codex / sol (high reasoning)
    sel_review = cfg.selection_for_state("Review")
    assert sel_review == AgentSelection(kind="codex", profile="sol-reviewer")
    res_review = resolve_agent_config(cfg, sel_review)
    assert res_review.kind == "codex"
    assert res_review.profile_name == "sol-reviewer"
    assert isinstance(res_review.codex, CodexConfig)
    assert res_review.codex.model == "sol"
    assert res_review.codex.reasoning_effort == "high"
    assert res_review.codex.command == "codex app-server"

    # 6. Stage 5: QA -> Codex / luna (medium reasoning)
    sel_qa = cfg.selection_for_state("QA")
    assert sel_qa == AgentSelection(kind="codex", profile="luna-qa")
    res_qa = resolve_agent_config(cfg, sel_qa)
    assert res_qa.kind == "codex"
    assert res_qa.profile_name == "luna-qa"
    assert isinstance(res_qa.codex, CodexConfig)
    assert res_qa.codex.model == "luna"
    assert res_qa.codex.reasoning_effort == "medium"
    assert res_qa.codex.command == "codex app-server"


def test_backward_compatibility_legacy_workflow_with_stage_kinds() -> None:
    """Verify legacy workflow with only agent.kind and stage_kinds works unmodified."""
    legacy_yaml = """
tracker:
  kind: file
  board_root: ./kanban

agent:
  kind: claude
  stage_kinds:
    Build: claude
    Review: codex

codex:
  command: codex app-server
  model: gpt-5.5
  reasoning_effort: high

claude:
  command: claude -p --output-format stream-json --verbose
"""
    cfg = _parse_workflow(legacy_yaml)

    assert cfg.agent_profiles == {}
    assert cfg.agent.stage_profiles == {}
    assert cfg.agent.default_profile is None
    assert cfg.agent.stage_kinds == {"build": "claude", "review": "codex"}

    # Build stage resolves to claude with profile=None
    sel_build = cfg.selection_for_state("Build")
    assert sel_build == AgentSelection(kind="claude", profile=None)
    res_build = resolve_agent_config(cfg, sel_build)
    assert res_build.kind == "claude"
    assert res_build.profile_name is None
    assert res_build.claude == cfg.claude

    # Review stage resolves to codex with profile=None
    sel_review = cfg.selection_for_state("Review")
    assert sel_review == AgentSelection(kind="codex", profile=None)
    res_review = resolve_agent_config(cfg, sel_review)
    assert res_review.kind == "codex"
    assert res_review.profile_name is None
    assert res_review.codex == cfg.codex

    # Unmapped stage falls back to agent.kind (claude)
    sel_todo = cfg.selection_for_state("Todo")
    assert sel_todo == AgentSelection(kind="claude", profile=None)
    res_todo = resolve_agent_config(cfg, sel_todo)
    assert res_todo.claude == cfg.claude


def test_multi_model_same_backend_profiles() -> None:
    """Verify multiple profiles under the same backend kind resolve distinctly."""
    yaml_text = """
tracker:
  kind: file
agent:
  kind: codex
  stage_profiles:
    Plan: codex-sol
    Implement: codex-luna
agent_profiles:
  codex-sol:
    kind: codex
    model: sol
    reasoning_effort: high
  codex-luna:
    kind: codex
    model: luna
    reasoning_effort: medium
codex:
  command: codex app-server
"""
    cfg = _parse_workflow(yaml_text)

    sel_plan = cfg.selection_for_state("Plan")
    sel_impl = cfg.selection_for_state("Implement")

    assert sel_plan == AgentSelection(kind="codex", profile="codex-sol")
    assert sel_impl == AgentSelection(kind="codex", profile="codex-luna")
    assert sel_plan != sel_impl

    res_plan = resolve_agent_config(cfg, sel_plan)
    res_impl = resolve_agent_config(cfg, sel_impl)

    assert res_plan.codex is not None and res_plan.codex.model == "sol"
    assert res_plan.codex.reasoning_effort == "high"
    assert res_impl.codex is not None and res_impl.codex.model == "luna"
    assert res_impl.codex.reasoning_effort == "medium"


def test_migration_stage_profiles_precedence_over_stage_kinds() -> None:
    """Verify stage_profiles takes precedence over stage_kinds for incremental migration."""
    yaml_text = """
tracker:
  kind: file
agent:
  kind: claude
  default_profile: default-sonnet
  stage_kinds:
    Plan: codex
    Review: codex
    Build: gemini
  stage_profiles:
    Plan: custom-planner
agent_profiles:
  custom-planner:
    kind: codex
    model: gpt-5.5-planner
  default-sonnet:
    kind: claude
    model: sonnet-3.7
"""
    cfg = _parse_workflow(yaml_text)

    # 1. Plan has both stage_profiles and stage_kinds: stage_profiles wins (tier 5 > tier 6)
    sel_plan = cfg.selection_for_state("Plan")
    assert sel_plan == AgentSelection(kind="codex", profile="custom-planner")
    res_plan = resolve_agent_config(cfg, sel_plan)
    assert res_plan.codex is not None and res_plan.codex.model == "gpt-5.5-planner"

    # 2. Review has only stage_kinds: stage_kinds wins over default_profile (tier 6 > tier 7)
    sel_review = cfg.selection_for_state("Review")
    assert sel_review == AgentSelection(kind="codex", profile=None)

    # 3. Document has neither stage_profiles nor stage_kinds: falls back to default_profile (tier 7)
    sel_doc = cfg.selection_for_state("Document")
    assert sel_doc == AgentSelection(kind="claude", profile="default-sonnet")
    res_doc = resolve_agent_config(cfg, sel_doc)
    assert res_doc.claude is not None and res_doc.claude.model == "sonnet-3.7"


def test_doctor_passes_section_20_acceptance_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify symphony doctor reports PASS for all profiles in the §20 acceptance config."""
    import shutil

    # Ensure fake binaries appear on PATH so doctor command checks pass
    orig_which = shutil.which

    def _fake_which(cmd: str) -> str | None:
        if cmd in ("codex", "claude"):
            return f"/usr/local/bin/{cmd}"
        return orig_which(cmd)

    monkeypatch.setattr(shutil, "which", _fake_which)

    cfg = _parse_workflow(SECTION_20_ACCEPTANCE_YAML)
    results = check_agent_profiles(cfg)

    assert len(results) >= 10
    for r in results:
        assert r.status in ("pass", "warn"), f"Unexpected failure in doctor check: {r.name} {r.status} {r.message}"
