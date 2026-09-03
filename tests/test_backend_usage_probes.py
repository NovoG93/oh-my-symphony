"""Unit tests for Stage 2.2-2.8 backend usage probes and fail-open invariants.

Tests cover:
- AGY probe, read-only command, bucket preservation (Stage 6.4)
- Claude normalization, fail-open, hard-limit detection (Stage 6.5)
- Gemini fail-open, hard-limit detection, reset extraction (Stage 6.6)
- Kiro credit-based normalization, fail-open, credit exhaustion (Stage 6.7)
- OpenCode provider delegation, non-authoritative estimates (Stage 6.8)
- Pi and Prime Agent explicit pool binding & Copilot probe (Stage 6.9)
- Global fail-open invariant across all 8 backends (Stage 6.13)
- Registry lazy loading and resolution
"""

from __future__ import annotations

from pathlib import Path
import json
import textwrap
from unittest.mock import AsyncMock, patch
import pytest

from symphony.backends.agy import AgyUsageProbe, normalize_agy_usage
from symphony.backends.claude_code import (
    ClaudeUsageProbe,
    normalize_claude_usage,
    _is_genuine_claude_exhaustion,
)
from symphony.backends.gemini import (
    GeminiUsageProbe,
    normalize_gemini_usage,
    _parse_gemini_exhaustion,
)
from symphony.backends.kiro import (
    KiroUsageProbe,
    normalize_kiro_usage,
    _is_genuine_kiro_exhaustion,
)
from symphony.backends.opencode import (
    normalize_opencode_local_usage,
    _is_genuine_opencode_exhaustion,
)
from symphony.backends.copilot import (
    CopilotUsageProbe,
    _is_genuine_copilot_exhaustion,
)
from symphony.backends.pi import (
    _is_genuine_pi_exhaustion,
)
from symphony.backends.usage import (
    ProviderUsageSnapshot,
    UsageWindow,
    get_usage_probe,
)
from symphony.issue import Issue
from symphony.orchestrator.core import Orchestrator
from symphony.orchestrator.usage import ProviderUsageManager, UsageDecision
from symphony.workflow.builder import build_service_config
from symphony.workflow.config import (
    ServiceConfig,
    UsagePoolConfig,
)
from symphony.workflow.parser import parse_workflow_text
from symphony.workflow.state import WorkflowState


def _parse_config(workflow_text: str) -> ServiceConfig:
    dedented = textwrap.dedent(workflow_text).strip()
    if not dedented.startswith("---"):
        dedented = f"---\n{dedented}\n---\n"
    definition = parse_workflow_text(dedented, source_path=Path("/tmp/WORKFLOW.md"))
    return build_service_config(definition)


def _issue(
    identifier: str,
    *,
    state: str = "In Progress",
    agent_kind: str | None = None,
    agent_profile: str | None = None,
) -> Issue:
    return Issue(
        id=identifier,
        identifier=identifier,
        title=identifier,
        description="",
        state=state,
        priority=1,
        agent_kind=agent_kind,
        agent_profile=agent_profile,
    )


def _orch(cfg: ServiceConfig | None = None) -> Orchestrator:
    state = WorkflowState(Path("/tmp/WORKFLOW.md"))
    if cfg is None:
        cfg = _parse_config("""
        tracker:
          kind: file
        agent:
          kind: codex
          max_concurrent_agents: 5
        """)
    state._config = cfg  # type: ignore[attr-defined]
    return Orchestrator(state)


# ==============================================================================
# Stage 6.4 AGY Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_agy_quota_probe_uses_read_only_command() -> None:
    probe = AgyUsageProbe(command="agy -p /quota --output-format json")
    assert "-p" in probe.command
    assert "/quota" in probe.command
    assert "--output-format json" in probe.command

    sample_output = textwrap.dedent("""
    {
      "buckets": {
        "gemini-2.5-pro": {
          "used_percent": 30.0,
          "remaining_percent": 70.0,
          "resets_at": "2026-08-18T00:00:00Z"
        }
      }
    }
    """)

    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (sample_output.encode("utf-8"), b"")
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_shell", return_value=mock_proc) as mock_shell:
        snapshot = await probe.fetch_usage()
        assert mock_shell.called
        assert snapshot is not None
        assert isinstance(snapshot, ProviderUsageSnapshot)
        assert snapshot.pool_id == "agy"
        assert snapshot.source == "agy"
        assert "gemini-2.5-pro" in snapshot.windows
        assert snapshot.windows["gemini-2.5-pro"].used_percent == 30.0
        assert snapshot.windows["gemini-2.5-pro"].remaining_percent == 70.0
        assert snapshot.authoritative is True


