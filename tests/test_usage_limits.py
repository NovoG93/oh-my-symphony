from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
import textwrap
import pytest

from symphony.errors import ConfigValidationError
from symphony.workflow.builder import build_service_config
from symphony.workflow.config import (
    AgentProfileConfig,
    ServiceConfig,
    UsagePoolConfig,
)
from symphony.workflow.parser import parse_workflow_text
from symphony.backends.usage import (
    ProviderUsageSnapshot,
    UsageProbe,
    UsageWindow,
    USAGE_PROBES,
    get_usage_probe,
)


def _parse(workflow_text: str) -> ServiceConfig:
    dedented = textwrap.dedent(workflow_text).strip()
    if not dedented.startswith("---"):
        dedented = f"---\n{dedented}\n---\n"
    definition = parse_workflow_text(
        dedented, source_path=Path("/tmp/WORKFLOW.md")
    )
    return build_service_config(definition)


# --- Stage 6.1 Configuration Tests ---


def test_usage_pool_config_dataclass_fields() -> None:
    pool = UsagePoolConfig(source="codex", caps={"five_hour": 80.0, "weekly": 70.0})
    assert pool.source == "codex"
    assert pool.caps == {"five_hour": 80.0, "weekly": 70.0}

    with pytest.raises(FrozenInstanceError):
        pool.source = "claude"  # type: ignore[misc]


def test_usage_limit_is_shared_by_profiles_of_same_kind() -> None:
    cfg = _parse("""
    usage_pools:
      codex:
        source: codex
        caps:
          five_hour: 80
          weekly: 70

    agent_profiles:
      builder:
        kind: codex

      reviewer:
        kind: codex
    """)

    assert "codex" in cfg.usage_pools
    assert cfg.usage_pools["codex"].source == "codex"
    assert cfg.usage_pools["codex"].caps["weekly"] == 70.0
    assert cfg.usage_pools["codex"].caps["five_hour"] == 80.0
    assert cfg.agent_profiles["builder"].usage_pool is None
    assert cfg.agent_profiles["reviewer"].usage_pool is None


def test_pi_profile_can_explicitly_share_codex_pool() -> None:
    cfg = _parse("""
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70

    agent_profiles:
      pi-builder:
        kind: pi
        usage_pool: codex
    """)

    assert cfg.agent_profiles["pi-builder"].usage_pool == "codex"
    assert cfg.agent_profiles["pi-builder"].kind == "pi"


def test_opencode_and_prime_agent_profiles_can_explicitly_bind_usage_pool() -> None:
    cfg = _parse("""
    usage_pools:
      copilot:
        source: github-copilot
        caps:
          monthly: 85

    agent_profiles:
      opencode-worker:
        kind: opencode
        usage_pool: copilot

      prime-worker:
        kind: prime-agent
        usage_pool: copilot
    """)

    assert cfg.agent_profiles["opencode-worker"].usage_pool == "copilot"
    assert cfg.agent_profiles["prime-worker"].usage_pool == "copilot"


@pytest.mark.parametrize(
    "cap_repr",
    [
        "-1",
        "0",
        "0.0",
        "101",
        "100.1",
        "-0.5",
        '"80"',
        "'70%'",
        "true",
        "false",
        "null",
    ],
)
def test_usage_cap_rejects_invalid_percent(cap_repr: str) -> None:
    text = f"""
    usage_pools:
      test-pool:
        source: codex
        caps:
          weekly: {cap_repr}
    """
    with pytest.raises(ConfigValidationError, match="caps\\.weekly"):
        _parse(text)


def test_unknown_usage_pool_reference_is_rejected() -> None:
    text = """
    agent_profiles:
      builder:
        kind: codex
        usage_pool: non-existent-pool
    """
    with pytest.raises(ConfigValidationError, match="references unknown usage pool"):
        _parse(text)


def test_missing_usage_pools_is_backward_compatible() -> None:
    text = """
    tracker:
      kind: file
    agent:
      kind: codex
    agent_profiles:
      simple:
        kind: codex
    """
    cfg = _parse(text)
    assert cfg.usage_pools == {}
    assert cfg.agent_profiles["simple"].usage_pool is None


def test_partial_usage_policy_is_valid() -> None:
    text = """
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 75.5
    """
    cfg = _parse(text)
    assert "codex" in cfg.usage_pools
    assert cfg.usage_pools["codex"].caps == {"weekly": 75.5}


def test_generic_daily_window_is_valid() -> None:
    text = """
    usage_pools:
      gemini-daily:
        source: gemini
        caps:
          daily: 85.0
    """
    cfg = _parse(text)
    assert cfg.usage_pools["gemini-daily"].caps == {"daily": 85.0}


