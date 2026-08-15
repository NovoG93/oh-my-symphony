from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import pytest

from symphony.errors import ConfigValidationError
from symphony.workflow.builder import build_service_config
from symphony.workflow.config import AgentProfileConfig, ServiceConfig
from symphony.workflow.constants import PROFILE_FIELDS_BY_KIND, SUPPORTED_AGENT_KINDS
from symphony.workflow.parser import parse_workflow_text


def _parse(workflow_text: str) -> ServiceConfig:
    stripped = workflow_text.strip()
    if not stripped.startswith("---"):
        workflow_text = f"---\n{stripped}\n---\n"
    definition = parse_workflow_text(
        workflow_text, source_path=Path("/tmp/WORKFLOW.md")
    )
    return build_service_config(definition)


def test_agent_profile_config_dataclass_fields() -> None:
    profile = AgentProfileConfig(
        name="test-profile",
        kind="codex",
        model="gpt-5.5",
        reasoning_effort="high",
        command="codex app-server",
        turn_timeout_ms=10000,
        read_timeout_ms=5000,
        stall_timeout_ms=20000,
        resume_across_turns=False,
    )
    assert profile.name == "test-profile"
    assert profile.kind == "codex"
    assert profile.model == "gpt-5.5"
    assert profile.reasoning_effort == "high"
    assert profile.command == "codex app-server"
    assert profile.turn_timeout_ms == 10000
    assert profile.read_timeout_ms == 5000
    assert profile.stall_timeout_ms == 20000
    assert profile.resume_across_turns is False

    # Defaults for optional fields are None
    minimal = AgentProfileConfig(name="min", kind="claude")
    assert minimal.name == "min"
    assert minimal.kind == "claude"
    assert minimal.model is None
    assert minimal.reasoning_effort is None
    assert minimal.command is None
    assert minimal.turn_timeout_ms is None
    assert minimal.read_timeout_ms is None
    assert minimal.stall_timeout_ms is None
    assert minimal.resume_across_turns is None

    # Frozen
    with pytest.raises(FrozenInstanceError):
        minimal.name = "new-name"  # type: ignore[misc]


def test_parse_valid_agent_profiles_and_agent_routing() -> None:
    text = """
tracker:
  kind: file
agent:
  kind: codex
  default_profile: sol-planner
  stage_profiles:
    Plan: sol-planner
    Build: sonnet-builder
    Review: sol-reviewer
agent_profiles:
  sol-planner:
    kind: codex
    model: sol
    reasoning_effort: high
    turn_timeout_ms: 1800000
  sonnet-builder:
    kind: claude
    model: sonnet-4
    resume_across_turns: true
  sol-reviewer:
    kind: codex
    model: sol
    reasoning_effort: high
"""
    cfg = _parse(text)
    assert "sol-planner" in cfg.agent_profiles
    assert "sonnet-builder" in cfg.agent_profiles
    assert "sol-reviewer" in cfg.agent_profiles

    planner = cfg.agent_profiles["sol-planner"]
    assert planner.name == "sol-planner"
    assert planner.kind == "codex"
    assert planner.model == "sol"
    assert planner.reasoning_effort == "high"
    assert planner.turn_timeout_ms == 1800000
    assert planner.read_timeout_ms is None

    builder = cfg.agent_profiles["sonnet-builder"]
    assert builder.name == "sonnet-builder"
    assert builder.kind == "claude"
    assert builder.model == "sonnet-4"
    assert builder.resume_across_turns is True

    assert cfg.agent.default_profile == "sol-planner"
    assert cfg.agent.stage_profiles == {
        "plan": "sol-planner",
        "build": "sonnet-builder",
        "review": "sol-reviewer",
    }


def test_agent_profiles_canonicalizes_antigravity_kind() -> None:
    text = """
tracker:
  kind: file
agent_profiles:
  agy-helper:
    kind: antigravity
    command: agy --custom
"""
    cfg = _parse(text)
    assert cfg.agent_profiles["agy-helper"].kind == "agy"
    assert cfg.agent_profiles["agy-helper"].command == "agy --custom"


def test_profile_fields_by_kind_allowlist_structure() -> None:
    for kind in SUPPORTED_AGENT_KINDS:
        assert kind in PROFILE_FIELDS_BY_KIND
        allowed = PROFILE_FIELDS_BY_KIND[kind]
        assert isinstance(allowed, set)
        assert "command" in allowed
        assert "turn_timeout_ms" in allowed
        assert "read_timeout_ms" in allowed
        assert "stall_timeout_ms" in allowed

    assert "model" in PROFILE_FIELDS_BY_KIND["codex"]
    assert "reasoning_effort" in PROFILE_FIELDS_BY_KIND["codex"]
    assert "resume_across_turns" not in PROFILE_FIELDS_BY_KIND["codex"]

    assert "model" in PROFILE_FIELDS_BY_KIND["claude"]
    assert "resume_across_turns" in PROFILE_FIELDS_BY_KIND["claude"]
    assert "reasoning_effort" not in PROFILE_FIELDS_BY_KIND["claude"]

    assert "reasoning_effort" not in PROFILE_FIELDS_BY_KIND["agy"]
    assert "model" not in PROFILE_FIELDS_BY_KIND["agy"]