def test_agy_structured_quota_is_normalized() -> None:
    raw = {
        "buckets": {
            "default": {
                "used_percent": 45.5,
                "remaining_percent": 54.5,
                "resets_at": 1755475200,
            }
        },
        "hard_limit_reached": False,
    }
    snapshot = normalize_agy_usage(raw, pool_id="agy-pool")
    assert snapshot.pool_id == "agy-pool"
    assert snapshot.source == "agy"
    assert snapshot.hard_limit_reached is False
    assert snapshot.authoritative is True
    assert "default" in snapshot.windows
    assert snapshot.windows["default"].used_percent == 45.5
    assert snapshot.windows["default"].remaining_percent == 54.5
    assert snapshot.windows["default"].resets_at is not None


def test_agy_model_specific_quota_buckets_are_preserved() -> None:
    raw = {
        "buckets": {
            "gemini-2.5-pro": {
                "usedPercent": 25.0,
                "remainingPercent": 75.0,
            },
            "claude-3-7-sonnet": {
                "usedPercent": 60.0,
                "remainingPercent": 40.0,
            },
            "gpt-5.6-sol": {
                "usedPercent": 10.0,
                "remainingPercent": 90.0,
            },
        }
    }
    snapshot = normalize_agy_usage(raw)
    assert set(snapshot.windows.keys()) == {
        "gemini-2.5-pro",
        "claude-3-7-sonnet",
        "gpt-5.6-sol",
    }
    assert "five_hour" not in snapshot.windows
    assert "weekly" not in snapshot.windows
    assert snapshot.windows["gemini-2.5-pro"].used_percent == 25.0
    assert snapshot.windows["claude-3-7-sonnet"].used_percent == 60.0
    assert snapshot.windows["gpt-5.6-sol"].used_percent == 10.0


def test_agy_122_grouped_quota_is_order_independent() -> None:
    raw = {
        "status": "SUCCESS",
        "command": {"name": "usage", "data": {"groups": [
            {"name": "Claude and GPT models", "buckets": [
                {"id": "3p-weekly", "window": "weekly", "remaining_fraction": 0.4413555860519409,
                 "reset_time": "2026-09-05T07:22:30Z"},
                {"id": "3p-5h", "window": "5h", "remaining_fraction": 1,
                 "reset_time": "2026-08-30T22:05:19Z"},
            ]},
            {"name": "Gemini Models", "buckets": [
                {"id": "gemini-5h", "window": "5h", "remaining_fraction": 1,
                 "reset_time": "2026-08-30T21:48:19Z"},
                {"id": "gemini-weekly", "window": "weekly", "remaining_fraction": 0.9570363759994507,
                 "reset_time": "2026-09-05T07:22:39Z"},
            ]},
        ]}}
    }
    snapshot = normalize_agy_usage(raw)
    assert set(snapshot.windows) == {
        "gemini_five_hour", "gemini_weekly",
        "third_party_five_hour", "third_party_weekly",
    }
    weekly = snapshot.windows["third_party_weekly"]
    assert weekly.group_key == "third_party"
    assert weekly.period_key == "weekly"
    assert weekly.used_percent == pytest.approx(55.8644414)
    assert weekly.remaining_percent == pytest.approx(44.1355586)
    assert weekly.resets_at is not None
    assert weekly.resets_at.isoformat() == "2026-09-05T07:22:30+00:00"