def test_generic_monthly_window_is_valid() -> None:
    text = """
    usage_pools:
      kiro-monthly:
        source: kiro
        caps:
          monthly: 90
    """
    cfg = _parse(text)
    assert cfg.usage_pools["kiro-monthly"].caps == {"monthly": 90.0}


def test_arbitrary_window_names_are_supported() -> None:
    text = """
    usage_pools:
      custom:
        source: custom-source
        caps:
          five_hour: 80
          rolling_7d: 70
          ten_minute: 50
    """
    cfg = _parse(text)
    assert cfg.usage_pools["custom"].caps == {
        "five_hour": 80.0,
        "rolling_7d": 70.0,
        "ten_minute": 50.0,
    }


def test_usage_pools_validation_rejects_non_mapping() -> None:
    text = """
    usage_pools:
      - not
      - a
      - mapping
    """
    with pytest.raises(ConfigValidationError, match="usage_pools must be a mapping"):
        _parse(text)


def test_usage_pools_validation_rejects_empty_name() -> None:
    text = """
    usage_pools:
      "":
        source: codex
        caps:
          weekly: 80
    """
    with pytest.raises(ConfigValidationError, match="pool name"):
        _parse(text)


def test_usage_pools_validation_rejects_non_mapping_pool_entry() -> None:
    text = """
    usage_pools:
      codex: "invalid-string"
    """
    with pytest.raises(ConfigValidationError, match="usage_pools\\['codex'\\] must be a mapping"):
        _parse(text)


def test_usage_pools_validation_rejects_missing_or_empty_source() -> None:
    text_missing = """
    usage_pools:
      codex:
        caps:
          weekly: 80
    """
    with pytest.raises(ConfigValidationError, match="source is required"):
        _parse(text_missing)

    text_empty = """
    usage_pools:
      codex:
        source: "  "
        caps:
          weekly: 80
    """
    with pytest.raises(ConfigValidationError, match="source must be a non-empty string"):
        _parse(text_empty)


def test_usage_pools_validation_rejects_non_mapping_caps() -> None:
    text = """
    usage_pools:
      codex:
        source: codex
        caps: "80%"
    """
    with pytest.raises(ConfigValidationError, match="caps must be a mapping"):
        _parse(text)


def test_usage_pools_validation_rejects_unsupported_field() -> None:
    text = """
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 80
        extra_field: 123
    """
    with pytest.raises(ConfigValidationError, match="unsupported field 'extra_field'"):
        _parse(text)


# --- Normalized backend usage types & probe protocol tests ---


def test_usage_window_dataclass() -> None:
    now = datetime.now(timezone.utc)
    w = UsageWindow(
        key="five_hour",
        used_percent=65.5,
        remaining_percent=34.5,
        resets_at=now,
    )
    assert w.key == "five_hour"
    assert w.used_percent == 65.5
    assert w.remaining_percent == 34.5
    assert w.resets_at == now

    # Frozen
    with pytest.raises(FrozenInstanceError):
        w.used_percent = 50.0  # type: ignore[misc]


def test_provider_usage_snapshot_dataclass() -> None:
    now = datetime.now(timezone.utc)
    snapshot = ProviderUsageSnapshot(
        pool_id="codex",
        source="codex",
        windows={
            "five_hour": UsageWindow(
                key="five_hour",
                used_percent=60.0,
                remaining_percent=40.0,
                resets_at=now,
            )
        },
        hard_limit_reached=False,
        authoritative=True,
        observed_at=now,
        stale=False,
    )
    assert snapshot.pool_id == "codex"
    assert snapshot.source == "codex"
    assert snapshot.hard_limit_reached is False
    assert snapshot.authoritative is True
    assert snapshot.stale is False
    assert "five_hour" in snapshot.windows

    # Defaults check
    minimal = ProviderUsageSnapshot(
        pool_id="claude",
        source="claude",
        windows={},
    )
    assert minimal.hard_limit_reached is False
    assert minimal.authoritative is True
    assert minimal.observed_at is None
    assert minimal.stale is False

    with pytest.raises(FrozenInstanceError):
        snapshot.hard_limit_reached = True  # type: ignore[misc]


def test_usage_probe_protocol_and_registry_fail_open() -> None:
    # A missing or unsupported probe returns None (fail open)
    assert get_usage_probe("non-existent-source") is None
    assert USAGE_PROBES.get("non-existent-source") is None

    # Verify a class implementing fetch_usage matches UsageProbe Protocol
    class DummyProbe:
        async def fetch_usage(self) -> ProviderUsageSnapshot | None:
            return None

    assert isinstance(DummyProbe(), UsageProbe)