def test_validation_rejects_empty_or_whitespace_profile_name() -> None:
    text = """
tracker:
  kind: file
agent_profiles:
  "":
    kind: codex
"""
    with pytest.raises(ConfigValidationError, match="profile name"):
        _parse(text)

    text_ws = """
tracker:
  kind: file
agent_profiles:
  "   ":
    kind: codex
"""
    with pytest.raises(ConfigValidationError, match="profile name"):
        _parse(text_ws)


def test_validation_rejects_unknown_backend_kind() -> None:
    text = """
tracker:
  kind: file
agent_profiles:
  custom-runner:
    kind: hal9000
"""
    with pytest.raises(ConfigValidationError, match="kind must be one of"):
        _parse(text)


def test_validation_rejects_missing_kind() -> None:
    text = """
tracker:
  kind: file
agent_profiles:
  no-kind:
    model: gpt-5.5
"""
    with pytest.raises(ConfigValidationError, match="kind is required"):
        _parse(text)


def test_validation_rejects_missing_profile_in_stage_profiles() -> None:
    text = """
tracker:
  kind: file
agent:
  stage_profiles:
    Plan: non-existent-profile
agent_profiles:
  existing-profile:
    kind: codex
"""
    with pytest.raises(ConfigValidationError, match="references unknown profile"):
        _parse(text)


def test_validation_rejects_missing_profile_in_default_profile() -> None:
    text = """
tracker:
  kind: file
agent:
  default_profile: missing-profile
agent_profiles:
  existing-profile:
    kind: codex
"""
    with pytest.raises(ConfigValidationError, match="references unknown profile"):
        _parse(text)


def test_validation_rejects_unsupported_fields_for_backend() -> None:
    # reasoning_effort on agy
    text_agy_reasoning = """
tracker:
  kind: file
agent_profiles:
  qa:
    kind: agy
    reasoning_effort: high
"""
    with pytest.raises(ConfigValidationError, match="not supported for backend 'agy'"):
        _parse(text_agy_reasoning)

    # reasoning_effort on claude
    text_claude_reasoning = """
tracker:
  kind: file
agent_profiles:
  qa:
    kind: claude
    reasoning_effort: high
"""
    with pytest.raises(
        ConfigValidationError, match="not supported for backend 'claude'"
    ):
        _parse(text_claude_reasoning)

    # resume_across_turns on codex
    text_codex_resume = """
tracker:
  kind: file
agent_profiles:
  qa:
    kind: codex
    resume_across_turns: true
"""
    with pytest.raises(
        ConfigValidationError, match="not supported for backend 'codex'"
    ):
        _parse(text_codex_resume)

    # arbitrary unknown field
    text_unknown = """
tracker:
  kind: file
agent_profiles:
  qa:
    kind: codex
    foo_bar_setting: 123
"""
    with pytest.raises(
        ConfigValidationError, match="unsupported field 'foo_bar_setting'"
    ):
        _parse(text_unknown)


def test_validation_rejects_non_string_model() -> None:
    text = """
tracker:
  kind: file
agent_profiles:
  bad-model:
    kind: codex
    model: 12345
"""
    with pytest.raises(ConfigValidationError, match="model must be a string"):
        _parse(text)


def test_validation_rejects_non_positive_timeouts() -> None:
    for timeout_field in ("turn_timeout_ms", "read_timeout_ms", "stall_timeout_ms"):
        text_zero = f"""
tracker:
  kind: file
agent_profiles:
  bad-timeout:
    kind: codex
    {timeout_field}: 0
"""
        with pytest.raises(
            ConfigValidationError, match=f"{timeout_field} must be a positive integer"
        ):
            _parse(text_zero)

        text_neg = f"""
tracker:
  kind: file
agent_profiles:
  bad-timeout:
    kind: codex
    {timeout_field}: -100
"""
        with pytest.raises(
            ConfigValidationError, match=f"{timeout_field} must be a positive integer"
        ):
            _parse(text_neg)


def test_validation_rejects_non_boolean_resume_across_turns() -> None:
    text = """
tracker:
  kind: file
agent_profiles:
  bad-resume:
    kind: claude
    resume_across_turns: "yes"
"""
    with pytest.raises(
        ConfigValidationError, match="resume_across_turns must be a boolean"
    ):
        _parse(text)


def test_validation_rejects_non_mapping_agent_profiles() -> None:
    text = """
tracker:
  kind: file
agent_profiles:
  - list
  - of
  - items
"""
    with pytest.raises(ConfigValidationError, match="agent_profiles must be a mapping"):
        _parse(text)

    text_item_not_map = """
tracker:
  kind: file
agent_profiles:
  item: "string value"
"""
    with pytest.raises(
        ConfigValidationError, match="agent_profiles\\['item'\\] must be a mapping"
    ):
        _parse(text_item_not_map)


def test_backward_compatibility_defaults() -> None:
    text = """
tracker:
  kind: file
"""
    cfg = _parse(text)
    assert cfg.agent_profiles == {}
    assert cfg.agent.stage_profiles == {}
    assert cfg.agent.default_profile is None


def test_validation_rejects_duplicate_profile_names() -> None:
    # Keys normalizing to the same trimmed name
    text_ws_duplicate = """
tracker:
  kind: file
agent_profiles:
  "qa":
    kind: agy
  " qa":
    kind: codex
"""
    with pytest.raises(ConfigValidationError, match="duplicate profile name 'qa'"):
        _parse(text_ws_duplicate)