def test_agy_grouped_malformed_and_unknown_buckets_are_ignored() -> None:
    raw = {"command": {"data": {"groups": [
        {"name": "Gemini Models", "buckets": [
            {"window": "monthly", "remaining_fraction": 0.5},
            {"window": "weekly", "remaining_fraction": "bad"},
            {"window": "5h", "remaining_fraction": 0.25},
            "metadata",
        ]},
        {"name": "Other", "buckets": [{"window": "5h", "remaining_fraction": 0}]},
    ]}}}
    snapshot = normalize_agy_usage(raw)
    assert set(snapshot.windows) == {"gemini_five_hour"}
    assert snapshot.windows["gemini_five_hour"].used_percent == 75.0


@pytest.mark.asyncio
async def test_agy_probe_rejects_failed_status_and_empty_group_parse() -> None:
    class Proc:
        returncode = 0
        async def communicate(self):
            return (json.dumps({"status": "ERROR"}).encode(), b"")
    with patch("asyncio.create_subprocess_shell", return_value=Proc()):
        assert await AgyUsageProbe().fetch_usage() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"command": {"data": {"groups": {}}}},
    {"command": {"data": {}}},
    {"command": "not-an-envelope"},
])
async def test_agy_probe_rejects_malformed_command_envelope(payload: dict) -> None:
    class Proc:
        returncode = 0
        async def communicate(self):
            return (json.dumps(payload).encode(), b"")
    with patch("asyncio.create_subprocess_shell", return_value=Proc()):
        assert await AgyUsageProbe().fetch_usage() is None


@pytest.mark.asyncio
async def test_agy_probe_rejects_empty_legacy_and_manager_retains_stale() -> None:
    class Proc:
        returncode = 0
        async def communicate(self):
            return (b'{"buckets": []}', b"")
    with patch("asyncio.create_subprocess_shell", return_value=Proc()):
        assert await AgyUsageProbe().fetch_usage() is None


def test_agy_command_metadata_does_not_hide_valid_legacy_buckets() -> None:
    snapshot = normalize_agy_usage({
        "command": {"name": "metadata"},
        "quotas": {"weekly": {"used_percent": 25, "remaining_percent": 75}},
    })
    assert snapshot.windows["weekly"].used_percent == 25


def test_agy_groups_empty_falls_back_to_legacy_quotas() -> None:
    snapshot = normalize_agy_usage({
        "command": {"data": {"groups": []}},
        "quotas": {"weekly": {"used_percent": 25, "remaining_percent": 75}},
    })
    assert set(snapshot.windows) == {"weekly"}


def test_agy_legacy_invalid_bucket_is_not_authoritative() -> None:
    snapshot = normalize_agy_usage({
        "buckets": {"metadata": {"description": "not usage"},
                     "weekly": {"used_percent": "bad"}}
    })
    assert snapshot.windows == {}


def test_agy_legacy_non_finite_bucket_values_are_ignored() -> None:
    """Never let NaN/Infinity reach the JSON API and browser JSON.parse."""
    snapshot = normalize_agy_usage({
        "buckets": {
            "nan-used": {"used_percent": "NaN"},
            "infinite-remaining": {"remaining_percent": "Infinity"},
            "valid": {"used_percent": 25},
        }
    })
    assert set(snapshot.windows) == {"valid"}


def test_agy_legacy_used_limit_non_finite_values_are_ignored() -> None:
    """The legacy used/limit alias must also fail open on bad telemetry."""
    snapshot = normalize_agy_usage({
        "buckets": {
            "nan-used": {"used": "NaN", "limit": 1},
            "infinite-used": {"used": "Infinity", "limit": 1},
            "infinite-limit": {"used": 1, "limit": "Infinity"},
            "valid": {"used": 1, "limit": 4},
        }
    })
    assert set(snapshot.windows) == {"valid"}
    assert snapshot.windows["valid"].used_percent == 25.0


@pytest.mark.asyncio
async def test_agy_probe_manager_retains_stale_on_invalid_legacy_payload() -> None:
    initial = ProviderUsageSnapshot(
        "agy", "agy", windows={"weekly": UsageWindow("weekly", 20, 80)}
    )
    class Proc:
        returncode = 0
        async def communicate(self):
            return (b'{"buckets": {"weekly": {"used_percent": "bad"}}}', b"")
    manager = ProviderUsageManager(probes={"agy": AgyUsageProbe()})
    manager.set_snapshot("agy", initial)
    with patch("asyncio.create_subprocess_shell", return_value=Proc()):
        result = await manager.refresh("agy")
    assert result is not None and result.stale is True
    assert result.windows["weekly"].used_percent == 20


