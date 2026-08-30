from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import textwrap
import pytest

from symphony.backends import (
    EVENT_PROVIDER_USAGE_EXHAUSTED,
    BackendInit,
    ProviderCapacityError,
)
from symphony.backends.codex import (
    CodexAppServerBackend,
    CodexUsageProbe,
    normalize_codex_rate_limits,
    _is_genuine_provider_exhaustion,
)
from symphony.backends.usage import (
    ProviderUsageSnapshot,
    UsageProbe,
    USAGE_PROBES,
    get_usage_probe,
)
from symphony.errors import TurnFailed
from symphony.issue import Issue
from symphony.orchestrator.core import Orchestrator, _EligibilityDisposition
from symphony.orchestrator.entries import RunningEntry
from symphony.orchestrator.usage import ProviderUsageManager, UsageDecision
from symphony.workflow.builder import build_service_config
from symphony.workflow.config import ServiceConfig, UsagePoolConfig
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
# Stage 6.3 Codex Tests
# ==============================================================================


def test_codex_normalizes_five_hour_window() -> None:
    raw = {
        "primary": {
            "usedPercent": 61,
            "windowDurationMins": 300,
            "resetsAt": 12345,
        }
    }

    result = normalize_codex_rate_limits(raw)

    assert isinstance(result, ProviderUsageSnapshot)
    assert "five_hour" in result.windows
    assert result.windows["five_hour"].used_percent == 61.0
    assert result.windows["five_hour"].remaining_percent == 39.0
    assert result.windows["five_hour"].resets_at is not None
    assert result.windows["five_hour"].resets_at == datetime.fromtimestamp(12345, tz=timezone.utc)
    assert result.authoritative is True
    assert result.hard_limit_reached is False


def test_codex_detects_windows_by_duration_not_position() -> None:
    # Reverse primary and secondary positions
    raw = {
        "primary": {
            "usedPercent": 40,
            "windowDurationMins": 10080,
        },
        "secondary": {
            "usedPercent": 20,
            "windowDurationMins": 300,
        },
    }

    result = normalize_codex_rate_limits(raw)

    assert result.windows["weekly"].used_percent == 40.0
    assert result.windows["weekly"].remaining_percent == 60.0
    assert result.windows["five_hour"].used_percent == 20.0
    assert result.windows["five_hour"].remaining_percent == 80.0


def test_codex_rate_limits_read_normalization() -> None:
    # Full response payload from account/rateLimits/read
    raw = {
        "rateLimits": {
            "primary": {
                "usedPercent": 55.5,
                "windowDurationMins": 300,
                "resetsAt": 1723900000,
            },
            "secondary": {
                "usedPercent": 33.3,
                "windowDurationMins": 10080,
                "resetsAt": 1724500000,
            },
        },
        "rateLimitReachedType": None,
    }

    result = normalize_codex_rate_limits(raw, pool_id="codex-custom")

    assert result.pool_id == "codex-custom"
    assert result.source == "codex"
    assert result.authoritative is True
    assert result.hard_limit_reached is False
    assert result.windows["five_hour"].used_percent == 55.5
    assert result.windows["weekly"].used_percent == 33.3


def test_codex_0147_payload_restores_both_windows_without_metadata_windows() -> None:
    raw = {
        "rateLimits": {
            "limitId": "codex",
            "limitName": None,
            "primary": {
                "usedPercent": 12,
                "windowDurationMins": 300,
                "resetsAt": 1788105600,
            },
            "secondary": {
                "usedPercent": 34,
                "windowDurationMins": 10080,
                "resetsAt": 1788710400,
            },
            "credits": {
                "hasCredits": False,
                "unlimited": False,
                "balance": None,
            },
            "individualLimit": None,
            "planType": "plus",
            "rateLimitReachedType": None,
        },
        "rateLimitsByLimitId": None,
        "rateLimitResetCredits": {
            "availableCount": 0,
            "credits": [],
        },
    }

    result = normalize_codex_rate_limits(raw)

    assert set(result.windows) == {"five_hour", "weekly"}
    assert result.windows["five_hour"].used_percent == 12.0
    assert result.windows["weekly"].used_percent == 34.0
    assert result.credits is None


