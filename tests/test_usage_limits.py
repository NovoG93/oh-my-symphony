from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path
import textwrap
import pytest

from symphony.errors import ConfigValidationError
from symphony.orchestrator.usage import (
    ProviderUsageManager,
    UsageDecision,
    format_wait_reason,
)
from symphony.workflow.builder import build_service_config
from symphony.workflow.config import (
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
    definition = parse_workflow_text(dedented, source_path=Path("/tmp/WORKFLOW.md"))
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
    with pytest.raises(
        ConfigValidationError, match="usage_pools\\['codex'\\] must be a mapping"
    ):
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
    with pytest.raises(
        ConfigValidationError, match="source must be a non-empty string"
    ):
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


# --- Stage 6.2 Generic Usage-Pool Tests ---


def test_profiles_with_same_usage_pool_share_limit() -> None:
    manager = ProviderUsageManager()
    pool = UsagePoolConfig(source="codex", caps={"weekly": 70.0})
    manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=80.0,
                    remaining_percent=20.0,
                )
            },
            authoritative=True,
        ),
    )
    # Both builder and reviewer resolving to the "codex" pool are blocked
    assert manager.evaluate("codex", pool) == UsageDecision.WAIT_PROVIDER_USAGE


def test_pi_copilot_is_not_blocked_by_codex_limit() -> None:
    manager = ProviderUsageManager()
    codex_pool = UsagePoolConfig(source="codex", caps={"weekly": 70.0})
    copilot_pool = UsagePoolConfig(source="github-copilot", caps={"weekly": 70.0})

    manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=100.0,
                    remaining_percent=0.0,
                )
            },
            authoritative=True,
        ),
    )
    # Codex pool is blocked
    assert manager.evaluate("codex", codex_pool) == UsageDecision.WAIT_PROVIDER_USAGE
    # Independent copilot pool is not blocked (no snapshot / under cap -> fail open)
    assert manager.evaluate("copilot", copilot_pool) == UsageDecision.READY


@pytest.mark.parametrize(
    ("window", "used", "cap"),
    [
        ("five_hour", 80.0, 80.0),
        ("weekly", 70.0, 70.0),
        ("daily", 90.0, 80.0),
        ("monthly", 95.0, 90.0),
    ],
)
def test_any_configured_window_can_block(
    window: str, used: float, cap: float
) -> None:
    manager = ProviderUsageManager()
    pool = UsagePoolConfig(source="test-provider", caps={window: cap})
    manager.set_snapshot(
        "test-pool",
        ProviderUsageSnapshot(
            pool_id="test-pool",
            source="test-provider",
            windows={
                window: UsageWindow(
                    key=window,
                    used_percent=used,
                    remaining_percent=100.0 - used,
                )
            },
            authoritative=True,
        ),
    )
    assert manager.evaluate("test-pool", pool) == UsageDecision.WAIT_PROVIDER_USAGE


def test_estimated_usage_never_blocks_scheduler() -> None:
    manager = ProviderUsageManager()
    pool = UsagePoolConfig(source="opencode-go", caps={"weekly": 70.0})
    manager.set_snapshot(
        "opencode-go",
        ProviderUsageSnapshot(
            pool_id="opencode-go",
            source="local-estimate",
            authoritative=False,
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=99.0,
                    remaining_percent=1.0,
                    resets_at=None,
                )
            },
        ),
    )
    assert manager.evaluate("opencode-go", pool) == UsageDecision.READY


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
    assert minimal.credits is None

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


# --- ProviderUsageManager Unit Tests ---


def test_provider_usage_manager_evaluate_returns_ready_when_snapshot_is_none() -> None:
    manager = ProviderUsageManager()
    pool = UsagePoolConfig(source="codex", caps={"weekly": 70.0})
    assert manager.evaluate("codex", pool) == UsageDecision.READY


def test_provider_usage_manager_evaluate_returns_ready_when_snapshot_is_stale() -> None:
    manager = ProviderUsageManager()
    pool = UsagePoolConfig(source="codex", caps={"weekly": 70.0})
    manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly", used_percent=99.0, remaining_percent=1.0
                )
            },
            stale=True,
            authoritative=True,
        ),
    )
    assert manager.evaluate("codex", pool) == UsageDecision.READY


def test_provider_usage_manager_evaluate_returns_ready_when_non_authoritative() -> None:
    manager = ProviderUsageManager()
    pool = UsagePoolConfig(source="codex", caps={"weekly": 70.0})
    manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly", used_percent=99.0, remaining_percent=1.0
                )
            },
            authoritative=False,
        ),
    )
    assert manager.evaluate("codex", pool) == UsageDecision.READY