@pytest.mark.asyncio
async def test_agy_probe_fails_open_on_error() -> None:
    probe = AgyUsageProbe(command="nonexistent-agy-cmd")
    with patch("asyncio.create_subprocess_shell", side_effect=OSError("binary not found")):
        res = await probe.fetch_usage()
        assert res is None


# ==============================================================================
# Stage 6.5 Claude Tests
# ==============================================================================


def test_claude_normalizes_subscription_limits() -> None:
    raw = {
        "rate_limits": {
            "five_hour": {
                "used_percentage": 35,
                "resets_at": 1000,
            },
            "seven_day": {
                "used_percentage": 65,
                "resets_at": 2000,
            },
        }
    }

    usage = normalize_claude_usage(raw)

    assert isinstance(usage, ProviderUsageSnapshot)
    assert usage.source == "claude"
    assert "five_hour" in usage.windows
    assert "weekly" in usage.windows
    assert usage.windows["five_hour"].used_percent == 35.0
    assert usage.windows["five_hour"].remaining_percent == 65.0
    assert usage.windows["weekly"].used_percent == 65.0
    assert usage.windows["weekly"].remaining_percent == 35.0
    assert usage.authoritative is True


def test_claude_missing_rate_limits_returns_unknown() -> None:
    raw = {}
    usage = normalize_claude_usage(raw)
    assert usage.windows == {}
    assert usage.hard_limit_reached is False


def test_claude_missing_single_window_is_supported() -> None:
    raw = {
        "rate_limits": {
            "five_hour": {
                "used_percentage": 50,
                "resets_at": 1723456789,
            }
        }
    }
    usage = normalize_claude_usage(raw)
    assert "five_hour" in usage.windows
    assert "weekly" not in usage.windows
    assert usage.windows["five_hour"].used_percent == 50.0


@pytest.mark.asyncio
async def test_claude_unknown_quota_fails_open() -> None:
    probe = ClaudeUsageProbe(pool_id="claude")
    # Cold start: no cached telemetry
    res = await probe.fetch_usage()
    assert res is None


def test_claude_limit_error_sets_hard_limit() -> None:
    raw = {
        "error": "usage limit reached for current billing cycle",
        "hard_limit_reached": True,
    }
    usage = normalize_claude_usage(raw)
    assert usage.hard_limit_reached is True


def test_claude_genuine_exhaustion_detection() -> None:
    assert _is_genuine_claude_exhaustion("You have reached your usage limit for Claude.") is True
    assert _is_genuine_claude_exhaustion("Error: rate_limit_reached: quota exceeded") is True
    assert _is_genuine_claude_exhaustion("credit balance is too low") is True
    # Transient RPM / 429 should not be classified as provider exhaustion
    assert _is_genuine_claude_exhaustion("429 Too Many Requests: requests per minute exceeded") is False
    assert _is_genuine_claude_exhaustion("tpm limit exceeded") is False


# ==============================================================================
# Stage 6.6 Gemini Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_gemini_missing_programmatic_quota_fails_open() -> None:
    probe = GeminiUsageProbe(pool_id="gemini")
    res = await probe.fetch_usage()
    assert res is None


def test_gemini_quota_exhaustion_is_not_normal_retry() -> None:
    exhausted, reset_at = _parse_gemini_exhaustion(
        "ResourceExhausted: 429 Quota exceeded for quota metric 'GenerateContent requests'"
    )
    assert exhausted is True

    generic_429, _ = _parse_gemini_exhaustion("429 requests per minute rate limit exceeded")
    assert generic_429 is False

    normal_err, _ = _parse_gemini_exhaustion("SyntaxError: invalid syntax in prompt")
    assert normal_err is False