def test_codex_rate_limits_by_limit_id_codex_fallback_is_supported() -> None:
    raw = {
        "rateLimitsByLimitId": {
            "other": {
                "primary": {"usedPercent": 99, "windowDurationMins": 300},
            },
            "codex": {
                "limitId": "codex",
                "primary": {"usedPercent": 21, "windowDurationMins": 300},
                "secondary": {"usedPercent": 43, "windowDurationMins": 10080},
                "rateLimitReachedType": None,
            },
        },
    }

    result = normalize_codex_rate_limits(raw)

    assert result.windows["five_hour"].used_percent == 21.0
    assert result.windows["weekly"].used_percent == 43.0


def test_codex_weekly_only_initial_snapshot_stays_weekly_only() -> None:
    raw = {
        "rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": 31, "windowDurationMins": 10080},
            "secondary": None,
            "credits": None,
            "individualLimit": None,
        }
    }

    result = normalize_codex_rate_limits(raw)

    assert set(result.windows) == {"weekly"}


@pytest.mark.parametrize(
    ("credits", "expected_unlimited", "expected_balance"),
    [
        ({"hasCredits": True, "unlimited": False, "balance": "42.5"}, False, "42.5"),
        ({"hasCredits": False, "unlimited": True, "balance": None}, True, None),
    ],
)
def test_codex_usable_credits_are_normalized_separately(
    credits: dict[str, object],
    expected_unlimited: bool,
    expected_balance: str | None,
) -> None:
    result = normalize_codex_rate_limits(
        {
            "rateLimits": {
                "primary": {"usedPercent": 1, "windowDurationMins": 300},
                "credits": credits,
            }
        }
    )

    assert set(result.windows) == {"five_hour"}
    assert result.credits is not None
    assert result.credits.unlimited is expected_unlimited
    assert result.credits.balance == expected_balance


def test_codex_credits_never_participate_in_scheduling_caps() -> None:
    result = normalize_codex_rate_limits(
        {
            "rateLimits": {
                "credits": {
                    "hasCredits": True,
                    "unlimited": False,
                    "balance": "0",
                }
            }
        }
    )
    manager = ProviderUsageManager()
    manager.set_snapshot("codex", result)

    assert result.windows == {}
    assert manager.evaluate(
        "codex",
        UsagePoolConfig(source="codex", caps={"five_hour": 1.0}),
    ) == UsageDecision.READY


def test_codex_multiple_limit_ids_are_preserved() -> None:
    raw = {
        "limit_300": {
            "usedPercent": 10,
            "windowDurationMins": 300,
        },
        "limit_10080": {
            "usedPercent": 20,
            "windowDurationMins": 10080,
        },
        "limit_daily": {
            "usedPercent": 30,
            "windowDurationMins": 1440,
        },
    }

    result = normalize_codex_rate_limits(raw)

    assert result.windows["five_hour"].used_percent == 10.0
    assert result.windows["weekly"].used_percent == 20.0
    assert result.windows["1440_minutes"].used_percent == 30.0


@pytest.mark.asyncio
async def test_codex_updated_notification_updates_shared_pool(tmp_path: Path) -> None:
    manager = ProviderUsageManager()
    events = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    init = BackendInit(
        cfg=_parse_config("tracker:\n  kind: file\nagent:\n  kind: codex\n"),
        cwd=tmp_path,
        workspace_root=tmp_path,
        on_event=on_event,
        usage_manager=manager,
    )
    backend = CodexAppServerBackend(init)

    await backend._handle_notification(
        {
            "method": "account/rateLimits/updated",
            "params": {
                "rateLimits": {
                    "primary": {
                        "usedPercent": 10,
                        "windowDurationMins": 300,
                        "resetsAt": 1788105600,
                    },
                    "secondary": {
                        "usedPercent": 64,
                        "windowDurationMins": 10080,
                        "resetsAt": 1788710400,
                    },
                    "credits": {
                        "hasCredits": True,
                        "unlimited": False,
                        "balance": "10",
                    },
                }
            },
        }
    )

    notification = {
        "method": "account/rateLimits/updated",
        "params": {
            "rateLimits": {
                "primary": {
                    "usedPercent": 82.5,
                    "windowDurationMins": 300,
                },
                "secondary": None,
                "credits": None,
            }
        },
    }

    await backend._handle_notification(notification)

    # Immediately reflected in the shared manager
    snapshot = manager.snapshot("codex")
    assert snapshot is not None
    assert snapshot.windows["five_hour"].used_percent == 82.5
    assert snapshot.windows["weekly"].used_percent == 64.0
    assert snapshot.windows["weekly"].resets_at == datetime.fromtimestamp(
        1788710400, tz=timezone.utc
    )
    assert snapshot.credits is not None
    assert snapshot.credits.balance == "10"
    latest = backend.latest_rate_limits
    assert latest is not None
    assert latest["secondary"]["usedPercent"] == 64
    assert latest["credits"]["balance"] == "10"

    await backend._handle_notification(
        {
            "method": "account/rateLimits/updated",
            "params": {
                "rateLimits": {
                    "primary": {
                        "usedPercent": None,
                        "windowDurationMins": 300,
                        "resetsAt": None,
                    },
                    "credits": {
                        "hasCredits": True,
                        "unlimited": None,
                        "balance": None,
                    },
                }
            },
        }
    )
    snapshot = manager.snapshot("codex")
    assert snapshot is not None
    assert snapshot.windows["five_hour"].used_percent == 82.5
    assert snapshot.windows["five_hour"].resets_at == datetime.fromtimestamp(
        1788105600, tz=timezone.utc
    )
    latest = backend.latest_rate_limits
    assert latest is not None
    assert latest["primary"]["usedPercent"] == 82.5
    assert latest["primary"]["resetsAt"] == 1788105600
    assert latest["credits"]["unlimited"] is False
    assert latest["credits"]["balance"] == "10"