def test_provider_usage_manager_evaluate_returns_wait_when_hard_limit_reached() -> None:
    manager = ProviderUsageManager()
    pool = UsagePoolConfig(source="codex", caps={"weekly": 70.0})
    manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly", used_percent=10.0, remaining_percent=90.0
                )
            },
            hard_limit_reached=True,
            authoritative=True,
        ),
    )
    assert manager.evaluate("codex", pool) == UsageDecision.WAIT_PROVIDER_USAGE


def test_provider_usage_manager_evaluate_returns_wait_when_window_exceeds_cap() -> None:
    manager = ProviderUsageManager()
    pool = UsagePoolConfig(source="codex", caps={"five_hour": 80.0, "weekly": 70.0})
    manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "five_hour": UsageWindow(
                    key="five_hour", used_percent=50.0, remaining_percent=50.0
                ),
                "weekly": UsageWindow(
                    key="weekly", used_percent=75.0, remaining_percent=25.0
                ),
            },
            authoritative=True,
        ),
    )
    assert manager.evaluate("codex", pool) == UsageDecision.WAIT_PROVIDER_USAGE


def test_provider_usage_manager_evaluate_returns_ready_when_under_cap() -> None:
    manager = ProviderUsageManager()
    pool = UsagePoolConfig(source="codex", caps={"five_hour": 80.0, "weekly": 70.0})
    manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "five_hour": UsageWindow(
                    key="five_hour", used_percent=79.9, remaining_percent=20.1
                ),
                "weekly": UsageWindow(
                    key="weekly", used_percent=69.9, remaining_percent=30.1
                ),
            },
            authoritative=True,
        ),
    )
    assert manager.evaluate("codex", pool) == UsageDecision.READY


def test_provider_usage_manager_evaluate_fails_open_when_window_resets_at_has_passed() -> (
    None
):
    now = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)
    manager = ProviderUsageManager(clock=lambda: now)
    pool = UsagePoolConfig(source="codex", caps={"weekly": 70.0})
    past = now - timedelta(minutes=5)
    manager.set_snapshot(
        "codex",
        ProviderUsageSnapshot(
            pool_id="codex",
            source="codex",
            windows={
                "weekly": UsageWindow(
                    key="weekly",
                    used_percent=90.0,
                    remaining_percent=10.0,
                    resets_at=past,
                )
            },
            authoritative=True,
        ),
    )
    # Passed reset_at -> fails open
    assert manager.evaluate("codex", pool) == UsageDecision.READY


@pytest.mark.asyncio
async def test_provider_usage_manager_refresh_success_caches_snapshot() -> None:
    snapshot = ProviderUsageSnapshot(
        pool_id="codex",
        source="codex",
        windows={
            "weekly": UsageWindow(
                key="weekly", used_percent=55.0, remaining_percent=45.0
            )
        },
        authoritative=True,
    )

    class MockProbe:
        async def fetch_usage(self) -> ProviderUsageSnapshot:
            return snapshot

    manager = ProviderUsageManager(probes={"codex": MockProbe()})
    res = await manager.refresh("codex")
    assert res == snapshot
    assert manager.snapshot("codex") == snapshot


@pytest.mark.asyncio
async def test_provider_usage_manager_refresh_failure_marks_existing_stale() -> None:
    initial = ProviderUsageSnapshot(
        pool_id="codex",
        source="codex",
        windows={
            "weekly": UsageWindow(
                key="weekly", used_percent=55.0, remaining_percent=45.0
            )
        },
        authoritative=True,
    )

    class FailingProbe:
        async def fetch_usage(self) -> ProviderUsageSnapshot:
            raise RuntimeError("API connection timeout")

    manager = ProviderUsageManager(probes={"codex": FailingProbe()})
    manager.set_snapshot("codex", initial)
    res = await manager.refresh("codex")
    assert res is not None
    assert res.stale is True
    assert manager.snapshot("codex").stale is True


@pytest.mark.asyncio
async def test_provider_usage_manager_retains_stale_on_empty_parse() -> None:
    initial = ProviderUsageSnapshot(
        pool_id="agy", source="agy",
        windows={"weekly": UsageWindow("weekly", 20, 80)},
    )

    class EmptyProbe:
        async def fetch_usage(self) -> ProviderUsageSnapshot | None:
            return None

    manager = ProviderUsageManager(probes={"agy": EmptyProbe()})
    manager.set_snapshot("agy", initial)
    result = await manager.refresh("agy")
    assert result is not None
    assert result.stale is True
    assert result.windows["weekly"].used_percent == 20