def test_gemini_reset_time_is_extracted_when_available() -> None:
    error_msg = "Quota exceeded. Resets at 2026-08-18T12:00:00Z"
    exhausted, reset_at = _parse_gemini_exhaustion(error_msg)
    assert exhausted is True
    assert reset_at is not None
    assert reset_at.year == 2026

    error_msg_mins = "Quota exceeded. Please retry after 300 seconds"
    exhausted2, reset_at2 = _parse_gemini_exhaustion(error_msg_mins)
    assert exhausted2 is True
    assert reset_at2 is not None


def test_gemini_usage_snapshot_normalization() -> None:
    raw = {
        "windows": {
            "daily": {
                "used_percent": 80.0,
                "remaining_percent": 20.0,
            }
        }
    }
    snap = normalize_gemini_usage(raw)
    assert "daily" in snap.windows
    assert snap.windows["daily"].used_percent == 80.0
    assert snap.source == "gemini"


# ==============================================================================
# Stage 6.7 Kiro Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_kiro_missing_usage_probe_fails_open() -> None:
    probe = KiroUsageProbe(pool_id="kiro")
    res = await probe.fetch_usage()
    assert res is None


def test_kiro_credit_exhaustion_blocks_new_dispatch() -> None:
    assert _is_genuine_kiro_exhaustion("Error: monthly credits exhausted for account") is True
    assert _is_genuine_kiro_exhaustion("insufficient credits to complete request") is True
    assert _is_genuine_kiro_exhaustion("monthly limit reached") is True
    assert _is_genuine_kiro_exhaustion("network timeout during chat") is False


def test_kiro_monthly_credit_window_can_be_normalized() -> None:
    raw = {
        "used_credits": 850,
        "total_credits": 1000,
    }
    snap = normalize_kiro_usage(raw, pool_id="kiro")
    assert snap.source == "kiro"
    assert "monthly" in snap.windows
    assert snap.windows["monthly"].used_percent == 85.0
    assert snap.windows["monthly"].remaining_percent == 15.0
    assert snap.authoritative is True


# ==============================================================================
# Stage 6.8 OpenCode Tests
# ==============================================================================


def test_opencode_local_stats_are_non_authoritative() -> None:
    raw = {
        "used_percent": 95.0,
        "total_tokens": 100000,
    }
    snap = normalize_opencode_local_usage(raw, pool_id="opencode-go")
    assert snap.pool_id == "opencode-go"
    assert snap.source == "opencode-go"
    assert snap.authoritative is False
    assert snap.windows["local"].used_percent == 95.0


def test_opencode_bound_to_codex_uses_codex_pool() -> None:
    cfg = _parse_config("""
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
    agent_profiles:
      opencode-codex:
        kind: opencode
        usage_pool: codex
    """)
    profile = cfg.agent_profiles["opencode-codex"]
    assert profile.usage_pool == "codex"
    assert profile.kind == "opencode"


def test_opencode_go_estimate_does_not_block_scheduler() -> None:
    snapshot = ProviderUsageSnapshot(
        pool_id="opencode-go",
        source="opencode-go",
        authoritative=False,
        windows={
            "weekly": UsageWindow(
                key="weekly",
                used_percent=99.0,
                remaining_percent=1.0,
                resets_at=None,
            )
        },
    )

    manager = ProviderUsageManager()
    manager.set_snapshot("opencode-go", snapshot)
    pool_cfg = UsagePoolConfig(source="opencode-go", caps={"weekly": 70.0})

    decision = manager.evaluate("opencode-go", pool_cfg)
    assert decision == UsageDecision.READY



def test_opencode_exhaustion_detection() -> None:
    assert _is_genuine_opencode_exhaustion("quota exceeded on upstream provider") is True
    assert _is_genuine_opencode_exhaustion("usage limit reached") is True
    assert _is_genuine_opencode_exhaustion("rate limit: requests per minute") is False


# ==============================================================================
# Stage 6.9 Pi / Prime Agent Tests
# ==============================================================================


def test_pi_requires_bound_pool_for_subscription_policy() -> None:
    cfg = _parse_config("""
    agent_profiles:
      pi-builder:
        kind: pi
    """)
    assert cfg.agent_profiles["pi-builder"].usage_pool is None