def test_codex_unknown_window_is_preserved_or_ignored_safely() -> None:
    raw = {
        "weird_custom_window": {
            "usedPercent": 45.0,
            "windowDurationMins": 45,
        },
        "non_dict_entry": "invalid_value",
    }

    result = normalize_codex_rate_limits(raw)

    assert "45_minutes" in result.windows
    assert result.windows["45_minutes"].used_percent == 45.0


def test_codex_hard_limit_reached_is_normalized() -> None:
    raw = {
        "rateLimits": {
            "primary": {
                "usedPercent": 100.0,
                "windowDurationMins": 300,
            }
        },
        "rateLimitReachedType": "hard",
    }

    result = normalize_codex_rate_limits(raw)
    assert result.hard_limit_reached is True


def test_codex_api_key_auth_does_not_apply_chatgpt_cap() -> None:
    raw = {
        "primary": {
            "usedPercent": 95.0,
            "windowDurationMins": 300,
        },
        "authMode": "apiKey",
    }

    result = normalize_codex_rate_limits(raw)

    # API key auth should be non-authoritative for subscription caps so it does not block
    assert result.authoritative is False

    manager = ProviderUsageManager()
    manager.set_snapshot("codex", result)
    pool = UsagePoolConfig(source="codex", caps={"five_hour": 80.0})
    decision = manager.evaluate("codex", pool)
    assert decision == UsageDecision.READY


@pytest.mark.asyncio
async def test_codex_usage_probe_calls_rate_limits_read() -> None:
    class MockClient:
        async def request(self, method: str, params: dict) -> dict:
            if method == "account/rateLimits/read":
                return {
                    "primary": {
                        "usedPercent": 42.0,
                        "windowDurationMins": 300,
                    }
                }
            return {}

    probe = CodexUsageProbe(client=MockClient(), pool_id="codex")
    snap = await probe.fetch_usage()

    assert snap is not None
    assert snap.windows["five_hour"].used_percent == 42.0
    assert snap.authoritative is True


@pytest.mark.asyncio
async def test_codex_usage_probe_fails_open_on_error() -> None:
    class FailingClient:
        async def request(self, method: str, params: dict) -> dict:
            raise RuntimeError("network down")

    probe = CodexUsageProbe(client=FailingClient(), pool_id="codex")
    snap = await probe.fetch_usage()

    assert snap is None


def test_codex_usage_probe_registered_in_usage_probes() -> None:
    probe_cls = get_usage_probe("codex")
    assert probe_cls is not None
    assert issubclass(probe_cls, UsageProbe) or issubclass(probe_cls, CodexUsageProbe)
    assert USAGE_PROBES.get("codex") is not None