@pytest.mark.asyncio
async def test_provider_usage_manager_refresh_if_needed_respects_cache_ttl() -> None:
    call_count = 0

    class CountingProbe:
        async def fetch_usage(self) -> ProviderUsageSnapshot:
            nonlocal call_count
            call_count += 1
            return ProviderUsageSnapshot(
                pool_id="codex",
                source="codex",
                windows={
                    "weekly": UsageWindow(
                        key="weekly", used_percent=20.0, remaining_percent=80.0
                    )
                },
            )

    manager = ProviderUsageManager(cache_ttl_s=60.0, probes={"codex": CountingProbe()})
    await manager.refresh_if_needed("codex", "codex")
    assert call_count == 1

    # Within TTL -> no second probe
    await manager.refresh_if_needed("codex", "codex")
    assert call_count == 1

    # Force refresh -> re-probes
    await manager.refresh_if_needed("codex", "codex", force=True)
    assert call_count == 2


def test_provider_usage_manager_format_wait_reason() -> None:
    now = datetime.now(timezone.utc) + timedelta(days=1)
    pool = UsagePoolConfig(source="codex", caps={"weekly": 70.0})
    snap = ProviderUsageSnapshot(
        pool_id="codex",
        source="codex",
        windows={
            "weekly": UsageWindow(
                key="weekly", used_percent=75.0, remaining_percent=25.0, resets_at=now
            )
        },
    )
    reason = format_wait_reason("codex", pool, snap)
    assert "codex weekly usage cap reached (75.0% >= 70.0%)" in reason
    assert now.isoformat() in reason

    hard_snap = ProviderUsageSnapshot(
        pool_id="codex",
        source="codex",
        hard_limit_reached=True,
    )
    hard_reason = format_wait_reason("codex", pool, hard_snap)
    assert "codex provider hard usage limit reached" in hard_reason


def test_quota_group_config_validates_and_preserves_legacy_defaults() -> None:
    cfg = _parse("""
    usage_pools:
      agy:
        source: agy
        quota_group: gemini
        caps: {five_hour: 80, weekly: 70}
      legacy:
        source: codex
        caps: {weekly: 70}
    """)
    assert cfg.usage_pools["agy"].quota_group == "gemini"
    assert cfg.usage_pools["legacy"].quota_group is None
    with pytest.raises(ConfigValidationError, match="quota_group"):
        _parse("""
        usage_pools:
          agy:
            source: agy
            quota_group: ''
            caps: {weekly: 70}
        """)
    with pytest.raises(ConfigValidationError, match="must be 'gemini' or 'third_party'"):
        _parse("""
        usage_pools:
          agy:
            source: agy
            quota_group: gemnii
            caps: {weekly: 70}
        """)


def test_grouped_usage_only_enforces_selected_group_and_recovers_after_reset() -> None:
    now = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)
    manager = ProviderUsageManager(clock=lambda: now)
    snap = ProviderUsageSnapshot("agy", "agy", windows={
        "gemini_five_hour": UsageWindow(
            "gemini_five_hour", 50, 50, now + timedelta(hours=1), "gemini", "five_hour"
        ),
        "gemini_weekly": UsageWindow(
            "gemini_weekly", 50, 50, now + timedelta(days=1), "gemini", "weekly"
        ),
        "third_party_five_hour": UsageWindow(
            "third_party_five_hour", 100, 0, now + timedelta(hours=1), "third_party", "five_hour"
        ),
    })
    manager.set_snapshot("agy", snap)
    gemini = UsagePoolConfig("agy", {"five_hour": 80}, quota_group="gemini")
    third_party = UsagePoolConfig("agy", {"five_hour": 80}, quota_group="third_party")
    assert manager.evaluate("agy", gemini) == UsageDecision.READY
    assert manager.evaluate("agy", third_party) == UsageDecision.WAIT_PROVIDER_USAGE
    assert "agy third_party five_hour" in format_wait_reason("agy", third_party, snap)

    # A reset window is no longer a blocker on the next scheduler decision.
    recovered = UsageWindow(
        "third_party_five_hour", 100, 0, now - timedelta(seconds=1), "third_party", "five_hour"
    )
    manager.set_snapshot("agy", ProviderUsageSnapshot("agy", "agy", windows={
        "third_party_five_hour": recovered,
    }))
    assert manager.evaluate("agy", third_party) == UsageDecision.READY


def test_wait_reason_skips_expired_cap_and_reports_active_window() -> None:
    now = datetime.now(timezone.utc)
    pool = UsagePoolConfig("agy", {"five_hour": 80, "weekly": 70}, "gemini")
    snap = ProviderUsageSnapshot("agy", "agy", windows={
        "gemini_five_hour": UsageWindow(
            "gemini_five_hour", 100, 0, now - timedelta(minutes=1), "gemini", "five_hour"
        ),
        "gemini_weekly": UsageWindow(
            "gemini_weekly", 80, 20, now + timedelta(hours=2), "gemini", "weekly"
        ),
    })
    reason = format_wait_reason("agy", pool, snap)
    assert "weekly usage cap reached" in reason
    assert "five_hour usage cap reached" not in reason