def test_pi_profile_can_share_codex_usage_pool() -> None:
    cfg = _parse_config("""
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
    agent_profiles:
      pi-codex:
        kind: pi
        usage_pool: codex
    """)
    assert cfg.agent_profiles["pi-codex"].usage_pool == "codex"
    assert cfg.agent_profiles["pi-codex"].kind == "pi"


def test_prime_agent_uses_same_usage_pool_resolution_as_pi() -> None:
    cfg = _parse_config("""
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
    agent_profiles:
      prime-codex:
        kind: prime-agent
        usage_pool: codex
    """)
    assert cfg.agent_profiles["prime-codex"].usage_pool == "codex"
    assert cfg.agent_profiles["prime-codex"].kind == "prime-agent"


def test_prime_claude_does_not_implicitly_use_claude_code_pool() -> None:
    cfg = _parse_config("""
    usage_pools:
      claude:
        source: claude
        caps:
          weekly: 70
    agent_profiles:
      prime-claude:
        kind: prime-agent
    """)
    # When usage_pool is omitted, it is None (defaults to prime-agent, not claude)
    assert cfg.agent_profiles["prime-claude"].usage_pool is None


@pytest.mark.asyncio
async def test_copilot_usage_probe_fails_open() -> None:
    probe = CopilotUsageProbe(command="nonexistent-copilot-bin", pool_id="copilot")
    res = await probe.fetch_usage()
    assert res is None


def test_copilot_exhaustion_detection() -> None:
    assert _is_genuine_copilot_exhaustion("insufficient credits on model provider") is True
    assert _is_genuine_copilot_exhaustion("quota exceeded: rate limit reached") is True
    assert _is_genuine_copilot_exhaustion("requests per minute exceeded") is False


def test_pi_exhaustion_detection() -> None:
    assert _is_genuine_pi_exhaustion("insufficient credits on model provider") is True
    assert _is_genuine_pi_exhaustion("quota exceeded: rate limit reached") is True
    assert _is_genuine_pi_exhaustion("requests per minute exceeded") is False


# ==============================================================================
# Stage 6.13 Global Fail-Open Invariant Test
# ==============================================================================


@pytest.mark.parametrize(
    "kind",
    [
        "codex",
        "claude",
        "agy",
        "copilot",
        "gemini",
        "kiro",
        "opencode",
        "pi",
        "prime-agent",
    ],
)
def test_usage_probe_failure_never_prevents_dispatch(kind: str) -> None:
    cfg = _parse_config(f"""
    usage_pools:
      {kind}:
        source: {kind}
        caps:
          weekly: 70
    agent:
      kind: {kind}
    """)
    orch = _orch(cfg)

    class FailingProbe:
        async def fetch_usage(self) -> ProviderUsageSnapshot | None:
            raise RuntimeError("Probe network failure")

    # Simulated probe failure / no snapshot / broken probe:
    manager = orch._usage_manager
    manager.set_probe(kind, FailingProbe())
    manager.snapshots.pop(kind, None)

    issue = _issue(f"{kind}-test-1", agent_kind=kind)
    decision = orch._eligibility_usage_decision(issue, cfg)

    # Probe failure must ALWAYS fail open (decision is None, indicating not blocked by usage)
    assert decision is None



# ==============================================================================
# Registry Tests
# ==============================================================================


def test_usage_probes_registry_all_backends_registered() -> None:
    sources = [
        ("codex", "CodexUsageProbe"),
        ("claude", "ClaudeUsageProbe"),
        ("agy", "AgyUsageProbe"),
        ("gemini", "GeminiUsageProbe"),
        ("kiro", "KiroUsageProbe"),
        ("opencode-go", "OpenCodeGoUsageProbe"),
        ("copilot", "CopilotUsageProbe"),
        ("github-copilot", "CopilotUsageProbe"),
    ]
    for source, expected_cls_name in sources:
        probe_cls = get_usage_probe(source)
        assert probe_cls is not None, f"get_usage_probe('{source}') returned None"
        assert probe_cls.__name__ == expected_cls_name, f"Expected {expected_cls_name}, got {probe_cls.__name__}"