def test_provider_capacity_error_dataclass_and_event_constant() -> None:
    assert EVENT_PROVIDER_USAGE_EXHAUSTED == "provider_usage_exhausted"
    resets = datetime(2026, 8, 17, 23, 0, 0, tzinfo=timezone.utc)
    err = ProviderCapacityError(pool_id="codex", resets_at=resets, message="Usage limit exceeded")
    assert err.pool_id == "codex"
    assert err.resets_at == resets
    assert "codex: Usage limit exceeded" in str(err)
    assert isinstance(err, Exception)


# ==============================================================================
# Stage 4 & Stage 6.11 Runtime Provider-Exhaustion Classification
# ==============================================================================


def test_genuine_provider_exhaustion_detection() -> None:
    assert _is_genuine_provider_exhaustion("You've reached your usage limit for this plan.") is True
    assert _is_genuine_provider_exhaustion("rate_limit_reached: 5-hour quota exhausted") is True
    assert _is_genuine_provider_exhaustion("Error: insufficient_quota for current period") is True
    assert _is_genuine_provider_exhaustion("monthly credit exhausted", err_type="quota_exceeded") is True

    # Generic RPM / TPM / 429 should NOT be classified as genuine provider capacity exhaustion
    assert _is_genuine_provider_exhaustion("Rate limit exceeded: 60 requests per minute") is False
    assert _is_genuine_provider_exhaustion("TPM limit reached: slow down") is False
    assert _is_genuine_provider_exhaustion("HTTP 429 Too Many Requests (RPM)") is False
    assert _is_genuine_provider_exhaustion("connection reset by peer") is False


@pytest.mark.asyncio
async def test_provider_exhaustion_does_not_consume_retry_budget() -> None:
    cfg = _parse_config("""
    tracker:
      kind: file
    agent:
      kind: codex
      max_retries: 3
    usage_pools:
      codex:
        source: codex
        caps:
          weekly: 70
    """)
    orch = _orch(cfg)
    issue = _issue("TASK-14")
    orch._persisted_retry_attempts[issue.id] = 0

    entry = RunningEntry(
        issue=issue,
        started_at=datetime.now(timezone.utc),
        retry_attempt=0,
        worker_task=None,
        workspace_path=Path("/tmp/ws-task-14"),
    )
    orch._running[issue.id] = entry
    orch._claimed.add(issue.id)

    # Provider quota exhaustion occurs during running task
    resets_at = datetime.now(timezone.utc) + timedelta(hours=3)
    await orch._on_codex_event(
        issue.id,
        {
            "event": EVENT_PROVIDER_USAGE_EXHAUSTED,
            "payload": {
                "pool_id": "codex",
                "reason": "usage_limit_reached",
                "resets_at": resets_at.isoformat(),
            },
        },
    )

    # Worker exits due to provider capacity exhaustion
    await orch._on_worker_exit(issue.id, "provider_usage_exhausted", "usage_limit_reached")

    # Verify: ordinary retry budget is NOT consumed
    assert issue.id not in orch._retry
    assert orch._persisted_retry_attempts.get(issue.id) is None

    # Verify: shared provider snapshot is updated to hard limit reached
    snapshot = orch._usage_manager.snapshot("codex")
    assert snapshot is not None
    assert snapshot.hard_limit_reached is True

    # Verify: ticket returns to waiting_provider_usage (not blocked, not retrying)
    decision = orch._eligibility_decision(issue, cfg, owning_retry=False)
    assert decision.disposition is _EligibilityDisposition.WAIT_NON_SLOT
    assert decision.code == "waiting_provider_usage"


@pytest.mark.asyncio
async def test_generic_429_rpm_treated_as_normal_retry_not_provider_exhaustion(tmp_path: Path) -> None:
    events = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    init = BackendInit(
        cfg=_parse_config("tracker:\n  kind: file\nagent:\n  kind: codex\n"),
        cwd=tmp_path,
        workspace_root=tmp_path,
        on_event=on_event,
    )
    backend = CodexAppServerBackend(init)

    turn = {
        "status": "failed",
        "error": {
            "message": "Rate limit exceeded: 60 requests per minute (RPM)",
            "type": "rate_limit_rpm",
        },
    }

    # Should raise TurnFailed, NOT ProviderCapacityError, and NOT emit EVENT_PROVIDER_USAGE_EXHAUSTED
    with pytest.raises(TurnFailed):
        await backend._raise_for_terminal_status(turn)

    assert not any(ev.get("event") == EVENT_PROVIDER_USAGE_EXHAUSTED for ev in events)
